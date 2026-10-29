"""HQ paint_art through forge_hq on a fake api with the real Blender engine: stroke direction, not-noise, AO, projection.

Needs the engine (runtime.install()); skips without it. Run: py -3 -m backend.test_hq_paint_art
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

from . import hq, tools
from .engine import client, runtime
from .scene import forget, load

_READY = runtime.engine_status()["state"] == "ready"


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def _box(img, r):
    """Mean over a (2r+1)^2 window."""
    for ax in (0, 1):
        img = sum(np.roll(img, s, ax) for s in range(-r, r + 1)) / (2 * r + 1)
    return img


def _lum(path):
    rgb = np.asarray(Image.open(path).convert("RGB"), np.float32) / 255.0
    return rgb[::-1], rgb[::-1] @ np.array([0.299, 0.587, 0.114], np.float32)  # bottom-up like the engine


def _hf_ratio(lum, occ):
    """High-pass energy over total contrast: near 1 for dabs/noise, low for smooth directional brushwork."""
    inner = occ.copy()
    for s in (-3, 3):
        for ax in (0, 1):
            inner &= np.roll(occ, s, ax)
    hp = lum - _box(lum, 2)
    return float(hp[inner].std() / max(lum[inner].std(), 1e-6))

def _corr_len(lum, occ):
    """Longest decorrelation distance (px, acf below 1/e) of the brush-stroke residual over four directions."""
    res = (lum - _box(lum, 4)) * occ
    var = float((res[occ] ** 2).mean()) or 1e-9
    best = 0.0
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        length = 40.0
        for lag in range(1, 40):
            sh = lambda a: np.roll(np.roll(a, -lag * dy, 0), -lag * dx, 1)
            both = occ & sh(occ)
            if both.sum() < 20 or float((res * sh(res))[both].mean()) / var < 1.0 / np.e:
                length = lag * float(np.hypot(dx, dy))
                break
        best = max(best, length)
    return best



@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class PaintArt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-paint-")
        cls.patches = [patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")}),
                       patch.object(tools, "_attach_captures", lambda r: r)]
        for p in cls.patches:
            p.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        client.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def shape(self, name, kind, **params):
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        out = self.t["forge_batch"](name=name, steps=[{"tool": "forge_add", "kind": kind, "object_name": name, "params": params}])
        return out["ids"][0]

    def hq(self, name, op, nid, **params):
        out = self.t["forge_hq"](name=name, op=op, id=nid, params={"wait": True, **params})
        self.assertTrue(out.get("ok"), out)
        self.assertLessEqual(len(str(out)), 600, out)
        return out

    def textures(self, name, nid):
        scene = load(name)
        mat = scene.materials()[scene.node(nid)["material"]]
        folder = scene.folder / "textures"
        return folder, {slot: folder / f for slot, f in mat["textures"].items()}

    def painted(self, key):
        """A 512px sphere painted with flow=down, debug maps kept: (rgb, lum, flow map, ao map)."""
        if key not in self.cache:
            nid = self.shape("PaintSphere", "sphere", radius=100, seg=24, rings=12)
            self.hq("PaintSphere", "paint_strokes", nid, base=[0.85, 0.6, 0.2], flow="down", size=512, density=1.5, width=8, length=48, debug=True)
            folder, files = self.textures("PaintSphere", nid)
            rgb, lum = _lum(files["base_color"])
            stem = files["base_color"].stem[2:-3]
            self.cache[key] = (rgb, lum, np.load(folder / (stem + "_dbg_flow.npy")), np.load(folder / (stem + "_dbg_ao.npy")),
                               files, load("PaintSphere").materials()[load("PaintSphere").node(nid)["material"]]["textures"])
        return self.cache[key]

    def test_strokes_directional(self):
        rgb, lum, flow, ao, files, slots = self.painted("sphere")
        self.assertIn("normal", slots)
        occ = ao >= 0
        band = lum - _box(lum, 7)  # strokes only: the AO gradient is low frequency
        gx = (np.roll(band, -1, 1) - np.roll(band, 1, 1)) * 0.5
        gy = (np.roll(band, -1, 0) - np.roll(band, 1, 0)) * 0.5
        jxx, jyy, jxy = _box(gx * gx, 3), _box(gy * gy, 3), _box(gx * gy, 3)
        energy = jxx + jyy
        coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / np.maximum(energy, 1e-9)
        theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy) + np.pi / 2  # stroke direction, perpendicular to its gradient
        inner = occ.copy()
        for s in (-4, 4):
            for ax in (0, 1):
                inner &= np.roll(occ, s, ax)
        use = inner & (flow[..., 2] > 0.8) & (coherence > 0.5) & (energy > np.percentile(energy[inner], 60))
        self.assertGreater(int(use.sum()), 500, "too few stroke pixels measured (inner %d, flow %d, coherent %d, strength %s)" % (
            inner.sum(), (inner & (flow[..., 2] > 0.8)).sum(), (inner & (coherence > 0.5)).sum(), np.nanpercentile(flow[..., 2], [0, 50, 100])))
        cos = np.abs(np.cos(theta - np.arctan2(flow[..., 1], flow[..., 0])))[use]
        self.assertGreater(float(cos.mean()), 0.7, "stroke orientation vs flow field")

    def test_not_noise(self):
        rgb, lum, flow, ao, files, slots = self.painted("sphere")
        occ = ao >= 0
        rng = np.random.default_rng(3)
        dabs = np.full(lum.shape, lum[occ].mean(), np.float32)
        for _ in range(int(occ.sum() / 9)):  # random 3px dabs with the same contrast
            y, x = rng.integers(2, lum.shape[0] - 2), rng.integers(2, lum.shape[1] - 2)
            dabs[y - 1:y + 2, x - 1:x + 2] = lum[occ].mean() + rng.normal() * lum[occ].std()
        strokes, noise = _hf_ratio(lum, occ), _hf_ratio(dabs, occ)
        self.assertLess(strokes, noise * 0.8, "strokes %.2f vs dabs %.2f" % (strokes, noise))

    def test_ao_darkens_crevices(self):
        nid = self.shape("PaintRing", "torus", major=60, minor=50, seg=24, rings=12)
        self.hq("PaintRing", "paint_strokes", nid, base=[0.8, 0.6, 0.3], flow="normal", size=256, density=0.3, ao=0.9, rim=0.1, debug=True)
        folder, files = self.textures("PaintRing", nid)
        rgb, lum = _lum(files["base_color"])
        ao = np.load(folder / (files["base_color"].stem[2:-3] + "_dbg_ao.npy"))
        occ = ao >= 0
        tight, open_ = occ & (ao < 0.85), occ & (ao > 0.98)
        self.assertGreater(int(tight.sum()), 200, "no crevice found, ao %s" % np.percentile(ao[occ], [0, 5, 50]))
        dark, light = lum[tight].mean(), lum[open_].mean()
        self.assertLess(dark, light - 0.03, "crevice %.3f vs open %.3f, ao %s" % (dark, light, np.percentile(ao[occ], [0, 20, 50, 80, 100])))

    def test_project_front_colors(self):
        nid = self.shape("ProjBox", "box", w=100, d=100, h=100)
        art = Path(self.tmp.name) / "front.png"
        img = np.zeros((64, 64, 3), np.uint8)
        img[:, :32], img[:, 32:] = (230, 30, 30), (30, 30, 230)  # red left, blue right, edge to edge
        Image.fromarray(img).save(art)
        done = self.hq("ProjBox", "project_ref", nid, images={"front": str(art)}, fill="flat", size=256)
        folder, files = self.textures("ProjBox", nid)
        rgb, _ = _lum(files["base_color"])
        px = (rgb * 255).reshape(-1, 3)
        px = px[px.sum(1) > 30]  # painted texels, not the empty gutter
        red = (np.abs(px - (230, 30, 30)).max(1) < 25).mean()
        blue = (np.abs(px - (30, 30, 230)).max(1) < 25).mean()
        self.assertGreater(red, 0.3, "red share %.2f mean %s %s" % (red, px.mean(0), done))
        self.assertGreater(blue, 0.3, "blue share %.2f" % blue)
        self.assertGreater(red + blue, 0.85, "front art must reach the surface unmuddied (%.2f)" % (red + blue))
        both = self.hq("ProjBox", "project_ref", nid, images={"front": str(art)}, size=128)  # strokes fill: still runs
        self.assertIn("projected", both["summary"])

    def bake(self, name, parts, presets, **extra):
        """parts: [(kind, object_name, params)], one material each. Returns (result, texture rgb, occupancy, scene)."""
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        ids = [self.t["forge_batch"](name=name, steps=[{"tool": "forge_add", "kind": k, "object_name": n, "params": prm}])["ids"][0] for k, n, prm in parts]
        for i, oid in enumerate(ids):
            self.t["forge_hq"](name=name, op="material", params={"material": "Part%d" % i, "overrides": {"color": [0.5, 0.5, 0.5]}, "id": oid})
        res = self.t["forge_hq"](name=name, op="paint_bake", params={"ids": ids, "presets": presets, "size": 256, "wait": True, "debug": True, **extra})
        self.assertTrue(res.get("ok"), res)
        self.assertLessEqual(len(str(res)), 300, res)
        scene = load(name)
        tex = next((scene.folder / "textures").glob("T_*_D.png"))
        rgb = np.asarray(Image.open(tex).convert("RGB"), np.float32)[::-1] / 255.0  # bottom-up like the engine
        return res, rgb, np.load(str(tex)[:-4] + "_dbg_occ.npy"), scene

    def test_bake_sphere_and_strip(self):
        res, img, occ, scene = self.bake("PaintBakeA", [("sphere", "Ball", {"radius": 30}), ("box", "Strip", {"w": 8, "d": 2, "h": 70})],
                                          ["fur_warm", "mane"])
        self.assertIn("paint_bake 256 T_Ball_D", res["summary"])
        node = next(n for n in scene.data["nodes"] if n.get("type") == "object")
        self.assertTrue(scene.materials()[node["material"]]["textures"]["base_color"].endswith("_D.png"))
        self.assertGreater(float(img[occ].std()), 0.03, "texture is flat")
        n = img.shape[0] // 8  # no 8x8 tile with zero variance sitting next to a different tile
        tiles = img.reshape(n, 8, n, 8, 3).transpose(0, 2, 1, 3, 4).reshape(n, n, 64, 3)
        full, flat, mean = occ.reshape(n, 8, n, 8).all(axis=(1, 3)), tiles.var(2).sum(-1) < 1e-8, tiles.mean(2)
        for y in range(n):
            for x in range(n):
                if full[y, x] and flat[y, x]:
                    for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < n and 0 <= xx < n and full[yy, xx]:
                            self.assertLess(float(np.abs(mean[y, x] - mean[yy, xx]).max()), 1e-3, "block at tile %d,%d" % (y, x))

    def test_bake_dashes_are_short(self):
        res, img, occ, _ = self.bake("PaintBakeB", [("box", "Strip", {"w": 8, "d": 2, "h": 70})], "mane")
        lum = img @ np.array([0.299, 0.587, 0.114], np.float32)
        run = max(max(int(r.sum()) for r in occ), max(int(c.sum()) for c in occ.T))  # strip length in texture pixels
        self.assertLess(_corr_len(lum, occ), 0.2 * run)

    def test_bake_mane_roots_darker_from_tuft_colour(self):
        name = "PaintBakeT"
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        nid = self.t["forge_batch"](name=name, steps=[{"tool": "forge_add", "kind": "box", "object_name": "Strip", "params": {"w": 8, "d": 2, "h": 70}}])["ids"][0]
        self.t["forge_hq"](name=name, op="material", params={"material": "Part0", "overrides": {"color": [0.5, 0.5, 0.5]}, "id": nid})
        scene = load(name)
        node = scene.node(nid)
        mesh = scene._bake(node)
        zs = [v[2] for v in mesh.verts]
        lo, hi = min(zs), max(zs)
        mesh.attrs["col:tuft"] = [[(z - lo) / (hi - lo)] * 3 + [1.0] for z in zs]  # root=0 at the bottom, tip=1 at the top
        scene.write_mesh(node, mesh)
        res = self.t["forge_hq"](name=name, op="paint_bake", params={"ids": [nid], "presets": "mane", "size": 256, "wait": True, "debug": True})
        self.assertTrue(res.get("ok"), res)
        self.assertLessEqual(len(str(res)), 300, res)
        self.assertIn("mane roots from tuft colour", res["summary"])
        tex = next((load(name).folder / "textures").glob("T_*_D.png"))
        lum = (np.asarray(Image.open(tex).convert("RGB"), np.float32)[::-1] / 255.0) @ np.array([0.299, 0.587, 0.114], np.float32)
        tmap = np.load(str(tex)[:-4] + "_dbg_tuft.npy")
        root, tip = lum[(tmap >= 0) & (tmap < 0.2)].mean(), lum[tmap > 0.8].mean()
        self.assertGreater(tip - root, 0.1, "root %.3f tip %.3f" % (root, tip))

    def test_bake_bad_preset_says_the_fix(self):
        nid = self.shape("PaintBakeC", "sphere", radius=10)
        with self.assertRaisesRegex(ValueError, "Use one of: fur_warm"):
            self.t["forge_hq"](name="PaintBakeC", op="paint_bake", id=nid, params={"presets": "lava", "size": 64, "wait": True})

    def test_errors_say_the_fix(self):
        nid = self.shape("PaintErr", "box", w=10, d=10, h=10)
        out = self.t["forge_hq"](name="PaintErr", op="paint_strokes", id=nid, params={"flow": "dwn"})
        self.assertIn("Use one of", str(out))
        out = self.t["forge_hq"](name="PaintErr", op="project_ref", id=nid, params={"images": {"front": "nope.png"}})
        self.assertIn("absolute path", str(out))
        self.assertIn("paint_strokes", hq.HQ_OPS)
        self.assertIn("project_ref", hq.HQ_OPS)


if __name__ == "__main__":
    unittest.main()
