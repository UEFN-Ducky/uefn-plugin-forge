"""Regression checks for scene caching, selection, hierarchy and export; no UEFN."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from .scene import Scene, dispatch, load, forget


class SceneRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-regression-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(self.temp.name) / "scenes")})
        self.env.start()
        dispatch("", "scene_new", {"name": "Test"})
        self.a = dispatch("Test", "add", {"kind": "box", "name": 'A <mesh> "quoted"'})["id"]
        self.b = dispatch("Test", "add", {"kind": "box", "pos": [100, 200, 0]})["id"]

    def tearDown(self):
        forget("Test")
        self.env.stop()
        self.temp.cleanup()

    def call(self, operation, **params):
        return dispatch("Test", operation, params, actor="user")

    def test_selection_is_observable_without_copy_write_or_history(self):
        scene = load("Test")
        payload = scene.payload()
        prototype = scene.node(self.a)["prototype"]
        stamp = (scene.folder / "scene.json").stat().st_mtime_ns
        history = scene._hist().read_bytes()
        rev = scene.data["rev"]
        with patch.object(Scene, "_snapshot", side_effect=AssertionError("selection copied scene")), patch.object(Scene, "save", side_effect=AssertionError("selection wrote scene")):
            self.call("select", object=self.a, faces=[1])
            self.call("select", object=self.b)
            self.assertEqual(scene.data["selection"]["faces"], [])
            self.call("select", object="")
        self.assertGreater(scene.data["rev"], rev)
        self.assertIsNone(scene.data["selection"]["object"])
        self.assertEqual(scene._hist().read_bytes(), history)
        self.assertEqual((scene.folder / "scene.json").stat().st_mtime_ns, stamp)
        self.assertEqual(scene.node(self.a)["prototype"], prototype)
        self.assertIs(scene.payload()["instances"], payload["instances"])

    def test_payload_invalidates_after_edit_undo_redo(self):
        scene = load("Test")
        before = scene.payload()
        self.call("edit", id=self.a, pos=[300, 0, 0])
        after = scene.payload()
        rev = scene.data["rev"]
        self.assertIsNot(before["instances"], after["instances"])
        self.call("undo")
        self.assertGreater(scene.data["rev"], rev)
        row = next(r for r in scene.payload()["instances"] if r[0] == self.a)
        self.assertEqual(row[3], 0)
        self.call("redo")
        row = next(r for r in scene.payload()["instances"] if r[0] == self.a)
        self.assertEqual(row[3], 300)

    def test_failed_edit_rolls_back_cached_scene(self):
        scene = load("Test")
        self.call("select", object=self.a, faces=[])
        before = scene._snapshot()
        with self.assertRaises(ValueError):
            self.call("mesh_op", op="__invalid_after_bake__")
        self.assertEqual(scene.data, before)

    def test_open_invalidates_cache_and_save_captures_session(self):
        from .files import open_forge, save_forge
        import zipfile
        self.call("select", object=self.a)
        saved = save_forge("Test")
        with zipfile.ZipFile(saved["path"]) as archive:
            self.assertEqual(json.loads(archive.read("scene.json"))["selection"]["object"], self.a)
        original = load("Test")
        self.call("edit", id=self.a, name="Changed")
        open_forge(saved["path"])
        self.assertIsNot(load("Test"), original)
        self.assertNotEqual(load("Test").node(self.a)["name"], "Changed")

    def test_external_scene_replacement_invalidates_cache(self):
        old = load("Test")
        path = old.folder / "scene.json"
        data = json.loads(path.read_text())
        data["nodes"][0]["name"] = "External"
        path.write_text(json.dumps(data))
        os.utime(path, ns=(time.time_ns(), old._stamp + 1_000_000))
        self.assertEqual(load("Test").node(self.a)["name"], "External")

    def test_nested_groups_preserve_world_and_prototypes(self):
        scene = load("Test")
        group = self.call("group", ids=[self.a], name="Body")["id"]
        self.call("edit", id=group, pos=[100, 40, 80], rot=[15, 30, 45], scale=[2, 2, 2])
        original = scene.world_mesh(scene.node(self.a)).verts
        prototype = scene.node(self.a)["prototype"]
        outer = self.call("group", ids=[group, self.b], name="Assembly")["id"]
        self.call("ungroup", id=group)
        current = scene.world_mesh(scene.node(self.a)).verts
        for a, b in zip(original, current):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=6)
        self.assertEqual(scene.node(self.a)["prototype"], prototype)
        self.assertEqual(scene.node(self.a)["parent"], outer)
        self.call("delete", ids=[outer])
        self.assertEqual(scene.data["nodes"], [])

    def test_cycles_and_missing_parents_rejected_atomically(self):
        scene = load("Test")
        inner = self.call("group", ids=[self.a])["id"]
        outer = self.call("group", ids=[inner])["id"]
        before = scene._snapshot()
        for op, params in (("edit", {"id": outer, "parent": inner}), ("group", {"ids": [outer], "into": inner}), ("edit", {"id": self.b, "parent": "missing"})):
            with self.assertRaises(ValueError):
                self.call(op, **params)
            self.assertEqual(scene.data, before)

    def test_layer_lock_blocks_mesh_edit_and_invalidates_payload(self):
        scene = load("Test")
        before = scene.payload()
        self.call("layer", action="set", id="default", locked=True)
        self.assertIsNot(scene.payload()["nodes"], before["nodes"])
        self.call("select", object=self.a, query="top")
        with self.assertRaises(ValueError):
            self.call("mesh_op", op="extrude", distance=32)

    def test_export_stays_shared(self):
        from .uefn import collect_exports
        exports = collect_exports(load("Test"), "each")
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0]["mesh"].tri_count(), 12)

    def test_export_hides_descendants_and_avoids_file_name_collisions(self):
        from .uefn import collect_exports, export_glbs
        scene = load("Test")
        group = self.call("group", ids=[self.a, self.b])["id"]
        self.call("edit", id=group, visible=False)
        self.assertEqual(collect_exports(scene), [])
        self.call("edit", id=group, visible=True)
        self.call("edit", id=self.a, name="Part")
        self.call("edit", id=self.b, name="Part", params={"w": 64})
        exported = export_glbs(scene, "each", Path(self.temp.name) / "export")
        self.assertEqual(len({e["glb"] for e in exported}), 2)

    def test_edge_delete_and_shear_rejection(self):
        scene = load("Test")
        self.call("select", object=self.a, select_mode="edge", edges=[[0, 1]])
        self.call("mesh_op", op="delete", kind="edge")
        self.assertLess(scene.base_mesh(scene.node(self.a)).tri_count(), 12)
        group = self.call("group", ids=[self.b])["id"]
        self.call("edit", id=group, scale=[2, 1, 1])
        self.call("edit", id=self.b, rot=[0, 0, 45])
        before = scene._snapshot()
        with self.assertRaisesRegex(ValueError, "shear"):
            self.call("ungroup", id=group)
        self.assertEqual(scene.data, before)

    def test_cache_is_scoped_to_scene_directory(self):
        original = load("Test")
        with patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(self.temp.name) / "other")}):
            dispatch("", "scene_new", {"name": "Test"})
            self.assertIsNot(load("Test"), original)
            self.assertEqual(load("Test").data["nodes"], [])
            forget("Test")

    def test_material_edits_refresh_and_keep_export_variants(self):
        from .uefn import collect_exports
        scene = load("Test")
        before = scene.data["geom"]
        mat = self.call("material_set", name="Paint", base_color=[1, 0, 0])["material"]["id"]
        self.assertGreater(scene.data["geom"], before)
        self.call("select", object=self.a)
        self.call("material_assign", material=mat)
        self.assertEqual(len(scene.data["prototypes"]), 1)
        self.assertEqual(len(collect_exports(scene)), 2)
        self.call("select", object=self.a, query="top")
        self.call("mesh_op", op="extrude", distance=32)
        self.call("material_assign", material=mat)
        self.assertEqual(set(scene.base_mesh(scene.node(self.a)).face_mat), {mat})
        self.assertEqual(len(collect_exports(scene)), 2)
        default = scene.data["materials"][0]["id"]
        self.call("material_assign", material=default, faces=[0])
        self.assertEqual(scene.base_mesh(scene.node(self.a)).face_mat[0], default)

    def test_empty_group_still_supported(self):
        gid = self.call("group", ids=[], name="Empty")["id"]
        self.assertEqual(load("Test").node(gid)["type"], "group")

    def test_join_under_transformed_parent_preserves_world_bounds(self):
        scene = load("Test")
        group = self.call("group", ids=[self.a, self.b])["id"]
        self.call("edit", id=group, pos=[100, 50, 20], rot=[20, 35, 50])
        points = scene.world_mesh(scene.node(self.a)).verts + scene.world_mesh(scene.node(self.b)).verts
        expected = [[fn(v[axis] for v in points) for axis in range(3)] for fn in (min, max)]
        self.call("join", ids=[self.a, self.b])
        node = scene.node(scene.data["selection"]["object"])
        self.assertEqual(node["parent"], group)
        actual = scene.world_mesh(node).bounds()
        for a, b in zip(expected, actual):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=6)


if __name__ == "__main__":
    unittest.main()
