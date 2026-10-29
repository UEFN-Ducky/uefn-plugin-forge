"""Hand-painted textures: directional brush strokes over baked AO (paint_strokes) and camera-projected
reference art (project_ref). Everything is painted in texture space with numpy; Cycles only bakes AO.

Texture rows run bottom-up (row = v * size), the same order bpy image pixels use.
"""
import json
import math
from pathlib import Path

import numpy as np

_MAX_TRIS = 600_000
_MAX_STROKES = 400_000
_CELL = 12  # triangles whose UV box fits this many pixels are rasterised in one batch
_EPS = 0.03
_FLOWS = ("down", "normal", "radial", "tufts")
_BLENDS = {"soft": 2.0, "hard": 8.0}
_VIEWS = ("front", "back", "side")


def _rgb(value, default):
    if value is None:
        return np.array(default, np.float32)
    if isinstance(value, str):
        h = value.lstrip("#")
        if len(h) != 6:
            raise ValueError("Color %r: pass [r,g,b] (0-1 or 0-255) or '#rrggbb'" % value)
        return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)
    c = [float(x) for x in value][:3]
    if len(c) != 3:
        raise ValueError("Color %r: pass [r,g,b] (0-1 or 0-255) or '#rrggbb'" % (value,))
    return np.array([x / 255.0 for x in c] if max(c) > 1.0 else c, np.float32)


# ---- UV rasteriser ----------------------------------------------------------


def _cells(idx, bary, uvp, lo, t, kx, ky, size):
    """Write triangles t over a kx*ky pixel window each, starting at lo[t]."""
    X = lo[t, 0][:, None, None] + np.arange(kx)[None, None, :]
    Y = lo[t, 1][:, None, None] + np.arange(ky)[None, :, None]
    a, b, c = uvp[t, 0], uvp[t, 1], uvp[t, 2]
    v0, v1 = b - a, c - a
    det = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
    ok = np.abs(det) > 1e-9
    d = np.where(ok, det, 1.0)[:, None, None]
    dx = X + 0.5 - a[:, 0][:, None, None]
    dy = Y + 0.5 - a[:, 1][:, None, None]
    w1 = (dx * v1[:, 1][:, None, None] - dy * v1[:, 0][:, None, None]) / d
    w2 = (v0[:, 0][:, None, None] * dy - v0[:, 1][:, None, None] * dx) / d
    w0 = 1.0 - w1 - w2
    inside = (w0 >= -_EPS) & (w1 >= -_EPS) & (w2 >= -_EPS) & ok[:, None, None] & (X < size) & (Y < size)
    ti, yi, xi = np.nonzero(inside)
    yy, xx = Y[ti, yi, 0], X[ti, 0, xi]
    idx[yy, xx] = t[ti]
    bary[yy, xx] = np.stack([w0, w1, w2], -1)[ti, yi, xi]


def _raster(uvp, size):
    """(tri id map (size,size) -1 outside, barycentric map (size,size,3)) for UV triangles in pixel units."""
    idx = np.full((size, size), -1, np.int32)
    bary = np.zeros((size, size, 3), np.float32)
    lo = np.clip(np.floor(uvp.min(1)).astype(np.int64), 0, size - 1)
    hi = np.clip(np.ceil(uvp.max(1)).astype(np.int64), 1, size)
    dim = np.maximum(hi - lo, 1)
    small = dim.max(1) <= _CELL
    ids = np.nonzero(small)[0]
    for s in range(0, len(ids), 20000):
        _cells(idx, bary, uvp, lo, ids[s:s + 20000], _CELL, _CELL, size)
    big = np.nonzero(~small)[0]
    if len(big):  # batch by power-of-two bbox class so thousands of mid-size triangles cost a few calls, not one each
        cls = np.ceil(np.log2(dim[big])).astype(np.int64)
        key = cls[:, 0] * 64 + cls[:, 1]
        for k in np.unique(key):
            grp = big[key == k]
            kx, ky = int(dim[grp, 0].max()), int(dim[grp, 1].max())
            step = max(1, 2_000_000 // (kx * ky))
            for s in range(0, len(grp), step):
                _cells(idx, bary, uvp, lo, grp[s:s + step], kx, ky, size)
    bary = np.clip(bary, 0.0, None)
    bary /= np.maximum(bary.sum(-1, keepdims=True), 1e-9)
    return idx, bary


def _gather(vals, tid, bw):
    """Barycentric interpolation of per-triangle-corner values (T,3,k) at pixels (tid, bw), in chunks."""
    out = np.empty((len(tid), vals.shape[2]), np.float32)
    for s in range(0, len(tid), 500_000):
        out[s:s + 500_000] = np.einsum("nk,nkj->nj", bw[s:s + 500_000], vals[tid[s:s + 500_000]])
    return out


def _blur(img, r):
    for ax in (0, 1):
        acc = np.zeros_like(img)
        for s in range(-r, r + 1):
            acc += np.roll(img, s, ax)
        img = acc / (2 * r + 1)
    return img


def _bleed(img, occ, iters=6):
    """Push painted colour a few pixels past the UV islands so mips and seams never show black."""
    img, occ = img.copy(), occ.copy()
    for _ in range(iters):
        acc = np.zeros_like(img)
        cnt = np.zeros(occ.shape, np.float32)
        for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
            m = np.roll(occ, sh, ax)
            acc += np.roll(img, sh, ax) * m[..., None]
            cnt += m
        new = ~occ & (cnt > 0)
        img[new] = acc[new] / cnt[new][:, None]
        occ |= new
    return img


# ---- the surface being painted ---------------------------------------------


class _Tex:
    pass


def _unwrap(ob):
    import bpy
    from .bake import _select
    _select(ob, active=ob)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def _prepare(p):
    """Load the object, unwrap if needed, and rasterise the target material's triangles into texture space."""
    import bpy
    from . import bridge
    from .bake import _ensure_uv
    objs, _ = bridge.load(p["scene"])
    ob = objs[p["id"]]
    me = ob.data
    mats = [m for m in me.materials if m is not None]
    want = p.get("material")
    whole = bool(p.get("all"))  # paint_bake: every slot goes into one texture
    pick = next((i for i, m in enumerate(me.materials)
                 if m is not None and (whole or not want or json.loads(m.get("forge") or "{}").get("id") == want)), None)
    if pick is None:
        raise ValueError("%s has no material; run material first" % ob.name)
    size = int(p["size"])
    S = _Tex()
    S.ob, S.mats, S.mat, S.size = ob, mats, me.materials[pick], size
    S.unwrapped = _ensure_uv(ob)
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    if nt > _MAX_TRIS:
        raise ValueError("%s has %d triangles (limit %d); run cleanup or lods first, then paint the lighter mesh" % (ob.name, nt, _MAX_TRIS))
    tv = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    tl = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("loops", tl)
    tp = np.empty(nt, np.int32)
    me.loop_triangles.foreach_get("polygon_index", tp)
    pm = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("material_index", pm)
    keep = np.ones(len(tp), bool) if whole else pm[tp] == pick
    S.slots, S.tslot = list(me.materials), pm[tp][keep]
    if not keep.any():
        raise ValueError("No faces use that material on %s" % ob.name)
    tv, tl = tv.reshape(-1, 3)[keep], tl.reshape(-1, 3)[keep]
    nv = len(me.vertices)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    M = np.array(ob.matrix_world, np.float64)
    P = (co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]).astype(np.float32)
    nrm = np.empty(nv * 3, np.float32)
    me.vertex_normals.foreach_get("vector", nrm)
    nrm = nrm.reshape(-1, 3) @ np.linalg.inv(M[:3, :3]).astype(np.float32)
    N = (nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)).astype(np.float32)
    ed = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", ed)
    ed = ed.reshape(-1, 2)
    lap, cnt = np.zeros((nv, 3), np.float32), np.zeros(nv, np.float32)
    for a, b in ((0, 1), (1, 0)):
        np.add.at(lap, ed[:, a], P[ed[:, b]] - P[ed[:, a]])
        np.add.at(cnt, ed[:, a], 1.0)
    conc = np.einsum("nj,nj->n", lap / np.maximum(cnt, 1.0)[:, None], N)  # >0 concave, <0 convex
    conc = np.clip(conc / max(float(np.percentile(np.abs(conc), 95)), 1e-9), -1.0, 1.0).astype(np.float32)
    for attempt in (0, 1):
        uv = np.empty(len(me.loops) * 2, np.float32)
        me.uv_layers.active.data.foreach_get("uv", uv)
        uvp = (uv.reshape(-1, 2)[tl] * size).astype(np.float64)
        cross = (uvp[:, 1, 0] - uvp[:, 0, 0]) * (uvp[:, 2, 1] - uvp[:, 0, 1]) - (uvp[:, 1, 1] - uvp[:, 0, 1]) * (uvp[:, 2, 0] - uvp[:, 0, 0])
        area = 0.5 * np.abs(cross).sum()
        if not attempt and area > size * size:  # faces stacked in UV space (a stock box maps every side to 0..1): never rasterise that
            _unwrap(ob)  # paint needs islands of their own
            S.unwrapped = True
            continue
        S.idx, bary = _raster(uvp, size)
        S.occ = S.idx >= 0
        if attempt or area <= 1.2 * S.occ.sum():
            break
        _unwrap(ob)  # smaller overlaps found only after rasterising
        S.unwrapped = True
    S.ys, S.xs = np.nonzero(S.occ)
    S.tid = S.idx[S.ys, S.xs]
    bw = bary[S.ys, S.xs]
    del bary
    S.pos = _gather(P[tv], S.tid, bw)
    sm = np.empty(len(me.polygons), bool)
    me.polygons.foreach_get("use_smooth", sm)
    fn = np.cross(P[tv][:, 1] - P[tv][:, 0], P[tv][:, 2] - P[tv][:, 0])  # flat faces keep their face normal, unlike vertex normals
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    fn *= np.where(np.einsum("tj,tj->t", fn, N[tv].sum(1)) < 0, -1.0, 1.0)[:, None]
    corner = np.where(sm[tp[keep]][:, None, None], N[tv], fn[:, None, :]).astype(np.float32)
    n = _gather(corner, S.tid, bw)
    S.nrm = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    S.curv = _gather(conc[tv][..., None], S.tid, bw)[:, 0]
    Pt = P[tv]
    S.e1, S.e2 = Pt[:, 1] - Pt[:, 0], Pt[:, 2] - Pt[:, 0]
    S.du1, S.du2 = (uvp[:, 1] - uvp[:, 0]).astype(np.float32), (uvp[:, 2] - uvp[:, 0]).astype(np.float32)
    S.g11 = np.einsum("tj,tj->t", S.e1, S.e1)
    S.g12 = np.einsum("tj,tj->t", S.e1, S.e2)
    S.g22 = np.einsum("tj,tj->t", S.e2, S.e2)
    S.det = S.g11 * S.g22 - S.g12 ** 2
    S.tuft, S.tgrad = None, None
    attr = me.color_attributes.get("tuft")  # the Mane step's root(0)-to-tip(1) value, red channel
    if attr is not None and attr.domain == "POINT":
        tc = np.empty(nv * 4, np.float32)
        attr.data.foreach_get("color", tc)
        tc = tc.reshape(-1, 4)[:, 0]
        if tc.max() > 0.0:
            S.tuft = _gather(tc[tv][..., None], S.tid, bw)[:, 0]
            d1, d2 = tc[tv][:, 1] - tc[tv][:, 0], tc[tv][:, 2] - tc[tv][:, 0]
            det = np.where(np.abs(S.det) > 1e-12, S.det, 1.0)
            a, b = (S.g22 * d1 - S.g12 * d2) / det, (S.g11 * d2 - S.g12 * d1) / det
            g = a[:, None] * S.e1 + b[:, None] * S.e2  # uphill direction of the tuft value on each triangle
            S.tgrad = g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)
            S.tgrad[np.abs(d1) + np.abs(d2) < 1e-6] = 0.0
    S.center = (P.min(0) + P.max(0)) / 2.0
    S.bounds = (P.min(0), P.max(0))
    S.diag = float(np.linalg.norm(P.max(0) - P.min(0))) or 1.0
    return S


def _flow(S, kind):
    """Per-pixel stroke direction in texture space: (flow map (size,size,2), strength per pixel 0..1)."""
    n, tid = S.nrm, S.tid
    r = S.pos - S.center
    r = r / np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-9)
    down = np.array([0.0, 0.0, -1.0], np.float32)
    if kind == "down":
        v = np.broadcast_to(down, n.shape).copy()
    elif kind == "normal":
        v = np.cross(np.array([0.0, 0.0, 1.0], np.float32), n)
    elif kind == "radial":
        v = r
    else:  # tufts: out from the centre and sagging down, the sag swelling and fading across the surface
        sway = 0.5 + 0.4 * np.sin(6.0 * (S.pos @ np.array([1.0, 1.3, 0.7], np.float32)) / S.diag)
        v = r + down * sway[:, None]
    if kind == "tufts" and S.tgrad is not None:  # tuft pixels follow root to tip
        g = S.tgrad[tid]
        v = np.where((np.abs(g).sum(1) > 0)[:, None], g, v)
    v = v - np.einsum("nj,nj->n", v, n)[:, None] * n
    ln = np.linalg.norm(v, axis=1)
    strength = np.clip(ln / 0.35, 0.0, 1.0)
    bad = ln < 1e-3
    if bad.any():  # no tangent (a floor under "down"): follow the triangle's first edge so strokes still lie flat
        v[bad] = S.e1[tid[bad]]
        ln = np.linalg.norm(v, axis=1)
    v = v / np.maximum(ln, 1e-9)[:, None]
    e1, e2 = S.e1[tid], S.e2[tid]
    r1, r2 = np.einsum("nj,nj->n", e1, v), np.einsum("nj,nj->n", e2, v)
    det = np.where(np.abs(S.det[tid]) > 1e-12, S.det[tid], 1.0)
    a = (S.g22[tid] * r1 - S.g12[tid] * r2) / det
    b = (S.g11[tid] * r2 - S.g12[tid] * r1) / det
    uvd = a[:, None] * S.du1[tid] + b[:, None] * S.du2[tid]
    ln = np.linalg.norm(uvd, axis=1)
    uvd = np.where(ln[:, None] > 1e-9, uvd / np.maximum(ln, 1e-9)[:, None], np.array([1.0, 0.0], np.float32))
    F = np.zeros((S.size, S.size, 2), np.float32)
    F[S.ys, S.xs] = uvd
    return F, strength


def _ao(S):
    """Baked ambient occlusion for the target material, per covered pixel plus the full map."""
    import bpy
    from .bake import _bake, _device
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    S.device = _device(S.size)
    full = _bake(S.ob, S.mats, S.size, "AO", True)[S.mat.name][:, 0].reshape(S.size, S.size)
    full = _blur(full, 1)
    return full, full[S.ys, S.xs].astype(np.float32)


# ---- strokes ----------------------------------------------------------------


def _stroke_pass(S, F, base, dk, lt, ao_px, rim, rng, count, width, length, cov, col, hgt):
    n, size, occ = len(S.ys), S.size, S.occ
    done = 0
    while done < count:
        m = min(50_000, count - done)
        done += m
        pick = rng.integers(0, n, m)
        pos = np.stack([S.xs[pick], S.ys[pick]], 1).astype(np.float32) + 0.5
        cv, top = S.curv[pick], np.clip(S.nrm[pick, 2], 0.0, 1.0)
        pd = np.clip((1.0 - ao_px[pick]) * 1.6 + np.maximum(cv, 0.0) * 0.7, 0.0, 0.85)
        pl = np.clip((np.maximum(-cv, 0.0) * 1.1 + 0.35 * top * top) * (0.5 + rim), 0.0, 0.85)
        r = rng.random(m)
        dark, light = r < pd, (r >= pd) & (r < pd + pl)
        jit = (0.93 + 0.14 * rng.random(m)).astype(np.float32)[:, None]
        c = base[pick] * jit
        c[dark], c[light] = dk[pick][dark] * jit[dark], lt[pick][light] * jit[light]
        c = np.clip(c, 0.0, 1.0)
        sgn = np.where(dark, -1.0, np.where(light, 1.0, 0.0)).astype(np.float32)
        strength = (0.4 + 0.35 * rng.random(m)).astype(np.float32)
        w_s = (width * (0.7 + 0.6 * rng.random(m))).astype(np.float32)
        omega = ((rng.random(m) - 0.5) * 0.1).astype(np.float32)
        R = int(math.ceil(float(w_s.max()) / 2.0))
        for k in range(length):
            taper = max(0.0, math.sin(math.pi * k / max(1, length - 1))) ** 0.7
            ix, iy = np.floor(pos[:, 0]).astype(np.int64), np.floor(pos[:, 1]).astype(np.int64)
            alive = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
            ix, iy = np.clip(ix, 0, size - 1), np.clip(iy, 0, size - 1)
            alive &= occ[iy, ix]
            if not alive.any():
                break
            radius = np.maximum(0.7, 0.5 * w_s * taper)
            for dy in range(-R, R + 1):
                for dx in range(-R, R + 1):
                    dist = math.hypot(dx, dy)
                    if dist > R + 0.5:
                        continue
                    a = np.clip(1.0 - dist / radius, 0.0, 1.0)
                    a = a * a * (3.0 - 2.0 * a) * strength
                    yy, xx = iy + dy, ix + dx
                    ok = alive & (a > 0.02) & (yy >= 0) & (yy < size) & (xx >= 0) & (xx < size)
                    ok[ok] &= occ[yy[ok], xx[ok]]
                    if not ok.any():
                        continue
                    yy, xx, aa = yy[ok], xx[ok], a[ok]
                    win = aa > cov[yy, xx]
                    yw, xw = yy[win], xx[win]
                    cov[yw, xw] = aa[win]
                    col[yw, xw] = c[ok][win]
                    hgt[yw, xw] = sgn[ok][win] * aa[win]
            d = F[iy, ix]
            cs, sn = np.cos(omega * k), np.sin(omega * k)
            pos[:, 0] += d[:, 0] * cs - d[:, 1] * sn
            pos[:, 1] += d[:, 0] * sn + d[:, 1] * cs


def _compose(S, base, dk, lt, ao_px, F, p, rng, ao_amt, rim, shadow):
    """Shaded base + strokes: (image (size,size,3), height (size,size), stroke coverage, stroke count)."""
    size = S.size
    top = np.clip(S.nrm[:, 2], 0.0, 1.0)
    convex = np.clip(-S.curv, 0.0, 1.0)
    shade = base + (shadow - base) * ((1.0 - ao_px) * ao_amt)[:, None]
    hl = np.clip(rim * (0.6 * convex + 0.4 * top * top), 0.0, 1.0)
    shade = shade + (lt - shade) * hl[:, None]
    img = np.zeros((size, size, 3), np.float32)
    img[S.ys, S.xs] = shade
    width = float(p.get("width") or max(3.0, size / 384.0))
    length = int(p.get("length") or max(8, size // 32))
    density = float(p.get("density", 1.0))
    count = int(min(_MAX_STROKES, density * len(S.ys) / (length * width * 0.5)))
    cov = np.zeros((size, size), np.float32)
    col = np.zeros((size, size, 3), np.float32)
    hgt = np.zeros((size, size), np.float32)
    if count > 0:
        _stroke_pass(S, F, base, dk, lt, ao_px, rim, rng, count, width, length, cov, col, hgt)
    img = img * (1.0 - cov[..., None]) + col * cov[..., None]
    return img, hgt, cov, count


def _normal_map(hgt, occ, strength=3.0):
    h = _blur(hgt, 1)
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5
    n = np.stack([-gx * strength, -gy * strength, np.ones_like(h)], -1)
    n = n / np.linalg.norm(n, axis=-1, keepdims=True)
    out = n * 0.5 + 0.5
    out[~occ] = (0.5, 0.5, 1.0)
    return out


def _save_maps(S, p, color, hgt):
    from .bake import SUFFIX, _safe, _save
    out_dir = Path(p["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    forge = json.loads(S.mat.get("forge") or "{}")
    stem = _safe(forge.get("name") or S.mat.name)
    color = _bleed(np.clip(color, 0.0, 1.0), S.occ)
    normal = _normal_map(hgt, S.occ)
    if p.get("target", "ue") in ("ue", "dx"):
        normal[..., 1] = 1.0 - normal[..., 1]
    files = {}
    for slot, img, srgb in (("base_color", color, True), ("normal", normal, False)):
        fname = "T_%s_%s.png" % (stem, SUFFIX[slot])
        _save(out_dir / fname, np.concatenate([img.reshape(-1, 3), np.ones((S.size * S.size, 1), np.float32)], 1), srgb)
        files[slot] = fname
    return {forge.get("id") or S.mat.name: files}, stem


def _finish(S, p, textures, extra):
    from . import bridge
    return {"textures": textures, "size": S.size, "device": getattr(S, "device", "CPU"), "unwrapped": S.unwrapped,
            "scene": bridge.dump(p["scene"]), **extra}


# ---- ops --------------------------------------------------------------------


def op_paint_strokes(p):
    flow = p.get("flow", "down")
    if flow not in _FLOWS:
        raise ValueError("flow %r unknown. Use one of: %s" % (flow, ", ".join(_FLOWS)))
    S = _prepare(p)
    rng = np.random.default_rng(int(p.get("seed", 1)))
    base = _rgb(p.get("base"), [0.8, 0.55, 0.2])
    shadow = _rgb(p.get("shadow"), base * np.array([0.6, 0.5, 0.55], np.float32))
    light = _rgb(p.get("highlight"), base + (np.array([1.0, 0.95, 0.8], np.float32) - base) * 0.5)
    F, strength = _flow(S, flow)
    ao_full, ao_px = _ao(S)
    n = len(S.ys)
    img, hgt, cov, count = _compose(S, np.broadcast_to(base, (n, 3)), np.broadcast_to(shadow, (n, 3)),
                                    np.broadcast_to(light, (n, 3)), ao_px, F, p, rng,
                                    float(p.get("ao", 0.6)), float(p.get("rim", 0.3)), shadow)
    textures, stem = _save_maps(S, p, img, hgt)
    if p.get("debug"):
        out = Path(p["out_dir"])
        flowmap = np.zeros((S.size, S.size, 3), np.float32)
        flowmap[..., :2] = F
        flowmap[S.ys, S.xs, 2] = strength
        np.save(out / (stem + "_dbg_flow.npy"), flowmap)
        np.save(out / (stem + "_dbg_ao.npy"), np.where(S.occ, ao_full, -1.0).astype(np.float32))
    return _finish(S, p, textures, {"strokes": count, "cover": round(float(cov[S.occ].mean()), 3), "flow": flow})


# ---- reference projection -----------------------------------------------------


def _load_ref(path):
    """(rgb (h,w,3), alpha (h,w)) bottom-up, large files scaled down so they never stall the worker."""
    import bpy
    path = Path(path)
    if not path.is_file():
        raise ValueError("Reference image %s not found; pass absolute paths in images={front: path, ...}" % path)
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        if max(w, h) > 2048:
            k = 2048.0 / max(w, h)
            img.scale(max(1, int(w * k)), max(1, int(h * k)))
            w, h = img.size
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(img)
    px = px.reshape(h, w, 4)
    return px[..., :3].copy(), px[..., 3].copy()


def _sample(rgb, valid, x, y):
    """Bilinear colour and validity at float pixel coordinates (x right, y up)."""
    h, w = valid.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = x.astype(np.int64), y.astype(np.int64)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    wts = valid.astype(np.float32)
    out = np.zeros((len(x), 3), np.float32)
    tot = np.zeros(len(x), np.float32)
    for dx, dy, wgt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        v = wts[y0 + dy, x0 + dx]
        out += rgb[y0 + dy, x0 + dx] * (wgt[:, 0] * v)[:, None]
        tot += wgt[:, 0] * v
    return out / np.maximum(tot, 1e-6)[:, None], tot


def _nearest_fill(pos, color, seen, res=48):
    """Colour for unseen pixels from the nearest seen ones in 3D, spread through a coarse voxel grid."""
    lo, hi = pos.min(0), pos.max(0)
    scale = (res - 1) / max(float((hi - lo).max()), 1e-6)
    ijk = np.clip(((pos - lo) * scale).astype(np.int64), 0, res - 1)
    flat = ijk[:, 0] * res * res + ijk[:, 1] * res + ijk[:, 2]
    cnt = np.bincount(flat[seen], minlength=res ** 3).astype(np.float32)
    grid = np.stack([np.bincount(flat[seen], weights=color[seen, c], minlength=res ** 3) for c in range(3)], 1).astype(np.float32)
    grid /= np.maximum(cnt, 1.0)[:, None]
    grid, filled = grid.reshape(res, res, res, 3), (cnt > 0).reshape(res, res, res)
    need = np.unique(flat[~seen])
    for _ in range(res * 2):
        if filled.reshape(-1)[need].all():
            break
        fp, gp = np.pad(filled, 1), np.pad(grid, ((1, 1), (1, 1), (1, 1), (0, 0)))
        acc, num = np.zeros_like(grid), np.zeros(filled.shape, np.float32)
        for ax in range(3):
            for sh in (0, 2):
                sl = [slice(1, -1)] * 3
                sl[ax] = slice(sh, res + sh)
                acc += gp[tuple(sl)] * fp[tuple(sl)][..., None]
                num += fp[tuple(sl)]
        new = ~filled & (num > 0)
        grid[new] = acc[new] / num[new][:, None]
        filled |= new
    out = color.copy()
    out[~seen] = grid.reshape(-1, 3)[flat[~seen]]
    return out


def op_project_ref(p):
    images = p.get("images") or {}
    bad = [k for k in images if k not in _VIEWS]
    if bad or not images:
        raise ValueError("images keys must be from %s (got %s); pass images={front: path, back: path}" % (", ".join(_VIEWS), ", ".join(bad) or "none"))
    power = _BLENDS.get(p.get("blend", "soft"))
    if power is None:
        raise ValueError("blend %r unknown. Use one of: %s" % (p.get("blend"), ", ".join(_BLENDS)))
    fill = p.get("fill", "strokes")
    if fill not in ("strokes", "flat"):
        raise ValueError("fill %r unknown. Use strokes (painted detail on unseen areas) or flat (nearest colour only)" % fill)
    S = _prepare(p)
    lo, hi = S.bounds
    span = np.maximum(hi - lo, 1e-6)
    rel = (S.pos - lo) / span
    n = S.nrm
    acc, wsum, used = np.zeros((len(S.ys), 3), np.float32), np.zeros(len(S.ys), np.float32), {}
    for view, path in images.items():
        rgb, alpha = _load_ref(path)
        h, w = alpha.shape
        lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        fg = (alpha > 0.5) & (rgb.min(-1) < 0.92)
        if not fg.any():
            raise ValueError("%s image %s has no drawing (all white/transparent)" % (view, path))
        cols, rows = np.nonzero(fg.any(0))[0], np.nonzero(fg.any(1))[0]
        x0, x1, y0, y1 = cols[0], cols[-1], rows[0], rows[-1]
        valid = fg & (lum > 0.2)  # black outlines and the white page never become paint
        if view == "front":
            u, facing = rel[:, 0], -n[:, 1]
        elif view == "back":
            u, facing = 1.0 - rel[:, 0], n[:, 1]
        else:  # side: one picture for both flanks, mirrored for the far one
            right = n[:, 0] >= 0
            u, facing = np.where(right, rel[:, 1], 1.0 - rel[:, 1]), np.abs(n[:, 0])
        c, cover = _sample(rgb, valid, x0 + u * (x1 - x0), y0 + rel[:, 2] * (y1 - y0))
        wgt = np.where(facing > 0.15, np.clip(facing, 0.0, 1.0) ** power, 0.0) * np.clip(cover, 0.0, 1.0)
        acc += c * wgt[:, None]
        wsum += wgt
        used[view] = int((wgt > 0.05).sum())
    seen = wsum > 0.02
    if not seen.any():
        raise ValueError("No surface faces any reference view. Check the model faces -Y (front) and the images match front/back/side")
    proj = _nearest_fill(S.pos, acc / np.maximum(wsum, 1e-6)[:, None], seen)
    if fill == "strokes":
        rng = np.random.default_rng(int(p.get("seed", 1)))
        F, strength = _flow(S, p.get("flow", "tufts"))
        ao_full, ao_px = _ao(S)
        painted, hgt, cov, count = _compose(S, proj, proj * 0.6, np.clip(proj * 1.25 + 0.05, 0, 1), ao_px, F, p, rng,
                                            float(p.get("ao", 0.35)), float(p.get("rim", 0.2)), proj * 0.7)
        painted_px = painted[S.ys, S.xs]
    else:
        hgt, count, painted_px = np.zeros((S.size, S.size), np.float32), 0, proj
    keep = np.clip(wsum / 0.35, 0.0, 1.0)[:, None]
    seen_px = np.where(seen[:, None], acc / np.maximum(wsum, 1e-6)[:, None], proj)
    final = np.zeros((S.size, S.size, 3), np.float32)
    final[S.ys, S.xs] = seen_px * keep + painted_px * (1.0 - keep)
    textures, _ = _save_maps(S, p, final, hgt)
    return _finish(S, p, textures, {"views": used, "seen": round(float(seen.mean()), 3), "strokes": count})


# ---- paint_bake: procedural presets baked to one texture ------------------------

# base/dark/light/band colours; bands = colour bands up the height; root = darkening toward each slot's centre (tuft roots)
_PRESETS = {
    "fur_warm": dict(base=(0.86, 0.52, 0.17), dark=(0.50, 0.24, 0.08), light=(1.0, 0.78, 0.38), band=(0.95, 0.62, 0.22), bands=3, amt=0.5, root=0.0),
    "mane": dict(base=(0.58, 0.27, 0.08), dark=(0.26, 0.10, 0.04), light=(0.90, 0.50, 0.14), band=(0.72, 0.36, 0.10), bands=5, amt=0.5, root=0.7),
    "cream": dict(base=(0.96, 0.86, 0.66), dark=(0.72, 0.57, 0.40), light=(1.0, 0.97, 0.86), band=(0.90, 0.78, 0.58), bands=2, amt=0.3, root=0.0),
    "pink": dict(base=(0.93, 0.56, 0.56), dark=(0.64, 0.28, 0.32), light=(1.0, 0.80, 0.76), band=(0.86, 0.46, 0.48), bands=2, amt=0.25, root=0.0),
}
_STROKES = ("dash", "grain")


def _preset_for(spec, mat, nth):
    if isinstance(spec, str):
        name = spec
    elif isinstance(spec, dict):
        fid = json.loads(mat.get("forge") or "{}").get("id") if mat is not None else None
        name = spec.get(fid) or (spec.get(mat.name) if mat is not None else None) or spec.get("*") or list(_PRESETS)[nth % len(_PRESETS)]
    elif isinstance(spec, (list, tuple)) and spec:
        name = spec[nth % len(spec)]
    else:
        name = list(_PRESETS)[nth % len(_PRESETS)]
    if name not in _PRESETS:
        raise ValueError("preset %r unknown. Use one of: %s" % (name, ", ".join(_PRESETS)))
    return name


def _ao_all(S):
    """Baked AO for every material slot in one pass, per covered pixel."""
    import bpy
    from .bake import _bake, _device
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    S.device = _device(S.size)
    res = _bake(S.ob, S.mats, S.size, "AO", True)
    ao = np.ones(len(S.ys), np.float32)
    for i, m in enumerate(S.slots):
        if m is None or m.name not in res:
            continue
        sel = S.pslot == i
        if sel.any():
            full = _blur(res[m.name][:, 0].reshape(S.size, S.size), 1)
            ao[sel] = full[S.ys[sel], S.xs[sel]]
    return ao


def op_paint_bake(p):
    stroke = p.get("stroke", "dash")
    if stroke not in _STROKES:
        raise ValueError("stroke %r unknown. Use dash (short tapered dashes) or grain (long streaks)" % stroke)
    flow = p.get("flow", "tufts")
    if flow not in _FLOWS:
        raise ValueError("flow %r unknown. Use one of: %s" % (flow, ", ".join(_FLOWS)))
    S = _prepare({**p, "all": True})
    rng = np.random.default_rng(int(p.get("seed", 1)))
    S.pslot = S.tslot[S.tid]
    n = len(S.ys)
    base, dk, lt = (np.zeros((n, 3), np.float32) for _ in range(3))
    used, nth, tuft_used = {}, 0, False
    for slot in np.unique(S.pslot):
        mat = S.slots[slot] if slot < len(S.slots) else None
        name = _preset_for(p.get("presets"), mat, nth)
        nth += 1
        used[name] = used.get(name, 0) + 1
        k, sel = _PRESETS[name], S.pslot == slot
        pos = S.pos[sel]
        z = pos[:, 2]
        t = (z - z.min()) / max(float(np.ptp(z)), 1e-6)
        d = np.linalg.norm(pos - pos.mean(0), axis=1)
        d = d / max(float(d.max()), 1e-6)
        tf = S.tuft[sel] if S.tuft is not None else None
        if tf is not None and tf.max() > 0.0:
            d, tuft_used = np.clip(tf, 0.0, 1.0), True  # root=0 is dark, tip=1 is light
            fall, amt = 1.0, min(1.0, k["root"] * 1.3)  # a real signal: broader, deeper root shade
        else:
            fall, amt = 1.5, k["root"]
        wave = 0.5 + 0.5 * np.sin(t * k["bands"] * 2.0 * math.pi + 0.7)
        band = np.clip((wave - 0.35) / 0.3, 0.0, 1.0)[:, None] * k["amt"]
        c = np.array(k["base"], np.float32) * (1.0 - band) + np.array(k["band"], np.float32) * band
        c = c + (np.array(k["dark"], np.float32) - c) * (amt * (1.0 - d) ** fall)[:, None]
        base[sel] = c * (0.97 + 0.06 * rng.random((len(c), 1))).astype(np.float32)  # fine grain so flat areas never band
        dk[sel], lt[sel] = k["dark"], k["light"]
    size = S.size
    q = dict(p)
    if stroke == "dash":
        q.setdefault("length", max(6, size // 96))
        q.setdefault("width", max(3.0, size / 256.0))
        q["density"] = float(p.get("density", 1.2))
    F, _ = _flow(S, flow)
    ao_px = _ao_all(S)
    img, hgt, cov, count = _compose(S, base, dk, lt, ao_px, F, q, rng, float(p.get("ao", 0.6)), float(p.get("rim", 0.3)), dk)
    from .bake import _safe, _save
    out_dir = Path(p["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = "T_%s_D.png" % _safe(p.get("name") or "Paint")
    color = _bleed(np.clip(img, 0.0, 1.0), S.occ)
    _save(out_dir / fname, np.concatenate([color.reshape(-1, 3), np.ones((size * size, 1), np.float32)], 1), True)
    if p.get("debug"):
        np.save(out_dir / (fname[:-4] + "_dbg_occ.npy"), S.occ)
        if S.tuft is not None:
            tmap = np.full((size, size), -1.0, np.float32)
            tmap[S.ys, S.xs] = S.tuft
            np.save(out_dir / (fname[:-4] + "_dbg_tuft.npy"), tmap)
    return _finish(S, p, {p["out_mat"]: {"base_color": fname}}, {"strokes": count, "stroke": stroke, "presets": used, "texture": fname[:-4], "tuft": tuft_used})
