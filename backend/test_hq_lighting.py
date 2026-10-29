"""HQ light: preset look-dev stills are lit (not black, not clipped) and the presets differ (Blender tests skip until installed)."""
import json
import os
import statistics
import tempfile
import unittest
from unittest.mock import patch

from . import hq
from .engine import runtime

_READY = runtime.engine_status()["state"] == "ready"


def _luma(path):
    from PIL import Image
    return [sum(px) / 3 for px in Image.open(path).convert("RGB").resize((160, 120)).getdata()]


class LightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-light-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        from .scene import forget
        forget("Light")
        self.env.stop()
        self.tmp.cleanup()

    def test_registered_and_errors(self):
        self.assertIn("light", hq.HQ_OPS)
        self.assertNotIn("lighting", hq._BROKEN)
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Light"})
        with self.assertRaisesRegex(ValueError, "Did you mean 'studio'"):
            hq.HQ_OPS["light"](load("Light"), {"preset": "studoi"})

    @unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
    def test_presets_render_and_differ(self):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Light"})
        nid = dispatch("Light", "add", {"kind": "sphere", "params": {"radius": 40}})["id"]
        scene = load("Light")
        hq.HQ_OPS["material"](scene, {"material": "Clay", "preset": "clay", "id": nid})
        spread = {}
        for preset in ("studio", "dramatic", "neutral"):
            r = hq.HQ_OPS["light"](scene, {"id": nid, "preset": preset, "size": [320, 240]})
            self.assertLessEqual(len(json.dumps(hq.compact(r))), 300, r)
            luma = _luma(r["path"])
            mean = statistics.mean(luma)
            self.assertTrue(8 < mean < 245, (preset, mean))
            spread[preset] = statistics.pstdev(luma)
        self.assertGreater(spread["dramatic"], spread["neutral"])
        r = hq.HQ_OPS["light"](scene, {"id": nid, "env": "golden", "transform": "neutral", "size": [320, 240]})
        self.assertTrue(r["path"].endswith("light_golden.png"))
        self.assertEqual(scene.data["light"]["env"], "golden")


if __name__ == "__main__":
    unittest.main()
