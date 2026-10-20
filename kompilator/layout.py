"""Natočenie, rozrezanie príliš veľkých dielov a rozloženie dielov na podložky."""
import math
from dataclasses import dataclass, field

import numpy as np
import trimesh

from .config import PRINTER, USABLE_X, USABLE_Y
from .models import Part

try:
    import manifold3d
except ImportError:  # pragma: no cover
    manifold3d = None


# --- natočenie okolo Z -------------------------------------------------


def _rotate_z(mesh: trimesh.Trimesh, angle: float):
    mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))


def footprint(mesh: trimesh.Trimesh) -> np.ndarray:
    """Konvexný obrys dielu pri pohľade zhora (body XY)."""
    pts = mesh.vertices[:, :2]
    try:
        from scipy.spatial import ConvexHull  # noqa: PLC0415

        hull = ConvexHull(pts)
        return pts[hull.vertices]
    except Exception:
        lo, hi = pts.min(0), pts.max(0)
        return np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])


def align_min_rect(mesh: trimesh.Trimesh):
    """Natočí diel okolo Z tak, aby mal čo najmenší obdĺžnikový pôdorys."""
    hull = footprint(mesh)
    if len(hull) < 3:
        return
    best = (math.inf, 0.0)
    for i in range(len(hull)):
        e = hull[(i + 1) % len(hull)] - hull[i]
        a = -math.atan2(e[1], e[0])
        c, s = math.cos(a), math.sin(a)
        r = hull @ np.array([[c, s], [-s, c]])
        area = np.ptp(r[:, 0]) * np.ptp(r[:, 1])
        if area < best[0] - 1e-6:
            best = (area, a)
    if abs(best[1]) > 1e-4:
        _rotate_z(mesh, best[1])
    ext = mesh.extents
    if ext[1] > ext[0]:  # dlhšia strana pozdĺž X
        _rotate_z(mesh, math.pi / 2)


def fits(mesh: trimesh.Trimesh) -> bool:
    ex, ey, ez = mesh.extents
    return ez <= PRINTER["max_z"] and ((ex <= USABLE_X and ey <= USABLE_Y) or (ey <= USABLE_X and ex <= USABLE_Y))


# --- rezanie -----------------------------------------------------------


def _split_plane(mesh: trimesh.Trimesh, axis: int, value: float) -> list[trimesh.Trimesh]:
    normal = np.zeros(3)
    normal[axis] = 1.0
    pieces = []
    if manifold3d is not None and mesh.is_watertight:
        try:
            man = manifold3d.Manifold(manifold3d.Mesh(
                vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
                tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
            ))
            if man.status() == manifold3d.Error.NoError:
                for half in man.split_by_plane(tuple(normal), float(value)):
                    out = half.to_mesh()
                    if len(out.tri_verts):
                        pieces.append(trimesh.Trimesh(np.asarray(out.vert_properties)[:, :3], np.asarray(out.tri_verts)))
                if pieces:
                    return pieces
        except Exception:
            pieces = []
    # záloha pre siete, ktoré nie sú uzavreté
    origin = np.zeros(3)
    origin[axis] = value
    for sign in (1, -1):
        try:
            half = trimesh.intersections.slice_mesh_plane(mesh, sign * normal, origin, cap=True)
        except Exception:
            half = trimesh.intersections.slice_mesh_plane(mesh, sign * normal, origin, cap=False)
        if half is not None and len(half.faces):
            pieces.append(half)
    return pieces


def cut_to_fit(part: Part, max_depth: int = 6) -> list[Part]:
    """Rozreže diel rovinami na kusy, ktoré sa zmestia na podložku. Kusy sú pripravené na lepenie."""
    limits = [USABLE_X, USABLE_Y, PRINTER["max_z"]]
    work = [(part.mesh, part.name)]
    done = []
    for _ in range(max_depth):
        nxt = []
        for mesh, name in work:
            align_min_rect(mesh)
            mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
            if fits(mesh):
                done.append((mesh, name))
                continue
            ext = mesh.extents
            # os, ktorá najviac prekračuje limit
            axis = int(np.argmax(ext / np.array(limits)))
            n = math.ceil(ext[axis] / limits[axis])
            lo = mesh.bounds[0][axis]
            step = ext[axis] / n
            remaining = mesh
            for k in range(1, n):
                halves = _split_plane(remaining, axis, lo + k * step)
                if len(halves) < 2:
                    break
                upper, lower = halves[0], halves[1]
                nxt.append((lower, f"{name}-{k}"))
                remaining = upper
            nxt.append((remaining, f"{name}-{n}"))
        work = nxt
        if not work:
            break
    done += work
    if len(done) == 1:
        return [part]
    out = []
    for i, (mesh, _n) in enumerate(done, 1):
        mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
        p = Part(name=f"{part.name} – kus {i}z{len(done)}", mesh=mesh, material=part.material, source=part.source)
        p.notes.append(f"Diel „{part.name}“ bol väčší ako podložka – rozrezaný na {len(done)} kusy na zlepenie.")
        out.append(p)
    return out


# --- rozloženie (MaxRects) ---------------------------------------------


@dataclass
class Placed:
    part: Part
    x: float  # stred dielu na podložke (súradnice od stredu podložky)
    y: float


@dataclass
class Plate:
    material: str
    placed: list[Placed] = field(default_factory=list)


class _MaxRects:
    def __init__(self, w, h):
        self.free = [(0.0, 0.0, w, h)]

    def insert(self, w, h):
        best = None
        for fx, fy, fw, fh in self.free:
            for rw, rh, rot in ((w, h, False), (h, w, True)):
                if rw <= fw + 1e-6 and rh <= fh + 1e-6:
                    score = (min(fw - rw, fh - rh), max(fw - rw, fh - rh))
                    if best is None or score < best[0]:
                        best = (score, fx, fy, rw, rh, rot)
        if best is None:
            return None
        _, x, y, rw, rh, rot = best
        self._split(x, y, rw, rh)
        return x, y, rot

    def _split(self, x, y, w, h):
        new = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                new.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                new.append((fx, y + h, fw, fy + fh - y - h))
        # odstráni obdĺžniky obsiahnuté v iných
        self.free = [
            a for i, a in enumerate(new)
            if not any(
                j != i and a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9
                and a[0] + a[2] <= b[0] + b[2] + 1e-9 and a[1] + a[3] <= b[1] + b[3] + 1e-9
                and (a != b or j < i)
                for j, b in enumerate(new)
            )
        ]


def pack(parts: list[Part], default_material: str) -> list[Plate]:
    """Rozloží diely na čo najmenej podložiek. Diely z rôznych materiálov idú na rôzne podložky."""
    gap = PRINTER["spacing"]
    by_mat: dict[str, list[Part]] = {}
    for p in parts:
        by_mat.setdefault(p.material or default_material, []).append(p)
    plates: list[Plate] = []
    for mat, group in by_mat.items():
        for p in group:
            align_min_rect(p.mesh)
        group.sort(key=lambda p: -(p.mesh.extents[0] * p.mesh.extents[1]))
        bins: list[tuple[_MaxRects, Plate]] = []
        for p in group:
            w, h = p.mesh.extents[0] + gap, p.mesh.extents[1] + gap
            pos = None
            for packer, plate in bins:
                pos = packer.insert(w, h)
                if pos:
                    break
            if pos is None:
                packer = _MaxRects(USABLE_X + gap, USABLE_Y + gap)
                plate = Plate(material=mat)
                bins.append((packer, plate))
                pos = packer.insert(w, h)
                if pos is None:  # nemalo by nastať – diel je po cut_to_fit menší ako podložka
                    raise ValueError(f"Diel {p.name} sa nezmestí na podložku")
            x, y, rot = pos
            if rot:
                _rotate_z(p.mesh, math.pi / 2)
            ew, eh = p.mesh.extents[0], p.mesh.extents[1]
            cx = x + ew / 2 - (USABLE_X + gap) / 2 + gap / 2
            cy = y + eh / 2 - (USABLE_Y + gap) / 2 + gap / 2
            plate.placed.append(Placed(part=p, x=cx, y=cy))
        plates += [pl for _, pl in bins]
    # podložky rozmiestnime na stred – posun celej skupiny
    for plate in plates:
        if not plate.placed:
            continue
        lo = np.min([[pl.x - pl.part.mesh.extents[0] / 2, pl.y - pl.part.mesh.extents[1] / 2] for pl in plate.placed], axis=0)
        hi = np.max([[pl.x + pl.part.mesh.extents[0] / 2, pl.y + pl.part.mesh.extents[1] / 2] for pl in plate.placed], axis=0)
        shift = -(lo + hi) / 2
        for pl in plate.placed:
            pl.x += shift[0]
            pl.y += shift[1]
    return plates


def plate_mesh(plate: Plate) -> trimesh.Trimesh:
    """Všetky diely podložky v jednej sieti, stred podložky = (0, 0), spodok na Z = 0."""
    meshes = []
    for pl in plate.placed:
        m = pl.part.mesh.copy()
        c = (m.bounds[0] + m.bounds[1]) / 2
        m.apply_translation([pl.x - c[0], pl.y - c[1], -m.bounds[0][2]])
        meshes.append(m)
    return trimesh.util.concatenate(meshes)


def placed_meshes(plate: Plate) -> list[tuple[str, trimesh.Trimesh]]:
    out = []
    for pl in plate.placed:
        m = pl.part.mesh.copy()
        c = (m.bounds[0] + m.bounds[1]) / 2
        m.apply_translation([pl.x - c[0], pl.y - c[1], -m.bounds[0][2]])
        out.append((pl.part.name, m))
    return out


# --- analýza pre automatické nastavenia ---------------------------------


def needs_support(mesh: trimesh.Trimesh, angle_deg: float = 55.0, cell: float = 2.0) -> float:
    """Plocha (mm²) previsov strmejších ako `angle_deg`, pod ktorými je až podložka.

    Podpery sa robia len od podložky, takže previsy nad iným miestom dielu
    (napr. strop kabíny Benchy) sa nerátajú – tam by podpery aj tak nevznikli.
    """
    n = mesh.face_normals
    limit = -math.cos(math.radians(90 - angle_deg))
    mask = n[:, 2] < limit
    z0 = mesh.bounds[0][2]
    zmin = mesh.triangles[:, :, 2].min(axis=1)
    mask &= zmin > z0 + 0.3
    if not mask.any():
        return 0.0
    # mapa najnižšieho povrchu dielu v mriežke XY (body rovnomerne po povrchu)
    count = int(min(200000, max(20000, mesh.area / (cell * cell) * 4)))
    pts, _ = trimesh.sample.sample_surface_even(mesh, count)
    pts = np.vstack([pts, mesh.vertices])
    lo = mesh.bounds[0][:2]
    shape = (np.ceil((mesh.bounds[1][:2] - lo) / cell).astype(int) + 1)
    ix = ((pts[:, :2] - lo) / cell).astype(int)
    lowest = np.full(shape, np.inf)
    np.minimum.at(lowest, (ix[:, 0], ix[:, 1]), pts[:, 2])
    cent = mesh.triangles_center[mask]
    ci = ((cent[:, :2] - lo) / cell).astype(int)
    below = lowest[ci[:, 0], ci[:, 1]]
    free = below >= cent[:, 2] - 1.0  # pod previsom nie je nič iné ako vzduch až po podložku
    return float(mesh.area_faces[mask][free].sum())


def needs_brim(mesh: trimesh.Trimesh) -> bool:
    """Brim pre malé, vysoké alebo úzke diely, ktoré by sa mohli odlepiť."""
    z = mesh.vertices[:, 2]
    base = mesh.vertices[z < z.min() + 0.25][:, :2]
    if len(base) < 3:
        return True
    ext = np.ptp(base, axis=0)
    small_side = float(min(ext))
    height = float(mesh.extents[2])
    return small_side < 10 or height / max(small_side, 1) > 3
