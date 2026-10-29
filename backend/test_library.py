"""py -3 -m backend.test_library"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import library, projects, scene
from backend.scene import dispatch


class _Api:
    def __init__(self):
        self.fns, self.rpcs = {}, {}

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def register_panel_rpc(self, name, fn):
        self.rpcs[name] = fn


class Library(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-lib-")
        self.env = patch.dict(os.environ, {"FORGE_HOME": self.tmp.name, "FORGE_PROJECTS_DIR": "", "FORGE_SCENES_DIR": "",
                                            "FORGE_SOFT_RENDER": "1"})
        self.env.start()
        projects._DONE.clear()
        scene._OPEN.clear()
        projects.new_project("LimoRide")
        projects.set_active("LimoRide")
        for n in ("Car1", "Car2", "Car3"):
            scene.create("LimoRide/" + n)
        dispatch("LimoRide/Car1", "material_set", {"name": "Rubber", "base_color": [0.8, 0.1, 0.1, 1]})
        self.wheel = dispatch("LimoRide/Car1", "add", {"kind": "cylinder", "params": {"radius": 30, "height": 20, "seg": 8},
                                                       "name": "Wheel", "material": "Rubber"})["id"]
        self.big = dispatch("LimoRide/Car1", "add", {"kind": "sphere", "params": {"radius": 40, "seg": 12, "rings": 6},
                                                     "name": "Big", "material": "Rubber"})["id"]

    def tearDown(self):
        scene._OPEN.clear()
        self.env.stop()
        projects._DONE.clear()
        self.tmp.cleanup()

    def tris(self, name, nid):
        sc = scene.load(name)
        return sc.base_mesh(sc.node(nid)).tri_count()

    def link(self, scene_name):
        library.act("link", id="wheel", scene=scene_name)
        return [n for n in scene.load(scene_name).data["nodes"] if n.get("lib") == "wheel"][-1]["id"]

    def test_link_updates_all_scenes(self):
        out = library.act("add", "part", "wheel", from_scene="LimoRide/Car1", object=self.wheel)
        self.assertIn("Added part wheel", out["summary"])
        linked = {"LimoRide/Car1": [self.link("LimoRide/Car1"), self.link("LimoRide/Car1")],
                  "LimoRide/Car2": [self.link("LimoRide/Car2")], "LimoRide/Car3": [self.link("LimoRide/Car3")]}
        self.assertEqual(library.act("list")["items"], ["wheel part x4"])
        before = self.tris("LimoRide/Car2", linked["LimoRide/Car2"][0])
        protos = [p for p in scene.load("LimoRide/Car1").data["prototypes"] if p.get("lib") == "wheel"]
        self.assertEqual(len(protos), 1)  # two links in one scene share one stored mesh
        parts = Path(self.tmp.name, "projects", "LimoRide", "library", "parts", "wheel")
        self.assertEqual(len(list(parts.glob("mesh.*"))), 1)
        # the library asset changes: every scene follows
        out = library.act("add", "part", "wheel", from_scene="LimoRide/Car1", object=self.big)
        self.assertIn("refreshed 3 scene(s)", out["summary"])
        for name, ids in linked.items():
            for nid in ids:
                self.assertNotEqual(self.tris(name, nid), before)
        self.assertEqual(library.act("list")["items"], ["wheel part x4"])
        # materials follow too
        library.act("add", "material", "rubber", from_scene="LimoRide/Car1", object="Rubber")
        library.act("link", id="rubber", scene="LimoRide/Car2")
        dispatch("LimoRide/Car1", "material_set", {"id": "Rubber", "base_color": [0.1, 0.1, 0.9, 1]})
        library.act("add", "material", "rubber", from_scene="LimoRide/Car1", object="Rubber")
        mat = next(m for m in scene.load("LimoRide/Car2").data["materials"] if m.get("lib") == "rubber")
        self.assertEqual(mat["base_color"], [0.1, 0.1, 0.9, 1])
        # the wheel's own material came along with the part
        self.assertTrue(any(m.get("lib", "").startswith("wheel:") for m in scene.load("LimoRide/Car3").data["materials"]))

    def test_make_local_breaks_link(self):
        library.act("add", "part", "wheel", from_scene="LimoRide/Car1", object=self.wheel)
        keep = self.link("LimoRide/Car2")
        follow = self.link("LimoRide/Car3")
        before = self.tris("LimoRide/Car2", keep)
        out = library.act("make_local", scene="LimoRide/Car2", object=keep)
        self.assertIn("local copy", out["summary"])
        node = scene.load("LimoRide/Car2").node(keep)
        self.assertNotIn("lib", node)
        self.assertEqual(library.act("list")["items"], ["wheel part x1"])
        library.act("add", "part", "wheel", from_scene="LimoRide/Car1", object=self.big)
        self.assertEqual(self.tris("LimoRide/Car2", keep), before)  # detached
        self.assertNotEqual(self.tris("LimoRide/Car3", follow), before)  # still linked
        sc = scene.load("LimoRide/Car2")
        self.assertFalse(sc.materials()[sc.node(keep)["material"]].get("lib"))  # its material is local too
        with self.assertRaises(ValueError) as cm:
            library.act("make_local", scene="LimoRide/Car2", object=keep)
        self.assertIn("No linked entry", str(cm.exception))

    def test_list_compact(self):
        self.assertEqual(library.act("list")["items"], [])
        library.act("add", "part", "wheel", from_scene="LimoRide/Car1", object=self.wheel)
        library.act("add", "material", "rubber", from_scene="LimoRide/Car1", object="Rubber")
        png = Path(self.tmp.name, "grain.png")
        from PIL import Image
        Image.new("RGB", (64, 64), (200, 160, 40)).save(png)
        library.act("add", "texture", "grain", path=str(png))
        library.act("link", id="grain", scene="LimoRide/Car2")
        out = library.act("list")
        self.assertLess(len(json.dumps(out)), 300)
        self.assertEqual(sorted(out["items"]), ["grain texture x1", "rubber material x0", "wheel part x0"])
        self.assertEqual(library.act("list", "texture")["items"], ["grain texture x1"])
        with self.assertRaises(ValueError) as cm:
            library.act("remove", id="grain")
        self.assertIn("make_local", str(cm.exception))
        library.act("remove", id="grain", force=True)
        self.assertFalse(any(m.get("lib") for m in scene.load("LimoRide/Car2").data["materials"]))
        self.assertEqual(sorted(i.split()[0] for i in library.act("list")["items"]), ["rubber", "wheel"])
        with self.assertRaises(ValueError) as cm:
            library.act("link", id="whel", scene="LimoRide/Car2")
        self.assertIn("wheel", str(cm.exception))

    def test_rig_thumb_and_tool(self):
        api = _Api()
        library.register(api)
        t = api.fns["forge_library"]
        self.assertIn("from_scene", t(action="add", kind="part", id="wheel")["error"])  # fix hint, no traceback
        t(action="add", kind="part", id="wheel", from_scene="LimoRide/Car1", object=self.wheel)
        thumb = t(action="thumb", id="wheel")
        from PIL import Image
        with Image.open(thumb["path"]) as im:
            self.assertLessEqual(max(im.size), 128)
        t(action="link", id="wheel", scene="LimoRide/Car2")
        self.assertEqual(api.rpcs["forge_library"](action="list")["items"], ["wheel part x1"])
        self.assertFalse(api.rpcs["forge_library"](action="nope")["ok"])


if __name__ == "__main__":
    unittest.main()
