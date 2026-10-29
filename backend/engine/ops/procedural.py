"""bpy procedural ops for Forge HQ: scatter, gn, sdf_blend, drop, procedural (buildings, kits, stairs, fences, pipes).

Lengths come in as Forge units (cm); p["unit"] turns them into metres. New objects carry no
forge_id, so the bridge returns them as new Forge objects.
"""
import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import bridge
from .mesh import _ctx, _object_from, _shape, _tris, clean, cutter, manifold, remesh

MAX_ITEMS = 2000


def _world_tris(ob):
    me = ob.data
    me.calc_loop_triangles()
    mw, rot = ob.matrix_world, ob.matrix_world.to_3x3()
    out = []
    for t in me.loop_triangles:
        a, b, c = (mw @ me.vertices[i].co for i in t.vertices)
        area = (b - a).cross(c - a).length / 2
        if area > 0:
            out.append((a, b, c, (rot @ t.normal).normalized(), area))
    return out


def _on_surface(on, count, density, rng, max_slope):
    tris = _world_tris(on)
    if not tris:
        raise ValueError("scatter target has no faces")
    if max_slope < 90:
        up = math.cos(math.radians(max_slope))
        tris = [t for t in tris if t[3].z >= up]
        if not tris:
            raise ValueError("no faces within max_slope")
    total = sum(t[4] for t in tris)
    n = min(MAX_ITEMS, int(count or round(total * density)))
    out = []
    for a, b, c, nor, _ in rng.choices(tris, weights=[t[4] for t in tris], k=n):
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        out.append((a + (b - a) * u + (c - a) * v, nor))
    return out


def _along(path, spacing):
    """Points every `spacing` along a polyline; the 'normal' is up, the heading rides in the tangent."""
    pts = [Vector(q) for q in path]
    if len(pts) < 2 or spacing <= 0:
        raise ValueError("path needs 2+ points and spacing > 0")
    out, carry = [], 0.0
    for a, b in zip(pts, pts[1:]):
        seg = (b - a).length
        d = carry
        while d <= seg + 1e-9:
            out.append((a + (b - a) * (d / seg if seg else 0), Vector((0, 0, 1)), (b - a).normalized()))
            d += spacing
            if len(out) >= MAX_ITEMS:
                return out
        carry = d - seg
    return out


def scatter(objs, p):
    """Copies of the items on a surface (or along a path), merged into one new object."""
    unit, rng = p.get("unit", 1.0), random.Random(int(p.get("seed", 1)))
    items = [objs[i] for i in p["items"]]
    lo, hi = (list(p.get("scale") or [0.8, 1.2]) * 2)[:2]
    if p.get("path"):
        spots = _along([[c * unit for c in q] for q in p["path"]], float(p.get("spacing", 50)) * unit)
    else:
        on = objs[p["on"]]
        spots = [(loc, nor, None) for loc, nor in _on_surface(
            on, p.get("count"), float(p.get("density", 20)), rng, float(p.get("max_slope", 90)))]
    bm, mats, slot = bmesh.new(), [], {}
    for it in items:
        slot[it.name] = len(mats)
        mats.extend(it.data.materials)
    for loc, nor, heading in spots:
        it = rng.choice(items)
        if heading is not None:
            rot = Matrix.Rotation(math.atan2(heading.y, heading.x), 4, "Z")
        else:
            rot = (nor.to_track_quat("Z", "Y").to_matrix().to_4x4() if p.get("align", "normal") == "normal" else Matrix())
            rot = rot @ Matrix.Rotation(rng.uniform(0, 2 * math.pi), 4, "Z")
        tmp = it.data.copy()
        tmp.transform(Matrix.Translation(loc) @ rot @ Matrix.Scale(rng.uniform(lo, hi), 4) @ it.matrix_world.to_3x3().to_4x4())
        start = len(bm.faces)
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
        bm.faces.ensure_lookup_table()
        for f in bm.faces[start:]:
            f.material_index += slot[it.name]
    ob = _object_from(bm, p.get("name") or "Scatter")
    for m in mats:
        ob.data.materials.append(m)
    return {"summary": f"scatter {len(spots)} copies: {_tris(ob)} tris", "count": len(spots), "tris": _tris(ob)}


# A GN spec: nodes [{id, type, in: {socket: value}, props: {attr: value}}], links ["a.Socket", "b.Socket"].
# "in" / "out" are the group's Geometry input and output. Socket values are Blender units (metres).
GN_PRESETS = {
    "pebbles": {
        "nodes": [
            {"id": "pts", "type": "GeometryNodeDistributePointsOnFaces", "in": {"Density": 10.0}},  # per m2: 20 tris each
            {"id": "rock", "type": "GeometryNodeMeshIcoSphere", "in": {"Radius": 0.03, "Subdivisions": 1}},
            {"id": "inst", "type": "GeometryNodeInstanceOnPoints"},
            {"id": "real", "type": "GeometryNodeRealizeInstances"},
            {"id": "join", "type": "GeometryNodeJoinGeometry"},
        ],
        "links": [["in.Geometry", "pts.Mesh"], ["pts.Points", "inst.Points"], ["rock.Mesh", "inst.Instance"],
                  ["inst.Instances", "real.Geometry"], ["in.Geometry", "join.Geometry"],
                  ["real.Geometry", "join.Geometry"], ["join.Geometry", "out.Geometry"]],
    },
    "wire": {
        "nodes": [
            {"id": "curve", "type": "GeometryNodeMeshToCurve"},
            {"id": "ring", "type": "GeometryNodeCurvePrimitiveCircle", "in": {"Radius": 0.01, "Resolution": 6}},
            {"id": "tube", "type": "GeometryNodeCurveToMesh", "in": {"Fill Caps": True}},
        ],
        "links": [["in.Geometry", "curve.Mesh"], ["curve.Curve", "tube.Curve"], ["ring.Curve", "tube.Profile Curve"],
                  ["tube.Mesh", "out.Geometry"]],
    },
}


def _sock(node, name, outputs):
    socks = node.outputs if outputs else node.inputs
    for s in socks:
        if s.enabled and (s.name == name or s.identifier == name):
            return s
    raise ValueError("%s has no %s socket %r. Sockets: %s" % (
        node.bl_idname, "output" if outputs else "input", name, ", ".join(s.name for s in socks if s.enabled)))


def build_tree(spec, inputs=None):
    tree = bpy.data.node_groups.new("hq_gn", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes = {"in": tree.nodes.new("NodeGroupInput"), "out": tree.nodes.new("NodeGroupOutput")}
    for n in spec.get("nodes") or []:
        try:
            node = tree.nodes.new(n["type"])
        except RuntimeError:
            raise ValueError("Unknown node type %r (use bl_idname, e.g. GeometryNodeMeshGrid)" % n.get("type")) from None
        for k, v in (n.get("props") or {}).items():
            setattr(node, k, v)
        nodes[n["id"]] = node
    for key, value in {**{"%s.%s" % (n["id"], k): v for n in spec.get("nodes") or [] for k, v in (n.get("in") or {}).items()},
                       **(inputs or {})}.items():
        nid, _, name = key.partition(".")
        if nid not in nodes:
            raise ValueError("inputs key %r: no node %r" % (key, nid))
        _sock(nodes[nid], name, False).default_value = value
    for a, b in spec.get("links") or []:
        (na, _, sa), (nb, _, sb) = a.partition("."), b.partition(".")
        if na not in nodes or nb not in nodes:
            raise ValueError("link %s -> %s names a missing node" % (a, b))
        tree.links.new(_sock(nodes[na], sa, True), _sock(nodes[nb], sb, False))
    return tree


def gn(objs, p):
    """Run a geometry-nodes graph on id (or on an empty new object) and apply it."""
    spec = p.get("spec") or GN_PRESETS.get(p.get("preset") or "")
    if not spec:
        raise ValueError("give spec={nodes, links} or preset: %s" % ", ".join(GN_PRESETS))
    ob = objs.get(p.get("id") or "")
    if ob is None:
        me = bpy.data.meshes.new(p.get("name") or "GN")
        ob = bpy.data.objects.new(me.name, me)
        bpy.context.scene.collection.objects.link(ob)
    tree = build_tree(spec, p.get("inputs"))
    try:
        m = ob.modifiers.new("hq_gn", "NODES")
        m.node_group = tree
        with _ctx(ob):
            bpy.ops.object.modifier_apply(modifier=m.name)
    finally:
        bpy.data.node_groups.remove(tree)
    me = ob.data
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    if hasattr(me, "set_sharp_from_angle"):
        me.set_sharp_from_angle(angle=math.radians(30))  # smooth by angle
    me.update()
    return {"summary": f"gn {p.get('preset') or 'spec'}: {_tris(ob)} tris", "tris": _tris(ob)}


def sdf_blend(objs, p):
    """Union the objects into one watertight mesh and round only the seams where they meet."""
    unit = p.get("unit", 1.0)
    src = [objs[i] for i in p["ids"]]
    bm = bmesh.new()
    for ob in src:
        tmp = ob.data.copy()
        tmp.transform(ob.matrix_world)
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
    out = _object_from(bm, p.get("name") or src[0].name + "_blend")
    for m in src[0].data.materials:
        out.data.materials.append(m)
    voxel, fillet = float(p.get("voxel", 1)) * unit, float(p.get("fillet", 2)) * unit
    remesh(out, {"voxel": voxel, "unit": 1.0})

    def gap(ob, co):
        ok, loc, _, _ = ob.closest_point_on_mesh(ob.matrix_world.inverted() @ co)
        return (ob.matrix_world @ loc - co).length if ok else 1e9

    bm = bmesh.new()
    bm.from_mesh(out.data)
    seam = [v for v in bm.verts if sorted(gap(ob, v.co) for ob in src)[1] < fillet]
    for _ in range(max(2, round(fillet / voxel) * 2)):
        bmesh.ops.smooth_vert(bm, verts=seam, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)
    bm.to_mesh(out.data)
    bm.free()
    out.data.update()
    return {"summary": f"blend {len(src)} into {out.name}: {_tris(out)} tris, {len(seam)} seam verts",
            "tris": _tris(out), "seam": len(seam), "manifold": manifold(out)}


def drop(objs, p):
    """Rigid-body settle: ids fall (convex hull) onto `onto` (mesh). Returns world matrices in Forge units."""
    unit, scene = p.get("unit", 1.0), bpy.context.scene
    movers = [objs[i] for i in p["ids"]]
    with bpy.context.temp_override(scene=scene):
        if scene.rigidbody_world is None:
            bpy.ops.rigidbody.world_add()
    world = scene.rigidbody_world
    if world.collection is None:
        world.collection = bpy.data.collections.new("RigidBodyWorld")
    world.substeps_per_frame, world.solver_iterations = 10, 10
    for ob, kind in [(o, "ACTIVE") for o in movers] + [(objs[i], "PASSIVE") for i in p.get("onto") or []]:
        if ob.name not in world.collection.objects:
            world.collection.objects.link(ob)
        ob.rigid_body.type = kind
        ob.rigid_body.collision_shape = "CONVEX_HULL" if kind == "ACTIVE" else "MESH"
        ob.rigid_body.collision_margin = 0.001
    frames = int(p.get("frames", 60))
    world.point_cache.frame_start, world.point_cache.frame_end = 1, frames
    for f in range(1, frames + 1):
        scene.frame_set(f)
    dg = bpy.context.evaluated_depsgraph_get()
    out = {}
    for fid, ob in objs.items():
        if ob in movers:
            m = ob.evaluated_get(dg).matrix_world.copy()
            m.translation = m.translation / unit
            out[fid] = [list(r) for r in m]
    return {"summary": f"dropped {len(movers)} over {frames} frames", "matrices": out}


# ---- generators -------------------------------------------------------------------

KINDS = ("building", "wall", "floor", "stairs", "fence", "pipe", "cable")


def _boxes(specs, unit):
    """[(dims cm, centre cm)] -> one bmesh of boxes."""
    bm = bmesh.new()
    for dims, at in specs:
        _shape(bm, "box", [d * unit for d in dims], Matrix.Translation(Vector(at) * unit))
    return bm


def _cut(ob, specs, unit, mode="DIFFERENCE"):
    cut = _object_from(_boxes(specs, unit), "hq_cut")
    m = ob.modifiers.new("hq_bool", "BOOLEAN")
    m.operation, m.solver, m.object, m.use_self = mode, "EXACT", cut, True
    with _ctx(ob):
        bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cut, do_unlink=True)


def _building(p, u):
    w, d, fh, floors = float(p.get("width", 1000)), float(p.get("depth", 800)), float(p.get("floor_height", 300)), int(p.get("floors", 4))
    h = fh * floors
    ob = _object_from(_boxes([((w, d, h), (0, 0, h / 2))], u), "SM_Building")
    ledges = [((w + 20, d + 20, 15), (0, 0, fh * f)) for f in range(1, floors)]
    if ledges:
        _cut(ob, ledges, u, "UNION")
    ww, wh, rec, bay = 120.0, 150.0, 15.0, float(p.get("bay", 300))
    wins = []
    for f in range(floors):
        z = fh * f + fh * 0.55
        for length, along_x, half in ((w, True, d / 2), (d, False, w / 2)):
            cols = max(1, int(length // bay))
            for c in range(cols):
                s = -length / 2 + length / cols * (c + 0.5)
                for side in (-1, 1):
                    if along_x:
                        wins.append(((ww, 2 * rec, wh), (s, side * half, z)))
                    else:
                        wins.append(((2 * rec, ww, wh), (side * half, s, z)))
    wins.append(((w - 60, d - 60, 80), (0, 0, h)))  # roof well behind a parapet
    _cut(ob, wins, u)
    return ob, f"{floors}F {len(wins) - 1} windows"


def _wall(p, u):
    w, h, t = float(p.get("width", 512)), float(p.get("height", 384)), float(p.get("thickness", 20))  # UEFN grid
    ob = _object_from(_boxes([((w, t, h), (w / 2, t / 2, h / 2))], u), "SM_Wall")
    opening = p.get("opening", "none")
    if opening == "door":
        _cut(ob, [((100, t * 3, 220), (w / 2, t / 2, 110))], u)
    elif opening == "window":
        _cut(ob, [((120, t * 3, 120), (w / 2, t / 2, 150))], u)
    elif opening != "none":
        raise ValueError("opening must be none, door or window")
    return ob, f"wall {w:g}x{h:g} {opening}"


def _floor(p, u):
    w, d, t = float(p.get("width", 512)), float(p.get("depth", 512)), float(p.get("thickness", 20))
    return _object_from(_boxes([((w, d, t), (w / 2, d / 2, -t / 2))], u), "SM_Floor"), f"floor {w:g}x{d:g}"


def _stairs(p, u):
    n, width, rise, run = int(p.get("steps", 12)), float(p.get("width", 100)), float(p.get("rise", 17.5)), float(p.get("run", 28))
    prof = [(0, 0), (n * run, 0)]
    for i in reversed(range(n)):
        prof += [((i + 1) * run, (i + 1) * rise), (i * run, (i + 1) * rise)]
    bm = bmesh.new()
    verts = [bm.verts.new((x * u, 0, z * u)) for x, z in prof]
    face = bm.faces.new(verts)
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    bmesh.ops.translate(bm, vec=(0, width * u, 0), verts=[e for e in ext["geom"] if isinstance(e, bmesh.types.BMVert)])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return _object_from(bm, "SM_Stairs"), f"stairs {n} steps"


def _fence(p, u):
    length, h, gap = float(p.get("length", 400)), float(p.get("height", 100)), float(p.get("spacing", 100))
    posts = max(2, int(round(length / gap)) + 1)
    specs = [((10, 10, h), (length * i / (posts - 1), 0, h / 2)) for i in range(posts)]
    specs += [((length, 4, 8), (length / 2, 0, z)) for z in (h * 0.35, h * 0.8)]
    return _object_from(_boxes(specs, u), "SM_Fence"), f"fence {posts} posts"


def _tube(p, u, name, sag=0.0):
    pts = [Vector(q) * u for q in p.get("path") or [[0, 0, 0], [400, 0, 0]]]
    if len(pts) < 2:
        raise ValueError("path needs 2+ points [x, y, z] cm")
    if sag:  # a hanging cable: a parabola between each pair of points
        pts = [a.lerp(b, t / 8) - Vector((0, 0, sag * u * 4 * (t / 8) * (1 - t / 8)))
               for a, b in zip(pts, pts[1:]) for t in range(8)] + [pts[-1]]
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for pt, co in zip(sp.points, pts):
        pt.co = (*co, 1.0)
    cu.bevel_depth, cu.bevel_resolution, cu.use_fill_caps = float(p.get("radius", 5)) * u, 2, True
    tmp = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(tmp)
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.curves.remove(cu)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    clean(ob, 1e-6)
    return ob, f"{name[3:].lower()} {len(pts)} points"


def procedural(p):
    kind, u = p.get("kind", "building"), p.get("unit", 1.0)
    makers = {"building": _building, "wall": _wall, "floor": _floor, "stairs": _stairs, "fence": _fence,
              "pipe": lambda q, v: _tube(q, v, "SM_Pipe"),
              "cable": lambda q, v: _tube(q, v, "SM_Cable", float(q.get("sag", 40)))}
    if kind not in makers:
        raise ValueError("kind must be one of: %s" % ", ".join(KINDS))
    ob, what = makers[kind](p, u)
    clean(ob, 1e-6)
    if p.get("name"):
        ob.name = ob.data.name = p["name"]
    ob.location = Vector(p.get("at") or [0, 0, 0]) * u
    return {"summary": f"{what}: {_tris(ob)} tris" + ("" if manifold(ob) else ", open"), "tris": _tris(ob), "name": ob.name}


def _run(fn, p, write=True):
    objs, _ = bridge.load(p["scene"])
    out = fn(objs, {**p, "unit": float(p["scene"].get("unit", 0.01))})
    if write:
        out["scene"] = bridge.dump(p["scene"])
    return out


def op_scatter(p):
    return _run(scatter, p)


def op_gn(p):
    return _run(gn, p)


def op_sdf_blend(p):
    return _run(sdf_blend, p)


def op_drop(p):
    return _run(drop, p, write=False)


def op_procedural(p):
    return _run(lambda objs, q: procedural(q), p)
