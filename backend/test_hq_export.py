"""HQ export: per-engine recipes and the re-import round trip (needs the Blender engine)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from .engine import runtime
from .hq import export as hq_export

_READY = runtime.engine_status()["state"] == "ready"


class RecipeTests(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(hq_export.recipe("godot")["format"], "glb")
        self.assertEqual(hq_export.recipe("ue5")["normal"], "dx")
        self.assertEqual(hq_export.recipe("unity")["normal"], "gl")
        self.assertEqual(hq_export.recipe("ue5", "abc")["format"], "abc")
        with self.assertRaisesRegex(ValueError, "Did you mean 'godot'"):
            hq_export.recipe("godt")
        with self.assertRaisesRegex(ValueError, "No format"):
            hq_export.recipe("ue5", "dwg")
        roblox = hq_export.recipe("roblox")
        self.assertEqual((roblox["format"], roblox["tris"], roblox["verts"], roblox["triangulate"]), ("fbx", 20000, 10000, True))
        self.assertEqual(hq_export.recipe("web")["webp"], 85)
        self.assertEqual(hq_export.recipe("godot")["ucx"], "godot")
        self.assertEqual(hq_export.recipe("ue5", "stl")["format"], "stl")


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-export-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        from .scene import forget
        forget("Hero")
        self.env.stop()
        self.tmp.cleanup()

    def test_export_roundtrip(self):
        from . import hq
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Hero"})
        nid = dispatch("Hero", "add", {"kind": "capsule", "params": {"radius": 25, "height": 180}})["id"]
        dispatch("Hero", "select", {"object": nid})
        dispatch("Hero", "rig", {"action": "template", "template": "biped"})
        dispatch("Hero", "rig", {"action": "bind", "auto": True, "ids": [nid]})
        dispatch("Hero", "anim", {"action": "generate", "kind": "walk"})
        scene = load("Hero")
        node = scene.node(nid)
        mesh = scene.local_mesh(node)
        for k, name in enumerate(("smile", "blink")):
            mesh.attrs["sk:" + name] = [[0.0, 0.0, (k + 1) * v[2] / 100.0] for v in mesh.verts]
        scene.write_mesh(node, mesh)
        for target, ext in (("godot", ".glb"), ("ue5", ".fbx"), ("unity", ".fbx")):
            out = hq.run_hq(scene, "export", {"target": target, "verify": True})
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["path"].endswith(ext), out)
            self.assertEqual((out["stats"]["skins"], out["stats"]["clips"], out["stats"]["morphs"]), (1, 1, 2), out)
            self.assertNotIn("warnings", out)
            self.assertTrue(out["verified"], out)
            self.assertLessEqual(len(str(out)), 300)

    def static(self, name="Hero"):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": name})
        nid = dispatch(name, "add", {"kind": "box", "object_name": "Crate", "params": {"w": 50, "d": 40, "h": 200}})["id"]
        return load(name), nid

    def test_obj_and_stl_axis_and_scale(self):
        """Read the files themselves: OBJ is metres Y-up, STL is millimetres Z-up."""
        import struct
        from . import hq
        scene, _ = self.static()
        out = hq.run_hq(scene, "export", {"format": "obj", "verify": True})
        self.assertTrue(out["verified"], out)
        from pathlib import Path
        ys = [float(line.split()[2]) for line in Path(out["path"]).read_text().splitlines() if line.startswith("v ")]
        self.assertAlmostEqual(max(ys) - min(ys), 2.0, places=3)
        out = hq.run_hq(scene, "export", {"format": "stl", "verify": True})
        self.assertTrue(out["verified"], out)
        raw = Path(out["path"]).read_bytes()
        count = struct.unpack("<I", raw[80:84])[0]
        zs = [struct.unpack("<f", raw[84 + 50 * t + 12 + 12 * k + 8:84 + 50 * t + 12 + 12 * k + 12])[0] for t in range(count) for k in range(3)]
        self.assertAlmostEqual(max(zs) - min(zs), 2000.0, delta=0.5)
        out = hq.run_hq(scene, "export", {"format": "usdz", "verify": True})
        self.assertTrue(out["path"].endswith(".usdz") and out["verified"], out)

    def test_roblox_limits_and_web(self):
        from . import hq
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Hero"})
        dispatch("Hero", "add", {"kind": "sphere", "params": {"radius": 50, "seg": 200, "rings": 100}})
        scene = load("Hero")
        out = hq.run_hq(scene, "export", {"target": "roblox", "verify": True})
        self.assertTrue(out["path"].endswith(".fbx"), out)
        self.assertIn("> 20k", out["summary"], out)
        out = hq.run_hq(scene, "export", {"target": "web"})
        self.assertTrue(out["path"].endswith(".glb"), out)
        self.assertIn("> 30k", out["summary"], out)
        self.assertLessEqual(len(str(out)), 300)

    def test_ucx_and_lods(self):
        from . import hq
        scene, nid = self.static()
        hq.run_hq(scene, "collision", {"id": nid, "kind": "box"})
        scene.node(nid)["hq"] = {"lods": {"ratios": [1, 0.5, 0.25]}}
        out = hq.run_hq(scene, "export", {"target": "ue5", "verify": True, "lods": True})
        self.assertIn("3 LODs", out["summary"])
        self.assertIn("1 UCX", out["summary"])
        from pathlib import Path
        text = Path(out["path"]).read_bytes()
        base = scene.node(nid)["name"].encode()
        for name in (b"UCX_" + base + b"_01", base + b"_LOD0", base + b"_LOD2"):
            self.assertTrue(name in text, name)
        out = hq.run_hq(scene, "export", {"target": "godot"})
        self.assertTrue(base + b"_01-colonly" in Path(out["path"]).read_bytes())
        out = hq.run_hq(scene, "export", {"target": "unity"})
        self.assertFalse(b"UCX_" in Path(out["path"]).read_bytes())

    def test_convert(self):
        from . import hq
        scene, _ = self.static()
        glb = hq.run_hq(scene, "export", {"target": "generic"})["path"]
        out = hq.run_hq(scene, "convert", {"path": glb, "to": "obj"})
        self.assertTrue(out["path"].endswith(".obj") and os.path.isfile(out["path"]), out)
        self.assertEqual(out["tris"], 12, out)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            hq.run_hq(scene, "convert", {"path": glb, "to": "glb"})
        self.assertLessEqual(len(str(out)), 300)


class SendTests(unittest.TestCase):
    def test_send_payload(self):
        """Mock listener: shader picks the master, Normal/ORM params switch on, Nanite flag, brief result."""
        from pathlib import Path
        from .scene import dispatch, forget, load
        from .uefn import brief, send_to_uefn
        calls = []

        def listener(command, params, timeout=None):
            calls.append((command, params))
            if command == "import_asset":
                stem = Path(params["source_file"]).stem
                return {"imported": [f"{params['destination_path']}/StaticMeshes/{stem}.{stem}"]}
            if command == "create_material_instance":
                return {"material_instance_path": f"{params['folder']}/{params['asset_name']}"}
            return {"get_project_info": {"content_root": "/Island"},
                    "forge_build_master": {"material_path": "/Island/M_Forge_Master"},
                    "forge_build_hair": {"material_path": "/Island/M_Forge_Hair"},
                    "forge_build_skin": {"material_path": "/Island/M_Forge_Skin"},
                    "validate_uefn_asset": {"ok": True}}.get(command, {"ok": True})

        with tempfile.TemporaryDirectory(prefix="forge-send-") as tmp, patch.dict(os.environ, {"FORGE_SCENES_DIR": tmp}):
            dispatch("", "scene_new", {"name": "Prop"})
            dispatch("Prop", "add", {"kind": "box", "object_name": "Face"})
            scene = load("Prop")
            for m in scene.data["materials"]:
                m.update(shader="skin", subsurface_color=[0.8, 0.3, 0.2], textures={"normal": "n.png", "orm": "o.png"})
            (Path(scene.folder) / "textures").mkdir(parents=True, exist_ok=True)
            for name in ("n.png", "o.png"):
                (Path(scene.folder) / "textures" / name).write_bytes(b"x")
            scene.save()
            out = send_to_uefn("Prop", listener, nanite=True, verbose=False)
            forget("Prop")
        by = {}
        for c, p in calls:
            by.setdefault(c, []).append(p)
        self.assertEqual(len(by["forge_build_skin"]), 1)
        self.assertNotIn("forge_build_hair", by)
        self.assertTrue(all(p["parent_material_path"] == "/Island/M_Forge_Skin" for p in by["create_material_instance"]))
        names = {p.get("param_name"): p for p in by["set_material_instance_scalar"] + by["set_material_instance_texture"]}
        self.assertIn("NormalTex", names)
        self.assertIn("ORMTex", names)
        self.assertEqual(names["UseNormalTex"]["value"], 1.0)
        self.assertEqual(names["UseORM"]["value"], 1.0)
        self.assertEqual({p["usage"] for p in by["forge_set_texture_usage"]}, {"normal", "masks"})
        self.assertTrue(by["forge_set_mesh_options"][0]["nanite"])
        self.assertTrue(any(p.get("param_name") == "SubsurfaceColor" for p in by["set_material_instance_vector"]))
        self.assertLessEqual(len(str(out)), 300, out)
        self.assertTrue(out["ok"] and out["assets"][0].startswith("SM_"), out)
        many = {"folder": "/a/F", "assets": [{"mesh": "/a/SK_%d" % i, "materials": ["/a/MI_Long_Name_%d" % i] * 2} for i in range(30)]}
        self.assertLessEqual(len(str(brief(many))), 300)

    def test_send_keeps_existing_prefix(self):
        """A node already named SM_X lands as SM_X, never SM_SM_X; a plain X still gets SM_."""
        from pathlib import Path
        from .scene import dispatch, forget
        from .uefn import _asset_name, send_to_uefn
        calls = []

        def listener(command, params, timeout=None):
            calls.append((command, params))
            if command == "import_asset":
                stem = Path(params["source_file"]).stem
                return {"imported": [f"{params['destination_path']}/StaticMeshes/{stem}.{stem}"]}
            if command == "create_material_instance":
                return {"material_instance_path": f"{params['folder']}/{params['asset_name']}"}
            return {"get_project_info": {"content_root": "/Island"}, "forge_build_master": {"material_path": "/Island/M_Forge_Master"},
                    "validate_uefn_asset": {"ok": True}}.get(command, {"ok": True})

        with tempfile.TemporaryDirectory(prefix="forge-send-") as tmp, patch.dict(os.environ, {"FORGE_SCENES_DIR": tmp}):
            for scene, node in (("Lamp", "SM_Headlight"), ("Car", "Bumper")):
                dispatch("", "scene_new", {"name": scene})
                dispatch(scene, "add", {"kind": "box", "name": node})
                send_to_uefn(scene, listener, verbose=True)
                forget(scene)
        stems = sorted(Path(p["source_file"]).stem for c, p in calls if c == "import_asset" and p["source_file"].endswith((".glb", ".fbx")))
        self.assertEqual(stems, ["SM_Bumper", "SM_Headlight"], calls)
        self.assertEqual(_asset_name("SK_", "sk_Hero"), "sk_Hero")
        self.assertEqual(_asset_name("SM_", "Smoke"), "SM_Smoke")  # "Sm" alone is no prefix


class StoredLodTests(unittest.TestCase):
    def send(self, hq, **kwargs):
        from pathlib import Path
        from .scene import dispatch, forget, load
        from .uefn import send_to_uefn
        calls = []

        def listener(command, params, timeout=None):
            calls.append((command, params))
            if command == "import_asset":
                stem = Path(params["source_file"]).stem
                return {"imported": [f"{params['destination_path']}/StaticMeshes/{stem}.{stem}"]}
            if command == "create_material_instance":
                return {"material_instance_path": f"{params['folder']}/{params['asset_name']}"}
            return {"get_project_info": {"content_root": "/Island"},
                    "forge_build_master": {"material_path": "/Island/M_Forge_Master"}}.get(command, {"ok": True})

        with tempfile.TemporaryDirectory(prefix="forge-lod-") as tmp, patch.dict(os.environ, {"FORGE_SCENES_DIR": tmp}):
            dispatch("", "scene_new", {"name": "Rock"})
            nid = dispatch("Rock", "add", {"kind": "box", "object_name": "Rock"})["id"]
            scene = load("Rock")
            if hq:
                scene.node(nid)["hq"] = {"lods": hq}
            scene.save()
            send_to_uefn("Rock", listener, **kwargs)
            forget("Rock")
        return [p for c, p in calls if c == "forge_set_mesh_options"]

    def test_send_uses_stored_lods(self):
        got = self.send({"ratios": [1, 0.5, 0.25, 0.1], "nanite": False})
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0]["mesh_path"].split("/")[-1].startswith("SM_"), got)
        self.assertEqual((got[0]["lod_count"], got[0]["nanite"], got[0]["ratios"]), (4, False, [1, 0.5, 0.25, 0.1]))
        self.assertTrue(self.send({"ratios": [], "nanite": True})[0]["nanite"])
        self.assertEqual(self.send(None), [])
        explicit = self.send({"ratios": [1, 0.5], "nanite": False}, lods=3)[0]
        self.assertEqual(explicit["lod_count"], 3)
        self.assertNotIn("ratios", explicit)


if __name__ == "__main__":
    unittest.main()
