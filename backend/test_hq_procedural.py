"""py -3 -m backend.test_hq_procedural  (re-runs itself under the Forge engine Python if bpy is missing)."""
import json
import os
import subprocess
import sys

try:
    import bpy
except ImportError:
    py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UEFN-Ducky", "forge-engine", "py", "python.exe")
    if not os.path.exists(py):
        sys.exit("bpy engine not installed (forge-engine/py/python.exe missing)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"import sys,runpy;sys.path.insert(0,{root!r});runpy.run_module('backend.test_hq_procedural',run_name='__main__')"
    sys.exit(subprocess.call([py, "-c", code]))

from backend import hq as hq_pkg
from backend.engine.ops import procedural as eng
from backend.engine.ops.mesh import manifold
from backend.hq import procedural as hq_proc

NAMES = {"scatter", "gn", "sdf_blend", "drop", "procedural"}


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def made(name):
    return bpy.data.objects[name]


def short(r):
    assert len(json.dumps(hq_pkg.compact(r))) <= 300, hq_pkg.compact(r)


def test_scatter_surface_and_path():
    fresh()
    bpy.ops.mesh.primitive_plane_add(size=4)
    ground = bpy.context.active_object
    bpy.ops.mesh.primitive_ico_sphere_add(radius=0.1, subdivisions=1)
    pebble = bpy.context.active_object
    pebble.data.materials.append(bpy.data.materials.new("Stone"))
    q = hq_proc.params("scatter", {"on": "g", "items": ["p"], "density": 5, "seed": 3})
    r = eng.scatter({"g": ground, "p": pebble}, q)
    assert r["count"] == 80, r  # 16 m2 x 5
    out = made("Scatter")
    assert len(out.data.polygons) == 80 * len(pebble.data.polygons) and out.data.materials[0].name == "Stone"
    assert all(-2.3 < v.co.x < 2.3 for v in out.data.vertices)
    short(r)
    r2 = eng.scatter({"g": ground, "p": pebble}, dict(q, name="Again"))
    assert [tuple(v.co) for v in made("Again").data.vertices] == [tuple(v.co) for v in out.data.vertices], "seed not stable"
    r = eng.scatter({"p": pebble}, hq_proc.params("scatter", {"items": ["p"], "path": [[0, 0, 0], [4, 0, 0], [4, 3, 0]],
                                                               "spacing": 1, "name": "Posts"}))
    assert r["count"] == 8, r  # 0..4 on x, then 1..3 on y
    assert r2["count"] == 80


def test_gn_presets_and_spec():
    fresh()
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.active_object
    base = len(plane.data.polygons)
    r = eng.gn({"pl": plane}, hq_proc.params("gn", {"id": "pl", "preset": "pebbles", "inputs": {"pts.Density": 20}}))
    assert len(plane.data.polygons) > base + 20 * 10, r  # ~80 icospheres of 20 faces
    short(r)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    r = eng.gn({"c": cube}, {"id": "c", "preset": "wire"})
    assert r["tris"] > 12 * 6 * 2 and manifold(cube), r
    spec = {"nodes": [{"id": "grid", "type": "GeometryNodeMeshGrid", "in": {"Size X": 3, "Vertices X": 4, "Vertices Y": 4}}],
            "links": [["grid.Mesh", "out.Geometry"]]}
    eng.gn({}, {"spec": spec, "name": "Grid"})
    assert len(made("Grid").data.polygons) == 9
    assert not bpy.data.node_groups, "node group left behind"
    for bad in ({"preset": "x"}, {}):
        try:
            hq_proc.params("gn", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    try:
        eng.gn({}, {"spec": {"nodes": [{"id": "a", "type": "Nope"}], "links": []}})
        raise AssertionError("bad node type accepted")
    except ValueError as e:
        assert "bl_idname" in str(e)


def test_sdf_blend_seams():
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5)
    a = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=1.2, location=(0, 0, 0.6))
    b = bpy.context.active_object
    r = eng.sdf_blend({"a": a, "b": b}, hq_proc.params("sdf_blend", {"ids": ["a", "b"], "fillet": 0.08, "voxel": 0.03}))
    out = made(r["summary"].split(" into ")[1].split(":")[0])
    assert r["manifold"] and r["seam"] > 10, r
    assert 1000 < r["tris"] < 60000, r
    short(r)


def test_drop_settles():
    fresh()
    bpy.ops.mesh.primitive_plane_add(size=10)
    floor = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 2))
    box = bpy.context.active_object
    r = eng.drop({"f": floor, "b": box}, hq_proc.params("drop", {"ids": ["b"], "onto": ["f"], "frames": 50}))
    z = r["matrices"]["b"][2][3]
    assert 0.2 < z < 0.35, r  # resting on the floor (unit 1: metres)
    assert set(r["matrices"]) == {"b"}
    short({k: v for k, v in r.items() if k != "matrices"})


def test_procedural_kinds():
    budget = {"building": 20000, "wall": 200, "floor": 12, "stairs": 200, "fence": 300, "pipe": 2000, "cable": 3000}
    for kind in hq_proc.KINDS:
        fresh()
        q = hq_proc.params("procedural", {"kind": kind, "floors": 3, "opening": "door"})
        r = eng.procedural({**q, "unit": 0.01})
        ob = made(r["name"])
        assert r["name"].startswith("SM_"), r
        assert 0 < r["tris"] <= budget[kind], (kind, r)
        assert manifold(ob) or kind == "fence", (kind, r)  # a fence is separate closed posts and rails
        short(r)
        if kind in ("wall", "floor"):  # kits sit on their min corner
            assert abs(min(v.co.x for v in ob.data.vertices)) < 1e-6 and abs(min(v.co.y for v in ob.data.vertices)) < 1e-6
    fresh()
    r = eng.procedural({**hq_proc.params("procedural", {"kind": "building", "floors": 6}), "unit": 0.01})
    assert "6F" in r["summary"] and abs(max(v.co.z for v in made(r["name"]).data.vertices) - 18.0) < 1e-3, r
    try:
        hq_proc.params("procedural", {"kind": "castle"})
        raise AssertionError("bad kind accepted")
    except ValueError:
        pass


def test_ops_registered():
    assert NAMES <= set(hq_pkg.HQ_OPS), sorted(NAMES - set(hq_pkg.HQ_OPS))
    assert "procedural: drop gn procedural scatter sdf_blend" in hq_pkg.run_hq(None, "help", {})["summary"]
    for n in NAMES:
        assert hq_pkg.run_hq(None, "help", {"op": n})["summary"].startswith(n)
        assert callable(getattr(eng, "op_" + n))


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
