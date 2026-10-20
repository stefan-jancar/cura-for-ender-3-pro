"""Stiahnutie modelu z odkazu: priamy súbor, Printables, Thingiverse, ZIP."""
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import httpx

MAX_BYTES = 400 * 1024 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KompilatorCuraEnder3Pro/1.0"


class FetchError(Exception):
    pass


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=20), headers={"User-Agent": UA})


def _filename(resp: httpx.Response, url: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return unquote(m.group(1)).strip()
    name = PurePosixPath(unquote(urlparse(str(resp.url)).path)).name or PurePosixPath(urlparse(url).path).name
    return name or "model"


def _download(client: httpx.Client, url: str) -> tuple[bytes, str]:
    with client.stream("GET", url) as r:
        if r.status_code >= 400:
            raise FetchError(f"Server vrátil chybu {r.status_code} pre {url}")
        ctype = r.headers.get("content-type", "")
        chunks, size = [], 0
        for c in r.iter_bytes():
            size += len(c)
            if size > MAX_BYTES:
                raise FetchError("Súbor je väčší ako 400 MB")
            chunks.append(c)
        data = b"".join(chunks)
        name = _filename(r, url)
    if "text/html" in ctype and not re.search(r"\.(stl|3mf|obj|zip)$", name, re.I):
        raise FetchError("Odkaz vedie na webovú stránku, nie na súbor modelu.")
    return data, name


# --- Printables ----------------------------------------------------------

_PRINTABLES_GQL = "https://api.printables.com/graphql/"


def _printables(client: httpx.Client, model_id: str) -> list[tuple[bytes, str]]:
    q = {
        "operationName": "ModelFiles",
        "variables": {"id": model_id},
        "query": "query ModelFiles($id: ID!) { model: print(id: $id) { id name stls { id name } "
                 "gcodes { id name } slas { id name } otherFiles { id name } } }",
    }
    r = client.post(_PRINTABLES_GQL, json=q, headers={"Content-Type": "application/json"})
    data = r.json().get("data", {}).get("model") if r.status_code == 200 else None
    if not data:
        raise FetchError("Printables: model sa nenašiel (alebo zmenili API).")
    files = [(f, "stl") for f in data.get("stls") or []]
    files += [(f, "other") for f in data.get("otherFiles") or [] if re.search(r"\.(3mf|zip|obj)$", f["name"], re.I)]
    if not files:
        raise FetchError("Printables: model nemá súbory STL ani 3MF.")
    # ak je k dispozícii 3MF, má prednosť (obsahuje natočenie dielov)
    has3 = [f for f in files if f[0]["name"].lower().endswith(".3mf")]
    if has3:
        files = has3
    out = []
    for f, ftype in files:
        mut = {
            "operationName": "GetDownloadLink",
            "variables": {"id": f["id"], "modelId": model_id, "fileType": ftype, "source": "model_detail"},
            "query": "mutation GetDownloadLink($id: ID!, $modelId: ID!, $fileType: DownloadFileTypeEnum!, "
                     "$source: DownloadSourceEnum!) { getDownloadLink(id: $id, printId: $modelId, "
                     "fileType: $fileType, source: $source) { ok output { link } } }",
        }
        rr = client.post(_PRINTABLES_GQL, json=mut, headers={"Content-Type": "application/json"})
        link = (((rr.json().get("data") or {}).get("getDownloadLink") or {}).get("output") or {}).get("link")
        if not link:
            raise FetchError(f"Printables: nepodarilo sa získať odkaz na {f['name']}")
        data, _ = _download(client, link)
        out.append((data, f["name"]))
    return out


# --- hlavná funkcia ------------------------------------------------------


def fetch(url: str) -> list[tuple[bytes, str]]:
    """Vráti zoznam (obsah, názov súboru)."""
    url = url.strip()
    if not re.match(r"https?://", url):
        raise FetchError("Odkaz musí začínať http:// alebo https://")
    host = urlparse(url).netloc.lower()
    with _client() as client:
        if "printables.com" in host:
            m = re.search(r"/model/(\d+)", url)
            if not m:
                raise FetchError("Printables: v odkaze chýba číslo modelu (…/model/123456-nazov).")
            return _printables(client, m.group(1))
        if "thingiverse.com" in host:
            m = re.search(r"thing:(\d+)", url)
            if not m:
                raise FetchError("Thingiverse: v odkaze chýba thing:číslo.")
            try:
                return [_download(client, f"https://www.thingiverse.com/thing:{m.group(1)}/zip")]
            except FetchError as exc:
                raise FetchError("Thingiverse nedovolil stiahnutie bez prihlásenia – stiahni ZIP ručne a nahraj ho ako súbor.") from exc
        if "makerworld.com" in host or "cults3d.com" in host or "myminifactory.com" in host:
            raise FetchError(f"{host} vyžaduje prihlásenie na stiahnutie – stiahni súbor ručne a nahraj ho.")
        return [_download(client, url)]
