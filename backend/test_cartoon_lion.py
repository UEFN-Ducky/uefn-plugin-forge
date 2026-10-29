"""Acceptance: the cartoon lion built from three reference drawings with the stylized toolkit, through forge_batch.

Writes the scene CartoonLion into the real scenes dir and the renders to LimoRide/.ducky/refs/cartoon_lion/result_*.png.
Needs the Blender engine and the reference images; skips without them. Run: py -3 -m backend.test_cartoon_lion
"""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from . import tools
from .engine import client, runtime
from .scene import forget, load

REFS = Path(os.environ.get("LION_REFS", "C:/Users/tas13/Documents/Fortnite Projects/LimoRide/.ducky/refs/cartoon_lion"))
SCENE = "CartoonLion"
_READY = runtime.engine_status()["state"] == "ready" and (REFS / "front.jpg").is_file()
S = 0.1176  # cm per reference pixel (2000 px wide display space): the lion is ~100 cm tall

# Regions of the 2000-px-wide reference, (x0, y0, x1, y1). The face disc is the head blockout; the tufts grow the mane around it.
FACE = (930, 470, 182, 170)  # centre x, y, radii
HEAD_FRONT, HEAD_BACK = (620, 135, 1275, 790), (720, 130, 1295, 720)
BODY_FRONT, BODY_BACK = (780, 772, 1228, 995), (780, 760, 1230, 995)
TAIL = [(1225, 905), (1285, 888), (1310, 805), (1260, 705), (1240, 645), (1272, 600), (1325, 565)]  # px along the S curve, base to tuft
GROUND, BODY_X = 992, 1004
MANE_BACK = 7  # cm the mane host sits behind the face ball


def world(px, py, y=0.0):
    return [round((px - BODY_X) * S, 2), y, round((GROUND - py) * S, 2)]


def tail_beads(step=2.0):
    """Overlapping spheres every `step` cm along the S curve, thinning toward the tip: (pos, radius)."""
    pts = [world(*p, y=16) for p in TAIL[:-1]]
    beads = []
    for a, b in zip(pts, pts[1:]):
        n = max(1, round(sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 / step))
        beads += [[a[i] + (b[i] - a[i]) * k / n for i in range(3)] for k in range(n)]
    return [(p, round(2.8 - 1.0 * i / len(beads), 2)) for i, p in enumerate(beads)]


def crops(folder: Path) -> dict:
    """Plain images cut from the references: the face disc on white, and each view of the head and body."""
    front, back = Image.open(REFS / "front.jpg").convert("RGB"), Image.open(REFS / "back.jpg").convert("RGB")
    k = front.width / 2000.0
    box = lambda r: tuple(int(c * k) for c in r)
    out = {}
    cx, cy, rx, ry = FACE
    disc = Image.new("L", front.size, 0)
    ImageDraw.Draw(disc).ellipse([int(c * k) for c in (cx - rx, cy - ry, cx + rx, cy + ry)], fill=255)
    face = Image.composite(front, Image.new("RGB", front.size, "white"), disc)
    for name, img in (("face", face.crop(box((cx - rx, cy - ry, cx + rx, cy + ry)))),
                      ("head_front", front.crop(box(HEAD_FRONT))), ("head_back", back.crop(box(HEAD_BACK))),
                      ("body_front", front.crop(box(BODY_FRONT))), ("body_back", back.crop(box(BODY_BACK)))):
        img.thumbnail((1024, 1024))
        out[name] = str(folder / (name + ".png"))
        img.save(out[name])
    return out


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


def components(mesh):
    parent = list(range(len(mesh.verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for face in mesh.faces:
        for v in face[1:]:
            parent[find(v)] = find(face[0])
    return len({find(v) for f in mesh.faces for v in f})


@unittest.skipUnless(_READY, "Blender engine or the cartoon_lion references are missing")
class CartoonLion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="forge-lion-"))
        cls.patch = patch.object(tools, "_attach_captures", lambda r: r)
        cls.patch.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns
        cls.log = []
        cls.build()

    @classmethod
    def tearDownClass(cls):
        client.stop()
        cls.patch.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def batch(cls, steps, **kw):
        out = cls.t["forge_batch"](name=SCENE, steps=steps, **kw)
        assert out.get("ok", True) and "error" not in out, out
        cls.log.append(len(json.dumps(out)))
        return out

    @classmethod
    def build(cls):
        img = crops(cls.tmp)
        try:
            cls.t["forge_scene_new"](name=SCENE)
        except Exception:
            forget(SCENE)
            scene = load(SCENE)
            ids = [n["id"] for n in scene.data["nodes"]]
            if ids:
                cls.t["forge_delete"](name=SCENE, ids=ids)
        beads = tail_beads()
        tail = [{"tool": "forge_add", "kind": "sphere", "object_name": "Tail%d" % i, "pos": p,
                 "params": {"radius": r, "seg": 10, "rings": 6}} for i, (p, r) in enumerate(beads)]
        steps = [
            hq("blockout_from_refs", {"front": img["face"], "height": 2 * FACE[3] * S, "name": "Head", "res": 18, "smooth": 6}),   # $0
            hq("blockout_from_refs", {"front": img["body_front"], "height": (GROUND - BODY_FRONT[1]) * S, "name": "Body", "res": 40, "smooth": 8}),  # $1
            {"tool": "forge_edit", "id": "$0", "pos": world(FACE[0], FACE[1] + FACE[3], -6)},
            {"tool": "forge_edit", "id": "$1", "pos": world(BODY_X, GROUND, 0), "scale": [0.9, 0.6, 1]},  # a lion is not as deep as wide
            {"tool": "forge_duplicate", "id": "$0", "offset": [0, MANE_BACK, 0]},   # $4: the mane host, behind the face ball
            {"tool": "forge_edit", "id": "$4", "object_name": "Mane"},
            *tail,                                                   # $6..
            {"tool": "forge_join", "ids": ["$%d" % (6 + i) for i in range(len(beads))], "weld": True},
        ]
        cls.batch(steps)
        scene = load(SCENE)
        by = {n["name"]: n["id"] for n in scene.data["nodes"] if n.get("type") == "object"}
        cls.head, cls.body, cls.mane = by["Head"], by["Body"], by["Mane"]
        cls.tail = next(i for n, i in by.items() if n.startswith("Tail"))
        # The face goes on the untouched ball; the voxel remesh of tufts would bury its 0.1 cm parts, so the mane grows on the copy.
        cls.batch([
            hq("retopo", {"faces": 2800}, id=cls.body),
            hq("ref_parts", {"front": img["face"], "k": 5}, id=cls.head),
            hq("ref_parts", {"front": img["body_front"], "k": 4}, id=cls.body),
            hq("cartoon_face", {"species": "cat", "style": "cute", "eye_size": 0.2}, id=cls.head),
            hq("tufts", {"region": cls.mane_regions()[:2], "length": 22, "width": 12, "count": 190, "layers": 2, "sweep": 0.4,
                         "budget": 3800, "curl": 0.3, "smooth": 6}, id=cls.mane),
            hq("tufts", {"region": cls.mane_regions()[2:], "length": 21, "width": 12, "count": 60, "layers": 2, "sweep": 0.7,
                         "budget": 4300, "curl": 0.2, "seed": 3}, id=cls.mane),
            hq("tufts", {"region": {"sphere": [round(c, 2) for c in tail_beads()[-1][0]] + [4.5]}, "length": 6, "width": 4, "count": 50,
                         "layers": 2, "budget": 1800}, id=cls.tail),
            *[{"tool": "forge_uv_project", "mode": "smart", "id": i} for i in (cls.head, cls.mane, cls.body, cls.tail)],
            hq("paint_strokes", {"base": [0.98, 0.72, 0.27], "shadow": [0.8, 0.5, 0.15], "highlight": [1.0, 0.88, 0.5], "flow": "normal", "size": 1024}, id=cls.head),
            hq("project_ref", {"images": {"front": img["head_front"], "back": img["head_back"]}, "size": 1024}, id=cls.mane),
            hq("project_ref", {"images": {"front": img["body_front"], "back": img["body_back"]}, "size": 1024}, id=cls.body),
            hq("paint_strokes", {"base": [0.95, 0.65, 0.2], "shadow": [0.7, 0.4, 0.1], "highlight": [1.0, 0.85, 0.4], "flow": "down", "size": 512}, id=cls.tail),
            hq("toon", {"ids": [cls.head, cls.mane, cls.body, cls.tail]}),
        ])

    @classmethod
    def mane_regions(cls):
        """Rim ring round the face disc, the back of the head and a chest bib, in world cm (on the mane host)."""
        scene = load(SCENE)
        lo, hi = scene.world_mesh(scene.node(cls.mane)).bounds()
        x, y, z = [round((a + b) / 2, 2) for a, b in zip(lo, hi)]
        r = FACE[2] * S
        return [{"ring": [x, y, z, 0.7 * r, 0.45 * r]}, {"sphere": [x, y + 0.7 * r, z, 0.95 * r]},
                {"sphere": [x, y - 0.1 * r, z - 0.75 * r, 0.7 * r]}]

    def render(self, view, style="toon"):
        out = self.t["forge_batch"](name=SCENE, steps=[{"tool": "forge_edit", "id": self.head}], look={"view": view, "style": style})
        self.assertNotIn("look_error", out)
        return out

    def test_renders_and_acceptance(self):
        nodes = load(SCENE).data["nodes"]
        outs = {}
        for key, view in (("front", "front"), ("three_quarter", "three")):
            out = self.render(view)
            dest = REFS / ("result_%s.png" % key)
            shutil.copyfile(out["path"], dest)
            outs[key] = dest
        group = self.t["forge_group"](name=SCENE, ids=[n["id"] for n in nodes if n.get("type") == "object" and not n.get("parent")], object_name="Lion")
        self.t["forge_edit"](name=SCENE, id=group["ids"][0], rot=[0, 0, 180])
        out = self.render("front")
        shutil.copyfile(out["path"], REFS / "result_back.png")
        self.t["forge_edit"](name=SCENE, id=group["ids"][0], rot=[0, 0, 0])
        self.t["forge_ungroup"](name=SCENE, id=group["ids"][0])
        want = mask(REFS / "front.jpg", (255, 255, 255))
        score = iou(mask(outs["front"]), want)
        print("\nfront silhouette IoU %.2f" % score)
        self.assertGreaterEqual(score, 0.8)

    def test_scene_content(self):
        scene = load(SCENE)
        objs = [n for n in scene.data["nodes"] if n.get("type") == "object"]
        solid = [n for n in objs if not n.get("toon_hull")]
        tris = sum(scene.world_mesh(n).tri_count() for n in solid)
        print("\n%d tris: %s" % (tris, {n["name"]: scene.world_mesh(n).tri_count() for n in solid}))
        self.assertLessEqual(tris, 20000)
        self.assertEqual(components(scene.local_mesh(scene.node(self.mane))), 1, "the mane is one connected mass")
        names = " ".join([n.get("name", "") for n in objs] + [m.get("name", "") for m in scene.data["materials"]]).lower()
        for part in ("eye", "brow", "nose", "mouth", "ear", "muzzle"):
            self.assertIn(part, names, "face part %s missing in: %s" % (part, names))
        mats = scene.materials()
        for ident in (self.head, self.mane, self.body, self.tail):
            tex = mats[scene.node(ident)["material"]].get("textures") or {}
            self.assertIn("base_color", tex, "%s needs a painted base colour" % ident)
            path = Path(next(iter(tex["base_color"].values())) if isinstance(tex["base_color"], dict) else tex["base_color"])
            im = Image.open(path if path.is_absolute() else scene.folder / "textures" / path.name).convert("RGB").resize((64, 64))
            self.assertGreater(len(set(im.getdata())), 200, "%s texture is flat" % ident)

    def test_batches_stay_small(self):
        self.assertLessEqual(self.log[-1], 300, self.log)  # the op batch; the first one lists a dozen tail sphere ids


class DocCalls(unittest.TestCase):
    """skills/forge/references/stylized.md: every json call parses and names real tools and ops."""

    def test_calls_parse(self):
        from . import hq as hqmod
        from .scene import _BATCH
        doc = Path(__file__).resolve().parent.parent / "skills" / "forge" / "references" / "stylized.md"
        calls = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", doc.read_text(encoding="utf-8"), re.S)]
        self.assertEqual(len(calls), 2, "the doc shows the 2-call sequence")
        for call in calls:
            self.assertTrue(call["name"] and call["steps"])
            for i, step in enumerate(call["steps"]):
                self.assertIn(step["tool"], _BATCH, step)
                if step["tool"] == "forge_hq":
                    self.assertIn(step["op"], hqmod.HQ_OPS, step)
                for ref in re.findall(r"\$(\d+)", json.dumps(step)):
                    self.assertLess(int(ref), i, "step %d refers forward: %s" % (i, step))


if __name__ == "__main__":
    unittest.main()
