"""Tlačiareň Creality Ender-3 Pro a optimálne predvoľby tlače."""

PRINTER = {
    "name": "Creality Ender-3 Pro",
    "bed_x": 220.0,
    "bed_y": 220.0,
    "max_z": 245.0,  # 250 mm mínus rezerva na nerovnosť podložky
    "margin": 8.0,  # okraj od hrany podložky (skirt/brim, čistiaca čiara vľavo)
    "spacing": 6.0,  # medzera medzi dielmi na podložke
}

USABLE_X = PRINTER["bed_x"] - 2 * PRINTER["margin"]
USABLE_Y = PRINTER["bed_y"] - 2 * PRINTER["margin"]

# Kvalita → výška vrstvy a typ kvality v Cure (pre .curaprofile)
QUALITIES = {
    "rychla": {"label": "Rýchla 0,28 mm", "quality_type": "low", "layer_height": 0.28},
    "standard": {"label": "Štandard 0,2 mm", "quality_type": "standard", "layer_height": 0.2},
    "jemna": {"label": "Jemná 0,12 mm", "quality_type": "super", "layer_height": 0.12},
}

# Spoločný základ pre Ender-3 Pro (Bowden, tryska 0,4 mm, stock firmvér, akcelerácia 500)
BASE = {
    "line_width": 0.4,
    "initial_layer_line_width_factor": 110,
    "wall_thickness": 1.2,
    "optimize_wall_printing_order": True,
    "z_seam_type": "sharpest_corner",
    "z_seam_corner": "z_seam_corner_weighted",
    "top_thickness": 1.0,
    "bottom_thickness": 0.8,
    "roofing_layer_count": 1,
    "infill_sparse_density": 20,
    "infill_pattern": "gyroid",
    "speed_travel": 150,
    "speed_travel_layer_0": 80,
    "retraction_enable": True,
    "retraction_min_travel": 1.5,
    "retraction_combing": "noskin",
    "travel_avoid_other_parts": True,
    "cool_fan_enabled": True,
    "cool_fan_speed_0": 0,
    "cool_fan_full_layer": 3,
    "cool_min_layer_time": 8,
    "cool_min_speed": 10,
    "support_structure": "tree",
    "support_type": "buildplate",
    "support_angle": 55,
    "minimum_support_area": 4,
    "support_xy_distance": 0.8,
    "support_roof_enable": True,
    "support_bottom_enable": False,
    "skirt_line_count": 3,
    "skirt_gap": 5,
    "brim_width": 5,
    "xy_offset_layer_0": -0.1,
    "bridge_settings_enabled": True,
}

MATERIALS = {
    "PLA": {
        "label": "PLA",
        "density": 1.24,
        "values": {
            "material_print_temperature": 205,
            "material_print_temperature_layer_0": 210,
            "material_bed_temperature": 60,
            "material_bed_temperature_layer_0": 65,
            "speed_print": 50,
            "speed_infill": 60,
            "speed_wall_0": 30,
            "speed_wall_x": 45,
            "speed_topbottom": 35,
            "speed_roofing": 30,
            "speed_support": 50,
            "speed_support_interface": 35,
            "speed_layer_0": 20,
            "skirt_brim_speed": 20,
            "retraction_amount": 5,
            "retraction_speed": 45,
            "retraction_hop_enabled": False,
            "cool_fan_speed": 100,
        },
    },
    "PETG": {
        "label": "PETG",
        "density": 1.27,
        "values": {
            "material_print_temperature": 235,
            "material_print_temperature_layer_0": 235,
            "material_bed_temperature": 75,
            "material_bed_temperature_layer_0": 80,
            "speed_print": 40,
            "speed_infill": 50,
            "speed_wall_0": 25,
            "speed_wall_x": 35,
            "speed_topbottom": 30,
            "speed_roofing": 25,
            "speed_support": 40,
            "speed_support_interface": 30,
            "speed_layer_0": 18,
            "skirt_brim_speed": 18,
            "retraction_amount": 5.5,
            "retraction_speed": 40,
            "retraction_hop_enabled": True,
            "retraction_hop": 0.2,
            "cool_fan_speed": 40,
            "cool_fan_speed_max": 60,
            "support_z_distance": 0.25,
        },
    },
    "TPU": {
        "label": "TPU (flexibilný)",
        "density": 1.21,
        "values": {
            "material_print_temperature": 225,
            "material_print_temperature_layer_0": 225,
            "material_bed_temperature": 50,
            "material_bed_temperature_layer_0": 50,
            "speed_print": 20,
            "speed_infill": 20,
            "speed_wall_0": 15,
            "speed_wall_x": 20,
            "speed_topbottom": 15,
            "speed_roofing": 15,
            "speed_support": 20,
            "speed_support_interface": 15,
            "speed_layer_0": 12,
            "skirt_brim_speed": 12,
            "speed_travel": 100,
            "retraction_amount": 1.5,
            "retraction_speed": 20,
            "retraction_combing": "all",
            "retraction_hop_enabled": False,
            "cool_fan_speed": 80,
        },
    },
    "ABS": {
        "label": "ABS (potrebuje kryt)",
        "density": 1.04,
        "values": {
            "material_print_temperature": 240,
            "material_print_temperature_layer_0": 245,
            "material_bed_temperature": 100,
            "material_bed_temperature_layer_0": 100,
            "speed_print": 45,
            "speed_infill": 55,
            "speed_wall_0": 30,
            "speed_wall_x": 40,
            "speed_topbottom": 30,
            "speed_roofing": 25,
            "speed_support": 45,
            "speed_support_interface": 30,
            "speed_layer_0": 20,
            "skirt_brim_speed": 20,
            "retraction_amount": 5,
            "retraction_speed": 45,
            "retraction_hop_enabled": False,
            "cool_fan_speed": 0,
            "cool_fan_enabled": False,
        },
    },
}

STRONG = {"wall_thickness": 1.6, "top_thickness": 1.2, "bottom_thickness": 1.0, "infill_sparse_density": 40}

# Materiály z iných slicerov → naše predvoľby
MATERIAL_ALIASES = {
    "PLA": "PLA", "PLA+": "PLA", "PLA-CF": "PLA", "PLA SILK": "PLA", "PLA-S": "PLA",
    "PETG": "PETG", "PET": "PETG", "PETG-CF": "PETG", "PCTG": "PETG",
    "TPU": "TPU", "TPE": "TPU", "FLEX": "TPU",
    "ABS": "ABS", "ASA": "ABS",
}


def normalize_material(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().upper()
    if key in MATERIAL_ALIASES:
        return MATERIAL_ALIASES[key]
    for k, v in MATERIAL_ALIASES.items():
        if key.startswith(k):
            return v
    return None


def profile_values(material: str = "PLA", quality: str = "standard", strong: bool = False) -> dict:
    """Kompletné hodnoty profilu (bez automatických volieb podpier a prilnavosti)."""
    q = QUALITIES[quality]
    v = dict(BASE)
    v.update(MATERIALS[material]["values"])
    v["layer_height"] = q["layer_height"]
    v["layer_height_0"] = 0.2 if q["layer_height"] <= 0.2 else 0.28
    if q["layer_height"] < 0.16:
        # jemné vrstvy → pomalšie vonkajšie steny a viac vrstiev vrchu
        v["top_thickness"] = 1.0
        v["speed_wall_0"] = min(v["speed_wall_0"], 25)
    if strong:
        v.update(STRONG)
    return v
