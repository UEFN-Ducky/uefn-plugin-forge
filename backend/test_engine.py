"""Embedded Blender engine: provisioning, warm worker, bridge round trips and the HQ registry."""
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from .engine import bridge, client, runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-engine-test-")
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"FORGE_ENGINE_DIR": str(self.root / "engine")})
        self.env.start()
        os.environ.pop("FORGE_ENGINE_PYTHON", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_status_missing_then_ready(self):
        self.assertEqual(runtime.engine_status()["state"], "missing")
        exe = self.root / "engine" / "py" / "python.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.assertEqual(runtime.engine_status()["state"], "missing")
        (self.root / "engine" / "ready.json").write_text(json.dumps({"version": runtime.VERSION}))
        self.assertEqual(runtime.engine_status(), {"state": "ready", "version": runtime.VERSION, "progress": 100, "blender": ""})
        with patch.dict(os.environ, {"FORGE_ENGINE_PYTHON": str(self.root / "nope.exe")}):
            self.assertEqual(runtime.engine_status()["state"], "error")
        with patch.dict(os.environ, {"FORGE_ENGINE_PYTHON": str(exe)}):
            self.assertEqual(runtime.install()["state"], "ready")

    def test_download_verifies_checksum(self):
        src = self.root / "payload.bin"
        src.write_bytes(b"forge" * 1000)
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        dest = self.root / "out.bin"
        seen = []
        runtime.download(src.as_uri(), sha, dest, seen.append)
        self.assertEqual(dest.read_bytes(), src.read_bytes())
        self.assertEqual(sum(seen), 5000)
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            runtime.download(src.as_uri(), "0" * 64, self.root / "bad.bin")
        self.assertFalse((self.root / "bad.bin.part").exists())


class HelpTests(unittest.TestCase):
    def test_hq_help_per_op(self):
        from . import hq
        top = hq.help_text()
        self.assertLessEqual(len(top), 1500)
        self.assertFalse(top.endswith("..."), "top-level help was cut")
        for name in hq.HQ_OPS:
            self.assertIn(name, top)
            text = hq.help_text(name)
            self.assertLessEqual(len(text), 400, name)
            self.assertTrue(text.startswith(hq._SIGS[name]), name + " signature over 400 chars")
        with self.assertRaisesRegex(ValueError, "Did you mean"):
            hq.help_text(next(iter(hq.HQ_OPS), "x") + "z")


_READY = runtime.engine_status()["state"] == "ready"


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class WorkerTests(unittest.TestCase):
    def test_ping(self):
        self.assertTrue(client.call("ping")["blender"])
        self.assertIn("import_file", client._worker.ops)
        self.assertEqual(client._worker.broken, {})
        start = time.perf_counter()
        client.call("ping")
        self.assertLess(time.perf_counter() - start, 0.05)

    def test_crash_restart(self):
        client.call("ping")
        killer = threading.Timer(0.3, client._worker.proc.kill)
        killer.start()
        with self.assertRaisesRegex(client.EngineError, "stopped during sleep"):
            client.call("sleep", {"seconds": 30})
        self.assertTrue(client.call("ping")["blender"])

    def test_cancel(self):
        client.call("ping")
        flag = threading.Event()
        threading.Timer(0.2, flag.set).start()
        start = time.perf_counter()
        with self.assertRaisesRegex(client.EngineError, "Cancelled sleep"):
            client.call("sleep", {"seconds": 30}, cancel=flag.is_set)
        self.assertLess(time.perf_counter() - start, 5)
        with self.assertRaisesRegex(client.EngineError, "timed out"):
            client.call("sleep", {"seconds": 30}, timeout=0.3)
        with self.assertRaisesRegex(client.EngineError, "No engine op 'nope'"):
            client.call("nope")
        self.assertTrue(client.call("ping")["blender"])

    def test_import_on_worker(self):
        from .blend_import import import_blend
        from .scene import dispatch, forget, load
        from .uefn import export_glbs
        with tempfile.TemporaryDirectory(prefix="forge-engine-import-") as tmp, \
                patch.dict(os.environ, {"FORGE_SCENES_DIR": tmp + "/scenes"}), \
                patch("backend.blend_import._blender", side_effect=ValueError("no Blender")):
            dispatch("", "scene_new", {"name": "Crate"})
            dispatch("Crate", "add", {"kind": "box", "params": {"w": 100, "d": 100, "h": 50}})
            glb = export_glbs(load("Crate"), "each", Path(tmp) / "exports")[0]["glb"]
            result = import_blend(glb, "Imported")
            mesh = load(result["name"]).world_mesh(next(n for n in load(result["name"]).data["nodes"] if n["type"] == "object"))
            low, high = mesh.bounds()
            self.assertAlmostEqual(high[2] - low[2], 50, delta=0.1)
            forget("Crate")
            forget(result["name"])

    @classmethod
    def tearDownClass(cls):
        client.stop()


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-bridge-test-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        from .scene import forget
        for name in ("Box", "Hero", "Blob"):
            forget(name)
        self.env.stop()
        self.tmp.cleanup()

    def close(self, actual, expected, path=""):
        """Equal, numbers to 1e-4."""
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            self.assertAlmostEqual(actual, expected, delta=1e-4, msg=path)
        elif isinstance(expected, dict):
            self.assertEqual(sorted(actual), sorted(expected), path)
            for key in expected:
                self.close(actual[key], expected[key], path + "/" + str(key))
        elif isinstance(expected, (list, tuple)):
            self.assertEqual(len(actual), len(expected), path)
            for i, (a, e) in enumerate(zip(actual, expected)):
                self.close(a, e, path + "/" + str(i))
        else:
            self.assertEqual(actual, expected, path)

    def echo(self, scene, ids):
        result, data = bridge.run(scene, "bridge_echo", ids)
        return data

    def test_roundtrip_box_uv_2mats(self):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Box"})
        nid = dispatch("Box", "add", {"kind": "box", "params": {"w": 120, "d": 80, "h": 60}})["id"]
        dispatch("Box", "select", {"object": nid})
        dispatch("Box", "uv", {"mode": "box"})
        mat = dispatch("Box", "material_set", {"name": "Paint", "base_color": [0.4, 0.6, 0.8]})["material"]["id"]
        dispatch("Box", "material_assign", {"material": mat, "faces": [0, 1]})
        scene = load("Box")
        ref = scene.local_mesh(scene.node(nid))
        data = self.echo(scene, [nid])
        back = data["objects"][0]["mesh"]
        self.close(back.to_dict(), ref.to_dict())
        self.assertEqual(len(set(back.face_mat)), 2)
        self.close({m["id"]: m for m in data["materials"]}, {m["id"]: m for m in scene.data["materials"] if m["id"] in ref.face_mat})

    def test_roundtrip_biped_walk(self):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Hero"})
        nid = dispatch("Hero", "add", {"kind": "capsule", "params": {"radius": 25, "height": 180}})["id"]
        dispatch("Hero", "select", {"object": nid})
        dispatch("Hero", "rig", {"action": "template", "template": "biped"})
        dispatch("Hero", "rig", {"action": "bind", "auto": True, "ids": [nid]})
        clip = dispatch("Hero", "anim", {"action": "generate", "kind": "walk"})["clip"]
        scene = load("Hero")
        node = scene.node(nid)
        mesh = scene.local_mesh(node)
        for k, name in enumerate(("smile", "blink", "puff")):
            mesh.attrs["sk:" + name] = [[0.0, 0.0, (k + 1) * v[2] / 100.0] for v in mesh.verts]
        scene.write_mesh(node, mesh)
        dispatch("Hero", "anim", {"action": "key", "clip": clip, "keys": [
            {"shape": "smile", "frame": 0, "value": 0}, {"shape": "smile", "frame": 15, "value": 1, "interp": "linear"},
            {"shape": "blink", "frame": 5, "value": 0.5, "interp": "step"}, {"shape": "puff", "frame": 30, "value": 0.25}]})
        rig = scene.data["rigs"][0]
        self.assertEqual((len(rig["bones"]), len(rig["actions"]), len(rig["actions"][0]["morphs"])), (23, 1, 3))
        data = self.echo(scene, [nid])
        self.close(data["rigs"], [rig])
        obj = data["objects"][0]
        self.assertEqual(obj["skin"], node["skin"])
        self.assertTrue(obj["auto_weights"])
        self.assertFalse([k for k in obj["mesh"].attrs if k.startswith("wt:")])
        self.close(obj["mesh"].to_dict(), scene.local_mesh(node).to_dict())

    def test_roundtrip_shapekeys(self):
        from .scene import dispatch, load
        dispatch("", "scene_new", {"name": "Blob"})
        nid = dispatch("Blob", "add", {"kind": "sphere", "params": {"radius": 50}, "pos": [10, 20, 30]})["id"]
        scene = load("Blob")
        node = scene.node(nid)
        mesh = scene.local_mesh(node)
        n = len(mesh.verts)
        mesh.attrs["sk:up"] = [[0.0, 0.0, 5.0] for _ in range(n)]
        mesh.attrs["sk:wide"] = [[v[0] * 0.1, 0.0, 0.0] for v in mesh.verts]
        mesh.attrs["sk:twist"] = [[-v[1] * 0.2, v[0] * 0.2, 0.0] for v in mesh.verts]
        mesh.attrs["col:color"] = [[i / n, 0.5, 0.25, 1.0] for i in range(n)]
        mesh.attrs["wt:spine"] = [i / n for i in range(n)]
        scene.write_mesh(node, mesh)
        centre = [0.0, 0.0, 50.0]
        normals = []
        for v in mesh.verts:
            d = [v[i] - centre[i] for i in range(3)]
            length = sum(c * c for c in d) ** 0.5
            normals.append([c / length for c in d])
        scene.proto(node)["mesh"]["attrs"]["nrm:source"] = normals
        ref = scene.local_mesh(node)
        data = self.echo(scene, [nid])
        back = data["objects"][0]["mesh"]
        self.close(back.to_dict(), ref.to_dict())
        self.assertEqual(bridge.apply(scene, data), [nid])
        self.close(scene.local_mesh(node).attrs["sk:twist"], ref.attrs["sk:twist"])

    def test_hq_registry(self):
        from . import hq
        from .scene import dispatch, load

        @hq.op("test_echo", "test_echo(id) round-trips one object")
        def _echo(scene, p):
            result, changed = hq.engine(scene, "bridge_echo", [p["id"]])
            return {"ids": changed, "tris": scene.local_mesh(scene.node(p["id"])).tri_count(), "note": "x" * 500,
                    "warnings": ["w" * 400]}

        try:
            dispatch("", "scene_new", {"name": "Box"})
            nid = dispatch("Box", "add", {"kind": "box"})["id"]
            scene = load("Box")
            self.assertEqual(hq.run_hq(scene, "test_echo", {"id": nid}), {"ok": True, "ids": [nid], "tris": 12})
            top = hq.run_hq(scene, "help")["summary"]
            self.assertIn("test_echo", top)
            self.assertLessEqual(len(top), 1500)
            self.assertIn("test_echo(id)", hq.run_hq(scene, "help", {"op": "test_echo"})["summary"])
            self.assertIn("note", hq.run_hq(scene, "test_echo", {"id": nid, "verbose": True}))
            with self.assertRaisesRegex(ValueError, "Did you mean 'test_echo'"):
                hq.run_hq(scene, "test_ecko")
            with patch.object(runtime, "engine_status", return_value={"state": "missing", "version": "", "progress": 0}), \
                    patch.object(runtime, "install", return_value={"state": "installing", "version": "", "progress": 42}):
                self.assertEqual(hq.run_hq(scene, "test_echo", {"id": nid})["progress"], 42)
        finally:
            hq.HQ_OPS.pop("test_echo", None)


if __name__ == "__main__":
    unittest.main()
