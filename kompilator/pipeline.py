"""Celý postup: model → diely → rozrezanie → podložky → nastavenia → G-code."""
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import cura, layout
from .config import MATERIALS, PRINTER, QUALITIES, profile_values
from .models import ModelError, load_any, prepare


@dataclass
class Options:
    material: str = "auto"  # auto = podľa pôvodného projektu, inak PLA
    quality: str = "standard"
    strong: bool = False
    supports: str = "auto"  # auto | on | off
    adhesion: str = "auto"  # auto | skirt | brim
    slice: bool = True

    @classmethod
    def from_dict(cls, d: dict | None):
        d = d or {}
        o = cls()
        for k in ("material", "quality", "supports", "adhesion"):
            if isinstance(d.get(k), str):
                setattr(o, k, d[k])
        for k in ("strong", "slice"):
            if k in d:
                setattr(o, k, bool(d[k]))
        if o.material != "auto" and o.material not in MATERIALS:
            o.material = "auto"
        if o.quality not in QUALITIES:
            o.quality = "standard"
        return o


def _safe(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", name, flags=re.U).strip("_")
    return s[:60] or "model"


def plate_svg(objects, size=PRINTER["bed_x"]) -> str:
    """Náhľad podložky zhora (obrysy dielov) ako SVG."""
    half = size / 2
    polys = []
    for name, mesh in objects:
        hull = layout.footprint(mesh)
        pts = " ".join(f"{x + half:.1f},{size - (y + half):.1f}" for x, y in hull)
        nm = name.replace("&", "&amp;").replace("<", "&lt;")
        polys.append(f'<polygon points="{pts}" class="part"><title>{nm}</title></polygon>')
    m = PRINTER["margin"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size:.0f} {size:.0f}">'
        f'<rect x="0" y="0" width="{size:.0f}" height="{size:.0f}" rx="6" class="bed"/>'
        f'<rect x="{m}" y="{m}" width="{size - 2 * m:.0f}" height="{size - 2 * m:.0f}" rx="3" class="usable"/>'
        + "".join(polys) + "</svg>"
    )


def run(inputs: list[tuple[bytes, str]], out_dir: Path, opts: Options, cura_dir: Path | None = None,
        progress=None, title: str | None = None) -> dict:
    """Spracuje modely a výsledky uloží do `out_dir`. Vráti manifest (uloží ho aj ako manifest.json)."""
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def say(msg, frac=None):
        if progress:
            progress(msg, frac)

    say("Načítavam modely", 0.02)
    parts = []
    for data, name in inputs:
        parts += load_any(data, name)
    if not parts:
        raise ModelError("Nenašiel som žiadne diely")
    notes: list[str] = []
    for p in parts:
        prepare(p)

    default_mat = opts.material if opts.material != "auto" else None
    detected = {p.material for p in parts if p.material}
    if default_mat:
        for p in parts:
            p.material = default_mat
    else:
        default_mat = "PLA"
        if detected:
            notes.append("Materiál prevzatý z pôvodného projektu: " + ", ".join(sorted(detected)) + ".")

    say("Kontrolujem veľkosť dielov", 0.08)
    fitted = []
    for p in parts:
        fitted += layout.cut_to_fit(p)
    for p in fitted:
        for n in p.notes:
            if n not in notes:
                notes.append(n)

    say("Rozkladám diely na podložky", 0.12)
    plates = layout.pack(fitted, default_mat)
    base_name = _safe(title or Path(inputs[0][1]).stem)
    q = QUALITIES[opts.quality]

    manifest = {
        "title": title or Path(inputs[0][1]).stem,
        "printer": PRINTER["name"],
        "options": opts.__dict__,
        "notes": notes,
        "plates": [],
        "files": [],
        "created": int(time.time()),
    }

    cura_dir = Path(cura_dir) if cura_dir else None
    if opts.slice and cura_dir is None:
        cura_dir = cura.find_cura()

    profiles_written = set()
    for i, plate in enumerate(plates, 1):
        frac0 = 0.15 + 0.8 * (i - 1) / len(plates)
        say(f"Podložka {i}/{len(plates)}: nastavenia", frac0)
        objects = layout.placed_meshes(plate)
        merged = layout.plate_mesh(plate)
        values = profile_values(plate.material, opts.quality, opts.strong)

        # podpery, ak má aspoň jeden diel väčší previs nad podložkou (drobnosti zvládne PLA aj bez nich)
        overhang = max(layout.needs_support(m, values["support_angle"]) for _, m in objects)
        support = opts.supports == "on" or (opts.supports == "auto" and overhang > 25)
        values["support_enable"] = support
        brim = opts.adhesion == "brim" or plate.material == "ABS" or (
            opts.adhesion == "auto" and any(layout.needs_brim(m) for _, m in objects))
        values["adhesion_type"] = "brim" if brim else "skirt"

        stem = f"{base_name}_podlozka{i}_{plate.material}"
        f3mf = out_dir / f"{stem}.3mf"
        cura.write_3mf(f3mf, objects)
        manifest["files"].append({"name": f3mf.name, "kind": "3mf", "plate": i})

        info = {
            "index": i,
            "material": plate.material,
            "parts": [{"name": pl.part.name, "size": [round(float(v), 1) for v in pl.part.mesh.extents]} for pl in plate.placed],
            "height_mm": round(float(merged.extents[2]), 1),
            "supports": support,
            "overhang_mm2": round(overhang),
            "adhesion": values["adhesion_type"],
            "svg": plate_svg(objects),
            "gcode": None,
        }

        prof_key = (plate.material, support, brim)
        if prof_key not in profiles_written:
            prof_name = f"Ender3Pro {plate.material} {q['layer_height']}{' pevny' if opts.strong else ''}"
            pf = out_dir / f"{base_name}_{plate.material}{'_podpery' if support else ''}{'_brim' if brim else ''}.curaprofile"
            pf.write_bytes(cura.curaprofile(prof_name, values, q["quality_type"]))
            manifest["files"].append({"name": pf.name, "kind": "curaprofile", "plate": None})
            profiles_written.add(prof_key)

        if opts.slice:
            say(f"Podložka {i}/{len(plates)}: slicujem v CuraEngine", frac0 + 0.05)
            settings, resolver = cura.resolve_settings(cura_dir, values, q["quality_type"])
            gfile = out_dir / f"{stem}.gcode"

            def engine_progress(stage, frac, i=i, frac0=frac0):
                say(f"Podložka {i}/{len(plates)}: {stage}", frac0 + 0.8 / len(plates) * min(frac, 1.0))

            stats = cura.slice_mesh(cura_dir, merged, settings, resolver, gfile, log=engine_progress)
            stats["filament_g"] = round(stats["filament_cm3"] * MATERIALS[plate.material]["density"], 1)
            info["gcode"] = gfile.name
            info.update(stats)
            manifest["files"].append({"name": gfile.name, "kind": "gcode", "plate": i})
        manifest["plates"].append(info)

    manifest["total_time_s"] = sum(p.get("time_s", 0) for p in manifest["plates"])
    manifest["total_filament_g"] = round(sum(p.get("filament_g", 0) for p in manifest["plates"]), 1)
    manifest["took_s"] = round(time.time() - t0, 1)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    say("Hotovo", 1.0)
    return manifest


def bbox_summary(parts) -> list:
    return [np.round(p.mesh.extents, 1).tolist() for p in parts]
