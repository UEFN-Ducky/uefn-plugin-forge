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


def op_tufts(p):
    objs, _ = bridge.load(p["scene"])
    q = {**p, "unit": float(p["scene"].get("unit", 0.01))}
    meshes = [ob for ob in objs.values() if ob.type == "MESH"]
    if not meshes:
        raise ValueError("No mesh to grow tufts on")
    out = tufts(meshes[0], q)
    out["scene"] = bridge.dump(p["scene"])
    return out
