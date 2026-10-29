"""Every weapon kind through forge_batch: length, sockets, grip origin, tris, qa(uefn) score, 300-char result.

Needs the Blender engine (runtime.install()); the param checks run without it. Run: py -3 -m backend.test_hq_weapon
"""
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import hq, tools
from .engine import client, runtime
from .hq import qa as hq_qa
from .hq import weapon as hq_weapon
from .scene import forget, load
from .test_hq_e2e import _Api

_READY = runtime.engine_status()["state"] == "ready"
_N = itertools.count()  # unique scene names: forget() keeps the folder, so a reused name fails scene_new


class Params(unittest.TestCase):
    def test_registered(self):
        self.assertIn("weapon", hq.HQ_OPS)
        self.assertTrue(hq.run_hq(None, "help", {"op": "weapon"})["summary"].startswith("weapon("))

    def test_defaults_and_errors(self):
        q = hq_weapon.params({"kind": "rifle"})
        self.assertEqual((q["length"], q["style"]), (92.0, "realistic"))
        for bad, hint in (({"kind": "rifel"}, "rifle"), ({"kind": "sword", "parts": {"stock": "ar"}}, "guns"),
                          ({"kind": "rifle", "parts": {"stok": "ar"}}, "stock"), ({"kind": "rifle", "parts": {"sight": "laser"}}, "scope"),
                          ({"kind": "pistol", "parts": {"sight": "scope"}}, "red_dot"), ({"kind": "knife", "length_cm": 200}, "15"),
                          ({"kind": "rifle", "colour": 1}, "Params")):
            with self.assertRaises(ValueError) as err:
                hq_weapon.params(bad)
            self.assertIn(hint, str(err.exception), bad)


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-weapon-")
        cls.patches = [patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")}),
                       patch.object(tools, "_attach_captures", lambda r: r)]
        for p in cls.patches:
            p.start()
        api = _Api()
        tools.register_tools(api)
        cls.t = api.fns

    @classmethod
    def tearDownClass(cls):
        client.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def build(self, params):
        name = "Weapon_%s_%d" % (params["kind"], next(_N))
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        out = self.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": "weapon", "params": params}])
        self.assertTrue(out.get("ok", True), out)
        self.assertLessEqual(len(json.dumps(out)), 300, out)
        scene = load(name)
        node = scene.node(out["ids"][0])
        return out, scene, node

    def check(self, params, length):
        out, scene, node = self.build(params)
        kind = params["kind"]
        lo, hi = scene.world_mesh(node).bounds()
        self.assertLess(abs((hi[0] - lo[0]) - length) / length, 0.05, (kind, lo, hi))
        data = node["hq"]["weapon"]
        want = hq_weapon.SOCKETS["gun" if kind in hq_weapon.GUNS else "melee"]
        self.assertEqual(tuple(sorted(data["sockets"])), want, data)
        self.assertEqual(data["sockets"]["Grip"], [0, 0, 0])
        self.assertEqual([round(v, 3) for v in node["transform"]["pos"]], [0, 0, 0])
        self.assertTrue(all(lo[i] < 0 < hi[i] for i in range(3)), (kind, "grip origin outside the mesh", lo, hi))
        tip = data["sockets"]["Muzzle" if kind in hq_weapon.GUNS else "TrailTip"]
        if kind not in ("axe", "hammer"):  # their trail tip is the head's edge, not the far end
            self.assertLess(abs(tip[0] - hi[0]), 1.5, (kind, tip, hi))
        tris = scene.world_mesh(node).tri_count()
        self.assertTrue(300 <= tris <= 15000, (kind, tris))
        qa = hq_qa.qa(scene, {"id": node["id"], "profile": "uefn", "budget": "weapon"})
        self.assertGreaterEqual(qa["score"], 85, qa["summary"])
        print("ok", kind, params.get("style", "realistic"), out["summary"][:120], "| qa", qa["score"])

    def test_every_kind(self):
        for kind, length in hq_weapon.LENGTHS.items():
            with self.subTest(kind=kind):
                self.check({"kind": kind}, length)

    def test_stylized_parts_and_length(self):
        self.check({"kind": "rifle", "style": "stylized", "length_cm": 80, "seed": 3,
                    "parts": {"stock": "none", "sight": "scope", "muzzle": "suppressor", "mag": "drum"}}, 80)
        self.check({"kind": "smg", "parts": {"sight": "holo", "muzzle": "brake", "mag": "extended", "stock": "ar"}}, 68)
        self.check({"kind": "pistol", "parts": {"sight": "red_dot", "muzzle": "suppressor"}}, 20.2)
        self.check({"kind": "sword", "style": "stylized", "seed": 2}, 100)

    def test_doc_wear_keeps_slots(self):
        """The weapons.md wear block (edit slots by name, no id) leaves the rifle's 3 slots and the metal colour alone."""
        import re
        text = (Path(__file__).resolve().parent.parent / "skills" / "forge" / "references" / "weapons.md").read_text(encoding="utf-8")
        wear = next(json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S) if "edge_wear" in b)
        out, scene, node = self.build({"kind": "rifle"})
        before = set(scene.world_mesh(node).face_mat)
        got = self.t["forge_batch"](name=scene.name, steps=wear)
        self.assertTrue(got.get("ok", True), got)
        scene = load(scene.name)
        after = set(scene.world_mesh(scene.node(node["id"])).face_mat)
        self.assertEqual((len(before), after), (3, before))
        first = next(m for m in scene.data["materials"] if m["name"] == wear[0]["params"]["material"])
        self.assertLess(first["base_color"][0], 0.1, first)

    def test_rifle_detail(self):
        """The detail pass: >= 25 parts, 3 finishes (anodised receiver, parkerised steel, polymer) with wear layers."""
        out, scene, node = self.build({"kind": "rifle"})
        import re
        self.assertGreaterEqual(int(re.search(r"(\d+) parts", out["summary"]).group(1)), 25, out)
        used = {m["name"] for m in scene.data["materials"] if m["id"] in set(scene.world_mesh(node).face_mat)}
        self.assertEqual(used, {"Weapon_Anodized_Realistic", "Weapon_Metal_Realistic", "Weapon_Polymer_Realistic"})
        for m in scene.data["materials"]:
            if m["name"] in used:
                self.assertIn("edge_wear" if "Polymer" not in m["name"] else "dirt",
                              [layer["type"] for layer in m["spec"]["layers"]], m["name"])
        self.assertLessEqual(scene.world_mesh(node).tri_count(), 15000)
        qa = hq_qa.qa(scene, {"id": node["id"], "profile": "uefn", "budget": "weapon"})
        self.assertGreaterEqual(qa["score"], 85, qa["summary"])


class DocCalls(unittest.TestCase):
    def test_doc_calls_parse(self):
        import re
        text = (Path(__file__).resolve().parent.parent / "skills" / "forge" / "references" / "weapons.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```json\n(.*?)```", text, re.S)
        self.assertTrue(blocks)
        for raw in blocks:
            for step in (lambda c: c if isinstance(c, list) else [c])(json.loads(raw)):
                self.assertIn(step["op"], hq.HQ_OPS, step)
                if step["op"] == "weapon":
                    hq_weapon.params(step["params"])
                if step["op"] == "material":  # documented finishes pass the PBR lint (coats are dielectric)
                    from .hq import materials as hq_mat
                    q = step["params"]
                    self.assertEqual(hq_mat.lint(hq_mat.expand(q.get("preset"), q.get("overrides"), q.get("layers"))), [], q)


@unittest.skipUnless(_READY and os.environ.get("FORGE_WEAPON_DEMO_OUT"), "set FORGE_WEAPON_DEMO_OUT=<folder> to write demo renders")
class Demo(Build):
    """weapon_rifle.png, weapon_pistol.png, weapon_sword.png: look three, then side, into FORGE_WEAPON_DEMO_OUT."""

    def test_demo_renders(self):
        import shutil
        out_dir = Path(os.environ["FORGE_WEAPON_DEMO_OUT"])
        out_dir.mkdir(parents=True, exist_ok=True)
        for kind in ("rifle", "pistol", "sword"):
            name = "WeaponDemo_" + kind
            self.addCleanup(forget, name)
            self.t["forge_scene_new"](name=name)
            self.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": "weapon", "params": {"kind": kind}}])
            for view, suffix in (("three", ""), ("front", "_side")):  # front = from -Y: the profile, ejection-port side
                got = self.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": "light", "params": {"preset": "product"}}],
                                            look=view)
                self.assertTrue(got.get("path"), got)
                shutil.copyfile(got["path"], out_dir / ("weapon_%s%s.png" % (kind, suffix)))

    test_every_kind = test_stylized_parts_and_length = None


if __name__ == "__main__":
    unittest.main()
