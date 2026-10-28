"""Phase 2 proof: a rigged, walking lion built through the AI path (forge_batch on a fake api), real scenes dir.

Needs the Blender engine (skips without it). Writes scene Lion_Built and Eevee renders to FORGE_DEMO_OUT.
Run: py -3 -m backend.test_creature_demo
"""
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from . import hq, tools
from .engine import client, runtime
from .scene import forget, load
from .test_hq_e2e import _Api

_READY = runtime.engine_status()["state"] == "ready"
OUT = Path(os.environ.get("FORGE_DEMO_OUT") or
           "C:/Users/tas13/Documents/Fortnite Projects/LimoRide/.ducky/swarms/c53fda25-c0fe-463d-818d-4c8fe4fb75f0/"
           "outputs/5-demos-a-high-fidelity-walking-lion-two-ways")
DOC = Path(__file__).resolve().parent.parent / "skills" / "forge" / "references" / "creatures.md"
UEFN_TRIS = 20000


def _objects(name):
    return [n for n in load(name).data["nodes"] if n.get("type") == "object"]


def _tris(name):
    scene = load(name)
    return sum(scene.world_mesh(n).tri_count() for n in _objects(name))


class Fake:
    """forge_batch / forge_hq on a fake api; one scene."""
    def __init__(self, name):
        self.name = name
        self.api = _Api()
        tools.register_tools(self.api)
        self.t = self.api.fns

    def hq(self, op, **params):
        step = {"tool": "forge_hq", "op": op, "params": params}
        if "id" in params:
            step["id"] = params["id"]
        out = self.t["forge_batch"](name=self.name, steps=[step])
        assert out.get("ok", True), out
        return out


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class LionBuilt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch = patch.object(tools, "_attach_captures", lambda r: r)  # no Ducky frontend here
        cls.patch.start()
        OUT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        client.stop()
        cls.patch.stop()

    def test_lion_built(self):
        name = "Lion_Built"
        f = Fake(name)
        try:
            f.t["forge_scene_new"](name=name)
        except Exception:  # left by an earlier run: start clean
            forget(name)
            shutil.rmtree(load_dir(name), ignore_errors=True)
            f.t["forge_scene_new"](name=name)

        made = f.hq("creature", kind="big_cat", length=220, faces=2500)
        objs = _objects(name)
        body = max(objs, key=lambda n: load(name).world_mesh(n).tri_count())["id"]
        scene = load(name)
        lo, hi = scene.world_mesh(scene.node(body)).bounds()
        eyes = [n for n in objs if "eye" in (n.get("name") or "").lower()]
        pts = [scene.world_mesh(n).bounds() for n in eyes] or [(lo, hi)]
        head = [sum((a[0][k] + a[1][k]) / 2 for a in pts) / len(pts) for k in range(3)]

        preset = "fur_lion" if "fur_lion" in __import__("backend.hq.materials", fromlist=["presets"]).presets() else None
        if preset is None:
            preset = next((p for p in __import__("backend.hq.materials", fromlist=["presets"]).presets() if "fur" in p), None)
        f.hq("material", material="LionCoat", preset=preset, id=body) if preset else None
        f.hq("hair", id=body, style="mane", color=[0.45, 0.27, 0.1], region={"sphere": head + [48.0]})
        f.hq("hair_cards", id=body)
        f.hq("fur_shells", id=body, layers=2, decimate_shells=False, budget=UEFN_TRIS, color=[0.72, 0.5, 0.28])
        f.hq("fur_bake", id=body, size=512)
        f.hq("rig_template", template="quadruped", ids=[o["id"] for o in _objects(name)])
        f.hq("auto_weights", ids=[o["id"] for o in _objects(name)])
        look = f.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": "clip", "params": {"kind": "walk"}}], look="three")
        self.assertTrue(look.get("seen"), look)
        self.assertNotIn("look_error", look)

        from PIL import Image, ImageStat
        shutil.copyfile(look["path"], OUT / "Lion_Built_three.png")
        with Image.open(look["path"]) as im:
            self.assertGreater(sum(ImageStat.Stat(im.convert("L")).stddev), 5, "look is blank")

        scene = load(name)
        rig = scene.data["rigs"][0]
        walk = next(c for c in rig["actions"] if c["name"].startswith("Walk"))
        self.assertTrue(walk.get("loop"), "Walk must loop")
        self.assertTrue([n for n in _objects(name) if n.get("skin")], "something must be skinned")
        from . import render, rig as rg
        skinned = [n for n in _objects(name)]
        for frame in (0, 8, 16, 24):
            mesh = rg.posed_meshes(scene, skinned, rig, walk, frame)
            render.render_tiles(mesh, OUT / f"Lion_Built_walk_{frame:02d}.png", ["side"], scene.materials(), scene.folder / "textures")
        tris = _tris(name)
        print("\nLion_Built: %d tris, %d bones, %d objects" % (tris, len(rig["bones"]), len(skinned)))
        self.assertLessEqual(tris, UEFN_TRIS, "over the UEFN budget")
        self.assertGreater(len(rig["bones"]), 10)


@unittest.skipUnless(_READY and os.environ.get("FORGE_DEMO_LIVE") == "1",
                     "spends Tripo/Meshy credits: set FORGE_DEMO_LIVE=1 inside Ducky with the plugin installed")
class LionGenerated(unittest.TestCase):
    def test_lion_generated(self):
        from . import live_view, render, rig as rg
        if live_view._api is None:
            self.skipTest("no Ducky plugin api: run from inside Ducky so generate can call Tripo/Meshy")
        name = "Lion_Generated"
        OUT.mkdir(parents=True, exist_ok=True)
        with patch.object(tools, "_attach_captures", lambda r: r):
            f = Fake(name)
            f.t["forge_scene_new"](name=name)
            f.hq("creature_from_prompt", prompt="adult male lion, full mane, standing, realistic", kind="quadruped", faces=12000)
            body = max(_objects(name), key=lambda n: load(name).world_mesh(n).tri_count())["id"]
            f.hq("fur_bake", id=body, size=1024)
            look = f.t["forge_batch"](name=name, steps=[{"tool": "forge_hq", "op": "clip", "params": {"kind": "walk"}}], look="three")
        shutil.copyfile(look["path"], OUT / "Lion_Generated_three.png")
        scene = load(name)
        rig = scene.data["rigs"][0]
        walk = next(c for c in rig["actions"] if c["name"].startswith("Walk"))
        self.assertTrue(walk.get("loop"))
        for frame in (0, 8, 16, 24):
            mesh = rg.posed_meshes(scene, _objects(name), rig, walk, frame)
            render.render_tiles(mesh, OUT / f"Lion_Generated_walk_{frame:02d}.png", ["side"], scene.materials(), scene.folder / "textures")


def load_dir(name):
    return load(name).folder


class DocCalls(unittest.TestCase):
    def test_doc_calls_parse(self):
        text = DOC.read_text(encoding="utf-8")
        blocks = re.findall(r"```json\n(.*?)```", text, re.S)
        self.assertTrue(blocks, "creatures.md needs JSON call blocks")
        names = set(hq.HQ_OPS)
        for raw in blocks:
            call = json.loads(raw)
            for step in call.get("steps") or [call]:
                if step.get("tool") == "forge_hq" or "op" in step:
                    self.assertIn(step["op"], names, step)


if __name__ == "__main__":
    unittest.main()
