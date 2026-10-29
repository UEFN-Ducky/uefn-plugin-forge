"""HQ groom: strand density, card budget, cloth bake, fur shells. Runs without Blender: the engine call is
replaced by the same op functions the bpy worker would run (through a JSON round trip, like the wire)."""
import json
import math
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from . import hq, rig as rigs
from .engine.ops import cloth as ec, groom as eg
from .hq import groom as hqg
from .kernel.mesh import Mesh
from .scene import dispatch, forget, load

OPS = {"hair": eg.op_hair, "hair_cards": eg.op_hair_cards, "cloth": ec.op_cloth, "cloth_fabric": eg.op_cloth_fabric, "fur_shells": eg.op_fur_shells, "fur_bake": eg.op_fur_bake}


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

    def test_cloth_fabric_presets(self):
        body = self.add("capsule", radius=25, height=180)
        scene = load("Groom")
        denim = hqg.cloth(scene, {"preset": "denim", "pin": "shoulders", "collide": body, "frames": 8})
        self.assertEqual(denim["fabric"], "denim")
        self.assertIn("cape denim", denim["summary"])
        self.assertEqual(scene.local_mesh(scene.node(denim["ids"][0])).tri_count() > 0, True)
        silk = hqg.cloth(scene, {"preset": "cape", "fabric": "silk", "pin": "shoulders", "collide": body, "frames": 8})
        self.assertLess(denim["drop"], silk["drop"])  # denim stretches less under its own weight
        drops = {f: eg.simulate_fabric({"preset": "cape", "fabric": f, "pin": [0, 0, 150], "frames": 4})["drop"] for f in eg.FABRICS}
        self.assertLess(drops["leather"], drops["cotton"])
        self.assertLess(drops["cotton"], drops["silk"])
        with self.assertRaisesRegex(ValueError, "Did you mean 'denim'"):
            hqg.cloth(scene, {"fabric": "demin", "pin": "shoulders", "collide": body})
        self.assertLessEqual(len(json.dumps(hq.compact(denim))), 300)

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

    def test_fur_shells_budget(self):
        body = self.add("sphere", radius=40, seg=110, rings=58)
        scene = load("Groom")
        node = scene.node(body)
        mesh = scene.local_mesh(node)
        base = mesh.tri_count()
        body_verts = len(mesh.verts)
        self.assertGreater(base, 11000)
        mesh.attrs["wt:Spine"] = [min(1.0, max(0.0, v[2] / 80 + 0.5)) for v in mesh.verts]
        mesh.attrs["wt:Root"] = [1.0 - w for w in mesh.attrs["wt:Spine"]]
        scene.write_mesh(node, mesh)
        result = hqg.fur_shells(scene, {"id": body})
        mesh = scene.local_mesh(scene.node(body))
        self.assertLessEqual(mesh.tri_count(), 20000)
        self.assertGreaterEqual(result["layers"], 3)
        self.assertTrue(result["warnings"])  # asked for 8, fewer fit
        self.assertIn("20000", result["warnings"][0])
        for key in ("wt:Spine", "wt:Root"):  # shells deform with the rig
            self.assertEqual(len(mesh.attrs[key]), len(mesh.verts))
        spine = mesh.attrs["wt:Spine"]
        self.assertLess(len(spine) - body_verts, 5 * 1500)  # shells sit on the small proxy, not a full body copy each
        self.assertTrue(set(spine[body_verts:]) <= set(spine[:body_verts]))  # weights come from body vertices
        self.assertEqual(len(mesh.uvs), len(mesh.faces))
        tight = hqg.fur_shells(scene, {"id": self.add("sphere", radius=10), "layers": 8, "budget": 100})
        self.assertLess(tight["layers"], 8)
        self.assertLessEqual(len(json.dumps(hq.compact(result))), 300)

    def test_mane_sphere_region(self):
        nid = self.add("sphere", radius=30)
        scene = load("Groom")
        world = scene.world_mesh(scene.node(nid))
        cap = hqg.region_tris(world, {"sphere": [0, 0, 60, 12]})
        self.assertTrue(0 < len(cap) < len(hqg.region_tris(world, "all")))
        self.assertTrue(all(math.dist([sum(p[i] for p in t) / 3 for i in range(3)], [0, 0, 60]) <= 12.5 for t in cap))
        box = hqg.region_tris(world, {"box": [-40, -40, 45, 40, 40, 70]})
        self.assertTrue(box and all(sum(p[2] for p in t) / 3 >= 44 for t in box))
        with self.assertRaisesRegex(ValueError, "sphere"):
            hqg.region_tris(world, {"sphere": [0, 0]})
        result = hqg.hair(scene, {"id": nid, "style": "mane", "region": {"sphere": [0, 0, 60, 12]}})
        self.assertGreater(result["strands"], 200)

    def test_mane_direction(self):
        nid = self.add("sphere", radius=30)
        scene = load("Groom")
        tris = hqg.region_tris(scene.world_mesh(scene.node(nid)), {"sphere": [0, 0, 60, 14]})
        strands = eg.build_strands({"tris": tris, "style": "mane", "density": 8, "seed": 3, "clump": 0, "curl": 0, "frizz": 0})
        crown = [s for s in strands if s[0][2] > 58]
        self.assertGreater(len(crown), 20)
        mean = [sum(s[-1][i] - s[0][i] for s in crown) / len(crown) for i in range(3)]
        self.assertGreater(mean[1], 1.0)  # swept back
        self.assertGreater(mean[2], 1.0)  # lifted
        low = min(s[0][2] for s in strands)
        self.assertLess(sum(s[-1][2] < low for s in strands) / len(strands), 0.2)

    def test_mane_color(self):
        nid = self.add("sphere", radius=30)
        scene = load("Groom")
        color = [0.33, 0.19, 0.08]
        result = hqg.hair(scene, {"id": nid, "style": "mane", "region": {"sphere": [0, 0, 60, 14]}, "color": color, "density": 4})
        mat = scene.materials()[scene.data["grooms"][0]["material"]]
        for got, want in zip(mat["base_color"], color):
            self.assertAlmostEqual(got, want, delta=0.15 * want)
        self.assertTrue(result["strands"])

    def test_mane_cards_budget(self):
        nid = self.add("sphere", radius=30)
        scene = load("Groom")
        hqg.hair(scene, {"id": nid, "style": "mane", "region": {"sphere": [0, 0, 60, 14]}, "color": "auburn"})
        result = hqg.hair_cards(scene, {"id": nid})
        self.assertLessEqual(result["tris"], 4000)
        self.assertGreater(result["cards"], 100)
        mesh = scene.local_mesh(scene.node(result["ids"][0]))
        self.assertEqual(mesh.tri_count(), result["tris"])
        self.assertFalse(result["warnings"])
    def test_decimated_shells_stay_close(self):
        body = self.add("sphere", radius=40, seg=110, rings=58)
        scene = load("Groom")
        skin = scene.local_mesh(scene.node(body))
        skin.verts = [[v[0], v[1], v[2] * 2.0] for v in skin.verts]  # a long body, so radial offsets would be wrong
        scene.write_mesh(scene.node(body), skin)
        base_tris = skin.tri_count()
        self.assertGreater(base_tris, hqg.PROXY_TRIS)  # the default decimation is in play
        body_verts, length = [list(v) for v in skin.verts], 2.0
        result = hqg.fur_shells(scene, {"id": body, "layers": 4, "length": length})
        mesh = scene.local_mesh(scene.node(body))
        self.assertLess(mesh.tri_count(), base_tris + 4 * hqg.PROXY_TRIS + 1)
        cells = {}
        for v in body_verts:
            cells.setdefault(tuple(int(c // 6) for c in v), []).append(v)
        for v in mesh.verts[len(body_verts):]:
            near = [q for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                    for q in cells.get((int(v[0] // 6) + dx, int(v[1] // 6) + dy, int(v[2] // 6) + dz), [])]
            self.assertTrue(near and min(math.dist(v, q) for q in near) <= length + 1.0, v)
        self.assertEqual(result["layers"], 4)

    def test_fur_budget_counts_whole_asset(self):
        body = self.add("sphere", radius=40, seg=60, rings=30)
        scene = load("Groom")
        cards = Mesh()
        cards.verts = [[0, 0, 0], [1, 0, 0], [1, 1, 0]]
        cards.faces = [[0, 1, 2]] * 4000
        cards.uvs = [[[0, 0], [1, 0], [1, 1]]] * 4000
        cards.face_mat = ["default"] * 4000
        hqg._add_object(scene, "cards", cards)
        base = scene.local_mesh(scene.node(body)).tri_count()
        result = hqg.fur_shells(scene, {"id": body, "layers": 8})
        total = sum(scene.local_mesh(n).tri_count() for n in scene.data["nodes"] if n.get("type") == "object")
        self.assertLessEqual(total, 20000)
        self.assertGreaterEqual(result["layers"], 1)
        self.assertTrue(result["warnings"] or result["layers"] == 8)
        full = hqg.fur_shells(scene, {"id": self.add("sphere", radius=10), "budget": 4000 + base})
        self.assertEqual(full["layers"], 0)  # cards + that body already fill the budget
        self.assertIn("asset already uses", full["summary"])

    def test_mane_ring_coverage(self):
        nid = self.add("sphere", radius=30)
        scene = load("Groom")
        lo_z, hi_z = 10.0, 50.0
        hqg.hair(scene, {"id": nid, "style": "mane", "region": {"box": [-40, -40, lo_z, 40, 40, hi_z]}, "color": "auburn"})
        result = hqg.hair_cards(scene, {"id": nid})
        mesh = scene.local_mesh(scene.node(result["ids"][0]))
        self.assertLessEqual(result["tris"], 4000)
        hit = set()
        for x, y, z in mesh.verts:
            if lo_z <= z < hi_z:
                hit.add((int((math.atan2(y, x) + math.pi) / (2 * math.pi) * 16) % 16, int((z - lo_z) / (hi_z - lo_z) * 3)))
        self.assertGreaterEqual(len(hit) / 48.0, 0.7)
        # layered: inner cards share an atlas with darker even cells
        mat = scene.materials()[mesh.face_mat[0]]
        self.assertTrue(mat["textures"]["base_color"].endswith("_color.png"))

    def test_lion_coat_gradient(self):
        nid = self.add("sphere", radius=10)
        scene = load("Groom")
        size = 128
        skin = scene.local_mesh(scene.node(nid))  # the sphere primitive tiles UVs per face: give it a lon/lat unwrap
        cz = (min(v[2] for v in skin.verts) + max(v[2] for v in skin.verts)) / 2
        skin.uvs = []
        for f in skin.faces:
            uv = [[math.atan2(skin.verts[i][1], skin.verts[i][0]) / (2 * math.pi) % 1.0, (skin.verts[i][2] - cz) / 20 + 0.5] for i in f]
            if max(c[0] for c in uv) - min(c[0] for c in uv) > 0.5:
                uv = [[c[0] + (1.0 if c[0] < 0.5 else 0.0) - 1e-3, c[1]] for c in uv]
            skin.uvs.append(uv)
        scene.write_mesh(scene.node(nid), skin)
        hqg.fur_bake(scene, {"id": nid, "size": size, "coat": "lion"})
        mat = next(m for m in scene.data["materials"] if m.get("textures", {}).get("normal"))
        raw = (Path(scene.folder) / "textures" / mat["textures"]["base_color"]).read_bytes()
        idat = raw[raw.index(b"IDAT") + 4:raw.index(b"IEND") - 8]
        pixels = zlib.decompress(idat)
        stride = 1 + size * 3
        mesh = scene.local_mesh(scene.node(nid))
        zs = [sum(mesh.verts[i][2] for i in f) / len(f) for f in mesh.faces]
        lo, hi = min(zs), max(zs)

        def luma(face):
            u = sum(c[0] for c in mesh.uvs[face]) / len(mesh.uvs[face])
            v = sum(c[1] for c in mesh.uvs[face]) / len(mesh.uvs[face])
            at = int((1 - v) * (size - 1)) * stride + 1 + int(min(u, 1.0) * (size - 1)) * 3
            return sum(pixels[at:at + 3])

        top = [luma(i) for i, z in enumerate(zs) if z > hi - 0.2 * (hi - lo)]
        bottom = [luma(i) for i, z in enumerate(zs) if z < lo + 0.2 * (hi - lo)]
        self.assertLess(sum(top) / len(top), sum(bottom) / len(bottom) * 0.85)  # tawny back, cream belly
        self.assertEqual(mat["base_color"][:3], list(eg.COATS["lion"]["back"]))
        with self.assertRaisesRegex(ValueError, "Did you mean 'lion'"):
            hqg.fur_bake(scene, {"id": nid, "size": 64, "coat": "lon"})

    def test_fur_bake_writes_maps(self):
        nid = self.add("sphere", radius=10)
        scene = load("Groom")
        result = hqg.fur_bake(scene, {"id": nid, "size": 128})
        mat = next(m for m in scene.data["materials"] if m.get("textures", {}).get("normal"))
        self.assertEqual(mat["textures"]["normal"], "T_%s_N.png" % (scene.node(nid).get("name") or nid))
        self.assertEqual(len(result["textures"]), 2)
        import zlib as z
        raw = (Path(scene.folder) / "textures" / mat["textures"]["normal"]).read_bytes()
        self.assertEqual(struct.unpack(">II", raw[16:24]), (128, 128))
        idat = raw[raw.index(b"IDAT") + 4:raw.index(b"IEND") - 8]
        pixels = z.decompress(idat)
        self.assertGreater(len(set(pixels[::7])), 8)  # not a flat (128,128,255) map


if __name__ == "__main__":
    unittest.main()
