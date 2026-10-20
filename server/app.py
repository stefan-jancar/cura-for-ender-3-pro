"""API fronty úloh – rovnaké pre Vercel (cloud) aj pre aplikáciu na PC (desktop).

Stavy úlohy: nahrava → caka → spracuva → hotovo / chyba
Súbory sa prenášajú po kúskoch (≤ 3 MB), lebo Vercel prijme najviac ~4,5 MB naraz.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response

from .storage import CHUNK, StorageError, update_json

JOBS = "jobs.json"
TOKEN_DAYS = 365
MAX_JOBS = 60
WORKER_WRITE_EVERY = 90  # s – ako často sa zapisuje „PC je online“ (každý zápis je commit)

MATERIALS = [("auto", "Auto"), ("PLA", "PLA"), ("PETG", "PETG"), ("TPU", "TPU"), ("ABS", "ABS")]
QUALITIES = [("rychla", "Rýchla 0,28"), ("standard", "Štandard 0,2"), ("jemna", "Jemná 0,12")]

_NAME_OK = re.compile(r"^[^/\\:*?\"<>|]{1,120}$")


def _secret() -> bytes:
    s = os.environ.get("APP_SECRET") or ("pw:" + os.environ.get("APP_PASSWORD", ""))
    return hashlib.sha256(s.encode()).digest()


def make_token() -> str:
    exp = str(int(time.time()) + TOKEN_DAYS * 86400)
    sig = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{exp}.{sig}".encode()).decode()


def valid_token(token: str) -> bool:
    try:
        exp, sig = base64.urlsafe_b64decode(token.encode()).decode().split(".")
    except Exception:
        return False
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and int(exp) > time.time()


def _title_from_url(url: str) -> str:
    """https://www.printables.com/model/3161-3d-benchy → „3d benchy“."""
    path = re.sub(r"[?#].*$", "", url).rstrip("/")
    last = path.rsplit("/", 1)[-1]
    last = re.sub(r"\.(stl|3mf|obj|zip)$", "", last, flags=re.I)
    last = re.sub(r"^(thing:)?\d+-?", "", last)
    nice = re.sub(r"[-_]+", " ", last).strip()
    return (nice or re.sub(r"^https?://(www\.)?", "", url))[:60]


def _clean_name(name: str) -> str:
    name = name.strip().replace("\\", "/").split("/")[-1]
    if not _NAME_OK.match(name) or name in (".", ".."):
        raise HTTPException(400, "Neplatný názov súboru")
    return name


def _opts(d: dict | None) -> dict:
    d = d or {}
    mats = {m for m, _ in MATERIALS}
    quals = {q for q, _ in QUALITIES}
    return {
        "material": d.get("material") if d.get("material") in mats else "auto",
        "quality": d.get("quality") if d.get("quality") in quals else "standard",
        "strong": bool(d.get("strong")),
        "supports": d.get("supports") if d.get("supports") in ("auto", "on", "off") else "auto",
        "adhesion": d.get("adhesion") if d.get("adhesion") in ("auto", "skirt", "brim") else "auto",
    }


def create_app(store, mode: str = "cloud", on_ready=None, auth_required: bool | None = None) -> FastAPI:
    """mode=cloud: Vercel, úlohy spracuje PC cez /api/worker. mode=desktop: `on_ready(job_id)` spracuje hneď."""
    app = FastAPI(title="Kompilátor Cura Ender-3 Pro")
    if auth_required is None:
        auth_required = mode == "cloud"
    on_vercel = bool(os.environ.get("VERCEL"))

    def auth(request: Request):
        if not auth_required:
            return
        if not os.environ.get("APP_PASSWORD"):
            if on_vercel:
                raise HTTPException(503, "Na Verceli nastav premennú APP_PASSWORD.")
            return
        header = request.headers.get("authorization", "")
        if not (header.startswith("Bearer ") and valid_token(header[7:])):
            raise HTTPException(401, "Neprihlásený")

    @app.exception_handler(StorageError)
    async def _storage_err(_, exc):
        return Response(f'{{"detail": "Úložisko: {str(exc)[:200]}"}}', status_code=502, media_type="application/json")

    api = APIRouter(prefix="/api")

    def load():
        data, _ = store.read_json(JOBS, {"jobs": []})
        return data

    def find(data, job_id):
        for j in data["jobs"]:
            if j["id"] == job_id:
                return j
        raise HTTPException(404, "Úloha neexistuje")

    def mutate(job_id, fn, message="kompilátor: úloha"):
        def inner(data):
            return fn(find(data, job_id), data)
        return update_json(store, JOBS, {"jobs": []}, inner, message)

    # --- verejné ---------------------------------------------------------
    @api.get("/info")
    def info():
        seen = 0
        try:
            seen = load().get("worker_seen", 0)
        except StorageError:
            pass
        return {
            "mode": mode,
            "auth_required": bool(auth_required and (os.environ.get("APP_PASSWORD") or on_vercel)),
            "worker_seen": seen,
            "worker_online": mode == "desktop" or time.time() - seen < WORKER_WRITE_EVERY * 3,
            "materials": MATERIALS,
            "qualities": QUALITIES,
            "chunk": CHUNK,
            "storage": getattr(store, "kind", "?"),
        }

    @api.post("/login")
    def login(body: dict = Body(...)):
        pw = os.environ.get("APP_PASSWORD", "")
        if not pw or not hmac.compare_digest(str(body.get("password", "")), pw):
            time.sleep(1)
            raise HTTPException(401, "Nesprávne heslo")
        return {"token": make_token()}

    # --- úlohy -----------------------------------------------------------
    @api.get("/jobs", dependencies=[Depends(auth)])
    def jobs():
        return load()["jobs"]

    @api.post("/jobs", dependencies=[Depends(auth)])
    def create_job(body: dict = Body(...)):
        url = (body.get("url") or "").strip()
        files = []
        for f in body.get("files") or []:
            size = int(f.get("size", 0))
            if size <= 0 or size > 400 * 1024 * 1024:
                raise HTTPException(400, "Súbor je prázdny alebo väčší ako 400 MB")
            files.append({"name": _clean_name(str(f.get("name", ""))), "size": size, "chunks": -(-size // CHUNK)})
        if not url and not files:
            raise HTTPException(400, "Zadaj odkaz alebo nahraj súbor")
        if url and not re.match(r"^https?://", url):
            raise HTTPException(400, "Odkaz musí začínať http:// alebo https://")
        title = (body.get("title") or "").strip()[:80]
        if not title:
            title = files[0]["name"].rsplit(".", 1)[0] if files else _title_from_url(url)
        job = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3),
            "title": title,
            "created": int(time.time()),
            "status": "nahrava" if files else "caka",
            "origin": body.get("origin") or ("pc" if mode == "desktop" else "mobil"),
            "url": url or None,
            "src": files,
            "options": _opts(body.get("options")),
            "progress": None,
            "result": None,
            "error": None,
        }

        def add(data):
            data["jobs"].insert(0, job)
            # staré úlohy sa zo zoznamu odstránia (súbory zostanú, kým ich nezmažeš)
            del data["jobs"][MAX_JOBS:]
            return job

        update_json(store, JOBS, {"jobs": []}, add, f"kompilátor: nová úloha {job['title']}")
        if job["status"] == "caka" and on_ready:
            on_ready(job["id"])
        return job

    @api.put("/jobs/{job_id}/src/{name}/{chunk}", dependencies=[Depends(auth)])
    async def upload_src(job_id: str, name: str, chunk: int, request: Request):
        body = await request.body()
        if len(body) > CHUNK:
            raise HTTPException(413, "Kúsok je príliš veľký")
        name = _clean_name(name)
        job = find(load(), job_id)
        if not any(f["name"] == name for f in job["src"]):
            raise HTTPException(400, "Tento súbor úloha nemá")
        store.write_bytes(f"files/{job_id}/src/{name}.{chunk:04d}", body, f"kompilátor: {name} ({chunk + 1})")
        return {"ok": True}

    @api.post("/jobs/{job_id}/ready", dependencies=[Depends(auth)])
    def ready(job_id: str):
        def fn(job, data):
            if job["status"] == "nahrava":
                job["status"] = "caka"
            return job
        job = mutate(job_id, fn)
        if on_ready:
            on_ready(job_id)
        return job

    @api.post("/jobs/{job_id}/retry", dependencies=[Depends(auth)])
    def retry(job_id: str, body: dict | None = Body(None)):
        def fn(job, data):
            job.update(status="caka", progress=None, error=None, result=None)
            if body and body.get("options"):
                job["options"] = _opts(body["options"])
            return job
        job = mutate(job_id, fn, "kompilátor: znova")
        if on_ready:
            on_ready(job_id)
        return job

    @api.delete("/jobs/{job_id}", dependencies=[Depends(auth)])
    def delete(job_id: str):
        def fn(data):
            data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
        update_json(store, JOBS, {"jobs": []}, fn, "kompilátor: zmazanie úlohy")
        store.delete_tree(f"files/{job_id}")
        return {"ok": True}

    @api.get("/jobs/{job_id}/file/{area}/{name}/{chunk}", dependencies=[Depends(auth)])
    def get_chunk(job_id: str, area: str, name: str, chunk: int):
        if area not in ("src", "out"):
            raise HTTPException(400, "Neplatná oblasť")
        name = _clean_name(name)
        data = store.read_bytes(f"files/{job_id}/{area}/{name}.{chunk:04d}")
        return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "private, max-age=86400"})

    # --- spracovanie na PC (worker) --------------------------------------
    @api.post("/worker/claim", dependencies=[Depends(auth)])
    def claim(body: dict | None = Body(None)):
        now = int(time.time())

        def fn(data):
            for j in reversed(data["jobs"]):  # najstaršia čakajúca
                if j["status"] == "caka" or (j["status"] == "spracuva" and now - (j.get("claimed") or 0) > 3600):
                    j.update(status="spracuva", claimed=now, progress={"msg": "PC prevzalo úlohu", "frac": 0})
                    data["worker_seen"] = now
                    return dict(j)
            if now - data.get("worker_seen", 0) > WORKER_WRITE_EVERY:
                data["worker_seen"] = now
                return None
            raise _NoWrite()

        try:
            return {"job": update_json(store, JOBS, {"jobs": []}, fn, "kompilátor: PC")}
        except _NoWrite:
            return {"job": None}

    @api.post("/worker/{job_id}/progress", dependencies=[Depends(auth)])
    def progress(job_id: str, body: dict = Body(...)):
        def fn(job, data):
            job["progress"] = {"msg": str(body.get("msg", ""))[:120], "frac": float(body.get("frac") or 0)}
            data["worker_seen"] = int(time.time())
        mutate(job_id, fn, "kompilátor: priebeh")
        return {"ok": True}

    @api.put("/worker/{job_id}/out/{name}/{chunk}", dependencies=[Depends(auth)])
    async def upload_out(job_id: str, name: str, chunk: int, request: Request):
        body = await request.body()
        if len(body) > CHUNK:
            raise HTTPException(413, "Kúsok je príliš veľký")
        store.write_bytes(f"files/{job_id}/out/{_clean_name(name)}.{chunk:04d}", body, f"kompilátor: výsledok {name}")
        return {"ok": True}

    @api.post("/worker/{job_id}/done", dependencies=[Depends(auth)])
    def done(job_id: str, body: dict = Body(...)):
        def fn(job, data):
            if body.get("error"):
                job.update(status="chyba", error=str(body["error"])[:2000], progress=None)
            else:
                job.update(status="hotovo", result=body.get("result"), progress=None, finished=int(time.time()))
            data["worker_seen"] = int(time.time())
            return job
        return mutate(job_id, fn, "kompilátor: hotovo")

    app.include_router(api)
    app.state.store = store
    return app


class _NoWrite(Exception):
    """Interný signál pre update_json: nič sa nezmenilo, netreba zapisovať (commitovať)."""
