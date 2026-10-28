"""generate / cleanup / creature_from_prompt with the provider mocked and the real Blender engine.

Run: py -3 -m backend.test_hq_generate   (skips without the engine; runtime.install() provisions it)
"""
import json
import math
import os
import random
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from . import tools
from .engine import client, runtime
from .hq import generate as gen
from .scene import forget, load
from .test_hq_e2e import _Api

_READY = runtime.engine_status()["state"] == "ready"
PROMPT = "adult male lion, standing"


def _png(size=8):
    rows = b"".join(b"\x00" + bytes([200, 150, 60]) * size for _ in range(size))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def write_glb(path, seg=40, ring=20):
    """A noisy, textured, lion-sized ellipsoid (metres, long axis Y) with two triangles missing: non-manifold on purpose."""
    rnd = random.Random(3)
    pos, uv = [], []
    for i in range(ring + 1):
        theta = math.pi * i / ring
        for j in range(seg + 1):
            phi = math.tau * j / seg
            k = 1 + rnd.uniform(-0.03, 0.03)
            pos += [0.3 * k * math.sin(theta) * math.cos(phi), 1.0 * k * math.sin(theta) * math.sin(phi), 0.5 * k * math.cos(theta)]
            uv += [j / seg, i / ring]
    idx = []
    for i in range(ring):
        for j in range(seg):
            a, b = i * (seg + 1) + j, i * (seg + 1) + j + 1
            idx += [a, b, a + seg + 1, b, b + seg + 1, a + seg + 1]
    del idx[300:306]  # two holes
    image = _png()
    blobs = [struct.pack("<%df" % len(pos), *pos), struct.pack("<%df" % len(uv), *uv), struct.pack("<%dI" % len(idx), *idx), image]
    offsets, binary = [], b""
    for blob in blobs:
        offsets.append(len(binary))
        binary += blob + b"\x00" * (-len(blob) % 4)
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "lion"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"name": "fur", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"source": 0}], "images": [{"bufferView": 3, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(pos) // 3, "type": "VEC3",
             "min": [min(pos[a::3]) for a in range(3)], "max": [max(pos[a::3]) for a in range(3)]},
            {"bufferView": 1, "componentType": 5126, "count": len(uv) // 2, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5125, "count": len(idx), "type": "SCALAR"}],
        "bufferViews": [{"buffer": 0, "byteOffset": o, "byteLength": len(b)} for o, b in zip(offsets, blobs)],
        "buffers": [{"byteLength": len(binary)}],
    }
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    Path(path).write_bytes(b"glTF" + struct.pack("<II", 2, 28 + len(text) + len(binary))
                           + struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def open_edges(scene, nid):
    """Edges that do not have exactly two faces."""
    count = {}
    for face in scene.local_mesh(scene.node(nid)).faces:
        for a, b in zip(face, face[1:] + face[:1]):
            count[(min(a, b), max(a, b))] = count.get((min(a, b), max(a, b)), 0) + 1
    return sum(n != 2 for n in count.values())


class Provider:
    """Stands in for the Tripo/Meshy plugin tools; counts calls so a retry shows."""
    def __init__(self, glb, reported=20.0, balances=(100.0, 80.0)):
        self.glb, self.reported, self.balances, self.calls = glb, reported, list(balances), []

    def __call__(self, tool, args):
        self.calls.append((tool, args))
        if tool.endswith("_balance"):
            return {"balance": self.balances.pop(0)}
        result = {"task_id": "t1", "files": {"model": str(self.glb)}}
        if self.reported is not None:
            result["credits_consumed"] = self.reported
        return result


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class Generate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-gen-")
        cls.glb = Path(cls.tmp.name) / "lion.glb"
        write_glb(cls.glb)
        cls.patches = [patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")}),
                       patch.object(tools, "_attach_captures", lambda r: r)]
        for p in cls.patches:
            p.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns

    @classmethod
    def tearDownClass(cls):
        client.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def scene(self, name):
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        return name

    def run_op(self, name, op, params, provider=None):
        with patch.object(gen, "_call", provider or Provider(self.glb)):
            out = self.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": op, "params": params}])
        self.assertTrue(out.get("ok", True), out)
        self.assertLessEqual(len(json.dumps(out)), 600, out)
        return out

    def test_generate_imports_mock_glb(self):
        name = self.scene("GenImport")
        provider = Provider(self.glb)
        out = self.run_op(name, "generate", {"prompt": PROMPT}, provider)
        scene = load(name)
        lion = next(n for n in scene.data["nodes"] if n.get("name") == "Lion_gen")
        self.assertIn(lion["id"], out["ids"])
        self.assertGreater(scene.local_mesh(lion).tri_count(), 1000)
        self.assertTrue(any((m.get("textures") or {}).get("base_color") for m in scene.data["materials"]), "texture imported")
        paid = [c for c in provider.calls if c[0] == "tripo_text_to_3d"]
        self.assertEqual(len(paid), 1, "one paid call, never retried")
        self.assertIn(PROMPT, paid[0][1]["prompt"])
        with self.assertRaises(ValueError):  # a bad provider is refused before anything is spent
            self.run_op(name, "generate", {"prompt": PROMPT, "provider": "nope"}, provider)
        self.assertEqual(len([c for c in provider.calls if c[0] == "tripo_text_to_3d"]), 1)

    def test_generate_reports_cost(self):
        name = self.scene("GenCost")
        out = self.run_op(name, "generate", {"prompt": PROMPT}, Provider(self.glb, reported=20.0))
        self.assertIn("20 credits", out["summary"])
        name = self.scene("GenCostBalance")  # no cost in the result: the balance drop says it
        out = self.run_op(name, "generate", {"prompt": PROMPT, "provider": "meshy"}, Provider(self.glb, reported=None))
        self.assertIn("20 credits", out["summary"])

    def test_cleanup_chain_on_fixture(self):
        name = self.scene("GenCleanup")
        made = self.run_op(name, "generate", {"prompt": PROMPT})
        lion = load(name).data["nodes"]
        nid = next(n["id"] for n in lion if n.get("name") == "Lion_gen")
        self.assertGreater(open_edges(load(name), nid), 0, "fixture must start non-manifold")
        self.run_op(name, "cleanup", {"id": nid, "faces": 800, "height": 100, "size": 256})
        scene = load(name)
        mesh = scene.local_mesh(scene.node(nid))
        self.assertEqual(open_edges(scene, nid), 0, "watertight")
        self.assertGreaterEqual(sum(len(f) == 4 for f in mesh.faces) / len(mesh.faces), 0.9)
        lo, hi = scene.world_mesh(scene.node(nid)).bounds()
        self.assertAlmostEqual(hi[2] - lo[2], 100, delta=1)
        mats = [m for m in scene.data["materials"] if (m.get("textures") or {}).get("base_color")]
        self.assertTrue(mats and (scene.folder / "textures" / Path(mats[0]["textures"]["base_color"]).name).is_file(), "baked texture on disk")
        self.assertEqual([n for n in scene.data["nodes"] if str(n.get("name")).endswith("_high")], [], "source removed")
        self.assertGreater(made["ids"].count(nid), 0)

    def test_creature_from_prompt_mocked(self):
        name = self.scene("GenCreature")
        self.run_op(name, "creature_from_prompt", {"prompt": PROMPT, "kind": "quadruped", "faces": 800, "height": 100})
        scene = load(name)
        rig = scene.data["rigs"][0]
        self.assertEqual(len(rig["bones"]), 25)
        self.assertIn("Walk", [c["name"] for c in rig["actions"]])
        lion = next(n for n in scene.data["nodes"] if n.get("name") == "Lion_gen")
        self.assertEqual((lion.get("skin") or {}).get("rig"), rig["id"], "skinned")


class Refusals(unittest.TestCase):
    def test_errors_say_how_to_fix(self):
        with self.assertRaisesRegex(ValueError, "Pass prompt"):
            gen.generate(None, {})
        with self.assertRaisesRegex(ValueError, "Providers: tripo, meshy"):
            gen.generate(None, {"prompt": "x", "provider": "x"})
        with self.assertRaisesRegex(ValueError, "Poses: T, A, stand"):
            gen.generate(None, {"prompt": "x", "pose": "Z"})


if __name__ == "__main__":
    unittest.main()
