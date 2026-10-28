"""bpy mesh ops for Forge HQ: remesh, retopo, sculpt_hd, modifiers, lods.

Each op is `fn(ob, params) -> dict` on a bpy mesh object (scene must contain it).
Needs bpy; import lazily so the hq layer stays importable without it.
"""
import os
import bpy
import bmesh

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


def retopo(ob, p):
    """Quadriflow retopo to ~faces quads."""
    faces = int(p.get("faces", 4000))
    src = _snapshot(ob)
    with _ctx(ob):
        r = bpy.ops.object.quadriflow_remesh(
            target_faces=faces, use_mesh_symmetry=bool(p.get("symmetry", False)),
            use_preserve_sharp=True, use_preserve_boundary=True)
    if "FINISHED" not in r:
        bpy.data.objects.remove(src, do_unlink=True)
        raise RuntimeError("quadriflow failed: mesh must be closed and manifold; run remesh first")
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
}


def modifiers(ob, p):
    """Replace the hq_ modifier stack; evaluated mesh is what look/send use."""
    stack = p.get("stack") or []
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
        for a, v in {**defaults, **{k: x * p.get("unit", 1.0) if k in _LEN else x for k, x in s.items()}}.items():
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


FNS = {"remesh": remesh, "retopo": retopo, "sculpt_hd": sculpt_hd,
       "modifiers": modifiers, "lods": lods}


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
