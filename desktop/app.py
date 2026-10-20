"""Kompilátor Cura → Ender-3 Pro – aplikácia pre Windows.

Spustí lokálny server (len 127.0.0.1) s rovnakým rozhraním ako webová verzia
a otvorí ho v okne aplikácie. Úlohy slicuje CuraEngine z nainštalovanej Cura 5.12.
"""
import ctypes
import logging
import os
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import Body, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from desktop.worker import Processor, Settings, app_dirs  # noqa: E402
from kompilator.cura import CuraError, find_cura  # noqa: E402
from server.app import create_app  # noqa: E402
from server.storage import LocalStore  # noqa: E402

TITLE = "Kompilátor Cura → Ender-3 Pro"
DATA_DIR, SETTINGS_FILE = app_dirs()
DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=DATA_DIR.parent / "kompilator.log", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")

store = LocalStore(DATA_DIR)
settings = Settings(SETTINGS_FILE)
processor = Processor(store, settings)
app = create_app(store, mode="desktop", on_ready=processor.submit)
window = None


def _job(job_id):
    data, _ = store.read_json("jobs.json", {"jobs": []})
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "Úloha neexistuje")
    return job


def _out_file(job_id, name) -> Path:
    p = store.local_path(f"files/{job_id}/out/{Path(name).name}")
    if not p.exists():
        raise HTTPException(404, "Súbor neexistuje")
    return p


def removable_drives() -> list[dict]:
    out = []
    if os.name != "nt":
        return out
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << i):
            root = f"{letter}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 2:  # DRIVE_REMOVABLE
                label = ctypes.create_unicode_buffer(64)
                ctypes.windll.kernel32.GetVolumeInformationW(root, label, 64, None, None, None, None, 0)
                try:
                    free = shutil.disk_usage(root).free
                except OSError:
                    continue  # čítačka bez karty
                out.append({"root": root, "label": label.value or "SD karta", "free": free})
    return out


@app.get("/api/desktop/settings")
def get_settings():
    try:
        cura = str(find_cura(settings.get("cura_dir") or None))
    except CuraError as exc:
        cura = None
        cura_err = str(exc)
    else:
        cura_err = None
    return {**settings.public(), "cura_found": cura, "cura_error": cura_err, "cloud_status": processor.cloud_status,
            "drives": removable_drives()}


@app.post("/api/desktop/settings")
def set_settings(body: dict = Body(...)):
    allowed = {k: body[k] for k in ("output_dir", "cura_dir", "cloud_enabled", "copy_to_output") if k in body}
    settings.update(allowed)
    return get_settings()


@app.post("/api/desktop/cloud-login")
def cloud_login(body: dict = Body(...)):
    try:
        processor.cloud_login(str(body.get("url", "")), str(body.get("password", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Server sa nedá zastihnúť: {exc}") from exc
    return get_settings()


@app.post("/api/desktop/cloud-logout")
def cloud_logout():
    settings.update({"cloud_token": "", "cloud_enabled": False})
    return get_settings()


@app.get("/api/desktop/file/{job_id}/{name}")
def get_file(job_id: str, name: str):
    return FileResponse(_out_file(job_id, name), filename=Path(name).name)


@app.post("/api/desktop/open")
def open_thing(body: dict = Body(...)):
    job = _job(body.get("job"))
    how = body.get("how")
    if how == "folder":
        folder = (job.get("result") or {}).get("folder") or str(store.local_path(f"files/{job['id']}/out"))
        os.startfile(folder)  # noqa: S606
    elif how == "cura":
        f = _out_file(job["id"], body.get("name"))
        exe = find_cura(settings.get("cura_dir") or None) / "UltiMaker-Cura.exe"
        subprocess.Popen([str(exe), str(f)], close_fds=True)
    elif how == "file":
        os.startfile(_out_file(job["id"], body.get("name")))  # noqa: S606
    else:
        raise HTTPException(400, "Neznáma akcia")
    return {"ok": True}


@app.post("/api/desktop/to-sd")
def to_sd(body: dict = Body(...)):
    src = _out_file(body.get("job"), body.get("name"))
    drives = removable_drives()
    root = body.get("drive") or (drives[0]["root"] if drives else None)
    if not root or root not in [d["root"] for d in drives]:
        raise HTTPException(400, "Nenašiel som SD kartu – vlož ju do čítačky.")
    # Ender-3 Pro (Marlin) zobrazuje len krátke názvy – skrátime, nech je v menu čitateľný
    name = src.stem[:40] + src.suffix
    shutil.copy2(src, Path(root) / name)
    return {"ok": True, "path": str(Path(root) / name)}


@app.post("/api/desktop/save-as")
def save_as(body: dict = Body(...)):
    src = _out_file(body.get("job"), body.get("name"))
    import webview  # noqa: PLC0415

    res = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=src.name) if window else None
    if not res:
        return {"ok": False}
    target = res if isinstance(res, str) else res[0]
    shutil.copy2(src, target)
    return {"ok": True, "path": target}


@app.post("/api/desktop/pick-folder")
def pick_folder():
    import webview  # noqa: PLC0415

    res = window.create_file_dialog(webview.FOLDER_DIALOG) if window else None
    return {"path": (res[0] if res else None)}


app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="ui")


def _free_port(preferred=8765) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 30)):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    global window
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/?desktop=1"
    if "--browser" in sys.argv:
        import webbrowser  # noqa: PLC0415

        webbrowser.open(url)
        while True:
            time.sleep(3600)
    import webview  # noqa: PLC0415

    window = webview.create_window(TITLE, url, width=1180, height=820, min_size=(420, 600), background_color="#0b0d12")
    webview.start(private_mode=False)


if __name__ == "__main__":
    main()
