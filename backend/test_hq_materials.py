"""HQ materials: layer spec -> nodes, 40 presets, bakes (Blender engine tests skip until it is installed)."""
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

    def test_forty_presets_compile(self):
        presets = hqm.presets()
        self.assertEqual(len(presets), 40)
        self.assertGreaterEqual(len({p["cat"] for p in presets.values()}), 11)
        for name in presets:
            self.assertTrue(graph(hqm.expand(name)["layers"])["out"], name)

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
