"""Import regressions; conversion integration runs when local Blender is available."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .scene import dispatch, load, forget
from .blend_import import import_blend, _blender
from .uefn import export_glbs


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-import-test-")
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(self.root / "scenes")})
        self.env.start()
        dispatch("", "scene_new", {"name": "Building"})
        dispatch("Building", "add", {"kind": "box", "params": {"w": 300, "d": 200, "h": 1000}})

    def tearDown(self):
        for name in ("Building", "Building_1", "Imported"):
            forget(name)
        self.env.stop()
        self.tmp.cleanup()

    def fake_convert(self, command, **kwargs):
        output = Path(command[command.index("--") + 1])
        output.write_text(json.dumps(load("Building").data))
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_collision_preserves_scene_and_cache(self):
        src = self.root / "Building.glb"
        src.write_bytes(b"fixture")
        original = load("Building")
        disk = (original.folder / "scene.json").read_bytes()
        with patch("backend.blend_import._validate_source"), patch("backend.blend_import._blender", return_value=Path("blender")), patch("backend.blend_import.subprocess.run", side_effect=self.fake_convert):
            result = import_blend(str(src))
        self.assertEqual(result["name"], "Building_1")
        self.assertEqual((original.folder / "scene.json").read_bytes(), disk)
        self.assertIs(load("Building"), original)
        self.assertTrue(Path(result["path"]).is_file())

    def test_failed_conversion_leaves_no_scene(self):
        src = self.root / "Broken.fbx"
        src.write_bytes(b"bad")
        with patch("backend.blend_import._validate_source"), patch("backend.blend_import._blender", return_value=Path("blender")), patch("backend.blend_import.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "bad format")):
            with self.assertRaisesRegex(ValueError, "bad format"):
                import_blend(str(src))
        self.assertFalse((self.root / "scenes" / "Broken").exists())

    def test_failure_to_save_cleans_only_new_scene(self):
        src = self.root / "Building.glb"
        src.write_bytes(b"fixture")
        with patch("backend.blend_import._validate_source"), patch("backend.blend_import._blender", return_value=Path("blender")), patch("backend.blend_import.subprocess.run", side_effect=self.fake_convert), patch("backend.blend_import.save_forge", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                import_blend(str(src))
        self.assertTrue((self.root / "scenes" / "Building").is_dir())
        self.assertFalse((self.root / "scenes" / "Building_1").exists())

    def test_unsupported_format_rejected_before_conversion(self):
        src = self.root / "asset.uasset"
        src.write_bytes(b"asset")
        with patch("backend.blend_import.subprocess.run") as run:
            with self.assertRaises(ValueError):
                import_blend(str(src))
            run.assert_not_called()

    def test_resource_validation(self):
        from .blend_import import _validate_source
        source = self.root / "asset.gltf"
        for uri in ("../outside.bin", "https://example.com/a.bin", "file:///a.bin", "%2e%2e/out.bin", "C:/private.bin"):
            source.write_text(json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": uri}]}))
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                _validate_source(source)
        source.write_text(json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "missing.bin"}]}))
        with self.assertRaisesRegex(ValueError, "Missing"):
            _validate_source(source)
        broken = self.root / "broken.glb"
        broken.write_bytes(b"not a GLB")
        with self.assertRaisesRegex(ValueError, "Invalid GLB"):
            _validate_source(broken)
        ascii_fbx = self.root / "ascii.fbx"
        ascii_fbx.write_text("; FBX 7.4.0 project file")
        with self.assertRaisesRegex(ValueError, "ASCII FBX"):
            _validate_source(ascii_fbx)

    def test_preview_does_not_create_scene(self):
        source = self.root / "Building.glb"
        source.write_bytes(b"fixture")
        with patch("backend.blend_import._validate_source"), patch("backend.blend_import._blender", return_value=Path("blender")), patch("backend.blend_import.subprocess.run", side_effect=self.fake_convert):
            result = import_blend(str(source), preview=True)
        self.assertEqual(result["dimensions_cm"], [300, 200, 1000])
        self.assertEqual(result["triangles"], 12)
        self.assertFalse((self.root / "scenes" / "Building_1").exists())

    def test_reflection_preserves_normal_and_winding(self):
        from .kernel.mesh import Mesh
        mesh = Mesh()
        mesh.verts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        mesh.add_face([0, 1, 2])
        mesh.attrs["nrm:source"] = [[0, 0, 1] for _ in mesh.verts]
        mesh.transform([0, 0, 0], [0, 0, 0], [-2, 3, 1])
        self.assertEqual(mesh.faces[0], [2, 1, 0])
        self.assertEqual(mesh.face_normal(0), [0, 0, 1])
        self.assertEqual(mesh.attrs["nrm:source"][0], [0, 0, 1])

    def test_existing_import_single_undo_redo(self):
        source = self.root / "Building.glb"
        source.write_bytes(b"fixture")
        before = len(load("Building").data["nodes"])
        with patch("backend.blend_import._validate_source"), patch("backend.blend_import._blender", return_value=Path("blender")), patch("backend.blend_import.subprocess.run", side_effect=self.fake_convert):
            dispatch("Building", "import_mesh", {"path": str(source), "scale": 2})
        after = len(load("Building").data["nodes"])
        self.assertGreater(after, before)
        dispatch("Building", "undo")
        self.assertEqual(len(load("Building").data["nodes"]), before)
        dispatch("Building", "redo")
        self.assertEqual(len(load("Building").data["nodes"]), after)
        forget("Building")
        self.assertEqual(len(load("Building").data["nodes"]), after)

    def test_cancel_job_before_commit(self):
        from . import jobs
        import time
        def work():
            while True:
                jobs.checkpoint("Converting")
                time.sleep(0.01)
        result = jobs.submit(work, {})
        self.assertFalse(result["done"])
        job = result["job"]
        jobs.cancel(job)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = jobs.poll(job)
            if not result["ok"] or result.get("done"):
                break
            time.sleep(0.01)
        self.assertFalse(result["ok"])
        self.assertIn("cancelled", result["error"])

    def test_glb_roundtrip_real_converter(self):
        try:
            _blender()
        except ValueError as exc:
            self.skipTest(str(exc))
        scene = load("Building")
        node = next(n for n in scene.data["nodes"] if n["type"] == "object")
        dispatch("Building", "select", {"object": node["id"]})
        dispatch("Building", "uv", {"mode": "box"})
        from PIL import Image
        textures = scene.folder / "textures"
        textures.mkdir(exist_ok=True)
        Image.new("RGB", (2, 2), (120, 180, 200)).save(textures / "paint.png")
        mat = dispatch("Building", "material_set", {"name": "Paint", "base_color": [0.4, 0.6, 0.8]})["material"]["id"]
        next(m for m in scene.data["materials"] if m["id"] == mat)["textures"] = {"base_color": "paint.png"}
        dispatch("Building", "material_assign", {"material": mat, "faces": [0]})
        exported = export_glbs(scene, "each", self.root / "exports")
        textured = self.root / "textured.glb"
        script = ("import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
                  f"bpy.ops.import_scene.gltf(filepath={exported[0]['glb']!r}); "
                  "mat = next(m for m in bpy.data.materials if m.name == 'Paint'); "
                  f"image = bpy.data.images.load({str(textures / 'paint.png')!r}); "
                  "image.pack(); tex = mat.node_tree.nodes.new('ShaderNodeTexImage'); tex.image = image; "
                  "shader = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'); "
                  "mat.node_tree.links.new(tex.outputs['Color'], shader.inputs['Base Color']); "
                  "obj = next(o for o in bpy.context.scene.objects if o.type == 'MESH'); "
                  "col = obj.data.color_attributes.active_color or obj.data.color_attributes.new(name='Color', type='FLOAT_COLOR', domain='POINT'); "
                  "[setattr(c, 'color', (0.2, 0.4, 0.6, 1.0)) for c in col.data]; "
                  f"bpy.ops.export_scene.gltf(filepath={str(textured)!r}, export_format='GLB', export_vertex_color='ACTIVE', export_all_vertex_colors=True)")
        subprocess.run([str(_blender()), "--background", "--factory-startup", "--disable-autoexec", "--python-expr", script],
                       check=True, capture_output=True, timeout=120)
        result = import_blend(str(textured), "Imported")
        imported = load(result["name"])
        objects = [n for n in imported.data["nodes"] if n["type"] == "object"]
        self.assertEqual(len(objects), 1)
        mesh = imported.world_mesh(objects[0])
        before = scene.world_mesh(node)
        for actual, expected in zip(mesh.bounds()[1], before.bounds()[1]):
            self.assertAlmostEqual(actual, expected, delta=0.1)
        self.assertEqual(mesh.tri_count(), before.tri_count())
        self.assertTrue(imported.base_mesh(objects[0]).uvs)
        self.assertEqual(len(mesh.attrs["nrm:source"]), len(mesh.verts))
        self.assertEqual(len(mesh.attrs["col:color"]), len(mesh.verts))
        self.assertAlmostEqual(mesh.attrs["col:color"][0][0], 0.2, delta=0.01)
        self.assertAlmostEqual(sum(v*v for v in mesh.attrs["nrm:source"][0]), 1, delta=0.001)
        self.assertEqual(len(set(mesh.face_mat)), 2)
        payload = imported.payload()["prototypes"][0]
        self.assertEqual(len(payload["material_slots"]), 2)
        self.assertEqual(sum(g[1] for g in payload["groups"]), mesh.tri_count() * 3)
        self.assertIn("ub", payload)
        self.assertIn("nb", payload)
        self.assertIn("vcb", payload)
        images = [m["textures"]["base_color"] for m in imported.data["materials"] if m.get("textures", {}).get("base_color")]
        self.assertTrue(images)
        self.assertTrue(all((imported.folder / "textures" / image).is_file() for image in images))

    def test_fbx_and_gltf_real_converter(self):
        try:
            blender = _blender()
        except ValueError as exc:
            self.skipTest(str(exc))
        exported = export_glbs(load("Building"), "each", self.root / "exports")[0]["glb"]
        fbx = self.root / "reference.fbx"
        gltf = self.root / "reference.gltf"
        script = ("import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
                  f"bpy.ops.import_scene.gltf(filepath={exported!r}); "
                  f"bpy.ops.export_scene.fbx(filepath={str(fbx)!r}); "
                  f"bpy.ops.export_scene.gltf(filepath={str(gltf)!r}, export_format='GLTF_SEPARATE')")
        subprocess.run([str(blender), "--background", "--factory-startup", "--disable-autoexec", "--python-expr", script],
                       check=True, capture_output=True, timeout=120)
        for source in (fbx, gltf):
            result = import_blend(str(source), "Imported")
            scene = load(result["name"])
            nodes = [n for n in scene.data["nodes"] if n["type"] == "object"]
            self.assertEqual(len(nodes), 1)
            mesh = scene.world_mesh(nodes[0])
            low, high = mesh.bounds()
            self.assertAlmostEqual(high[2] - low[2], 1000, delta=0.1)
            forget(result["name"])


if __name__ == "__main__":
    unittest.main()
