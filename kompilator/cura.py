"""Práca s nainštalovanou Cura: nájdenie inštalácie, slicovanie cez CuraEngine, export profilu a 3MF."""
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import trimesh

from .cura_settings import Resolver, engine_str, expand_gcode

CURA_SETTING_VERSION = 26  # Cura 5.12 / 5.13


class CuraError(Exception):
    pass


def find_cura(preferred: str | None = None) -> Path:
    """Nájde inštaláciu UltiMaker Cura (preferuje 5.12, inak najnovšiu)."""
    if preferred and (Path(preferred) / "CuraEngine.exe").exists():
        return Path(preferred)
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")), Path(r"C:\Program Files")]
    found = []
    for root in roots:
        if root.exists():
            for d in root.glob("UltiMaker Cura *"):
                if (d / "CuraEngine.exe").exists():
                    m = re.search(r"(\d+)\.(\d+)\.(\d+)", d.name)
                    found.append((tuple(int(x) for x in m.groups()) if m else (0, 0, 0), d))
    if not found:
        raise CuraError("Nenašiel som nainštalovanú UltiMaker Cura (C:\\Program Files\\UltiMaker Cura 5.x).")
    for ver, d in found:
        if ver[:2] == (5, 12):
            return d
    return max(found)[1]


def resources(cura_dir: Path) -> Path:
    return Path(cura_dir) / "share" / "cura" / "resources"


def resolve_settings(cura_dir: Path, values: dict, quality_type: str = "standard") -> tuple[dict, Resolver]:
    r = Resolver(resources(cura_dir), quality_type)
    r.add_values(values)
    s = r.resolve()
    for key in ("machine_start_gcode", "machine_end_gcode"):
        s[key] = expand_gcode(s[key], s)
    return s, r


def _write_defs(folder: Path, settings: dict, resolver: Resolver) -> Path:
    def payload(name, meta):
        children = {}
        for k, v in settings.items():
            t = resolver.defs[k].get("type", "str")
            dv = v if isinstance(v, (int, float)) and not isinstance(v, bool) else engine_str(v)
            children[k] = {"label": k, "type": t, "default_value": dv}
        return {"version": 2, "name": name, "metadata": meta,
                "settings": {"all": {"label": "all", "type": "category", "children": children}}}

    (folder / "kompilator_extruder.def.json").write_text(json.dumps(payload("kompilator_extruder", {"position": "0"})), "utf-8")
    machine = folder / "kompilator_ender3pro.def.json"
    machine.write_text(json.dumps(payload("kompilator_ender3pro", {"machine_extruder_trains": {"0": "kompilator_extruder"}})), "utf-8")
    return machine


def slice_mesh(cura_dir: Path, mesh: trimesh.Trimesh, settings: dict, resolver: Resolver, out_gcode: Path,
               timeout: int = 1800, log=None) -> dict:
    """Nareže sieť (stred podložky = 0,0) na G-code. Vráti čas tlače a spotrebu filamentu."""
    engine = Path(cura_dir) / "CuraEngine.exe"
    with tempfile.TemporaryDirectory(prefix="kompilator_") as tmp:
        tmp = Path(tmp)
        machine = _write_defs(tmp, settings, resolver)
        stl = tmp / "plate.stl"
        mesh.export(stl)
        args = [str(engine), "slice", "-p", "-d", str(tmp), "-j", str(machine), "-l", str(stl), "-o", str(out_gcode)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=flags)
        tail = []
        with proc:
            for line in proc.stdout:
                tail = (tail + [line.rstrip()])[-30:]
                if log and "Progress:" in line:
                    m = re.search(r"Progress:\s*(\w+):\s*\d+\s+(\d+)\s+([\d.]+)", line)
                    if m:
                        log(m.group(1), float(m.group(3)))
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise CuraError("CuraEngine trval príliš dlho") from None
        if proc.returncode != 0 or not out_gcode.exists():
            raise CuraError("CuraEngine zlyhal:\n" + "\n".join(tail[-12:]))
    return finalize_gcode(out_gcode, settings)


def finalize_gcode(path: Path, settings: dict) -> dict:
    """Doplní do hlavičky čas, filament a rozmery (ako to robí Cura) a vráti súhrn."""
    text = path.read_bytes().decode("utf-8", "replace")  # bez prekladu koncov riadkov (Windows by pridal \r)
    elapsed = 0.0
    e_max = 0.0
    e_total = 0.0
    xs, ys, zs = [], [], []
    started = False
    for line in text.splitlines():
        if line.startswith(";TIME_ELAPSED:"):
            elapsed = float(line.split(":")[1])
        elif line.startswith(";LAYER:0"):
            started = True
        elif started and line.startswith(("G1", "G0")):
            mz = re.search(r" Z(-?[\d.]+)", line)
            if mz:
                zs.append(float(mz.group(1)))
            m = re.search(r" E(-?[\d.]+)", line)
            if m:
                e = float(m.group(1))
                e_max = max(e_max, e)
                for ax, arr in (("X", xs), ("Y", ys)):
                    mm = re.search(rf" {ax}(-?[\d.]+)", line)
                    if mm:
                        arr.append(float(mm.group(1)))
        elif line.startswith("G92") and " E0" in line and started:
            e_total += e_max
            e_max = 0.0
    e_total += e_max
    meters = e_total / 1000.0
    diameter = float(settings.get("material_diameter", 1.75))
    volume_cm3 = 3.14159265 * (diameter / 2) ** 2 * e_total / 1000.0
    header = {
        ";TIME:": str(int(elapsed)),
        ";Filament used:": f"{meters:.5f}m",
    }
    if xs and ys:
        header.update({";MINX:": f"{min(xs):.3f}", ";MINY:": f"{min(ys):.3f}", ";MINZ:": f"{min(zs) if zs else 0:.3f}",
                       ";MAXX:": f"{max(xs):.3f}", ";MAXY:": f"{max(ys):.3f}", ";MAXZ:": f"{max(zs) if zs else 0:.3f}"})
    head, sep, rest = text.partition(";Generated with")
    for key, val in header.items():
        head = re.sub(rf"^{re.escape(key)}.*$", key + val, head, count=1, flags=re.M)
    path.write_bytes((head + sep + rest).encode("utf-8"))
    return {"time_s": int(elapsed), "filament_m": round(meters, 2), "filament_cm3": round(volume_cm3, 1)}


def curaprofile(name: str, values: dict, quality_type: str) -> bytes:
    """Profil na import do Cura (Preferences → Profiles → Import)."""
    lines = [
        "[general]", "version = 4", f"name = {name}", "definition = creality_base", "",
        "[metadata]", "type = quality_changes", f"quality_type = {quality_type}",
        f"setting_version = {CURA_SETTING_VERSION}", "", "[values]",
    ]
    for k, v in values.items():
        lines.append(f"{k} = {engine_str(v)}")
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(safe, "\n".join(lines) + "\n")
    return buf.getvalue()


def write_3mf(path: Path, objects: list[tuple[str, trimesh.Trimesh]], bed=(220.0, 220.0)):
    """Jednoduchý 3MF so samostatnými objektmi na pozíciách na podložke – otvorí sa v Cure aj v iných slicer-och."""
    res, items = [], []
    for i, (name, mesh) in enumerate(objects, 1):
        v = "\n".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in mesh.vertices)
        t = "\n".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
        nm = name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        res.append(f'<object id="{i}" name="{nm}" type="model"><mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}" transform="1 0 0 0 1 0 0 0 1 {bed[0] / 2:.3f} {bed[1] / 2:.3f} 0"/>')
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="sk-SK" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Application">Kompilator Cura Ender3Pro</metadata>'
        f'<resources>{"".join(res)}</resources><build>{"".join(items)}</build></model>'
    )
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
