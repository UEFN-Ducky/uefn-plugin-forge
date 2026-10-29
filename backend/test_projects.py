"""py -3 -m backend.test_projects"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from unittest.mock import MagicMock

from backend import panel_rpc, projects, scene, tools


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def _env(tmp, **extra):
    env = {"FORGE_HOME": str(tmp), "FORGE_PROJECTS_DIR": "", "FORGE_SCENES_DIR": ""}
    env.update(extra)
    return patch.dict(os.environ, env)


def _fake_scene(folder: Path, name: str, rev: int = 1, big: int = 0):
    folder.mkdir(parents=True)
    (folder / "scene.json").write_text(json.dumps({"name": name, "rev": rev, "blob": "x" * big}), encoding="utf-8")


class ProjectStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-proj-")
        self.root = Path(self.tmp.name)
        self.env = _env(self.root)
        self.env.start()
        projects._DONE.clear()
        scene._OPEN.clear()

    def tearDown(self):
        self.env.stop()
        projects._DONE.clear()
        self.tmp.cleanup()

    def test_migration_keeps_every_scene(self):
        for n in ("Biome_Canyon", "Lion_Built", "Zed"):
            _fake_scene(self.root / "scenes" / n, n, big=1000)
        (self.root / "scenes" / "Zed" / "textures").mkdir()
        (self.root / "files").mkdir()
        (self.root / "files" / "a.txt").write_text("keep")
        names = [s["name"] for s in scene.list_scenes()]
        self.assertEqual(names, ["Biome_Canyon", "Lion_Built", "Zed"])
        moved = self.root / "projects" / "My Scenes" / "scenes"
        self.assertTrue((moved / "Zed" / "textures").is_dir())
        self.assertEqual((self.root / "projects" / "My Scenes" / "files" / "a.txt").read_text(), "keep")
        self.assertFalse((self.root / "scenes").exists())
        mapping = json.loads((self.root / "scenes-migrated.json").read_text())
        self.assertEqual(mapping["Lion_Built"], "My Scenes/Lion_Built")
        self.assertEqual(projects.list_projects()[0]["scenes"], 3)

    def test_project_scene_paths(self):
        projects.new_project("LimoRide", ["uefn", "roblox"])
        self.assertTrue((self.root / "projects" / "LimoRide" / "library").is_dir())
        info = json.loads((self.root / "projects" / "LimoRide" / "project.json").read_text())
        self.assertEqual(info["targets"], ["uefn", "roblox"])
        scene.create("LimoRide/Limo_Body")
        self.assertTrue((self.root / "projects" / "LimoRide" / "scenes" / "Limo_Body" / "scene.json").is_file())
        self.assertEqual(scene.load("LimoRide/Limo_Body").data["name"], "Limo_Body")
        scene.create("Plain")  # plain name -> active project (My Scenes)
        self.assertTrue((self.root / "projects" / "My Scenes" / "scenes" / "Plain").is_dir())
        projects.set_active("LimoRide")
        self.assertEqual(scene.load("Limo_Body").data["name"], "Limo_Body")
        self.assertEqual([s["name"] for s in scene.list_scenes("My Scenes")], ["Plain"])
        with self.assertRaises(ValueError) as cm:
            scene.create("Nope/Thing")
        self.assertIn("LimoRide", str(cm.exception))

    def test_listing_never_loads_meshes(self):
        projects.new_project("Big")
        base = self.root / "projects" / "Big" / "scenes"
        for i in range(50):
            _fake_scene(base / ("S%02d" % i), "S%02d" % i, rev=i, big=2_000_000)
        with patch.object(scene, "load", side_effect=AssertionError("loaded a scene")), \
                patch.object(json, "loads", side_effect=AssertionError("parsed a whole scene")):
            projects.list_scenes("Big")  # warm the file cache: the first read of fresh files is the OS scanning them
            t = time.perf_counter()
            out = projects.list_scenes("Big")
            ms = (time.perf_counter() - t) * 1000
        projects.list_projects()
        self.assertEqual(len(out), 50)
        self.assertEqual(out[7], {"name": "S07", "rev": 7})
        self.assertLess(ms, 100)

    def test_project_tool_round_trip(self):
        api = _Api()
        tools.register_tools(api)
        t = api.fns
        out = t["forge_project"](action="new", name="LimoRide", targets=["uefn", "roblox"])
        self.assertEqual(out["active"], "LimoRide")
        made = t["forge_scene_new"](name="Limo_Body", project="LimoRide")
        self.assertEqual(made["name"], "LimoRide/Limo_Body")
        self.assertEqual([s["name"] for s in t["forge_scene_list"](project="LimoRide")["scenes"]], ["Limo_Body"])
        self.assertEqual(t["forge_scene_list"]()["scenes"][0]["name"], "Limo_Body")  # active project
        rows = {p["name"]: p for p in t["forge_project"](action="list")["projects"]}
        self.assertEqual(rows["LimoRide"]["scenes"], 1)
        self.assertEqual(rows["LimoRide"]["targets"], ["uefn", "roblox"])
        self.assertEqual(t["forge_project"](action="rename", name="LimoRide", new_name="Limo")["name"], "Limo")
        self.assertEqual(scene.load("Limo/Limo_Body").data["name"], "Limo_Body")
        moved = t["forge_project"](action="move_scene", scene="Limo/Limo_Body", name="My Scenes")
        self.assertEqual(moved["scene"], "My Scenes/Limo_Body")
        self.assertEqual(scene.list_scenes("Limo"), [])
        self.assertIn("confirm", t["forge_project"](action="delete", name="Limo")["error"])
        gone = t["forge_project"](action="delete", name="Limo", confirm=True)
        self.assertTrue(Path(gone["to"]).is_dir() and ".trash" in gone["to"])
        self.assertNotIn("Limo", [p["name"] for p in projects.list_projects()])
        self.assertEqual(projects.active(), "My Scenes")
        self.assertIn("Projects:", t["forge_project"](action="open", name="Nope")["error"])
        for out in (t["forge_project"](action="list"), gone):
            self.assertLessEqual(len(json.dumps(out)), 300)
        rpc = panel_rpc.forge_projects(action="active")
        self.assertTrue(rpc["ok"] and rpc["active"] == "My Scenes")

    def test_render_style_passthrough(self):
        api = _Api()
        tools.register_tools(api)
        t = api.fns
        t["forge_scene_new"](name="Toony")
        calls = []

        def fake_png(mesh, path, views=None, materials=None, textures=None, style=""):
            calls.append(("png", style, views))
            return []

        def fake_tiles(mesh, path, views, materials=None, textures=None, long_side=768, quality="look", style=""):
            calls.append(("tiles", style, views))
            return str(path)

        with patch("backend.render.render_png", fake_png), patch("backend.render.render_tiles", fake_tiles), \
                patch.object(tools, "_attach_captures", lambda r: r):
            t["forge_render"](name="Toony", views=["front"], style="toon")
            t["forge_render"](name="Toony", views=["front"])
            t["forge_fix"](name="Toony", look={"view": "front", "style": "toon"})
            t["forge_fix"](name="Toony", look="front")
            with self.assertRaises(ValueError) as bad:
                t["forge_render"](name="Toony", style="neon")
        self.assertEqual(calls, [("png", "toon", ["front"]), ("png", "", ["front"]),
                                 ("tiles", "toon", ["front"]), ("tiles", "", ["front"])])
        self.assertIn("pbr or toon", str(bad.exception))

    def test_scene_rename(self):
        projects.new_project("Race")
        a, b = scene.create("Race/Track_A"), scene.create("Race/Track_B")
        (a.folder / "history.json").write_text("[1]")
        lib = self.root / "projects" / "Race" / "library"
        lib.mkdir(exist_ok=True)
        (lib / "index.json").write_text(json.dumps({"items": {"asphalt": {"kind": "texture", "name": "Asphalt"}},
                                                    "links": {"asphalt": {str(a.folder): {"name": "Track_A", "refs": ["m1"]}}}}))
        out = scene.dispatch("Race/Track_A", "scene_rename", {"name": "Track_Main"})
        self.assertEqual(out["name"], "Race/Track_Main")
        base = self.root / "projects" / "Race" / "scenes"
        self.assertFalse((base / "Track_A").exists())
        self.assertEqual((base / "Track_Main" / "history.json").read_text(), "[1]")
        self.assertEqual(scene.load("Race/Track_Main").name, "Track_Main")
        self.assertEqual([s["name"] for s in scene.list_scenes("Race")], ["Track_B", "Track_Main"])
        links = json.loads((lib / "index.json").read_text())["links"]["asphalt"]
        self.assertEqual(list(links), [str(base / "Track_Main")])
        self.assertEqual(links[str(base / "Track_Main")]["name"], "Track_Main")
        with self.assertRaises(ValueError) as cm:
            scene.dispatch("Race/Track_Main", "scene_rename", {"name": "Track_B"})
        self.assertIn("already exists", str(cm.exception))
        self.assertTrue((base / "Track_B" / "scene.json").is_file())
        # the dock sends the new name as typed, through forge_call
        self.assertTrue(panel_rpc.forge_call(op="scene_rename", name="Race/Track_B", params={"name": "Track_Z"})["ok"])

    def test_panel_library_rpc(self):
        projects.new_project("Race")
        projects.set_active("Race")
        lib = self.root / "projects" / "Race" / "library"
        (lib / "thumbs").mkdir(parents=True)
        (lib / "thumbs" / "asphalt.png").write_bytes(b"\x89PNG-fake")
        (lib / "index.json").write_text(json.dumps({"items": {
            "asphalt": {"kind": "texture", "name": "Asphalt"}, "rig_a": {"kind": "rig", "name": "Biped"},
            "red": {"kind": "material", "name": "Red paint"}}, "links": {}}))
        out = panel_rpc.forge_library(action="list", kind="Texture", q="asph")
        self.assertTrue(out["ok"])
        self.assertEqual([(i["id"], i["name"], i["kind"]) for i in out["items"]], [("asphalt", "Asphalt", "texture")])
        self.assertTrue(out["items"][0]["thumb"].startswith("data:image/png;base64,"))
        everything = panel_rpc.forge_library(action="list")["items"]
        self.assertEqual({i["id"] for i in everything}, {"asphalt", "rig_a", "red"})
        self.assertEqual(next(i for i in everything if i["id"] == "rig_a")["thumb"], "")  # rigs have none
        with patch("backend.library.act", return_value={"summary": "Linked"}) as act:
            res = panel_rpc.forge_library(action="link", id="asphalt", kind="texture", scene="Race/Track_A")
        self.assertEqual(res, {"ok": True, "summary": "Linked"})
        self.assertEqual(act.call_args.kwargs["scene"], "Race/Track_A")
        bad = panel_rpc.forge_library(action="link", id="asphalt", kind="texture", scene="Nope/X")
        self.assertFalse(bad["ok"])

    def _library_part(self, kind="sphere"):
        """Race/Src holds one object; it becomes library part 'wheel' of project Race."""
        from backend import library

        projects.new_project("Race")
        if not (self.root / "projects" / "Race" / "scenes" / "Src").exists():
            scene.create("Race/Src")
        ids = scene.dispatch("Race/Src", "add", {"kind": kind})["ids"]
        library.act("add", kind="part", id="wheel", from_scene="Race/Src", object=ids[0])
        return library

    def _link(self, library, scene_name, n=1):
        scene.create(scene_name)
        for _ in range(n):
            library.act("link", kind="part", id="wheel", scene=scene_name)
        return scene.load(scene_name)

    @staticmethod
    def _size(folder: Path) -> int:
        """Scene bytes without the (capped) undo snapshots, which hold the node list but no meshes."""
        return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and "history-snapshots" not in f.parts)

    def test_linked_part_not_stored_in_scene(self):
        library = self._library_part()
        car = self._link(library, "Race/Car", 1)
        one = self._size(car.folder)
        for _ in range(99):
            library.act("link", kind="part", id="wheel", scene="Race/Car")
        car = scene.load("Race/Car")
        self.assertEqual(sum(1 for n in car.data["nodes"] if n.get("lib") == "wheel"), 100)
        self.assertEqual(len([p for p in car.data["prototypes"] if p.get("lib") == "wheel"]), 1)  # instanced
        self.assertEqual(list((car.folder / "mesh-blobs").glob("*")) if (car.folder / "mesh-blobs").is_dir() else [], [])
        self.assertLess(self._size(car.folder) - one, 120_000)  # 99 nodes + undo index, no mesh copies
        scene._OPEN.clear()
        again = scene.load("Race/Car")  # resolved from the library at load
        proto = next(p for p in again.data["prototypes"] if p.get("lib") == "wheel")
        self.assertGreater(len(proto["mesh"]["verts"]), 8)
        self.assertGreater(again.world_mesh(again.data["nodes"][0]).tri_count(), 8)
        scene.dispatch("Race/Car", "undo", {})  # undo restores the linked mesh too
        self.assertTrue(all(p["mesh"] for p in scene.load("Race/Car").data["prototypes"] if p.get("lib")))

    def test_linked_part_edits_propagate(self):
        library = self._library_part("sphere")
        self._link(library, "Race/Car", 2)
        box = scene.dispatch("Race/Src", "add", {"kind": "box"})["ids"][0]
        out = library.act("add", kind="part", id="wheel", from_scene="Race/Src", object=box)
        self.assertIn("Updated", out["summary"])
        scene._OPEN.clear()
        car = scene.load("Race/Car")
        proto = next(p for p in car.data["prototypes"] if p.get("lib") == "wheel")
        lib_mesh = projects._part_mesh("Race", "wheel")
        self.assertEqual(proto["mesh"]["verts"], lib_mesh["verts"])
        self.assertEqual(len(proto["mesh"]["verts"]), 8)  # the box replaced the sphere
        self._link(library, "Race/Truck", 1)
        scene._OPEN.clear()
        other = scene.load("Race/Truck")  # a second scene sees the same shared copy
        self.assertIs(next(p for p in other.data["prototypes"] if p.get("lib"))["mesh"]["verts"], lib_mesh["verts"])

    def test_missing_library_part_warns(self):
        import shutil

        library = self._library_part()
        self._link(library, "Race/Car", 1)
        shutil.rmtree(self.root / "projects" / "Race" / "library" / "parts" / "wheel")
        scene._OPEN.clear()
        car = scene.load("Race/Car")
        self.assertTrue(car.warnings and "wheel" in car.warnings[0] and "Race" in car.warnings[0], car.warnings)
        node = next(n for n in car.data["nodes"] if n.get("lib") == "wheel")
        self.assertEqual(car.world_mesh(node).tri_count(), 12)  # placeholder box, no crash
        self.assertIn("warnings", car.summary())
        car.save()  # saving a scene with a missing part keeps the placeholder instead of losing the node
        scene._OPEN.clear()
        self.assertEqual(len(scene.load("Race/Car").data["nodes"]), 1)

    def test_flat_override_still_works(self):
        with patch.dict(os.environ, {"FORGE_SCENES_DIR": str(self.root / "flat")}):
            scene.create("A")
            self.assertTrue((self.root / "flat" / "A" / "scene.json").is_file())
            self.assertEqual([s["name"] for s in scene.list_scenes()], ["A"])


if __name__ == "__main__":
    unittest.main()
