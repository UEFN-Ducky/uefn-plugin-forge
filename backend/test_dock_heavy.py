"""A huge mesh opens in the dock as a light preview and never stalls it. Run: py -3 -m backend.test_dock_heavy"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from .scene import PREVIEW_TRIS, dispatch, forget, load


class HeavyDock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-heavy-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        dispatch("Heavy", "scene_new", {"name": "Heavy"})
        # about 200k triangles: three times the preview cap
        dispatch("Heavy", "add", {"kind": "sphere", "params": {"radius": 100, "seg": 400, "rings": 260}})
        scene = load("Heavy")
        self.node = next(n for n in scene.data["nodes"] if n.get("type") == "object")
        self.full = scene.local_mesh(self.node).tri_count()

    def tearDown(self):
        forget("Heavy")
        self.env.stop()
        self.tmp.cleanup()

    def test_heavy_scene_payload_small(self):
        self.assertGreater(self.full, 3 * PREVIEW_TRIS)
        start = time.perf_counter()
        info = dispatch("Heavy", "scene_payload", {})
        took = time.perf_counter() - start
        protos = [p for p in info["prototypes"] if p.get("tris")]
        self.assertTrue(protos)
        for proto in protos:
            self.assertLessEqual(proto["tris"], PREVIEW_TRIS)
            self.assertTrue(proto.get("preview"))
            self.assertEqual(proto.get("full_tris"), self.full)
        size = len(json.dumps(info))
        self.assertLess(size, 8_000_000, size)
        self.assertLess(took, 5.0, took)
        print("\nheavy: %d tris -> preview %d tris, payload %.1f MB in %.2f s"
              % (self.full, protos[0]["tris"], size / 1e6, took))

    def test_full_mesh_untouched(self):
        dispatch("Heavy", "scene_payload", {})
        scene = load("Heavy")
        self.assertEqual(scene.local_mesh(self.node).tri_count(), self.full)


if __name__ == "__main__":
    unittest.main()
