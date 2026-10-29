"""bpy tufts: pointed flame blades rooted in a surface region, fused into the host with one voxel remesh.

Steps: sample faces of the region (area weighted) -> a tapered, curved blade per sample, rooted inside
the surface -> voxel remesh (one connected mass) -> smooth only the concave valleys (tips are convex,
so they stay sharp) -> decimate to the triangle budget. World space, Forge cm in, metres in bpy.
"""
import math
import random

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import bridge
from . import mesh as hostmesh

RINGS = (0.0, 0.2, 0.4, 0.6, 0.8, 0.94)  # blade cross-sections along its length; the tip closes it
ROOT_IN = 0.3  # fraction of the length that starts inside the surface
THICK = 0.55  # blade thickness / width: flat flame, not a spike


def _pick(specs, centre, idx, unit):
    """True when a face (world centre in metres, index) is inside any region spec."""
    for s in specs:
        if "faces" in s and idx in s["faces"]:
            return True
        if "sphere" in s:
            x, y, z, r = s["sphere"]
            if (centre - Vector((x, y, z)) * unit).length <= r * unit:
                return True
        if "ring" in s:  # radial distance from the front (Y) axis through the centre, in the XZ plane
            x, y, z, r, w = s["ring"]
            d = math.hypot(centre.x - x * unit, centre.z - z * unit)
            if r * unit <= d <= (r + w) * unit:
                return True
    return False


def _blade(bm, root, d, wd, length, width, curl, mat):
    """One tapered blade: 4-sided rings along a curved centreline, closed at the tip and the root."""
    td = d.cross(wd).normalized()
    wd = td.cross(d).normalized()
    rings = []
    for u in RINGS:
        c = root + d * (length * u) + wd * (curl * length * u * u)
        half = width * 0.5 * (1.0 - u)
        t = half * THICK
        rings.append([bm.verts.new(c + wd * half), bm.verts.new(c + td * t), bm.verts.new(c - wd * half), bm.verts.new(c - td * t)])
    tip = bm.verts.new(root + d * length + wd * (curl * length))
    faces = [bm.faces.new(rings[0])]
    for a, b in zip(rings, rings[1:]):
        faces += [bm.faces.new((a[i], a[(i + 1) % 4], b[(i + 1) % 4], b[i])) for i in range(4)]
    faces += [bm.faces.new((rings[-1][i], rings[-1][(i + 1) % 4], tip)) for i in range(4)]
    for f in faces:
        f.material_index = mat


def _grow(bm, ob, p, unit):
    """Blades into bm (host-local space). Returns {layers: [length cm], count}."""
    me = ob.data
    m3 = ob.matrix_world.to_3x3()
    world = ob.matrix_world
    back = world.inverted()
    specs = p["regions"]
    faces, areas = [], []
    for poly in me.polygons:
        centre = world @ poly.center
        if _pick(specs, centre, poly.index, unit):
            faces.append(poly)
            areas.append(poly.area)
    if not faces:
        raise ValueError("region selected no faces; widen the sphere/ring, check its world cm position, or pass faces=[...]")
    rng = random.Random(int(p.get("seed", 0)))
    layers = max(1, int(p.get("layers", 2)))
    length, width, curl = float(p["length"]) * unit, float(p["width"]) * unit, float(p["curl"])
    weights = [1.0 / (1.0 + 0.5 * k) for k in range(layers)]
    total = int(p["count"])
    lengths = []
    for k in range(layers):  # layer 0 is the outer one: longest, widest, hangs lowest
        count = max(1, round(total * weights[k] / sum(weights)))
        L = length * (1.0 - 0.35 * k)
        W = width * (1.0 - 0.2 * k)
        lengths.append(round(L / unit, 2))
        droop = float(p.get("sweep", 0.35)) * (1.0 - 0.5 * k)
        for poly in rng.choices(faces, weights=areas, k=count):
            a, b, c = [Vector(me.vertices[i].co) for i in poly.vertices[:3]]
            r1, r2 = rng.random(), rng.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            point = world @ (a + (b - a) * r1 + (c - a) * r2)
            n = (m3 @ poly.normal).normalized()
            d = (n + Vector((0, 0, -droop))).normalized()
            wd = n.cross(Vector((0, 0, 1)))
            if wd.length < 1e-4:
                wd = n.cross(Vector((1, 0, 0)))
            wd.normalize()
            wd.rotate(Matrix.Rotation(rng.gauss(0, 0.5), 3, n))
            wd = (wd - d * wd.dot(d)).normalized()
            jitter = 0.75 + 0.5 * rng.random()
            root = point - d * (ROOT_IN * L * jitter)
            _blade(bm, back @ root, (back.to_3x3() @ d).normalized(), (back.to_3x3() @ wd).normalized(),
                   L * jitter * (1 + ROOT_IN), W, curl * rng.choice((-1, 1)), poly.material_index)
    return {"layers": lengths, "count": total}


def _smooth_valleys(ob, iterations, strength=0.5):
    """Move only concave vertices (neighbour average lies outside the surface) toward their neighbours."""
    me = ob.data
    n = len(me.vertices)
    ed = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", ed)
    ed = ed.reshape(-1, 2)
    for _ in range(iterations):
        co = np.empty(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        nrm = np.empty(n * 3, np.float32)
        me.vertex_normals.foreach_get("vector", nrm)
        nrm = nrm.reshape(-1, 3)
        sums, cnt = np.zeros_like(co), np.zeros(n, np.float32)
        np.add.at(sums, ed[:, 0], co[ed[:, 1]])
        np.add.at(sums, ed[:, 1], co[ed[:, 0]])
        np.add.at(cnt, ed[:, 0], 1)
        np.add.at(cnt, ed[:, 1], 1)
        lap = sums / np.maximum(cnt, 1)[:, None] - co
        valley = (lap * nrm).sum(1) > 0
        co[valley] += lap[valley] * strength
        me.vertices.foreach_set("co", co.ravel())
        me.update()


def _roots(me):
    parent = list(range(len(me.vertices)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for e in me.edges:
        parent[find(e.vertices[0])] = find(e.vertices[1])
    return [find(i) for i in range(len(parent))]


def _islands(me):
    roots = _roots(me)
    return len({roots[v] for p in me.polygons for v in p.vertices})


def _drop_fragments(ob):
    """Voxel remesh can pinch a blade tip off into a loose speck: keep only the biggest piece."""
    me = ob.data
    roots = _roots(me)
    sizes = {}
    for r in roots:
        sizes[r] = sizes.get(r, 0) + 1
    keep = max(sizes, key=sizes.get)
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[i] for i, r in enumerate(roots) if r != keep], context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()


def _decimate(ob, budget):
    for _ in range(2):
        tris = hostmesh._stats(ob)["tris"]
        if tris <= budget:
            return
        mod = ob.modifiers.new("hq_dec", "DECIMATE")
        mod.ratio = max(0.01, budget * 0.97 / tris)
        with hostmesh._ctx(ob):
            bpy.ops.object.modifier_apply(modifier=mod.name)


def tufts(ob, p):
    unit = float(p.get("unit", 0.01))
    src = hostmesh._snapshot(ob)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    info = _grow(bm, ob, p, unit)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    area = sum(poly.area for poly in ob.data.polygons) * float(ob.matrix_world.to_scale()[0]) ** 2
    voxel = max(float(p["width"]) * unit / 5.0, math.sqrt(2.0 * area / 300000.0))
    ob.data.remesh_voxel_size = voxel
    ob.data.remesh_voxel_adaptivity = 0.0
    with hostmesh._ctx(ob):
        bpy.ops.object.voxel_remesh()
    _drop_fragments(ob)
    _smooth_valleys(ob, int(p.get("smooth", 4)))
    _decimate(ob, int(p["budget"]))
    ob.data.update()
    hostmesh._finish(ob, src)
    return {"summary": "%d tufts, layers %s cm, %d tris" % (info["count"], info["layers"], hostmesh._stats(ob)["tris"]),
            "tufts": info["count"], "layers": info["layers"], "islands": _islands(ob.data),
            "voxel_cm": round(voxel / unit, 2), **hostmesh._stats(ob)}


# ---- style=leaf: the Blender lion's mane. Leaf-shaped quad strips, not fused; UV v and vertex colour run root 0 -> tip 1 ----

LEAF_U = (0.0, 0.3, 0.6, 0.85)  # strip rows; the tip vertex closes it at 1.0
LEAF_HALF = (0.45, 1.0, 0.85, 0.4)  # half-width per row, fraction of the tuft width: a leaf, widest near 30%
DOWN = Vector((0, 0, -1))
BACK = Vector((0, 1, 0))  # Forge heads face -Y
RING_THETA, ROW_THETA = math.radians(52), (math.radians(66), math.radians(158))


def _smooth(a, b, x):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _dir(o, th):
    """Flow of a tuft rooted where the head's outward direction is o (th = angle from the face axis).
    Face side: out and back. Behind the ears: straight down, leaning slightly into the head so no
    tuft normal points up (no pinecone). Crown: along the surface toward the back."""
    oh = Vector((o.x, o.y, 0))
    oh = oh.normalized() if oh.length > 0.15 else BACK.copy()
    front = (o * 0.6 + BACK * 0.7 + DOWN * 0.1).normalized()
    lean = (DOWN - oh * 0.2).normalized()
    tb = BACK - o * o.y
    crown = ((tb.normalized() if tb.length > 0.1 else BACK) + DOWN * 0.3).normalized()
    back = lean.lerp(crown, _smooth(0.3, 0.6, o.z))
    d = front.lerp(back, _smooth(math.radians(75), math.radians(100), th)).normalized()
    wback = _smooth(math.radians(75), math.radians(100), th) * (1 - _smooth(0.3, 0.6, o.z))
    return d, o.lerp(oh, wback).normalized(), oh


def _leaf(bm, uvl, tv, root, d, ref, length, width, curl, bend, mat=0):
    """One tuft: 3 quads and a tip triangle. Face normal = ref made perpendicular to d."""
    n = ref - d * ref.dot(d)
    n = n.normalized() if n.length > 1e-4 else d.orthogonal().normalized()
    w = d.cross(n).normalized()
    rows = []
    for u, hf in zip(LEAF_U, LEAF_HALF):
        c = root + d * (length * u) + DOWN * (bend * length * u * u) + w * (curl * length * u * u)
        h = width * 0.5 * hf
        rows.append((bm.verts.new(c - w * h), bm.verts.new(c + w * h)))
        tv.extend((u, u))
    tip = bm.verts.new(root + d * length + DOWN * (bend * length) + w * (curl * length))
    tv.append(1.0)
    uv = {tip: (0.5, 1.0)}
    for (a, b), u in zip(rows, LEAF_U):
        uv[a], uv[b] = (0.0, u), (1.0, u)
    faces = [bm.faces.new((a0, b0, b1, a1)) for (a0, b0), (a1, b1) in zip(rows, rows[1:])]
    faces.append(bm.faces.new((rows[-1][0], rows[-1][1], tip)))
    for f in faces:
        f.smooth = True
        f.material_index = mat
        for loop in f.loops:
            loop[uvl].uv = uv[loop.vert]


def _beard_sizes(n):
    sizes = []
    while n > 0:
        sizes.append(min(max(1, 5 - len(sizes)), n))
        n -= sizes[-1]
    return sizes


def _plan_head(p, rng):
    """[(o, theta, length factor, width factor, curl, bend, d override|None)] for the whole mane."""
    count, rows = int(p["count"]), max(2, int(p["rows"]))
    ring, peak, beard = int(p["face_ring"]), int(p["peak"]), int(p["beard"])
    plan = []
    cuts = ([(math.pi / 2, math.radians(22))] if peak else []) + ([(-math.pi / 2, math.radians(25))] if beard else [])

    def cut(phi):
        return any(abs((phi - c + math.pi) % (2 * math.pi) - math.pi) < w for c, w in cuts)

    def pos(th, phi):
        return Vector((math.sin(th) * math.cos(phi), -math.cos(th), math.sin(th) * math.sin(phi)))

    cand = [j * 2 * math.pi / (4 * max(ring, 1)) for j in range(4 * max(ring, 1))]
    cand = [c for c in cand if not cut(c)]
    for j in np.linspace(0, len(cand) - 1, ring).astype(int) if ring else []:
        plan.append((pos(RING_THETA, cand[j]), RING_THETA, 1.0, 1.0, 0.1, 0.1, None))
    for i in range(peak):  # V on the forehead: the centre tuft hangs lowest and longest
        k = i - (peak - 1) / 2
        a, el = k * 0.2, math.radians(50) + abs(k) * math.radians(8)
        o = Vector((math.sin(a) * math.cos(el), -math.cos(a) * math.cos(el), math.sin(el)))
        d = (DOWN * 0.8 + BACK * -0.15 + Vector((-math.copysign(1, k) * 0.4 * min(1, abs(k)), 0, 0))).normalized()
        plan.append((o, math.acos(-o.y), 1.0 - 0.1 * abs(k), 0.9, 0.0, 0.15, d))
    for r, n in enumerate(_beard_sizes(beard)):  # pointed beard: rows narrow and lengthen toward the chin
        el = -math.radians(40 + 14 * r)
        for k in range(n):
            m = k - (n - 1) / 2
            a = m * 0.16
            o = Vector((math.sin(a) * math.cos(el), -math.cos(a) * math.cos(el), math.sin(el)))
            d = (DOWN + BACK * -0.15 + Vector((-m * 0.25, 0, 0))).normalized()
            plan.append((o, math.acos(-o.y), (1.15 + 0.15 * r) - 0.18 * abs(m), 0.85, 0.0, 0.1, d))
    left = count - len(plan)
    thetas = [ROW_THETA[0] + (ROW_THETA[1] - ROW_THETA[0]) * k / (rows - 1) for k in range(rows)]
    weights = [math.sin(t) for t in thetas]
    sizes = [round(left * w / sum(weights)) for w in weights]
    sizes[-1] += left - sum(sizes)
    for k, (th, n) in enumerate(zip(thetas, sizes)):
        for j in range(max(0, n)):
            phi = (j + 0.5 * (k % 2)) * 2 * math.pi / max(n, 1) + rng.gauss(0, 0.05)
            if th < math.radians(95) and cut(phi):
                continue
            plan.append((pos(th, phi), th, 0.85 + 0.45 * k / (rows - 1), 1.0, rng.gauss(0, 0.12), 0.25, None))
    while len(plan) < count:  # tufts the face cut-outs skipped go to the back rows
        th, phi = rng.uniform(math.radians(110), ROW_THETA[1]), rng.uniform(0, 2 * math.pi)
        plan.append((pos(th, phi), th, 1.0, 1.0, rng.gauss(0, 0.12), 0.25, None))
    return plan, thetas, sizes


def _plan_tail(p, rng):
    dirt = Vector(p.get("dir") or (0, 1, 0)).normalized()
    a1 = dirt.cross(Vector((0, 0, 1)))
    a1 = a1.normalized() if a1.length > 1e-3 else Vector((1, 0, 0))
    a2 = dirt.cross(a1)
    plan = []
    for i in range(int(p["count"])):
        layer, phi = i % 2, (i // 2) * 2 * math.pi / math.ceil(int(p["count"]) / 2) + 0.4 * (i % 2) + rng.gauss(0, 0.05)
        o = a1 * math.cos(phi) + a2 * math.sin(phi)
        d = (dirt * (0.7 + 0.2 * layer) + o * (0.5 - 0.2 * layer) + DOWN * 0.3).normalized()
        plan.append((o, d, 1.0 - 0.3 * layer, 1.0, rng.gauss(0, 0.1), 0.2))
    return plan


def leaf(ob, p):
    """Leaf tufts as quad strips appended to the host. Roots sit on the host surface (ray from the head centre)."""
    unit = float(p.get("unit", 0.01))
    me = ob.data
    world = ob.matrix_world
    wv = [world @ v.co for v in me.vertices]
    tree = BVHTree.FromPolygons(wv, [tuple(q.vertices) for q in me.polygons])
    lo = Vector((min(v.x for v in wv), min(v.y for v in wv), min(v.z for v in wv)))
    hi = Vector((max(v.x for v in wv), max(v.y for v in wv), max(v.z for v in wv)))
    centre, radius = (lo + hi) / 2, max(hi - lo) / 2
    rng = random.Random(int(p.get("seed", 1)))
    length, width = float(p["length"]) * unit, p.get("width")
    curl = float(p.get("curl", 0.15))
    back = world.inverted()
    nv0 = len(me.vertices)
    name = str(p.get("material") or "Mane")
    mat = next((i for i, m in enumerate(me.materials) if m and m.name == name), None)
    if mat is None:  # own slot so paint_bake can colour the tufts apart from the host
        me.materials.append(bpy.data.materials.get(name) or bpy.data.materials.new(name))
        mat = len(me.materials) - 1
    bm = bmesh.new()
    bm.from_mesh(me)
    uvl = bm.loops.layers.uv.verify()
    tv = []
    if p.get("target") == "tail":
        at = Vector(p["at"]) * unit
        root = at  # the tuft sits exactly on the given tip; snapping to the nearest surface could land on other geometry (the mane)
        w0 = float(width) * unit if width else 0.5 * length
        for o, d, lf, wf, cv, bend in _plan_tail(p, rng):
            _leaf(bm, uvl, tv, back @ root, (back.to_3x3() @ d).normalized(), (back.to_3x3() @ o).normalized(),
                  length * lf * (0.85 + 0.3 * rng.random()), w0 * wf, cv, bend, mat)
        count = int(p["count"])
    else:
        plan, thetas, sizes = _plan_head(p, rng)
        ring_w = 1.6 * 2 * math.pi * radius * math.sin(RING_THETA) / max(int(p["face_ring"]), 1)
        for o, th, lf, wf, cv, bend, d in plan:
            hit = tree.ray_cast(centre, o, radius * 6)[0]
            root = hit if hit is not None else tree.find_nearest(centre + o * radius)[0]
            flow, ref, _ = _dir(o, th)
            if d is None:  # sideways jitter around the horizontal normal keeps the no-pinecone lean
                side = flow.cross(ref)
                flow = (flow + side.normalized() * rng.gauss(0, 0.06)).normalized() if side.length > 1e-4 else flow
            else:
                ref = o
                flow = d
            n_row = max(1, sizes[min(range(len(thetas)), key=lambda i: abs(thetas[i] - th))])
            w_auto = 1.7 * 2 * math.pi * radius * math.sin(th) / n_row
            w_m = float(width) * unit if width else min(max(min(w_auto, ring_w * 1.4), 0.08 * radius), 0.5 * radius)
            _leaf(bm, uvl, tv, back @ root, (back.to_3x3() @ flow).normalized(), (back.to_3x3() @ ref).normalized(),
                  length * lf * (0.85 + 0.3 * rng.random()), w_m * wf, cv, bend, mat)
        count = len(plan)
    bm.to_mesh(me)
    bm.free()
    me.update()
    attr = me.color_attributes.get("tuft") or me.color_attributes.new("tuft", "FLOAT_COLOR", "POINT")
    vals = np.empty(len(me.vertices) * 4, np.float32)
    attr.data.foreach_get("color", vals)
    vals = vals.reshape(-1, 4)
    vals[:, 3] = 1.0
    vals[nv0:, :3] = np.asarray(tv, np.float32)[:, None]
    attr.data.foreach_set("color", vals.ravel())
    me.update()
    stats = hostmesh._stats(ob)
    tail = p.get("target") == "tail"
    extra = "" if tail else "".join(", " + k for k in ("peak", "beard") if int(p[k]))
    return {"summary": "%s leaf %d tufts %.1fk tris%s, material %s" % ("tail" if tail else "mane", count, stats["tris"] / 1000, extra, name),
            "tufts": count, "style": "leaf", **stats}


def op_tufts(p):
    objs, _ = bridge.load(p["scene"])
    q = {**p, "unit": float(p["scene"].get("unit", 0.01))}
    meshes = [ob for ob in objs.values() if ob.type == "MESH"]
    if not meshes:
        raise ValueError("No mesh to grow tufts on")
    out = (leaf if p.get("style") == "leaf" else tufts)(meshes[0], q)
    out["scene"] = bridge.dump(p["scene"])
    return out
