"""Načítanie modelov: STL, OBJ, 3MF (aj projekty Bambu Studio / OrcaSlicer / PrusaSlicer) a ZIP."""
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np
import trimesh

from .config import normalize_material

MODEL_EXT = {".stl", ".obj", ".3mf"}


class ModelError(Exception):
    pass


@dataclass
class Part:
    name: str
    mesh: trimesh.Trimesh
    material: str | None = None  # materiál odhadnutý zo zdrojového projektu
    source: str = ""  # odkiaľ diel pochádza (súbor / platňa v pôvodnom projekte)
    notes: list[str] = field(default_factory=list)


# --- 3MF ---------------------------------------------------------------

_NS = re.compile(r"\{[^}]*\}")


def _matrix(s: str | None) -> np.ndarray:
    """3MF transform (12 čísel, riadkový vektor) → 4×4 matica pre stĺpcové vektory."""
    m = np.eye(4)
    if s:
        v = [float(x) for x in s.split()]
        m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
        m[:3, 3] = v[9:12]
    return m


def _attr(el_text: str, name: str) -> str | None:
    m = re.search(r'(?:^|\s)(?:[\w]+:)?%s="([^"]*)"' % re.escape(name), el_text)
    return m.group(1) if m else None


class _ThreeMF:
    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.names = {n.lstrip("/"): n for n in zf.namelist()}
        self.files: dict[str, dict] = {}  # cesta → {object_id: (kind, data)}

    def _read(self, path: str) -> str:
        key = path.lstrip("/")
        if key not in self.names:
            # niektoré slicery majú v cestách iné veľké/malé písmená
            low = {k.lower(): v for k, v in self.names.items()}
            if key.lower() not in low:
                raise ModelError(f"V 3MF chýba súbor {path}")
            return self.zf.read(low[key.lower()]).decode("utf-8", "replace")
        return self.zf.read(self.names[key]).decode("utf-8", "replace")

    def objects(self, path: str) -> dict:
        if path in self.files:
            return self.files[path]
        text = self._read(path)
        objs = {}
        for m in re.finditer(r"<object\b([^>]*)>(.*?)</object>", text, re.S):
            head, body = m.group(1), m.group(2)
            oid = _attr(head, "id")
            if "<mesh" in body:
                vtxt = re.findall(r'<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"', body)
                if not vtxt:  # iné poradie atribútov
                    vtxt = [(_attr(v, "x"), _attr(v, "y"), _attr(v, "z")) for v in re.findall(r"<vertex\b([^>]*)/?>", body)]
                ttxt = re.findall(r'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"', body)
                if not ttxt:
                    ttxt = [(_attr(t, "v1"), _attr(t, "v2"), _attr(t, "v3")) for t in re.findall(r"<triangle\b([^>]*)/?>", body)]
                V = np.array(vtxt, dtype=float).reshape(-1, 3)
                F = np.array(ttxt, dtype=np.int64).reshape(-1, 3)
                objs[oid] = ("mesh", (V, F))
            else:
                comps = []
                for c in re.findall(r"<component\b([^>]*)/?>", body):
                    comps.append((_attr(c, "path") or path, _attr(c, "objectid"), _matrix(_attr(c, "transform"))))
                objs[oid] = ("components", comps)
        self.files[path] = objs
        return objs

    def flatten(self, path: str, oid: str, m: np.ndarray, skip: set[int] | None = None) -> list[tuple[np.ndarray, np.ndarray]]:
        kind, data = self.objects(path)[oid]
        if kind == "mesh":
            V, F = data
            if len(F) == 0:
                return []
            Vh = np.c_[V, np.ones(len(V))] @ m.T
            return [(Vh[:, :3], F)]
        out = []
        for i, (cpath, coid, cm) in enumerate(data):
            if skip and i in skip:
                continue
            out += self.flatten(cpath, coid, m @ cm)
        return out


def _main_model_path(zf: zipfile.ZipFile) -> str:
    try:
        rels = zf.read("_rels/.rels").decode("utf-8", "replace")
        m = re.search(r'Target="([^"]+\.model)"', rels)
        if m:
            return m.group(1).lstrip("/")
    except KeyError:
        pass
    for n in zf.namelist():
        if n.lower().endswith(".model"):
            return n
    raise ModelError("V 3MF súbore nie je žiadny model")


def _bambu_meta(zf: zipfile.ZipFile):
    """Z projektu Bambu/Orca vytiahne názvy, materiály, neplatné časti a pôvodné platne."""
    names = set(zf.namelist())
    if "Metadata/model_settings.config" not in names:
        return None
    cfg = zf.read("Metadata/model_settings.config").decode("utf-8", "replace")
    filaments = []
    if "Metadata/project_settings.config" in names:
        try:
            ps = json.loads(zf.read("Metadata/project_settings.config"))
            filaments = ps.get("filament_type") or []
        except Exception:
            pass
    objects = {}
    for m in re.finditer(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
        body = m.group(2)
        name = re.search(r'<metadata key="name" value="([^"]*)"', body)
        ext = re.search(r'<metadata key="extruder" value="(\d+)"', body)
        parts = re.findall(r'<part id="\d+" subtype="([^"]+)"', body)
        skip = {i for i, st in enumerate(parts) if st != "normal_part"}
        mat = None
        if ext and filaments:
            idx = int(ext.group(1)) - 1
            if 0 <= idx < len(filaments):
                mat = filaments[idx]
        objects[m.group(1)] = {"name": name.group(1) if name else None, "material": mat, "skip": skip}
    plates = {}
    for pm in re.finditer(r"<plate>(.*?)</plate>", cfg, re.S):
        pid = re.search(r'key="plater_id" value="(\d+)"', pm.group(1))
        for oid in re.findall(r'key="object_id" value="(\d+)"', pm.group(1)):
            plates[oid] = pid.group(1) if pid else "?"
    return {"objects": objects, "plates": plates}


def _prusa_names(zf: zipfile.ZipFile) -> dict:
    out = {}
    for cfgname in ("Metadata/Slic3r_PE_model.config", "Metadata/PrusaSlicer_model.config"):
        if cfgname in zf.namelist():
            cfg = zf.read(cfgname).decode("utf-8", "replace")
            for m in re.finditer(r'<object id="(\d+)"[^>]*>(.*?)</object>', cfg, re.S):
                n = re.search(r'<metadata type="object" key="name" value="([^"]*)"', m.group(2))
                if n:
                    out[m.group(1)] = n.group(1)
    return out


def load_3mf(data: bytes, filename: str) -> list[Part]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ModelError(f"{filename}: poškodený 3MF súbor") from exc
    tm = _ThreeMF(zf)
    main = _main_model_path(zf)
    text = tm._read(main)
    meta = _bambu_meta(zf)
    prusa = _prusa_names(zf)
    tm.objects(main)
    names_in_model = {
        _attr(h, "id"): _attr(h, "name") for h in re.findall(r"<object\b([^>]*)>", text)
    }
    parts = []
    build = re.search(r"<build\b[^>]*>(.*?)</build>", text, re.S)
    items = re.findall(r"<item\b([^>]*)/?>", build.group(1)) if build else []
    for i, item in enumerate(items):
        if _attr(item, "printable") == "0":
            continue
        oid = _attr(item, "objectid")
        info = (meta or {}).get("objects", {}).get(oid, {})
        chunks = tm.flatten(main, oid, _matrix(_attr(item, "transform")), info.get("skip"))
        if not chunks:
            continue
        V = np.vstack([c[0] for c in chunks])
        offs = np.cumsum([0] + [len(c[0]) for c in chunks[:-1]])
        F = np.vstack([c[1] + o for c, o in zip(chunks, offs)])
        mesh = trimesh.Trimesh(V, F, process=True)
        name = info.get("name") or prusa.get(oid) or names_in_model.get(oid) or f"{Path(filename).stem}_{i + 1}"
        name = re.sub(r"\.(stl|obj|3mf|step)(_\w{1,3})?$", "", name, flags=re.I).strip()
        src = filename
        if meta and oid in meta["plates"]:
            src += f" (pôvodná platňa {meta['plates'][oid]})"
        parts.append(Part(name=name, mesh=mesh, material=normalize_material(info.get("material")), source=src))
    if not parts:
        raise ModelError(f"{filename}: 3MF neobsahuje žiadne tlačiteľné objekty")
    return parts


# --- STL / OBJ / ZIP -----------------------------------------------------


def load_mesh_file(data: bytes, filename: str) -> list[Part]:
    ext = Path(filename).suffix.lower()
    if ext == ".3mf":
        return load_3mf(data, filename)
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=ext[1:], force="mesh", process=True)
    except Exception as exc:
        raise ModelError(f"{filename}: súbor sa nedá načítať ({exc})") from exc
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ModelError(f"{filename}: súbor neobsahuje žiadnu geometriu")
    # niektoré modely sú v metroch alebo palcoch – odhad podľa veľkosti
    size = float(np.max(loaded.extents))
    notes = []
    if size < 1.0:
        loaded.apply_scale(1000.0)
        notes.append("Model bol v metroch – prepočítaný na milimetre.")
    elif size < 8.0 and ext == ".obj":
        loaded.apply_scale(25.4)
        notes.append("Model vyzeral ako v palcoch – prepočítaný na milimetre.")
    return [Part(name=Path(filename).stem, mesh=loaded, source=filename, notes=notes)]


def load_any(data: bytes, filename: str) -> list[Part]:
    ext = Path(filename).suffix.lower()
    if ext in MODEL_EXT:
        return load_mesh_file(data, filename)
    if ext == ".zip" or data[:2] == b"PK" and ext not in MODEL_EXT:
        parts = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [n for n in zf.namelist() if PurePosixPath(n).suffix.lower() in MODEL_EXT and "__MACOSX" not in n]
            # ak je v ZIPe 3MF aj STL toho istého modelu, stačí 3MF
            stems3 = {PurePosixPath(n).stem.lower() for n in members if n.lower().endswith(".3mf")}
            for n in members:
                if not n.lower().endswith(".3mf") and PurePosixPath(n).stem.lower() in stems3:
                    continue
                parts += load_mesh_file(zf.read(n), PurePosixPath(n).name)
        if not parts:
            raise ModelError(f"{filename}: v ZIP archíve nie je žiadny STL, OBJ ani 3MF")
        return parts
    raise ModelError(f"{filename}: nepodporovaný formát (podporované: STL, OBJ, 3MF, ZIP)")


def prepare(part: Part) -> Part:
    """Oprava siete a položenie na podložku."""
    m = part.mesh
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    if not m.is_winding_consistent:
        trimesh.repair.fix_winding(m)
    if m.volume < 0:
        m.invert()
    m.apply_translation([0, 0, -m.bounds[0][2]])
    return part
