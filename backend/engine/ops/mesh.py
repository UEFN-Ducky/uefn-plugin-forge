"""bpy mesh ops for Forge HQ: remesh, retopo, sculpt_hd, modifiers, lods, boolean, hardsurface,
sculpt, origin.

Each op is `fn(ob, params) -> dict` on a bpy mesh object (scene must contain it).
Needs bpy; import lazily so the hq layer stays importable without it.
"""
import math
import os
import random

import bpy
import bmesh
from mathutils import Euler, Matrix, Vector, noise

from . import bridge

NANITE_MIN_TRIS = 1000


def _ctx(ob):
    return bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                                     selected_editable_objects=[ob])


def _stats(ob):
    me = ob.data
    quads = sum(1 for p in me.polygons if len(p.vertices) == 4)
    return {"faces": len(me.polygons), "tris": sum(len(p.vertices) - 2 for p in me.polygons),
            "quads": quads}


def _snapshot(ob):
    src = ob.copy()
    src.data = ob.data.copy()
    src.name = ob.name + "__src"
    src["mats"] = [m.name if m else "" for m in ob.data.materials]
    bpy.context.scene.collection.objects.link(src)
    return src


def _reproject(ob, src):
    """Copy UVs + vertex colors from src onto ob (nearest face, interpolated)."""
    sm, me = src.data, ob.data
    uv = len(sm.uv_layers) > 0
    col = len(sm.color_attributes) > 0
    if not (uv or col):
        return
    for l in sm.uv_layers:
        if l.name not in me.uv_layers:
            me.uv_layers.new(name=l.name)
    for a in sm.color_attributes:
        if a.name not in me.color_attributes:
            me.color_attributes.new(a.name, a.data_type, a.domain)
    m = ob.modifiers.new("hq_xfer", "DATA_TRANSFER")
    m.object = src
    m.use_loop_data = uv or col
    m.data_types_loops = ({"UV"} if uv else set()) | ({"COLOR_CORNER"} if col else set())
    m.loop_mapping = "POLYINTERP_NEAREST"
    if col:
        m.use_vert_data = True
        m.data_types_verts = {"COLOR_VERTEX"}
        m.vert_mapping = "POLYINTERP_NEAREST"
    with _ctx(ob):
        bpy.ops.object.modifier_apply(modifier=m.name)


def _finish(ob, src):
    if len(ob.data.materials) != len(src.data.materials):  # keep slots even if the op dropped them
        ob.data.materials.clear()
        for m in src.data.materials:
            ob.data.materials.append(m)
    _reproject(ob, src)
    bpy.data.objects.remove(src, do_unlink=True)


def remesh(ob, p):
    """Voxel remesh. Materials stay (same slots); UV/colors reprojected."""
    voxel = float(p.get("voxel", 0.05)) * p.get("unit", 1.0)
    src = _snapshot(ob)
    ob.data.remesh_voxel_size = voxel
    ob.data.remesh_voxel_adaptivity = float(p.get("adaptivity", 0.0))
    with _ctx(ob):
        bpy.ops.object.voxel_remesh()
    _finish(ob, src)
    return {"summary": f"voxel {voxel:g}m", **_stats(ob)}


def _collapse_tiny_edges(ob):
    """Quadriflow refuses any edge shorter than 1e-4 (Blender's quadriflow_check) and calls it
    "not manifold". Voxel remesh leaves a few where two surfaces pinch: collapse them."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.dissolve_degenerate(bm, dist=2e-4, edges=bm.edges[:])
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def _tidy(ob):
    """Quadriflow leaves a few stray artifacts (a lion: 6 open edges, 8 zero-area faces, a
    duplicate vertex): weld them, drop the slivers and close the tiny holes they leave."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=6e-4)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-5, edges=bm.edges[:])
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=8)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def retopo(ob, p):
    """Quadriflow retopo to ~faces quads."""
    faces = int(p.get("faces", 4000))
    src = _snapshot(ob)
    _collapse_tiny_edges(ob)
    try:
        with _ctx(ob):
            r = bpy.ops.object.quadriflow_remesh(
                target_faces=faces, use_mesh_symmetry=bool(p.get("symmetry", False)),
                use_preserve_sharp=True, use_preserve_boundary=True)
    except RuntimeError:  # Oct 28 2026: a raise (not CANCELLED) left the __src snapshot in the scene
        r = {"CANCELLED"}
    if "FINISHED" not in r:
        bpy.data.objects.remove(src, do_unlink=True)
        raise RuntimeError("quadriflow failed: mesh must be closed and manifold; run remesh first")
    _tidy(ob)
    _finish(ob, src)
    return {"summary": f"retopo {faces}", **_stats(ob)}  # ponytail: quadriflow needs a closed mesh, remesh first


# detail preset -> (texture type, texture kwargs, strength as fraction of object size)
_DETAIL = {
    "pores": ("CLOUDS", {"noise_scale": 0.02, "noise_depth": 2}, 0.004),
    "scales": ("VORONOI", {"noise_scale": 0.08, "noise_intensity": 1.0}, 0.015),
    "wood": ("MARBLE", {"noise_scale": 0.15, "noise_depth": 2}, 0.01),
    "cloth": ("DISTORTED_NOISE", {"noise_scale": 0.03, "noise_distortion": "BLENDER_ORIGINAL"}, 0.004),
    "rock": ("CLOUDS", {"noise_scale": 0.25, "noise_depth": 4}, 0.04),
}


def sculpt_hd(ob, p):
    """High-res detail in a sidecar .blend; the base mesh is untouched."""
    levels = int(p.get("levels", 3))
    detail = p.get("detail", "rock")
    if detail not in _DETAIL:
        raise ValueError(f"Unknown detail '{detail}'. Options: {', '.join(_DETAIL)}")
    base = _stats(ob)["faces"]
    hi = ob.copy()
    hi.data = ob.data.copy()
    hi.name = ob.name + "_hd"
    bpy.context.scene.collection.objects.link(hi)
    m = hi.modifiers.new("Multires", "MULTIRES")
    with _ctx(hi):
        for _ in range(levels):
            bpy.ops.object.multires_subdivide(modifier=m.name, mode="CATMULL_CLARK")
        m.levels = m.sculpt_levels = m.render_levels = levels
        bpy.ops.object.modifier_apply(modifier=m.name)
    size = max(hi.dimensions) or 1.0
    u = p.get("unit", 1.0)
    strokes = [{**s, "co": [c * u for c in s["co"]], "radius": s.get("radius", 10) * u,
                "strength": s.get("strength", 2) * u} for s in p.get("strokes") or [] if "co" in s]
    ttype, kw, k = _DETAIL[detail]
    tex = bpy.data.textures.new("hq_" + detail, ttype)
    for a, v in kw.items():
        setattr(tex, a, v)
    d = hi.modifiers.new("hq_detail", "DISPLACE")
    d.texture, d.texture_coords, d.strength, d.mid_level = tex, "OBJECT", size * k, 0.5
    with _ctx(hi):
        bpy.ops.object.modifier_apply(modifier=d.name)
    _strokes(hi, strokes)
    n = len(hi.data.polygons)
    path = p.get("path") or os.path.join(bpy.app.tempdir or os.getcwd(), hi.name + ".blend")
    bpy.data.libraries.write(path, {hi}, fake_user=True)
    bpy.data.objects.remove(hi, do_unlink=True)
    return {"summary": f"{detail} L{levels}: {n} faces in sidecar, {base} in base",
            "path": path, "sidecar_faces": n, "base_faces": base,
            "node_data": {"sidecar": path, "faces": n, "levels": levels, "detail": detail}}


def _strokes(hi, strokes):
    """strokes: [{co:[x,y,z], radius, strength}] radial bumps along normals."""
    if not strokes:
        return
    import numpy as np
    me = hi.data
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    nor = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("normal", nor)
    nor = nor.reshape(-1, 3)
    for s in strokes:
        c = np.array(s["co"], np.float32)
        r = float(s.get("radius", 0.1))
        w = np.clip(1 - np.linalg.norm(co - c, axis=1) / r, 0, 1) ** 2
        co += nor * (w * float(s.get("strength", 0.02)))[:, None]
    me.vertices.foreach_set("co", co.ravel())
    me.update()


_LEN = {"width", "thickness", "strength"}  # Forge cm in, metres in bpy
_MOD = {
    "bevel": ("BEVEL", {"segments": 3, "width": 0.02, "limit_method": "ANGLE"}),
    "subsurf": ("SUBSURF", {"levels": 1, "render_levels": 1}),
    "solidify": ("SOLIDIFY", {"thickness": 0.02}),
    "weighted_normal": ("WEIGHTED_NORMAL", {"keep_sharp": True}),
    "displace": ("DISPLACE", {"strength": 0.02}),
    "array": ("ARRAY", {"count": 2}),
    "mirror": ("MIRROR", {}),
    "triangulate": ("TRIANGULATE", {}),
}
STACKS = ("midpoly", "subd", "render")


def preset_stack(ob, name, unit=1.0):
    """Hard-surface stacks (Forge units): bevel 1% of the largest side, 30 deg, clamped, hardened + weighted normals."""
    if name not in STACKS:
        raise ValueError(f"Unknown stack '{name}'. Presets: {', '.join(STACKS)}")
    w = round(max(ob.dimensions) / unit * 0.01, 4) or 1.0
    bevel = {"type": "bevel", "width": w, "segments": 3, "angle_limit": math.radians(30),
             "use_clamp_overlap": True, "harden_normals": True}
    wn = {"type": "weighted_normal", "mode": "FACE_AREA", "keep_sharp": True}
    if name == "subd":  # tight support loops, then Catmull-Clark
        return [dict(bevel, width=w / 2, segments=2, harden_normals=False), {"type": "subsurf", "levels": 2, "render_levels": 2}]
    return [dict(bevel, segments=5 if name == "render" else 3), wn]


def modifiers(ob, p):
    """Replace the hq_ modifier stack; evaluated mesh is what look/send use."""
    stack = p.get("stack") or []
    if isinstance(stack, str):
        stack = preset_stack(ob, stack, p.get("unit", 1.0))
    for m in [m for m in ob.modifiers if m.name.startswith("hq_")]:
        ob.modifiers.remove(m)
    norm = []
    for i, s in enumerate(stack):
        s = {"type": s} if isinstance(s, str) else dict(s)
        t = s.pop("type")
        if t not in _MOD:
            raise ValueError(f"Unknown modifier '{t}'. Options: {', '.join(_MOD)}")
        bt, defaults = _MOD[t]
        m = ob.modifiers.new(f"hq_{i}_{t}", bt)
        given = dict(s)
        if "weight_angle" in given:  # bevel only the edges sharper than this many degrees
            given.update(limit_method="ANGLE", angle_limit=math.radians(float(given.pop("weight_angle"))))
        for a, v in {**defaults, **{k: x * p.get("unit", 1.0) if k in _LEN else x for k, x in given.items()}}.items():
            setattr(m, a, v)
        norm.append({"type": t, **s})
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    tris = sum(len(q.vertices) - 2 for q in me.polygons)
    faces = len(me.polygons)
    ev.to_mesh_clear()
    return {"summary": f"{len(norm)} modifiers -> {tris} tris", "stack": norm,
            "faces": faces, "tris": tris, "node_data": {"stack": norm}}


def _pin_seams(lod, m):
    """Decimate only verts off the UV seams (weight 0 = untouched)."""
    me = lod.data
    pinned = {i for e in me.edges if e.use_seam for i in e.vertices}
    if not pinned:
        return
    vg = lod.vertex_groups.new(name="hq_lod_free")
    vg.add([v.index for v in me.vertices if v.index not in pinned], 1.0, "REPLACE")
    m.vertex_group = vg.name


def lods(ob, p):
    """Seam-preserving decimate chain, objects <name>_LOD<i>; returns node payload."""
    ratios = p.get("ratios") or [1, 0.5, 0.25, 0.1]
    nanite = bool(p.get("nanite", False))
    out, counts = [], []
    for i, r in enumerate(ratios):
        lod = ob.copy()
        lod.data = ob.data.copy()
        lod.name = f"{ob.name}_LOD{i}"
        bpy.context.scene.collection.objects.link(lod)
        if r < 1:
            m = lod.modifiers.new("hq_lod", "DECIMATE")
            m.decimate_type, m.ratio = "COLLAPSE", float(r)
            _pin_seams(lod, m)
            with _ctx(lod):
                bpy.ops.object.modifier_apply(modifier=m.name)
        out.append(lod.name)
        counts.append(_stats(lod)["tris"])
        if not p.get("keep"):  # Export rebuilds LODs from node_data; keep=True leaves the objects
            bpy.data.objects.remove(lod, do_unlink=True)
    return {"summary": "/".join(map(str, counts)) + (" nanite" if nanite else ""),
            "lods": out, "tris": counts, "nanite": nanite, "ratios": ratios,
            "node_data": {"ratios": ratios, "tris": counts, "nanite": nanite}}


# ---- hard surface ---------------------------------------------------------------


def _tris(ob):
    return sum(len(q.vertices) - 2 for q in ob.data.polygons)


def manifold(ob):
    """Every edge has exactly two faces."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    ok = all(len(e.link_faces) == 2 for e in bm.edges)
    bm.free()
    return ok


def _shape(bm, shape, dims, matrix, seg=16):
    """Add a box | cylinder | sphere of dims (x, y, z) to bm, centred, then moved by matrix."""
    m = matrix @ Matrix.Diagonal((*dims, 1.0))
    if shape == "box":
        bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    elif shape == "cylinder":
        bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=0.5, radius2=0.5, depth=1.0, matrix=m)
    elif shape == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=max(4, seg // 2), radius=0.5, matrix=m)
    else:
        raise ValueError(f"Unknown cutter shape '{shape}'. Options: box, cylinder, sphere")


def _object_from(bm, name):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def cutter(spec, unit=1.0):
    """{shape, size: cm or [x,y,z], at: [x,y,z] cm, rot: [deg x,y,z], seg} -> a temporary bpy object."""
    size = spec.get("size", 20)
    dims = [float(s) * unit for s in (size if isinstance(size, (list, tuple)) else [size] * 3)]
    rot = [math.radians(float(r)) for r in spec.get("rot") or [0, 0, 0]]
    matrix = Matrix.Translation(Vector(spec.get("at") or [0, 0, 0]) * unit) @ Euler(rot).to_matrix().to_4x4()
    bm = bmesh.new()
    _shape(bm, spec.get("shape", "box"), dims, matrix, int(spec.get("seg", 32)))
    return _object_from(bm, "hq_cutter")


def clean(ob, dist=1e-7, weld=False):
    """Boolean cleanup: drop slivers, consistent normals. No weld by default: exact-boolean output is
    already closed, and welding its coincident vertices opened edges (panel then bolts, Oct 29 2026)."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    if weld:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=dist)
    bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


_SOLVER = {"exact": "EXACT", "manifold": "MANIFOLD", "fast": "FLOAT"}
_BOOL = {"difference": "DIFFERENCE", "union": "UNION", "intersect": "INTERSECT"}


def _apply_bool(ob, cut, mode, solver):
    m = ob.modifiers.new("hq_bool", "BOOLEAN")
    m.operation, m.solver, m.object = _BOOL[mode], _SOLVER[solver], cut
    if solver == "exact":
        m.use_self = True  # joined cutters may overlap each other
    with _ctx(ob):
        bpy.ops.object.modifier_apply(modifier=m.name)


def boolean(ob, p, objs=None):
    """Cut (or join) every cutter into ob: Forge ids from objs or {shape, size, at} specs made here."""
    unit, mode, solver = p.get("unit", 1.0), p.get("mode", "difference"), p.get("solver", "exact")
    if mode not in _BOOL or solver not in _SOLVER:
        raise ValueError(f"mode: {', '.join(_BOOL)}; solver: {', '.join(_SOLVER)}")
    made, cuts = [], []
    for c in p.get("cutters") or []:
        if isinstance(c, dict):
            made.append(cutter(c, unit))
            cuts.append(made[-1])
        elif objs and c in objs:
            cuts.append(objs[c])
        else:
            raise ValueError(f"No cutter {c!r}: give a Forge id or {{shape, size, at}}")
    if not cuts:
        raise ValueError("cutters is empty")
    try:
        for c in cuts:
            _apply_bool(ob, c, mode, solver)
    finally:
        for c in made:
            bpy.data.objects.remove(c, do_unlink=True)
    clean(ob)
    ok = manifold(ob)
    return {"summary": f"{mode} x{len(cuts)} ({solver}): {_tris(ob)} tris, {'manifold' if ok else 'OPEN edges'}",
            "tris": _tris(ob), "manifold": ok}


KITS = ("panel", "vent", "bolt", "grille")


def _surface(ob, at):
    """(world point, world normal) on ob nearest at (world metres); at None = top centre."""
    mw = ob.matrix_world
    if at is None:
        box = [mw @ Vector(c) for c in ob.bound_box]
        at = Vector((sum(v.x for v in box) / 8, sum(v.y for v in box) / 8, max(v.z for v in box) + 1.0))
    ok, loc, nor, _ = ob.closest_point_on_mesh(mw.inverted() @ at)
    if not ok:
        raise ValueError("object has no faces")
    return mw @ loc, (mw.to_3x3() @ nor).normalized()


def _kit_parts(preset, size, depth, count, radius):
    """[(shape, dims, offset)] in a frame whose Z is the surface normal and whose origin is on it."""
    h = 2 * depth  # half above, half below: cuts `depth` deep, bolts stand `depth` proud
    if preset == "panel":  # count x count panels separated by grooves
        g = max(depth, size * 0.015)
        lines = [-size / 2 + i * size / count for i in range(count + 1)]
        return ([("box", (size + g, g, h), (0, y, 0)) for y in lines] +
                [("box", (g, size + g, h), (x, 0, 0)) for x in lines])
    if preset == "vent":  # count slots
        step = size / count
        return [("box", (size, step * 0.5, h), (0, -size / 2 + step * (i + 0.5), 0)) for i in range(count)]
    if preset == "grille":  # count x count round holes
        step = size / count
        r = radius or step * 0.3
        return [("cylinder", (2 * r, 2 * r, h), (-size / 2 + step * (i + .5), -size / 2 + step * (j + .5), 0))
                for i in range(count) for j in range(count)]
    r = radius or size * 0.05  # bolts on a ring of `size`
    return [("cylinder", (2 * r, 2 * r, h), (size / 2 * math.cos(a), size / 2 * math.sin(a), 0))
            for a in (2 * math.pi * (i + .5) / count for i in range(count))]


def hardsurface(ob, p):
    """Detail kit cut into (or bolted onto) the surface nearest `at`, aligned to its normal."""
    unit, preset = p.get("unit", 1.0), p.get("preset", "panel")
    if preset not in KITS:
        raise ValueError(f"Unknown preset '{preset}'. Options: {', '.join(KITS)}")
    span = max(ob.dimensions) or 1.0
    size = float(p["size"]) * unit if p.get("size") else span * 0.3
    depth = float(p["depth"]) * unit if p.get("depth") else span * 0.01
    count = int(p.get("count") or {"panel": 2, "vent": 5, "grille": 4, "bolt": 4}[preset])
    radius = float(p["radius"]) * unit if p.get("radius") else 0.0
    loc, nor = _surface(ob, Vector(p["at"]) * unit if p.get("at") else None)
    frame = Matrix.Translation(loc) @ nor.to_track_quat("Z", "Y").to_matrix().to_4x4()
    seg = 8 if preset == "bolt" else 12  # segment table: bolts 6-8, small holes 12
    bm = bmesh.new()
    for shape, dims, off in _kit_parts(preset, size, depth, count, radius):
        _shape(bm, shape, dims, frame @ Matrix.Translation(off), seg)
    cut = _object_from(bm, "hq_kit")
    try:
        _apply_bool(ob, cut, "union" if preset == "bolt" else "difference", "exact")
    finally:
        bpy.data.objects.remove(cut, do_unlink=True)
    clean(ob)
    n = count * count if preset in ("panel", "grille") else count
    return {"summary": f"{preset} x{n}, depth {depth / unit:g}: {_tris(ob)} tris" + ("" if manifold(ob) else ", OPEN edges"),
            "tris": _tris(ob), "count": n, "manifold": manifold(ob)}


# ---- procedural sculpt ----------------------------------------------------------

_SYM = {"+x": "X", "-x": "-X", "+y": "Y", "-y": "-Y", "+z": "Z", "-z": "-Z"}
SCULPT_STEPS = ("inflate", "smooth", "symmetrize", "detail", "multires", "remesh")
MAX_FACES = 400000


def _steps(ops):
    """["inflate:2", {"detail": {...}}] -> [(name, value)]."""
    out = []
    for s in ops or []:
        items = s.items() if isinstance(s, dict) else [str(s).partition(":")[::2]]
        for k, v in items:
            if k not in SCULPT_STEPS:
                raise ValueError(f"Unknown sculpt step '{k}'. Steps: {', '.join(SCULPT_STEPS)}")
            out.append((k, v))
    return out


def _bm_step(ob, fn):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.normal_update()
    fn(bm)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def _detail(bm, v, unit):
    v = v if isinstance(v, dict) else {"strength": float(v or 1)}
    k = float(v.get("strength", 1)) * unit
    scale = float(v.get("scale", 20)) * unit or 1.0
    off = Vector([random.Random(int(v.get("seed", 0))).uniform(-100, 100) for _ in range(3)])
    moves = [(vert, vert.normal * k * noise.fractal(vert.co / scale + off, 1.0, 2.0, 4)) for vert in bm.verts]
    for vert, d in moves:
        vert.co += d


def sculpt(ob, p):
    """Headless sculpt chain: inflate:cm, smooth:n, symmetrize:+x, detail:{strength,scale,seed} (cm), multires:n, remesh:cm."""
    unit, steps = p.get("unit", 1.0), _steps(p.get("ops"))
    if not steps:
        raise ValueError("ops is empty, e.g. [\"remesh:2\", \"inflate:1\", \"smooth:3\", \"symmetrize:+x\"]")
    for k, v in steps:
        if k == "inflate":
            _bm_step(ob, lambda bm, d=float(v) * unit: [setattr(x, "co", x.co + x.normal * d) for x in bm.verts])
        elif k == "smooth":
            _bm_step(ob, lambda bm, n=int(v or 1): [bmesh.ops.smooth_vert(
                bm, verts=bm.verts[:], factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True) for _ in range(n)])
        elif k == "symmetrize":
            if str(v) not in _SYM:
                raise ValueError(f"symmetrize takes {', '.join(_SYM)}")
            _bm_step(ob, lambda bm: bmesh.ops.symmetrize(
                bm, input=bm.verts[:] + bm.edges[:] + bm.faces[:], direction=_SYM[str(v)], dist=1e-4 * max(ob.dimensions)))
        elif k == "detail":
            _bm_step(ob, lambda bm: _detail(bm, v, unit))
        elif k == "multires":
            n = int(v or 1)
            if len(ob.data.polygons) * 4 ** n > MAX_FACES:
                raise ValueError(f"multires:{n} would pass {MAX_FACES} faces; use fewer levels or sculpt_hd")
            m = ob.modifiers.new("hq_sub", "SUBSURF")
            m.levels = m.render_levels = n
            with _ctx(ob):
                bpy.ops.object.modifier_apply(modifier=m.name)
        else:
            remesh(ob, {"voxel": float(v), "unit": unit})
    return {"summary": f"sculpt {len(steps)} steps: {_tris(ob)} tris", "tris": _tris(ob), "steps": len(steps)}


ORIGINS = ("bottom", "center", "corner")


def origin(ob, p):
    """Pivot to the bottom centre | centre | min corner; ground=True drops the lowest point to z=0.
    Returns move: the world shift (Forge units) the host adds to the node position."""
    at, unit = p.get("at", "bottom"), p.get("unit", 1.0)
    if at not in ORIGINS:
        raise ValueError(f"at must be one of: {', '.join(ORIGINS)}")
    me = ob.data
    if not len(me.vertices):
        raise ValueError("object has no vertices")
    lo = Vector([min(v.co[i] for v in me.vertices) for i in range(3)])
    hi = Vector([max(v.co[i] for v in me.vertices) for i in range(3)])
    mid = (lo + hi) / 2
    pivot = {"bottom": Vector((mid.x, mid.y, lo.z)), "center": mid, "corner": lo}[at]
    me.transform(Matrix.Translation(-pivot))
    me.update()
    move = ob.matrix_world.to_3x3() @ pivot
    ob.matrix_world = Matrix.Translation(move) @ ob.matrix_world
    if p.get("ground"):
        low = min((ob.matrix_world @ v.co).z for v in me.vertices)
        move.z -= low
        ob.matrix_world = Matrix.Translation((0, 0, -low)) @ ob.matrix_world
    move = [c / unit for c in move]
    return {"summary": f"origin {at}" + (" grounded" if p.get("ground") else "") + ", moved %.1f %.1f %.1f" % tuple(move),
            "move": move}


FNS = {"remesh": remesh, "retopo": retopo, "sculpt_hd": sculpt_hd,
       "modifiers": modifiers, "lods": lods, "hardsurface": hardsurface, "sculpt": sculpt, "origin": origin}


def _each(name, p, write):
    """Run FNS[name] on the loaded objects; write=True sends the changed meshes back."""
    objs, _ = bridge.load(p["scene"])
    q = {**p, "unit": float(p["scene"].get("unit", 0.01))}
    results = [FNS[name](ob, q) for ob in objs.values() if ob.type == "MESH"]
    if not results:
        raise ValueError("No mesh to run %s on" % name)
    out = dict(results[0])
    if len(results) > 1:
        out["summary"] += " (+%d more objects)" % (len(results) - 1)
    if write:
        out["scene"] = bridge.dump(p["scene"])
    return out


def op_remesh(p):
    return _each("remesh", p, True)


def op_retopo(p):
    return _each("retopo", p, True)


def op_sculpt_hd(p):
    return _each("sculpt_hd", p, False)


def op_modifiers(p):
    return _each("modifiers", p, False)


def op_lods(p):
    return _each("lods", p, False)


def op_hardsurface(p):
    return _each("hardsurface", p, True)


def op_sculpt(p):
    return _each("sculpt", p, True)


def op_origin(p):
    return _each("origin", p, True)


def op_boolean(p):
    """The target is p['id']; every other loaded object can be a cutter."""
    objs, _ = bridge.load(p["scene"])
    out = boolean(objs[p["id"]], {**p, "unit": float(p["scene"].get("unit", 0.01))}, objs)
    out["scene"] = bridge.dump(p["scene"])
    return out
