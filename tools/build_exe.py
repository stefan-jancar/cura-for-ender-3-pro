"""Zostaví Windows aplikáciu (PyInstaller, priečinok dist/KompilatorCura). Spusti z koreňa repozitára."""
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
sep = ";" if sys.platform == "win32" else ":"

PyInstaller.__main__.run([
    str(ROOT / "desktop" / "app.py"),
    "--name", "KompilatorCura",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--icon", str(ROOT / "desktop" / "assets" / "icon.ico"),
    "--add-data", f"{ROOT / 'public'}{sep}public",
    "--paths", str(ROOT),
    "--collect-all", "trimesh",
    "--collect-all", "manifold3d",
    "--collect-all", "webview",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "scipy.spatial",
    "--hidden-import", "clr",
    "--exclude-module", "tkinter",
    "--exclude-module", "matplotlib",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
])
print("Hotovo: dist/KompilatorCura/KompilatorCura.exe")
