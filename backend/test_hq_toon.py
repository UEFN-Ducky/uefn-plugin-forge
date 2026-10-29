"""Toon op: inverted-hull outline, cel ramp on materials, dark edges in the toon render.

Run: py -3 -m backend.test_hq_toon
"""
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from . import hq, render
from .engine import client, runtime
from .kernel.mesh import Mesh
from .scene import create as new_scene, forget

_READY = runtime.engine_status()["state"] == "ready"
_RADIUS = 30.0


def brown(path):
    """Pixels close to the outline brown (0.3, 0.13, 0.05 lit down): red-led, dark, clearly not grey or blue."""
    px = Image.open(path).convert("RGB").getdata()
    return sum(1 for r, g, b in px if 40 < r < 130 and r - b > 25 and g < r and b < 70)


class ToonOp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-toon-")
        cls.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        client.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def test_outline_hull(self):
        from .hq.toon import hull_mesh
        from .kernel.mesh import sphere
        ball = sphere(_RADIUS, 16, 8, "gold")
        hull = hull_mesh(ball, 0.6, "ink")
        gains, mid = [], ball.centroid()
        for a, b in zip(ball.verts, hull.verts):  # pushed out along the normal: radius + 0.6
            gain = math.dist(b, mid) - math.dist(a, mid)
            gains.append(gain)
            self.assertTrue(0.2 <= gain <= 0.61, gain)  # faceted normals (and the poles) tilt off radial
        self.assertGreater(sum(gains) / len(gains), 0.5)
        for fi, face in enumerate(hull.faces):
            self.assertEqual(face, ball.faces[fi][::-1], "faces flipped")
            c = hull.face_center(fi)
            self.assertLess(sum(x * y for x, y in zip(hull.face_normal(fi), c)), 0, "normals point inward")
        self.assertEqual(set(hull.face_mat), {"ink"})
        box = Mesh()  # a hard-edged cube must grow as one shell, no gaps at the corners
        box.verts = [[x, y, z] for x in (0, 10) for y in (0, 10) for z in (0, 10)]
        for q in ([0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]):
            box.add_face(q, "gold")
        grown = hull_mesh(box, 1.0, "ink")
        self.assertEqual(len(grown.verts), 8)
        self.assertTrue(all(abs(abs(c - 5) - 5 - 1 / math.sqrt(3)) < 1e-6 for v in grown.verts for c in v))

    def test_toon_op_scene(self):
        name = "ToonScene"
        scene = new_scene(name)
        self.addCleanup(forget, name)
        scene.data["materials"].append({"id": "gold", "name": "Gold", "base_color": [0.95, 0.7, 0.2, 1.0]})
        from .kernel.mesh import sphere
        from .hq.groom import _add_object
        node = _add_object(scene, "Ball", sphere(_RADIUS, 16, 8, "gold"))
        out = hq.HQ_OPS["toon"](scene, {"ids": [node["id"]], "outline": 0.6, "bands": 4})
        hull = scene.node(out["ids"][0])
        self.assertEqual((hull["parent"], hull["toon_hull"]), (node["id"], node["id"]))
        mats = scene.materials()
        self.assertEqual(mats["gold"]["toon"]["bands"], 4)
        self.assertTrue(mats[hull["material"]]["toon_outline"])
        again = hq.HQ_OPS["toon"](scene, {"ids": [node["id"]]})  # re-run replaces, never stacks
        self.assertEqual(sum(1 for n in scene.data["nodes"] if n.get("toon_hull")), 1, again)
        with self.assertRaises(ValueError) as err:
            hq.HQ_OPS["toon"](scene, {"ids": []})
        self.assertIn("Example", str(err.exception))
        self.assertLessEqual(len(str(hq.compact(out))), 300)

    @unittest.skipUnless(_READY, "Blender engine not installed")
    def test_toon_render_has_dark_edges(self):
        from .hq.groom import _add_object
        from .kernel.mesh import sphere
        name = "ToonRender"
        scene = new_scene(name)
        self.addCleanup(forget, name)
        scene.data["materials"].append({"id": "gold", "name": "Gold", "base_color": [0.95, 0.7, 0.2, 1.0], "roughness": 0.8})
        node = _add_object(scene, "Ball", sphere(_RADIUS, 32, 16, "gold"))
        hq.HQ_OPS["toon"](scene, {"ids": [node["id"]], "outline": 0.8})
        mesh = Mesh()
        for n in scene.data["nodes"]:
            if n.get("type") == "object":
                mesh.append(scene.world_mesh(n))
        mats = scene.materials()
        folder = Path(self.tmp.name)
        toon = render.render_tiles(mesh, folder / "toon.png", ["front"], mats, style="toon")
        self.assertEqual(render.last_renderer, "eevee", render.last_note)
        flat = Mesh()
        flat.append(scene.world_mesh(scene.node(node["id"])))
        plain = render.render_tiles(flat, folder / "plain.png", ["front"], mats)
        self.assertGreater(brown(toon), 150, "toon render needs a crisp brown outline")
        self.assertLess(brown(plain), 40)


if __name__ == "__main__":
    unittest.main()
