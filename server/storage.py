"""Úložisko fronty a súborov.

- GitHubStore: na Verceli – všetko sa ukladá ako commity do GitHub repozitára s dátami
  (rovnako ako Môj priestor). Súbory sú rozdelené na kúsky ≤ 3 MB kvôli limitu Vercelu.
- LocalStore: v aplikácii na PC – priečinok na disku.
"""
import base64
import json
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx

CHUNK = 3 * 1024 * 1024


class StorageError(Exception):
    pass


class Conflict(StorageError):
    pass


class LocalStore:
    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise StorageError("Neplatná cesta")
        return p

    def read_json(self, path, default):
        p = self._p(path)
        if not p.exists():
            return default, None
        return json.loads(p.read_text("utf-8")), str(p.stat().st_mtime_ns)

    def write_json(self, path, data, sha, message=""):
        p = self._p(path)
        if sha is not None and p.exists() and str(p.stat().st_mtime_ns) != sha:
            raise Conflict("Súbor sa medzitým zmenil")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(p)

    def read_bytes(self, path) -> bytes:
        p = self._p(path)
        if not p.exists():
            raise StorageError("Súbor neexistuje")
        return p.read_bytes()

    def write_bytes(self, path, content: bytes, message=""):
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def delete_tree(self, path, message=""):
        p = self._p(path)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()

    def local_path(self, path) -> Path:
        return self._p(path)


class GitHubStore:
    kind = "github"

    def __init__(self, token: str, repo: str, branch: str = "main", prefix: str = "kompilator"):
        self.repo = repo
        self.branch = branch
        self.prefix = prefix.strip("/")
        self.http = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "kompilator-cura-ender3pro",
            },
            timeout=60,
        )

    def _url(self, path):
        return f"/repos/{self.repo}/contents/{quote(self.prefix + '/' + path.strip('/'))}"

    def _req(self, method, url, **kw):
        r = self.http.request(method, url, **kw)
        if r.status_code == 409 or (r.status_code == 422 and "sha" in r.text):
            raise Conflict("Súbor sa medzitým zmenil")
        if r.status_code >= 400 and r.status_code != 404:
            raise StorageError(f"GitHub {r.status_code}: {r.text[:200]}")
        return r

    def read_json(self, path, default):
        r = self._req("GET", self._url(path), params={"ref": self.branch})
        if r.status_code == 404:
            return default, None
        d = r.json()
        if d.get("content") is None or d.get("encoding") != "base64" or not d.get("content"):
            raw = self._req("GET", self._url(path), params={"ref": self.branch},
                            headers={"Accept": "application/vnd.github.raw"}).content
        else:
            raw = base64.b64decode(d["content"])
        return json.loads(raw.decode("utf-8")), d["sha"]

    def write_json(self, path, data, sha, message="kompilátor: fronta"):
        body = {
            "message": message,
            "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=1).encode()).decode(),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        self._req("PUT", self._url(path), json=body)

    def read_bytes(self, path) -> bytes:
        r = self._req("GET", self._url(path), params={"ref": self.branch}, headers={"Accept": "application/vnd.github.raw"})
        if r.status_code == 404:
            raise StorageError("Súbor neexistuje")
        return r.content

    def write_bytes(self, path, content: bytes, message="kompilátor: súbor"):
        sha = None
        head = self._req("GET", self._url(path), params={"ref": self.branch})
        if head.status_code == 200 and isinstance(head.json(), dict):
            sha = head.json().get("sha")
        body = {"message": message, "content": base64.b64encode(content).decode(), "branch": self.branch}
        if sha:
            body["sha"] = sha
        self._req("PUT", self._url(path), json=body)

    def delete_tree(self, path, message="kompilátor: zmazanie"):
        r = self._req("GET", self._url(path), params={"ref": self.branch})
        if r.status_code == 404:
            return
        items = r.json()
        if isinstance(items, dict):
            items = [items]
        for it in items:
            rel = it["path"][len(self.prefix) + 1:]
            if it["type"] == "dir":
                self.delete_tree(rel, message)
            else:
                self._req("DELETE", self._url(rel), json={"message": message, "sha": it["sha"], "branch": self.branch})


def update_json(store, path, default, fn, message="kompilátor: fronta", tries=6):
    """Načíta JSON, upraví ho funkciou `fn` a zapíše; pri súbehu to skúsi znova."""
    for i in range(tries):
        data, sha = store.read_json(path, default)
        result = fn(data)
        try:
            store.write_json(path, data, sha, message)
            return result
        except Conflict:
            time.sleep(0.4 * (i + 1))
    raise StorageError("Nepodarilo sa uložiť (príliš veľa súbežných zmien)")
