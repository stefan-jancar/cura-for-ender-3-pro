"""Vyhodnotenie nastavení Cura pre CuraEngine.

CuraEngine z príkazového riadku pozná len `default_value` z definícií – vzorce
(`value`) počíta až aplikácia Cura. Tu ich počítame sami v rovnakom poradí ako Cura:
definícia stroja → extrudér → globálna kvalita → náš profil.
Tlačiareň má jeden extrudér, takže globálne a extrudérové nastavenia sú jeden priestor.
"""
import ast
import configparser
import json
import math
import re
from pathlib import Path

MACHINE = "creality_ender3pro"
EXTRUDER = "creality_base_extruder_0"


class SettingError(Exception):
    pass


def _flatten(settings: dict, out: dict):
    for key, d in settings.items():
        if "type" in d and d.get("type") != "category":
            out[key] = dict(d)
        if "children" in d:
            _flatten(d["children"], out)


def _load_def(res: Path, folder: str, name: str) -> list[dict]:
    """Vráti reťaz definícií od najvšeobecnejšej (fdmprinter) po zadanú."""
    data = json.loads((res / folder / f"{name}.def.json").read_text("utf-8"))
    chain = []
    parent = data.get("inherits")
    if parent:
        chain = _load_def(res, "definitions", parent)
    chain.append(data)
    return chain


def _parse_cfg_value(raw: str):
    raw = raw.strip()
    if raw.startswith("="):
        return ("expr", raw[1:])
    return ("value", raw)


class Resolver:
    def __init__(self, resources: Path, quality_type: str = "standard"):
        self.res = Path(resources)
        self.defs: dict[str, dict] = {}
        for chain in (_load_def(self.res, "definitions", MACHINE), _load_def(self.res, "extruders", EXTRUDER)):
            for d in chain:
                if "settings" in d:
                    _flatten(d["settings"], self.defs)
            for d in chain:
                for key, ov in d.get("overrides", {}).items():
                    if key in self.defs:
                        self.defs[key].update(ov)
        # vrstvy hodnôt nad definíciou (neskoršie vyhrávajú)
        self.layers: list[dict] = []
        q = self.res / "quality" / "creality" / "base" / f"base_global_{quality_type}.inst.cfg"
        if q.exists():
            cp = configparser.ConfigParser(interpolation=None)
            cp.read(q, encoding="utf-8")
            self.layers.append({k: _parse_cfg_value(v) for k, v in cp["values"].items()})

    def add_values(self, values: dict):
        """Pridá vrstvu hodnôt. Reťazec začínajúci '=' je vzorec ako v Cure."""
        layer = {}
        for k, v in values.items():
            if k not in self.defs:
                raise SettingError(f"Neznáme nastavenie Cura: {k}")
            layer[k] = _parse_cfg_value(v) if isinstance(v, str) and v.startswith("=") else ("py", v)
        self.layers.append(layer)

    # --- vyhodnotenie -------------------------------------------------
    def _convert(self, key, raw):
        t = self.defs[key].get("type")
        if isinstance(raw, str):
            if t in ("float",):
                return float(raw)
            if t in ("int", "extruder", "optional_extruder"):
                return int(float(raw))
            if t == "bool":
                return raw.strip().lower() in ("true", "1", "yes", "on")
            if t in ("polygon", "polygons", "[int]"):
                try:
                    return ast.literal_eval(raw) if raw.strip() else []
                except Exception:
                    return raw
        return raw

    def resolve(self) -> dict:
        cache: dict = {}
        stack: list = []

        def get(key):
            if key in cache:
                return cache[key]
            if key not in self.defs:
                raise NameError(key)
            if key in stack:
                raise SettingError("Cyklus v nastaveniach: " + " → ".join(stack + [key]))
            stack.append(key)
            try:
                src = None
                for layer in reversed(self.layers):
                    if key in layer:
                        src = layer[key]
                        break
                d = self.defs[key]
                if src is None:
                    src = ("expr", d["value"]) if isinstance(d.get("value"), str) else ("value", d.get("value", d.get("default_value")))
                kind, v = src
                if kind == "expr":
                    v = evaluate(v)
                elif kind == "value" and isinstance(v, str):
                    v = self._convert(key, v)
                if d.get("type") in ("int", "extruder", "optional_extruder") and isinstance(v, float):
                    v = int(round(v))
                cache[key] = v
                return v
            finally:
                stack.pop()

        class NS(dict):
            def __missing__(self, name):
                return get(name)

        def extruder_value(_e, key):
            return get(key)

        def extruder_values(key):
            return [get(key)]

        base = {
            "math": math, "max": max, "min": min, "round": round, "int": int, "float": float,
            "str": str, "len": len, "sum": sum, "any": any, "all": all, "map": map, "abs": abs,
            "list": list, "True": True, "False": False, "None": None,
            "extruderValue": extruder_value,
            "extruderValues": extruder_values,
            "resolveOrValue": lambda key: get(key),
            "defaultExtruderPosition": lambda: 0,
            "anyExtruderWithMaterial": lambda key: 0,
            "anyExtruderNrWithOrDefault": lambda key: 0,
            "valueFromContainer": lambda key, idx=0: get(key),
            "valueFromExtruderContainer": lambda key, idx=0: get(key),
        }

        def evaluate(expr: str):
            ns = NS(base)
            try:
                return eval(compile(expr, "<cura>", "eval"), {"__builtins__": {}}, ns)  # noqa: S307 – vzorce z inštalácie Cura
            except (NameError, SettingError):
                raise
            except Exception as exc:  # delenie nulou a pod. – Cura v takom prípade berie default
                raise SettingError(f"Vzorec '{expr}' zlyhal: {exc}") from exc

        out = {}
        for key in self.defs:
            try:
                out[key] = get(key)
            except SettingError:
                out[key] = self.defs[key].get("default_value")
                cache[key] = out[key]
        return out


def engine_str(v) -> str:
    """Formát hodnoty, ktorý očakáva CuraEngine (rovnako ako str() v Cure)."""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return repr(round(v, 6)) if not v.is_integer() else str(v)
    return str(v)


_TOKEN = re.compile(r"\{([a-z_0-9]+)(?:\s*,\s*[0-9]+)?\}")


def expand_gcode(template: str, settings: dict, extra: dict | None = None) -> str:
    """Nahradí {nastavenie} v štart/koniec G-code ako to robí Cura."""
    vals = {**settings, **(extra or {})}

    def repl(m):
        k = m.group(1)
        return engine_str(vals[k]) if k in vals else m.group(0)

    return _TOKEN.sub(repl, template)
