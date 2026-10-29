"""py -3 -m backend.test_hq_rig  (bpy-only checks skip until the Forge engine is installed)."""
import math
import os
import tempfile
import unittest
from unittest.mock import patch

from . import rig as rg
from .engine import runtime
from .engine.ops import rig as ew
from .hq import HQ_OPS, rig as hq
from .scene import dispatch, forget, load

_READY = runtime.engine_status()["state"] == "ready"

ARKIT = """eyeBlinkLeft eyeLookDownLeft eyeLookInLeft eyeLookOutLeft eyeLookUpLeft eyeSquintLeft eyeWideLeft
eyeBlinkRight eyeLookDownRight eyeLookInRight eyeLookOutRight eyeLookUpRight eyeSquintRight eyeWideRight
jawForward jawLeft jawRight jawOpen mouthClose mouthFunnel mouthPucker mouthLeft mouthRight
mouthSmileLeft mouthSmileRight mouthFrownLeft mouthFrownRight mouthDimpleLeft mouthDimpleRight
mouthStretchLeft mouthStretchRight mouthRollLower mouthRollUpper mouthShrugLower mouthShrugUpper
mouthPressLeft mouthPressRight mouthLowerDownLeft mouthLowerDownRight mouthUpperUpLeft mouthUpperUpRight
browDownLeft browDownRight browInnerUp browOuterUpLeft browOuterUpRight cheekPuff cheekSquintLeft cheekSquintRight
noseSneerLeft noseSneerRight tongueOut""".split()

MANNY = ("root pelvis spine_01 spine_02 spine_03 spine_04 spine_05 neck_01 neck_02 head").split()
for _s in "lr":
    MANNY += ["%s_%s" % (n, _s) for n in (
        "clavicle upperarm upperarm_twist_01 upperarm_twist_02 lowerarm lowerarm_twist_01 hand "
        "thumb_01 thumb_02 thumb_03 index_01 index_02 index_03 middle_01 middle_02 middle_03 "
        "ring_01 ring_02 ring_03 pinky_01 pinky_02 pinky_03 thigh thigh_twist_01 thigh_twist_02 calf calf_twist_01 foot ball").split()]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-hq-rig-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        self.names = []

    def tearDown(self):
        for name in self.names:
            forget(name)
        self.env.stop()
        self.tmp.cleanup()

    def scene(self, kind="box", **params):
        name = "HqRig%d" % len(self.names)
        self.names.append(name)
        dispatch("", "scene_new", {"name": name})
        dispatch(name, "add", {"kind": kind, "params": params})
        return load(name)

    def character(self):
        scene = self.scene("box", w=60, d=30, h=180)
        node = next(n for n in scene.data["nodes"] if n["type"] == "object")
        HQ_OPS["rig_template"](scene, {"template": "ue5_manny", "ids": [node["id"]]})
        return scene, scene.data["rigs"][0]


class TemplateTests(Base):
    def test_manny_names_match(self):
        scene, rig = self.character()
        names = [b["name"] for b in rig["bones"]]
        self.assertEqual(len(names), 68)
        self.assertEqual(set(names), set(MANNY))
        by = {b["name"]: b for b in rig["bones"]}
        for b in rig["bones"]:
            self.assertTrue(b["parent"] is None or b["parent"] in by, b["name"])
        for n in MANNY:
            if n.endswith("_l"):
                a, b = by[n]["head"], by[n[:-2] + "_r"]["head"]
                self.assertAlmostEqual(a[0], -b[0], places=2)
                self.assertEqual(a[1:], b[1:])
        node = next(n for n in scene.data["nodes"] if n["type"] == "object")
        low, high = scene.world_mesh(node).bounds()
        self.assertAlmostEqual(by["head"]["tail"][2], high[2], places=1)
        self.assertAlmostEqual(by["root"]["head"][2], low[2], places=1)
        self.assertEqual(node["skin"]["rig"], rig["id"])

    def test_other_templates_fit_bounds(self):
        for template in ("quadruped", "bird", "tail_chain"):
            scene = self.scene("box", w=80, d=160, h=90)
            HQ_OPS["rig_template"](scene, {"template": template})
            rig = scene.data["rigs"][0]
            node = next(n for n in scene.data["nodes"] if n["type"] == "object")
            low, high = scene.world_mesh(node).bounds()
            for b in rig["bones"]:
                for point in (b["head"], b["tail"]):
                    for i in range(3):
                        self.assertTrue(low[i] - 1e-3 <= point[i] <= high[i] + 1e-3, (template, b["name"], point))
            rg.ordered(rig)  # parents resolve, no cycles
        with self.assertRaisesRegex(ValueError, "Did you mean 'quadruped'"):
            HQ_OPS["rig_template"](scene, {"template": "quadrupd"})

    def test_landmark_moves_head(self):
        scene = self.scene("box", w=60, d=30, h=180)
        HQ_OPS["rig_template"](scene, {"landmarks": {"head": [0, 0, 150]}})
        by = {b["name"]: b for b in scene.data["rigs"][0]["bones"]}
        self.assertEqual(by["head"]["head"][2], 150)
        self.assertEqual(by["neck_02"]["tail"][2], 150)


class WeightTests(unittest.TestCase):
    def test_weights_limits(self):
        rows = [{"b%d" % k: 1.0 / (1 + (k * (i + 1)) % 7) for k in range(12)} for i in range(50)]
        rows[3] = {"a": 0.995, "b": 0.0049, "c": 0.0001}
        for limit in (4, 8):
            for row in ew.finish(rows, None, limit, 0, 0.01):
                self.assertLessEqual(len(row), limit)
                self.assertAlmostEqual(sum(row.values()), 1.0, places=9)
                self.assertTrue(all(w >= 0.0099 for w in row.values()), row)
        self.assertEqual(ew.finish([{"a": 0.995, "b": 0.0049, "c": 0.0001}], None, 4, 0, 0.01), [{"a": 1.0}])
        adj = [[(i - 1) % 50, (i + 1) % 50] for i in range(50)]  # smoothing spreads influence, the limit still holds
        self.assertTrue(all(len(r) <= 4 for r in ew.finish(rows, adj, 4, 2, 0.01)))

    def test_elbow_volume(self):
        bones = [("upper", [0, 0, 0], [1, 0, 0]), ("lower", [1, 0, 0], [2, 0, 0])]
        verts = [[x / 10.0, 0.2 * math.cos(a), 0.2 * math.sin(a)] for x in range(0, 21) for a in (0, 2, 4)]
        rows = ew.finish(ew.fallback_weights(verts, bones), None, 4, 0, 0.01)
        at = {round(v[0], 1): r for v, r in zip(verts, rows)}
        self.assertGreater(min(at[1.0].values()), 0.3)  # the joint is shared, not snapped to one bone
        self.assertGreater(at[0.0]["upper"], 0.9)
        self.assertGreater(at[2.0]["lower"], 0.9)
        self.assertGreater(at[0.9].get("lower", 0), 0.05)

    @unittest.skipUnless(_READY, "Blender engine not installed")
    def test_engine_heat_runs(self):
        base = Base("setUp")
        base.setUp()
        try:
            scene, rig = base.character()
            result = HQ_OPS["auto_weights"](scene, {"ids": [n["id"] for n in scene.data["nodes"] if n["type"] == "object"]})
            self.assertTrue(result["ids"], result)
            node = next(n for n in scene.data["nodes"] if n["type"] == "object")
            groups = [k for k in scene.local_mesh(node).attrs if k.startswith("wt:")]
            self.assertTrue(groups)
        finally:
            base.tearDown()


class PoseTests(Base):
    def test_ik_reaches_target(self):
        scene, rig = self.character()
        bones = rg.export_bones(rig)
        hand = next(b for b in bones if b["name"] == "hand_l")["head"]
        target = [hand[0] - 15, hand[1] - 25, hand[2] - 18]
        HQ_OPS["pose"](scene, {"controls": {"hand_l": {"ik": target}}, "frame": 10})
        clip = rig["actions"][0]
        got = hq._world_head(rg.export_bones(rig), clip, 10, "hand_l")
        self.assertLess(math.dist(got, target), 0.5)

    def test_pin_and_spine(self):
        scene, rig = self.character()
        clip = {"id": "a1", "name": "Pose", "fps": 30, "frames": 10, "loop": False,
                "tracks": {"pelvis": {"loc": [[5, [0, 0, -10], "smooth"]]}}}
        rig["actions"].append(clip)
        bones = rg.export_bones(rig)
        rest = hq._world_head(bones, None, 0, "foot_r")
        HQ_OPS["pose"](scene, {"controls": {"foot_r": {"pin": True}, "spine": {"curve": 0.2}}, "frame": 5})
        self.assertLess(math.dist(hq._world_head(bones, clip, 5, "foot_r"), rest), 0.5)
        self.assertAlmostEqual(clip["tracks"]["spine_01"]["rot"][0][1][0], 0.2 * 90 / 5, places=3)

    def test_bake_has_no_constraints(self):
        scene, rig = self.character()
        names = {b["name"] for b in rig["bones"]}
        HQ_OPS["pose"](scene, {"controls": {"hand_l": {"ik": [40, -30, 120]}, "foot_r": {"pin": True}}, "frame": 3})
        clip = rig["actions"][0]
        self.assertFalse({"constraints", "ik"} & (set(clip) | set(rig)))
        for bone, channels in clip["tracks"].items():
            self.assertIn(bone, names)
            self.assertLessEqual(set(channels), {"rot", "loc", "scale"})
            for keys in channels.values():
                self.assertTrue(all(len(k[1]) == 3 and all(math.isfinite(c) for c in k[1]) for k in keys))
        with self.assertRaisesRegex(ValueError, "needs ik"):
            HQ_OPS["pose"](scene, {"controls": {"hand_l": {}}})


class ClipTests(Base):
    def test_clips_loop(self):
        scene, rig = self.character()
        for kind in hq.CLIP_KINDS:
            made = hq.make_clip(rig, kind, {})
            self.assertEqual(made["loop"], kind in ("walk", "run", "idle"), kind)
            if made["loop"]:
                for bone, channels in made["tracks"].items():
                    if bone == "root":
                        continue
                    for channel, keys in channels.items():
                        self.assertEqual(keys[0][1], keys[-1][1], (kind, bone, channel))
                        self.assertEqual(keys[-1][0], made["frames"])
            else:
                self.assertTrue(made["tracks"], kind)
        heavy = hq.make_clip(rig, "run", {"style": "heavy"})
        self.assertEqual(heavy["frames"], 24)
        self.assertEqual(hq.make_clip(rig, "run", {"speed": 2})["frames"], 10)
        quad = self.scene("box", w=80, d=160, h=90)
        HQ_OPS["rig_template"](quad, {"template": "quadruped"})
        for kind in hq.CLIP_KINDS:  # any template, through the shared bone names
            self.assertTrue(hq.make_clip(quad.data["rigs"][0], kind, {})["tracks"], kind)
        result = HQ_OPS["clip"](scene, {"kind": "run", "style": "heavy"})
        self.assertEqual(result["frames"], 24)
        with self.assertRaisesRegex(ValueError, "Did you mean 'jump'"):
            HQ_OPS["clip"](scene, {"kind": "jmp"})

    def test_root_motion_flag(self):
        scene, rig = self.character()
        on = hq.make_clip(rig, "run", {"root_motion": True})
        off = hq.make_clip(rig, "run", {})
        self.assertTrue(on["root_motion"])
        self.assertIn("loc", on["tracks"]["root"])
        self.assertLess(on["tracks"]["root"]["loc"][-1][1][1], 0)  # travels toward -Y, the front
        self.assertFalse(off["root_motion"])
        self.assertNotIn("root", off["tracks"])


class QuadrupedWalkTests(Base):
    def test_four_beat_walk_moves_every_leg_and_the_tail(self):
        scene = self.scene("box", w=50, d=160, h=90)
        HQ_OPS["rig_template"](scene, {"template": "quadruped"})
        rig = scene.data["rigs"][0]
        made = hq.make_clip(rig, "walk", {})
        self.assertTrue(made["loop"])
        legs = ("thigh_l", "thigh_r", "upperarm_l", "upperarm_r", "calf_l", "calf_r", "lowerarm_l", "lowerarm_r")
        for bone in legs + ("tail_01", "tail_03"):
            self.assertIn(bone, made["tracks"], bone)

        def peak(bone):  # when the leg is furthest back: the four feet take turns
            keys = made["tracks"][bone]["rot"]
            return max(keys, key=lambda k: k[1][0])[0]

        order = sorted(("thigh_l", "upperarm_l", "thigh_r", "upperarm_r"), key=peak)
        self.assertEqual(len({peak(b) for b in order}), 4, order)
        for bone, channels in made["tracks"].items():
            for keys in channels.values():
                self.assertEqual(keys[0][1], keys[-1][1], bone)


class FaceTests(Base):
    def head(self):
        scene = self.scene("sphere", radius=12, seg=24, rings=16)
        return scene, next(n for n in scene.data["nodes"] if n["type"] == "object")

    def test_face_shapes_count_and_names(self):
        scene, node = self.head()
        HQ_OPS["face_shapes"](scene, {"ids": [node["id"]]})
        shapes = sorted(k[3:] for k in scene._bake(node).attrs if k.startswith("sk:"))
        self.assertEqual(len(shapes), 52)
        self.assertEqual(shapes, sorted(ARKIT))
        HQ_OPS["face_shapes"](scene, {"ids": [node["id"]], "set": "basic12"})
        self.assertEqual(len([k for k in scene._bake(node).attrs if k.startswith("sk:")]), 12)

    def test_jaw_open_moves_jaw_verts(self):
        scene, node = self.head()
        HQ_OPS["face_shapes"](scene, {"ids": [node["id"]]})
        mesh = scene._bake(node)
        deltas, zs = mesh.attrs["sk:jawOpen"], [v[2] for v in mesh.verts]
        low, high = min(zs), max(zs)
        jaw = [d for d, z in zip(deltas, zs) if (z - low) / (high - low) < 0.2]
        crown = [d for d, z in zip(deltas, zs) if (z - low) / (high - low) > 0.5]
        self.assertTrue(any(d[2] < -0.1 for d in jaw))
        self.assertTrue(all(d == [0.0, 0.0, 0.0] for d in crown))
        left = mesh.attrs["sk:mouthSmileLeft"]
        self.assertTrue(all(d[0] >= 0 for d, v in zip(left, mesh.verts) if any(d)))  # the left corner moves outward (+X)

    def test_visemes_split_and_report(self):
        scene, node = self.head()
        out = HQ_OPS["face_shapes"](scene, {"ids": [node["id"]], "set": "basic12", "visemes": True,
                                            "split": ["jawOpen"], "report": True})
        attrs = scene._bake(node).attrs
        self.assertEqual(len([k for k in attrs if k.startswith("sk:viseme_")]), 14)
        self.assertEqual(out["shapes"], 12 + 14 + 2)
        self.assertEqual(out["flat"], 0, out)
        mesh = scene._bake(node)
        whole, left, right = attrs["sk:jawOpen"], attrs["sk:jawOpen_L"], attrs["sk:jawOpen_R"]
        for w, l, r in zip(whole, left, right):
            self.assertTrue(all(abs(w[i] - l[i] - r[i]) < 1e-3 for i in range(3)))
        far_right = [l for l, v in zip(left, mesh.verts) if v[0] < -8]
        self.assertTrue(all(d == [0.0, 0.0, 0.0] for d in far_right))
        self.assertLessEqual(len(str(out)), 300)
        with self.assertRaisesRegex(ValueError, "No shape key"):
            HQ_OPS["face_shapes"](scene, {"ids": [node["id"]], "set": "none", "split": ["nope"]})


class AnimToolsTests(Base):
    def test_loop_seam_reported(self):
        scene, rig = self.character()
        out = HQ_OPS["clip"](scene, {"kind": "walk"})
        self.assertEqual(out["seam_deg"], 0.0, out)
        self.assertNotIn("seam_deg", HQ_OPS["clip"](scene, {"kind": "jump"}))

    def test_stress_clip(self):
        scene, rig = self.character()
        out = HQ_OPS["stress"](scene, {})
        clip = rg.find_clip(rig, "Stress")
        self.assertEqual(clip["frames"], 50)
        for bone in ("upperarm_l", "lowerarm_r", "thigh_l", "calf_r"):
            self.assertIn(bone, clip["tracks"])
        _, q, _ = rg.local_pose(clip, "calf_l", 30)
        self.assertAlmostEqual(math.degrees(2 * math.acos(min(1, abs(q[0])))), 150, delta=1)
        HQ_OPS["stress"](scene, {})  # again: replaced, not duplicated
        self.assertEqual([c["name"] for c in rig["actions"]].count("Stress"), 1)
        self.assertLessEqual(len(str(out)), 300)

    def test_retarget_between_rigs_scales_travel(self):
        scene = self.scene("box", w=60, d=30, h=180)
        first = next(n for n in scene.data["nodes"] if n["type"] == "object")["id"]
        node = dispatch(scene.name, "add", {"kind": "box", "params": {"w": 30, "d": 15, "h": 90}})["id"]
        scene = load(scene.name)
        HQ_OPS["rig_template"](scene, {"template": "ue5_manny", "ids": [first]})
        small = HQ_OPS["rig_template"](scene, {"template": "ue5_manny", "ids": [node], "bind": False})["id"]
        HQ_OPS["clip"](scene, {"kind": "walk", "root_motion": True})
        out = HQ_OPS["retarget"](scene, {"src": "Walk", "rig": small})
        big = scene.data["rigs"][0]
        tiny = rg.find_rig(scene, small)
        got = rg.find_clip(tiny, "Walk")
        self.assertIn("thigh_l", got["tracks"])
        src_y = rg.find_clip(big, "Walk")["tracks"]["root"]["loc"][-1][1][1]
        self.assertAlmostEqual(got["tracks"]["root"]["loc"][-1][1][1], src_y * rg.rig_height(tiny) / rg.rig_height(big), places=2)
        self.assertIn("bones mapped", out["summary"])

    def test_retarget_bvh(self):
        bvh = """HIERARCHY
ROOT Hips
{
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT LeftUpLeg
 {
  OFFSET 10 0 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  JOINT LeftLeg
  {
   OFFSET 0 -45 0
   CHANNELS 3 Zrotation Xrotation Yrotation
   JOINT LeftFoot
   {
    OFFSET 0 -45 0
    CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
    {
     OFFSET 0 0 10
    }
   }
  }
 }
}
MOTION
Frames: 5
Frame Time: 0.0166667
0 90 0 0 0 0 0 0 0 0 0 0 0 0 0
0 90 5 0 0 0 0 10 0 0 0 0 0 0 0
0 90 10 0 0 0 0 20 0 0 30 0 0 0 0
0 90 15 0 0 0 0 30 0 0 0 0 0 0 0
0 90 20 0 0 0 0 40 0 0 0 0 0 0 0
"""
        scene, rig = self.character()
        path = os.path.join(self.tmp.name, "walk.bvh")
        with open(path, "w") as f:
            f.write(bvh)
        out = HQ_OPS["retarget"](scene, {"src": path})
        clip = rg.find_clip(rig, "walk")
        self.assertEqual(clip["frames"], 2)  # 60 fps -> 30 fps
        self.assertEqual(set(clip["tracks"]), {"pelvis", "thigh_l", "calf_l", "foot_l"})
        # BVH +X rotation of the thigh (leg swings forward in +Z) stays +X in Forge; BVH +Z travel -> Forge -Y
        self.assertAlmostEqual(clip["tracks"]["thigh_l"]["rot"][2][1][0], 40, places=1)
        self.assertLess(clip["tracks"]["pelvis"]["loc"][-1][1][1], 0)
        self.assertIn("4 bones mapped", out["summary"])

    def test_bake_clip_reduces_and_closes_loop(self):
        scene, rig = self.character()
        HQ_OPS["clip"](scene, {"kind": "idle"})
        clip = rg.find_clip(rig, "Idle")
        clip["tracks"]["pelvis"]["scale"] = [[0, [1, 1, 1], "smooth"], [10, [1.1, 1, 1], "smooth"]]
        clip["tracks"]["head"]["rot"][-1][1] = [5.0, 0.0, 0.0]  # a broken seam
        out = HQ_OPS["bake_clip"](scene, {"clip": "Idle", "tol": 0.05})
        self.assertNotIn("scale", clip["tracks"]["pelvis"])
        head = clip["tracks"]["head"]["rot"]
        self.assertEqual(head[0][1], head[-1][1])
        self.assertTrue(all(k[2] == "linear" for k in head))
        self.assertLess(out["keys"], 61 * len(clip["tracks"]))
        self.assertIn("seam 0 deg", out["summary"])
        self.assertEqual(hq.reduce_keys([[0, [0]], [1, [1]], [2, [2]], [3, [5]]], 0.01), [[0, [0]], [2, [2]], [3, [5]]])


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class TinyHeatTests(Base):
    def test_small_mesh_still_gets_heat(self):
        """An 18 cm figure: heat runs on a 10x copy instead of falling back to distance weights."""
        scene = self.scene("capsule", radius=3, height=18)
        node = next(n for n in scene.data["nodes"] if n["type"] == "object")
        HQ_OPS["rig_template"](scene, {"template": "ue5_manny", "ids": [node["id"]]})
        out = HQ_OPS["auto_weights"](scene, {"ids": [node["id"]]})
        heat = int(out["summary"].split("heat ")[1].split(",")[0])
        self.assertGreater(heat, 0, out)


if __name__ == "__main__":
    unittest.main()
