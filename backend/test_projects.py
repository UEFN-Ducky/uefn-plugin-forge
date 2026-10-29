"""py -3 -m backend.test_projects"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import projects, scene


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

    def test_flat_override_still_works(self):
        with patch.dict(os.environ, {"FORGE_SCENES_DIR": str(self.root / "flat")}):
            scene.create("A")
            self.assertTrue((self.root / "flat" / "A" / "scene.json").is_file())
            self.assertEqual([s["name"] for s in scene.list_scenes()], ["A"])


if __name__ == "__main__":
    unittest.main()
