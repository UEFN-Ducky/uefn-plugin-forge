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

from backend import hq as hq_pkg
from backend.engine.ops import mesh as engine_mesh
from backend.hq import mesh as hq_mesh

NAMES = {"remesh", "retopo", "sculpt_hd", "modifiers", "lods"}


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
    r = hq.run("retopo", ob, {"faces": 4000, "symmetry": True})
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


def test_ops_registered():
    assert NAMES <= set(hq_pkg.HQ_OPS), sorted(NAMES - set(hq_pkg.HQ_OPS))
    assert not hasattr(hq_mesh, "OPS")
    text = hq_pkg.run_hq(None, "help", {})["summary"]
    assert "mesh: lods modifiers remesh retopo sculpt_hd" in text, text
    for n in NAMES:
        assert hq_pkg.run_hq(None, "help", {"op": n})["summary"].startswith(n)
    for n in ("remesh", "retopo", "sculpt_hd", "modifiers", "lods"):  # engine side discoverable as op_<name>
        assert callable(getattr(engine_mesh, "op_" + n))
    r = engine_mesh.modifiers(bpy.context.active_object if bpy.context.active_object else _cube(), {"stack": ["bevel"]})
    assert r["node_data"] == {"stack": [{"type": "bevel"}]}


def _cube():
    bpy.ops.mesh.primitive_cube_add()
    return bpy.context.active_object


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
