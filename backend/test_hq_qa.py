"""py -3 -m backend.test_hq_qa  (print_check skips until the Forge engine is installed)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from .engine import runtime
from .hq import HQ_OPS, qa
from .kernel.mesh import Mesh
from .scene import dispatch, forget, load

_READY = runtime.engine_status()["state"] == "ready"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-hq-qa-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        self.names = []

    def tearDown(self):
        for name in self.names:
            forget(name)
        self.env.stop()
        self.tmp.cleanup()

    def scene(self, kind="box", **params):
        name = "HqQa%d" % len(self.names)
        self.names.append(name)
        dispatch("", "scene_new", {"name": name})
        nid = dispatch(name, "add", {"kind": kind, "object_name": "Crate", "params": params})["id"]
        scene = load(name)
        return scene, scene.node(nid)

    def short(self, out):
        from .hq import compact
        self.assertLessEqual(len(json.dumps(compact(out))), 300, out)


class QaTests(Base):
    def test_clean_box_passes(self):
        scene, node = self.scene("box", w=60, d=60, h=60)
        out = HQ_OPS["qa"](scene, {"id": node["id"]})
        self.assertTrue(out["pass"], out)
        self.assertEqual(out["score"], 100, out)
        self.short(out)

    def test_broken_mesh_flagged_then_fixed(self):
        scene, node = self.scene("box", w=60, d=60, h=60)
        mesh = scene._bake(node)
        for face in mesh.faces:  # inside out
            face.reverse()
        mesh.add_face([mesh.add_vert(v) for v in ([0, 0, 200], [10, 0, 200], [10, 10, 200], [5, 15, 200], [0, 10, 200])])
        mesh.add_vert([999, 999, 999])  # loose
        scene.write_mesh(node, mesh)
        node["name"] = "Crate.001"
        node["transform"]["pos"] = [0, 0, 30]
        out = HQ_OPS["qa"](scene, {"id": node["id"], "profile": "roblox", "budget": "prop"})
        self.assertFalse(out["pass"], out)
        for word in ("n-gon", "unused", "duplicate-style", "floats"):
            self.assertIn(word, out["summary"], out)
        self.assertLess(out["score"], 80)
        self.short(out)
        fixed = HQ_OPS["qa"](scene, {"id": node["id"], "profile": "roblox", "fix": True})
        self.assertGreater(fixed["score"], out["score"], fixed)
        self.assertEqual(node["name"], "Crate")
        self.assertNotIn("n-gon", fixed["summary"])

    def test_inward_normals_and_fix(self):
        scene, node = self.scene("box", w=40, d=40, h=40)
        mesh = scene._bake(node)
        for face in mesh.faces:
            face.reverse()
        scene.write_mesh(node, mesh)
        out = HQ_OPS["qa"](scene, {"id": node["id"]})
        self.assertIn("inward", out["summary"])
        HQ_OPS["qa"](scene, {"id": node["id"], "fix": True})
        self.assertGreater(qa.signed_volume(scene.world_mesh(node)), 0)

    def test_budget_and_profile(self):
        scene, node = self.scene("sphere", radius=30, seg=64, rings=48)
        out = HQ_OPS["qa"](scene, {"id": node["id"], "budget": "module"})
        self.assertIn("> module 5k", out["summary"], out)
        with self.assertRaisesRegex(ValueError, "Did you mean 'roblox'"):
            HQ_OPS["qa"](scene, {"profile": "robox"})

    def test_pivot_fix_keeps_world_place(self):
        scene, node = self.scene("box", w=40, d=40, h=40)
        mesh = scene._bake(node)
        mesh.translate([0, 0, 25])
        scene.write_mesh(node, mesh)
        before = scene.world_mesh(node).bounds()
        out = HQ_OPS["qa"](scene, {"id": node["id"], "tol": 100})
        self.assertIn("origin=base", out["summary"])
        HQ_OPS["qa"](scene, {"id": node["id"], "fix": True, "tol": 100})
        after = scene.world_mesh(node).bounds()
        for a, b in zip(before[0] + before[1], after[0] + after[1]):
            self.assertAlmostEqual(a, b, places=3)
        self.assertTrue(qa._pivot_base(scene.local_mesh(node)))

    def test_uv_overlap(self):
        m = Mesh()
        a = [m.add_vert(v) for v in ([0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0])]
        m.add_face(a, uvs=[[0, 0], [1, 0], [1, 1], [0, 1]])
        m.add_face(a[::-1], uvs=[[0, 1], [1, 1], [1, 0], [0, 0]])
        self.assertEqual(qa.uv_overlap(m), 100.0)
        m.uvs[1] = [[0, 0.5], [0.5, 0.5], [0.5, 0], [0, 0]]
        m.uvs[0] = [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]]
        self.assertEqual(qa.uv_overlap(m), 0.0)


class CollisionTests(Base):
    def test_box_and_hull(self):
        scene, node = self.scene("sphere", radius=20)
        out = HQ_OPS["collision"](scene, {"id": node["id"]})
        ucx = scene.node(out["ids"][0])
        self.assertEqual(ucx["name"], "UCX_%s_01" % node["name"])
        self.assertEqual(ucx["layer"], "collision")
        hull = scene.world_mesh(ucx)
        self.assertLessEqual(len(hull.verts), 32)
        self.assertGreater(qa.signed_volume(hull), 0)  # closed and outward
        edges = {}
        for f in hull.faces:
            for i, a in enumerate(f):
                k = tuple(sorted((a, f[(i + 1) % len(f)])))
                edges[k] = edges.get(k, 0) + 1
        self.assertTrue(all(v == 2 for v in edges.values()))
        HQ_OPS["collision"](scene, {"id": node["id"], "kind": "box"})
        hulls = [n for n in scene.data["nodes"] if n["name"].startswith("UCX_")]
        self.assertEqual(len(hulls), 1)  # replaced, not stacked
        lo, hi = scene.world_mesh(hulls[0]).bounds()
        self.assertAlmostEqual(hi[0] - lo[0], 40 * 0.98, delta=0.5)
        self.short(out)
        self.assertTrue(HQ_OPS["qa"](scene, {})["pass"])  # UCX parts are not graded as assets
        with self.assertRaisesRegex(ValueError, "needs id"):
            HQ_OPS["collision"](scene, {})


class RigCheckTests(Base):
    def test_manny_passes_and_roblox_flags_names(self):
        scene, node = self.scene("box", w=60, d=30, h=180)
        HQ_OPS["rig_template"](scene, {"template": "ue5_manny", "ids": [node["id"]]})
        HQ_OPS["clip"](scene, {"kind": "walk"})
        out = HQ_OPS["rig_check"](scene, {})
        self.assertTrue(out["pass"], out)
        roblox = HQ_OPS["rig_check"](scene, {"profile": "roblox"})
        self.assertIn("roblox bones missing", roblox["summary"])
        self.assertFalse(roblox["pass"])
        self.short(roblox)

    def test_painted_skin_problems(self):
        scene, node = self.scene("box", w=60, d=30, h=180)
        HQ_OPS["rig_template"](scene, {"template": "ue5_manny", "ids": [node["id"]]})
        rig = scene.data["rigs"][0]
        mesh = scene._bake(node)
        n = len(mesh.verts)
        for k, bone in enumerate(("pelvis", "spine_01", "head", "thigh_l", "thigh_r")):
            mesh.attrs["wt:" + bone] = [0.2 if i else 0.0 for i in range(n)]
        scene.write_mesh(node, mesh)
        node["skin"] = {"rig": rig["id"], "paint": True}
        rig.setdefault("actions", []).append({"id": "a1", "name": "Bad", "fps": 30, "frames": 10, "loop": True,
                                              "tracks": {"head": {"rot": [[0, [0, 0, 0], "linear"], [10, [20, 0, 0], "linear"]],
                                                                  "scale": [[0, [1, 1, 1], "linear"]]}}})
        out = HQ_OPS["rig_check"](scene, {})
        for word in ("unweighted", "> 4 influences", "weight nothing", "bone scale", "loop seam 20"):
            self.assertIn(word, out["summary"], out)


def _png(path, w, h, inside):
    """8-bit RGB PNG: black where inside(x, y), white elsewhere."""
    import struct
    import zlib
    rows = b"".join(b"\x00" + b"".join(b"\x00\x00\x00" if inside(x, y) else b"\xff\xff\xff" for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class RefCompareTests(Base):
    def test_box_matches_rectangle_not_circle(self):
        scene, node = self.scene("box", w=60, d=60, h=120)
        rect = os.path.join(self.tmp.name, "rect.png")
        disc = os.path.join(self.tmp.name, "disc.png")
        _png(rect, 120, 160, lambda x, y: 30 <= x < 90 and 20 <= y < 140)
        _png(disc, 160, 160, lambda x, y: (x - 80) ** 2 + (y - 80) ** 2 < 60 ** 2)
        good = HQ_OPS["ref_compare"](scene, {"id": node["id"], "front": rect, "side": rect})
        self.assertTrue(good["pass"], good)
        self.assertGreater(good["front_iou"], 0.95)
        bad = HQ_OPS["ref_compare"](scene, {"id": node["id"], "front": disc})
        self.assertFalse(bad["pass"], bad)
        self.assertIn("IoU", bad["summary"])
        self.short(bad)
        with self.assertRaisesRegex(ValueError, "needs front"):
            HQ_OPS["ref_compare"](scene, {"id": node["id"]})


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class PrintTests(Base):
    def test_closed_box_prints_open_plane_fails(self):
        scene, node = self.scene("box", w=40, d=40, h=40)
        out = HQ_OPS["print_check"](scene, {"id": node["id"]})
        self.assertTrue(out["pass"], out)
        self.assertAlmostEqual(out["volume_cm3"], 64000, delta=100)
        self.short(out)
        mesh = scene._bake(node)
        mesh.faces, mesh.uvs, mesh.face_mat = mesh.faces[1:], mesh.uvs[1:], mesh.face_mat[1:]
        scene.write_mesh(node, mesh)
        bad = HQ_OPS["print_check"](scene, {"id": node["id"]})
        self.assertFalse(bad["pass"], bad)
        self.assertIn("open", bad["summary"])

    def test_thin_wall(self):
        scene, node = self.scene("box", w=40, d=40, h=0.05)
        out = HQ_OPS["print_check"](scene, {"id": node["id"], "min_wall": 0.1})
        self.assertIn("thinner", out["summary"], out)


if __name__ == "__main__":
    unittest.main()
