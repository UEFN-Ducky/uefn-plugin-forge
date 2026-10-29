"""HQ vehicles through forge_hq with the real Blender engine: every car_part kind, whole cars, car_swap.

Needs the engine (runtime.install()); the error-message tests run without it. Run: py -3 -m backend.test_hq_vehicle
"""
import math
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

from . import tools
from .check import check_mesh
from .engine import client, runtime
from .hq import vehicle as hqv
from .hq.qa import _zfight
from .scene import dispatch, forget, load

_READY = runtime.engine_status()["state"] == "ready"
# solid parts are closed; text, glass sheets and open sweeps still close here, so every kind is checked
SOLID = hqv.KINDS


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def open_edges(mesh):
    edges = Counter()
    for face in mesh.faces:
        for a, b in zip(face, face[1:] + face[:1]):
            edges[(min(a, b), max(a, b))] += 1
    return sum(1 for c in edges.values() if c != 2)


class _Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-vehicle-")
        cls.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")})
        cls.env.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns
        cls.name = cls.__name__ + "Scene"
        cls.t["forge_scene_new"](name=cls.name)

    @classmethod
    def tearDownClass(cls):
        client.stop()
        forget(cls.name)
        cls.env.stop()
        cls.tmp.cleanup()

    def hq(self, op, **params):
        out = self.t["forge_hq"](name=self.name, op=op, params=params)
        self.assertTrue(out.get("ok", True), out)
        self.assertLessEqual(len(str(out)), 300, out)
        return out


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class Parts(_Engine):
    def test_every_kind_builds_with_socket_and_pivot(self):
        x = 0
        for kind in hqv.KINDS:
            with self.subTest(kind=kind):
                out = self.hq("car_part", kind=kind, style="sport" if kind in ("spoiler", "diffuser") else "sedan", at=[x, 0, 0])
                scene = load(self.name)
                node = scene.node(out["ids"][0])
                self.assertIn(kind, out["summary"])
                self.assertEqual(node["transform"]["pos"], [x, 0, 0], "origin is the mount point")
                self.assertEqual(list(node["sockets"])[0], hqv.SOCKETS.get(kind, "Mount"))
                self.assertEqual(node["sockets"][hqv.SOCKETS.get(kind, "Mount")]["pos"], [0.0, 0.0, 0.0])
                self.assertEqual(node["car_part"]["kind"], kind)
                mesh = scene.local_mesh(node)
                self.assertGreater(mesh.tri_count(), 50)
                if kind in SOLID:
                    self.assertEqual(open_edges(mesh), 0, "%s has open edges" % kind)
                errors = [i["message"] for i in check_mesh(scene.world_mesh(node), node["id"], has_collision=True, pivot_is_base=True)
                          if i["severity"] == "error"]
                self.assertEqual(errors, [], "%s: non-manifold or zero-area faces" % kind)
                x += 300

    def test_no_zfight_any_kind_or_style(self):  # qa's z-fight cells are world-anchored: build off the origin
        for kind in hqv.KINDS:
            for style in hqv.STYLES:
                with self.subTest(kind=kind, style=style):
                    out = self.hq("car_part", kind=kind, style=style, at=[37.3, -11.7, 5.1])
                    scene = load(self.name)
                    self.assertEqual(_zfight([scene.world_mesh(scene.node(out["ids"][0]))], 0.1), 0)

    def test_documented_part_qa(self):  # the call vehicles.md gives for one part
        part = self.hq("car_part", kind="wheel", tire="225/45R17", spokes="10")
        out = self.t["forge_hq"](name=self.name, op="qa", params={"id": part["ids"][0], "profile": "uefn", "budget": "vehicle"})
        self.assertEqual(out["errors"], 0, out)

    def test_tire_radius_matches_code(self):
        out = self.hq("car_part", kind="tire", tire="225/45R17")
        self.assertIn("r=31.7cm", out["summary"])
        self.assertIn("tread 64", out["summary"])
        self.assertIn("socket Hub", out["summary"])
        scene = load(self.name)
        lo, hi = scene.world_mesh(scene.node(out["ids"][0])).bounds()
        r = (hi[2] - lo[2]) / 2
        self.assertAlmostEqual(r, hqv.tire_size("225/45R17")[3], delta=31.715 * 0.02)
        self.assertAlmostEqual(hi[0] - lo[0], 22.5, delta=22.5 * 0.06, msg="section width")

    def test_materials_slots(self):
        out = self.hq("car_part", kind="headlight", style="sport")
        scene = load(self.name)
        used = {m for m in scene.local_mesh(scene.node(out["ids"][0])).face_mat}
        names = {m["id"]: m for m in scene.data["materials"]}
        self.assertTrue({"mcar_glass", "mcar_chrome", "mcar_emissive", "mcar_plastic"} <= used, used)
        self.assertGreater(names["mcar_emissive"]["emissive_strength"], 0)
        self.assertEqual(names["mcar_paint"]["clearcoat"], 1.0) if "mcar_paint" in names else None

    def test_right_side_mirrors(self):
        left = self.hq("car_part", kind="mirror", side="left")
        right = self.hq("car_part", kind="mirror", side="right")
        scene = load(self.name)
        a = scene.local_mesh(scene.node(left["ids"][0])).bounds()
        b = scene.local_mesh(scene.node(right["ids"][0])).bounds()
        self.assertGreater(a[1][0], 20)
        self.assertLess(b[0][0], -20)


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class Look(_Engine):
    """Render stats of the centre of a look render (the part fills it): paint never clips, glass is not speckled."""

    def centre(self, kind, **params):
        from PIL import Image
        out = self.hq("car_part", kind=kind, **params)
        with patch.object(tools, "_attach_captures", lambda r: r):  # no Ducky frontend here
            got = dispatch(self.name, "look", {"look": out["ids"], "px": 256}, actor="agent")
        img = Image.open(got.get("path") or got["paths"][0]).convert("L")
        w, h = img.size
        px = [v / 255.0 for v in img.crop((w * 2 // 5, h * 2 // 5, w * 3 // 5, h * 3 // 5)).getdata()]
        mean = sum(px) / len(px)
        return mean, (sum((v - mean) ** 2 for v in px) / len(px)) ** 0.5, sum(v > 0.98 for v in px) / len(px)

    def test_paint_does_not_clip(self):
        for paint in ("#b0101a", "#f2f2f2", "#0c0c10"):  # red, near-white, black
            with self.subTest(paint=paint):
                mean, _, clipped = self.centre("hood", paint=paint)
                self.assertLess(mean, 0.9, "hood washes out")
                self.assertLess(clipped, 0.15, "hood clips to white")  # a sun highlight (~6 % on white) is fine, a sheet is not

    def test_glass_is_smooth_dark(self):
        mean, std, _ = self.centre("window")
        self.assertLess(std, 0.08, "glass is speckled")
        self.assertLess(mean, 0.6, "glass is not tinted")


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class Cars(_Engine):
    def build(self, body, **extra):
        out = self.hq("car", body=body, **extra)
        scene = load(self.name)
        gid = out["ids"][0]
        return out, scene, gid, hqv.car_nodes(scene, gid)

    def check_dims(self, body, dims):
        out, scene, gid, nodes = self.build(body)
        length, width, height, tris = hqv.measure(scene, nodes)
        for got, want in zip((length, width, height), dims):
            self.assertAlmostEqual(got, want, delta=want * 0.05, msg="%s dims %s" % (body, (length, width, height)))
        self.assertTrue(40000 <= tris <= 120000, "%s tris %d" % (body, tris))
        print("\n%s" % out)
        return out, scene, gid, nodes

    def test_sedan(self):
        out, scene, gid, nodes = self.check_dims("sedan", (480, 185, 145))
        wheels = [n for n in nodes if n.get("car_slot", "").startswith("wheel_")]
        self.assertEqual(len(wheels), 4)
        rig = next(r for r in scene.data["rigs"] if r["id"] == out["ids"][1])
        bones = {b["name"]: b for b in rig["bones"]}
        for n in wheels:
            tag = n["car_slot"][6:]
            self.assertEqual(n["skin"]["bone"], tag + "_wheel")
            for a, b in zip(bones[tag + "_wheel"]["head"], n["transform"]["pos"]):
                self.assertAlmostEqual(a, b, places=3, msg="bone head at the hub centre")
            lo, _ = scene.world_mesh(n).bounds()
            self.assertAlmostEqual(lo[2], 0.0, delta=0.5, msg="wheel touches the ground")
        self.assertIn("root", bones)
        self.assertTrue(any(b.startswith("door_") for b in bones))
        self.assertTrue(all((n.get("hq") or {}).get("lods") for n in nodes))
        self.assertTrue(any(n.get("layer") == "collision" and n.get("parent") == gid for n in scene.data["nodes"]))
        gaps = [n for n in nodes if n.get("car_slot") == "body_hood"]
        self.assertEqual(len(gaps), 1)

    def test_limo_and_swap(self):
        out, scene, gid, nodes = self.check_dims("limo", (720, 190, 148))
        qa = self.t["forge_hq"](name=self.name, op="qa", params={"id": gid, "profile": "uefn", "budget": "hero"})
        self.assertEqual(qa["errors"], 0, qa)
        self.assertTrue(qa["pass"], qa)
        before = {n["car_slot"]: dict(n["transform"]) for n in nodes if n.get("car_slot", "").startswith("wheel_")}
        swap = self.hq("car_swap", car=gid, slot="rim", spokes="mesh")
        self.assertIn("4 x wheel", swap["summary"])
        scene = load(self.name)
        after = {n["car_slot"]: n for n in hqv.car_nodes(scene, gid) if n.get("car_slot", "").startswith("wheel_")}
        self.assertEqual(set(after), set(before))
        for slot, n in after.items():
            self.assertEqual(n["transform"], before[slot], "socket transform kept")
            self.assertEqual(n["car_part"]["params"]["spokes"], "mesh")
            self.assertEqual(n["skin"]["bone"], slot[6:] + "_wheel")
        mirror = self.hq("car_swap", car="Car_Limo", slot="mirror_L", style="sport")
        self.assertIn("1 x mirror", mirror["summary"])

    def test_documented_limo_batch(self):  # vehicles.md: car -> car_swap(mirror, classic) -> qa
        car = self.hq("car", body="limo", length_cm=720, paint="#101014", parts={"rim": "mesh"}, name="DocLimo")
        gid = car["ids"][0]
        self.hq("car_swap", car=gid, slot="mirror", style="classic")
        qa = self.t["forge_hq"](name=self.name, op="qa", params={"id": gid, "profile": "uefn", "budget": "hero"})
        self.assertEqual(qa["errors"], 0, qa)
        scene = load(self.name)
        self.assertEqual(_zfight([scene.world_mesh(n) for n in hqv.car_nodes(scene, gid)], 0.1), 0)

    def test_kart(self):
        out, scene, gid, nodes = self.build("kart")
        self.assertIn("4 wheels rigged", out["summary"])
        self.assertTrue(any(n.get("car_slot") == "seat" for n in nodes))


class Errors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-vehicle-err-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        dispatch("", "scene_new", {"name": "VehErr"})

    def tearDown(self):
        forget("VehErr")
        self.env.stop()
        self.tmp.cleanup()

    def test_messages_say_how_to_fix(self):
        scene = load("VehErr")
        with self.assertRaisesRegex(ValueError, "Did you mean 'wheel'"):
            hqv.car_part(scene, {"kind": "wheell"})
        with self.assertRaisesRegex(ValueError, "225/45R17"):
            hqv.car_part(scene, {"kind": "tire", "tire": "big"})
        with self.assertRaisesRegex(ValueError, "Unknown param"):
            hqv.car_part(scene, {"kind": "tire", "colour": 1})
        with self.assertRaisesRegex(ValueError, "spokes"):
            hqv.car_part(scene, {"kind": "rim", "spokes": "lots"})
        with self.assertRaisesRegex(ValueError, "Did you mean 'limo'"):
            hqv.car(scene, {"body": "limmo"})
        with self.assertRaisesRegex(ValueError, "No car"):
            hqv.car_swap(scene, {"car": "zzz", "slot": "rim"})

    def test_tire_size(self):
        w, side, rim, r = hqv.tire_size("225/45R17")
        self.assertAlmostEqual(r, 21.59 + 10.125, places=2)
        self.assertAlmostEqual(hqv.tire_size("265/70R17")[3], 21.59 + 18.55, places=2)


if __name__ == "__main__":
    unittest.main()
