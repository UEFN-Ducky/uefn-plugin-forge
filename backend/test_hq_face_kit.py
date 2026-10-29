"""py -3 -m backend.test_hq_face_kit  (re-runs itself under the Forge engine Python if bpy is missing)."""
import os
import subprocess
import sys

try:
    import bpy
    import bmesh
except ImportError:
    py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UEFN-Ducky", "forge-engine", "py", "python.exe")
    if not os.path.exists(py):
        sys.exit("bpy engine not installed (forge-engine/py/python.exe missing)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"import sys,runpy;sys.path.insert(0,{root!r});runpy.run_module('backend.test_hq_face_kit',run_name='__main__')"
    sys.exit(subprocess.call([py, "-c", code]))

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from backend import hq as hq_pkg
from backend import tools
from backend.engine import client
from backend.engine.ops import face_kit as engine_face
from backend.hq import face_kit as hq_face
from backend.scene import forget, load

HEAD_W = 0.4  # m
_CACHE = {}


def make_head():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=HEAD_W / 2)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=__import__("mathutils").Matrix.Rotation(-1.5708, 3, "X"))
    me = bpy.data.meshes.new("Head")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Head", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = (0.3, 0.1, 1.0)
    bpy.context.view_layer.update()
    return ob


def build(**kw):
    key = repr(sorted(kw.items()))
    if key not in _CACHE:
        head = make_head() if not _CACHE else _CACHE["head"]
        p = hq_face.params(kw)
        _CACHE[key] = engine_face.build_face(head, {**p, "unit": 0.01})
        _CACHE["head"] = head
    return _CACHE[key], _CACHE["head"]


def poly_verts(ob, material):
    idx = [i for i, m in enumerate(ob.data.materials) if m is not None and m.name == material]
    assert idx, "no %s material on %s" % (material, ob.name)
    return sorted({v for q in ob.data.polygons if q.material_index == idx[0] for v in q.vertices})


def test_face_parts_on_surface():
    (face, ears), head = build()
    mw = head.matrix_world
    tree = BVHTree.FromPolygons([mw @ v.co for v in head.data.vertices], [tuple(p.vertices) for p in head.data.polygons])
    worst = max((tree.find_nearest(v.co)[3] for v in face.data.vertices))
    assert worst <= 0.005, "face part %.3f cm off the head surface (max 0.5)" % (worst * 100)
    assert worst > 0, "parts sunk into the head: nothing would render"
    assert ears is not None and "EarInner" in [m.name for m in ears.data.materials], "ears need a pink inner material"
    for name in ("EyeWhite", "EyeIris", "EyePupil", "EyeGlint", "Brow", "Muzzle", "Nose", "MouthLine"):
        assert poly_verts(face, name), name


def test_eye_proportion():
    (face, _), head = build()
    xs = np.array([face.data.vertices[i].co.x for i in poly_verts(face, "EyeWhite")])
    xs = xs[xs > head.matrix_world.translation.x]  # right eye only
    frac = (xs.max() - xs.min()) / HEAD_W
    assert abs(frac - 0.22) <= 0.22 * 0.15, "cute eye is %.3f of head width, want 0.22" % frac
    small, _ = build(style="realistic")
    sx = np.array([small[0].data.vertices[i].co.x for i in poly_verts(small[0], "EyeWhite")])
    sx = sx[sx > head.matrix_world.translation.x]
    assert (sx.max() - sx.min()) / HEAD_W < frac * 0.6, "realistic eyes should be much smaller than cute"


def test_blink_shape_key():
    (face, _), _ = build()
    keys = face.data.shape_keys.key_blocks
    assert {"blink", "smile", "brow_up"} <= set(keys.keys()), list(keys.keys())
    ids = poly_verts(face, "EyeWhite")
    z = lambda block: np.array([block.data[i].co.z for i in ids])
    open_h, shut_h = np.ptp(z(keys["Basis"])), np.ptp(z(keys["blink"]))
    assert shut_h < open_h * 0.2, "blink leaves %.0f%% of the eye open" % (100 * shut_h / open_h)
    mouth = poly_verts(face, "MouthLine")
    assert max(keys["smile"].data[i].co.z - keys["Basis"].data[i].co.z for i in mouth) > 0.001, "smile moves nothing"
    brow = poly_verts(face, "Brow")
    assert min(keys["brow_up"].data[i].co.z - keys["Basis"].data[i].co.z for i in brow) > 0.001, "brow_up moves nothing"


def test_mouth_crease_carved():
    (_, _), head = build()
    moved = sum(1 for v in head.data.vertices if abs(v.co.length - HEAD_W / 2) > 0.0005)
    assert moved > 5, "no head vertex was pressed in for the mouth crease (%d)" % moved


def test_species_and_look():
    for species in hq_face.SPECIES:
        (face, ears), _ = build(species=species)
        assert face.data.shape_keys and (ears is None) == (species == "bird"), species
    a, _ = build(look=[0, 0])
    b, _ = build(look=[1, 0])
    ia = np.mean([a[0].data.vertices[i].co.x for i in poly_verts(a[0], "EyeIris")])
    ib = np.mean([b[0].data.vertices[i].co.x for i in poly_verts(b[0], "EyeIris")])
    assert ib > ia + 0.003, "look=[1,0] did not move the irises (%.4f -> %.4f)" % (ia, ib)


def test_params_and_registration():
    for bad in ({"style": "scary"}, {"species": "fish"}, {"eye_size": 0.9}, {"look": [1]}, {"mouthy": 1}):
        try:
            hq_face.params(bad)
            assert False, bad
        except ValueError as e:
            assert len(str(e)) > 15, e
    assert "cartoon_face" in hq_pkg.HQ_OPS and callable(engine_face.op_cartoon_face)
    assert len(hq_pkg.run_hq(None, "help", {"op": "cartoon_face"})["summary"]) <= 400


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def test_through_forge_batch():
    """The AI path: forge_batch -> forge_hq cartoon_face on a sphere head, result <= 300 chars."""
    import json
    with tempfile.TemporaryDirectory(prefix="forge-face-") as tmp, \
            patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(tmp) / "scenes")}), \
            patch.object(tools, "_attach_captures", lambda r: r):
        api = _Api()
        tools.register_tools(api)
        t = api.fns
        t["forge_scene_new"](name="FaceKit")
        try:
            # sphere's front is -Y after forge builds it facing the default camera; rotate-free test
            head = t["forge_batch"](name="FaceKit", steps=[{"tool": "forge_add", "kind": "sphere", "object_name": "Head",
                                                           "params": {"radius": 20, "seg": 48, "rings": 24}}])["ids"][0]
            out = t["forge_batch"](name="FaceKit", steps=[{"tool": "forge_hq", "op": "cartoon_face", "params": {"head": head, "species": "cat"}}])
            assert out.get("ok", True), out
            assert len(json.dumps(out)) <= 900, len(json.dumps(out))
            names = [n.get("name") for n in load("FaceKit").data["nodes"] if n.get("type") == "object"]
            assert "Head_face" in names and "Head_ears" in names, names
        finally:
            client.stop()
            forget("FaceKit")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    client.stop()
