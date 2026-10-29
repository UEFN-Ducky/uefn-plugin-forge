"""py -3 -m backend.test_hq_anatomy  (re-runs itself under the Forge engine Python if bpy is missing)."""
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
    code = f"import sys,runpy;sys.path.insert(0,{root!r});runpy.run_module('backend.test_hq_anatomy',run_name='__main__')"
    sys.exit(subprocess.call([py, "-c", code]))

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from backend import hq as hq_pkg
from backend.engine.ops import anatomy as engine_anatomy
from backend.hq import anatomy as hq_anatomy

# Side-view silhouette area (m^2) of creature(kind="big_cat", length=220), measured when the
# spec was tuned (ANATOMY_SHOW=big_cat prints it); about 28% of the 2.2 x 1.2 m bounding box.
LION_SIDE_AREA = 0.75
_CACHE = {}


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make(kind="big_cat", **kw):
    key = (kind, tuple(sorted(kw.items())))
    if key not in _CACHE:
        if not _CACHE:
            fresh()  # one scene for the whole run: later builds must not free earlier ones
        p = hq_anatomy.params({"kind": kind, "length": 220, **kw})
        _CACHE[key] = (engine_anatomy.build_creature({**p, "unit": 0.01}), p)
    return _CACHE[key]


def watertight(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    ok = all(len(e.link_faces) == 2 for e in bm.edges)
    parts = len({v.index for v in bm.verts}) and _components(bm)
    bm.free()
    return ok, parts


def _components(bm):
    seen, n = set(), 0
    for v in bm.verts:
        if v.index in seen:
            continue
        n += 1
        stack = [v]
        while stack:
            c = stack.pop()
            if c.index in seen:
                continue
            seen.add(c.index)
            stack += [e.other_vert(c) for e in c.link_edges]
    return n


def side_silhouette(ob, cell=0.01):
    """Boolean image of the body seen from the side (rays along X), and its area in m^2."""
    me = ob.data
    me.calc_loop_triangles()
    verts = [v.co.copy() for v in me.vertices]
    tree = BVHTree.FromPolygons(verts, [tuple(p.vertices) for p in me.polygons])
    lo = Vector(np.min([tuple(v) for v in verts], axis=0))
    hi = Vector(np.max([tuple(v) for v in verts], axis=0))
    ys = np.arange(lo.y, hi.y + cell, cell)
    zs = np.arange(lo.z, hi.z + cell, cell)
    img = np.zeros((len(zs), len(ys)), bool)
    for j, z in enumerate(zs):
        for i, y in enumerate(ys):
            img[j, i] = tree.ray_cast(Vector((lo.x - 1, y, z)), Vector((1, 0, 0)))[0] is not None
    return img, img.sum() * cell * cell


def islands(img):
    seen = np.zeros_like(img)
    n = 0
    for start in zip(*np.nonzero(img)):
        if seen[start]:
            continue
        n += 1
        stack = [start]
        while stack:
            j, i = stack.pop()
            if seen[j, i] or not img[j, i]:
                continue
            seen[j, i] = True
            stack += [(j + dj, i + di) for dj in (-1, 0, 1) for di in (-1, 0, 1)
                      if 0 <= j + dj < img.shape[0] and 0 <= i + di < img.shape[1]]
    return n


def test_creature_watertight_and_quads():
    ob, _ = make()
    ok, parts = watertight(ob)
    s = engine_anatomy._stats(ob)
    assert ok, "creature has open edges"
    assert parts == 1, "%d loose parts" % parts
    assert s["quads"] / s["faces"] >= 0.9, s
    assert 5000 <= s["faces"] <= 8000, s


def test_creature_silhouette():
    ob, _ = make()
    img, area = side_silhouette(ob)
    assert abs(area / LION_SIDE_AREA - 1) <= 0.20, "side silhouette %.3f m2, reference %.2f" % (area, LION_SIDE_AREA)
    assert islands(img) == 1, "silhouette has isolated islands"
    d = ob.dimensions
    assert abs(d.y - 2.2) < 0.25 and d.z > 0.9, tuple(d)


def front_depth(ob, z, x=0.0):
    """Y where a ray flying +Y at (x, z) first hits the body: the snout profile at that height."""
    me = ob.data
    verts = [v.co.copy() for v in me.vertices]
    tree = BVHTree.FromPolygons(verts, [tuple(p.vertices) for p in me.polygons])
    hit = tree.ray_cast(Vector((x, -3, z)), Vector((0, 1, 0)))[0]
    return hit.y if hit else None


def test_creature_smooth_shaded():
    ob, _ = make()
    assert all(q.use_smooth for q in ob.data.polygons), "flat-shaded faces"
    assert not [m for m in ob.modifiers], "modifier left unapplied"


def test_default_budget():
    ob, p = make()
    assert p["faces"] == 7000
    assert len(ob.data.polygons) <= 8000, len(ob.data.polygons)  # budget counts quad faces


def test_big_cat_proportions():
    # lion ranges, as fractions of body height / length (measured on the 220 cm cat)
    j = hq_anatomy.spec_for("big_cat")["joints"]
    assert 1.5 <= j["elbow"][3] / 0.078 <= 1.7 and 1.5 <= j["wrist"][3] / 0.05 <= 1.7, "forelegs ~1.6x"
    assert 1.25 <= j["head"][3] / 0.15 <= 1.35, "head ~1.3x"
    assert j["chest"][3] > j["belly"][3] * 1.3 and j["belly"][2] <= 0.65, "deep chest, low belly"
    ob, p = make()
    H = p["height"] * 0.01
    zs = [v.co.z for v in ob.data.vertices if abs(v.co.y) < 0.8 and v.co.z < 0.3 * H]
    assert zs  # legs exist
    assert 0.55 <= ob.dimensions.z / ob.dimensions.y <= 0.80, tuple(ob.dimensions)


def test_head_parts_present():
    ob, p = make()
    eyes = [o for o in bpy.data.objects if "_eye_" in o.name and o.type == "MESH"]
    assert len(eyes) == 2, [o.name for o in bpy.data.objects]
    assert eyes[0].data.materials[0].name == "Eye" and "Eye" not in [m.name for m in ob.data.materials]
    assert sorted(m.name for m in ob.data.materials) == ["Hide", "Nose"]
    assert any(q.material_index == 1 for q in ob.data.polygons), "no nose leather faces"
    # mouth slit: deeper than the same body without a face, with the lips standing proud of it
    H = p["height"] * 0.01
    mouth = p["spec"]["head"]["mouth"][0]
    z = mouth[2] * H
    plain, _ = make(face=False)
    slit = front_depth(ob, z) - front_depth(plain, z)
    lips = [front_depth(ob, z + d) - front_depth(plain, z + d) for d in (0.03 * H, -0.03 * H)]
    assert slit >= 0.003, "mouth slit only %.4f m deep" % slit
    assert all(l < slit - 0.002 for l in lips), (slit, lips)
    # ears: part of the same closed shell, standing above the skull
    top = max(v.co.z for v in ob.data.vertices)
    assert top > p["spec"]["joints"]["head"][2] * H + p["spec"]["joints"]["head"][3] * H, top


def test_head_texel_density():
    ob, _ = make()
    me = ob.data
    head = set(me["head_faces"])
    uv = me.uv_layers.active.data
    dens = {True: [0.0, 0.0], False: [0.0, 0.0]}  # uv area, surface area
    for q in me.polygons:
        pts = [uv[i].uv for i in range(q.loop_start, q.loop_start + q.loop_total)]
        area = 0.5 * abs(sum(a.x * b.y - b.x * a.y for a, b in zip(pts, pts[1:] + pts[:1])))
        d = dens[q.index in head]
        d[0] += area
        d[1] += q.area
    ratio = (dens[True][0] / dens[True][1]) / (dens[False][0] / dens[False][1])
    assert head and ratio >= 1.5, "head texel density only %.2fx the body" % ratio
    assert max(abs(c) for q in me.polygons for i in range(q.loop_start, q.loop_start + q.loop_total) for c in (uv[i].uv.x - 0.5, uv[i].uv.y - 0.5)) <= 0.5001, "UVs outside 0-1"


def test_kinds_and_params():
    assert {"big_cat", "canine", "horse"} <= set(hq_anatomy.kinds())
    for bad in ({"kind": "dragon"}, {"build": "fat"}, {"length": 0}, {"legs": 4}):
        try:
            hq_anatomy.params(bad)
            assert False, bad
        except ValueError as e:
            assert "Kinds" in str(e) or "must" in str(e) or "Params" in str(e), e


def test_ops_registered():
    assert "creature" in hq_pkg.HQ_OPS
    assert hq_pkg.run_hq(None, "help", {"op": "creature"})["summary"].startswith("creature")
    assert callable(engine_anatomy.op_creature)


def show(kind="big_cat", step=4):
    """ASCII side view for eyeballing a spec: ANATOMY_SHOW=canine py -3 -m backend.test_hq_anatomy"""
    img, area = side_silhouette(make(kind)[0])
    print("%s side area %.3f m2" % (kind, area))
    for row in img[::-step]:
        print("".join("#" if c else "." for c in row[::2]))


if __name__ == "__main__":
    if os.environ.get("ANATOMY_SHOW"):
        show(os.environ["ANATOMY_SHOW"])
        sys.exit()
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
