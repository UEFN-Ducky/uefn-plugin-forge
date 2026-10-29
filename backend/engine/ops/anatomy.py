"""bpy creature bodies for Forge HQ: anatomy spec -> skin-modifier skeleton -> remesh -> retopo -> sculpt.

The spec (assets/anatomy/<kind>.json, passed in by hq/anatomy.py) holds joints as [u, w, v, r]:
u along the body (0 tail .. 1 nose, fraction of length), w sideways and v up (fractions of height),
r radius (fraction of height). Joints with w != 0 are mirrored. Needs bpy.
"""
import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import bridge
from .mesh import _ctx, _stats, _strokes, remesh, retopo

BUILD = {"lean": 0.92, "heavy": 1.12}  # scales radii and muscle strength


def _at(spec_at, length, height, side=1):
    u, w, v = spec_at
    return [side * w * height, -(u - 0.5) * length, v * height]  # -Y is the front


def _sides(at):
    return (1, -1) if abs(at[1]) > 1e-6 else (1,)


def _skeleton(spec, length, height, scale):
    """Mirrored joint positions, radii and bone edges."""
    co, rad, index = [], [], {}
    for name, (u, w, v, r) in spec["joints"].items():
        for s in ((1, -1) if abs(w) > 1e-6 else (0,)):
            index[(name, s)] = len(co)
            co.append(_at((u, w, v), length, height, s or 1))
            rad.append(r * height * scale)

    def pick(name, s):
        i = index.get((name, s), index.get((name, 0)))
        if i is None:
            raise ValueError("Spec bone names unknown joint %r. Joints: %s" % (name, ", ".join(spec["joints"])))
        return i

    edges = {tuple(sorted((pick(a, s), pick(b, s)))) for a, b in spec["bones"] for s in (1, -1)}
    return co, rad, sorted(edges), index[(spec["root"], 0)]


def _apply(ob, mod):
    with _ctx(ob):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _skin_body(spec, length, height, scale):
    co, rad, edges, root = _skeleton(spec, length, height, scale)
    me = bpy.data.meshes.new("creature")
    me.from_pydata(co, edges, [])
    ob = bpy.data.objects.new("creature", me)
    bpy.context.scene.collection.objects.link(ob)
    skin = ob.modifiers.new("skin", "SKIN")
    skin.branch_smoothing = 1.0
    skin.use_smooth_shade = True
    for i, r in enumerate(rad):
        me.skin_vertices[0].data[i].radius = (r, r)
    me.skin_vertices[0].data[root].use_root = True
    _apply(ob, skin)
    sub = ob.modifiers.new("sub", "SUBSURF")
    sub.levels = sub.render_levels = 2
    _apply(ob, sub)
    return ob


def _strokes_for(items, length, height, scale, sign):
    out = []
    for m in items:
        for s in _sides(m["at"]):
            out.append({"co": _at(m["at"], length, height, s), "radius": m["r"] * height,
                        "strength": sign * m["k"] * height * scale})
    return out


def _tree(ob):
    me = ob.data
    return BVHTree.FromPolygons([v.co.copy() for v in me.vertices], [tuple(p.vertices) for p in me.polygons])


def _snap(ob, strokes):
    """Move stroke centres onto the surface: spec points are fractions, the skin's real skin is where it is."""
    tree = _tree(ob)
    return [{**s, "co": list(tree.find_nearest(Vector(s["co"]))[0])} for s in strokes]


def _smooth(ob, factor=0.5, repeat=2):
    m = ob.modifiers.new("smooth", "SMOOTH")
    m.factor, m.iterations = factor, repeat
    _apply(ob, m)


def _shade(ob):
    ob.data.polygons.foreach_set("use_smooth", np.ones(len(ob.data.polygons), bool))
    ob.data.update()
    m = ob.modifiers.new("wn", "WEIGHTED_NORMAL")  # big faces win: no faceting on the flat quads
    m.keep_sharp = False
    _apply(ob, m)


def _material(name, color, roughness):
    m = bpy.data.materials.new(name)
    m.diffuse_color, m.roughness = (*color, 1.0), roughness
    return m


def _ear(spec, length, height, side):
    """Thin ellipsoid cupped toward the front, tilted out; half sunk in the skull."""
    h = spec["head"]["ear_size"] * height
    c = _at(spec["head"]["ear"], length, height, side)
    c[2] -= 0.1 * h
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=1.0)
    for v in bm.verts:
        x, y, z = v.co
        v.co = (x * 0.8 * h, y * 0.2 * h + 0.5 * 0.8 * h * x * x, z * h)  # y: bowl, edges swept back
    me = bpy.data.meshes.new("ear")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("ear", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location, ob.rotation_euler = c, (0.25, side * 0.35, 0)
    return ob


def _fuse_ears(ob, spec, length, height):
    for side in (1, -1):
        ear = _ear(spec, length, height, side)
        m = ob.modifiers.new("ears", "BOOLEAN")
        m.operation, m.solver, m.object = "UNION", "EXACT", ear
        _apply(ob, m)
        bpy.data.objects.remove(ear, do_unlink=True)


def _eyes(ob, spec, length, height):
    h = spec["head"]
    tree = _tree(ob)
    mat = _material("Eye", (0.55, 0.35, 0.08), 0.1)
    r = h["eye_r"] * height
    out = []
    for side, tag in ((1, "L"), (-1, "R")):
        p = Vector(tree.find_nearest(Vector(_at(h["eye"], length, height, side)))[0])
        # the socket is already carved: the ball sits on its floor
        bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=p, segments=16, ring_count=10)
        eye = bpy.context.active_object
        eye.name = "%s_eye_%s" % (ob.name, tag)
        eye.data.materials.append(mat)
        eye.data.polygons.foreach_set("use_smooth", np.ones(len(eye.data.polygons), bool))
        out.append(eye)
    return out


def _face_strokes(spec, length, height, scale):
    """Eye sockets in, nose leather out, a deep mouth slit between raised lips."""
    h = spec["head"]
    er = h["eye_r"]
    strokes = _strokes_for([{"at": h["eye"], "r": er * 2.2, "k": er * 0.8}], length, height, scale, -1)
    strokes += _strokes_for([{"at": h["nose"], "r": 0.035, "k": 0.006}], length, height, scale, 1)
    a, b = np.array(h["mouth"][0]), np.array(h["mouth"][1])
    for t in np.linspace(0, 1, 6):
        at = list(a + (b - a) * t)
        slit = [{"at": at, "r": 0.03, "k": 0.012}]  # radii span a few quads: 12k faces cannot carve less
        strokes += _strokes_for(slit, length, height, scale, -1)
        for dz in (0.03, -0.03):  # lips either side of the slit
            lip = [{"at": [at[0], at[1], at[2] + dz], "r": 0.03, "k": 0.004}]
            strokes += _strokes_for(lip, length, height, scale, 1)
    return strokes


def _head_mask(ob, spec, length, height):
    """Faces within 1.4 head radii of the head-to-snout segment."""
    me = ob.data
    c = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("center", c)
    c = c.reshape(-1, 3)
    a = np.array(_at(spec["joints"]["head"][:3], length, height))
    b = np.array(_at(spec["joints"]["snout"][:3], length, height))
    t = np.clip(((c - a) @ (b - a)) / ((b - a) @ (b - a)), 0, 1)
    return np.linalg.norm(c - (a + t[:, None] * (b - a)), axis=1) < 1.4 * spec["joints"]["head"][3] * height


def _nose_slot(ob, spec, length, height):
    me = ob.data
    c = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("center", c)
    near = np.linalg.norm(c.reshape(-1, 3) - np.array(_at(spec["head"]["nose"], length, height)), axis=1) < 0.035 * height
    me.polygons.foreach_set("material_index", near.astype(np.int32))


def _unwrap(ob, head, density=1.7):
    """Smart UV: head faces and body faces unwrapped apart, head scaled up, then packed together."""
    me = ob.data
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    bpy.context.view_layer.objects.active = ob
    with _ctx(ob):
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(me)
        for part in (head, ~head):
            bpy.ops.mesh.select_all(action="DESELECT")
            bm.faces.ensure_lookup_table()
            for i in np.nonzero(part)[0]:
                bm.faces[int(i)].select_set(True)
            bmesh.update_edit_mesh(me)
            bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
        bpy.ops.object.mode_set(mode="OBJECT")
    uv = me.uv_layers.active.data
    xy = np.empty(len(me.loops) * 2, np.float32)
    uv.foreach_get("uv", xy)
    xy = xy.reshape(-1, 2)
    loops = np.concatenate([np.arange(p.loop_start, p.loop_start + p.loop_total) for p in me.polygons if head[p.index]])
    mid = xy[loops].mean(axis=0)
    xy[loops] = mid + (xy[loops] - mid) * density
    uv.foreach_set("uv", xy.ravel())
    with _ctx(ob):
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(rotate=False, margin=0.004)
        bpy.ops.object.mode_set(mode="OBJECT")
    me["head_faces"] = [int(i) for i in np.nonzero(head)[0]]


def build_creature(p):
    """Body only: returns the bpy object (metres, feet on z=0, nose toward -Y)."""
    spec = p["spec"]
    unit = float(p.get("unit", 0.01))
    length = float(p["length"]) * unit
    height = float(p["height"]) * unit
    scale = BUILD[p.get("build", "lean")]
    ob = _skin_body(spec, length, height, scale)
    ob.name = p.get("name") or p.get("kind") or "creature"
    ob.data.name = ob.name
    cm = lambda v: v / unit  # noqa: E731
    # voxel remesh welds the skin joins; retopo gives even quads
    remesh(ob, {"voxel": cm(length) / 110, "unit": unit})
    me = ob.data
    if len(me.vertices) - len(me.edges) + len(me.polygons) != 2:  # handles between fat limbs and body hang quadriflow
        raise ValueError("Spec skin has holes or handles (limbs merge into a loop with the body); "
                         "shrink the limb/chest radii or move the joints apart")
    faces = int(p.get("faces", 7000))
    for k in (1.0, 1.15, 0.85, 1.3):  # quadriflow fails on some targets of the same mesh: nudge the count
        try:
            retopo(ob, {"faces": int(faces * k)})
            break
        except RuntimeError:
            if k == 1.3:
                raise
    # sculpt: muscle masses out, smooth, then creases in so the smooth cannot fill them
    _strokes(ob, _strokes_for(spec["muscles"], length, height, scale, 1))
    _smooth(ob)
    face = p.get("face", True)
    if face:
        _fuse_ears(ob, spec, length, height)
    creases = _snap(ob, _strokes_for(spec["creases"], length, height, scale, -1))
    if face:
        creases += _snap(ob, _face_strokes(spec, length, height, scale))
    _strokes(ob, creases)
    if face:
        ob.data.materials.clear()  # the ear boolean can leave empty slots
        ob.data.materials.append(_material("Hide", (0.62, 0.45, 0.25), 0.8))
        ob.data.materials.append(_material("Nose", (0.18, 0.1, 0.1), 0.35))
        _nose_slot(ob, spec, length, height)
        _eyes(ob, spec, length, height)
        _unwrap(ob, _head_mask(ob, spec, length, height))
    _shade(ob)
    return ob


_CUBES = {}


def _cube_sphere(n):
    """All-quad sphere (6 n^2 quads, n even so each cube face has a centre vertex): unit points + faces."""
    if n not in _CUBES:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=n - 1, use_grid_fill=True)
        bm.verts.ensure_lookup_table()
        pts = np.array([v.co.normalized() for v in bm.verts], np.float64)
        faces = [[v.index for v in f.verts] for f in bm.faces]
        bm.free()
        _CUBES[n] = (pts, faces)
    return _CUBES[n]


def _spline(pts, t):
    """Catmull-Rom through pts (uniform), t in 0..1 -> (N, 3)."""
    pts = np.asarray(pts, np.float64)
    k = len(pts) - 1
    s = np.clip(t, 0, 1) * k
    i = np.minimum(s.astype(int), k - 1)
    u = (s - i)[:, None]
    p0, p1, p2, p3 = pts[np.maximum(i - 1, 0)], pts[i], pts[i + 1], pts[np.minimum(i + 2, k)]
    return 0.5 * (2 * p1 + (p2 - p0) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u ** 2 + (3 * p1 - p0 - 3 * p2 + p3) * u ** 3)


def _tube(n, path, radii, blunt=3.0):
    """Closed quad tube along a spline: path points, radii [(t, rx, ry)]; ends are rounded caps.

    Returns verts, faces and per-vertex (unit sphere point, centre, ry) for material picks.
    """
    pts, faces = _cube_sphere(n)
    x, y, z = pts.T
    t = (z + 1) / 2
    c = _spline(path, t)
    tan = _spline(path, np.clip(t + 0.01, 0, 1)) - _spline(path, np.clip(t - 0.01, 0, 1))
    tan /= np.linalg.norm(tan, axis=1)[:, None] + 1e-12
    ref = np.where((abs(tan[:, :1]) > 0.9), np.array([[0, 0, 1.0]]), np.array([[1.0, 0, 0]]))
    n1 = ref - (ref * tan).sum(1)[:, None] * tan
    n1 /= np.linalg.norm(n1, axis=1)[:, None]
    n2 = np.cross(tan, n1)
    r = np.asarray(radii, np.float64)
    rx, ry = np.interp(t, r[:, 0], r[:, 1]), np.interp(t, r[:, 0], r[:, 2])
    hl = np.sqrt(x * x + y * y)
    g = np.clip(1 - np.abs(z) ** blunt, 0, 1) ** (1 / blunt)  # blunt > 2 squares the caps off
    k = np.where(hl > 1e-9, g / np.maximum(hl, 1e-9), 0.0)
    v = c + (x * k * rx)[:, None] * n1 + (y * k * ry)[:, None] * n2
    return v, faces, pts, c, ry


def _ellipsoid(n, centre, radii):
    pts, faces = _cube_sphere(n)
    return np.asarray(centre) + pts * np.asarray(radii), faces, pts


def _toes(pts, v, centre, radii, grooves=3, depth=0.2):
    """Press `grooves` vertical creases into the front of a paw (toward -Y)."""
    x, y, z = pts.T
    front = np.clip(-y, 0, 1) ** 2 * np.clip(1.2 - np.abs(z + 0.1) * 1.4, 0, 1)
    cut = np.zeros(len(pts))
    for xg in np.linspace(-0.55, 0.55, grooves):
        cut += np.exp(-(((x - xg) / 0.13) ** 2))
    k = 1 - depth * front * np.clip(cut, 0, 1)
    return np.asarray(centre) + (v - np.asarray(centre)) * k[:, None]


# preset: torso width, leg length, tail length, tail radius, default head_ratio
KINDS = {"lion": (1.0, 1.0, 1.0, 0.045, 0.36), "cat": (0.85, 1.0, 1.5, 0.032, 0.34),
         "dog": (0.95, 1.15, 0.9, 0.035, 0.30), "bear": (1.25, 0.9, 0.25, 0.07, 0.30)}
POSES = ("sit", "stand")


def _parts(kind, pose, head_ratio):
    """Part list in height units (H=1): (name, slot, verts, faces, pts) pieces plus head and tail positions."""
    w, leg, tl, tr, _ = KINDS[kind]
    parts = []
    neck = 1 - head_ratio * 0.9
    sit = pose == "sit"
    if sit:
        body = [(0, 0.10, 0.02), (0, 0.08, 0.20), (0, 0.02, 0.45), (0, -0.03, neck)]
        rad = [(0, .17 * w, .17 * w), (.3, .26 * w, .25 * w), (.65, .20 * w, .18 * w), (1, .13 * w, .12 * w)]
        head = [0, -0.07, 1 - head_ratio / 2]
        arm_top, arm_bot = 0.5, 0.07
        fx, fy = 0.115 * w + 0.01, -0.15
    else:
        z0 = 0.30 * leg + 0.16
        body = [(0, 0.28, z0), (0, 0.12, z0 + 0.02), (0, -0.08, z0 + 0.03), (0, -0.20, z0 + 0.12)]
        rad = [(0, .14 * w, .14 * w), (.3, .17 * w, .17 * w), (.7, .16 * w, .16 * w), (1, .12 * w, .12 * w)]
        head = [0, -0.26, 1 - head_ratio / 2]
        arm_top, arm_bot = z0 + 0.04, 0.07
        fx, fy = 0.09 * w + 0.01, -0.15
    torso = _tube(20, body, rad, 2.5)
    pts, c, ry = torso[2], torso[3], torso[4]
    belly_face = (-(torso[0][:, 1] - c[:, 1]) / ry > 0.35) & (pts[:, 2] > -0.55) & (pts[:, 2] < 0.8)
    parts.append(("torso", belly_face, torso[0], torso[1], None, None))
    for sx in (-fx, fx):
        v, f, _p, _c, _r = _tube(8, [(sx, -0.02, arm_top), (sx, fy + 0.04, arm_bot)],
                                 [(0, .085 * w, .085 * w), (1, .068 * w, .068 * w)], 4.0)
        parts.append(("leg", 0, v, f, None, None))
        c = (sx, fy, 0.05)
        r = (0.075 * w, 0.10, 0.05)
        v, f, p = _ellipsoid(12, c, r)
        parts.append(("paw", 2, _toes(p, v, c, r), f, None, None))
    if sit:
        for sx in (-0.205 * w, 0.205 * w):
            c, r = (sx, 0.07, 0.17), (0.12 * w, 0.17, 0.17)
            v, f, _p = _ellipsoid(10, c, r)
            parts.append(("haunch", 0, v, f, None, None))
            c, r = (sx * 1.02, -0.07, 0.04), (0.07 * w, 0.095, 0.045)
            v, f, p = _ellipsoid(12, c, r)
            parts.append(("paw", 2, _toes(p, v, c, r), f, None, None))
    else:
        for sx in (-0.09 * w - 0.01, 0.09 * w + 0.01):
            v, f, _p, _c, _r = _tube(8, [(sx, 0.24, z0 + 0.04), (sx, 0.26, 0.07)],
                                     [(0, .09 * w, .09 * w), (1, .068 * w, .068 * w)], 4.0)
            parts.append(("leg", 0, v, f, None, None))
            c, r = (sx, 0.22, 0.05), (0.07 * w, 0.095, 0.05)
            v, f, p = _ellipsoid(12, c, r)
            parts.append(("paw", 2, _toes(p, v, c, r), f, None, None))
    if sit:
        tail = [(0, 0.15, 0.10), (0.14, 0.27, 0.06), (0.22, 0.38, 0.14), (0.16, 0.46, 0.30), (0.20, 0.50, 0.40)]
    else:
        tail = [(0, 0.30, 0.50), (0.06, 0.40, 0.45), (-0.04, 0.50, 0.30), (0.06, 0.55, 0.15)]
    tail = [(x, 0.15 + (y - 0.15) * tl, z) if sit else (x, 0.30 + (y - 0.30) * tl, z) for x, y, z in tail]
    v, f, _p, _c, _r = _tube(8, tail, [(0, tr, tr), (.5, tr * .9, tr * .9), (1, tr * .75, tr * .75)], 4.0)
    parts.append(("tail", 0, v, f, None, None))
    return parts, head, list(tail[-1])


def build_cartoon_body(p):
    """One quad mesh (bridge metres from cm height, feet on z=0, front toward -Y) with slots body, belly, paw, ear_inner."""
    kind = p.get("kind", "lion")
    pose = p.get("pose", "sit")
    height = float(p.get("height", 60.0))
    unit = float(p.get("unit", 0.01))  # height is cm; the bridge is metres
    head_ratio = float(p.get("head_ratio") or KINDS[kind][4])
    parts, head, tip = _parts(kind, pose, head_ratio)
    verts, faces, slots = [], [], []
    for name, slot, v, f, _pts, _extra in parts:
        v = v.copy()
        v[:, 2] = np.maximum(v[:, 2], 0.0)  # sitting weight rests flat on the ground plane
        base = len(verts)
        verts.extend((v * height * unit).tolist())
        faces.extend([[i + base for i in face] for face in f])
        if name == "torso":  # slot is a per-vertex belly mask here
            slots.extend([1 if all(slot[i] for i in face) else 0 for face in f])
        else:
            slots.extend([slot] * len(f))
    me = bpy.data.meshes.new(p.get("name") or kind)
    me.from_pydata(verts, [], faces)
    me.polygons.foreach_set("material_index", np.array(slots, np.int32))
    me.polygons.foreach_set("use_smooth", np.ones(len(faces), bool))
    for name, color, rough in (("body", (0.86, 0.55, 0.2), 0.8), ("belly", (0.97, 0.88, 0.7), 0.8),
                               ("paw", (0.75, 0.45, 0.17), 0.7), ("ear_inner", (0.95, 0.6, 0.6), 0.8)):
        me.materials.append(_material(name, color, rough))
    me.update()
    ob = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob["head_socket"] = [round(h * height, 2) for h in head]
    ob["tail_tip"] = [round(t * height, 2) for t in tip]
    return ob


def op_cartoon_body(p):
    ob = build_cartoon_body(p)
    s = _stats(ob)
    head, tip = list(ob["head_socket"]), list(ob["tail_tip"])
    fmt = lambda a: "(%s)" % ",".join("%g" % round(c, 1) for c in a)  # noqa: E731
    return {"summary": "CartoonBody %.1fk tris %d%% quads, head@%s tail_tip@%s" % (
        s["tris"] / 1000, round(100 * s["quads"] / s["faces"]), fmt(head), fmt(tip)),
        "head": head, "tail_tip": tip, **s, "scene": bridge.dump(p["scene"])}


def op_creature(p):
    ob = build_creature(p)
    return {"summary": "%s %s: %d quads" % (p.get("kind"), p.get("build", "lean"), _stats(ob)["quads"]),
            **_stats(ob), "scene": bridge.dump(p["scene"])}
