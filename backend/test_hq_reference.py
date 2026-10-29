"""HQ reference: silhouette hull and colour parts. Runs without Blender: the engine call is replaced by the
same op functions the bpy worker would run (through a JSON round trip, like the wire). Fixtures are PNGs drawn here."""
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from . import hq
from .engine.ops import reference as er
from .hq import reference as hqr
from .scene import dispatch, forget, load

OPS = {"blockout_from_refs": er.op_blockout_from_refs, "ref_parts": er.op_ref_parts}
BLUE = (40, 90, 220)
WHITE, ORANGE = (255, 255, 255), (230, 150, 40)


def direct(name, params, timeout=600.0):
    return json.loads(json.dumps(OPS[name](json.loads(json.dumps(params)))))


def write_png(path, w, h, pixel):
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def inside(x, y, shapes):
    return any(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1 for cx, cy, rx, ry in shapes)


FRONT = [(50, 70, 28, 30), (50, 28, 20, 20)]  # body + head, rows 8..100
SIDE = [(55, 70, 22, 30), (45, 28, 20, 20)]


def draw(path, shapes, w=100, h=110, tint=lambda x, y: ORANGE):
    write_png(path, w, h, lambda x, y: tint(x, y) if inside(x, y, shapes) else WHITE)


def raster(mesh_verts, faces, nx, nz, lo, hi):
    """Orthographic front (XZ) coverage mask of the quads, nz rows tall."""
    sx, sz = (nx - 1) / ((hi[0] - lo[0]) or 1), (nz - 1) / ((hi[2] - lo[2]) or 1)
    cells = set()
    for f in faces:
        for a in range(1, len(f) - 1):
            tri = [(mesh_verts[f[0]], mesh_verts[f[a]], mesh_verts[f[a + 1]])][0]
            pts = [((v[0] - lo[0]) * sx, (v[2] - lo[2]) * sz) for v in tri]
            x0, x1 = int(min(p[0] for p in pts)), int(max(p[0] for p in pts)) + 1
            z0, z1 = int(min(p[1] for p in pts)), int(max(p[1] for p in pts)) + 1
            (ax, az), (bx, bz), (cx, cz) = pts
            den = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
            for i in range(x0, x1 + 1):
                for k in range(z0, z1 + 1):
                    if den:
                        l1 = ((bz - cz) * (i - cx) + (cx - bx) * (k - cz)) / den
                        l2 = ((cz - az) * (i - cx) + (ax - cx) * (k - cz)) / den
                        if min(l1, l2, 1 - l1 - l2) >= -0.5 / max(nx, nz):
                            cells.add((i, k))
    return cells


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-ref-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        self.call = patch.object(hqr, "_call", direct)
        self.call.start()
        dispatch("", "scene_new", {"name": "Ref"})
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.call.stop()
        forget("Ref")
        self.env.stop()
        self.tmp.cleanup()

    def image(self, name, shapes, **kw):
        path = self.dir / name
        draw(path, shapes, **kw)
        return str(path)

    def front_mask(self, path, nx, nz):
        w, h, px = er.load_image(path)
        mask, cw, ch, _ = er.silhouette(w, h, px)
        grid = er.grid_from(mask, cw, ch, nx, nz)
        return {(i, k) for i in range(nx) for k in range(nz) if grid[i][k]}

    def iou(self, scene, result, path):
        mesh = scene.local_mesh(scene.node(result["ids"][0]))
        lo = [min(v[c] for v in mesh.verts) for c in range(3)]
        hi = [max(v[c] for v in mesh.verts) for c in range(3)]
        nx, nz = 80, 100
        got = raster(mesh.verts, mesh.faces, nx, nz, lo, hi)
        want = self.front_mask(path, nx, nz)
        # the fill: rasterised quads are a shell, so close the span between the outermost cells of every row
        rows = {}
        for i, k in got:
            rows.setdefault(k, []).append(i)
        solid = {(i, k) for k, cols in rows.items() for i in range(min(cols), max(cols) + 1)}
        return len(solid & want) / float(len(solid | want))

    def two_color(self):
        front = self.image("two.png", FRONT, tint=lambda x, y: ORANGE if x < 50 else BLUE)
        scene = load("Ref")
        made = hqr.blockout_from_refs(scene, {"front": front, "side": self.image("side.png", SIDE)})
        return scene, made["ids"][0], front

    def test_ops_registered(self):
        for name in ("blockout_from_refs", "ref_parts"):
            self.assertIn(name, hq.HQ_OPS)

    def test_parts_from_two_color_fixture(self):
        scene, nid, front = self.two_color()
        result = hqr.ref_parts(scene, {"id": nid, "front": front, "k": 2})
        self.assertEqual(result["parts"], 2)
        node = scene.node(nid)
        mesh, world = scene.local_mesh(node), scene.world_mesh(node)
        mats = scene.materials()
        self.assertEqual(len({mats[m]["name"] for m in mesh.face_mat}), 2)
        right = ok = 0
        for f, m in zip(world.faces, mesh.face_mat):
            x = sum(world.verts[v][0] for v in f) / len(f)
            if abs(x) < 4:  # the colour edge itself may fall either way
                continue
            red = mats[m]["base_color"][0] > mats[m]["base_color"][2]  # orange material
            ok += red == (x < 0)
            right += 1
        self.assertGreater(right, 100)
        self.assertGreater(ok / float(right), 0.97)
        self.assertLessEqual(len(json.dumps(hq.compact(result))), 300)

    def test_parts_palette_names_and_split(self):
        scene, nid, front = self.two_color()
        palette = [{"name": "Mane", "color": "#e69628"}, {"name": "Eye", "color": [40, 90, 220]}]
        result = hqr.ref_parts(scene, {"id": nid, "front": front, "palette": palette, "split": True})
        objs = [scene.node(i) for i in result["ids"]]
        self.assertEqual(sorted(n["name"].rsplit("_", 1)[1] for n in objs), ["Eye", "Mane"])
        self.assertIsNone(scene.node(nid))  # replaced by its parts
        total = sum(scene.local_mesh(n).tri_count() for n in objs)
        self.assertGreater(total, 500)
        with self.assertRaisesRegex(ValueError, "rrggbb"):
            hqr.ref_parts(scene, {"id": objs[0]["id"], "front": front, "palette": ["orange"]})

    def test_part_names_from_colour(self):
        labs = [er.lab(c) for c in ((20, 15, 10), (255, 250, 240), (240, 160, 190), (220, 150, 40), (150, 90, 30))]
        self.assertEqual(er.name_clusters(labs, [10, 50, 5, 900, 400]), ["Outline", "Muzzle", "Ear", "Body", "Mane"])

    def test_hull_matches_front_silhouette(self):
        front, side = self.image("front.png", FRONT), self.image("side.png", SIDE)
        scene = load("Ref")
        result = hqr.blockout_from_refs(scene, {"front": front, "side": side, "height": 100, "name": "Body"})
        self.assertEqual(result["mode"], "hull")
        self.assertFalse(result["warnings"])
        self.assertGreater(self.iou(scene, result, front), 0.85)
        mesh = scene.local_mesh(scene.node(result["ids"][0]))
        zs = [v[2] for v in mesh.verts]
        self.assertAlmostEqual(max(zs) - min(zs), 100, delta=6)  # height is honoured
        ys = [v[1] for v in mesh.verts]
        self.assertGreater(max(ys) - min(ys), 30)  # the side drawing gave it real depth
        self.assertTrue(all(len(f) == 4 for f in mesh.faces))
        self.assertLessEqual(len(json.dumps(hq.compact(result))), 300)

    def test_hull_is_left_right_symmetric(self):
        front, side = self.image("front.png", FRONT + [(30, 40, 6, 6)]), self.image("side.png", SIDE)  # a lopsided bump
        scene = load("Ref")
        result = hqr.blockout_from_refs(scene, {"front": front, "side": side})
        mesh = scene.local_mesh(scene.node(result["ids"][0]))
        xs = [v[0] for v in mesh.verts]
        self.assertAlmostEqual(min(xs), -max(xs), delta=1.0)

    def test_front_only_inflation(self):
        front = self.image("disc.png", [(50, 50, 40, 40)], w=100, h=100)
        scene = load("Ref")
        result = hqr.blockout_from_refs(scene, {"front": front, "height": 80})
        self.assertEqual(result["mode"], "inflate")
        mesh = scene.local_mesh(scene.node(result["ids"][0]))
        span = [max(v[c] for v in mesh.verts) - min(v[c] for v in mesh.verts) for c in range(3)]
        self.assertAlmostEqual(span[1], span[0], delta=0.25 * span[0])  # a disc inflates to roughly a ball
        centre = [(max(v[c] for v in mesh.verts) + min(v[c] for v in mesh.verts)) / 2 for c in range(3)]
        mid = [v for v in mesh.verts if abs(v[2] - centre[2]) < 4]
        self.assertGreater(max(abs(v[1]) for v in mid), 0.8 * span[0] / 2)  # fullest depth at the middle
        edge = [v for v in mesh.verts if v[2] > max(v[2] for v in mesh.verts) - 3]
        self.assertLess(max(abs(v[1]) for v in edge), 0.5 * span[0] / 2)  # and thin at the rim

    def test_three_quarter_side_uses_rounded_depth(self):
        front, tq = self.image("front.png", FRONT), self.image("tq.png", SIDE)
        scene = load("Ref")
        result = hqr.blockout_from_refs(scene, {"front": front, "side": tq, "mode": "inflate"})  # side is not a true side view
        self.assertEqual(result["mode"], "inflate")
        self.assertGreater(self.iou(scene, result, front), 0.85)

    def test_alpha_and_missing_file(self):
        scene = load("Ref")
        with self.assertRaisesRegex(ValueError, "existing PNG"):
            hqr.blockout_from_refs(scene, {"front": str(self.dir / "nope.png")})
        with self.assertRaisesRegex(ValueError, "front="):
            hqr.blockout_from_refs(scene, {})
        blank = self.image("blank.png", [])
        with self.assertRaisesRegex(ValueError, "No subject"):
            hqr.blockout_from_refs(scene, {"front": blank})

    def test_silhouette_fills_holes_keeps_biggest(self):
        shapes = [(50, 50, 40, 40)]
        path = self.dir / "ring.png"
        write_png(path, 100, 100, lambda x, y: WHITE if (not inside(x, y, shapes) or inside(x, y, [(50, 50, 10, 10)])) and not (x < 6 and y < 6) else ORANGE)
        w, h, px = er.load_image(path)
        mask, cw, ch, _ = er.silhouette(w, h, px)
        self.assertEqual(mask[ch // 2 * cw + cw // 2], 1)  # the hole is filled
        self.assertEqual((cw, ch), (81, 81))  # the speck in the corner is not the subject


if __name__ == "__main__":
    unittest.main()
