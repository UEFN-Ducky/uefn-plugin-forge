"""HQ materials: layer spec -> nodes, 40 presets, bakes (Blender engine tests skip until it is installed)."""
import json
import os
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import hq
from .engine import bridge, runtime
from .engine.ops.materials import graph
from .hq import materials as hqm

_READY = runtime.engine_status()["state"] == "ready"
_ALL = ["base", "noise", "edge_wear", "dirt", {"type": "triplanar", "image": "tile.png"}, "scratches",
        "sss", "clearcoat", {"type": "normal", "image": "n.png"}]


def _pixels(path):
    from PIL import Image
    return list(Image.open(path).convert("RGB").getdata())


class GraphTests(unittest.TestCase):
    def test_graph_compiles_all_layer_types(self):
        g = graph(_ALL)
        seen = {n["id"] for n in g["nodes"]}
        types = {n["type"] for n in g["nodes"]}
        for needed in ("ShaderNodeNewGeometry", "ShaderNodeAmbientOcclusion", "ShaderNodeTexImage", "ShaderNodeNormalMap",
                       "ShaderNodeBump", "ShaderNodeTexNoise", "ShaderNodeMix", "ShaderNodeValToRGB"):
            self.assertIn(needed, types)
        placed = set()
        for node in g["nodes"]:  # every ref points at an earlier node
            for value in node["in"].values():
                if isinstance(value, dict):
                    self.assertIn(value["n"], placed)
            placed.add(node["id"])
        for value in g["out"].values():
            if isinstance(value, dict):
                self.assertIn(value["n"], seen)
        for socket in ("Base Color", "Roughness", "Metallic", "Normal", "Subsurface Weight", "Coat Weight"):
            self.assertIn(socket, g["out"])
        bump = next(n for n in g["nodes"] if n["type"] == "ShaderNodeBump")
        self.assertIn("Normal", bump["in"])  # bump rides on the normal-map layer

    def test_layer_errors_say_the_fix(self):
        with self.assertRaisesRegex(ValueError, "Did you mean 'dirt'"):
            graph(["dirtt"])
        with self.assertRaisesRegex(ValueError, "Params: color"):
            graph([{"type": "base", "colour": [1, 0, 0]}])
        with self.assertRaisesRegex(ValueError, "needs image"):
            graph(["triplanar"])

    def test_worn_painted_steel_is_short(self):
        spec = hqm.expand("worn_steel", {"color": [0.1, 0.3, 0.6]})
        self.assertEqual(spec["layers"][0]["color"], [0.1, 0.3, 0.6])
        self.assertEqual([l["type"] for l in spec["layers"]], ["base", "noise", "scratches", "edge_wear", "dirt"])
        self.assertLess(len(str(hqm.presets()["worn_steel"]["layers"])), 600)
        self.assertEqual(hqm.expand("worn_steel", {"edge_wear": {"amount": 0.9}})["layers"][3]["amount"], 0.9)
        with self.assertRaisesRegex(ValueError, "Did you mean 'worn_steel'"):
            hqm.expand("worn_stel")
        with self.assertRaisesRegex(ValueError, "Override 'colr' unknown"):
            hqm.expand("worn_steel", {"colr": 1})

    def test_presets_compile(self):
        presets = hqm.presets()
        self.assertEqual(len(presets), 46)
        self.assertGreaterEqual(len({p["cat"] for p in presets.values()}), 11)
        for name in presets:
            self.assertTrue(graph(hqm.expand(name)["layers"])["out"], name)

    def test_category_presets_and_lint(self):
        self.assertEqual(hqm.expand("metal")["preset"], "metal")
        self.assertEqual(hqm.expand("wood")["layers"][0]["color"], hqm.presets()["oak"]["color"])
        painted = hqm.expand("painted_metal")
        self.assertEqual([l["type"] for l in painted["layers"]], ["base", "noise", "scratches", "edge_wear", "dirt"])
        self.assertEqual(hqm.lint(hqm.expand("gold")), [])
        self.assertEqual(hqm.lint(hqm.expand("painted_metal")), [])
        self.assertRegex(" ".join(hqm.lint(hqm.expand("gold", {"metallic": 0.5}))), "metallic 0.50")
        self.assertRegex(" ".join(hqm.lint(hqm.expand("iron", {"color": [0.05, 0.05, 0.05]}))), "metal albedo")
        self.assertRegex(" ".join(hqm.lint(hqm.expand("cotton", {"color": [1, 1, 1]}))), "outside 30-240")
        bad = {"layers": [{"type": "base"}, {"type": "image", "image": "rock_nor_gl_2k.png", "target": "color"}]}
        self.assertRegex(" ".join(hqm.lint(bad)), "normal map")

    def test_texture_names(self):
        cases = {"rock_wall_diff_2k": "base_color", "rock_wall_nor_gl_2k": "normal", "rock_wall_arm_2k": "orm",
                 "Bricks059_2K-PNG_Color": "base_color", "Bricks059_2K-PNG_NormalDX": "normal",
                 "Bricks059_2K-PNG_AmbientOcclusion": "ao", "Crate_Roughness": "roughness", "Crate_Metallic": "metallic",
                 "T_Crate_BC": "base_color", "T_Crate_N": "normal", "T_Crate_ORM": "orm", "metal_plate_diff_1k": "base_color",
                 "x_disp_2k": "height", "Crate_Emissive": "emissive", "Crate_Opacity": "opacity"}
        for stem, slot in cases.items():
            self.assertEqual(hqm._slot(stem)[0], slot, stem)
        self.assertEqual(hqm._slot("T_Crate_N")[1], "dx")
        self.assertEqual(hqm._slot("Bricks059_2K-PNG_NormalDX")[1], "dx")
        self.assertEqual(hqm._slot("rock_nor_gl_2k")[1], "gl")

    def test_image_layer_and_dx_normal_compile(self):
        g = graph(["base", {"type": "image", "image": "t.png", "target": "orm"}, {"type": "image", "image": "o.png", "target": "opacity"},
                   {"type": "normal", "image": "n.png", "dx": True}])
        types = [n["type"] for n in g["nodes"]]
        self.assertIn("ShaderNodeCombineColor", types)
        self.assertIsInstance(g["out"]["Roughness"], dict)
        self.assertIsInstance(g["out"]["Alpha"], dict)
        with self.assertRaisesRegex(ValueError, "image target"):
            graph([{"type": "image", "image": "t.png", "target": "bump"}])

    def test_wet_and_snow_layers(self):
        wet = graph([{"type": "base", "roughness": 0.7}, {"type": "wet", "amount": 1.0, "porosity": 1.0}])
        self.assertEqual(wet["out"]["Coat Weight"], 1.0)
        mix = next(n for n in wet["nodes"] if n["type"] == "ShaderNodeMix")
        self.assertAlmostEqual(mix["in"]["#0"], 0.55)  # albedo darkens 55 % at full porosity
        snow = graph(["base", {"type": "snow", "up": 0.7}])
        self.assertIn("ShaderNodeSeparateXYZ", [n["type"] for n in snow["nodes"]])
        self.assertIsInstance(snow["out"]["Base Color"], dict)

    def test_registered(self):
        self.assertIn("material", hq.HQ_OPS)
        self.assertIn("bake", hq.HQ_OPS)
        self.assertNotIn("materials", hq._BROKEN)


class SceneCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-mat-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        from .scene import forget
        forget("Hull")
        self.env.stop()
        self.tmp.cleanup()

    def scene(self, kind="box", **params):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Hull"})
        nid = dispatch("Hull", "add", {"kind": kind, "params": params})["id"]
        return load("Hull"), nid

    def test_material_persist(self):
        from .scene import forget, load
        scene, nid = self.scene()
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "worn_steel", "id": nid})
        mat = next(m for m in scene.data["materials"] if m["name"] == "Hull")
        mat["textures"] = {"normal": "T_Hull_N.png", "orm": "T_Hull_ORM.png"}
        scene.save()
        forget("Hull")
        back = next(m for m in load("Hull").data["materials"] if m["name"] == "Hull")
        self.assertEqual(back["textures"], {"normal": "T_Hull_N.png", "orm": "T_Hull_ORM.png"})
        self.assertEqual(back["spec"]["preset"], "worn_steel")
        self.assertEqual(load("Hull").node(nid)["material"], mat["id"])

    def test_texture_folder_wires_and_packs(self):
        from PIL import Image
        scene, nid = self.scene()
        src = Path(self.tmp.name) / "set"
        src.mkdir()
        Image.new("RGB", (16, 16), (200, 100, 50)).save(src / "rock_diff_1k.png")
        Image.new("RGB", (16, 16), (128, 200, 255)).save(src / "rock_nor_gl_1k.png")
        Image.new("L", (16, 16), 180).save(src / "rock_rough_1k.png")
        Image.new("L", (16, 16), 90).save(src / "rock_ao_1k.png")
        Image.new("L", (16, 16), 7).save(src / "rock_disp_1k.png")
        r = hq.HQ_OPS["material"](scene, {"material": "Rock", "preset": "granite", "textures": str(src), "id": nid})
        mat = next(m for m in scene.data["materials"] if m["name"] == "Rock")
        self.assertEqual(set(mat["textures"]), {"base_color", "normal", "orm"})
        tex = scene.folder / "textures"
        self.assertEqual(Image.open(tex / mat["textures"]["orm"]).getpixel((3, 3)), (90, 180, 0))
        self.assertEqual(Image.open(tex / mat["textures"]["normal"]).getpixel((3, 3))[1], 55)  # GL -> DX for ue
        self.assertIn("skipped height", " ".join(r["warnings"]))
        self.assertEqual([l["type"] for l in mat["spec"]["layers"]][:4], ["base", "image", "normal", "image"])
        self.assertLessEqual(len(json.dumps(hq.compact(r))), 300)


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class BakeTests(SceneCase):
    def bake(self, scene, nid, **p):
        return hq.HQ_OPS["bake"](scene, {"id": nid, **p})

    def test_graph_builds_in_blender(self):
        scene, nid = self.scene()
        tex = scene.folder / "textures"
        tex.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        for name in ("tile.png", "n.png"):
            Image.new("RGB", (8, 8), (128, 128, 255)).save(tex / name)
        hq.HQ_OPS["material"](scene, {"material": "All", "layers": _ALL, "id": nid})
        result, _ = bridge.run(scene, "material_check", [nid], {"textures_dir": str(tex)})
        self.assertTrue(all(count > 10 for count in result["built"].values()), result)

    def test_orm_channels(self):
        scene, nid = self.scene()
        hq.HQ_OPS["material"](scene, {"material": "Hull", "layers": [{"type": "base", "color": [0.5, 0.5, 0.5], "metallic": 1.0, "roughness": 0.3}], "id": nid})
        r = self.bake(scene, nid, size=128, maps=["orm"])
        orm = scene.folder / "textures" / "T_Hull_ORM.png"
        self.assertTrue(orm.is_file(), r)
        covered = [px for px in _pixels(orm) if px[2] > 128]
        self.assertGreater(len(covered), 128 * 128 * 0.3)
        self.assertAlmostEqual(statistics.mean(px[1] for px in covered), 0.3 * 255, delta=10)
        self.assertGreater(statistics.mean(px[0] for px in covered), 200)
        self.assertGreater(statistics.mean(px[2] for px in covered), 245)
        self.assertEqual(next(m for m in scene.data["materials"] if m["name"] == "Hull")["textures"]["orm"], "T_Hull_ORM.png")

    def test_bake_normal_from_high(self):
        scene, nid = self.scene("box", w=100, d=100, h=100)
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "plastic_white", "id": nid})
        self.bake(scene, nid, size=128, maps=["normal"], target="gl")
        path = scene.folder / "textures" / "T_Hull_N.png"
        flat = [px[0] for px in _pixels(path) if px[2] > 128]
        flat_spread = statistics.pstdev(flat)
        self.assertLess(flat_spread, 8)
        from .scene import dispatch, load
        high = dispatch("Hull", "add", {"kind": "sphere", "params": {"radius": 52}})["id"]
        scene = load("Hull")
        self.bake(scene, nid, size=128, maps=["normal"], target="gl", from_high=high)
        bumpy = [px[0] for px in _pixels(path) if px[2] > 128]
        self.assertGreater(statistics.pstdev(bumpy), flat_spread * 3)
        dx = [px[1] for px in _pixels(path) if px[2] > 128]
        self.bake(scene, nid, size=128, maps=["normal"], target="ue", from_high=high)
        flipped = [px[1] for px in _pixels(path) if px[2] > 128]
        self.assertAlmostEqual(statistics.mean(dx) + statistics.mean(flipped), 255, delta=6)

    def test_extra_maps_not_flat(self):
        from .scene import dispatch, load
        scene, nid = self.scene("box", w=100, d=100, h=100)
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "plastic_white", "id": nid})
        high = dispatch("Hull", "add", {"kind": "sphere", "params": {"radius": 105}})["id"]  # a faceted sphere still clear of the corners (0.87 m)
        scene = load("Hull")
        hq.HQ_OPS["uv"](scene, {"id": nid, "method": "smart", "texel": 0, "res": 128})  # box UVs stack every face on 0-1
        r = self.bake(scene, nid, size=128, maps=["ao", "curvature", "id", "thickness", "normal"], from_high=high, extrusion=0.4)
        self.assertLessEqual(len(json.dumps(hq.compact(r))), 300, r)
        tex = next(m for m in scene.data["materials"] if m["name"] == "Hull")["textures"]
        for slot in ("ao", "curvature", "thickness", "normal"):
            px = [p[0] for p in _pixels(scene.folder / "textures" / tex[slot])]
            self.assertGreater(statistics.pstdev(px), 2, slot)
        self.assertIsNotNone(r["stats"]["miss_pct"])
        self.assertLess(r["stats"]["miss_pct"], 3, r)
        self.assertFalse(r["warnings"], r)
        flat = scene.folder / "textures" / tex["id"]
        self.assertGreater(len(set(_pixels(flat))), 1)

    def test_short_ray_misses(self):
        from .scene import dispatch, load
        scene, nid = self.scene("box", w=100, d=100, h=100)
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "plastic_white", "id": nid})
        high = dispatch("Hull", "add", {"kind": "sphere", "params": {"radius": 40}})["id"]
        scene = load("Hull")
        r = self.bake(scene, nid, size=64, maps=["normal"], from_high=high, extrusion=0.01, ray=0.02)
        self.assertGreater(r["stats"]["miss_pct"], 20, r)
        self.assertTrue(r["warnings"], r)

    def test_bake_to_vertex_colours(self):
        from .scene import load
        scene, nid = self.scene("sphere", radius=50)
        hq.HQ_OPS["material"](scene, {"material": "Hull", "layers": [{"type": "base", "color": [0.2, 0.4, 0.8]}], "id": nid})
        r = self.bake(scene, nid, to="vcol", maps=["base_color"])
        self.assertIn("bake_base_color", r["summary"])
        cols = load("Hull").base_mesh(load("Hull").node(nid)).attrs["col:bake_base_color"]
        self.assertAlmostEqual(statistics.mean(c[2] for c in cols), 0.8, delta=0.05)
        with self.assertRaisesRegex(ValueError, "vertex-colour map"):
            self.bake(scene, nid, to="vcol", maps=["normal"])

    def test_bake_lightmap(self):
        scene, nid = self.scene("box", w=100, d=100, h=100)
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "plastic_white", "id": nid})
        hq.HQ_OPS["uv"](scene, {"id": nid, "method": "smart", "lightmap": True, "res": 128})
        r = self.bake(scene, nid, size=128, to="lightmap")
        self.assertFalse(r["warnings"], r)
        self.assertIn("_LM.png", r["summary"])
        lit = [px[0] for px in _pixels(r["path"])]
        self.assertGreater(statistics.pstdev(lit), 5, r)  # sun side and shade side differ
        self.assertLessEqual(len(json.dumps(hq.compact(r))), 300, r)

    def test_bake_normal_from_multires(self):
        scene, nid = self.scene("box", w=100, d=100, h=100)
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "plastic_white", "id": nid})
        hq.HQ_OPS["uv"](scene, {"id": nid, "method": "smart", "texel": 0, "res": 128})
        with self.assertRaisesRegex(ValueError, "run sculpt_hd"):
            self.bake(scene, nid, size=64, source="multires")
        hq.HQ_OPS["sculpt_hd"](scene, {"id": nid, "levels": 3, "detail": "rock"})
        r = self.bake(scene, nid, size=128, source="multires", target="gl")
        self.assertIn("3 multires levels", r["summary"])
        bumpy = [px[0] for px in _pixels(scene.folder / "textures" / "T_Hull_N.png") if px[2] > 128]
        self.assertGreater(statistics.pstdev(bumpy), 4, r)

    def test_bake_speed(self):
        import time
        scene, nid = self.scene()
        hq.HQ_OPS["material"](scene, {"material": "Hull", "preset": "worn_steel", "id": nid})
        self.bake(scene, nid, size=64)  # warm worker
        start = time.perf_counter()
        self.bake(scene, nid, size=64, maps=["base_color", "normal", "orm"])
        small = time.perf_counter() - start
        self.assertLess(small, 5, "64 px bake took %.1fs" % small)
        start = time.perf_counter()
        self.bake(scene, nid, size=512, maps=["base_color", "normal", "orm"])
        self.assertLess(time.perf_counter() - start, 15)

    def test_every_preset_bakes(self):
        scene, nid = self.scene()
        for name in hqm.presets():
            hq.HQ_OPS["material"](scene, {"material": name, "preset": name, "id": nid})
            self.bake(scene, nid, size=256)
            mat = next(m for m in scene.data["materials"] if m["name"] == name)
            for slot in ("base_color", "normal", "orm"):
                self.assertTrue((scene.folder / "textures" / mat["textures"][slot]).is_file(), (name, slot))


if __name__ == "__main__":
    unittest.main()
