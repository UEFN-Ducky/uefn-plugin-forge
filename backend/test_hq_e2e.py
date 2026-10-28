"""End to end through the AI path: every HQ op as a forge_batch step, a full character, export, send.

Needs the Blender engine (runtime.install()); skips without it. Run: py -3 -m backend.test_hq_e2e
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from . import hq, tools
from .engine import client, runtime
from .scene import forget, load

_READY = runtime.engine_status()["state"] == "ready"


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):  # register_pipeline_node etc.
        return lambda *a, **k: (lambda fn: fn)


def _listener(calls):
    """Answers like UEFN for a send (same shapes as test_forge.test_send)."""
    def listener(command, params, timeout=None):
        calls.append((command, params))
        base = params.get("destination_path", "")
        if command == "import_asset":
            stem = Path(params["source_file"]).stem
            return {"imported": [f"{base}/StaticMeshes/{stem}.{stem}"]}
        if command == "create_material_instance":
            return {"material_instance_path": f"{params['folder']}/{params['asset_name']}.{params['asset_name']}"}
        if command == "forge_import_skeletal":
            stem = Path(params.get("source_file") or params.get("fbx") or "SK_Hero").stem
            return {"skeletal_mesh": f"{base}/{stem}", "skeleton": f"{base}/{stem}_Skeleton",
                    "physics_asset": f"{base}/{stem}_PhysicsAsset", "animations": [f"{base}/AS_{stem}_Run"]}
        return {
            "get_project_info": {"content_root": "/Island/"},
            "forge_build_master": {"material_path": "/Island/Forge/Materials/M_Forge_Master.M_Forge_Master"},
            "forge_build_blend_master": {"material_path": "/Island/Forge/Materials/M_Forge_Blend.M_Forge_Blend"},
            "validate_uefn_asset": {"ok": True, "valid": True, "errors": []},
        }.get(command, {"ok": True})
    return listener


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-e2e-")
        cls.patches = [
            patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")}),
            patch.object(tools, "_attach_captures", lambda r: r),  # no Ducky frontend here
        ]
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

    def batch(self, name, steps, **kw):
        out = self.t["forge_batch"](name=name, steps=steps, **kw)
        self.assertTrue(out.get("ok", True), out)
        return out

    def hero(self, name):
        """humanoid_base: 6 parts, biped rig, idle clip. Returns {part name: id}."""
        self.batch(name, [{"tool": "forge_hq", "op": "recipe", "params": {"recipe": "humanoid_base"}}])
        return {n["name"]: n["id"] for n in load(name).data["nodes"] if n.get("type") == "object"}

    def test_every_op_through_batch(self):
        name = self.scene("E2EOps")
        parts = self.hero(name)
        crate = self.batch(name, [{"tool": "forge_add", "kind": "box", "object_name": "Crate", "pos": [200, 0, 0],
                                   "params": {"w": 60, "d": 60, "h": 60}}])["ids"][0]
        head, torso = parts["Head"], parts["Torso"]
        preset = next(iter(__import__("backend.hq.materials", fromlist=["presets"]).presets()))
        # Order matters only where an op reads another's output (hair before hair_cards).
        cases = [
            ("recipe", {"recipe": "barrel"}),
            ("material", {"material": "Wood", "preset": preset, "id": crate}),
            ("modifiers", {"id": crate, "stack": ["bevel"]}),
            ("remesh", {"id": crate, "voxel": 5}),
            ("retopo", {"id": crate, "faces": 500}),
            ("sculpt_hd", {"id": crate, "levels": 1}),
            ("lods", {"id": crate}),
            ("cleanup", {"id": crate, "faces": 500}),
            ("bake", {"id": crate, "size": 64}),
            ("hair", {"id": head, "style": "short"}),
            ("hair_cards", {"id": head, "cards": 40}),
            ("fur_shells", {"id": head, "layers": 2}),
            ("fur_bake", {"id": head, "size": 64}),
            ("face_shapes", {"ids": [head], "set": "basic12"}),
            ("auto_weights", {"ids": [torso]}),
            ("clip", {"kind": "run"}),
            ("pose", {"clip": "Run", "frame": 4, "controls": {"hand_l": {"ik": [30, -40, 120]}}}),
            ("cloth", {"preset": "cape", "collide": torso, "frames": 8}),
            ("rig_template", {"template": "tail_chain", "ids": [crate], "count": 3}),
            ("creature", {"kind": "big_cat", "length": 120}),
            ("export", {"target": "generic"}),
        ]
        # These spend the person's Tripo/Meshy credits; test_hq_generate covers them with a mocked provider.
        paid = {"generate", "creature_from_prompt"}
        registered = {n for n, fn in hq.HQ_OPS.items() if fn.__module__.startswith("backend.hq.")} - paid
        self.assertEqual(registered - {op for op, _ in cases}, set(), "every registered HQ op needs a case here")
        failed, timing = {}, {}
        for op, params in cases:
            start = time.perf_counter()
            try:
                out = self.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": op, "params": params}])
            except Exception as exc:
                failed[op] = str(exc)[:300]
                continue
            timing[op] = round(time.perf_counter() - start, 1)
            if not out.get("ok", True) or len(json.dumps(out)) > 300:
                failed[op] = json.dumps(out)[:300]
        self.assertEqual(failed, {}, "ops that failed or broke the 300-char budget")
        print("\n%d HQ ops ok through forge_batch: %s" % (len(cases), timing))

    def test_full_character(self):
        name = self.scene("E2EHero")
        parts = self.hero(name)
        head, torso = parts["Head"], parts["Torso"]
        unskinned = [n["name"] for n in load(name).data["nodes"] if n.get("type") == "object" and not n.get("skin")]
        self.assertEqual(unskinned, [], "humanoid_base promises a skinned rig")
        # ponytail: two calls, not one: a batch can't name "the Head the recipe made" ($0.ids has no index).
        out = self.batch(name, [
            {"tool": "forge_hq", "op": "material", "params": {"material": "Skin", "preset": "skin_dark"}},
            {"tool": "forge_hq", "op": "hair", "id": head, "params": {"style": "ponytail"}},
            {"tool": "forge_hq", "op": "hair_cards", "id": head},
            {"tool": "forge_hq", "op": "face_shapes", "params": {"ids": [head], "set": "basic12"}},
            {"tool": "forge_hq", "op": "clip", "params": {"kind": "run"}},
            {"tool": "forge_hq", "op": "bake", "id": torso, "params": {"size": 512}},
            {"tool": "forge_hq", "op": "lods", "id": torso},
            # A static prop the hero carries: UEFN takes LODs on static meshes (the skeletal import has none).
            {"tool": "forge_add", "kind": "box", "object_name": "Shield", "pos": [60, -20, 100],
             "params": {"w": 40, "d": 6, "h": 50}},
            {"tool": "forge_hq", "op": "lods", "id": "$7"},
        ], look="three")
        self.assertTrue(out.get("seen"), out)
        self.assertNotIn("look_error", out)
        from PIL import Image
        with Image.open(out["path"]) as image:
            self.assertLessEqual(max(image.size), 768)
        scene = load(name)
        rig = scene.data["rigs"][0]
        self.assertIn("Run", [c["name"] for c in rig["actions"]])
        self.assertTrue(scene.data.get("grooms"), "hair groom stored")
        self.assertIn("lods", scene.node(torso).get("hq") or {})
        textures = {k for m in scene.data["materials"] for k in (m.get("textures") or {})}
        self.assertIn("base_color", textures)
        tris = sum(scene.world_mesh(n).tri_count() for n in scene.data["nodes"] if n.get("type") == "object")
        print("\ncharacter: %d tris, %d objects, clips %s, textures %s, lods %s" % (
            tris, len(parts), [c["name"] for c in rig["actions"]], sorted(textures), scene.node(torso)["hq"]["lods"].get("ratios")))

        for target in ("ue5", "godot"):
            got = self.t["forge_hq"](name=name, op="export", params={"target": target, "verify": True, "verbose": True})
            self.assertTrue(got.get("ok", True), got)
            self.assertFalse([w for w in got.get("warnings") or [] if "reimport differs" in w], got)
            stats = got["stats"]
            self.assertGreaterEqual(stats["skins"], 1, got)
            self.assertGreaterEqual(stats["clips"], 2, got)  # idle + run
            self.assertGreaterEqual(stats["morphs"], 1, got)
            self.assertTrue(Path(got["path"]).is_file(), got)

        calls = []
        self.api.listener = _listener(calls)
        sent = self.t["forge_send_to_uefn"](name=name, verbose=True)
        commands = [c for c, _ in calls]
        meshes = [a.get("mesh", "") for a in sent.get("assets") or []]
        self.assertTrue(any("/SK_" in m for m in meshes), sent)
        skeletal = next(p for c, p in calls if c == "forge_import_skeletal")
        self.assertTrue(skeletal, calls)
        self.assertTrue(any(c == "create_material_instance" and p["asset_name"].startswith("MI_") for c, p in calls), commands)
        options = [p for c, p in calls if c == "forge_set_mesh_options"]
        self.assertTrue(options, "LODs from lods() must reach UEFN")
        self.assertEqual(options[0]["lod_count"], 4, options)
        self.assertIn("/AS_", json.dumps(sent), "the run clip ships as an AS_ animation")


if __name__ == "__main__":
    unittest.main()
