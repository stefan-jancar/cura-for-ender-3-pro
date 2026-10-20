"""Spracovanie úloh na PC: lokálne úlohy aj fronta z mobilu (Vercel)."""
import gzip
import json
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
import traceback
from pathlib import Path

import httpx

from kompilator import fetch, pipeline
from kompilator.cura import find_cura
from server.storage import CHUNK, update_json

log = logging.getLogger("kompilator.worker")

APP_NAME = "KompilatorCuraEnder3Pro"


def app_dirs() -> tuple[Path, Path]:
    base = Path(os.environ.get("KOMPILATOR_HOME") or Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME)
    return base / "data", base / "settings.json"


def default_output() -> Path:
    docs = Path.home() / "Documents"
    return docs / "Ender3Pro G-code"


class Settings:
    DEFAULTS = {
        "output_dir": str(default_output()),
        "cura_dir": "",
        "cloud_url": "",
        "cloud_token": "",
        "cloud_enabled": False,
        "copy_to_output": True,
    }

    def __init__(self, path: Path):
        self.path = path
        self.data = dict(self.DEFAULTS)
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text("utf-8")))
            except Exception:
                pass
        self.lock = threading.Lock()

    def get(self, k):
        return self.data.get(k, self.DEFAULTS.get(k))

    def update(self, d: dict):
        with self.lock:
            for k in self.DEFAULTS:
                if k in d:
                    self.data[k] = d[k]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")

    def public(self) -> dict:
        d = {k: v for k, v in self.data.items() if k != "cloud_token"}
        d["cloud_logged_in"] = bool(self.data.get("cloud_token"))
        return d


def _safe_dir(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.()" else "_" for c in name).strip(" .")
    return keep[:60] or "model"


class Processor:
    """Spracúva úlohy jednu po druhej (CuraEngine aj tak využije všetky jadrá)."""

    def __init__(self, store, settings: Settings):
        self.store = store
        self.settings = settings
        self.q: queue.Queue = queue.Queue()
        self.cloud_status = {"state": "vypnuté", "detail": "", "last": 0}
        threading.Thread(target=self._loop, daemon=True, name="processor").start()
        threading.Thread(target=self._cloud_loop, daemon=True, name="cloud").start()
        # úlohy, ktoré zostali rozrobené po zatvorení aplikácie
        data, _ = store.read_json("jobs.json", {"jobs": []})
        for j in reversed(data["jobs"]):
            if j["status"] in ("caka", "spracuva"):
                self.q.put(j["id"])

    def submit(self, job_id: str):
        self.q.put(job_id)

    # --- pomocné -------------------------------------------------------
    def _set(self, job_id, **fields):
        def fn(data):
            for j in data["jobs"]:
                if j["id"] == job_id:
                    j.update(fields)
                    return j
        return update_json(self.store, "jobs.json", {"jobs": []}, fn)

    def _job(self, job_id):
        data, _ = self.store.read_json("jobs.json", {"jobs": []})
        return next((j for j in data["jobs"] if j["id"] == job_id), None)

    def cura_dir(self):
        return find_cura(self.settings.get("cura_dir") or None)

    def _process(self, job: dict, inputs, out_dir: Path, progress) -> dict:
        opts = pipeline.Options.from_dict(job["options"])
        return pipeline.run(inputs, out_dir, opts, cura_dir=self.cura_dir(), progress=progress, title=job["title"])

    def _copy_out(self, job: dict, out_dir: Path) -> str | None:
        if not self.settings.get("copy_to_output"):
            return None
        target = Path(self.settings.get("output_dir")) / _safe_dir(job["title"])
        target.mkdir(parents=True, exist_ok=True)
        for f in out_dir.iterdir():
            if f.suffix in (".gcode", ".3mf", ".curaprofile"):
                shutil.copy2(f, target / f.name)
        return str(target)

    @staticmethod
    def _result(manifest: dict, out_dir: Path, folder: str | None) -> dict:
        files = []
        for f in manifest["files"]:
            p = out_dir / f["name"]
            files.append({**f, "size": p.stat().st_size})
        res = {k: v for k, v in manifest.items() if k != "files"}
        res["files"] = files
        res["folder"] = folder
        return res

    # --- lokálne úlohy -------------------------------------------------
    def _loop(self):
        while True:
            job_id = self.q.get()
            job = self._job(job_id)
            if not job or job["status"] not in ("caka", "spracuva"):
                continue
            last = [0.0]

            def progress(msg, frac, job_id=job_id):
                if time.time() - last[0] > 0.7 or frac in (None, 1.0):
                    last[0] = time.time()
                    self._set(job_id, progress={"msg": msg, "frac": frac or 0})

            try:
                self._set(job_id, status="spracuva", progress={"msg": "Začínam", "frac": 0}, error=None)
                inputs = self._local_inputs(job, progress)
                out_dir = self.store.local_path(f"files/{job_id}/out")
                shutil.rmtree(out_dir, ignore_errors=True)
                manifest = self._process(job, inputs, out_dir, progress)
                folder = self._copy_out(job, out_dir)
                self._set(job_id, status="hotovo", progress=None, result=self._result(manifest, out_dir, folder),
                          finished=int(time.time()))
            except Exception as exc:  # chybu ukážeme v aplikácii
                log.error("Úloha %s zlyhala: %s", job_id, traceback.format_exc())
                self._set(job_id, status="chyba", progress=None, error=str(exc) or exc.__class__.__name__)

    def _local_inputs(self, job, progress):
        if job.get("url"):
            progress("Sťahujem model z odkazu", 0.01)
            return fetch.fetch(job["url"])
        inputs = []
        client = self._cloud() if job.get("cloud_id") else None  # úloha z mobilu – zdroj je v cloude
        for f in job["src"]:
            if client:
                data = b"".join(client.get(f"/api/jobs/{job['cloud_id']}/file/src/{f['name']}/{i}").raise_for_status().content
                                for i in range(f["chunks"]))
            else:
                data = b"".join(self.store.read_bytes(f"files/{job['id']}/src/{f['name']}.{i:04d}") for i in range(f["chunks"]))
            inputs.append((data, f["name"]))
        if client:
            client.close()
        return inputs

    # --- fronta z mobilu (Vercel) ----------------------------------------
    def _cloud(self) -> httpx.Client | None:
        url = (self.settings.get("cloud_url") or "").rstrip("/")
        token = self.settings.get("cloud_token")
        if not (self.settings.get("cloud_enabled") and url and token):
            return None
        return httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=120, follow_redirects=True)

    def cloud_login(self, url: str, password: str):
        url = url.strip().rstrip("/")
        if not url.startswith("https://") and not url.startswith("http://localhost") and not url.startswith("http://127."):
            raise ValueError("Adresa musí začínať https://")
        with httpx.Client(timeout=30, follow_redirects=True) as c:
            r = c.post(url + "/api/login", json={"password": password})
            if r.status_code != 200:
                raise ValueError(r.json().get("detail", "Prihlásenie zlyhalo") if r.headers.get("content-type", "").startswith("application/json") else f"Chyba {r.status_code}")
            self.settings.update({"cloud_url": url, "cloud_token": r.json()["token"], "cloud_enabled": True})

    def _cloud_loop(self):
        while True:
            client = self._cloud()
            if client is None:
                self.cloud_status = {"state": "vypnuté", "detail": "", "last": 0}
                time.sleep(5)
                continue
            try:
                with client:
                    r = client.post("/api/worker/claim", json={})
                    if r.status_code == 401:
                        self.settings.update({"cloud_token": ""})
                        raise RuntimeError("Prihlásenie do cloudu vypršalo – prihlás sa znova v Nastaveniach")
                    r.raise_for_status()
                    self.cloud_status = {"state": "online", "detail": "Čakám na úlohy z mobilu", "last": int(time.time())}
                    job = r.json().get("job")
                    if job:
                        self._cloud_job(client, job)
                        continue
            except Exception as exc:
                self.cloud_status = {"state": "chyba", "detail": str(exc)[:200], "last": int(time.time())}
                time.sleep(30)
                continue
            time.sleep(15)

    def _cloud_job(self, client: httpx.Client, job: dict):
        jid = job["id"]
        self.cloud_status["detail"] = f"Spracúvam: {job['title']}"
        # lokálna kópia úlohy, aby bola vidno aj v aplikácii na PC
        local = {**job, "origin": "mobil", "status": "spracuva", "cloud_id": jid}

        def add(data):
            data["jobs"] = [j for j in data["jobs"] if j.get("cloud_id") != jid and j["id"] != jid]
            data["jobs"].insert(0, local)
        update_json(self.store, "jobs.json", {"jobs": []}, add)
        last = [0.0, ""]

        def progress(msg, frac):
            self._set(jid, progress={"msg": msg, "frac": frac or 0})
            stage = msg.split(":")[0]
            if time.time() - last[0] > 20 or stage != last[1]:
                last[0], last[1] = time.time(), stage
                try:
                    client.post(f"/api/worker/{jid}/progress", json={"msg": msg, "frac": frac or 0})
                except Exception:
                    pass

        try:
            if job.get("url"):
                progress("Sťahujem model z odkazu", 0.01)
                inputs = fetch.fetch(job["url"])
            else:
                inputs = []
                for f in job["src"]:
                    parts = []
                    for i in range(f["chunks"]):
                        r = client.get(f"/api/jobs/{jid}/file/src/{f['name']}/{i}")
                        r.raise_for_status()
                        parts.append(r.content)
                    inputs.append((b"".join(parts), f["name"]))
            with tempfile.TemporaryDirectory(prefix="kompilator_") as tmp:
                out_dir = Path(tmp)
                manifest = self._process(job, inputs, out_dir, progress)
                # výsledky do lokálneho úložiska a výstupného priečinka
                local_out = self.store.local_path(f"files/{jid}/out")
                shutil.rmtree(local_out, ignore_errors=True)
                shutil.copytree(out_dir, local_out)
                folder = self._copy_out(job, out_dir)
                result = self._result(manifest, out_dir, folder)
                # nahratie do cloudu (gzip, po kúskoch)
                progress("Nahrávam výsledky pre mobil", 0.97)
                for f in result["files"]:
                    raw = (out_dir / f["name"]).read_bytes()
                    packed = gzip.compress(raw, 6)
                    chunks = [packed[i:i + CHUNK] for i in range(0, len(packed), CHUNK)] or [b""]
                    for i, c in enumerate(chunks):
                        r = client.put(f"/api/worker/{jid}/out/{f['name']}/{i}", content=c,
                                       headers={"Content-Type": "application/octet-stream"})
                        r.raise_for_status()
                    f.update(chunks=len(chunks), gzip=True)
            client.post(f"/api/worker/{jid}/done", json={"result": result}).raise_for_status()
            self._set(jid, status="hotovo", progress=None, result=result, finished=int(time.time()))
        except Exception as exc:
            log.error("Cloud úloha %s zlyhala: %s", jid, traceback.format_exc())
            msg = str(exc) or exc.__class__.__name__
            self._set(jid, status="chyba", progress=None, error=msg)
            try:
                client.post(f"/api/worker/{jid}/done", json={"error": msg})
            except Exception:
                pass
