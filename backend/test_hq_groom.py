"""HQ groom: strand density, card budget, cloth bake, fur shells. Runs without Blender: the engine call is
replaced by the same op functions the bpy worker would run (through a JSON round trip, like the wire)."""
import json
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import hq, rig as rigs
from .engine.ops import cloth as ec, groom as eg
from .hq import groom as hqg
from .scene import dispatch, forget, load

OPS = {"hair": eg.op_hair, "hair_cards": eg.op_hair_cards, "cloth": ec.op_cloth, "fur_shells": eg.op_fur_shells}


def direct(name, params, timeout=600.0):
    return json.loads(json.dumps(OPS[name](json.loads(json.dumps(params)))))


class GroomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-groom-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        self.call = patch.object(hqg, "_call", direct)
        self.call.start()
        dispatch("", "scene_new", {"name": "Groom"})

    def tearDown(self):
        self.call.stop()
        forget("Groom")
        self.env.stop()
        self.tmp.cleanup()

    def add(self, kind, **params):
        return dispatch("Groom", "add", {"kind": kind, "params": params})["id"]

    def test_ops_registered(self):
        for name in ("hair", "hair_cards", "cloth", "fur_shells"):
            self.assertIn(name, hq.HQ_OPS)
        with self.assertRaisesRegex(ValueError, "Did you mean 'ponytail'"):
            eg.style_of({"style": "pontytail"})
        with self.assertRaisesRegex(ValueError, "Did you mean 'auburn'"):
            eg.rgb("aubern")

    def test_hair_density(self):
        nid = self.add("box", w=20, d=20, h=20)
        scene = load("Groom")
        mesh = scene.world_mesh(scene.node(nid))
        lo, hi = mesh.bounds()
        tris = hqg.region_tris(mesh, "top")
        self.assertEqual(len(tris), 2)  # only the top face
        strands = eg.build_strands({"tris": tris, "style": "short", "density": 30, "seed": 7})
        want = 20 * 20 * 30
        self.assertLessEqual(abs(len(strands) - want), want * 0.05)
        for s in strands:
            x, y, z = s[0]
            self.assertAlmostEqual(z, hi[2], places=6)
            self.assertTrue(lo[0] - 1e-6 <= x <= hi[0] + 1e-6 and lo[1] - 1e-6 <= y <= hi[1] + 1e-6)
        result = hqg.hair(scene, {"id": nid, "style": "short", "color": "auburn"})
        self.assertEqual(result["strands"], round(400 * eg.STYLES["short"]["density"]))
        self.assertEqual(len(scene.data["grooms"]), 1)
        self.assertLessEqual(len(json.dumps(hq.compact(result))), 300)

    def test_hair_styles(self):
        nid = self.add("sphere", radius=12)
        scene = load("Groom")
        for style in eg.STYLES:
            result = hqg.hair(scene, {"id": nid, "style": style, "density": 6})
            self.assertGreater(result["strands"], 100, style)
        tris = hqg.region_tris(scene.world_mesh(scene.node(nid)), "top")
        pony = eg.build_strands({"tris": tris, "style": "ponytail", "density": 6})
        self.assertTrue(all(len(s) == eg.STYLES["ponytail"]["points"] for s in pony))
        self.assertLess(min(s[-1][2] for s in pony), min(s[0][2] for s in pony))  # the tail hangs below the roots
        with self.assertRaisesRegex(ValueError, "Did you mean 'top'"):
            hqg.region_tris(scene.world_mesh(scene.node(nid)), "tpo")

    def test_cards_budget(self):
        nid = self.add("sphere", radius=12)
        scene = load("Groom")
        hqg.hair(scene, {"id": nid, "style": "ponytail", "density": 6, "color": "blonde"})
        before = len(scene.data["nodes"])
        result = hqg.hair_cards(scene, {"id": nid, "cards": 120, "atlas": "1K"})
        self.assertEqual((result["cards"], result["tris"], result["atlas"]), (120, 2640, 1024))
        self.assertLessEqual(result["tris"], 2800)
        self.assertEqual(len(scene.data["nodes"]), before + 1)
        node = scene.node(result["ids"][0])
        mesh = scene.local_mesh(node)
        self.assertEqual(mesh.tri_count(), 2640)
        self.assertTrue(all(0.0 <= c <= 1.0 for face in mesh.uvs for uv in face for c in uv))
        mat = scene.materials()[mesh.face_mat[0]]
        self.assertEqual((mat["alpha_mode"], mat["double_sided"]), ("MASK", True))
        png = Path(scene.folder) / "textures" / mat["textures"]["base_color"]
        raw = png.read_bytes()
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", raw[16:24]), (1024, 1024))
        self.assertTrue((Path(scene.folder) / "textures" / mat["textures"]["groom_data"]).is_file())
        tight = hqg.hair_cards(scene, {"id": nid, "cards": 120, "atlas": 512, "tris": 100})
        self.assertEqual(tight["tris"], 240)  # one segment per card is the floor
        self.assertTrue(tight["warnings"])
        with self.assertRaisesRegex(ValueError, "Run hair"):
            hqg.hair_cards(scene, {"id": self.add("box")})

    def test_cloth_bake_bones(self):
        # a sphere sits in the cape's path: nothing may end up inside it
        ball = [0.0, 0.0, 160.0, 20.0]
        params = {"preset": "cape", "pin": [0.0, 0.0, 200.0], "spheres": [ball], "frames": 32, "bake_to": "bones"}
        sim = ec.simulate(params)
        self.assertEqual(len(sim["frames"]), 32)
        low = 1e9
        for grid in sim["frames"]:
            for row in grid[1:]:
                for q in row:
                    low = min(low, math.dist(q, ball[:3]))
        self.assertGreaterEqual(low, ball[3] - 1e-3)
        self.assertLess(low, ball[3] + 2.0)  # it did touch the sphere
        tracks, bones = ec.bake_bones(sim)
        self.assertEqual(len(bones), 6)
        self.assertTrue(all(len(t["rot"]) == 32 for t in tracks.values()))
        clip = {"tracks": tracks}
        rig = {"bones": bones}
        for frame in (0, 15, 31):  # the baked chain follows the simulated sheet
            mats = rigs.skin_matrices(rigs.ordered(rig), clip, frame)
            centers = ec._centers(sim["frames"][frame])
            for b, m in zip(rigs.ordered(rig), mats):
                h = b["head"]
                moved = [sum(m[i][k] * h[k] for k in range(3)) + m[i][3] for i in range(3)]
                self.assertLess(math.dist(moved, centers[bones.index(b)]), 8.0, (frame, b["name"]))
        # through the HQ op on a real body
        body = self.add("capsule", radius=25, height=180)
        scene = load("Groom")
        result = hqg.cloth(scene, {"preset": "cape", "pin": "shoulders", "collide": body, "frames": 32, "bake_to": "bones"})
        self.assertGreaterEqual(result["clearance"], 0.0)
        rig = rigs.find_rig(scene, result["ids"][1])
        clip = rigs.find_clip(rig, result["clip"])
        self.assertEqual((len(rig["bones"]), clip["frames"], len(clip["tracks"])), (6, 32, 6))
        cape = scene.node(result["ids"][0])
        self.assertEqual(cape["skin"], {"rig": rig["id"], "paint": True})
        weights = [scene.local_mesh(cape).attrs["wt:" + b["name"]] for b in rig["bones"]]
        self.assertTrue(all(abs(sum(w[v] for w in weights) - 1.0) < 1e-6 for v in range(len(weights[0]))))
        self.assertLessEqual(len(json.dumps(hq.compact(result))), 300)

    def test_cloth_bake_shapes(self):
        body = self.add("capsule", radius=25, height=180)
        scene = load("Groom")
        result = hqg.cloth(scene, {"preset": "scarf", "pin": "head", "collide": body, "frames": 16, "bake_to": "shapes"})
        cloth = scene.node(result["ids"][0])
        mesh = scene.local_mesh(cloth)
        shapes = [k for k in mesh.attrs if k.startswith("sk:")]
        self.assertEqual(len(shapes), 16)
        clip = rigs.find_clip(rigs.find_rig(scene, result["ids"][1]), result["clip"])
        self.assertEqual(sorted(clip["morphs"]), sorted(k[3:] for k in shapes))
        self.assertGreater(max(abs(c) for d in mesh.attrs[shapes[-1]] for c in d), 0.0)
        with self.assertRaisesRegex(ValueError, "Did you mean 'cape'"):
            ec.preset_of("capee")

    def test_fur_shells_layers(self):
        bear = self.add("sphere", radius=10)
        scene = load("Groom")
        base = scene.local_mesh(scene.node(bear)).tri_count()
        result = hqg.fur_shells(scene, {"id": bear, "layers": 8, "length": 1.4, "color": "brown"})
        self.assertEqual(result["tris_added"], 8 * base)
        mesh = scene.local_mesh(scene.node(bear))
        self.assertEqual(mesh.tri_count(), 9 * base)
        shells = [m for m in scene.data["materials"] if m["name"].startswith("FurShell_")]
        self.assertEqual(len(shells), 8)
        self.assertEqual(len({m["alpha_cutoff"] for m in shells}), 8)
        self.assertEqual(len(set(mesh.face_mat)), 9)
        self.assertEqual(len(mesh.uvs), len(mesh.faces))
        outer = max(math.dist(v, [0, 0, 10]) for v in mesh.verts)
        self.assertAlmostEqual(outer, 11.4, delta=0.1)  # last shell sits `length` above the skin
        self.assertTrue((Path(scene.folder) / "textures" / "fur_noise.png").is_file())


if __name__ == "__main__":
    unittest.main()
