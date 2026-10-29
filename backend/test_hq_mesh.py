"""py -3 -m backend.test_hq_mesh  (re-runs itself under the Forge engine Python if bpy is missing)."""
import os
import subprocess
import sys
import tempfile

try:
    import bpy
    import bmesh
except ImportError:
    py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UEFN-Ducky", "forge-engine", "py", "python.exe")
    if not os.path.exists(py):
        sys.exit("bpy engine not installed (forge-engine/py/python.exe missing)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # the embedded python ignores cwd/PYTHONPATH, so put the repo on sys.path explicitly
    code = f"import sys,runpy;sys.path.insert(0,{root!r});runpy.run_module('backend.test_hq_mesh',run_name='__main__')"
    sys.exit(subprocess.call([py, "-c", code]))

import json
import math

from backend import hq as hq_pkg
from backend.engine.ops import mesh as engine_mesh
from backend.hq import mesh as hq_mesh

NAMES = {"remesh", "retopo", "sculpt_hd", "modifiers", "lods", "boolean", "hardsurface", "sculpt", "origin"}


def short(r):
    assert len(json.dumps(hq_pkg.compact(r))) <= 300, hq_pkg.compact(r)


class hq:  # validate like the hq layer, run the bpy fn directly on an object
    params = staticmethod(hq_mesh.params)

    @staticmethod
    def run(name, ob, given=None):
        return engine_mesh.FNS[name](ob, hq_mesh.params(name, given))


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def sphere(name, loc=(0, 0, 0), r=0.5, seg=24):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc, segments=seg, ring_count=seg // 2)
    ob = bpy.context.active_object
    ob.name = name
    return ob


def blob():
    a = sphere("a", (0, 0, 0))
    b = sphere("b", (0.5, 0, 0))
    c = sphere("c", (0.25, 0.4, 0))
    for ob in (a, b, c):
        ob.select_set(True)
    bpy.context.view_layer.objects.active = a
    for m in ("Wood", "Steel"):
        a.data.materials.append(bpy.data.materials.new(m))
    bpy.ops.object.join()
    return a


def watertight(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    ok = all(len(e.link_faces) == 2 for e in bm.edges)
    bm.free()
    return ok


def test_remesh_closed():
    fresh()
    ob = blob()
    slots = len(ob.material_slots)
    r = hq.run("remesh", ob, {"voxel": 0.04})
    assert watertight(ob), r
    assert len(ob.material_slots) == slots >= 2, "material slots lost"
    assert len(ob.data.uv_layers) >= 1, "UVs not reprojected"


def test_retopo_quads():
    fresh()
    ob = blob()
    hq.run("remesh", ob, {"voxel": 0.04})
    r = hq.run("retopo", ob, {"faces": 4000, "symmetry": True, "method": "quadriflow"})
    assert r["quads"] / r["faces"] >= 0.9, r
    assert watertight(ob), "retopo left open edges"
    assert len(ob.material_slots) >= 2


def test_retopo_after_remesh_with_a_tiny_edge():
    # Oct 28 2026: a remeshed lion had 4 edges under 0.1 mm where parts pinched; quadriflow
    # refused it as "not manifold" though every edge had two faces.
    fresh()
    ob = blob()
    hq.run("remesh", ob, {"voxel": 0.04})
    edge = ob.data.edges[0]
    a, b = (ob.data.vertices[i] for i in edge.vertices)
    a.co = b.co + (b.co - a.co).normalized() * 5e-5
    ob.data.update()
    r = hq.run("retopo", ob, {"faces": 2000})
    assert r["quads"] / r["faces"] >= 0.9, r


def test_retopo_that_raises_leaves_no_snapshot():
    # Oct 28 2026: quadriflow raised "wrong init" instead of returning CANCELLED, so the
    # __src snapshot stayed in the scene and a demo lion doubled to 45k tris.
    fresh()
    ob = blob()
    hq.run("remesh", ob, {"voxel": 0.04})

    def boom(**_):
        raise RuntimeError("Error: QuadriFlow: wrong init")

    class Obj:
        quadriflow_remesh = staticmethod(boom)

        def __getattr__(self, n):
            return getattr(bpy.ops.object, n)

    class Ops:
        object = Obj()

        def __getattr__(self, n):
            return getattr(bpy.ops, n)

    class Fake:
        ops = Ops()

        def __getattr__(self, n):
            return getattr(bpy, n)

    real, engine_mesh.bpy = engine_mesh.bpy, Fake()
    try:
        hq.run("retopo", ob, {"faces": 2000})
        raise AssertionError("retopo should raise when quadriflow raises")
    except RuntimeError as exc:
        assert "quadriflow failed" in str(exc), exc
    finally:
        engine_mesh.bpy = real
    assert not [o.name for o in bpy.data.objects if o.name.endswith("__src")], "snapshot left behind"


def test_sculpt_hd_levels():
    fresh()
    ob = sphere("s")
    before = [tuple(v.co) for v in ob.data.vertices]
    base = len(ob.data.polygons)
    path = os.path.join(tempfile.mkdtemp(), "hd.blend")
    r = hq.run("sculpt_hd", ob, {"levels": 4, "detail": "scales", "path": path})
    assert r["sidecar_faces"] >= 16 * base, r
    assert os.path.exists(path)
    assert len(ob.data.polygons) == base and [tuple(v.co) for v in ob.data.vertices] == before
    try:
        hq.params("sculpt_hd", {"detail": "x", "levels": 7})
        assert False
    except ValueError:
        pass


def test_modstack_bevel_weighted():
    fresh()
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.active_object
    base = len(ob.data.polygons)
    r = hq.run("modifiers", ob, {"stack": [{"type": "bevel", "segments": 3}, "weighted_normal"]})
    assert [m.type for m in ob.modifiers] == ["BEVEL", "WEIGHTED_NORMAL"]
    assert ob.modifiers[0].segments == 3
    assert r["faces"] > base and len(ob.data.polygons) == base, r
    try:
        hq.run("modifiers", ob, {"stack": ["wobble"]})
        assert False
    except ValueError as e:
        assert "Options" in str(e)


def test_lods_ratios():
    fresh()
    ob = sphere("rock", seg=64)
    for e in ob.data.edges:  # seam ring on the equator
        a, b = (ob.data.vertices[i].co.z for i in e.vertices)
        if abs(a) < 1e-4 and abs(b) < 1e-4:
            e.use_seam = True
    seam = {tuple(round(c, 3) for c in ob.data.vertices[i].co)
            for e in ob.data.edges if e.use_seam for i in e.vertices}
    assert seam
    full = sum(len(p.vertices) - 2 for p in ob.data.polygons)
    r = hq.run("lods", ob, {"ratios": [1, 0.5, 0.25, 0.1], "nanite": True})
    assert r["node_data"]["tris"] == r["tris"] and not [o for o in bpy.data.objects if "LOD" in o.name]
    r = engine_mesh.lods(ob, {"ratios": [1, 0.5, 0.25, 0.1], "nanite": True, "keep": True})
    assert r["nanite"] and r["tris"][0] == full
    for t, ratio in zip(r["tris"], r["ratios"]):
        assert abs(t / full - ratio) <= 0.10, r
    lod = bpy.data.objects[r["lods"][-1]]
    have = {tuple(round(c, 3) for c in v.co) for v in lod.data.vertices}
    assert seam <= have, f"{len(seam - have)} seam verts lost"


def test_lods_cleanup():
    fresh()
    ob = sphere("rock", seg=32)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.split_edges(bm, edges=bm.edges[:])  # every face its own island: thousands of doubles
    bm.verts.new((5, 5, 5))  # a loose vert
    bm.to_mesh(ob.data)
    bm.free()
    before = len(ob.data.vertices)
    plain = hq.run("lods", ob, {"ratios": [1, 0.5]})
    assert len(ob.data.vertices) == before and "removed" in plain and plain["removed"] == 0
    r = hq.run("lods", ob, {"ratios": [1, 0.5], "cleanup": True})
    short(r)
    assert r["removed"] > 0 and len(ob.data.vertices) < before, r
    assert watertight(ob) and len(ob.data.vertices) == 32 * 15 + 2, len(ob.data.vertices)


def test_retopo_methods():
    fresh()
    ob = sphere("s", seg=32)
    full = sum(len(p.vertices) - 2 for p in ob.data.polygons)
    r = hq.run("retopo", ob, {"method": "decimate", "ratio": 0.5})
    short(r)
    assert r["summary"] == "retopo decimate 0.5" and abs(r["tris"] / full - 0.5) <= 0.1, r
    assert len(ob.data.uv_layers) == 1 and r["uvs"] == 1 and not ob.vertex_groups, "UVs lost"
    fresh()
    ob = sphere("s", seg=32)
    r = hq.run("retopo", ob, {"method": "decimate", "faces": 300})
    assert abs(r["tris"] - 300) <= 40, r
    fresh()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=20, y_subdivisions=20)
    ob = bpy.context.active_object
    r = hq.run("retopo", ob, {"method": "planar"})
    short(r)
    assert r["faces"] < 20 and len(ob.data.uv_layers) == 1, r
    try:
        hq.params("retopo", {"method": "wobble"})
        assert False
    except ValueError as e:
        assert "planar" in str(e)


def test_ops_registered():
    assert NAMES <= set(hq_pkg.HQ_OPS), sorted(NAMES - set(hq_pkg.HQ_OPS))
    assert not hasattr(hq_mesh, "OPS")
    text = hq_pkg.run_hq(None, "help", {})["summary"]
    assert "mesh: boolean hardsurface lods modifiers origin remesh retopo sculpt sculpt_hd" in text, text
    for n in NAMES:
        assert hq_pkg.run_hq(None, "help", {"op": n})["summary"].startswith(n)
    for n in NAMES:  # engine side discoverable as op_<name>
        assert callable(getattr(engine_mesh, "op_" + n))
    r = engine_mesh.modifiers(bpy.context.active_object if bpy.context.active_object else _cube(), {"stack": ["bevel"]})
    assert r["node_data"] == {"stack": [{"type": "bevel"}]}


def _cube(size=2.0, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    return bpy.context.active_object


def test_modstack_presets():
    fresh()
    ob = _cube()
    r = hq.run("modifiers", ob, {"stack": "midpoly"})
    bevel, wn = ob.modifiers
    assert (bevel.type, wn.type) == ("BEVEL", "WEIGHTED_NORMAL"), r
    assert abs(bevel.width - 0.02) < 1e-6 and bevel.segments == 3 and bevel.harden_normals and bevel.use_clamp_overlap
    assert abs(bevel.angle_limit - math.radians(30)) < 1e-4 and wn.mode == "FACE_AREA" and wn.keep_sharp
    assert 12 < r["tris"] < 500, r
    short(r)
    r = hq.run("modifiers", ob, {"stack": "subd"})
    assert [m.type for m in ob.modifiers] == ["BEVEL", "SUBSURF"] and r["tris"] > 500, r
    assert ob.modifiers[0].segments == 2
    assert hq.run("modifiers", ob, {"stack": "render"})["tris"] > 200
    for bad in ("chunky",):
        try:
            hq.params("modifiers", {"stack": bad})
            raise AssertionError("bad preset accepted")
        except ValueError as e:
            assert "midpoly" in str(e)


def test_bevel_weight_angle():
    fresh()
    ob = _cube()
    hq.run("modifiers", ob, {"stack": [{"type": "bevel", "weight_angle": 45}, "triangulate"]})
    assert ob.modifiers[0].limit_method == "ANGLE" and abs(ob.modifiers[0].angle_limit - math.radians(45)) < 1e-4
    assert ob.modifiers[1].type == "TRIANGULATE"


def test_boolean_shapes_and_ids():
    fresh()
    ob = _cube()
    base = engine_mesh._tris(ob)
    r = engine_mesh.boolean(ob, hq.params("boolean", {"cutters": [{"shape": "cylinder", "size": [0.6, 0.6, 3]}]}))
    assert r["manifold"] and r["tris"] > base, r
    assert not [o for o in bpy.data.objects if o.name.startswith("hq_cutter")], "cutter left behind"
    short(r)
    knob = _cube(0.5, (0, 0, 1.1))
    r = engine_mesh.boolean(ob, {"cutters": ["k"], "mode": "union"}, {"k": knob})
    assert r["manifold"] and max(v.co.z for v in ob.data.vertices) > 1.3, r
    try:
        hq.params("boolean", {"cutters": []})
        raise AssertionError("empty cutters accepted")
    except ValueError:
        pass


def test_hardsurface_kits():
    for preset in hq_mesh.KITS:
        fresh()
        ob = _cube()
        r = hq.run("hardsurface", ob, {"preset": preset, "depth": 0.03})
        assert r["manifold"], (preset, r)
        assert 12 < r["tris"] < 20000, (preset, r)
        assert r["count"] == {"panel": 4, "vent": 5, "grille": 16, "bolt": 4}[preset], r
        top = max(v.co.z for v in ob.data.vertices)
        assert (top > 1.01) == (preset == "bolt"), (preset, top)  # bolts stand proud, cuts go in
        assert not [o for o in bpy.data.objects if o.name.startswith("hq_kit")]
        short(r)
    fresh()  # Oct 29 2026: bolts unioned over a panel cut opened 4 edges when cleanup welded
    ob = _cube(1.0)
    hq.run("hardsurface", ob, {"preset": "panel", "at": [0, -1, 0], "size": 0.7, "depth": 0.006})
    r = hq.run("hardsurface", ob, {"preset": "bolt", "at": [0, -1, 0], "size": 0.8, "depth": 0.01})
    assert r["manifold"], r
    fresh()
    ob = _cube()  # a side face, picked by `at`
    hq.run("hardsurface", ob, {"preset": "vent", "at": [0, -3, 0], "size": 0.8, "depth": 0.05})
    assert min(v.co.y for v in ob.data.vertices) == -1 and any(abs(v.co.y + 0.95) < 1e-3 for v in ob.data.vertices)


def test_sculpt_chain():
    fresh()
    ob = sphere("s", r=1.0)
    faces = len(ob.data.polygons)
    r = hq.run("sculpt", ob, {"ops": ["inflate:0.1", {"detail": {"strength": 0.05, "scale": 0.3, "seed": 2}},
                                     "symmetrize:+x", "smooth:2", "multires:1"]})
    assert r["steps"] == 5 and len(ob.data.polygons) >= 3 * faces, r
    assert max(v.co.length for v in ob.data.vertices) > 1.03, "inflate did nothing"
    xs = sorted(round(v.co.x, 3) for v in ob.data.vertices)
    assert all(abs(a + b) < 2e-3 for a, b in zip(xs, reversed(xs))), "not symmetric"
    assert watertight(ob)
    short(r)
    for bad in (["wobble:1"], ["symmetrize:x"]):
        try:
            hq.run("sculpt", ob, {"ops": bad})
            raise AssertionError(bad)
        except ValueError:
            pass


def test_origin_corner_and_ground():
    fresh()
    ob = _cube(2.0, (1, 2, 3))
    world = sorted(tuple(round(c, 4) for c in ob.matrix_world @ v.co) for v in ob.data.vertices)
    r = hq.run("origin", ob, {"at": "corner"})
    assert [round(c, 4) for c in r["move"]] == [-1, -1, -1], r
    assert tuple(round(c, 4) for c in ob.location) == (0, 1, 2)
    assert sorted(tuple(round(c, 4) for c in ob.matrix_world @ v.co) for v in ob.data.vertices) == world, "object moved"
    r = hq.run("origin", ob, {"at": "bottom", "ground": True})
    assert abs(min((ob.matrix_world @ v.co).z for v in ob.data.vertices)) < 1e-5 and abs(ob.location.z) < 1e-5, r
    short(r)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
