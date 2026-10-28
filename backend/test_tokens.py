"""Token baseline for today's forge_* tools: schemas, SKILL.md, and 3 reference builds. No UEFN.

Tokens are chars/4. Run: py -3 -m backend.test_tokens
Ceilings are the measured baseline plus ~10%; lower them as the lean tools land.
"""
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from . import tools
from .scene import forget

ROOT = Path(__file__).resolve().parent.parent
tok = lambda s: len(s) // 4

# ceilings: schemas/skill in tokens; builds are (turns, tokens). Fill from the first measured run.
CEIL = {
    "schemas": 2980, "skill": 1200,  # measured 2711 / 1190 (was 4009 / 2823)
    "crate": (6, 510), "robot_walk": (5, 700), "character": (7, 950),  # measured 462 / 632 / 867 tok (was 523 / 810 / 1043)
}


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):  # register_pipeline_node etc.
        return lambda *a, **k: (lambda fn: fn)


def schema_tokens(fns):
    return {n: tok(n + str(inspect.signature(f)) + (inspect.getdoc(f) or "")) for n, f in fns.items()
            if n.startswith("forge_")}


def run_build(api, scene, calls):
    """calls: [(tool, args)] -> (turns, tokens of calls+results, last result)."""
    total, last = 0, {}
    for tool, args in calls:
        args = {"name": scene, **args} if tool != "forge_scene_new" else args
        last = api.fns[tool](**args)
        total += tok(json.dumps({"tool": tool, "args": args})) + tok(json.dumps(last, default=str))
        if last.get("error"):
            raise AssertionError(f"{tool} failed: {last['error']}")
    return len(calls), total, last


def add(kind, pos, scale=None, **p):
    return {"tool": "forge_add", "kind": kind, "pos": pos, **({"scale": scale} if scale else {}), **p}


BUILDS = {
    "crate": lambda n: [
        ("forge_scene_new", {"name": n}),
        ("forge_add", {"kind": "box", "scale": [60, 60, 60], "object_name": "Crate"}),
        ("forge_material_set", {"material": "Wood", "base_color": [0.45, 0.3, 0.15], "roughness": 0.8}),
        ("forge_material_assign", {"material": "Wood"}),
        ("forge_uv_project", {"mode": "box"}),
        ("forge_render", {}),  # look
    ],
    "robot_walk": lambda n: [
        ("forge_scene_new", {"name": n}),
        ("forge_batch", {"steps": [
            add("box", [0, 0, 100], [40, 25, 60], object_name="Torso"),
            add("sphere", [0, 0, 150], [25, 25, 25], object_name="Head"),
            add("cylinder", [-30, 0, 100], [10, 10, 50], object_name="ArmL"),
            add("cylinder", [30, 0, 100], [10, 10, 50], object_name="ArmR"),
            add("cylinder", [-12, 0, 40], [12, 12, 70], object_name="LegL"),
            add("cylinder", [12, 0, 40], [12, 12, 70], object_name="LegR"),
        ]}),
        ("forge_rig", {"action": "template", "template": "biped", "auto": True}),
        ("forge_anim", {"action": "generate", "kind": "walk"}),
        ("forge_render", {"clip": "Walk"}),  # look
    ],
    "character": lambda n: [
        ("forge_scene_new", {"name": n}),
        ("forge_batch", {"steps": [
            add("capsule", [0, 0, 90], [30, 20, 90], object_name="Body"),
            add("sphere", [0, 0, 175], [22, 22, 22], object_name="Head"),
            add("cylinder", [-28, 0, 100], [8, 8, 60], object_name="ArmL"),
            add("cylinder", [28, 0, 100], [8, 8, 60], object_name="ArmR"),
        ]}),
        ("forge_material_set", {"material": "Skin", "base_color": [0.8, 0.6, 0.5], "roughness": 0.6}),
        ("forge_material_assign", {"material": "Skin"}),
        ("forge_uv_project", {"mode": "smart"}),
        ("forge_rig", {"action": "template", "template": "biped", "auto": True}),
        ("forge_render", {}),  # look
    ],
}


class Tokens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-tokens-")
        cls.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")})
        cls.env.start()
        cls.cap = patch.object(tools, "_attach_captures", lambda r: {**r, "path": "C:/x/forge.png"})  # no Ducky frontend here
        cls.cap.start()
        cls.api = _Api()
        tools.register_tools(cls.api)

    @classmethod
    def tearDownClass(cls):
        cls.cap.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def test_recipes_save_60_percent_tokens_and_50_percent_turns(self):
        """The same three assets as one recipe call with a look, against the measured pre-HQ baseline."""
        from . import hq
        before = {"crate": (6, 523), "robot_walk": (5, 810), "character": (7, 1043)}  # turns, tokens: original tools
        after_ceil = {"crate": (2, 270), "robot_walk": (2, 300), "character": (2, 310)}  # measured 263 / 287 / 292
        lean = {
            "crate": {"recipe": "scifi_crate"},
            "robot_walk": {"recipe": "robot_walker"},
            "character": {"recipe": "humanoid_base", "hair": "ponytail", "skin": "skin_dark"},
        }

        def run_hq(scene, name, params=None):  # real material/recipe, stub for bpy-backed ops
            if name in ("material", "recipe"):
                return hq.compact(hq.HQ_OPS[name](scene, dict(params or {})))
            return {"ok": True, "summary": name + " (stub)"}

        print("\n%-12s %14s %14s %7s %7s" % ("build", "before", "after", "turns", "tokens"))
        total = 0
        with patch("backend.hq.run_hq", run_hq):
            for build, params in lean.items():
                scene = "Lean" + build
                try:
                    calls = [("forge_scene_new", {"name": scene}), ("forge_hq", {"op": "recipe", "params": params, "look": "three"})]
                    turns, tokens, last = run_build(self.api, scene, calls)
                finally:
                    forget(scene)
                self.assertIn("seen", last, build)  # the look came back in the same call
                self.assertLessEqual(len(json.dumps(last)), 600, build)
                (bt, bk), (mt, mk) = before[build], after_ceil[build]
                self.assertLessEqual(turns, bt * 0.5, build)
                self.assertLessEqual(tokens, bk * 0.55, build)
                self.assertLessEqual(turns, mt, build)
                self.assertLessEqual(tokens, mk, build)
                total += tokens
                print("%-12s %5d turn %4dt %5d turn %4dt %6d%% %6d%%" % (
                    build, bt, bk, turns, tokens, 100 - turns * 100 // bt, 100 - tokens * 100 // bk))
        # per build >=45% fewer tokens (the crate carries forge_scene_new's full-scene result, ~130 tok);
        # across the three, >=60% fewer
        self.assertLessEqual(total, sum(k for _, k in before.values()) * 0.4)

    def test_baseline(self):
        sch = schema_tokens(self.api.fns)
        skill = tok((ROOT / "skills/forge/SKILL.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(sch), 40)
        self.assertLessEqual(sum(sch.values()), CEIL["schemas"])
        self.assertLessEqual(skill, CEIL["skill"])
        out = []
        for build, mk in BUILDS.items():
            scene = "Tok" + build
            try:
                turns, tokens, _ = run_build(self.api, scene, mk(scene))
            finally:
                forget(scene)
            max_turns, max_tok = CEIL[build]
            self.assertLessEqual(turns, max_turns, build)
            self.assertLessEqual(tokens, max_tok, build)
            out.append((build, turns, tokens))
        print("\n%-22s %8s" % ("item", "tokens"))
        print("%-22s %8d" % ("schemas (%d tools)" % len(sch), sum(sch.values())))
        print("%-22s %8d" % ("SKILL.md", skill))
        print("heaviest: " + ", ".join(f"{n}={v}" for n, v in sorted(sch.items(), key=lambda kv: -kv[1])[:4]))
        for n, t, k in out:
            print("%-22s %2d turns %6d tok" % (n, t, k))


class SkillTests(unittest.TestCase):
    def test_skill_core_budget(self):
        core = (ROOT / "skills/forge/SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(tok(core), 1200, "core skill must stay lean; move detail into references/")
        for gone in ("What Forge leaves to Blender", "Import reference buildings"):
            self.assertNotIn(gone, core)

    def test_every_reference_listed_and_loadable(self):
        core = (ROOT / "skills/forge/SKILL.md").read_text(encoding="utf-8")
        for ref in sorted((ROOT / "skills/forge/references").glob("*.md")):
            self.assertIn("`%s`" % ref.stem, core, ref.name + " is not listed in SKILL.md")
            self.assertTrue(ref.read_text(encoding="utf-8").startswith("---\ndescription:"), ref.name)


class RecipeTests(unittest.TestCase):
    """Every assets/recipes/*.json expands and runs as one batch; engine ops are stubbed (no bpy here)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-recipes-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(self.tmp.name) / "scenes")})
        self.env.start()
        from . import hq
        self.seen = []

        def run_hq(scene, name, params=None):  # real material/recipe, stub for bpy-backed ops
            if name not in hq.HQ_OPS:
                raise ValueError("No HQ op %r" % name)
            self.seen.append(name)
            if name in ("material", "recipe"):
                return hq.compact(hq.HQ_OPS[name](scene, dict(params or {})))
            return {"ok": True, "summary": name + " (stub)"}

        self.hq = patch("backend.hq.run_hq", run_hq)
        self.hq.start()

    def tearDown(self):
        self.hq.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_recipe_registered(self):
        from . import hq
        from .hq.recipes import recipes
        self.assertIn("recipe", hq.HQ_OPS)
        self.assertNotIn("recipes", hq._BROKEN)
        text = hq.help_text("recipe")
        self.assertTrue(all(n in text for n in recipes()) or len(text) == 400, text)  # names listed (help caps at 400)
        self.assertIn("recipe", hq.help_text())

    def test_recipe_through_real_run_hq(self):
        """forge_hq(op='recipe') end to end: the real run_hq gate, only the engine state and bpy ops are faked."""
        from . import hq
        from .engine import runtime
        from .scene import dispatch
        real = dict(hq.HQ_OPS)
        stubs = {n: (lambda scene, p: {"summary": "stub"}) for n in real if n not in ("material", "recipe")}
        dispatch("", "scene_new", {"name": "Rr"})
        try:
            with patch.object(runtime, "engine_status", lambda: {"state": "ready"}), patch.dict(hq.HQ_OPS, stubs):
                out = dispatch("Rr", "hq", {"op": "recipe", "params": {"recipe": "scifi_crate"}})
                self.assertTrue(out["ok"] and out["ids"], out)
                self.assertEqual(len(json.dumps(out)), len(json.dumps(out)))  # compact dict, JSON-able
                with self.assertRaisesRegex(Exception, "Did you mean 'scifi_crate'"):
                    dispatch("Rr", "hq", {"op": "recipe", "params": {"recipe": "scifi_crat"}})
        finally:
            forget("Rr")

    def test_rigged_recipes_are_skinned(self):
        """Every recipe that makes a rig (native template or HQ rig_template) must leave all its parts skinned."""
        from .hq.recipes import expand, recipes
        from .scene import dispatch, load
        rigged = [n for n in recipes() if any(s.get("action") == "template" or s.get("op") == "rig_template" for s in expand(n))]
        self.assertGreaterEqual(len(rigged), 3)
        for name in rigged:  # HQ rig ops are stubbed here (no bpy): they must at least be followed by auto_weights
            steps = expand(name)
            if any(s.get("op") == "rig_template" for s in steps):
                ops = [s.get("op") for s in steps]
                self.assertIn("auto_weights", ops[ops.index("rig_template"):], name)
        for name in [n for n in rigged if any(s.get("action") == "template" for s in expand(n))]:
            scene = "S" + name
            dispatch("", "scene_new", {"name": scene})
            with self.subTest(recipe=name):
                try:
                    dispatch(scene, "hq", {"op": "recipe", "params": {"recipe": name}})
                    parts = [n for n in load(scene).data["nodes"] if n.get("type") == "object"]
                    self.assertGreaterEqual(len(parts), 5, name)
                    self.assertEqual([p["name"] for p in parts if not p.get("skin")], [], name + " has unskinned parts")
                finally:
                    forget(scene)

    def test_every_recipe_runs(self):
        from .hq.recipes import recipes
        from .scene import dispatch
        names = recipes()
        self.assertGreaterEqual(len(names), 15)
        for name in names:
            scene = "R" + name
            dispatch("", "scene_new", {"name": scene})
            with self.subTest(recipe=name):
                try:
                    out = dispatch(scene, "hq", {"op": "recipe", "params": {"recipe": name}})
                    self.assertTrue(out["ids"], name)
                    checked = dispatch(scene, "check", {})
                    self.assertFalse(checked.get("errors"), (name, checked))
                finally:
                    forget(scene)

    def test_humanoid_in_one_call(self):
        from .scene import dispatch
        dispatch("", "scene_new", {"name": "Hum"})
        try:
            call = {"op": "recipe", "params": {"recipe": "humanoid_base", "height": 170, "hair": "ponytail", "skin": "skin_dark"}}
            out = dispatch("Hum", "hq", call)
            self.assertIn("hair", self.seen)
            self.assertEqual(len(dispatch("Hum", "scene_get", {})["rigs"]), 1)
            print("humanoid_base: 1 call, %d tok" % (tok(json.dumps(call)) + tok(json.dumps(out))))
        finally:
            forget("Hum")

    def test_mistakes_say_how_to_fix(self):
        from .hq.recipes import expand
        with self.assertRaisesRegex(ValueError, "Did you mean 'barrel'"):
            expand("barel")
        with self.assertRaisesRegex(ValueError, "Did you mean 'height'.*Params: .*height"):
            expand("tree_stylized", {"hieght": 300})
        with self.assertRaisesRegex(ValueError, "must be a number"):
            expand("barrel", {"radius": "big"})
        self.assertEqual(expand("humanoid_base", {"hair": "none"})[-1]["tool"], "forge_anim")


if __name__ == "__main__":
    unittest.main()
