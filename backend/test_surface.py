"""Surface checks: forge_hq wiring, budgets for descriptions/results, look=, defaults, error hints. No UEFN.

Run: py -3 -m backend.test_surface
"""
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from . import hq, tools
from .engine import runtime
from .scene import forget, load


DOCS_BEFORE = 9165  # chars of all 44 forge_* docstrings before the one-line trim


REPRESENTATIVE = [  # 20 steps of a typical prop; "$0" is the first box
    {"tool": "forge_add", "kind": "box", "scale": [60, 60, 60], "object_name": "Crate"},
    {"tool": "forge_add", "kind": "cylinder", "pos": [100, 0, 0], "object_name": "Barrel"},
    {"tool": "forge_add", "kind": "sphere", "pos": [-100, 0, 0], "count": 5},
    {"tool": "forge_edit", "id": "$0", "rot": [0, 0, 15]},
    {"tool": "forge_duplicate", "id": "$0"},
    {"tool": "forge_material_set", "material": "Wood", "base_color": [0.45, 0.3, 0.15], "roughness": 0.8},
    {"tool": "forge_material_set", "material": "Steel", "metallic": 1.0},
    {"tool": "forge_select", "object": "$0"},
    {"tool": "forge_material_assign", "material": "Wood", "id": "$0"},
    {"tool": "forge_uv_project", "mode": "smart", "id": "$0"},
    {"tool": "forge_mesh_op", "op": "subdivide", "smooth": True, "levels": 2},
    {"tool": "forge_sculpt", "id": "$0", "brush": "noise", "strength": 1},
    {"tool": "forge_mesh_op", "op": "shade", "smooth": True},
    {"tool": "forge_paint", "id": "$0", "mode": "texture", "material": "Wood", "fill": "all", "color": "#886644", "size": 64},
    {"tool": "forge_shape", "id": "$0", "action": "add", "key": "Squash"},
    {"tool": "forge_layer", "action": "add", "object_name": "Props"},
    {"tool": "forge_modifier", "action": "add", "type": "mirror"},
    {"tool": "forge_modifier", "action": "apply"},
    {"tool": "forge_uv_transform", "id": "$0", "rotate": 90},
    {"tool": "forge_fix", "id": "$0"},
]


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):  # register_pipeline_node etc.
        return lambda *a, **k: (lambda fn: fn)


@hq.op("surface_dummy", "surface_dummy(id) test op: renames the object")
def _dummy(scene, p):
    node = scene.node(p["id"])
    node["name"] = "HQ_" + node["name"]
    return {"ids": [node["id"]], "summary": "dummy " + node["name"]}


@hq.op("lods" if "lods" not in hq.HQ_OPS else "surface_lods", "test op: returns node_data")
def _lods(scene, p):
    return {"ids": [p["id"]], "summary": "lods",
            "node_data": {"ratios": [1, .5, .25, .1], "tris": [40000, 20000, 10000, 4000], "nanite": False}}


LODS_OP = next(n for n, f in hq.HQ_OPS.items() if f is _lods)


class Surface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-surface-")
        cls.patches = [
            patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")}),
            patch.object(tools, "_attach_captures", lambda r: r),  # no Ducky frontend here
            patch.object(runtime, "engine_status", lambda: {"state": "ready"}),
        ]
        for p in cls.patches:
            p.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def scene(self, name):
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        return name

    def names(self, name):
        return sorted(n["name"] for n in load(name).data["nodes"])

    def test_description_budget(self):
        docs = {n: inspect.getdoc(f) or "" for n, f in self.t.items() if n.startswith("forge_")}
        long = {n: len(d) for n, d in docs.items() if len(d) > 160 or "\n" in d}
        self.assertFalse(long, "one line of 160 chars or less")
        self.assertLessEqual(sum(map(len, docs.values())), DOCS_BEFORE * 0.4)

    def test_result_budget(self):
        steps = REPRESENTATIVE
        s = self.scene("SurfBudget")
        self.first = "$0"
        for k, step in enumerate(steps):  # each op on its own, as the agent calls it
            args = {key: v for key, v in step.items() if key != "tool"}
            args = {key: (v.replace("$0", self.first) if isinstance(v, str) else v) for key, v in args.items()}
            got = self.t[step["tool"]](name=s, **args)
            if k == 0:
                self.first = got["ids"][0]
            self.assertLessEqual(len(json.dumps(got)), 300, (step["tool"], got))
        s2 = self.scene("SurfBudget2")
        got = self.t["forge_batch"](name=s2, steps=steps)
        self.assertLessEqual(len(json.dumps(got)), 600, got)
        self.assertNotIn("steps", got)
        self.assertIn("steps", self.t["forge_batch"](name=s2, steps=steps[:2], verbose=True))

    def test_look_one_image(self):
        from PIL import Image

        s = self.scene("SurfLook")
        got = self.t["forge_batch"](name=s, look="three", steps=[
            {"tool": "forge_add", "kind": "box", "params": {"w": 100, "d": 100, "h": 100}},
            {"tool": "forge_add", "kind": "sphere", "pos": [200, 0, 0]},
        ])
        paths = [v for k, v in got.items() if k in ("path", "paths")]
        self.assertEqual(len(paths), 1, got)
        with Image.open(got["path"]) as im:
            self.assertLessEqual(max(im.size), 768)
        self.assertRegex(got["seen"], r"^\d+x\d+x\d+ cm, \d+ tris, ")
        tiled = self.t["forge_edit"](name=s, id=got["ids"][0], pos=[0, 0, 10], look="ai")
        with Image.open(tiled["path"]) as im:
            self.assertEqual(max(im.size), 768)
        crop = self.t["forge_material_set"](name=s, material="Wood", look=[got["ids"][0]])
        self.assertTrue(crop["seen"].startswith("100x100x100 cm"), crop["seen"])
        with self.assertRaisesRegex(ValueError, "Use one of"):
            self.t["forge_add"](name=s, kind="box", look="sideways")

    def test_defaults_name_is_last_scene(self):
        s = self.scene("SurfLast")
        self.t["forge_add"](name=s, kind="box")
        self.assertEqual(self.t["forge_stats"]()["name"], s)
        self.assertEqual(len(self.t["forge_add"](kind="sphere")["ids"]), 1)
        self.assertEqual(len(load(s).data["nodes"]), 2)

    def test_defaults_target_is_last_added(self):
        s = self.scene("SurfTarget")
        self.t["forge_add"](name=s, kind="box")
        last = self.t["forge_add"](name=s, kind="cylinder")["ids"][0]
        load(s).data["selection"]["object"] = None
        self.assertEqual(self.t["forge_mesh_op"](name=s, op="subdivide", smooth=True)["ids"], [last])
        self.assertEqual(self.t["forge_uv_project"](name=s, mode="smart")["ids"], [last])

    def test_defaults_sculpt_subdivides(self):
        s = self.scene("SurfSculpt")
        box = self.t["forge_add"](name=s, kind="box")["ids"][0]
        got = self.t["forge_sculpt"](name=s, brush="inflate", points=[[0, 0, 512]], radius=200)
        self.assertIn("smooth-subdivided x2", " ".join(got["warnings"]))
        self.assertEqual(len(load(s).local_mesh(load(s).node(box)).verts), 98)
        again = self.t["forge_sculpt"](name=s, brush="inflate", points=[[0, 0, 512]], radius=200)
        self.assertNotIn("warnings", again, "a dense enough mesh is left alone")
        got = self.t["forge_material_set"](name=s, material="Blend", layers=["Default", "Default", "Default"])
        crate = self.t["forge_add"](name=s, kind="box", material="Blend")["ids"][0]
        got = self.t["forge_paint"](name=s, id=crate, mode="blend", mask="up")
        self.assertIn("subdivided x2", " ".join(got["warnings"]))
        lo, hi = load(s).local_mesh(load(s).node(crate)).bounds()
        self.assertEqual([round(hi[k] - lo[k]) for k in range(3)], [512, 512, 512], "blend paint keeps the shape")

    def test_defaults_texture_paint_unwraps(self):
        s = self.scene("SurfUnwrap")
        self.t["forge_add"](name=s, kind="box")
        self.t["forge_material_set"](name=s, material="Wood")
        self.t["forge_material_assign"](name=s, material="Wood")
        self.t["forge_uv_project"](name=s, mode="box")
        got = self.t["forge_paint"](name=s, mode="texture", fill="all", color="#886644", size=64)
        self.assertIn("smart-unwrapped", " ".join(got["warnings"]))
        got = self.t["forge_paint"](name=s, mode="texture", faces=[0], color="#ffffff", size=64)
        self.assertNotIn("warnings", got, "an unwrapped mesh is left alone")

    def test_defaults_shape_key_adds_rig(self):
        s = self.scene("SurfShape")
        box = self.t["forge_add"](name=s, kind="box")["ids"][0]
        self.t["forge_shape"](name=s, key="Squash")
        self.assertFalse(load(s).data.get("rigs"), "no rig until send: the agent may still rig it by hand")
        with patch.object(tools, "send_to_uefn", lambda *a, **k: {"ok": True}):
            got = self.t["forge_send_to_uefn"](name=s)
            self.assertIn("template=single", " ".join(got["warnings"]))
            self.assertEqual(load(s).node(box)["skin"]["rig"], load(s).data["rigs"][0]["id"])
            self.t["forge_add"](name=s, kind="sphere")
            self.t["forge_shape"](name=s, key="Puff")
            self.assertIn("action='bind'", " ".join(self.t["forge_send_to_uefn"](name=s)["warnings"]))

    def test_hq_payload_persists(self):
        s = self.scene("SurfHQData")
        box = self.t["forge_add"](name=s, kind="box")["ids"][0]
        got = self.t["forge_hq"](name=s, op=LODS_OP, id=box)
        self.assertNotIn("node_data", got)
        forget(s)  # reload from disk
        self.assertEqual(load(s).node(box)["hq"][LODS_OP]["tris"], [40000, 20000, 10000, 4000])
        self.assertNotIn("hq", next(n for n in load(s).payload()["nodes"] if n["id"] == box))
        self.t["forge_undo"](name=s)
        self.assertNotIn("hq", load(s).node(box))

    def test_send_is_brief(self):
        s = self.scene("SurfSend")
        self.t["forge_add"](name=s, kind="box")
        seen = {}

        def fake(name, listener, **k):
            seen.update(k)
            return {"ok": True, "assets": ["SM_Box"]}

        with patch.object(tools, "send_to_uefn", fake):
            got = self.t["forge_send_to_uefn"](name=s)
        self.assertIs(seen["verbose"], False)
        self.assertLessEqual(len(json.dumps(got)), 300)

    def test_ref_index(self):
        s = self.scene("SurfRef")
        self.t["forge_batch"](name=s, steps=[
            {"tool": "forge_add", "kind": "box", "count": 3},
            {"tool": "forge_edit", "id": "$0.ids.1", "object_name": "Head"},
            {"tool": "forge_edit", "id": "$0.ids.-1", "object_name": "Tail"},
            {"tool": "forge_add", "kind": "sphere"},
            {"tool": "forge_edit", "id": "$3", "object_name": "Ball"},  # $N and $N.ids still work
            {"tool": "forge_delete", "ids": ["$0.ids"]},
        ])
        self.assertEqual(self.names(s), ["Ball"])
        self.t["forge_undo"](name=s)
        self.t["forge_batch"](name=s, steps=[
            {"tool": "forge_add", "kind": "box", "count": 3},
            {"tool": "forge_edit", "id": "$0.ids.1", "object_name": "Head"},
            {"tool": "forge_edit", "id": "$0.ids.-1", "object_name": "Tail"},
        ])
        self.assertEqual(self.names(s), ["Box 1", "Head", "Tail"])
        with self.assertRaisesRegex(ValueError, r"\$0\.ids\.3: step 0 has 3 ids \(use \.0 to \.2"):
            self.t["forge_batch"](name=s, steps=[
                {"tool": "forge_add", "kind": "box", "count": 3},
                {"tool": "forge_edit", "id": "$0.ids.3", "object_name": "X"},
            ])
        self.assertEqual(self.names(s), ["Box 1", "Head", "Tail"], "a failed batch changes nothing")

    def test_error_hints(self):
        s = self.scene("SurfErrors")
        self.t["forge_add"](name=s, kind="box", object_name="Crate")
        self.t["forge_material_set"](name=s, material="Wood")
        self.t["forge_material_set"](name=s, material="Steel")
        with self.assertRaisesRegex(ValueError, r"No material 'Wod'\. Did you mean 'Wood'\? Materials: .*Wood, Steel"):
            self.t["forge_material_assign"](name=s, material="Wod")
        with self.assertRaisesRegex(ValueError, r"No object 'Crat'\. Did you mean 'Crate'\?"):
            self.t["forge_uv_project"](name=s, id="Crat", mode="smart")
        self.assertEqual(self.t["forge_uv_project"](name=s, id="Crate", mode="smart")["ok"], True, "names work as ids")
        self.t["forge_rig"](name=s, action="template", template="biped")
        with self.assertRaisesRegex(ValueError, r"No bone 'hand_left'\. Did you mean 'hand_l'\?"):
            self.t["forge_rig"](name=s, action="bind", bone="hand_left")
        with self.assertRaisesRegex(ValueError, r"step 0 \(forge_rig\) failed: No bone 'hand_left'\. Did you mean 'hand_l'\?"):
            self.t["forge_batch"](name=s, steps=[{"tool": "forge_rig", "action": "bind", "bone": "hand_left"}])
        with self.assertRaisesRegex(ValueError, r"No mesh op 'extrud'\. Did you mean 'extrude'\?"):
            self.t["forge_mesh_op"](name=s, op="extrud")
        with self.assertRaisesRegex(ValueError, r"forge_select\(query='top'\)"):
            self.t["forge_mesh_op"](name=s, op="extrude")
        with self.assertRaisesRegex(ValueError, r"No op 'materal_set'\. Did you mean 'material_set'\? Ops: "):
            tools.dispatch(s, "materal_set", {})

    def test_hq_tool_and_batch(self):
        s = self.scene("SurfHQ")
        self.assertEqual(self.t["forge_hq"](name=s)["summary"], hq.help_text())  # op="help" passes through
        box = self.t["forge_add"](name=s, kind="box", object_name="Crate")["ids"][0]
        rev = load(s).data["rev"]
        self.assertEqual(self.t["forge_hq"](name=s, op="help")["summary"], hq.help_text())
        self.assertEqual(load(s).data["rev"], rev, "help is not an edit")

        got = self.t["forge_hq"](name=s, op="surface_dummy", id=box)
        self.assertEqual(got["ids"], [box])
        self.assertLessEqual(len(str({k: got[k] for k in ("ok", "ids", "summary")})), 300)
        self.assertEqual(self.names(s), ["HQ_Crate"])
        self.t["forge_undo"](name=s)
        self.assertEqual(self.names(s), ["Crate"])

        with self.assertRaisesRegex(ValueError, "Did you mean 'surface_dummy'"):
            self.t["forge_hq"](name=s, op="surface_dumy", id=box)

        self.t["forge_batch"](name=s, steps=[
            {"tool": "forge_add", "kind": "box", "object_name": "Barrel"},
            {"tool": "forge_hq", "op": "surface_dummy", "id": "$0"},
        ])
        self.assertEqual(self.names(s), ["Crate", "HQ_Barrel"])
        self.t["forge_undo"](name=s)  # the whole batch is one undo step
        self.assertEqual(self.names(s), ["Crate"])


if __name__ == "__main__":
    unittest.main()
