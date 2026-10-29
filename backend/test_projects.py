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

    def test_flat_override_still_works(self):
        with patch.dict(os.environ, {"FORGE_SCENES_DIR": str(self.root / "flat")}):
            scene.create("A")
            self.assertTrue((self.root / "flat" / "A" / "scene.json").is_file())
            self.assertEqual([s["name"] for s in scene.list_scenes()], ["A"])


if __name__ == "__main__":
    unittest.main()
