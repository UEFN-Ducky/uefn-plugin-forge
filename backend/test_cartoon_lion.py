"""Acceptance: the cartoon lion v2 built with the cartoon ops (cartoon_body, cartoon_face, tufts, paint_bake, toon) through forge_batch.

Writes the scene CartoonLion into the real scenes dir and the renders to LimoRide/.ducky/refs/cartoon_lion/result2_*.png.
Needs the Blender engine; the front silhouette check needs front.jpg. Run: py -3 -m backend.test_cartoon_lion
"""
import json
import os
import re
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from . import tools
from .engine import client, runtime
from .scene import forget, load

REFS = Path(os.environ.get("LION_REFS", "C:/Users/tas13/Documents/Fortnite Projects/LimoRide/.ducky/refs/cartoon_lion"))
SCENE = "CartoonLion"
_READY = runtime.engine_status()["state"] == "ready"
HEAD_R = 13.0  # cm, the head ball (head_ratio 0.36 of a 60 cm lion, a little over half of it)


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def hq(op, params=None, id=None):
    step = {"tool": "forge_hq", "op": op, "params": params or {}}
    if id:
        step["id"] = id
    return step


def point(text, key):
    """The (x,y,z) after `key@` in a cartoon_body summary."""
    found = re.search(r"%s@\(([-\d.e]+),([-\d.e]+),([-\d.e]+)\)" % key, text)
    assert found, "no %s@(x,y,z) in %s" % (key, text)
    return [float(v) for v in found.groups()]


def mask(path, bg=None):
    """Silhouette of a render or drawing as a set of cells in its bounding box, scaled to 160x160."""
    im = Image.open(path).convert("RGB")
    bg = bg or im.getpixel((2, 2))
    px = im.load()
    pts = [(x, y) for y in range(im.height) for x in range(im.width) if max(abs(a - b) for a, b in zip(px[x, y], bg)) > 40]
    x0, x1, y0, y1 = min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)
    cut = Image.new("L", (x1 - x0 + 1, y1 - y0 + 1), 0)
    cut.putdata([255 if max(abs(a - b) for a, b in zip(px[x, y], bg)) > 40 else 0
                 for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)])
    return cut.resize((160, 160)).point(lambda v: 255 if v > 127 else 0)


def iou(a, b):
    pa, pb = a.load(), b.load()
    both = either = 0
    for y in range(160):
        for x in range(160):
            both += pa[x, y] > 0 and pb[x, y] > 0
            either += pa[x, y] > 0 or pb[x, y] > 0
    return both / float(either or 1)


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class CartoonLion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch = patch.object(tools, "_attach_captures", lambda r: r)
        cls.patch.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns
        cls.log = []
        cls.seconds = None
        cls.build()

    @classmethod
    def tearDownClass(cls):
        client.stop()
        cls.patch.stop()

    @classmethod
    def batch(cls, steps, **kw):
        out = cls.t["forge_batch"](name=SCENE, steps=steps, **kw)
        assert out.get("ok", True) and "error" not in out, out
        cls.log.append(len(json.dumps(out)))
        return out

    @classmethod
    def objects(cls):
        return [n for n in load(SCENE).data["nodes"] if n.get("type") == "object" and not n.get("toon_hull")]

    @classmethod
    def build(cls):
        try:
            cls.t["forge_scene_new"](name=SCENE)
        except Exception:
            forget(SCENE)
            scene = load(SCENE)
            ids = [n["id"] for n in scene.data["nodes"]]
            if ids:
                cls.t["forge_delete"](name=SCENE, ids=ids)
        start = time.perf_counter()
        # 1: the body (it tells where its head and tail tip are)
        out = cls.batch([hq("cartoon_body", {"kind": "lion", "pose": "sit", "height": 60, "name": "Lion"})])
        text = json.dumps(out)
        head, tip = point(text, "head"), point(text, "tail_tip")
        scene = load(SCENE)
        body = cls.objects()[0]
        lo, hi = scene.world_mesh(body).bounds()
        # cartoon_body writes its cm mesh into a metre-scale bridge: it comes out 100x too big until that op is fixed
        shrink = [{"tool": "forge_edit", "id": body["id"], "scale": [0.01, 0.01, 0.01]}] if hi[2] - lo[2] > 1000 else []
        body = body["id"]
        # 2: head ball at the socket, glossy face + ears, leaf mane, tail tuft
        cls.batch(shrink + [
            {"tool": "forge_add", "kind": "sphere", "object_name": "Head", "pos": [head[0], head[1], head[2] - HEAD_R], "params": {"radius": HEAD_R, "seg": 32, "rings": 20}},
            hq("cartoon_face", {"species": "cat", "style": "cute", "ears_forward": True}, id="$%d" % len(shrink)),
            hq("tufts", {"mane": "lion", "length": 13, "width": 9}, id="$%d" % len(shrink)),
            hq("tufts", {"target": "tail", "at": tip}, id=body),
        ])
        # 3: one texture for everything, outline, the front view
        ids = [n["id"] for n in cls.objects() if not n["name"].endswith("_face")]  # the face keeps its glossy eye materials
        out = cls.batch([hq("paint_bake", {"ids": ids, "presets": {"body": "fur_warm", "Default": "mane", "belly": "cream", "paw": "cream", "Muzzle": "cream", "EarInner": "pink"}, "name": "Lion", "size": 2048}),
                         hq("toon", {"ids": ["$0"]})], look={"view": "front", "style": "toon"})
        cls.seconds = time.perf_counter() - start
        cls.front = out.get("path")

    def render(self, view):
        ident = self.objects()[0]["id"]
        out = self.t["forge_batch"](name=SCENE, steps=[{"tool": "forge_edit", "id": ident}], look={"view": view, "style": "toon"})
        self.assertNotIn("look_error", out)
        return out

    def test_renders_and_acceptance(self):
        outs = {}
        for key, view in (("front", "front"), ("three_quarter", "three")):
            out = self.render(view)
            dest = REFS / ("result2_%s.png" % key)
            shutil.copyfile(out["path"], dest)
            outs[key] = dest
        group = self.t["forge_group"](name=SCENE, ids=[n["id"] for n in self.objects() if not n.get("parent")], object_name="Lion")
        self.t["forge_edit"](name=SCENE, id=group["ids"][0], rot=[0, 0, 180])  # forge_render has no back view
        out = self.render("front")
        shutil.copyfile(out["path"], REFS / "result2_back.png")
        self.t["forge_edit"](name=SCENE, id=group["ids"][0], rot=[0, 0, 0])
        self.t["forge_ungroup"](name=SCENE, id=group["ids"][0])
        if (REFS / "front.jpg").is_file():
            score = iou(mask(outs["front"]), mask(REFS / "front.jpg", (255, 255, 255)))
            print("\nfront silhouette IoU %.2f" % score)
            self.assertGreaterEqual(score, 0.5)

    def test_scene_content(self):
        scene = load(SCENE)
        solid = self.objects()
        tris = sum(scene.world_mesh(n).tri_count() for n in solid)
        print("\nCartoonLion v2: %d forge_batch calls, %d s, %dk tris: %s" % (
            len(self.log), self.seconds, tris // 1000, {n["name"]: scene.world_mesh(n).tri_count() for n in solid}))
        self.assertTrue(20000 <= tris <= 45000, "recipe built %d tris, want 20k-45k" % tris)
        names = " ".join([n.get("name", "") for n in solid] + [m.get("name", "") for m in scene.data["materials"]]).lower()
        for part in ("eye", "nose", "ear"):
            self.assertIn(part, names, "face part %s missing in: %s" % (part, names))
        mats = scene.materials()
        lion = next(n for n in solid if n["name"] == "Lion")
        tex = mats[scene.node(lion["id"])["material"]].get("textures") or {}
        self.assertIn("base_color", tex, "the baked paint needs a base colour")

    def test_batches_stay_small(self):
        self.assertTrue(all(n <= 300 for n in self.log[:-1]), self.log)  # the last one carries the look path


class DocCalls(unittest.TestCase):
    """skills/forge/references/stylized.md: every json call parses and names real tools and ops."""

    def test_calls_parse(self):
        from . import hq as hqmod
        from .scene import _BATCH
        doc = Path(__file__).resolve().parent.parent / "skills" / "forge" / "references" / "stylized.md"
        calls = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", doc.read_text(encoding="utf-8"), re.S)]
        self.assertGreaterEqual(len(calls), 1, "the doc shows the call sequence")
        for call in calls:
            if "steps" not in call:  # a single-op example: {"op": ..., "params": ...}
                self.assertIn(call.get("op"), hqmod.HQ_OPS, call)
                continue
            self.assertTrue(call["name"] and call["steps"])
            for i, step in enumerate(call["steps"]):
                self.assertIn(step["tool"], _BATCH, step)
                if step["tool"] == "forge_hq":
                    self.assertIn(step["op"], hqmod.HQ_OPS, step)
                for ref in re.findall(r"\$(\d+)", json.dumps(step)):
                    self.assertLess(int(ref), i, "step %d refers forward: %s" % (i, step))


if __name__ == "__main__":
    unittest.main()
