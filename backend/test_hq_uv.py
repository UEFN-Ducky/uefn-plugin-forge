"""HQ uv + topology: unwrap/pack to a texel target, overlap and density report (Blender tests skip until installed)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from . import hq
from .engine import runtime
from .hq.uv import report

_READY = runtime.engine_status()["state"] == "ready"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-uv-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        from .scene import forget
        forget("UV")
        self.env.stop()
        self.tmp.cleanup()

    def scene(self, kind="box", **params):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "UV"})
        nid = dispatch("UV", "add", {"kind": kind, "params": params})["id"]
        return load("UV"), nid

    def run_op(self, name, scene, **p):
        r = hq.HQ_OPS[name](scene, p)
        self.assertLessEqual(len(json.dumps(hq.compact(r))), 300, r)
        return r


class TopologyTests(Case):
    def test_box_report(self):
        scene, nid = self.scene(w=100, d=100, h=100)
        r = self.run_op("topology", scene, id=nid)
        self.assertEqual(r["stats"]["quads_pct"], 100.0)
        self.assertEqual(r["stats"]["ngons"], 0)
        self.assertEqual(r["stats"]["poles"], 8)  # every cube corner has 3 edges

    def test_ngon_and_tri_counts(self):
        class M:
            faces = [[0, 1, 2], [0, 2, 3, 4, 5]]
        r = report(M())
        self.assertEqual((r["tris"], r["ngons"], r["border_verts"]), (1, 1, 6))

    def test_registered(self):
        for name in ("uv", "topology"):
            self.assertIn(name, hq.HQ_OPS)
        self.assertNotIn("uv", hq._BROKEN)


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class UVTests(Case):
    def test_seams_hit_texel_target(self):
        scene, nid = self.scene(w=100, d=100, h=100)
        r = self.run_op("uv", scene, id=nid, method="seams", texel=512, res=2048)
        s = r["stats"]
        self.assertGreaterEqual(s["islands"], 1)
        self.assertLessEqual(s["overlap_pct"], 0.5, r)
        self.assertAlmostEqual(s["texel_px_m"], 512, delta=51.2)
        self.assertLessEqual(s["spread"], 1.3)
        self.assertEqual(s["margin_px"], 8)
        check = self.run_op("uv", scene, id=nid, check=True)["stats"]
        self.assertAlmostEqual(check["texel_px_m"], s["texel_px_m"], delta=1)

    def test_smooth_sphere_unwraps_without_overlap(self):
        scene, nid = self.scene("sphere", radius=50)
        r = self.run_op("uv", scene, id=nid, method="seams", texel=256, res=1024)
        self.assertLessEqual(r["stats"]["overlap_pct"], 0.5, r)
        self.assertAlmostEqual(r["stats"]["texel_px_m"], 256, delta=25.6)

    def test_lightmap_fills_square_without_overlap(self):
        scene, nid = self.scene(w=200, d=50, h=100)
        r = self.run_op("uv", scene, id=nid, method="smart", lightmap=True, res=512)
        s = self.run_op("uv", scene, id=nid, check=True, res=512)["stats"]
        self.assertEqual(s["overlap_pct"], 0.0, r)
        self.assertFalse(s["oob"])
        self.assertGreater(s["packed_pct"], 40)
        self.assertIn("lightmap-safe", r["summary"])

    def test_too_big_texel_warns(self):
        scene, nid = self.scene(w=1000, d=1000, h=1000)
        r = self.run_op("uv", scene, id=nid, texel=2048, res=512)
        self.assertTrue(any("does not fit" in w for w in r["warnings"]), r)
        self.assertFalse(self.run_op("uv", scene, id=nid, check=True, res=512)["stats"]["oob"])


if __name__ == "__main__":
    unittest.main()
