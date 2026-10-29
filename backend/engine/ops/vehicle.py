"""bpy vehicle ops for Forge HQ: car_part (every car part on its own, swappable) and car (a whole car).

Everything is modelled in cm in the car frame (X = the car's left, -Y = front, Z up, ground z=0)
and scaled to metres by p["unit"] at the end. A part's origin is its mount point: its socket.
"""
import json
import math
import random
import re

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import bridge
from .mesh import _tris

TAU = 2 * math.pi
SLOTS = {  # Forge material fields per slot; car paint keeps clearcoat 1.0 / 0.03 for UE
    "paint": {"base_color": [0.55, 0.03, 0.04, 1], "metallic": 0.25, "roughness": 0.3, "clearcoat": 1.0, "clearcoat_roughness": 0.03},
    "chrome": {"base_color": [0.95, 0.95, 0.96, 1], "metallic": 1.0, "roughness": 0.05},
    "rubber": {"base_color": [0.025, 0.025, 0.027, 1], "roughness": 0.9},
    "glass": {"base_color": [0.04, 0.06, 0.07, 1], "roughness": 0.02, "opacity": 0.35},
    "plastic": {"base_color": [0.035, 0.035, 0.04, 1], "roughness": 0.55},
    "emissive": {"base_color": [0.9, 0.9, 0.86, 1], "roughness": 0.2, "emissive": [1.0, 0.95, 0.85], "emissive_strength": 6.0},
    "metal": {"base_color": [0.42, 0.42, 0.43, 1], "metallic": 1.0, "roughness": 0.35},
    "lens_red": {"base_color": [0.45, 0.01, 0.01, 1], "roughness": 0.05, "opacity": 0.7},
    "emissive_red": {"base_color": [0.8, 0.02, 0.02, 1], "emissive": [1.0, 0.05, 0.02], "emissive_strength": 4.0},
    "leather": {"base_color": [0.09, 0.065, 0.05, 1], "roughness": 0.6},
    "accent": {"base_color": [0.75, 0.05, 0.03, 1], "metallic": 0.3, "roughness": 0.35},
}
STYLES = ("sport", "sedan", "suv", "truck", "classic", "scifi")
_STATE = {"paint": None}


def tire_size(code):
    """'225/45R17' -> (width, sidewall, rim radius, outer radius) in cm."""
    m = re.fullmatch(r"\s*(\d{2,3})\s*/\s*(\d{2,3})\s*[A-Za-z-]*\s*(\d{1,2}(?:\.\d+)?)\s*", str(code))
    if not m:
        raise ValueError("tire %r: use a size code like 225/45R17 (width mm / aspect %% R rim inches)" % (code,))
    w = int(m[1]) / 10.0
    side = w * int(m[2]) / 100.0
    rim = float(m[3]) * 2.54 / 2
    return w, side, rim, rim + side


# ---- geometry kit (cm) ---------------------------------------------------------------------


class Geo:
    """One part's bmesh plus its material slot names."""

    def __init__(self):
        self.bm = bmesh.new()
        self.slots = []

    def mi(self, slot):
        if slot not in self.slots:
            self.slots.append(slot)
        return self.slots.index(slot)

    def paint(self, verts, slot):
        i = self.mi(slot)
        for f in {f for v in verts for f in v.link_faces}:
            f.material_index = i


def _rot(axis, deg):
    return Matrix.Rotation(math.radians(deg), 4, axis)


def box(g, size, at=(0, 0, 0), slot="plastic", bevel=0.0, rot=None, seg=2):
    m = Matrix.Translation(Vector(at)) @ (rot or Matrix.Identity(4)) @ Matrix.Diagonal((*size, 1.0))
    verts = bmesh.ops.create_cube(g.bm, size=1.0, matrix=m)["verts"]
    g.paint(verts, slot)
    if bevel > 0:
        edges = list({e for v in verts for e in v.link_edges})
        bmesh.ops.bevel(g.bm, geom=edges, offset=min(bevel, 0.45 * min(size)), offset_type="OFFSET", segments=seg,
                        profile=0.5, affect="EDGES", clamp_overlap=True)


_AXIS = {"x": Matrix.Rotation(math.pi / 2, 4, "Y"), "y": Matrix.Rotation(-math.pi / 2, 4, "X"), "z": Matrix.Identity(4)}


def cyl(g, r, depth, at=(0, 0, 0), axis="x", slot="metal", seg=16, r2=None, rot=None):
    m = Matrix.Translation(Vector(at)) @ (rot or Matrix.Identity(4)) @ _AXIS[axis]
    verts = bmesh.ops.create_cone(g.bm, cap_ends=True, cap_tris=False, segments=seg, radius1=r,
                                  radius2=r if r2 is None else r2, depth=depth, matrix=m)["verts"]
    g.paint(verts, slot)


def lathe(g, prof, seg, slot, matrix=None, closed=True, a0=0.0, a1=TAU):
    """Revolve [(x, r)] around the X axis (then matrix). Returns rows of faces per profile segment."""
    m = matrix or Matrix.Identity(4)
    full = abs(a1 - a0 - TAU) < 1e-6
    n = seg if full else seg + 1
    angles = [a0 + (a1 - a0) * j / seg for j in range(n)]
    bm, mi = g.bm, g.mi(slot)
    rings = []
    for x, r in prof:
        if r < 1e-6:
            rings.append([bm.verts.new(m @ Vector((x, 0, 0)))] * n)
        else:
            rings.append([bm.verts.new(m @ Vector((x, r * math.cos(a), r * math.sin(a)))) for a in angles])
    rows = []
    for i in range(len(prof) if closed else len(prof) - 1):
        a, b = rings[i], rings[(i + 1) % len(prof)]
        row = []
        for j in range(seg):
            k = (j + 1) % n
            quad = list(dict.fromkeys([a[j], b[j], b[k], a[k]]))
            face = None
            if len(quad) >= 3:
                face = bm.faces.new(quad)
                face.material_index = mi
            row.append(face)
        rows.append(row)
    if not full:
        for end in (0, n - 1):
            cap = list(dict.fromkeys(ring[end] for ring in rings))
            if len(cap) >= 3:
                bm.faces.new(cap).material_index = mi
    return rows


def tube(g, pts, r, slot, seg=8, closed=False):
    """Sweep a circle along a polyline (parallel-transport frames), capped unless closed."""
    pts = [Vector(p) for p in pts]
    n = len(pts)
    tans = []
    for i in range(n):
        t = (pts[(i + 1) % n] - pts[i - 1]) if closed else (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)])
        tans.append(t.normalized())
    ref = Vector((0, 0, 1)) if abs(tans[0].z) < 0.9 else Vector((1, 0, 0))
    nrm = (ref - tans[0] * ref.dot(tans[0])).normalized()
    bm, mi = g.bm, g.mi(slot)
    rings = []
    for i in range(n):
        nrm = (nrm - tans[i] * nrm.dot(tans[i])).normalized()
        b = tans[i].cross(nrm)
        rings.append([bm.verts.new(pts[i] + (nrm * math.cos(TAU * j / seg) + b * math.sin(TAU * j / seg)) * r) for j in range(seg)])
    for i in range(n if closed else n - 1):
        a, c = rings[i], rings[(i + 1) % n]
        for j in range(seg):
            bm.faces.new([a[j], a[(j + 1) % seg], c[(j + 1) % seg], c[j]]).material_index = mi
    if not closed:
        bm.faces.new(rings[0]).material_index = mi
        bm.faces.new(list(reversed(rings[-1]))).material_index = mi


def hexa(g, c, slot):
    """Closed six-sided block from 8 corners c[a][b][d] (a, b, d in 0/1)."""
    bm = g.bm
    v = {(a, b, d): bm.verts.new(c[a][b][d]) for a in (0, 1) for b in (0, 1) for d in (0, 1)}
    mi = g.mi(slot)
    for q in (((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)), ((1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)),
              ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)),
              ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), ((0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1))):
        bm.faces.new([v[k] for k in q]).material_index = mi


def slab(g, P, nu, nv, thick, slot):
    """Closed panel: the surface P(u, v) (u, v in 0..1) plus a copy thick cm behind it, walls between."""
    bm, mi = g.bm, g.mi(slot)
    pts = [[Vector(P(i / nu, j / nv)) for j in range(nv + 1)] for i in range(nu + 1)]

    def normal(i, j):
        du = pts[min(i + 1, nu)][j] - pts[max(i - 1, 0)][j]
        dv = pts[i][min(j + 1, nv)] - pts[i][max(j - 1, 0)]
        n = du.cross(dv)
        return n.normalized() if n.length > 1e-9 else Vector((0, 0, 1))

    top = [[bm.verts.new(pts[i][j]) for j in range(nv + 1)] for i in range(nu + 1)]
    bot = [[bm.verts.new(pts[i][j] - normal(i, j) * thick) for j in range(nv + 1)] for i in range(nu + 1)]
    for i in range(nu):
        for j in range(nv):
            bm.faces.new([top[i][j], top[i + 1][j], top[i + 1][j + 1], top[i][j + 1]]).material_index = mi
            bm.faces.new([bot[i][j], bot[i][j + 1], bot[i + 1][j + 1], bot[i + 1][j]]).material_index = mi
    ring = [(i, 0) for i in range(nu)] + [(nu, j) for j in range(nv)] + [(i, nv) for i in range(nu, 0, -1)] + [(0, j) for j in range(nv, 0, -1)]
    for k, (i, j) in enumerate(ring):
        i2, j2 = ring[(k + 1) % len(ring)]
        bm.faces.new([top[i][j], bot[i][j], bot[i2][j2], top[i2][j2]]).material_index = mi


def loft(g, sections, slot, closed_ends=True):
    """Join closed rings of equal size (lists of points) into a tube, n-gon caps at both ends."""
    bm, mi = g.bm, g.mi(slot)
    rings = [[bm.verts.new(p) for p in sec] for sec in sections]
    m = len(rings[0])
    for a, b in zip(rings, rings[1:]):
        for j in range(m):
            bm.faces.new([a[j], a[(j + 1) % m], b[(j + 1) % m], b[j]]).material_index = mi
    if closed_ends:
        bm.faces.new(rings[0]).material_index = mi
        bm.faces.new(list(reversed(rings[-1]))).material_index = mi
    return rings


def rrect(w, h, r, n=4):
    """Rounded rectangle outline in (x, z), counter-clockwise, 4*(n+1) points."""
    r = min(r, w / 2 - 1e-3, h / 2 - 1e-3)
    pts = []
    for cx, cz, a in ((w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180), (w / 2 - r, -h / 2 + r, 270)):
        for k in range(n + 1):
            t = math.radians(a + 90 * k / n)
            pts.append((cx + r * math.cos(t), cz + r * math.sin(t)))
    return pts


def ring_prism(g, outer, inner, y0, y1, slot, z=0.0):
    """Frame between two outlines in (x, z) with the same point count, from y0 to y1."""
    bm, mi = g.bm, g.mi(slot)
    loops = [[bm.verts.new((x, y, zz + z)) for x, zz in pts] for pts, y in ((outer, y0), (outer, y1), (inner, y1), (inner, y0))]
    m = len(outer)
    for k in range(4):
        a, b = loops[k], loops[(k + 1) % 4]
        for j in range(m):
            bm.faces.new([a[j], a[(j + 1) % m], b[(j + 1) % m], b[j]]).material_index = mi


def text(g, body, size, depth, place, slot):
    """Embossed text: a font mesh in (u, v, w) mapped through place(u, v, w) -> Vector."""
    cu = bpy.data.curves.new("forge_txt", "FONT")
    # resolution 1: glyph outlines at control points only; finer curves give sub-2 mm tris that qa's z-fight cells pair up
    cu.body, cu.size, cu.extrude, cu.resolution_u = body, size, depth / 2, 1
    cu.align_x, cu.align_y = "CENTER", "CENTER"
    ob = bpy.data.objects.new("forge_txt", cu)
    bpy.context.scene.collection.objects.link(ob)
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(ob, do_unlink=True)
    src = bmesh.new()
    src.from_mesh(me)
    bpy.data.meshes.remove(me)
    bmesh.ops.remove_doubles(src, verts=src.verts[:], dist=0.06)  # above qa's 0.05 cm weld, or glyph corners turn non-manifold
    bmesh.ops.dissolve_degenerate(src, dist=0.06, edges=src.edges[:])
    vmap = {v: g.bm.verts.new(place(*v.co)) for v in src.verts}
    mi = g.mi(slot)
    for f in src.faces:
        try:
            g.bm.faces.new([vmap[v] for v in f.verts]).material_index = mi
        except ValueError:
            pass
    src.free()


def append_mesh(g, me):
    vmap = [g.bm.verts.new(v.co) for v in me.vertices]
    for poly in me.polygons:
        g.bm.faces.new([vmap[i] for i in poly.vertices]).material_index = poly.material_index


def solidify(bm, thick, bevel=0.0, slots=12):
    """Sheet bmesh -> closed shell mesh (Solidify with rim, optional angle-limited bevel). Frees bm."""
    me = bpy.data.meshes.new("forge_sheet")
    bm.to_mesh(me)
    bm.free()
    for _ in range(slots):
        me.materials.append(None)
    ob = bpy.data.objects.new("forge_sheet", me)
    bpy.context.scene.collection.objects.link(ob)
    s = ob.modifiers.new("solid", "SOLIDIFY")
    s.thickness, s.offset, s.use_rim, s.use_even_offset = thick, -1.0, True, True
    if bevel > 0:
        b = ob.modifiers.new("bevel", "BEVEL")
        b.width, b.segments, b.limit_method, b.angle_limit = bevel, 2, "ANGLE", math.radians(30)
    out = bpy.data.meshes.new_from_object(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.meshes.remove(me)
    return out


def mirror_x(g):
    for v in g.bm.verts:
        v.co.x = -v.co.x
    bmesh.ops.reverse_faces(g.bm, faces=g.bm.faces[:])


def flip_y(g):
    for v in g.bm.verts:
        v.co.y = -v.co.y
    bmesh.ops.reverse_faces(g.bm, faces=g.bm.faces[:])


# ---- parts (origin = mount point, facing -Y, left-side parts on +X) -------------------------


def _tire_profile(w, side, rim, d, cols):
    hw, rc, tw = w / 2, rim + side - d, 0.4 * w
    left = [(-hw * 0.80, rim - 0.4), (-hw * 0.86, rim + side * 0.1), (-hw * 0.96, rim + side * 0.3), (-hw, rim + side * 0.5),
            (-hw * 0.97, rim + side * 0.7), (-hw * 0.88, rc - side * 0.1), (-tw - 0.05 * w, rc - 0.5)]
    crown = [(-tw + 2 * tw * k / cols, rc) for k in range(cols + 1)]
    right = [(-x, r) for x, r in reversed(left)]
    return left + crown + right, len(left)


_TREAD = {  # style -> (circumferential groove columns of 8, lateral groove rule(row, col) -> groove?)
    "sport": ({2, 5}, lambda j, k: (j + (k >= 3 and k <= 4)) % 2 == 1),
    "sedan": ({2, 5}, lambda j, k: (j + (k >= 3 and k <= 4)) % 2 == 1),
    "suv": ({3, 4}, lambda j, k: (j + (k >= 5)) % 2 == 1),
    "truck": ({3, 4}, lambda j, k: (j + (k >= 5)) % 2 == 1),
    "classic": ({2, 5}, lambda j, k: False),
    "scifi": ({1, 6}, lambda j, k: k in (0, 7) and j % 2 == 1),
}


def tire(g, code, style, tread=64, label="FORGE"):
    w, side, rim, R = tire_size(code)
    d = min(0.8, side * 0.1)
    cols = 8
    prof, first = _tire_profile(w, side, rim, d, cols)
    seg = tread * 2
    rows = lathe(g, prof, seg, "rubber")
    grooves, lateral = _TREAD.get(style, _TREAD["sedan"])
    blocks = [rows[first + k][j] for k in range(cols) if k not in grooves for j in range(seg)
              if rows[first + k][j] is not None and not lateral(j, k)]
    geom = bmesh.ops.extrude_face_region(g.bm, geom=blocks)["geom"]
    bmesh.ops.delete(g.bm, geom=blocks, context="FACES_ONLY")  # the op keeps the originals inside the new blocks
    for v in geom:
        if isinstance(v, bmesh.types.BMVert):
            v.co += Vector((0, v.co.y, v.co.z)).normalized() * d
    side_pts = sorted(prof[:first], key=lambda p: p[1])  # outer sidewall x by radius, for the lettering

    def xs(r):
        for (x0, r0), (x1, r1) in zip(side_pts, side_pts[1:]):
            if r0 <= r <= r1:
                return -(x0 + (x1 - x0) * (r - r0) / ((r1 - r0) or 1.0))
        return w / 2

    rmid, size = rim + side * 0.5, max(0.6, side * 0.2)
    for start in ((math.pi / 2, -math.pi / 2) if label else ()):
        def place(u, v, z, start=start):
            r = rmid + v
            a = start - u / rmid
            return Vector((xs(r) + z + 0.05, r * math.cos(a), r * math.sin(a)))
        text(g, "%s  %s" % (label, code), size, 0.3, place, "rubber")
    return {"r": R, "w": w, "rim": rim, "tread": tread}


def _spoke(g, phi, r0, r1, w0, w1, x0, x1, t, slot, twist=0.0):
    c = [[[None, None], [None, None]], [[None, None], [None, None]]]
    for a, (r, w, x, ang) in enumerate(((r0, w0, x0, phi), (r1, w1, x1, phi + twist))):
        d = Vector((0, math.cos(ang), math.sin(ang)))
        tn = Vector((0, -math.sin(ang), math.cos(ang)))
        for b, s in enumerate((-1, 1)):
            for k, dx in enumerate((0.0, -t)):
                c[a][b][k] = Vector((x + dx, 0, 0)) + d * r + tn * (s * w / 2)
    hexa(g, c, slot)


RIM_SPOKES = {"sport": "10", "sedan": "5", "suv": "6", "truck": "dish", "classic": "dish", "scifi": "3"}


def rim(g, rim_r, bw, spokes, style, slot="chrome"):
    """Barrel, hub, spokes and lug nuts; the face is on +X. Returns hub-face x."""
    o, lip = bw / 2, 1.4
    lathe(g, [(o, rim_r + lip), (o - 1.0, rim_r + lip), (o - 1.2, rim_r - 0.1), (-o + 1.2, rim_r - 0.1),
              (-o + 1.0, rim_r + lip), (-o, rim_r + lip), (-o, rim_r - 1.1), (o, rim_r - 1.1)], 48, slot)
    xf = o - 2.5 - (1.5 if style in ("sport", "scifi") else 0.6)
    rh, rin = 0.3 * rim_r, rim_r - 0.9
    lathe(g, [(xf + 1.2, 0), (xf + 1.2, rh * 0.55), (xf + 0.6, rh), (xf - 3.5, rh), (xf - 3.5, 0)], 24, slot)
    spokes = str(spokes)
    if spokes == "dish":
        lathe(g, [(xf + 0.6, rh * 0.9), (o - 1.5, rin), (o - 2.3, rin), (xf - 0.4, rh * 0.9)], 32, "metal" if style == "truck" else slot)
        for i in range(8):  # vent windows read as dark pockets
            a = TAU * i / 8
            cyl(g, rim_r * 0.09, 0.4, (xf + 0.2 + (o - 1.5 - xf) * 0.55, rim_r * 0.62 * math.cos(a), rim_r * 0.62 * math.sin(a)), "x", "plastic", 10)
        cyl(g, rh * 1.15, 1.6, (xf + 1.6, 0, 0), "x", "chrome", 24)
    elif spokes == "mesh":
        for i in range(10):
            phi = TAU * i / 10
            _spoke(g, phi, rh * 0.85, rin, 1.6, 1.9, xf + 0.6, o - 1.4, 1.6, slot, math.radians(22))
            _spoke(g, phi + TAU / 20, rh * 0.85, rin, 1.6, 1.9, xf - 0.4, o - 2.2, 1.6, slot, math.radians(-22))
    else:
        n = 5 if spokes == "10" else max(2, min(20, int(spokes)))
        split = spokes == "10"
        for i in range(n):
            phi = TAU * i / n
            if split:
                for s in (-1, 1):
                    _spoke(g, phi + s * math.radians(7), rh * 0.85, rin, rh * 0.42, rim_r * 0.12, xf + 0.8, o - 1.4, 2.4, slot)
            else:
                _spoke(g, phi, rh * 0.85, rin, min(rh * 0.95, TAU * rh / n * 0.8), min(rim_r * 1.7 / n, rim_r * 0.4), xf + 0.8, o - 1.4, 2.6, slot)
    for i in range(5):
        a = TAU * i / 5 + 0.3
        cyl(g, 0.9, 1.4, (xf + 1.6, rh * 0.62 * math.cos(a), rh * 0.62 * math.sin(a)), "x", "chrome", 6)
    cyl(g, rh * 0.32, 0.8, (xf + 1.6, 0, 0), "x", "plastic", 16)
    return xf


def disc_radius(rim_r, disc_mm=None):
    mm = float(disc_mm) if disc_mm else max(280.0, min(380.0, rim_r * 2 * 10 - 110.0))
    return min(mm / 20.0, rim_r - 4.5)


def brake_disc(g, rd, xd, xf):
    t, ri = 2.8, rd * 0.55
    lathe(g, [(xd + t / 2, ri), (xd + t / 2, rd - 0.2), (xd + t / 2 - 0.2, rd), (xd - t / 2 + 0.2, rd), (xd - t / 2, rd - 0.2), (xd - t / 2, ri)], 48, "metal")
    for i in range(12):  # drilled look: dark dimples on the outboard face
        a = TAU * i / 12
        cyl(g, 0.45, 0.2, (xd + t / 2, (ri + rd) / 2 * math.cos(a), (ri + rd) / 2 * math.sin(a)), "x", "plastic", 8)
    lathe(g, [(xd + t / 2 - 0.4, ri + 0.6), (xf - 3.6, ri * 0.75), (xf - 3.6, 0), (xd + t / 2 - 0.4, 0)], 32, "metal")


def caliper(g, rd, xd):
    t = 2.8
    lathe(g, [(xd - t / 2 - 2.0, rd - 4.6), (xd + t / 2 + 2.0, rd - 4.6), (xd + t / 2 + 2.0, rd + 1.2), (xd - t / 2 - 2.0, rd + 1.2)],
          10, "accent", a0=math.radians(5), a1=math.radians(60))


def wheel(g, q):
    code, style = q["tire"], q["style"]
    w, side, rr, R = tire_size(code)
    out = tire(g, code, style, int(q.get("tread", 64)), q.get("label", "FORGE"))
    xf = rim(g, rr, w * 0.85, q.get("spokes") or RIM_SPOKES[style], style)
    rd = disc_radius(rr, q.get("disc"))
    if q.get("brake", True) and rd > 6:
        brake_disc(g, rd, xf - 6.0, xf)
        out["disc"] = rd
    return out


LIGHT = {"sport": (55, 14, 18), "sedan": (60, 22, 20), "suv": (50, 25, 22), "truck": (45, 30, 20), "classic": (20, 20, 16), "scifi": (75, 6, 14)}


def light(g, q, tail=False):
    style = q["style"]
    w, h, d = LIGHT[style] if not tail else {"classic": (14, 14, 10), "scifi": (80, 5, 10)}.get(style, (45, 14, 12))
    w, h, d = float(q.get("width") or w), float(q.get("height") or h), float(q.get("length") or d)
    lens = "lens_red" if tail else "glass"
    glow = "emissive_red" if tail else "emissive"
    if style == "classic":
        r = min(w, h) / 2
        lathe(g, [(0, 0), (0, r), (d * 0.6, r), (d * 0.6, r * 0.9), (d * 0.1, 0)], 32, "chrome", Matrix.Rotation(-math.pi / 2, 4, "Z"))
        lathe(g, [(d * 0.6, 0), (d * 0.6, r * 0.88), (d * 0.75, r * 0.85), (d * 0.9, r * 0.5), (d * 0.95, 0)], 32, lens, Matrix.Rotation(-math.pi / 2, 4, "Z"))
        cyl(g, r * 0.3, 1.0, (0, -d * 0.62, 0), "y", glow, 16)
        lathe(g, [(d * 0.6, r * 0.88), (d * 0.6, r * 1.05), (d * 0.68, r * 1.05), (d * 0.72, r * 0.88)], 32, "chrome", Matrix.Rotation(-math.pi / 2, 4, "Z"))
    else:
        box(g, (w, d * 0.6, h), (0, -d * 0.3, 0), "plastic", min(h, d) * 0.15)
        count = 1 if style == "scifi" or w < 2.4 * h else 2
        rb = min(h * 0.4, w / (2 * count) * 0.8)
        db = d * 0.25
        if style != "scifi":
            for i in range(count):
                x = (-w / 4 + w / 2 * i) if count == 2 else 0.0
                m = Matrix.Translation((x, -d * 0.6, 0)) @ Matrix.Rotation(-math.pi / 2, 4, "Z")
                lathe(g, [(0, rb * 0.3), (db, rb), (db, rb * 0.9), (0.4, rb * 0.22)], 24, "chrome", m)
                cyl(g, rb * 0.35, 1.0, (x, -d * 0.6 - db * 0.5, 0), "y", glow if tail else "chrome", 16)
                if not tail:
                    lathe(g, [(db * 0.4, 0), (db * 0.4, rb * 0.36), (db * 0.6, rb * 0.3), (db * 0.75, 0)], 16, glow, m)
        y = -d + 1.6
        if tail and style != "scifi":
            pts = [(-w / 2 + 2, y, h / 2 - 2), (-w / 2 + 2, y, -h / 2 + 2), (w / 2 - 2, y, -h / 2 + 2), (w / 2 - 2, y, h / 2 - 2)]
            tube(g, pts, 0.6, glow, 6)
        elif style == "scifi":
            box(g, (w - 2, 1.0, max(1.0, h * 0.4)), (0, y, 0), glow, 0.3)
        else:
            tube(g, [(-w / 2 + 2, y, -h / 2 + 2), (0, y - 0.3, -h / 2 + 1.8), (w / 2 - 2, y, -h / 2 + 3)], 0.5, glow, 6)
        box(g, (w - 0.4, 1.0, h - 0.4), (0, -d + 0.5, 0), lens, min(1.0, h * 0.2))
    if tail:
        flip_y(g)
    return {"w": w, "h": h, "d": d}


GRILLE = {"sport": (70, 16, "plastic"), "sedan": (80, 24, "chrome"), "suv": (90, 34, "chrome"), "truck": (110, 45, "chrome"),
          "classic": (60, 36, "chrome"), "scifi": (100, 8, "plastic")}


def grille(g, q):
    style = q["style"]
    w, h, frame = GRILLE[style]
    w, h = float(q.get("width") or w), float(q.get("height") or h)
    ring_prism(g, rrect(w, h, h * 0.3), rrect(w - 4.4, h - 4.4, h * 0.3 - 2), 0.0, -4.0, frame)
    box(g, (w - 3, 0.6, h - 3), (0, -0.4, 0), "plastic")
    iw, ih = w - 4.4, h - 4.4
    if style == "sport":
        nx, nz = max(2, int(iw / 3.2)), max(1, int(ih / 3.2))
        for i in range(nx):
            for k in range(nz):
                x = -iw / 2 + iw * (i + 0.5) / nx
                z = -ih / 2 + ih * (k + 0.5) / nz
                box(g, (1.9, 2.0, 1.9), (x, -2.0, z), "plastic", rot=_rot("Y", 45))
    elif style == "classic":
        n = max(3, int(iw / 3.5))
        for i in range(n):
            box(g, (0.8, 2.6, ih), (-iw / 2 + iw * (i + 0.5) / n, -2.0, 0), "chrome", 0.2)
    elif style == "scifi":
        for k in range(3):
            box(g, (iw, 1.0, 0.6), (0, -2.0, -ih / 2 + ih * (k + 0.5) / 3), "emissive")
    else:
        n = {"sedan": 5, "suv": 3, "truck": 3}[style]
        for k in range(n):
            box(g, (iw, 2.6, ih / (n * 2.2)), (0, -1.9, -ih / 2 + ih * (k + 0.5) / n), frame, 0.3)  # sunk 1 mm into the backing, not flush
        if style in ("suv", "truck"):
            box(g, (ih / 3, 2.8, ih), (0, -2.2, 0), frame, 0.3)
    return {"w": w, "h": h}


def mirror(g, q):
    style = q["style"]
    sx, sy, sz = {"sport": (18, 8, 10), "truck": (20, 10, 30), "classic": (14, 7, 9)}.get(style, (22, 10, 12))
    box(g, (8, 3, 2.5), (4, 0, 1), "plastic", 0.6)
    cx = 8 + sx / 2
    box(g, (sx, sy, sz), (cx, 1.5, 3), "chrome" if style == "classic" else "paint", min(sy, sz) * 0.35)
    box(g, (sx - 2.4, 0.6, sz - 2.4), (cx, 1.5 + sy / 2 + 0.05, 3), "chrome", 0.2)
    if style not in ("classic",):
        box(g, (sx * 0.4, 0.5, 1.2), (cx + sx * 0.2, 1.5 - sy / 2 - 0.1, 3 - sz * 0.25), "emissive", 0.2)
    return {"w": sx}


def _bulge(v, amp):
    return amp * math.sin(math.pi * v)


def door(g, q):
    style = q["style"]
    ld = float(q.get("length") or {"sport": 120, "classic": 115, "truck": 115, "scifi": 120}.get(style, 105))
    hd = float(q.get("height") or 70)
    slab(g, lambda u, v: (_bulge(v, 2.5), 2 + u * (ld - 4), v * hd), 14, 8, 1.0, "paint")
    box(g, (2.0, ld - 6, hd - 8), (-5.5, ld / 2, hd / 2), "plastic", 0.8)
    box(g, (5, ld * 0.4, 3), (-7.5, ld * 0.55, hd * 0.62), "leather", 1.2)
    box(g, (1.0, 8, 2), (-6.6, ld * 0.25, hd * 0.75), "chrome", 0.4)
    for y in (2.5, ld - 2.5):
        box(g, (5.5, 1.0, hd - 2), (-2.6, y, hd / 2), "paint", 0.3)
    box(g, (5.5, ld - 4, 1.0), (-2.6, ld / 2, 1.2), "paint", 0.3)
    box(g, (2.0, 14, 2.5), (1.6, ld * 0.72, hd * 0.82), "chrome", 0.8)
    hg = 40.0
    rake = 0.45 if q.get("front", True) else 0.15

    def glass(u, v):
        y0 = 4 + v * hg * rake
        return (-1.5 - 4 * v, y0 + u * (ld - 6 - y0), hd + 1 + v * hg)
    slab(g, glass, 10, 6, 0.5, "glass")
    if style in ("sedan", "suv", "truck"):
        tube(g, [Vector(glass(0, 0)) + Vector((0.4, -1.4, 0)), Vector(glass(0, 1)) + Vector((0.4, -1.0, 1.4)),
                 Vector(glass(1, 1)) + Vector((0.4, 1.0, 1.4)), Vector(glass(1, 0)) + Vector((0.4, 1.4, 0))], 1.2, "paint", 8)
    return {"len": ld, "h": hd}


def window(g, q):
    w, h = float(q.get("width") or 90), float(q.get("height") or 40)
    slab(g, lambda u, v: (-3 * v * v, (u - 0.5) * (w - v * w * 0.25), v * h), 12, 6, 0.5, "glass")
    return {"w": w, "h": h}


def hood(g, q, trunk=False):
    style = q["style"]
    w = float(q.get("width") or 150)
    ln = float(q.get("length") or (85 if trunk else 135))
    s = 1 if trunk else -1
    bulge = style in ("suv", "truck")

    def P(u, v):
        x = (u - 0.5) * w * (1 - 0.08 * v)
        z = 4 * (1 - (2 * u - 1) ** 2) - v * v * (14 if not trunk else 18)
        if bulge and 0.3 < u < 0.7:
            z += 3.0 * math.sin(math.pi * (u - 0.3) / 0.4) * min(1.0, v * 5) * min(1.0, (1 - v) * 5)
        return (x, s * v * ln, z)
    slab(g, P, 16, 12, 1.0, "paint")
    if not trunk and style in ("sport", "scifi"):
        for x in (-w * 0.22, w * 0.22):
            box(g, (w * 0.16, ln * 0.18, 1.6), (x, -ln * 0.55, P(0.5 + x / w, 0.55)[2] + 0.6), "plastic", 0.5)
    if trunk and style in ("sport", "scifi"):
        box(g, (w * 0.9, 6, 4), (0, ln - 2, P(0.5, 1)[2] + 2), "paint", 1.5)
    return {"w": w, "len": ln}


def fender(g, q):
    _, _, _, R = tire_size(q["tire"])
    ra = R + 4.5
    y0, y1, z0, z1 = -ra - 25, ra + 20, -R + 10, R + 30
    nu, nv = 30, 16
    bm = bmesh.new()
    grid = [[bm.verts.new((0, y0 + (y1 - y0) * i / nu, z0 + (z1 - z0) * j / nv)) for j in range(nv + 1)] for i in range(nu + 1)]
    for i in range(nu):
        for j in range(nv):
            quad = [grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]]
            c = sum((v.co for v in quad), Vector()) / 4
            if math.hypot(c.y, c.z) >= ra:
                bm.faces.new(quad)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    for v in bm.verts:
        dist = math.hypot(v.co.y, v.co.z)
        if any(len(e.link_faces) == 1 for e in v.link_edges) and dist < ra + (z1 - z0) / nv:
            v.co.y, v.co.z = v.co.y * ra / dist, v.co.z * ra / dist
        dist = math.hypot(v.co.y, v.co.z)
        v.co.x = 14 + 2.5 * math.exp(-max(0.0, dist - ra) / 6) + 1.5 * math.sin(math.pi * (v.co.z - z0) / (z1 - z0))
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.3)  # the arch snap stacks neighbours on one point
    bmesh.ops.dissolve_degenerate(bm, dist=0.3, edges=bm.edges[:])
    for f in bm.faces:
        f.material_index = g.mi("paint")
    append_mesh(g, solidify(bm, 1.0, 0.25))
    return {"ra": ra}


BUMPER = {"sport": (10, 30), "sedan": (12, 34), "suv": (14, 40), "truck": (16, 42), "classic": (10, 18), "scifi": (10, 26)}


def bumper(g, q, rear=False):
    style = q["style"]
    dd, h = BUMPER[style]
    w = float(q.get("width") or 185)
    dd, h = float(q.get("length") or dd), float(q.get("height") or h)
    sec = [(3, h), (-dd * 0.7, h), (-dd, h * 0.7), (-dd, h * 0.28), (-dd * 0.85, 0), (3, 0)]
    if style == "classic":
        sec = [(3, h), (-dd * 0.6, h), (-dd, h * 0.6), (-dd, h * 0.35), (-dd * 0.6, 0), (3, 0)]
    n = 24
    sections = []
    for i in range(n + 1):
        x = -w / 2 + w * i / n
        e = abs(x) / (w / 2)
        back = dd * 0.9 * e ** 4
        taper = 1 - 0.15 * e ** 6
        sections.append([Vector((x, y * taper + back, z * taper + h * (1 - taper) / 2)) for y, z in sec])
    loft(g, sections, "chrome" if style == "classic" else "paint")
    if style == "classic":
        pass
    elif not rear:
        box(g, (w * 0.45, 2.0, h * 0.28), (0, -dd - 0.4, h * 0.3), "plastic", 0.6)
        if style == "sport":
            box(g, (w * 0.9, 8, 1.2), (0, -dd * 0.8 - 2, 0.6), "plastic", 0.4)
            for x in (-w * 0.36, w * 0.36):
                box(g, (w * 0.14, 2.0, h * 0.35), (x, -dd * 0.88, h * 0.35), "plastic", 0.6)
        elif style in ("suv", "truck"):
            box(g, (w * 0.5, 4, 1.6), (0, -dd * 0.7, 0.8), "metal", 0.5)
        if style in ("sedan", "suv"):
            for x in (-w * 0.36, w * 0.36):
                cyl(g, 3.2, 1.2, (x, -dd * 0.98, h * 0.35), "y", "emissive", 16)
        if style == "scifi":
            box(g, (w * 0.8, 1.0, 0.8), (0, -dd - 0.2, h * 0.6), "emissive")
    else:
        for x in (-w * 0.4, w * 0.4):
            box(g, (w * 0.08, 1.0, 2.0), (x, -dd * 0.98, h * 0.55), "lens_red", 0.3)
        if style in ("suv", "truck"):
            box(g, (w * 0.3, 8, 2.0), (0, -dd * 0.7, h + 0.8), "plastic", 0.5)
    if rear:
        flip_y(g)
    return {"w": w, "h": h, "d": dd}


def _airfoil(c, t=0.12, camber=-0.04, n=8):
    up, lo = [], []
    for i in range(n + 1):
        s = (1 - math.cos(math.pi * i / n)) / 2
        yt = 5 * t * c * (0.2969 * math.sqrt(s) - 0.126 * s - 0.3516 * s * s + 0.2843 * s ** 3 - 0.1036 * s ** 4)
        zc = camber * c * 4 * s * (1 - s)
        up.append((-c / 2 + s * c, zc + yt))
        lo.append((-c / 2 + s * c, zc - yt))
    return up + list(reversed(lo[1:-1]))


def spoiler(g, q):
    style = q["style"]
    w = float(q.get("width") or 130)
    if style in ("sport", "scifi", "truck"):
        hw, c = float(q.get("height") or (18 if style != "truck" else 10)), 24.0 if style != "truck" else 18.0
        foil = _airfoil(c)
        aoa = math.radians(-8)
        tiers = (0.0, 9.0) if style == "scifi" else (0.0,)
        for dz in tiers:
            secs = []
            for i in range(17):
                x = -w / 2 + w * i / 16
                secs.append([Vector((x, y * math.cos(aoa) - z * math.sin(aoa) + c * 0.2, hw + dz + y * math.sin(aoa) + z * math.cos(aoa)))
                             for y, z in foil])
            loft(g, secs, "paint")
        for x in (-w / 2 - 0.5, w / 2 + 0.5):
            box(g, (1.0, c * 1.15, 14 + (9 if style == "scifi" else 0)), (x, c * 0.2, hw + 1), "plastic", 0.3)
        for x in (-w * 0.3, w * 0.3):
            box(g, (2.0, 8, hw), (x, 0, hw / 2), "plastic", 0.5)
    else:  # ducktail lip or roof spoiler: a wedge across the width
        hh, c = (4.0, 14.0) if style in ("sedan", "classic") else (5.0, 26.0)
        sec = [(0, 0), (c, 0), (c * 0.95, hh), (c * 0.2, hh * 0.4)]
        loft(g, [[Vector((-w / 2 + w * i / 12, y, z)) for y, z in sec] for i in range(13)], "paint")
    return {"w": w}


def diffuser(g, q):
    w, ln = float(q.get("width") or 120), float(q.get("length") or 40)
    tilt = _rot("X", 10)
    box(g, (w, ln, 1.5), (0, ln / 2, 2), "plastic", 0.4, tilt)
    n = 5
    for i in range(n):
        x = -w / 2 + w * (i + 0.5) / n
        box(g, (1.0, ln * 0.9, 7), (x, ln * 0.5, -1), "plastic", 0.2, tilt)
    return {"w": w}


def exhaust(g, q):
    style = q["style"]
    xs = (-5.5, 5.5) if style in ("sport", "scifi") else (0.0,)
    r = 6.0 if style == "truck" else 4.5
    for x in xs:
        tube(g, [(x, 0, 0), (x, 30, 0), (x, 40, 1.5)], 2.6, "metal", 10)
        m = Matrix.Translation((x, 38, 1.5)) @ Matrix.Rotation(math.pi / 2, 4, "Z")
        if style == "scifi":
            ring_prism(g, rrect(r * 2.4, r * 1.2, 1.0), rrect(r * 2.0, r * 0.8, 0.6), 38, 50, "chrome", 1.5)
            box(g, (r * 2.0, 0.6, r * 0.8), (x, 46, 1.5), "emissive")
        else:
            lathe(g, [(0, r * 0.92), (12, r), (12.4, r * 0.9), (12, r * 0.8), (1, r * 0.7)], 24, "chrome", m)
            lathe(g, [(5, 0), (5, r * 0.75), (6, r * 0.75), (6, 0)], 16, "plastic", m)
    return {"tips": len(xs)}


def seat(g, q):
    style = q["style"]
    w = float(q.get("width") or (130 if style == "classic" else 50))
    box(g, (w * 0.8, 40, 14), (0, 0, 7), "plastic", 1.0)
    box(g, (w, 50, 12), (0, 0, 19.5), "leather", 4.0)  # 5 mm into the base: flush faces z-fight
    back = Matrix.Translation((0, 24, 20)) @ _rot("X", -15)
    tall = 75 if style in ("sport", "scifi") else 58
    box(g, (w, 12, tall), (0, 0, tall / 2), "leather", 4.0, back)
    if style in ("sport", "scifi"):
        for s in (-1, 1):
            box(g, (8, 46, 8), (s * (w / 2 - 3), 0, 28), "leather", 3.0)
            box(g, (8, 14, tall * 0.55), (0, 0, 0), "leather", 3.0, back @ Matrix.Translation((s * (w / 2 - 3), -5, tall * 0.4)))
    else:
        for xx in ((-w / 3, 0, w / 3) if style == "classic" else (0,)):
            hr = back @ Matrix.Translation((xx, 0, tall + 10))
            box(g, (26, 10, 18), (0, 0, 0), "leather", 3.5, hr)
            for s in (-6, 6):
                cyl(g, 0.6, 12, (0, 0, 0), "z", "chrome", 8, rot=hr @ Matrix.Translation((s, 0, -9)))
    return {"w": w}


def steering_wheel(g, q):
    style = q["style"]
    r = float(q.get("width") or 37) / 2
    tilt = _rot("X", 20)
    pts = []
    for i in range(32):
        a = TAU * i / 32
        z = r * math.sin(a)
        if style in ("sport", "scifi"):
            z = max(z, -r * 0.8)
        pts.append(tilt @ Vector((r * math.cos(a), 0, z)))
    tube(g, pts, 1.6, "leather", 8, closed=True)
    cyl(g, 6, 5, (0, 0, 0), "y", "plastic", 20, rot=tilt)
    for ang in (0, 180, 270):
        a = math.radians(ang)
        mid = (r + 5) / 2
        box(g, (r - 5, 1.6, 4), (0, 0, 0), "plastic", 0.6,
            tilt @ Matrix.Translation((mid * math.cos(a), 0.5, mid * math.sin(a))) @ _rot("Y", -ang))
    cyl(g, 2.5, 15, (0, 0, 0), "y", "plastic", 12, rot=tilt @ Matrix.Translation((0, -9, 0)))
    return {"r": r}


def dashboard(g, q):
    w = float(q.get("width") or 150)
    sec = [(0, 0), (0, 30), (8, 38), (30, 37), (42, 26), (40, 10), (28, 0)]
    secs = []
    for i in range(17):
        x = -w / 2 + w * i / 16
        e = abs(x) / (w / 2)
        secs.append([Vector((x, y * (1 - 0.1 * e ** 4), z)) for y, z in sec])
    loft(g, secs, "plastic")
    dx = w * 0.25
    box(g, (36, 12, 10), (dx, 30, 38), "plastic", 3.0)
    for s in (-8, 8):
        cyl(g, 5, 1.0, (dx + s, 36.5, 36), "y", "emissive", 20)
    box(g, (22, 1.0, 13), (0, 40.5, 28), "emissive", 0.4, _rot("X", -15))
    for x in (-w * 0.38, -12, 12, w * 0.38):
        box(g, (10, 1.2, 4), (x, 41.5, 22), "chrome", 0.4)
    return {"w": w}


def engine_block(g, q):
    style = q["style"]
    v8 = style != "sedan"
    box(g, (40, 55, 30), (0, 0, 25), "metal", 1.0)
    box(g, (32, 45, 10.5), (0, 2, 5.25), "metal", 1.0)  # stacked boxes overlap 5 mm: flush faces z-fight
    if v8:
        for s in (-1, 1):
            rot = _rot("Y", s * 40)
            box(g, (14, 52, 10), (s * 14, 0, 44), "metal", 1.0, rot)
            box(g, (12, 50, 5), (s * 18, 0, 50), "accent", 1.5, rot)
        box(g, (16, 40, 10), (0, 0, 48), "metal", 1.5)
        if style == "classic":
            cyl(g, 14, 7, (0, 0, 58), "z", "chrome", 32)
        else:
            box(g, (20, 16, 8), (0, -18, 56), "plastic", 2.0)
    else:
        box(g, (24, 50, 10), (0, 0, 44.5), "metal", 1.0)
        box(g, (22, 48, 6), (0, 0, 52), "accent", 2.0)
        for i in range(4):
            tube(g, [(-10, -18 + 12 * i, 47), (-20, -18 + 12 * i, 52), (-24, -10 + 6 * i, 58)], 1.6, "metal", 8)
    for r, z in ((7, 20), (5, 35), (4, 12)):
        cyl(g, r, 2.5, (8 if r == 4 else 0, -29, z), "y", "metal", 20)
    return {"v8": v8}


def suspension(g, q):
    box(g, (6, 8, 22), (-6, 0, 0), "metal", 1.0)
    for y in (-15, 15):
        tube(g, [(-8, 0, -9), (-45, y, -8)], 1.6, "metal", 8)
        tube(g, [(-8, 0, 10), (-38, y * 0.8, 13)], 1.3, "metal", 8)
    a, b = Vector((-28, 0, -7)), Vector((-32, 0, 38))
    axis = (b - a).normalized()
    cyl(g, 2.2, (b - a).length, (a + b) / 2, "z", "chrome", 12, rot=axis.to_track_quat("Z", "Y").to_matrix().to_4x4())
    side = axis.orthogonal().normalized()
    other = axis.cross(side)
    pts = []
    turns, steps = 6, 96
    for i in range(steps + 1):
        t = i / steps
        ang = TAU * turns * t
        pts.append(a + axis * ((b - a).length * (0.15 + 0.6 * t)) + (side * math.cos(ang) + other * math.sin(ang)) * 5.0)
    tube(g, pts, 0.8, "accent", 6)
    return {"chassis": [-45, 0, -8]}


def plate(g, q):
    us = q.get("region", "us" if q["style"] in ("truck", "classic") else "eu") == "us"
    w, h = (30.5, 15.2) if us else (52.0, 11.0)
    box(g, (w, 0.3, h), (0, -0.15, 0), "chrome", 0.1)
    ring_prism(g, rrect(w, h, 1.0), rrect(w - 1.2, h - 1.2, 0.6), -0.3, -0.6, "plastic")
    for x in (-w / 2 + 3, w / 2 - 3):
        cyl(g, 0.5, 0.4, (x, -0.5, h / 2 - 1.6), "y", "chrome", 8)
    body = str(q.get("text") or "FORGE 7")
    text(g, body, h * 0.45, 0.3, lambda u, v, z: Vector((u, -0.3 - z - 0.15, v - h * 0.05)), "plastic")
    if q.get("rear"):  # turn to face +Y; a mirror would print the text backwards
        for v in g.bm.verts:
            v.co.x, v.co.y = -v.co.x, -v.co.y
    return {"w": w, "h": h}


def wiper(g, q):
    ln = float(q.get("length") or 55)
    cyl(g, 1.6, 2.0, (0, 0, 0.5), "z", "plastic", 12)
    box(g, (ln * 0.85, 1.4, 1.0), (ln * 0.42, 0, 1.6), "plastic", 0.3)
    box(g, (ln, 0.8, 1.4), (ln * 0.5 + 4, 1.2, 0.8), "rubber", 0.2)
    return {"len": ln}


def _tire_only(g, q):
    return tire(g, q["tire"], q["style"], int(q.get("tread", 64)), q.get("label", "FORGE"))


def _rim_only(g, q):
    w, _, rr, R = tire_size(q["tire"])
    rim(g, rr, w * 0.85, q.get("spokes") or RIM_SPOKES[q["style"]], q["style"])
    return {"rim": rr}


def _disc_only(g, q):
    w, _, rr, _ = tire_size(q["tire"])
    rd = disc_radius(rr, q.get("disc"))
    xf = w * 0.425 - 2.5 - (1.5 if q["style"] in ("sport", "scifi") else 0.6)
    brake_disc(g, rd, xf - 6.0, xf)
    return {"disc": rd}


def _caliper_only(g, q):
    w, _, rr, _ = tire_size(q["tire"])
    rd = disc_radius(rr, q.get("disc"))
    xf = w * 0.425 - 2.5 - (1.5 if q["style"] in ("sport", "scifi") else 0.6)
    caliper(g, rd, xf - 6.0)
    return {"disc": rd}


# kind -> (builder, socket name, mirrors for the right side)
PARTS = {
    "wheel": (wheel, "Hub", False), "rim": (_rim_only, "Hub", False), "tire": (_tire_only, "Hub", False),
    "brake_disc": (_disc_only, "Hub", False), "caliper": (_caliper_only, "Hub", True),
    "headlight": (light, "Mount", True), "taillight": (lambda g, q: light(g, q, tail=True), "Mount", True),
    "grille": (grille, "Mount", False), "mirror": (mirror, "Mount", True), "door": (door, "Hinge", True),
    "hood": (hood, "Hinge", False), "trunk": (lambda g, q: hood(g, q, trunk=True), "Hinge", False),
    "bumper_front": (bumper, "Mount", False), "bumper_rear": (lambda g, q: bumper(g, q, rear=True), "Mount", False),
    "fender": (fender, "Hub", True), "spoiler": (spoiler, "Mount", False), "diffuser": (diffuser, "Mount", False),
    "exhaust": (exhaust, "Mount", False), "seat": (seat, "Mount", False), "steering_wheel": (steering_wheel, "Column", False),
    "dashboard": (dashboard, "Mount", False), "engine": (engine_block, "Mount", False), "suspension": (suspension, "Hub", True),
    "license_plate": (plate, "Mount", False), "wiper": (wiper, "Pivot", False), "window": (window, "Mount", True),
}
STYLE_TIRE = {"sport": "255/35R19", "sedan": "225/45R17", "suv": "255/55R19", "truck": "265/70R17", "classic": "205/70R15", "scifi": "245/40R20"}


def _collapse_slivers(bm, at, cell=0.1):
    """Parallel faces whose world centres (at = placement, cm) share one qa z-fight cell: collapse the shortest edge."""
    at = Vector(at)
    for _ in range(4):
        bm.normal_update()
        seen, edges = {}, set()
        for f in bm.faces:
            key = tuple(round(c / cell) for c in f.calc_center_median() + at)
            o = next((o for o in seen.get(key, ()) if abs(f.normal.dot(o.normal)) > 0.99), None)
            if o is not None:
                edges.add(min((e for x in (f, o) for e in x.edges), key=lambda e: e.calc_length()))
            seen.setdefault(key, []).append(f)
        if not edges:
            return
        bmesh.ops.collapse(bm, edges=list(edges), uvs=False)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-4, edges=bm.edges[:])


def _emit(g, name, u, at=(0, 0, 0), smooth=True):
    bm = g.bm
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.06)  # cm, before the unit scale: what qa welds, welded here
    bmesh.ops.dissolve_degenerate(bm, dist=0.06, edges=bm.edges[:])
    # ngons, and quads under 0.1 cm2 (the scene splits those into 2 tris in one qa z-fight cell, so split them here)
    big = [f for f in bm.faces if len(f.verts) > 4 or (len(f.verts) == 4 and f.calc_area() < 0.1)]
    if big:
        bmesh.ops.triangulate(bm, faces=big, quad_method="BEAUTY", ngon_method="BEAUTY")
    _collapse_slivers(bm, at)
    # sub-mm solidify/bevel folds leave edges with 3+ faces: keep the two biggest, the rest are slivers
    extra = {f for e in bm.edges if len(e.link_faces) > 2 for f in sorted(e.link_faces, key=lambda f: -f.calc_area())[2:]}
    if extra:
        bmesh.ops.delete(bm, geom=list(extra), context="FACES_ONLY")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.transform(Matrix.Scale(u, 4))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for s in g.slots:
        me.materials.append(_material(s))
    me.polygons.foreach_set("use_smooth", [smooth] * len(me.polygons))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.matrix_world = Matrix.Translation(Vector(at) * u)  # location alone waits for a depsgraph update dump never runs
    return ob


def _material(slot):
    name = "Car_" + slot.title().replace("_", "")
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        forge = {"id": "mcar_" + slot, "name": name, "base_color": [0.8, 0.8, 0.8, 1], "metallic": 0.0, "roughness": 0.5,
                 "emissive": [0, 0, 0], "emissive_strength": 0.0, "opacity": 1.0, "textures": {}}
        forge.update(json.loads(json.dumps(SLOTS[slot])))
        if slot == "paint" and _STATE["paint"]:
            forge["base_color"] = list(_STATE["paint"])[:3] + [1.0]
        mat["forge"] = json.dumps(forge)
    return mat


def build_part(spec, u):
    """spec: {kind, style, side, name, at, ...size params} -> (object, info)."""
    kind = spec["kind"]
    fn, socket, sided = PARTS[kind]
    q = dict(spec)
    q.setdefault("tire", STYLE_TIRE[q["style"]])
    random.seed(int(q.get("seed", 1)))
    g = Geo()
    info = fn(g, q) or {}
    side = q.get("side", "left")
    if side == "right":
        if sided:
            mirror_x(g)
        elif kind in ("wheel", "rim", "tire", "brake_disc"):  # turn, don't mirror: sidewall text still reads
            for v in g.bm.verts:
                v.co.x, v.co.y = -v.co.x, -v.co.y
    tris = sum(len(f.verts) - 2 for f in g.bm.faces)
    ob = _emit(g, spec.get("name") or "SM_" + kind.title().replace("_", ""), u, spec.get("at") or (0, 0, 0))
    info.update(kind=kind, socket=socket, tris=_tris(ob))
    return ob, info


def car_part(p):
    u = float(p.get("unit", 0.01))
    _STATE["paint"] = p.get("paint")
    parts = {}
    for spec in p["parts"]:
        ob, info = build_part(spec, u)
        parts[ob.name] = info
    return {"parts": parts}


# ---- whole car ---------------------------------------------------------------------------

# body: L, W, H, wheelbase, clearance, tire, greenhouse (windshield base, top, rear top, rear base),
#       belt, hood-front, deck (fractions of H), doors per side, front overhang share, style
BODIES = {
    "sedan": (480, 185, 145, 280, 14, "225/45R17", (.33, .46, .70, .82), .62, .58, .66, 2, .44, "sedan"),
    "coupe": (460, 185, 135, 270, 13, "245/40R18", (.36, .50, .66, .84), .62, .56, .64, 1, .44, "sport"),
    "hatch": (410, 178, 148, 260, 14, "205/55R16", (.33, .46, .86, .96), .60, .58, .62, 2, .46, "sedan"),
    "suv": (480, 195, 175, 285, 21, "255/55R19", (.32, .43, .90, .97), .60, .60, .62, 2, .45, "suv"),
    "pickup": (560, 200, 190, 340, 23, "265/70R17", (.30, .40, .55, .57), .58, .56, .58, 2, .40, "truck"),
    "limo": (720, 190, 148, 520, 14, "225/55R17", (.22, .30, .82, .90), .62, .58, .66, 3, .42, "sedan"),
    "sports": (445, 195, 120, 265, 11, "255/35R19", (.38, .52, .64, .90), .64, .55, .60, 1, .45, "sport"),
    "van": (520, 200, 200, 330, 17, "235/65R16", (.18, .27, .97, .99), .55, .50, .55, 2, .30, "suv"),
    "classic": (470, 180, 140, 275, 16, "205/70R15", (.36, .47, .66, .76), .64, .60, .68, 1, .45, "classic"),
    "kart": (180, 130, 60, 105, 3, "110/50R5", None, 0, 0, 0, 0, .45, "sport"),
}
TK = [0.0, 0.10, 0.16, 0.36, 0.56, 0.62, 0.84, 0.92, 1.0]
T_SEAMS = [0.10, 0.20, 0.58, 0.63, 0.66, 0.83, 0.86]
GAP = 0.4  # cm between panels


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class Body:
    def __init__(self, p):
        (L, W, H, wb, clr, code, gh, belt, nose, deck, doors, fo, style) = BODIES[p["body"]]
        self.kind = p["body"]
        self.L, self.W, self.H = float(p.get("length_cm") or L), float(p.get("width_cm") or W), float(p.get("height_cm") or H)
        if p.get("wheelbase_cm"):
            self.wb = float(p["wheelbase_cm"])
        else:  # a limo stretches between the axles; other bodies scale
            self.wb = wb + (self.L - L) if self.kind == "limo" else wb * self.L / L
        self.clr, self.style = clr, p.get("style") or style
        self.tire = (p.get("parts") or {}).get("tire") or code
        self.tw, self.side, self.rim_r, self.R = tire_size(self.tire)
        self.gh, self.belt, self.nose, self.deck, self.doors = gh, belt, nose, deck, int(p.get("doors") or doors)
        self.track = float(p.get("track_cm") or (self.W - self.tw - 8))
        self.bd = {"sport": 10, "sedan": 12, "suv": 14, "truck": 16, "classic": 10, "scifi": 10}[self.style]
        self.y0, self.y1 = -self.L / 2 + self.bd, self.L / 2 - self.bd
        self.yf = -self.L / 2 + (self.L - self.wb) * fo
        self.yr = self.yf + self.wb
        self.ra = self.R + 4.5
        self.zs = belt * self.H

    def f(self, y):
        return (y - self.y0) / (self.y1 - self.y0)

    def y(self, f):
        return self.y0 + f * (self.y1 - self.y0)

    def top(self, f):
        a0, a1, c1, c2 = self.gh
        H, zs = self.H, self.zs
        if f < a0:
            return self.nose * H + (zs + 1 - self.nose * H) * _smooth(f / a0) ** 0.7
        if f < a1:
            return zs + 1 + (H * 0.985 - zs - 1) * math.sin(math.pi / 2 * (f - a0) / (a1 - a0)) ** 1.2
        if f < c1:
            return H * (0.985 + 0.015 * math.sin(math.pi * (f - a1) / (c1 - a1)))
        if f < c2:
            return H * 0.985 + (self.deck * H - H * 0.985) * _smooth((f - c1) / (c2 - c1))
        return self.deck * H - 0.03 * H * (f - c2) / max(1e-3, 1 - c2)

    def half(self, f):
        """Half-section control points A..I in (x, z)."""
        e = abs(2 * f - 1)
        w = self.W / 2 * (0.8 + 0.2 * (1 - e ** 4) ** 0.25)
        zb = self.clr + 10 * max(0.0, 1 - f / 0.08) ** 2 + 12 * max(0.0, (f - 0.9) / 0.1) ** 2
        zt = self.top(f)
        zs = min(self.zs, zt - 1.0)
        g = max(0.0, min(1.0, (zt - zs - 2) / max(1.0, self.H - zs)))
        return [(0.0, zb), (w - 10, zb), (w - 2, zb + 6), (w, zb + (zs - zb) * 0.45), (w - 1.5, zs),
                (w - 4 - 8 * g, zt + (zs + 3 - zt) * g), (w - 6 - 22 * g, zt - 4 * g + 0.5 * (1 - g)),
                (0.8 * (w - 6 - 22 * g), zt - 0.5 * g + 1.5 * (1 - g)), (0.0, zt + 2 * (1 - g))]


def _cr(p0, p1, p2, p3, s):
    return tuple(0.5 * (2 * b + (-a + c) * s + (2 * a - 5 * b + 4 * c - d) * s * s + (-a + 3 * b - 3 * c + d) * s ** 3)
                 for a, b, c, d in zip(p0, p1, p2, p3))


def _at(ctrl, t):
    i = max(0, min(len(TK) - 2, next(k for k in range(len(TK) - 1) if t <= TK[k + 1] + 1e-12)))
    s = (t - TK[i]) / (TK[i + 1] - TK[i])
    ph0 = (-ctrl[1][0], ctrl[1][1])
    ph1 = (-ctrl[-2][0], ctrl[-2][1])
    pts = [ph0] + ctrl + [ph1]
    return _cr(pts[i], pts[i + 1], pts[i + 2], pts[i + 3], s)


def _samples(seams, n, near):
    out = sorted(set([0.0, 1.0] + list(seams)))
    for k in range(1, n):
        s = k / n
        if all(abs(s - x) > near for x in out):
            out.append(s)
    return sorted(out)


def _panels(B):
    """Labels as a function of (f, t, side) plus the f seams they need."""
    a0, a1, c1, c2 = B.gh
    L = B.y1 - B.y0
    fd0 = max(a0 - 0.02, B.f(B.yf + B.ra + 6))
    fd1 = min(B.f(B.yr - B.ra - 6), c2 + (0.0 if B.kind == "pickup" else 0.06))
    n = B.doors
    cuts = [fd0 + (fd1 - fd0) * k / n for k in range(n + 1)]
    pw = 4.0 / L
    wa, wc = a0 + (a1 - a0) * 0.6, c1 + (c2 - c1) * 0.35
    windows = []
    edges = [wa] + [c for c in cuts[1:-1] if wa < c < wc] + [wc]
    for k, (s0, s1) in enumerate(zip(edges, edges[1:])):
        lo = s0 + (pw if k else 0.0)
        hi = s1 - (pw if k < len(edges) - 2 else 0.0)
        if hi - lo > 2 * pw:
            windows.append((lo, hi))
    names = ["F", "R"] if n == 2 else ["F"] if n == 1 else ["F", "M", "R"]
    seams = [a0, a1, c1, c2, fd0, fd1, wa, wc] + cuts + [x for w in windows for x in w]
    bed = B.kind == "pickup"

    def label(f, t, s):
        if t < 0.10:
            return "Underbody"
        if t < 0.58:
            if f < fd0:
                return "Fender_" + s
            if f >= fd1:
                return "Quarter_" + s
            if t < 0.20:
                return "Rocker_" + s
            for k in range(n):
                if cuts[k] <= f < cuts[k + 1]:
                    return "Door_%s%s" % (names[k], s)
            return "Quarter_" + s
        if f < a0:
            return "Hood"
        if f >= c2:
            if bed:
                return "Quarter_" + s if t < 0.63 else None
            return "Trunk"
        if t >= 0.66 and f < a1 and f >= a0:
            return "Windshield"
        if t >= 0.66 and f >= c1:
            return "RearWindow"
        if 0.63 <= t < 0.83:
            for k, (lo, hi) in enumerate(windows):
                if lo <= f < hi:
                    door = next((names[d] for d in range(n) if cuts[d] <= (lo + hi) / 2 < cuts[d + 1]), "Q")
                    return "Window_%s%s" % (door, s)
        return "Roof"
    return label, seams, cuts, names


def _body(B, u):
    """Body shell panels as separate objects with 4 mm gaps, wheel arches and glass."""
    label, seams, cuts, names = _panels(B)
    fs = _samples(seams, max(24, int((B.y1 - B.y0) / 8)), 2.0 / (B.y1 - B.y0))
    ts = _samples(T_SEAMS, 26, 0.012)
    ring = [(t, "L") for t in ts] + [(t, "R") for t in reversed(ts[1:-1])]
    bm = bmesh.new()
    lay = bm.faces.layers.int.new("panel")
    tlay = bm.verts.layers.float.new("t")
    names_list = []
    rows = []
    for f in fs:
        ctrl = B.half(f)
        y = B.y(f)
        row = []
        for t, s in ring:
            x, z = _at(ctrl, t)
            v = bm.verts.new((x if s == "L" else -x, y, z))
            v[tlay] = t
            row.append(v)
        rows.append(row)
    m = len(ring)

    def tag(face, name):
        if name not in names_list:
            names_list.append(name)
        face[lay] = names_list.index(name)

    for i in range(len(fs) - 1):
        fm = (fs[i] + fs[i + 1]) / 2
        for j in range(m):
            k = (j + 1) % m
            tm = (ring[j][0] + ring[k][0]) / 2
            side = ring[j][1] if ring[j][1] == ring[k][1] else "L"
            face = bm.faces.new([rows[i][j], rows[i][k], rows[i + 1][k], rows[i + 1][j]])
            tag(face, label(fm, tm, side) or "-")
    tag(bm.faces.new(rows[0]), "Fascia")
    tag(bm.faces.new(list(reversed(rows[-1]))), "Tail")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    xin = B.W / 2 - 32
    gone = [f for f in bm.faces if names_list[f[lay]] == "-"]
    for yh in (B.yf, B.yr):
        for f in bm.faces:
            c = f.calc_center_median()
            if abs(c.x) > xin and (c.y - yh) ** 2 + (c.z - B.R) ** 2 < B.ra ** 2:
                gone.append(f)
    bmesh.ops.delete(bm, geom=list(set(gone)), context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    for v in bm.verts:
        if abs(v.co.x) > B.W / 2 - 15 and any(len(e.link_faces) < 2 for e in v.link_edges):
            yh = B.yf if abs(v.co.y - B.yf) < abs(v.co.y - B.yr) else B.yr
            d = math.hypot(v.co.y - yh, v.co.z - B.R)
            if d < B.ra + 8:
                v.co.y = yh + (v.co.y - yh) * B.ra / d
                v.co.z = B.R + (v.co.z - B.R) * B.ra / d
    objs = {}
    for idx, name in enumerate(names_list):
        if name == "-":
            continue
        pb = bm.copy()
        play = pb.faces.layers.int["panel"]
        bmesh.ops.delete(pb, geom=[f for f in pb.faces if f[play] != idx], context="FACES")
        if not pb.faces:
            pb.free()
            continue
        pb.normal_update()
        before = {f: f.normal.copy() for f in pb.faces}
        moves = {}
        for v in {v for e in pb.edges if len(e.link_faces) == 1 for v in e.verts}:
            c = sum((f.calc_center_median() for f in v.link_faces), Vector()) / len(v.link_faces)
            d = c - v.co
            d -= v.normal * d.dot(v.normal)
            if d.length > 1e-6:
                moves[v] = d.normalized() * GAP / 2
        for v, d in moves.items():
            v.co += d
        pb.normal_update()  # strips narrower than the gap fold over: drop them, or solidify welds them non-manifold
        folded = [f for f in pb.faces if f.calc_area() < 0.05 or f.normal.dot(before[f]) < 0.2]
        if folded and len(folded) < len(pb.faces):
            bmesh.ops.delete(pb, geom=folded, context="FACES_ONLY")
            bmesh.ops.delete(pb, geom=[v for v in pb.verts if not v.link_faces], context="VERTS")
        glass = name.startswith(("Window", "Windshield", "RearWindow"))
        slot = "glass" if glass else "plastic" if name.startswith(("Underbody", "Rocker")) else "paint"
        for f in pb.faces:
            f.material_index = 0
        me = solidify(pb, 0.5 if glass else 1.0, 0.0 if glass else 0.25, 1)
        g = Geo()
        g.mi(slot)
        append_mesh(g, me)
        bpy.data.meshes.remove(me)
        objs[name] = g
    return objs, cuts, names


def _well(B, x_sign, yh):
    g = Geo()
    xin, xout = B.W / 2 - 32, B.W / 2 - 4
    dz = math.asin(min(0.99, (B.R - B.clr) / B.ra))
    m = Matrix.Translation((0, yh, B.R)) @ Matrix.Diagonal((x_sign, 1, 1, 1))
    # liner + inner wall as one L profile: two lathes would share the wall at xin (non-manifold once welded)
    lathe(g, [(xin - 1, 0), (xin - 1, B.ra + 1), (xout, B.ra + 1), (xout, B.ra), (xin, B.ra), (xin, 0)], 24, "plastic", m,
          a0=-dz, a1=math.pi + dz)
    if x_sign < 0:
        bmesh.ops.reverse_faces(g.bm, faces=g.bm.faces[:])
    return g


def _pivot(g, at):
    """Move a body panel's origin to at (cm): returns at for _emit."""
    for v in g.bm.verts:
        v.co -= Vector(at)
    return at


def _kart(B, u, style, parts):
    out = []
    g = Geo()
    w, ln = 70.0, 150.0
    for x in (-w / 2, w / 2):
        tube(g, [(x * 0.7, -ln / 2, 8), (x, -ln / 2 + 30, 6), (x, ln / 2 - 20, 6), (x * 0.8, ln / 2, 8)], 1.6, "metal", 8)
    for y in (-ln / 2 + 30, -10, 30, ln / 2 - 20):
        tube(g, [(-w / 2, y, 6), (w / 2, y, 6)], 1.4, "metal", 8)
    box(g, (w * 0.8, ln * 0.55, 0.6), (0, -5, 5), "plastic", 0.2)
    for s in (-1, 1):
        slab(g, lambda a, b, s=s: (s * (w / 2 + 14 + 2 * math.sin(math.pi * b)), -20 + a * 60, 6 + b * 18), 8, 4, 1.0, "paint")
    out.append((g, "Body_Frame", (0, 0, 0), {"slot": "frame"}))
    return out


def car(p):
    u = float(p.get("unit", 0.01))
    _STATE["paint"] = p.get("paint")
    B = Body(p)
    style, prefix = B.style, p.get("name") or "Car"
    parts = dict(p.get("parts") or {})
    made, info = [], {}

    def add(g, name, at, meta):
        ob = _emit(g, "%s_%s" % (prefix, name), u, at)
        info[ob.name] = dict(meta, tris=_tris(ob))
        made.append(ob)

    def part(kind, slot, at, side="left", bone="root", **extra):
        spec = {"kind": kind, "style": parts.get(kind + "_style", style), "side": side, "tire": B.tire,
                "name": "%s_%s" % (prefix, slot), "at": at}
        spec.update(extra)
        given = parts.get(kind)
        if given is False:
            return
        if isinstance(given, dict):
            spec.update(given)
        elif isinstance(given, (str, int)) and kind in ("rim", "wheel"):
            spec["spokes"] = given
        elif isinstance(given, str):
            spec["style"] = given
        ob, meta = build_part(spec, u)
        params = {k: v for k, v in spec.items() if k not in ("name", "at", "side", "kind")}
        info[ob.name] = dict(meta, slot=slot, side=side, bone=bone, params=params)
        made.append(ob)

    if parts.get("rim") and "wheel" not in parts:
        parts["wheel"] = {"spokes": parts["rim"]} if not isinstance(parts["rim"], dict) else parts["rim"]
    tx = B.track / 2
    for tag, yh, x in (("FL", B.yf, tx), ("FR", B.yf, -tx), ("RL", B.yr, tx), ("RR", B.yr, -tx)):
        side = "left" if x > 0 else "right"
        # 48 blocks, no sidewall text: a limo stays under hero 100k, and glyph slivers would read as qa z-fight pairs
        part("wheel", "wheel_" + tag, (x, yh, B.R), side, tag + "_wheel", tread=48, label="")
        if B.kind != "kart":
            part("caliper", "caliper_" + tag, (x, yh, B.R), side)
    bones = [{"name": "root", "parent": None, "head": [0, 0, 0], "tail": [0, 0, 30]}]
    for tag, yh, x in (("FL", B.yf, tx), ("FR", B.yf, -tx), ("RL", B.yr, tx), ("RR", B.yr, -tx)):
        bones.append({"name": tag + "_wheel", "parent": "root", "head": [x, yh, B.R], "tail": [x + (15 if x > 0 else -15), yh, B.R]})
    if B.kind == "kart":
        for g, name, at, meta in _kart(B, u, style, parts):
            add(g, name, at, dict(meta, kind="body", bone="root", socket="Mount"))
        part("seat", "seat", (0, 15, 6), style="sport")
        part("steering_wheel", "steering_wheel", (0, -40, 38), width=28)
        part("engine", "engine", (25, 52, 6), style="sedan")
        part("bumper_front", "bumper_front", (0, -B.L / 2 + 10, 4), width=90, length=10, height=14)
        part("bumper_rear", "bumper_rear", (0, B.L / 2 - 10, 4), width=110, length=10, height=12)
        return {"parts": info, "bones": bones, "dims": [B.L, B.W, B.H], "R": B.R, "style": style}
    panels, cuts, names = _body(B, u)
    a0 = B.gh[0]
    for name, g in panels.items():
        at, bone = (0, 0, 0), "root"
        if name.startswith("Door_"):
            k = names.index(name[5])
            x = B.W / 2 if name.endswith("L") else -B.W / 2
            at, bone = _pivot(g, (x, B.y(cuts[k]), (B.clr + B.zs) / 2)), "door_" + name[5] + name[-1]
        elif name == "Hood":
            at, bone = _pivot(g, (0, B.y(a0), B.zs)), "hood"
        elif name == "Trunk":
            at, bone = _pivot(g, (0, B.y(B.gh[3]), B.top(B.gh[3]))), "trunk"
        elif name.startswith("Window_") and name[7] in names:
            bone = "door_" + name[7] + name[-1]
        add(g, "Body_" + name, at, {"kind": "body", "slot": "body_" + name.lower(), "bone": bone, "socket": "Hinge" if bone != "root" else "Mount"})
    for name, meta in list(info.items()):
        b = meta.get("bone", "root")
        if b not in [x["name"] for x in bones]:
            ob = bpy.data.objects[name]
            head = list(ob.location / u)
            bones.append({"name": b, "parent": "root", "head": head, "tail": [head[0], head[1], head[2] + 30]})
    for tag, yh, xs in (("FL", B.yf, 1), ("FR", B.yf, -1), ("RL", B.yr, 1), ("RR", B.yr, -1)):
        add(_well(B, xs, yh), "Well_" + tag, (0, 0, 0), {"kind": "body", "slot": "well_" + tag.lower(), "bone": "root", "socket": "Mount"})
    zt0, zs = B.top(0.0), B.zs
    yfr, yre = B.y0, B.y1
    lw, lh, ld = LIGHT[style]
    lw = min(lw, B.W * 0.24)
    hx = B.W / 2 * 0.8 - lw / 2 - 2
    hz = min(zt0 - lh / 2 - 3, zs - lh / 2)
    bh_f = max(18.0, (hz - lh / 2) - B.clr - 4)
    bh_r = max(18.0, B.zs * 0.45)
    bf, br = (0, yfr + 3, B.clr + 6), (0, yre - 3, B.clr + 10)
    part("bumper_front", "bumper_front", bf, width=B.W * 0.82, height=bh_f)
    part("bumper_rear", "bumper_rear", br, width=B.W * 0.82, height=bh_r)
    part("headlight", "headlight_L", (hx, yfr + ld - 1.5, hz), "left", width=lw)
    part("headlight", "headlight_R", (-hx, yfr + ld - 1.5, hz), "right", width=lw)
    gw = GRILLE[style][0]
    part("grille", "grille", (0, yfr + 3, hz), width=max(20.0, min(gw, 2 * (hx - lw / 2) - 8)))
    tw_, th_, td_ = {"classic": (14, 14, 10), "scifi": (80, 5, 10)}.get(style, (45, 14, 12))
    tw_ = min(tw_, B.W * 0.22)
    tz = min(B.top(1.0) - th_ / 2 - 4, br[2] + bh_r + th_ / 2 + 2)
    tx = B.W / 2 * 0.8 - tw_ / 2 - 2
    part("taillight", "taillight_L", (tx, yre - td_ + 1.5, tz), "left", width=tw_)
    part("taillight", "taillight_R", (-tx, yre - td_ + 1.5, tz), "right", width=tw_)
    part("license_plate", "plate_front", (0, bf[1] - BUMPER[style][0] - 0.1, bf[2] + bh_f * 0.5))
    part("license_plate", "plate_rear", (0, br[1] + BUMPER[style][0] + 0.1, br[2] + bh_r * 0.5), rear=True)
    ym = B.y(B.gh[0] + (B.gh[1] - B.gh[0]) * 0.25)
    xm = B.W / 2 - 6
    part("mirror", "mirror_L", (xm, ym, B.zs + 4), "left")
    part("mirror", "mirror_R", (-xm, ym, B.zs + 4), "right")
    for tag, x in (("R", -B.W * 0.27), ("L", 0.0)):
        part("wiper", "wiper_" + tag, (x, B.y(B.gh[0]) - 3, B.zs + 1), length=55)
    part("exhaust", "exhaust", (B.W * 0.28, yre - 40, B.clr + 2))
    if style in ("sport", "scifi") or parts.get("spoiler"):
        part("spoiler", "spoiler", (0, B.y(0.97), B.top(0.97) + 0.5), width=B.W * 0.75)
    if style in ("sport",) or parts.get("diffuser"):
        part("diffuser", "diffuser", (0, yre - 40, B.clr + 1), width=B.W * 0.6)
    part("engine", "engine", (0, B.yf + 10, B.clr + 6))
    if p.get("interior", True):
        yd = B.y(B.gh[0]) + 12
        part("dashboard", "dashboard", (0, yd, B.zs - 40), width=B.W - 40)
        xd = (B.W - 40) * 0.25
        yseat = yd + 75
        part("steering_wheel", "steering_wheel", (xd, yd + 50, B.zs - 6))
        for s in (-1, 1):
            part("seat", "seat_0" + ("L" if s > 0 else "R"), (s * xd, yseat, B.clr + 8))
        if B.kind == "limo":
            part("seat", "seat_bench", (0, B.y(cuts[-2]) + 35, B.clr + 8), style="classic", width=B.W - 50)
        elif B.doors >= 2:
            for s in (-1, 1):
                part("seat", "seat_1" + ("L" if s > 0 else "R"), (s * xd, yseat + 85, B.clr + 8))
    return {"parts": info, "bones": bones, "dims": [B.L, B.W, B.H], "R": B.R, "style": style}


def _run(fn, p):
    bridge.load(p["scene"])
    out = fn({**p, "unit": float(p["scene"].get("unit", 0.01))})
    out["scene"] = bridge.dump(p["scene"])
    return out


def op_car_part(p):
    return _run(car_part, p)


def op_car(p):
    return _run(car, p)
