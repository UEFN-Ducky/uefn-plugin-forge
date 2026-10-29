"""bpy weapon builder for Forge HQ: guns and melee weapons from part generators.

Parts are laid out in nominal cm with the grip at the origin, the muzzle / blade tip along +X and Z up,
chamfered, cut with the mesh ops' boolean and hardsurface kits, joined into one SM_ mesh, scaled to the
asked length, then given weighted normals (the hard-surface midpoly look) and unwrapped.
Sockets come back in Forge cm; the hq side stores them on the node.
"""
import hashlib
import json
import math
import random

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from . import bridge
from .mesh import _ctx, _object_from, _shape, _tris, boolean, hardsurface, manifold
from .uv import _edit, _pack, _unwrap

# slot: (realistic rgba, stylized rgba, metallic, roughness)
_MATS = {"metal": ((0.045, 0.047, 0.05, 1), (0.42, 0.45, 0.5, 1), 1.0, 0.38),
         "steel": ((0.72, 0.72, 0.7, 1), (0.82, 0.84, 0.88, 1), 1.0, 0.42),  # polished blade: reads light, not gunmetal
         "polymer": ((0.035, 0.035, 0.032, 1), (0.16, 0.17, 0.2, 1), 0.0, 0.62),
         "wood": ((0.28, 0.13, 0.05, 1), (0.55, 0.28, 0.1, 1), 0.0, 0.55),
         "grip": ((0.02, 0.02, 0.02, 1), (0.08, 0.06, 0.05, 1), 0.0, 0.88),
         "glow": ((0.05, 0.6, 1.0, 1), (0.1, 0.9, 1.0, 1), 0.0, 0.3)}

# Long guns, nominal cm. rec (x0, x1, z0, z1, width), split = lower/upper z, axis = bore z, bar (end x, r[, muzzle r]),
# hg (x0, x1, r, sides, dz), mag (x, length, width, height, tilt deg), butt x, grip tilt deg, material per role.
_LONG = {
    "rifle": dict(rec=(-6, 20, -2.0, 10.4, 5.2), split=4.0, axis=7.2, bar=(58, 0.95), hg=(19.5, 46, 3.2, 8, 0),
                  mag=(12, 6.2, 2.4, 14, -12), butt=-33, grip=18, stock="ar", sight="iron", muzzle="flash", magkind="std",
                  rail=True, furn="polymer", gripm="grip", pad="grip", hgm="metal", magm="polymer"),
    "smg": dict(rec=(-6, 20, -1.5, 8.6, 4.8), split=3.4, axis=5.8, bar=(34, 0.75), hg=(16, 30, 2.3, 12, 0),
                mag=(12.5, 4.2, 2.2, 19, -14), butt=-30, grip=16, stock="skeleton", sight="red_dot", muzzle="none",
                magkind="std", rail=True, furn="polymer", gripm="polymer", pad="polymer", hgm="polymer", magm="metal"),
    "shotgun": dict(rec=(-5, 19, -1.5, 7.8, 5.4), split=None, axis=5.6, bar=(78, 1.2), hg=(30, 52, 2.6, 16, -1.3),
                    mag=(10, 6, 2.6, 8, 0), butt=-36, grip=35, stock="wood", sight="bead", muzzle="none", magkind="tube",
                    rail=False, furn="wood", gripm="wood", pad="grip", hgm="wood", magm="metal"),
    "sniper": dict(rec=(-4, 18, -0.5, 7.0, 4.6), split=None, axis=4.6, bar=(86, 1.15, 0.85), hg=(12, 50, 0, 0, 0),
                   mag=(9, 7.5, 3.0, 4.5, 0), butt=-35, grip=14, stock="precision", sight="scope", muzzle="brake",
                   magkind="std", rail=False, furn="polymer", gripm="polymer", pad="grip", hgm="polymer", magm="metal"),
    "scifi_rifle": dict(rec=(-8, 22, -2.5, 11.5, 6.4), split=4.0, axis=7.5, bar=(50, 1.4), hg=(21.5, 48, 4.2, 6, 0),
                        mag=(10, 7, 3.4, 9, -4), butt=-30, grip=14, stock="block", sight="holo", muzzle="emitter",
                        magkind="std", rail=True, furn="polymer", gripm="polymer", pad="polymer", hgm="polymer", magm="glow"),
}


def _R(rot):
    return Euler([math.radians(a) for a in rot]).to_matrix()


def _material(slot, style):
    name = "Weapon_%s_%s" % (slot.capitalize(), style.capitalize())
    real, toon, metal, rough = _MATS[slot]
    color = list(real if style == "realistic" else toon)
    glow = slot == "glow"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat["forge"] = json.dumps({"id": "m" + hashlib.sha1(name.encode()).hexdigest()[:6], "name": name, "base_color": color,
                               "metallic": metal, "roughness": rough, "emissive": color[:3] if glow else [0, 0, 0],
                               "emissive_strength": 8.0 if glow else 0.0, "opacity": 1.0, "textures": {}})
    return mat


class _Kit:
    """Parts in nominal cm, one bpy object (one material) each, plus named sockets.

    Each primitive is bevelled as it is made, before any cut: the chamfer stays clean and the
    boolean / hardsurface details cut crisp into it (bevelling after the cuts leaves slivers).
    """

    def __init__(self, style):
        self.style, self.parts, self.sockets, self.mats = style, {}, {}, {}
        self.bevel, self.segments = (0.4, 3) if style == "stylized" else (0.18, 2)

    def _new(self, name, mat, build, bevel=True):
        bm = bmesh.new()
        build(bm)
        ob = _object_from(bm, "wp_" + name)
        if mat not in self.mats:
            self.mats[mat] = _material(mat, self.style)
        ob.data.materials.append(self.mats[mat])
        self.parts[name] = ob
        w = min(self.bevel, 0.12 * min(ob.dimensions))  # thin parts get a thinner chamfer, tiny ones none
        if bevel and w >= 0.08:
            m = ob.modifiers.new("wp_bevel", "BEVEL")
            m.width, m.segments, m.limit_method = w, self.segments if w >= 0.15 else 1, "ANGLE"
            m.angle_limit, m.use_clamp_overlap = math.radians(30), True
            with _ctx(ob):
                bpy.ops.object.modifier_apply(modifier=m.name)
        return ob

    def box(self, name, dims, at, mat, rot=(0, 0, 0)):
        m = Matrix.Translation(Vector(at)) @ _R(rot).to_4x4()
        return self._new(name, mat, lambda bm: _shape(bm, "box", dims, m))

    def cyl(self, name, a, b, r, mat, seg=16, r2=None, roll=0.0):
        """Cylinder (or cone when r2) from point a to point b; roll turns its sides about the axis."""
        a, b = Vector(a), Vector(b)
        d = b - a
        m = (Matrix.Translation((a + b) / 2) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4()
             @ Matrix.Rotation(math.radians(roll), 4, "Z"))
        return self._new(name, mat, lambda bm: bmesh.ops.create_cone(
            bm, cap_ends=True, segments=seg, radius1=r, radius2=r if r2 is None else r2, depth=d.length, matrix=m))

    def prism(self, name, pts, width, mat, sharp=()):
        """XZ outline extruded across Y; verts in sharp meet in one line at y=0 (a ground blade edge, kept crisp)."""
        def build(bm):
            front = [bm.verts.new((x, -width / 2 * (i not in sharp), z)) for i, (x, z) in enumerate(pts)]
            back = [v if i in sharp else bm.verts.new((v.co.x, width / 2, v.co.z)) for i, v in enumerate(front)]
            bm.faces.new(front)
            bm.faces.new(back[::-1])
            n = len(pts)
            for i in range(n):
                quad = [front[i], back[i], back[(i + 1) % n], front[(i + 1) % n]]
                ring = [v for k, v in enumerate(quad) if v is not quad[k - 1]]
                if len(set(ring)) >= 3:
                    bm.faces.new(ring)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        return self._new(name, mat, build, bevel=not sharp)

    def cut(self, name, specs):
        """Exact boolean of [(shape, size, at, rot)] out of one part, as one joined cutter."""
        bm = bmesh.new()
        for shape, size, at, rot in specs:
            _shape(bm, shape, size, Matrix.Translation(Vector(at)) @ _R(rot).to_4x4(), 12)
        tool = _object_from(bm, "wp_cut")
        try:
            boolean(self.parts[name], {"cutters": ["c"], "unit": 1.0}, {"c": tool})
        finally:
            bpy.data.objects.remove(tool, do_unlink=True)

    def kit(self, name, preset, at, size, depth, count):
        hardsurface(self.parts[name], {"preset": preset, "at": list(at), "size": size, "depth": depth, "count": count,
                                       "unit": 1.0})

    def join(self, name):
        objs = list(self.parts.values())
        main = objs[0]
        with bpy.context.temp_override(active_object=main, object=main, selected_objects=objs,
                                       selected_editable_objects=objs):
            bpy.ops.object.join()
        main.name = main.data.name = name
        return main


# ---- shared gun parts --------------------------------------------------------------------------


def _sight(k, kind, sx, top, sc=1.0, front=None, ftop=None):
    """Sight on a rail / slide top at sx (iron: front post at front, standing on ftop); returns the Sight socket."""
    if kind == "iron":
        k.box("rear_sight", (2.4 * sc, 2.2 * sc, 2.0 * sc), (sx, 0, top + 0.9 * sc), "metal")
        if front is not None:
            k.box("front_sight", (1.6 * sc, 2.0 * sc, 2.4 * sc), (front, 0, (top if ftop is None else ftop) + 1.1 * sc), "metal")
        return (sx, 0, top + 1.9 * sc)
    if kind == "red_dot":
        k.box("optic_mount", (4.4 * sc, 2.6 * sc, 1.4 * sc), (sx, 0, top + 0.6 * sc), "metal")
        zc = top + 3.0 * sc
        k.cyl("optic", (sx - 2.8 * sc, 0, zc), (sx + 2.8 * sc, 0, zc), 1.9 * sc, "metal")
        k.cut("optic", [("cylinder", (3.0 * sc, 3.0 * sc, 1.2 * sc), (sx + 2.8 * sc, 0, zc), (0, 90, 0))])
        return (sx, 0, zc)
    if kind == "holo":
        k.box("optic_mount", (6.0 * sc, 3.2 * sc, 1.4 * sc), (sx, 0, top + 0.6 * sc), "metal")
        k.box("optic", (6.0 * sc, 3.4 * sc, 4.2 * sc), (sx, 0, top + 3.0 * sc), "metal")
        k.cut("optic", [("box", (8.0 * sc, 2.6 * sc, 2.8 * sc), (sx, 0, top + 3.2 * sc), (0, 0, 0))])
        return (sx, 0, top + 3.2 * sc)
    if kind == "scope":
        zc = top + 3.2
        for i, dx in enumerate((-5, 5)):
            k.box("scope_ring%d" % i, (1.4, 3.2, 3.4), (sx + dx, 0, top + 1.6), "metal")
        k.cyl("scope", (sx - 10, 0, zc), (sx + 10, 0, zc), 1.3, "metal")
        # bells start inside the tube and end inside the lens housing: equal radii would put their cap rings
        # on the neighbour within qa's 0.05 cm weld and read as non-manifold
        k.cyl("scope_bell", (sx + 9.5, 0, zc), (sx + 17.6, 0, zc), 1.15, "metal", r2=2.15)
        k.cyl("scope_objective", (sx + 16.5, 0, zc), (sx + 20, 0, zc), 2.5, "metal")
        k.cyl("scope_eye_bell", (sx - 9.5, 0, zc), (sx - 15.6, 0, zc), 1.15, "metal", r2=1.65)
        k.cyl("scope_ocular", (sx - 14.5, 0, zc), (sx - 19, 0, zc), 2.0, "metal")
        k.cyl("turret_up", (sx, 0, zc), (sx, 0, zc + 2.6), 1.0, "metal", seg=12)
        k.cyl("turret_side", (sx, 0, zc), (sx, -2.6, zc), 1.0, "metal", seg=12)
        k.cut("scope_objective", [("cylinder", (4.4, 4.4, 1.0), (sx + 20, 0, zc), (0, 90, 0))])
        return (sx - 19, 0, zc)
    return (sx, 0, top + 1.0)


def _muzzle(k, kind, bx, br, ax):
    """Muzzle device on the barrel end at bx; returns the Muzzle socket (the very front)."""
    if kind == "flash":
        k.cyl("muzzle", (bx - 1, 0, ax), (bx + 5.5, 0, ax), br * 1.25, "metal", seg=12)
        k.cut("muzzle", [("box", (3.0, 0.5, 4 * br), (bx + 4.2, 0, ax), (a, 0, 0)) for a in (0, 60, 120)])
        front = bx + 5.5
    elif kind == "brake":
        k.box("muzzle", (6.5, br * 3.2, br * 2.6), (bx + 2.5, 0, ax), "metal")
        k.cut("muzzle", [("cylinder", (1.2, 1.2, 6.0), (bx + 1.2 + i * 1.7, 0, ax), (90, 0, 0)) for i in range(3)])
        front = bx + 5.75
    elif kind == "suppressor":
        r = max(2.0, br * 2.2)
        k.cyl("muzzle", (bx - 2, 0, ax), (bx + 15 * r / 2.0, 0, ax), r, "metal", seg=20)
        front = bx + 15 * r / 2.0
    elif kind == "emitter":
        k.cyl("muzzle", (bx - 1, 0, ax), (bx + 3, 0, ax), br * 1.6, "metal", seg=12)
        k.cyl("emitter", (bx + 2.6, 0, ax), (bx + 3.4, 0, ax), br * 1.25, "glow", seg=12)
        front = bx + 3.4
    else:
        front = bx
    return (front, 0, ax)


def _long(k, kind, parts, j):
    n = _LONG[kind]
    x0, x1, z0, z1, w = n["rec"]
    ax, sci = n["axis"], kind.startswith("scifi")
    stock, sight, muzzle = parts.get("stock", n["stock"]), parts.get("sight", n["sight"]), parts.get("muzzle", n["muzzle"])
    mag, rail = parts.get("mag", n["magkind"]), parts.get("rail", n["rail"])
    if n["split"]:  # AR-style lower + upper receiver
        s = n["split"]
        k.box("upper", (x1 - x0, w * 0.94, z1 - s + 0.2), ((x0 + x1) / 2, 0, (s - 0.2 + z1) / 2), "metal")
        k.box("lower", (x1 - x0 - 4, w, s - z0 + 0.2), ((x0 + x1 - 4) / 2, 0, (z0 + s + 0.2) / 2), "metal")
        upper = "upper"
    else:
        k.box("receiver", (x1 - x0, w, z1 - z0), ((x0 + x1) / 2, 0, (z0 + z1) / 2), "metal")
        upper = "receiver"
    if sci:
        k.box("glow_receiver", (x1 - x0 - 6, w + 0.3, 0.5), ((x0 + x1) / 2, 0, ax + 1.6), "glow")
    else:  # ejection port on the right (-Y) side
        k.cut(upper, [("box", (6.0, 2.0, 2.2), (x0 + (x1 - x0) * 0.45, -w / 2, ax + 0.5), (0, 0, 0))])
    if kind in ("rifle", "scifi_rifle"):
        k.box("charging_handle", (2.2, w * 0.9, 0.9), (x0 - 0.6, 0, z1 - 0.6), "metal")
    # pistol grip, trigger guard, trigger
    k.box("grip", (3.2, 3.0, 11.0), (-1.2, 0, -4.6), n["gripm"], rot=(0, j(n["grip"], 0.1), 0))
    zg = z0 - 2.8
    k.box("guard", (8.6, 1.2, 0.7), (4.6, 0, zg), "metal")
    k.box("guard_post", (0.7, 1.2, 3.2), (8.6, 0, zg + 1.4), "metal")
    k.box("trigger", (0.5, 0.6, 2.4), (4.0, 0, z0 - 1.1), "metal", rot=(0, -12, 0))
    # magazine
    bx, br = n["bar"][:2]
    if mag == "tube":
        tz = ax - 2.5
        k.cyl("mag_tube", (x1 - 1, 0, tz), (bx - 6, 0, tz), 1.1, "metal")
        k.sockets["Magazine"] = (x1 - 6, 0, z0)  # loading port under the receiver
    elif mag != "none":
        mx, ml, mw, mh, mr = n["mag"]
        mh = mh * 1.5 if mag == "extended" else 5.0 if mag == "drum" else mh
        rot = (0, j(mr, 0.15), 0)
        top = Vector((mx, 0, z0 + 1.0))
        k.box("mag", (ml, mw, mh), top + _R(rot) @ Vector((0, 0, -mh / 2)), n["magm"], rot)
        low = top + _R(rot) @ Vector((0, 0, -mh))
        if mag == "drum":
            k.cyl("drum", (low.x, -mw * 1.3, low.z - 4.0), (low.x, mw * 1.3, low.z - 4.0), 6.0, n["magm"], seg=20)
        else:
            k.box("mag_base", (ml + 0.6, mw + 0.5, 0.9), low, n["magm"], rot)
        k.box("magwell", (ml + 1.4, w + 0.3, 3.2), (mx, 0, z0 - 0.8), "metal")
        k.sockets["Magazine"] = (mx, 0, z0)
    # barrel and handguard
    k.cyl("barrel", (x1 - 1, 0, ax), (bx, 0, ax), br, "metal", r2=n["bar"][2] if len(n["bar"]) > 2 else None)
    hx0, hx1, hr, sides, dz = n["hg"]
    hx1 = hx0 + j(hx1 - hx0, 0.08)
    mid = (hx0 + hx1) / 2
    if kind == "sniper":  # the stock's forend runs under the free-floated barrel
        k.box("forend", (hx1 - hx0, w - 0.2, 4.6), (mid, 0, ax - 2.6), n["furn"])
    else:
        k.cyl("handguard", (hx0, 0, ax + dz), (hx1, 0, ax + dz), hr, n["hgm"], seg=sides, roll=180.0 / sides)
        if kind in ("rifle", "scifi_rifle"):  # M-LOK slots / cooling vents on both sides
            for side in (-1, 1):
                k.kit("handguard", "vent", (mid, side * hr, ax), (hx1 - hx0) * 0.45, 0.35, 4)
        if kind == "shotgun":  # pump grip ribs
            for i in range(5):
                x = hx0 + 3 + i * (hx1 - hx0 - 6) / 4
                k.cyl("rib%d" % i, (x - 0.3, 0, ax + dz), (x + 0.3, 0, ax + dz), hr + 0.2, n["hgm"], seg=sides)
        if sci:
            k.box("glow_handguard", (hx1 - hx0 - 4, 2 * hr + 0.3, 0.5), (mid, 0, ax), "glow")
    # rail
    top = z1
    if rail:
        rx0, rx1 = x0 + 1, (hx1 - 1) if kind in ("rifle", "scifi_rifle") else x1 - 1
        rz = z1 + 0.2
        k.box("rail", (rx1 - rx0, 2.2, 1.4), ((rx0 + rx1) / 2, 0, rz), "metal")
        slots = int((rx1 - rx0 - 1.0) / 1.6)
        k.cut("rail", [("box", (0.55, 3.0, 0.9), (rx0 + 1.0 + i * 1.6, 0, rz + 0.7), (0, 0, 0)) for i in range(slots)])
        top, front, ftop = rz + 0.7, rx1 - 1.2, rz + 0.7
    else:
        front, ftop = bx - 3, ax + br - 0.3
    if sight == "bead":
        k.cyl("bead", (bx - 1.5, 0, ax + br - 0.1), (bx - 1.5, 0, ax + br + 0.5), 0.25, "metal", seg=8)
        k.sockets["Sight"] = (x0 + 2, 0, z1 + 0.5)
    else:
        sx = (x0 + x1) / 2 if sight == "scope" else x0 + (3 if sight == "iron" else 5)
        k.sockets["Sight"] = _sight(k, sight, sx, top, front=front, ftop=ftop)
    k.sockets["Muzzle"] = _muzzle(k, muzzle, bx, n["bar"][2] if len(n["bar"]) > 2 else br, ax)
    # stock
    sb = j(n["butt"], 0.06)
    if stock == "ar":
        k.cyl("buffer_tube", (x0 + 0.5, 0, ax), (x0 - 17, 0, ax), 1.5, "metal")
        k.prism("stock", [(sb, ax + 4.8), (sb + 15, ax + 4.0), (sb + 15, ax - 2.6), (sb, ax - 8.8)], 4.6, n["furn"])
        k.box("butt_pad", (1.2, 4.9, 13.8), (sb - 0.4, 0, ax - 2.0), n["pad"])
    elif stock == "skeleton":
        for side in (-1, 1):
            k.cyl("stock_rod%d" % (side + 1), (x0 + 0.5, side * 1.7, ax + 1), (sb + 0.5, side * 1.7, ax + 1), 0.45, "metal", seg=8)
        k.box("butt_pad", (1.2, 5.0, 9.5), (sb, 0, ax - 2.5), n["pad"])
    elif stock == "wood":
        k.prism("stock", [(x0 + 0.5, z1 - 0.4), (sb, ax + 0.2), (sb, ax - 14), (x0 - 10, z0 - 4.5), (x0 + 0.5, z0 + 0.2)],
                4.2, n["furn"])
        k.box("butt_pad", (1.4, 4.4, 14.6), (sb - 0.5, 0, ax - 6.9), n["pad"])
    elif stock == "precision":
        k.prism("stock", [(x0 + 0.5, z1 - 1.2), (sb + 8, ax + 3.4), (sb, ax + 3.6), (sb, ax - 11.5), (sb + 12, ax - 9.5),
                          (x0 - 6, z0 - 4.8), (x0 + 1, z0 - 0.2)], 4.4, n["furn"])
        k.box("cheek_rest", (14, 3.6, 1.4), (sb + 9, 0, ax + 3.8), n["furn"])
        k.box("butt_pad", (1.4, 4.6, 15.6), (sb - 0.5, 0, ax - 3.95), n["pad"])
    elif stock == "block":
        k.prism("stock", [(x0 + 0.5, z1 - 0.5), (sb, z1 - 1.5), (sb, z0 - 6), (sb + 6, z0 - 7), (x0 - 4, z0 - 3.5),
                          (x0 + 0.5, z0 + 0.5)], 5.4, n["furn"])
        k.box("glow_stock", (x0 - sb - 6, 5.7, 0.5), ((sb + x0) / 2, 0, (z1 + z0) / 2), "glow")
    else:
        k.box("end_cap", (1.0, w, z1 - z0), (x0 - 0.4, 0, (z0 + z1) / 2), "metal")
    if kind == "sniper":  # bolt handle on the right
        k.cyl("bolt", (x0 + 4, -w / 2, z1 - 2.0), (x0 + 3, -w / 2 - 3.8, z1 - 3.6), 0.38, "metal", seg=8)
        k.cyl("bolt_knob", (x0 + 3, -w / 2 - 3.4, z1 - 3.5), (x0 + 3, -w / 2 - 4.6, z1 - 3.7), 0.9, "metal", seg=12)
    k.sockets["Grip"] = (0, 0, 0)


def _pistol(k, kind, parts, j):
    sci = kind == "scifi_pistol"
    sl, sw, sh = (22.0, 3.4, 4.4) if sci else (20.4, 2.6, 3.4)
    sx0, sz0 = -4.2, 1.4
    front, bz = sx0 + sl, sz0 + sh * 0.45
    k.box("slide", (sl, sw, sh), (sx0 + sl / 2, 0, sz0 + sh / 2), "metal")
    k.box("frame", (sl - 2.6, sw + 0.4, 2.2), (sx0 + 0.6 + (sl - 2.6) / 2, 0, 0.5), "polymer")
    rot = (0, j(18, 0.1), 0)
    k.box("grip", (3.3, sw + 0.4, 10.5), (-1.0, 0, -4.6), "polymer", rot=rot)
    low = Vector((-1.0, 0, -4.6)) + _R(rot) @ Vector((0, 0, -5.4))
    k.box("mag_base", (3.9, sw + 0.6, 0.9), low, "polymer", rot)
    k.box("guard", (5.6, 1.0, 0.6), (3.0, 0, -2.6), "polymer")
    k.box("guard_post", (0.6, 1.0, 2.8), (5.6, 0, -1.4), "polymer")
    k.box("trigger", (0.5, 0.6, 1.8), (2.4, 0, -1.2), "metal", rot=(0, -10, 0))
    k.box("rail", (5.5, sw, 0.8), (front - 6.5, 0, -0.6), "polymer")
    k.cut("rail", [("box", (0.5, sw + 1, 0.5), (front - 8.5 + i * 2.0, 0, -1.0), (0, 0, 0)) for i in range(3)])
    # 9 mm bore r 0.35: clear of the barrel's 0.47 cap chamfer ring, which qa's weld would merge into it
    bore = ("cylinder", (0.7, 0.7, 3.0), (front + 0.3, 0, bz), (0, 90, 0))
    k.cyl("barrel", (front - 1.0, 0, bz), (front + 0.3, 0, bz), 0.62, "metal")
    k.cut("barrel", [bore])
    k.cut("slide", [("box", (3.6, 1.6, 1.4), (sx0 + sl * 0.5, -sw / 2 + 0.3, sz0 + sh), (0, 0, 0))]
          + [("box", (0.35, 0.5, sh * 0.8), (sx0 + 0.8 + i * 0.6, s * sw / 2, sz0 + sh / 2), (0, 0, 0))
             for i in range(6) for s in (-1, 1)])  # rear serrations
    k.box("rear_sight", (0.9, 2.0, 0.7), (sx0 + 0.8, 0, sz0 + sh + 0.25), "metal")
    k.box("front_sight", (0.6, 0.5, 0.7), (front - 0.9, 0, sz0 + sh + 0.25), "metal")
    sight = parts.get("sight", "iron")
    k.sockets["Sight"] = (_sight(k, sight, sx0 + 6, sz0 + sh, sc=0.6) if sight != "iron"
                          else (sx0 + 0.8, 0, sz0 + sh + 0.6))
    if sci:
        k.prism("top_fin", [(sx0 + 3, sz0 + sh - 0.2), (front - 6, sz0 + sh - 0.2), (front - 9, sz0 + sh + 1.2),
                            (sx0 + 5, sz0 + sh + 1.2)], 1.2, "metal")
        k.box("glow_slide", (sl - 6, sw + 0.3, 0.4), (sx0 + sl / 2, 0, sz0 + sh * 0.3), "glow")
        k.kit("slide", "grille", (sx0 + sl * 0.7, -sw / 2, sz0 + sh * 0.65), 2.4, 0.2, 3)
    muzzle = parts.get("muzzle", "emitter" if sci else "none")
    k.sockets["Muzzle"] = _muzzle(k, muzzle, front + 0.3, 0.62, bz) if muzzle != "none" else (front + 0.3, 0, bz)
    k.sockets["Grip"] = (0, 0, 0)
    k.sockets["Magazine"] = tuple(low)


# ---- melee ----------------------------------------------------------------------------------------


def _knife(k, j):
    k.box("handle", (11, 2.4, 3.0), (0, 0, 0), "grip")
    for side in (-1, 1):  # scale pins; grip texture belongs in the material at this size
        k.kit("handle", "bolt", (0, side * 1.2, 0), 6.0, 0.12, 2)
    k.box("bolster", (1.0, 2.2, 4.6), (5.9, 0, 0.2), "metal")
    k.box("pommel", (1.2, 2.6, 3.2), (-6.0, 0, 0), "metal")
    tip = j(24.6, 0.04)
    k.prism("blade", [(6.2, 1.3), (tip - 5.1, 1.3), (tip, 0.2), (tip - 2.1, -0.9), (16, -1.6), (6.2, -1.6)], 0.45, "steel",
            sharp=(2, 3, 4))
    k.sockets.update(Grip=(0, 0, 0), TrailBase=(6.2, 0, -0.2), TrailTip=(tip, 0, 0.2))


def _sword(k, j):
    k.box("tsuka", (25.3, 2.4, 3.2), (-4.35, 0, 0), "grip")
    k.box("kashira", (1.2, 2.6, 3.4), (-17.2, 0, 0), "metal")
    k.cyl("tsuba", (8.0, 0, 0), (8.8, 0, 0), 3.8, "metal")
    k.box("habaki", (2.4, 1.1, 3.4), (9.7, 0, -0.1), "metal")
    base, blade, sori, n = 9.6, j(69.0, 0.05), 1.6, 10
    zc = lambda t: sori * t * t  # noqa: E731  tip rises; the edge sits on the convex (lower) side
    spine = [(base + blade * i / n, zc(i / n) + 1.3 - 0.35 * i / n) for i in range(n)]
    tip = (base + blade, zc(1) + 0.55)
    edge = [(base + blade * 0.96, zc(0.96) - 0.6)] + [(base + blade * i / n, zc(i / n) - 1.8 + 0.45 * i / n) for i in reversed(range(n))]
    pts = spine + [tip] + edge
    k.prism("blade", pts, 0.7, "steel", sharp=range(n, len(pts) - 1))
    k.sockets.update(Grip=(0, 0, 0), TrailBase=(base + 1, 0, 0), TrailTip=(tip[0], 0, tip[1]))


def _axe(k, j):
    k.box("haft", (66, 2.4, 3.2), (17, 0, 0), "wood")
    k.box("wrap", (14, 2.8, 3.6), (-6, 0, 0), "grip")
    k.box("knob", (1.6, 3.0, 4.0), (-16.4, 0, 0), "wood")
    k.box("eye", (7.5, 4.0, 5.4), (46, 0, 0), "metal")
    drop = j(15.5, 0.08)
    k.prism("bit", [(42.8, -2.0), (49.2, -2.0), (53.5, 2.5 - drop), (52.5, 0.5 - drop), (40.5, -drop), (39.5, 2.5 - drop)],
            3.6, "metal", sharp=(2, 3, 4, 5))
    k.box("poll", (3.4, 3.6, 2.6), (46, 0, 3.6), "metal")
    k.sockets.update(Grip=(0, 0, 0), TrailBase=(46, 0, -2.7), TrailTip=(46.5, 0, 0.2 - drop))


def _hammer(k, j):
    k.box("haft", (72, 2.8, 2.8), (18, 0, 0), "wood")
    k.box("wrap", (16, 3.2, 3.2), (-8, 0, 0), "grip")
    k.box("pommel", (2.0, 3.4, 3.4), (-18.8, 0, 0), "metal")
    hz = j(6.0, 0.1)
    k.box("head", (6.5, 6.5, 2 * hz), (54, 0, 0), "metal")
    for s in (-1, 1):
        k.cyl("face%d" % (s + 1), (54, 0, s * (hz - 0.4)), (54, 0, s * (hz + 2.2)), 3.3, "metal")
    k.cyl("collar", (48.5, 0, 0), (51, 0, 0), 2.2, "metal", seg=12)
    k.kit("head", "bolt", (54, -3.25, 0), 4.0, 0.3, 4)
    k.sockets.update(Grip=(0, 0, 0), TrailBase=(54, 0, 0), TrailTip=(54, 0, -(hz + 2.2)))


_MELEE = {"knife": _knife, "sword": _sword, "axe": _axe, "hammer": _hammer}


# ---- finish -------------------------------------------------------------------------------------


def _finish(ob, scale):
    """Slivers out, to final size, smooth + weighted normals (the midpoly game look), smart UVs.

    Planar caps stay n-gons: UE triangulates on import, and fanning a 3 mm bolt hole into slivers
    reads as z-fighting and zero-area faces to qa."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.dissolve_degenerate(bm, dist=0.01, edges=bm.edges[:])
    corner = lambda vs, i: (vs[(i + 1) % len(vs)].co - vs[i].co).cross(vs[(i + 2) % len(vs)].co - vs[i].co).length  # noqa: E731
    for face in [f for f in bm.faces if len(f.verts) > 3 and corner(list(f.verts), 0) < 2e-4]:
        vs = list(face.verts)  # a boolean T-junction first: start the face on a real corner (qa reads corner 0)
        best = max(range(len(vs)), key=lambda i: corner(vs, i))
        mat = face.material_index
        bm.faces.remove(face)
        bm.faces.new(vs[best:] + vs[:best]).material_index = mat
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.transform(scale)
    ob.data.shade_smooth()
    m = ob.modifiers.new("wp_normals", "WEIGHTED_NORMAL")
    m.mode, m.keep_sharp, m.weight = "FACE_AREA", True, 50
    with _ctx(ob):
        bpy.ops.object.modifier_apply(modifier=m.name)
    ob.data.uv_layers.new(name="UVMap")
    _edit(ob)
    _unwrap("smart", 66.0)
    _pack(0.004, True)
    bpy.ops.object.mode_set(mode="OBJECT")


def weapon(p):
    kind, style, u = p["kind"], p.get("style", "realistic"), float(p.get("unit", 0.01))
    seed = int(p.get("seed", 0))
    rng = random.Random(seed)
    j = (lambda v, amt=0.06: v * (1 + rng.uniform(-amt, amt))) if seed else (lambda v, amt=0.06: v)
    k = _Kit(style)
    if kind in _LONG:
        _long(k, kind, dict(p.get("parts") or {}), j)
    elif kind in ("pistol", "scifi_pistol"):
        _pistol(k, kind, dict(p.get("parts") or {}), j)
    elif kind in _MELEE:
        _MELEE[kind](k, j)
    else:
        raise ValueError("Unknown weapon kind %r. Kinds: %s" % (kind, ", ".join(list(_LONG) + ["pistol", "scifi_pistol"] + list(_MELEE))))
    count, mats = len(k.parts), len(k.mats)
    ob = k.join(p.get("name") or "SM_Weapon_" + "".join(w.capitalize() for w in kind.split("_")))
    xs = [v.co.x for v in ob.data.vertices]
    length = float(p["length"])
    f = length / (max(xs) - min(xs))  # fit the asked overall length; the grip origin stays put
    sy, sz = (1.25, 1.2) if style == "stylized" else (1.0, 1.0)  # stylized: chunkier cross-sections
    _finish(ob, Matrix.Diagonal((f * u, f * sy * u, f * sz * u, 1.0)))
    ob.location = Vector(p.get("at") or [0, 0, 0]) * u
    sockets = {name: [round(c, 1) for c in (v[0] * f, v[1] * f * sy, v[2] * f * sz)] for name, v in sorted(k.sockets.items())}
    tris = _tris(ob)
    summary = "%s %s %g cm, %d parts, %.1fk tris, sockets %s" % (kind, style, round(length, 1), count, tris / 1000.0,
                                                                  " ".join(sockets))
    return {"summary": summary + ("" if manifold(ob) else ", open"), "tris": tris, "parts": count, "materials": mats,
            "length": round(length, 1), "name": ob.name, "sockets": sockets}


def op_weapon(p):
    bridge.load(p["scene"])
    out = weapon({**p, "unit": float(p["scene"].get("unit", 0.01))})
    out["scene"] = bridge.dump(p["scene"])
    return out
