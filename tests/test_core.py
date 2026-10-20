"""Testy jadra: načítanie, rezanie, rozloženie, API fronty a (ak je Cura nainštalovaná) slicovanie."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import trimesh
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kompilator import cura, layout, pipeline  # noqa: E402
from kompilator.config import USABLE_X, USABLE_Y  # noqa: E402
from kompilator.models import load_any, prepare  # noqa: E402
from server.app import create_app  # noqa: E402
from server.storage import LocalStore  # noqa: E402


def stl_bytes(mesh: trimesh.Trimesh) -> bytes:
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


def cura_available() -> bool:
    try:
        cura.find_cura()
        return True
    except cura.CuraError:
        return False


class LayoutTest(unittest.TestCase):
    def test_small_parts_share_plate(self):
        parts = []
        for i in range(6):
            parts += load_any(stl_bytes(trimesh.creation.box([30, 20, 10])), f"kocka{i}.stl")
        for p in parts:
            prepare(p)
        plates = layout.pack(parts, "PLA")
        self.assertEqual(len(plates), 1)
        self.assertEqual(len(plates[0].placed), 6)

    def test_oversized_part_is_cut(self):
        part = load_any(stl_bytes(trimesh.creation.box([400, 300, 30])), "velka.stl")[0]
        prepare(part)
        pieces = layout.cut_to_fit(part)
        self.assertGreaterEqual(len(pieces), 4)
        total = sum(p.mesh.volume for p in pieces)
        self.assertAlmostEqual(total, 400 * 300 * 30, delta=400 * 300 * 30 * 0.01)
        for p in pieces:
            self.assertTrue(layout.fits(p.mesh), p.mesh.extents)

    def test_diagonal_part_is_rotated_not_cut(self):
        m = trimesh.creation.box([250, 30, 10])
        m.apply_transform(trimesh.transformations.rotation_matrix(0.5, [0, 0, 1]))
        part = load_any(stl_bytes(m), "dlha.stl")[0]
        prepare(part)
        pieces = layout.cut_to_fit(part)
        self.assertGreaterEqual(len(pieces), 2)  # 250 mm sa nezmestí ani po otočení
        m2 = trimesh.creation.box([190, 30, 10])
        m2.apply_transform(trimesh.transformations.rotation_matrix(0.7, [0, 0, 1]))
        part2 = load_any(stl_bytes(m2), "otocena.stl")[0]
        prepare(part2)
        self.assertEqual(len(layout.cut_to_fit(part2)), 1)

    def test_plate_within_bed(self):
        parts = [load_any(stl_bytes(trimesh.creation.cylinder(radius=r, height=20)), f"v{r}.stl")[0] for r in (10, 30, 50, 60)]
        for p in parts:
            prepare(p)
        for plate in layout.pack(parts, "PLA"):
            m = layout.plate_mesh(plate)
            self.assertLessEqual(m.bounds[1][0] - m.bounds[0][0], USABLE_X + 1)
            self.assertLessEqual(m.bounds[1][1] - m.bounds[0][1], USABLE_Y + 1)

    def test_3mf_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.3mf"
            cura.write_3mf(f, [("kocka", trimesh.creation.box([10, 10, 10])), ("valec", trimesh.creation.cylinder(5, 8))])
            parts = load_any(f.read_bytes(), "a.3mf")
            self.assertEqual([p.name for p in parts], ["kocka", "valec"])

    def test_support_detection(self):
        # „T“ tvar – previs nad podložkou potrebuje podperu, kocka nie
        t = trimesh.util.concatenate([trimesh.creation.box([10, 10, 30]).apply_translation([0, 0, 15]),
                                      trimesh.creation.box([60, 10, 4]).apply_translation([0, 0, 32])])
        self.assertGreater(layout.needs_support(t), 25)
        self.assertLess(layout.needs_support(trimesh.creation.box([20, 20, 20]).apply_translation([0, 0, 10])), 1)


class ApiTest(unittest.TestCase):
    def test_queue_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(LocalStore(tmp), mode="cloud", auth_required=False)
            c = TestClient(app)
            data = b"x" * 100
            job = c.post("/api/jobs", json={"files": [{"name": "a.stl", "size": len(data)}], "options": {"material": "PETG"}}).json()
            self.assertEqual(job["status"], "nahrava")
            self.assertEqual(job["options"]["material"], "PETG")
            c.put(f"/api/jobs/{job['id']}/src/a.stl/0", content=data)
            c.post(f"/api/jobs/{job['id']}/ready")
            claimed = c.post("/api/worker/claim", json={}).json()["job"]
            self.assertEqual(claimed["id"], job["id"])
            self.assertIsNone(c.post("/api/worker/claim", json={}).json()["job"])
            self.assertEqual(c.get(f"/api/jobs/{job['id']}/file/src/a.stl/0").content, data)
            c.post(f"/api/worker/{job['id']}/done", json={"result": {"plates": []}})
            self.assertEqual(c.get("/api/jobs").json()[0]["status"], "hotovo")
            self.assertEqual(c.put(f"/api/jobs/{job['id']}/src/..%2Fx/0", content=b"1").status_code in (400, 404), True)

    def test_url_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = TestClient(create_app(LocalStore(tmp), mode="cloud", auth_required=False))
            job = c.post("/api/jobs", json={"url": "https://www.printables.com/model/3161-3d-benchy"}).json()
            self.assertEqual(job["title"], "3d benchy")


@unittest.skipUnless(cura_available(), "UltiMaker Cura nie je nainštalovaná")
class SliceTest(unittest.TestCase):
    def test_slice_small_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = pipeline.run([(stl_bytes(trimesh.creation.box([20, 20, 10])), "kocka.stl")], Path(tmp),
                             pipeline.Options.from_dict({"quality": "rychla"}))
            g = Path(tmp) / m["plates"][0]["gcode"]
            head = g.read_text("utf-8")[:400]
            self.assertIn("Ender-3 Pro", head)
            self.assertIn("M190 S65", g.read_text("utf-8")[:3000])  # podložka 65 °C na prvú vrstvu
            self.assertGreater(m["plates"][0]["time_s"], 60)
            json.dumps(m)


if __name__ == "__main__":
    unittest.main()
