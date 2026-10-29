"""bpy cartoon face kit for Forge HQ: eyes, brows, muzzle, nose, mouth and ears placed on any head.

Every face part is a thin patch laid on the head by raycast from the front (-Y), then lifted along the
surface normal by well under a centimetre, so parts follow the head's real shape. Positions are fractions
of the head width W. Parts live in one "<head>_face" object (own materials, shape keys blink / smile /
brow_up); ears are a second object, "<head>_ears". The mouth is also carved into the head as a soft crease.
Needs bpy; length params are Forge cm, bpy is metres.
"""
import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import bridge

# scale: eye width vs eye_size; aspect: eye height/width; sep: eye spacing in eye widths; tilt: outer brow end
# rises (rad); up: eye centre above the face centre (W); iris: iris width / eye width
STYLES = {
    "cute": dict(scale=1.0, aspect=1.15, sep=1.0, tilt=0.0, up=0.08, iris=0.78),
    "heroic": dict(scale=0.75, aspect=0.8, sep=1.3, tilt=0.35, up=0.10, iris=0.6),
    "realistic": dict(scale=0.5, aspect=0.55, sep=1.4, tilt=0.1, up=0.10, iris=0.5),
}
# muzzle: size (0 = none); cheeks: pads either side; nose: width (W); nose_v: height (W); mouth: w | line | none;
# mouth_w: half width (W); mouth_v: line height for "line"; ear: (anchor, x, width, height, tilt) in W or None
SPECIES = {
    "cat": dict(muzzle=1.0, cheeks=True, nose=0.14, nose_v=-0.105, mouth="w", mouth_w=0.13, mouth_v=0,
                ear=("top", 0.34, 0.26, 0.28, 0.35)),
    "dog": dict(muzzle=1.25, cheeks=False, nose=0.16, nose_v=-0.10, mouth="w", mouth_w=0.15, mouth_v=0,
                ear=("top", 0.36, 0.24, 0.34, 1.0)),
    "human": dict(muzzle=0, cheeks=False, nose=0.07, nose_v=-0.08, mouth="line", mouth_w=0.14, mouth_v=-0.24,
                  ear=("side", 0.5, 0.10, 0.20, 0.0)),
    "bird": dict(muzzle=0, cheeks=False, nose=0.22, nose_v=-0.05, mouth="none", mouth_w=0, mouth_v=0, ear=None),
}
# cm above the head surface, base + dome; the deepest stack (nose + mouth carve) stays under 0.5 cm
LIFT = {"outline": 0.04, "white": 0.10, "iris": 0.16, "pupil": 0.21, "glint": 0.26, "brow": 0.12, "mouth": 0.27}
CARVE_CM = 0.15
COLORS = {
    "EyeOutline": ((0.12, 0.07, 0.04), 0.6, 0), "EyeWhite": ((0.98, 0.97, 0.93), 0.15, 0),
    "EyeIris": ((0.30, 0.16, 0.07), 0.1, 0), "EyePupil": ((0.03, 0.02, 0.02), 0.05, 0),
    "EyeGlint": ((1.0, 1.0, 1.0), 0.1, 1.0), "Brow": ((0.22, 0.11, 0.05), 0.6, 0),
    "Muzzle": ((0.96, 0.88, 0.70), 0.7, 0), "Nose": ((0.07, 0.04, 0.04), 0.25, 0),
    "MouthLine": ((0.16, 0.08, 0.05), 0.6, 0), "EarInner": ((0.93, 0.58, 0.62), 0.7, 0),
}


def _mat(name):
    color, rough, emit = COLORS[name]
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    m.diffuse_color, m.roughness = (*color, 1.0), rough
    b = m.node_tree.nodes.get("Principled BSDF")
    if b is not None:
        b.inputs["Base Color"].default_value = (*color, 1.0)
        b.inputs["Roughness"].default_value = rough
        if emit:
            b.inputs["Emission Color"].default_value = (*color, 1.0)
            b.inputs["Emission Strength"].default_value = emit
    return m


class _Parts:
    """Patches in face-plane (u, v) coordinates, resolved onto the head at the end."""

    def __init__(self):
        self.uv, self.lift, self.grp, self.faces, self.fmat = [], [], [], [], []

    def add(self, grp, mat, patch):
        uv, lift, faces = patch
        base = len(self.uv)
        self.uv += uv
        self.lift += lift
        self.grp += [grp] * len(uv)
        self.faces += [[base + i for i in f] for f in faces]
        self.fmat += [mat] * len(faces)


def _disc(R, c, lift, sx=1.0, sy=1.0, rings=5, segs=32):
    """Fan + rings out to the outline R(theta). lift(t) in cm, t = 0 centre .. 1 rim."""
    uv, lf = [c], [lift(0.0)]
    for r in range(1, rings + 1):
        t = r / rings
        for k in range(segs):
            th = 2 * math.pi * k / segs
            rr = t * R(th)
            uv.append((c[0] + rr * math.cos(th) * sx, c[1] + rr * math.sin(th) * sy))
            lf.append(lift(t))
    faces = [[0, 1 + k, 1 + (k + 1) % segs] for k in range(segs)]
    for r in range(rings - 1):
        a, b = 1 + r * segs, 1 + (r + 1) * segs
        faces += [[a + k, b + k, b + (k + 1) % segs, a + (k + 1) % segs] for k in range(segs)]
    return uv, lf, faces


def _ellipse(a, b):
    return lambda th: a * b / math.hypot(b * math.cos(th), a * math.sin(th))


def _ribbon(pts, widths, lift):
    uv, faces = [], []
    n = len(pts)
    for i, (u, v) in enumerate(pts):
        (u0, v0), (u1, v1) = pts[max(i - 1, 0)], pts[min(i + 1, n - 1)]
        d = math.hypot(u1 - u0, v1 - v0) or 1.0
        nu, nv = -(v1 - v0) / d, (u1 - u0) / d
        uv += [(u + nu * widths[i] / 2, v + nv * widths[i] / 2), (u - nu * widths[i] / 2, v - nv * widths[i] / 2)]
    for i in range(n - 1):
        faces.append([2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2])
    return uv, [lift] * len(uv), faces


def _exit(c, rx, ry, th):
    """Distance from the origin along th to the rim of an ellipse centred at c that contains the origin."""
    dx, dy = math.cos(th), math.sin(th)
    A = (dx / rx) ** 2 + (dy / ry) ** 2
    B = -2 * (dx * c[0] / rx ** 2 + dy * c[1] / ry ** 2)
    C = (c[0] / rx) ** 2 + (c[1] / ry) ** 2 - 1
    return (-B + math.sqrt(max(B * B - 4 * A * C, 0))) / (2 * A)


def _muzzle_outline(m, cheeks):
    lobes = [((0, 0), 0.15 * m, 0.125 * m)]
    if cheeks:
        lobes += [((s * 0.115 * m, -0.015 * m), 0.145 * m, 0.11 * m) for s in (1, -1)]
    return lambda th: sum(_exit(c, rx, ry, th) ** 8 for c, rx, ry in lobes) ** (1 / 8)  # smooth union


def _tri(th):
    return (1 + 0.22 * math.cos(3 * (th + math.pi / 2))) / 1.22  # rounded triangle, point down


class _Head:
    """World-space head: BVH for rays, bbox, vertex data for the mouth carve."""

    def __init__(self, ob):
        me, mw = ob.data, ob.matrix_world
        self.ob = ob
        self.co = np.array([tuple(mw @ v.co) for v in me.vertices])
        self.tree = BVHTree.FromPolygons([Vector(c) for c in self.co], [tuple(p.vertices) for p in me.polygons])
        lo, hi = self.co.min(axis=0), self.co.max(axis=0)
        self.W = float(hi[0] - lo[0])
        self.cx, self.cy, self.zc = float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2), float((lo[2] + hi[2]) / 2)
        self.ymin, self.zmax = float(lo[1]), float(hi[2])
        if self.W <= 0:
            raise ValueError("Head %r has no width; pass the head mesh object" % ob.name)

    def hit(self, u, v):
        o = Vector((self.cx + u * self.W, self.ymin - self.W, self.zc + v * self.W))
        loc, nor, _, _ = self.tree.ray_cast(o, Vector((0, 1, 0)))
        if loc is None:
            raise ValueError("Face part at u=%.2f v=%.2f is off the head: lower eye_size, or the head is too narrow "
                             "or not facing -Y (rotate it so the face looks toward -Y)" % (u, v))
        return loc, (nor if nor.y < 0 else -nor)


def _eyes(P, st, eye_size, look):
    w = eye_size * st["scale"]
    a, b = w / 2, w / 2 * st["aspect"]
    xe, ve = w * st["sep"], st["up"]
    ai = a * st["iris"]
    bi = min(b * 0.92, ai * 1.12)
    for s in (1, -1):
        c = (s * xe, ve)
        P.add("eye", "EyeOutline", _disc(_ellipse(a + 0.012, b + 0.012), c, lambda t: LIFT["outline"]))
        P.add("eye", "EyeWhite", _disc(_ellipse(a, b), c, lambda t: LIFT["white"]))
        ic = (c[0] + look[0] * (a - ai) * 0.8, c[1] + look[1] * (b - bi) * 0.8)
        P.add("eye", "EyeIris", _disc(_ellipse(ai, bi), ic, lambda t: LIFT["iris"]))
        P.add("eye", "EyePupil", _disc(_ellipse(ai * 0.5, bi * 0.5), ic, lambda t: LIFT["pupil"], rings=3, segs=20))
        for dx, dy, r in ((-0.35, 0.4, 0.2), (0.3, -0.35, 0.09)):
            P.add("eye", "EyeGlint", _disc(_ellipse(ai * r, ai * r), (ic[0] + dx * ai, ic[1] + dy * bi),
                                           lambda t: LIFT["glint"], rings=2, segs=12))
        length = w * 1.15
        vb = ve + b + 0.045
        pts, wid = [], []
        for j in range(11):
            t = j / 5 - 1
            pts.append((c[0] + t * length / 2, vb + 0.03 * (1 - t * t) + st["tilt"] * 0.5 * length / 2 * t * s))
            wid.append(0.034 * (0.35 + 0.65 * math.sin(math.pi * j / 10)))
        P.add("brow", "Brow", _ribbon(pts, wid, LIFT["brow"]))
    return ve


def _nose_mouth(P, sp):
    m = sp["muzzle"]
    if m:
        P.add("muzzle", "Muzzle", _disc(_muzzle_outline(m, sp["cheeks"]), (0, -0.2 * m + (0 if m == 1 else -0.02)),
                                        lambda t: 0.04 + 0.18 * (1 - t * t), rings=6, segs=48))
    nw, nv = sp["nose"], sp["nose_v"]
    P.add("nose", "Nose", _disc(_tri, (0, nv), lambda t: 0.15 + 0.13 * (1 - t * t), nw / 2, nw / 2 * 0.72))
    lines = []
    if sp["mouth"] == "w":
        m0 = nv - 0.36 * nw
        m1 = m0 - 0.04
        lines.append([(0, m0 + 0.01 - 0.04 * k / 4) for k in range(5)])
        for s in (1, -1):
            lines.append([(s * sp["mouth_w"] * t, m1 - 0.045 * math.sin(math.pi * t) + 0.025 * t) for t in (k / 8 for k in range(9))])
    elif sp["mouth"] == "line":
        w = sp["mouth_w"]
        lines.append([(w * t, sp["mouth_v"] + 0.03 * t * t) for t in (k / 5 - 1 for k in range(11))])
    for pts in lines:
        P.add("mouth", "MouthLine", _ribbon(pts, [0.014] * len(pts), LIFT["mouth"]))
    return lines


def _carve(head, lines, unit):
    """Press a soft crease into the head under the mouth lines (head vertices move inward along their normals)."""
    if not lines:
        return
    ob = head.ob
    me = ob.data
    nor = np.empty(len(me.vertices) * 3, np.float32)
    me.vertex_normals.foreach_get("vector", nor)
    nor = (np.array(ob.matrix_world.to_3x3()) @ nor.reshape(-1, 3).T).T
    nor /= np.maximum(np.linalg.norm(nor, axis=1, keepdims=True), 1e-9)
    samples = []
    for pts in lines:
        for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
            samples += [(u0 + (u1 - u0) * k / 4, v0 + (v1 - v0) * k / 4) for k in range(4)]
    s = np.array(samples)
    uv = np.stack([(head.co[:, 0] - head.cx) / head.W, (head.co[:, 2] - head.zc) / head.W], axis=1)
    d = np.linalg.norm(uv[:, None, :] - s[None, :, :], axis=2).min(axis=1)
    r = 0.024
    k = np.where((d < r) & (nor[:, 1] < -0.3), (1 - d / r) ** 2, 0.0)
    if not k.any():
        return
    world = head.co - nor * (k * CARVE_CM * unit)[:, None]
    inv = np.array(ob.matrix_world.inverted())
    local = (inv[:3, :3] @ world.T).T + inv[:3, 3]
    me.vertices.foreach_set("co", local.astype(np.float32).ravel())
    me.update()


def _face_object(head, P, unit, ve, st, sp, eye_size):
    co = np.array([tuple((lambda h: h[0] + h[1] * l * unit)(head.hit(u, v))) for (u, v), l in zip(P.uv, P.lift)])
    names = list(dict.fromkeys(P.fmat))
    me = bpy.data.meshes.new(head.ob.name + "_face")
    me.from_pydata([tuple(c) for c in co], [], P.faces)
    me.update()
    for n in names:
        me.materials.append(_mat(n))
    me.polygons.foreach_set("material_index", np.array([names.index(m) for m in P.fmat], np.int32))
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    ob = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(ob)
    # shape keys, deltas in metres
    grp = np.array(P.grp)
    u = np.array([p[0] for p in P.uv])
    W = head.W
    ob.shape_key_add(name="Basis", from_mix=False)
    base = co.copy()
    eye_z = head.zc + ve * W
    blink = np.zeros_like(co)
    m = grp == "eye"
    blink[m, 2] = -(co[m, 2] - eye_z) * 0.92
    smile = np.zeros_like(co)
    m = grp == "mouth"
    mw = max(sp["mouth_w"], 1e-3)
    smile[m, 2] = 0.035 * W * np.minimum(1.0, np.abs(u[m]) / mw) ** 2
    brow = np.zeros_like(co)
    brow[grp == "brow", 2] = 0.05 * W
    for name, delta in (("blink", blink), ("smile", smile), ("brow_up", brow)):
        ob.shape_key_add(name=name, from_mix=False).data.foreach_set("co", (base + delta).astype(np.float32).ravel())
    return ob


def _sphere_piece(size, tilt, base, side, anchor, tdisp, hoff):
    """Unit sphere stretched to (width, thickness, height), tilted about Y, set on `base`. -> (verts, faces)."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=1.0)
    bm.verts.index_update()
    w, th, h = size
    c, s = math.cos(side * tilt), math.sin(side * tilt)
    out = []
    for v in bm.verts:
        x, y, z = v.co
        wx, ty, hz = x * w / 2, y * th + tdisp, z * h / 2 + hoff
        p = Vector((wx, ty, hz)) if anchor == "top" else Vector((side * ty, wx, hz))
        out.append(base + Vector((p.x * c + p.z * s, p.y, -p.x * s + p.z * c)))
    faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    return out, faces


def _ears(head, sp, unit):
    if not sp["ear"]:
        return None
    anchor, fx, fw, fh, tilt = sp["ear"]
    W = head.W
    th = 0.045 * W
    outer = head.ob.data.materials[0] if len(head.ob.data.materials) and head.ob.data.materials[0] else None
    verts, faces, fmat = [], [], []
    for side in (1, -1):
        if anchor == "top":
            o, d = Vector((head.cx + side * fx * W, head.cy, head.zmax + W)), Vector((0, 0, -1))
        else:
            o, d = Vector((head.cx + side * W, head.cy, head.zc + 0.05 * W)), Vector((-side, 0, 0))
        base, _, _, _ = head.tree.ray_cast(o, d)[:4]
        if base is None:
            raise ValueError("No ear spot on the head for species ears; the head must be a closed mesh")
        front = -1 if anchor == "top" else 1
        hoff = fh * W / 4 if anchor == "top" else 0.0
        for kind, size, tdisp, mi in (("outer", (fw * W, th, fh * W), 0, 0),
                                      ("inner", (fw * W * 0.62, th * 0.5, fh * W * 0.62), front * th * 0.7, 1)):
            vs, fs = _sphere_piece(size, tilt, base, side, anchor, tdisp, hoff)
            n = len(verts)
            verts += vs
            faces += [[n + i for i in f] for f in fs]
            fmat += [mi] * len(fs)
    me = bpy.data.meshes.new(head.ob.name + "_ears")
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.update()
    me.materials.append(outer or _mat_outer())
    me.materials.append(_mat("EarInner"))
    me.polygons.foreach_set("material_index", np.array(fmat, np.int32))
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    ob = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _mat_outer():
    m = bpy.data.materials.get("EarOuter") or bpy.data.materials.new("EarOuter")
    m.diffuse_color = (0.8, 0.55, 0.25, 1.0)
    return m


def build_face(head, p):
    """Lay the face on `head` (bpy mesh object): returns (face object, ears object or None). p: hq params + unit."""
    st, sp = STYLES[p["style"]], SPECIES[p["species"]]
    unit = float(p.get("unit", 0.01))
    h = _Head(head)
    P = _Parts()
    ve = _eyes(P, st, float(p["eye_size"]), [max(-1.0, min(1.0, float(x))) for x in p.get("look", [0, 0])])
    lines = _nose_mouth(P, sp)
    face = _face_object(h, P, unit, ve, st, sp, p["eye_size"])
    ears = _ears(h, sp, unit)
    _carve(h, lines, unit)
    return face, ears


def op_cartoon_face(p):
    objs, _ = bridge.load(p["scene"])
    head = objs.get(p["head"]) or next(iter(objs.values()), None)
    if head is None or head.type != "MESH":
        raise ValueError("cartoon_face needs the head mesh object (head=<id>)")
    face, ears = build_face(head, {**p, "unit": float(p["scene"].get("unit", 0.01))})
    return {"summary": "%s %s face on %s: %d verts%s; shapes blink smile brow_up" % (
        p["style"], p["species"], head.name, len(face.data.vertices), " + ears" if ears else ""),
        "scene": bridge.dump(p["scene"])}
