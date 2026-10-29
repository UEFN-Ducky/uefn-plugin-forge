"""HQ tufts through forge_batch with the real Blender engine: fused single mass, pointed tips, triangle budget.

Needs the engine (runtime.install()); the error-message tests run without it. Run: py -3 -m backend.test_hq_tufts
"""
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from . import tools
from .engine import client, runtime
from .hq import tufts as hqt
from .scene import dispatch, forget, load

_READY = runtime.engine_status()["state"] == "ready"
BUDGET = 8000


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def islands(mesh):
    parent = list(range(len(mesh.verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for face in mesh.faces:
        for a, b in zip(face, face[1:]):
            parent[find(a)] = find(b)
    return len({find(v) for face in mesh.faces for v in face})


def island_sizes(mesh):
    """Vertex count of each connected piece, biggest first (failure messages)."""
    import collections
    parent = list(range(len(mesh.verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for face in mesh.faces:
        for a, b in zip(face, face[1:]):
            parent[find(a)] = find(b)
    return sorted(collections.Counter(find(v) for face in mesh.faces for v in face).values(), reverse=True)


def tip_defects(mesh, centre, min_reach):
    """Angle defect (rad) at every local-maximum vertex of distance from centre, farther than min_reach."""
    near = {}
    angle = {}
    for face in mesh.faces:
        for k, v in enumerate(face):
            a, b, c = (mesh.verts[face[(k + j) % len(face)]] for j in (0, 1, -1))
            u = [b[i] - a[i] for i in range(3)]
            w = [c[i] - a[i] for i in range(3)]
            cos = sum(x * y for x, y in zip(u, w)) / ((math.sqrt(sum(x * x for x in u)) * math.sqrt(sum(x * x for x in w))) or 1.0)
            angle[v] = angle.get(v, 0.0) + math.acos(max(-1.0, min(1.0, cos)))
            near.setdefault(v, set()).update(face[(k + 1) % len(face)::1][:1] + [face[k - 1]])
    reach = {v: math.dist(mesh.verts[v], centre) for v in near}
    return [2 * math.pi - angle[v] for v in near
            if reach[v] >= min_reach and all(reach[v] >= reach[n] for n in near[v])]


@unittest.skipUnless(_READY, "Blender engine not installed; runtime.install() provisions it")
class Tufts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-tufts-")
        cls.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")})
        cls.env.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns
        cls.name = "TuftsHead"
        cls.t["forge_scene_new"](name=cls.name)
        cls.head = cls.t["forge_batch"](name=cls.name, steps=[
            {"tool": "forge_add", "kind": "sphere", "object_name": "Head", "params": {"radius": 30, "seg": 32, "rings": 16}}])["ids"][0]
        out = cls.t["forge_batch"](name=cls.name, steps=[
            {"tool": "forge_hq", "op": "tufts", "id": cls.head, "params": {"mane": "lion", "budget": BUDGET}}])
        assert out.get("ok", True), out
        cls.out = out
        scene = load(cls.name)
        cls.mesh = scene.world_mesh(scene.node(cls.head))
        print("\ntufts: %s verts %s faces %s" % (out, len(cls.mesh.verts), len(cls.mesh.faces)))
        lo, hi = cls.mesh.bounds()
        cls.centre = [(a + b) / 2 for a, b in zip(lo, hi)]

    @classmethod
    def tearDownClass(cls):
        client.stop()
        forget(cls.name)
        cls.env.stop()
        cls.tmp.cleanup()

    def test_tufts_fused_single_island(self):
        self.assertEqual(islands(self.mesh), 1, "floating parts, sizes %s" % island_sizes(self.mesh)[:12])
        lo, hi = self.mesh.bounds()
        self.assertGreater(max(hi[i] - lo[i] for i in range(3)), 60 * 1.15, "tufts stick out of the 30 cm head")

    def test_tips_pointed(self):
        defects = tip_defects(self.mesh, self.centre, 30 * 1.25)
        self.assertGreaterEqual(len(defects), 8, "outer tips found: %d" % len(defects))
        defects.sort(reverse=True)
        top = defects[:len(defects) // 2 or 1]
        self.assertGreater(sum(top) / len(top), 0.35, "tip angle defect (a sphere vertex is ~0.01): %s" % [round(d, 2) for d in top[:6]])

    def test_budget(self):
        tris = self.mesh.tri_count()
        self.assertLessEqual(tris, BUDGET * 1.05)
        self.assertGreater(tris, BUDGET * 0.3, "decimated far below the budget: tips would be lost")
        self.assertLessEqual(len(str(self.out)), 300)

    def test_layers_outer_longer(self):
        layers = self.t["forge_hq"](name=self.name, op="tufts", params={
            "id": self.head, "region": {"sphere": [0, 0, 0, 12]}, "count": 6, "verbose": True}).get("layers")
        self.assertEqual(len(layers), 2)
        self.assertGreater(layers[0], layers[1])


class Errors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-tufts-err-")
        self.env = patch.dict(os.environ, {"FORGE_SCENES_DIR": self.tmp.name})
        self.env.start()
        dispatch("", "scene_new", {"name": "TuftsErr"})
        self.nid = dispatch("TuftsErr", "add", {"kind": "sphere", "params": {"radius": 20}})["id"]

    def tearDown(self):
        forget("TuftsErr")
        self.env.stop()
        self.tmp.cleanup()

    def test_messages_say_how_to_fix(self):
        scene = load("TuftsErr")
        with self.assertRaisesRegex(ValueError, "Did you mean 'lion'"):
            hqt.tufts(scene, {"id": self.nid, "mane": "lon"})
        with self.assertRaisesRegex(ValueError, "Give region"):
            hqt.tufts(scene, {"id": self.nid})
        with self.assertRaisesRegex(ValueError, "No object"):
            hqt.tufts(scene, {"id": "zzz", "mane": "lion"})
        with self.assertRaisesRegex(ValueError, "Unknown param"):
            hqt.tufts(scene, {"id": self.nid, "mane": "lion", "colour": 1})


if __name__ == "__main__":
    unittest.main()
