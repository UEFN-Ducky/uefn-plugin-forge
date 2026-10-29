"""UV unwrap (seams by angle, smart or minimum stretch), scaled to a texel target and packed, plus a UV report.

The report rasterises the UVs: islands, overlap % (pixels two islands share), texel px/m, spread
(densest / sparsest island) and packed % (UV square covered). Forge keeps one UV set, so
lightmap=true makes that set lightmap-safe (no overlaps, 0-1, wide margin) instead of adding UV2.
"""
import math

import bpy
import numpy as np
from bpy_extras import mesh_utils

from . import bridge


def _tris(me):
    """(face index per tri, loop indices (T, 3)) by fanning every polygon."""
    nf = len(me.polygons)
    starts, sizes = np.empty(nf, np.int64), np.empty(nf, np.int64)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", sizes)
    n = np.maximum(sizes - 2, 0)
    face = np.repeat(np.arange(nf), n)
    first = np.repeat(starts, n)
    k = np.arange(len(face)) - np.repeat(np.cumsum(n) - n, n)
    return face, np.stack([first, first + k + 1, first + k + 2], 1)


def _uv(me):
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)
    return uv.reshape(-1, 2).astype(np.float64)


def _raster(t, isl, grid):
    """(owner island per pixel, overlap mask) for UV triangles t (T, 3, 2) at grid x grid."""
    owner = np.full((grid, grid), -1, np.int64)
    over = np.zeros((grid, grid), bool)
    pts = t * grid - 0.5  # pixel centres sit on integers
    for (a, b, c), i in zip(pts, isl):
        d = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if abs(d) < 1e-12:
            continue
        x0, x1 = max(0, math.ceil(min(a[0], b[0], c[0]))), min(grid - 1, math.floor(max(a[0], b[0], c[0])))
        y0, y1 = max(0, math.ceil(min(a[1], b[1], c[1]))), min(grid - 1, math.floor(max(a[1], b[1], c[1])))
        if x0 > x1 or y0 > y1:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        w1 = ((xs - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (ys - a[1])) / d
        w2 = ((b[0] - a[0]) * (ys - a[1]) - (xs - a[0]) * (b[1] - a[1])) / d
        inside = (w1 >= 0) & (w2 >= 0) & (w1 + w2 <= 1)
        if not inside.any():
            continue
        ys, xs = ys[inside], xs[inside]
        prev = owner[ys, xs]
        over[ys, xs] |= (prev >= 0) & (prev != i)
        owner[ys, xs] = i
    return owner, over


def stats(ob, res=2048, grid=512):
    """{islands, overlap_pct, texel_px_m, spread, packed_pct, oob} of ob's active UVs (world-space metres)."""
    me = ob.data
    if not me.uv_layers or not len(me.polygons):
        return {"islands": 0, "overlap_pct": 0.0, "texel_px_m": 0.0, "spread": 0.0, "packed_pct": 0.0, "oob": False}
    face, tri = _tris(me)
    uv = _uv(me)
    vidx = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("vertex_index", vidx)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    mw = np.array(ob.matrix_world)
    co = co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
    p = co[vidx[tri]]
    a3 = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    t = uv[tri]
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    a2 = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    islands = mesh_utils.mesh_linked_uv_islands(me)
    of_face = np.zeros(len(me.polygons), np.int64)
    for i, faces in enumerate(islands):
        of_face[faces] = i
    isl = of_face[face]
    A3, A2 = np.bincount(isl, a3, len(islands)), np.bincount(isl, a2, len(islands))
    big = A3 >= 0.002 * A3.sum()  # slivers say nothing about density
    dens = np.sqrt(A2[big] / A3[big]) * res
    owner, over = _raster(t, isl, grid)
    covered = int((owner >= 0).sum())
    return {"islands": len(islands), "overlap_pct": round(100.0 * over.sum() / max(1, covered), 2),
            "texel_px_m": round(float(math.sqrt(A2.sum() / max(A3.sum(), 1e-12)) * res), 1),
            "spread": round(float(dens.max() / max(dens.min(), 1e-9)), 2) if len(dens) else 0.0,
            "packed_pct": round(100.0 * covered / grid / grid, 1),
            "oob": bool(len(uv) and (uv.min() < -1e-3 or uv.max() > 1 + 1e-3))}


def _edit(ob):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.context.scene.tool_settings.use_uv_select_sync = True  # every selected face's UVs, no UV editor needed
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def _pack(margin, scale):
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, scale=scale, margin_method="FRACTION", margin=margin)


def _unwrap(method, angle):
    if method == "smart":
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=0.0, scale_to_bounds=False)
        return
    bpy.ops.mesh.mark_seam(clear=True)
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(angle))
    bpy.ops.mesh.mark_seam(clear=False)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.unwrap(method="MINIMUM_STRETCH" if method == "min_stretch" else "ANGLE_BASED", margin=0.0)


def op_uv(p):
    """Unwrap, even out island scale, scale to texel px/m, pack with margin_px; returns stats + scene."""
    objs, _ = bridge.load(p["scene"])
    ob = objs[p["id"]]
    me = ob.data
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    res, texel, method = int(p["res"]), float(p.get("texel") or 0), p["method"]
    margin = float(p["margin_px"]) / res
    if p.get("lightmap"):
        margin, texel = max(margin, 0.02), 0.0  # lightmaps are tiny: wide gutters, fill the square
    used, warnings = method, []
    _edit(ob)
    _unwrap(method, float(p["angle"]))
    bpy.ops.object.mode_set(mode="OBJECT")
    if method != "smart" and stats(ob, res, 256)["overlap_pct"] > 1.0:
        used = "smart"  # a closed smooth part has no sharp edge to cut; smart cuts by angle instead
        _edit(ob)
        _unwrap("smart", 66.0)
        bpy.ops.object.mode_set(mode="OBJECT")
    _edit(ob)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.average_islands_scale()
    bpy.ops.object.mode_set(mode="OBJECT")
    if texel > 0:
        now = stats(ob, res, 64)["texel_px_m"]
        if now > 0:
            uv = _uv(me) * (texel / now)
            me.uv_layers.active.data.foreach_set("uv", uv.astype(np.float32).ravel())
    _edit(ob)
    _pack(margin, texel <= 0)
    bpy.ops.object.mode_set(mode="OBJECT")
    if texel > 0 and stats(ob, res, 64)["oob"]:
        warnings.append("texel %d px/m does not fit %dpx: packed to fill; raise res or lower texel" % (texel, res))
        _edit(ob)
        _pack(margin, True)
        bpy.ops.object.mode_set(mode="OBJECT")
    out = stats(ob, res)
    out.update(method=used, margin_px=round(margin * res, 1), warnings=warnings, scene=bridge.dump(p["scene"]))
    return out


def op_uv_check(p):
    objs, _ = bridge.load(p["scene"])
    return stats(objs[p["id"]], int(p.get("res", 2048)))
