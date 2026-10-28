"""Eevee look renders: one tile, hair visible, fallback without the engine, warm speed.

Needs the Blender engine for the Eevee tests (skipped without it). Run: py -3 -m backend.test_render_hq
"""
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageStat

from . import render
from .engine import client, runtime
from .kernel.mesh import sphere

_READY = runtime.engine_status()["state"] == "ready"
_MATS = {"fur": {"id": "fur", "name": "fur", "base_color": [0.8, 0.08, 0.04, 1.0], "roughness": 0.8},
         "skin": {"id": "skin", "name": "skin", "base_color": [0.3, 0.45, 0.8, 1.0], "roughness": 0.6}}


def head(hair=True):
    m = sphere(30, 24, 12, "skin")
    if hair:  # 60 tall cards fanned over the crown, like a mane
        for i in range(60):
            a = i * 2 * math.pi / 60
            dx, dy = math.cos(a), math.sin(a)
            base = len(m.verts)
            m.verts += [[dx * 12 - dy * 2, dy * 12 + dx * 2, 26], [dx * 12 + dy * 2, dy * 12 - dx * 2, 26],
                        [dx * 30 + dy * 2, dy * 30 - dx * 2, 60], [dx * 30 - dy * 2, dy * 30 + dx * 2, 60]]
            m.add_face([base, base + 1, base + 2, base + 3], "fur", [[0, 0], [1, 0], [1, 1], [0, 1]])
    return m


def reddish(im):
    px = im.convert("RGB").getdata()
    return sum(1 for r, g, b in px if r > g + 50 and r > b + 50)


class RenderHQ(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-render-")
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @unittest.skipUnless(_READY, "Blender engine not installed")
    def test_eevee_tile(self):
        views = ["three", "front", "side", "top"]
        path = render.render_tiles(head(), self.dir / "hair.png", views, _MATS)
        self.assertEqual(render.last_renderer, "eevee")
        bald = render.render_tiles(head(False), self.dir / "bald.png", views, _MATS)
        im = Image.open(path)
        self.assertLessEqual(max(im.size), 768)
        self.assertGreater(sum(ImageStat.Stat(im.convert("L")).stddev), 5)  # not blank
        # Hair region = the whole tile: the bald head has no fur-coloured pixels, the mane does.
        self.assertGreater(reddish(im), 400)
        self.assertEqual(reddish(Image.open(bald)), 0)
        self.assertGreater(ImageStat.Stat(im.convert("L")).var[0], ImageStat.Stat(Image.open(bald).convert("L")).var[0] * 0.5)
        single = render.render_png(head(), self.dir / "one.png", ["three"], _MATS)
        self.assertTrue(Path(single[0]).is_file())

    @unittest.skipUnless(_READY, "Blender engine not installed")
    def test_render_speed(self):
        big = sphere(40, 110, 110, "skin")  # 12100 quads
        self.assertGreaterEqual(len(big.faces), 12000)
        render.render_tiles(big, self.dir / "warm.png", ["three"], _MATS)  # starts the worker
        t = time.perf_counter()
        render.render_tiles(big, self.dir / "speed.png", ["three", "front", "side"], _MATS)
        self.assertLessEqual(time.perf_counter() - t, 3.0)
        self.assertEqual(render.last_renderer, "eevee")
        final = render.render_tiles(head(), self.dir / "final.png", ["three"], _MATS, quality="final")
        self.assertEqual(max(Image.open(final).size), 1024)

    def test_fallback_when_engine_missing(self):
        with patch.object(runtime, "engine_status", lambda: {"state": "missing"}):
            path = render.render_tiles(head(), self.dir / "soft.png", ["three", "front"], _MATS)
        self.assertEqual(render.last_renderer, "fallback renderer")
        self.assertLessEqual(max(Image.open(path).size), 768)
        with patch.object(client, "call", side_effect=client.EngineError("boom")), \
                patch.object(runtime, "engine_status", lambda: {"state": "ready"}):
            render.render_png(head(), self.dir / "soft2.png", ["three"], _MATS)
        self.assertEqual(render.last_renderer, "fallback renderer")


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        client.stop()
