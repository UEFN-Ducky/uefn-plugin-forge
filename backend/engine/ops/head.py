"""bpy realistic human head for Forge HQ (op_head).

One quad head shell with circular eye loops, a mouth loop set whose outer ring is the nasolabial loop, the
5-poles parked where those loop patches meet the skull grid, an eye-socket bag per eye and an inner mouth bag;
plus eyeballs (sclera + cornea shell, iris + pupil disc set back under the cornea for real parallax),
caruncles + tear lines, teeth + gums, a tongue and ears. Named vertex groups (eyes, lips, brows, jaw, nose,
cheeks) feed face_shapes.

Build space is head heights (chin z=0, crown z=1, mid-line x=0, face toward -Y), scaled to metres at the end.
Proportions follow Loomis / Reilly: eye line 0.5, brow 0.585, nose base 0.30, mouth 0.205, eyes one eye width
apart, ears from brow to nose base. Every feature only moves vertices along Y, so x and z come from the skull
map alone and the loops are placed exactly through its inverse.
"""
import hashlib
import json
import math

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import bridge
from .materials import build as build_material, graph

RES = 24  # skull cube-sphere cells per side
TILT = math.radians(25)  # the front cube face looks this far down, so brow to chin sits on one grid
EYE_RINGS, MOUTH_RINGS = 4, 5
SEX = {"male": dict(brow=1.4, jaw=1.06, nose=1.12, lips=0.85, chin=1.3, eye=0.97, cheek=1.0),
       "female": dict(brow=0.55, jaw=0.93, nose=0.88, lips=1.2, chin=0.8, eye=1.05, cheek=1.05),
       "neutral": dict(brow=1.0, jaw=1.0, nose=1.0, lips=1.0, chin=1.0, eye=1.0, cheek=1.0)}
AGE = {"child": dict(face=0.88, nose=0.65, brow=0.3, eye=1.12, fold=0.2, lips=1.05, ears=0.95),
       "adult": dict(face=1.0, nose=1.0, brow=1.0, eye=1.0, fold=1.0, lips=1.0, ears=1.0),
       "elder": dict(face=1.0, nose=1.12, brow=1.1, eye=0.95, fold=2.6, lips=0.7, ears=1.15)}
BUILD = {"slim": -1.0, "average": 0.0, "heavy": 1.0}
SLIDERS = ("face_width", "skull_depth", "jaw_width", "chin", "cheekbone", "brow", "nose_width", "nose_length",
           "nose_bridge", "lip_full", "mouth_width", "eye_size", "eye_tilt", "eye_spacing")
IRIS = {"brown": (0.16, 0.07, 0.025), "hazel": (0.28, 0.17, 0.05), "green": (0.12, 0.22, 0.08),
        "blue": (0.10, 0.22, 0.42), "grey": (0.30, 0.33, 0.35), "amber": (0.42, 0.22, 0.04)}
_LIGHT, _DEEP = np.array([0.68, 0.45, 0.36]), np.array([0.13, 0.065, 0.04])  # linear albedo, tone 0 .. 1


def _g(x, z, cx, cz, sx, sz):
    return np.exp(-0.5 * (((x - cx) / sx) ** 2 + ((z - cz) / sz) ** 2))


def _smooth(a, b, v):
    t = np.clip((v - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def shape(p):
    """Every coefficient the build reads, from sex / age / build and the -1..1 sliders."""
    sx, ag, b = SEX[p.get("sex", "neutral")], AGE[p.get("age", "adult")], BUILD[p.get("build", "average")]
    s = {k: float((p.get("shape") or {}).get(k, 0.0)) for k in SLIDERS}
    jaw = sx["jaw"] * (1 + 0.08 * s["jaw_width"]) * (1 + 0.04 * b)
    nose = sx["nose"] * ag["nose"]
    eye = sx["eye"] * ag["eye"] * (1 + 0.15 * s["eye_size"])
    return dict(
        width=1 + 0.06 * s["face_width"] + 0.03 * b, depth=1 + 0.08 * s["skull_depth"], taper=0.3 * (2 - jaw),
        jaw_fwd=0.13 * (1 + 0.15 * s["chin"]), f=ag["face"], brow=sx["brow"] * ag["brow"] * (1 + 0.4 * s["brow"]),
        nose=nose, nose_w=nose * (1 + 0.25 * s["nose_width"]), nose_l=nose * (1 + 0.2 * s["nose_length"]),
        bridge=1 + 0.4 * s["nose_bridge"], lips=sx["lips"] * ag["lips"] * (1 + 0.35 * s["lip_full"]),
        chin=sx["chin"] * (1 + 0.4 * s["chin"]), cheek=sx["cheek"] * (1 + 0.5 * s["cheekbone"]),
        fold=ag["fold"] * (1 + 0.3 * b), fat=b, eye=eye, R=0.05 * (ag["eye"] ** 0.5),
        tilt=0.004 + 0.006 * s["eye_tilt"], ex=0.13 * (1 + 0.08 * s["eye_spacing"]),
        mw=1 + 0.12 * s["mouth_width"], ears=ag["ears"], rot=_rot({"f": ag["face"]}))


# ---- skull map: cube point -> head point (x, z final; y before features) ---------------------------------

def _rot(k):
    """Front-face tilt: 25 deg for an adult face, more when the face sits lower on the skull (child)."""
    return np.array(Matrix.Rotation((TILT + math.radians(100) * (1 - k["f"])), 3, "X"))


def _raw(p, k):
    d = p @ k["rot"].T
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    dx, dy, dz = d.T
    front, back, low, up = (np.clip(v, 0, 1) for v in (-dy, dy, -dz, dz))
    gonial = 0.07 * np.exp(-((dz + 0.62) / 0.13) ** 2) * (1 - back)              # jaw angle
    zyg = 0.07 * np.exp(-((dz + 0.04) / 0.16) ** 2) * (1 - back)                 # zygomatic arch: widest of the face
    parietal = 0.12 * up * (1 - up) * 4 * (0.6 + 0.4 * back)                     # temples + parietal eminence
    rx = 0.31 * k["width"] * (1 - k["taper"] * low ** 3 + parietal + gonial + zyg)  # cubic taper: square jaw
    x = np.sign(dx) * np.abs(dx) ** (0.9 - 0.45 * up) * rx                     # upper skull: broad crown, not an egg
    y = np.sign(dy) * np.abs(dy) ** 0.85 * np.where(dy < 0, 0.35, 0.385 * k["depth"])
    z = np.sign(dz) * np.abs(dz) ** (0.95 + 0.6 * up) * np.where(dz > 0, 0.53, 0.47)  # flatter crown; blends: no squashed band
    y = y - k["jaw_fwd"] * low ** 1.5 * front                                   # mandible and chin come forward
    z = z + low ** 1.5 * (0.2 * _smooth(-0.55, 0.3, dy) + 0.5 * back)         # under-jaw rises to the skull base
    y = y + 0.03 * up ** 3 * front                                             # forehead slopes to the crown
    y = y - 0.07 * front * np.sin(np.pi * np.clip(up / 0.85, 0, 1))            # but stays full: upright forehead
    y = y + 0.015 * np.exp(-((dz - 0.25) / 0.3) ** 2) * back                    # occiput (flat-ish)
    return np.stack([x, y, z + 0.47], 1)


def _norm(k):
    """(z0, scale) so the skull spans z 0..1."""
    i = np.arange(20000) + 0.5
    phi, th = np.arccos(1 - 2 * i / 20000), math.pi * (1 + 5 ** 0.5) * i
    pts = _raw(np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1) @ k["rot"], k)
    lo, hi = pts[:, 2].min(), pts[:, 2].max()
    return float(lo), float(1.0 / (hi - lo))


def _skull(p, k, n):
    # each cube axis t -> t(0.5 + 0.5 t^2): the grid lines crowd 2x toward the face centre (same quads, the
    # surface is unchanged), so nose, philtrum and lips get the resolution and the back of the skull pays
    q = _raw(p * (0.5 + 0.5 * p * p), k)
    q[:, 2] -= n[0]
    return q * n[1]


def _front(uw, k, n):
    return _skull(np.column_stack([uw[:, 0], -np.ones(len(uw)), uw[:, 1]]), k, n)


def _invert(xz, k, n):
    """(u, w) on the front cube face whose skull point has these x, z (Newton)."""
    xz = np.asarray(xz, float).reshape(-1, 2)
    g = np.linspace(-0.98, 0.98, 50)
    grid = np.stack(np.meshgrid(g, g), -1).reshape(-1, 2)
    seen = _front(grid, k, n)[:, [0, 2]]
    uw = grid[np.argmin(((xz[:, None, :] - seen[None]) ** 2).sum(-1), axis=1)]  # nearest grid sample seeds Newton
    h = 1e-5
    for _ in range(20):
        f0 = _front(uw, k, n)[:, [0, 2]]
        fu = (_front(uw + [h, 0], k, n)[:, [0, 2]] - f0) / h
        fw = (_front(uw + [0, h], k, n)[:, [0, 2]] - f0) / h
        r = f0 - xz
        det = fu[:, 0] * fw[:, 1] - fw[:, 0] * fu[:, 1]
        step = np.column_stack([fw[:, 1] * r[:, 0] - fw[:, 0] * r[:, 1], -fu[:, 1] * r[:, 0] + fu[:, 0] * r[:, 1]]) / det[:, None]
        uw = np.clip(uw - step, -0.999, 0.999)
    return uw


# ---- features (Y only) -------------------------------------------------------------------------------------


def _relief(x, z, k, socket=True):
    """Y offset (+ = back) of every facial form at (x, z)."""
    ax, f = np.abs(x), k["f"]
    d = 0.035 * _g(ax, z, k["ex"], 0.5 * f, 0.055, 0.042) if socket else np.zeros_like(x)
    # brow ridge: sharp lower edge over the eye, soft slope up the forehead
    # landmarks (Farkas adult averages over head height): nasion 0.52, glabella 0.55, brow ridge 0.555
    ridge = np.where(z < 0.555 * f, 0.014, 0.03)
    d = d - 0.024 * k["brow"] * np.exp(-0.5 * (((ax - 0.115) / 0.075) ** 2 + ((z - 0.555 * f) / ridge) ** 2))
    d = d - 0.012 * k["brow"] * _g(x, z, 0, 0.55 * f, 0.03, 0.03)               # glabella
    d = d + 0.006 * _g(x, z, 0, 0.52 * f, 0.022, 0.014)                         # nasion dip
    zt, zr = 0.322 * f, 0.52 * f                                                # nose tip, root (nasion)
    t = np.clip((zr - z) / (zr - zt), 0, 1)
    reach = 0.1 * k["nose_l"] * (0.2 * k["bridge"] + 0.8 * t ** 1.1)           # bridge projects from the eye corners
    wid = (0.014 + 0.016 * t) * k["nose_w"]
    below = np.where(z < zt, np.exp(-0.5 * ((z - zt) / (0.013 * f)) ** 2), 1.0)
    above = np.where(z > zr, np.exp(-0.5 * ((z - zr) / 0.02) ** 2), 1.0)
    d = d - reach * np.exp(-(np.abs(x) / wid) ** 3) * below * above             # bridge: flat sides, defined edges
    d = d + 0.004 * _g(x, z, 0, 0.36 * f, 0.02, 0.012) * k["nose_l"]            # supratip break
    d = d - 0.03 * k["nose_l"] * _g(x, z, 0, zt, 0.019 * k["nose_w"], 0.017)    # tip lobule (ball)
    col = np.clip((z - 0.296 * f) / 0.01, 0, 1) * np.clip((zt - z) / 0.008 + 1, 0, 1)
    d = d - 0.035 * k["nose_l"] * np.exp(-(x / 0.008) ** 2) * col * (z < zt)    # columella down to the lip
    ala = (0.052 * k["nose_w"], 0.31 * f)
    d = d - 0.055 * k["nose"] * _g(ax, z, ala[0], ala[1], 0.018 * k["nose_w"], 0.016)  # alar wings: read from the front
    r_ala = np.hypot((ax - ala[0]) / k["nose_w"], z - ala[1])
    d = d + 0.006 * np.exp(-0.5 * ((r_ala - 0.02) / 0.004) ** 2) * (z > 0.29 * f)   # alar groove round each wing
    d = d - 0.03 * _g(x, z, 0, 0.215 * f, 0.11 * k["mw"], 0.065)                 # maxilla / dental arch
    d = d - 0.005 * _g(ax, z, 0.009, 0.255 * f, 0.004, 0.02)                     # philtrum columns
    d = d + 0.004 * _g(x, z, 0, 0.255 * f, 0.004, 0.02)                         # philtrum groove
    d = d + 0.008 * _g(ax, z, 0.104 * k["mw"], 0.205 * f, 0.01, 0.01)           # mouth-corner pits
    d = d - 0.012 * k["lips"] * _g(x, z, 0, 0.217 * f, 0.07 * k["mw"], 0.011)   # upper lip
    d = d - 0.015 * k["lips"] * _g(x, z, 0, 0.19 * f, 0.06 * k["mw"], 0.013)    # lower lip
    d = d + 0.008 * _g(x, z, 0, 0.152 * f, 0.05, 0.012)                         # mentolabial sulcus
    d = d - 0.022 * k["chin"] * _g(x, z, 0, 0.065 * f, 0.05, 0.035)             # chin
    d = d - 0.006 * k["chin"] * _g(x, z, 0, 0.08 * f, 0.025, 0.02)              # chin pad
    d = d - 0.026 * k["cheek"] * _g(ax, z, 0.2, 0.44 * f, 0.045, 0.028)         # zygoma
    d = d + 0.008 * max(0.4 - k["fat"], 0) * _g(ax, z, 0.17, 0.33 * f, 0.035, 0.035)  # submalar hollow
    d = d - 0.01 * k["fat"] * _g(ax, z, 0.15, 0.30 * f, 0.06, 0.06)             # cheek fat (build)
    a, b = np.array([0.06 * k["nose_w"], 0.31 * f]), np.array([0.125 * k["mw"], 0.17 * f])
    pq = np.stack([ax, z], -1) - a
    s = np.clip(pq @ (b - a) / ((b - a) @ (b - a)), 0, 1)
    dist = np.linalg.norm(pq - s[..., None] * (b - a), axis=-1)
    d = d + 0.005 * k["fold"] * np.exp(-0.5 * (dist / 0.007) ** 2)            # nasolabial crease
    d = d - 0.004 * k["fold"] * np.exp(-0.5 * ((dist - 0.016) / 0.008) ** 2)   # cheek fold above it
    return d


def _face_y(x, yb, z, k, lid=0.004):
    """Final Y: skull Y + relief on the front, lids wrapped over the eyeballs."""
    wf = np.clip((-yb - 0.12) / 0.12, 0, 1)
    y = yb + wf * _relief(x, z, k)
    ez = 0.5 * k["f"]
    lid = np.broadcast_to(lid, np.shape(x))
    for s in (1, -1):
        r2 = (x - s * k["ex"]) ** 2 + (z - ez) ** 2
        R = k["R"] + lid
        ys = k["ey"] - np.sqrt(np.maximum(R * R - r2, 0))
        y = np.where((r2 < R * R) & (wf > 0), np.minimum(y, ys), y)
    return y


def _surface(xz, k, n, lid=0.004):
    xz = np.asarray(xz, float).reshape(-1, 2)
    yb = _front(_invert(xz, k, n), k, n)[:, 1]
    return np.column_stack([xz[:, 0], _face_y(xz[:, 0], yb, xz[:, 1], k, lid), xz[:, 1]])


# ---- head shell --------------------------------------------------------------------------------------------


def _hole(bm, faces):
    """Delete a patch of faces: its boundary vertices, in order, walking against the kept faces."""
    edges = {e for f in faces for e in f.edges}
    verts = {v for f in faces for v in f.verts}
    bmesh.ops.delete(bm, geom=list(faces), context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[e for e in edges if e.is_valid and not e.link_faces], context="EDGES")
    bmesh.ops.delete(bm, geom=[v for v in verts if v.is_valid and not v.link_faces], context="VERTS")
    nxt = {}
    for e in edges:
        if e.is_valid and len(e.link_faces) == 1:
            loop = next(lp for lp in e.link_faces[0].loops if lp.edge == e)
            nxt[loop.link_loop_next.vert] = loop.vert
    cyc = [next(iter(nxt))]
    while nxt[cyc[-1]] is not cyc[0]:
        cyc.append(nxt[cyc[-1]])
    return cyc


def _ring(bm, prev, pts, mat, faces):
    new = [bm.verts.new(tuple(c)) for c in pts]
    m = len(prev)
    for i in range(m):
        f = bm.faces.new((prev[i], prev[(i + 1) % m], new[(i + 1) % m], new[i]))
        f.material_index = mat
        faces.append(f)
    return new


def _cap(bm, prev, centre, mat):
    c = bm.verts.new(tuple(centre))
    for i in range(len(prev)):
        bm.faces.new((prev[i], prev[(i + 1) % len(prev)], c)).material_index = mat
    return c


def _patch(bm, k, n, box):
    """Front-grid faces inside the head-space box (x0, x1, z0, z1), as a rectangle of the grid."""
    (x0, x1, z0, z1) = box
    corners = [(x, z) for x in (x0, (x0 + x1) / 2, x1) for z in (z0, (z0 + z1) / 2, z1)]
    uw = _invert(corners, k, n)
    u0, u1, w0, w1 = uw[:, 0].min(), uw[:, 0].max(), uw[:, 1].min(), uw[:, 1].max()
    out = []
    for f in bm.faces:
        c = f.calc_center_median()
        if abs(c.y + 1) < 1e-6 and u0 <= c.x <= u1 and w0 <= c.z <= w1:
            out.append(f)
    if len(out) < 4:
        raise ValueError("loop patch %s maps to %d grid faces (uw %.2f..%.2f, %.2f..%.2f): shape sliders too far"
                         % (tuple(round(b, 3) for b in box), len(out), u0, u1, w0, w1))
    return out


def _even(th, curve):
    """Angles re-spaced so curve(angle) points sit at equal arc length (same order, 0 stays 0): an almond's
    tips would otherwise crowd (qa welds points under 0.05 cm)."""
    fine = np.linspace(-math.pi, math.pi, 2001)
    pts = curve(fine)
    arc = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    total, zero = arc[-1], np.interp(0.0, fine, arc)
    want = (zero + th / (2 * math.pi) * total) % total
    return np.interp(want, arc, fine)


def _angles(cyc, cx, cz):
    """Ring angle per boundary vertex from its arc length (even spacing, monotonic), 0 at the vertex furthest
    toward +x, counter-clockwise seen from the front. atan2 on the warped patch bunched points together."""
    co = np.array([(v.co.x, v.co.z) for v in cyc])
    seg = np.linalg.norm(np.roll(co, -1, 0) - co, axis=1)
    s = np.concatenate([[0], np.cumsum(seg)[:-1]]) / seg.sum() * 2 * math.pi
    i0 = int(np.argmax(co[:, 0] - cx - 0.3 * np.abs(co[:, 1] - cz)))
    th = s - s[i0]
    if co[(i0 + 1) % len(co), 1] < co[i0, 1]:  # walking down at +x: clockwise
        th = -th
    return (th + math.pi) % (2 * math.pi) - math.pi


def build_shell(k):
    """Head shell bmesh plus the loop bookkeeping: (bm, info)."""
    n = _norm(k)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=RES - 1, use_grid_fill=True)
    f, ex, R = k["f"], k["ex"], k["R"]
    ez, mz = 0.5 * f, 0.205 * f
    e = k["eye"]  # the patch grows with the eye; its inner edge keeps a grid column for the nose bridge
    eye_box = lambda s: tuple(sorted((s * max(ex - 0.095 * e, 0.03), s * (ex + 0.095 * e)))) + (ez - 0.06 * e, ez + 0.07 * e)  # noqa: E731
    mouth_box = (-0.15 * k["mw"], 0.15 * k["mw"], 0.135 * f, 0.28 * f)
    patches = {"eye_L": _patch(bm, k, n, eye_box(1)), "eye_R": _patch(bm, k, n, eye_box(-1)), "mouth": _patch(bm, k, n, mouth_box)}
    cycles = {name: _hole(bm, faces) for name, faces in patches.items()}
    # skull grid -> head space
    bm.verts.ensure_lookup_table()
    co = np.array([tuple(v.co) for v in bm.verts])
    head = _skull(co, k, n)
    yb = head[:, 1]
    # eyeball centre: cornea apex 0.006 H behind the face plane (the face without its socket)
    plane = _front(_invert([[ex, ez]], k, n), k, n)[0, 1] + _relief(np.array([ex]), np.array([ez]), k, socket=False)[0]
    k["ey"] = float(plane + 0.006 + R)
    head[:, 1] = _face_y(head[:, 0], yb, head[:, 2], k)
    for v, c in zip(bm.verts, head):
        v.co = Vector(c)
    info = {"rings": {}, "bag": {}, "mat_faces": {1: [], 2: [], 3: []}}
    # eye loops: rings ease toward the lid margin; the margin hugs the eyeball, then a socket bag closes it
    for name, s in (("eye_L", 1), ("eye_R", -1)):
        cyc = cycles[name]
        cx = s * ex
        a = min(0.0625 * k["eye"], ex - 0.06)  # big, close-set eyes: the inner corner keeps 0.06 H from the mid-line

        def almond(th, d=0.0, cx=cx, s=s, a=a):
            """Lid outline grown by d (H) all round: rings are offset almonds, so they never cross."""
            c, sn = np.cos(th), np.sin(th)
            out_c = c * s  # + = toward the temple
            lid_z = np.where(sn > 0, 0.022 * k["eye"] * (1 - 0.15 * out_c), 0.016 * k["eye"]) + d
            return np.column_stack([cx + (a + d) * c, ez + lid_z * sn + k["tilt"] * out_c])

        th = _even(_angles(cyc, cx, ez), almond)
        xz = [np.array([(v.co.x, v.co.z) for v in cyc])] + [almond(th, 0.009 * (EYE_RINGS - r)) for r in range(1, EYE_RINGS + 1)]
        xz[1] = (xz[0] + xz[2]) / 2  # ring 1 bridges to the grid patch, whose edge may sit inside the nominal box
        prev, rings = cyc, []
        for r in range(1, EYE_RINGS + 1):
            lid = 0.0018 if r == EYE_RINGS else 0.004 if r == EYE_RINGS - 1 else 0.004
            pts = _surface(xz[r], k, n, lid)
            if r == 2:  # upper-lid crease: this ring sinks a little above the eye
                pts[:, 1] += 0.004 * np.clip(np.sin(th), 0, 1)
            prev = _ring(bm, prev, pts, 0, [])
            rings.append(prev)
        centre = np.array([cx, k["ey"], ez])
        # socket bag: a deep cone behind the lids (shallow fans z-fight in qa), hidden by the eyeball
        # (depth alternates by parity so neighbours are never coplanar; nobody sees it)
        inner = [np.array(tuple(v.co)) * 0.85 + centre * 0.15 + [0, (0.5 + 0.15 * (i % 2)) * R, 0] for i, v in enumerate(prev)]
        sock = _ring(bm, prev, inner, 3, info["mat_faces"][3])
        _cap(bm, sock, centre + [0, 1.0 * R, 0], 3)
        info["rings"][name] = rings
        info["bag"][name] = sock
    # mouth loops: outer ring = nasolabial loop, inner rings = lips, last = lip seam, then the mouth bag
    cyc = cycles["mouth"]
    w = 0.10 * k["mw"]

    def lip_h(th):
        c, sn = np.cos(th), np.sin(th)
        bow = np.where(sn > 0, -0.004 * np.exp(-0.5 * (c / 0.12) ** 2), 0.0)  # cupid's bow dip at the centre
        return np.where(sn > 0, 0.034, 0.04) * k["lips"] * (1 - 0.25 * np.abs(c) ** 4), bow

    def border_xz(th):
        h, bow = lip_h(th)
        return np.column_stack([1.06 * w * np.cos(th), mz + (h + bow) * np.sin(th)])

    th = _even(_angles(cyc, 0.0, (mouth_box[2] + mouth_box[3]) / 2), border_xz)
    c, sn = np.cos(th), np.sin(th)
    h, _ = lip_h(th)
    lips = {3: border_xz(th),                                                  # vermilion border
            4: np.column_stack([1.03 * w * c, mz + 0.5 * h * sn]),              # fullest part of the lip
            5: np.column_stack([w * c, mz + np.sign(sn) * np.maximum(0.0015 * np.abs(sn), 0.0016)])}  # seam, open at the corners
    start = np.array([(v.co.x, v.co.z) for v in cyc])
    prev, rings = cyc, []
    for r in range(1, MOUTH_RINGS + 1):
        pts = _surface(lips[r] if r in lips else start + (lips[3] - start) * (r / 3), k, n)
        if r == 4:
            pts[:, 1] -= 0.004 * k["lips"] * np.abs(sn) ** 0.5  # lips roll forward
        if r == MOUTH_RINGS:
            pts[:, 1] += 0.006  # the seam tucks between the lips
        prev = _ring(bm, prev, pts, 0, [])
        rings.append(prev)
    seam = np.array([tuple(v.co) for v in prev])
    ys = float(seam[:, 1].min())
    # bag rings are scaled copies of the vermilion border: its points are well spread (qa welds points
    # closer than 0.05 cm and flags parallel faces sharing a 0.1 cm cell)
    border = np.array([tuple(v.co) for v in rings[2]])
    bx, bz = border[:, 0], border[:, 2] - mz
    bag_rings = [
        np.column_stack([seam[:, 0] * 0.97, seam[:, 1] + 0.010, mz + 1.5 * (seam[:, 2] - mz)]),
        np.column_stack([bx * 0.8, np.full_like(c, ys + 0.045), mz + 0.9 * bz]),
        np.column_stack([bx * 0.7, np.full_like(c, ys + 0.095), mz - 0.006 + 1.1 * bz]),
        np.column_stack([0.045 * k["mw"] * c, np.full_like(c, ys + 0.14), mz + 0.022 * sn]),  # ellipse: no flat run
    ]
    bag = []
    for i, pts in enumerate(bag_rings):
        prev = _ring(bm, prev, pts, 1 if i == 0 else 2, info["mat_faces"][1 if i == 0 else 2])
        bag.append(prev)
    _cap(bm, prev, (0, ys + 0.21, mz), 2)  # deep pointed cap: a flat fan's coplanar tris z-fight in qa
    info["rings"]["mouth"] = rings
    info["bag"]["mouth"] = bag
    # nostrils: on each side the grid face nearest the nostril on the nose's underside becomes a hole with a short
    # tunnel up into the nose, left open at its far end (2 boundary loops, deep inside where nothing sees them)
    info["nostrils"] = []
    bm.normal_update()
    loops = {v for r in rings for v in r} | {v for r in bag for v in r}
    for s in (1, -1):
        want = Vector((s * 0.021 * k["nose_w"], 0, 0.3 * f))
        cand = [fc for fc in bm.faces if fc.material_index == 0 and fc.normal.z < -0.2 and len(fc.verts) == 4
                and not any(v in loops for v in fc.verts)]
        best = min(cand, key=lambda fc: (fc.calc_center_median() - want).xz.length + 0.3 * fc.calc_center_median().y)
        cyc = _hole(bm, [best])
        c = np.mean([tuple(v.co) for v in cyc], axis=0)
        prev = cyc
        for scale, back, up in ((0.75, 0.008, 0.008), (0.5, 0.016, 0.018)):
            prev = _ring(bm, prev, [c + (np.array(tuple(v.co)) - c) * scale + [0, back, up] for v in prev], 3, [])
        info["nostrils"].append(prev)
    info.update(ys=ys, mz=mz, ez=ez, n=n)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    _unfight(bm, [fc for fc in bm.faces if fc.material_index in (2, 3)], k.get("cell", 0.1 / 24))
    bm.verts.index_update()
    info["rings"] = {name: [[v.index for v in r] for r in rs] for name, rs in info["rings"].items()}
    info["nostrils"] = [[v.index for v in r] for r in info["nostrils"]]
    info["bag"] = {name: ([[v.index for v in r] for r in b] if name == "mouth" else [v.index for v in b])
                   for name, b in info["bag"].items()}
    return bm, info


def _unfight(bm, faces, cell, rounds=12):
    """qa flags parallel faces whose centres share a cell (z-fight). These faces are hidden (mouth bag, eye
    sockets, nostril tunnels), so push one vertex of each offender along its normal until none are left."""
    for _ in range(rounds):
        seen, bad = {}, []
        for fc in faces:
            fc.normal_update()
            key = tuple(round(v / cell) for v in fc.calc_center_median())
            if any(abs(fc.normal.dot(o)) > 0.99 for o in seen.get(key, ())):
                bad.append(fc)
            seen.setdefault(key, []).append(fc.normal.copy())
        if not bad:
            return
        for fc in bad:
            v = fc.verts[len(bad) % len(fc.verts)]
            v.co += fc.normal * cell * 0.8


def _unfight_arrays(co, faces, cell, rounds=12):
    """_unfight for plain arrays (hair cards): the nudges stay under 1 mm."""
    co = co.copy()
    for r in range(rounds):
        seen, bad = {}, []
        for fi, f in enumerate(faces):
            p = co[f]
            n = np.cross(p[2] - p[0], p[-1] - p[1] if len(f) == 4 else p[1] - p[0])
            n = n / max(np.linalg.norm(n), 1e-12)
            key = tuple(np.round(p.mean(0) / cell).astype(int))
            if any(abs(n @ o) > 0.99 for o in seen.get(key, ())):
                bad.append((f, n))
            seen.setdefault(key, []).append(n)
        if not bad:
            break
        for f, n in bad:
            co[f[r % len(f)]] += n * cell * 0.8
    return co


def _lips_mask(x, z, k):
    """Vermilion: upper lip 0.034 H, lower 0.04 H tall at the centre, ending at the mouth corners."""
    w, mz = 0.10 * k["mw"], 0.205 * k["f"]
    edge = np.clip(1 - (x / w) ** 2, 0, 1) ** 0.6
    up = (z >= mz) & (z - mz <= 0.034 * k["lips"] * edge + 0.002)
    lo = (z < mz) & (mz - z <= 0.04 * k["lips"] * edge + 0.002)
    return (up | lo) & (np.abs(x) < w)


# ---- small parts -------------------------------------------------------------------------------------------


def _lathe(profile, segs):
    """Surface of revolution about +Y: profile [(r, y)], r=0 points become poles. -> verts, faces, row of each face."""
    verts, rows, faces, frow = [], [], [], []
    for r, y in profile:
        if r <= 0:
            rows.append([len(verts)])
            verts.append((0.0, y, 0.0))
        else:
            rows.append(list(range(len(verts), len(verts) + segs)))
            verts += [(r * math.cos(2 * math.pi * j / segs), y, r * math.sin(2 * math.pi * j / segs)) for j in range(segs)]
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        for j in range(segs):
            if len(a) == 1:
                faces.append([a[0], b[(j + 1) % segs], b[j]])
            elif len(b) == 1:
                faces.append([a[j], a[(j + 1) % segs], b[0]])
            else:
                faces.append([a[j], a[(j + 1) % segs], b[(j + 1) % segs], b[j]])
            frow.append(i)
    return verts, faces, frow


def _rounded_box(size, n=2):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=n - 1, use_grid_fill=True)
    pts = np.array([tuple(v.co) for v in bm.verts])
    faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    pts = 0.45 * pts + 0.55 * pts / np.linalg.norm(pts, axis=1, keepdims=True) * 1.2
    return pts * (np.array(size) / 2 / np.abs(pts).max(axis=0)), faces


def _object(name, verts, faces, mats, face_mat=None, origin=(0, 0, 0), smooth=True):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [list(f) for f in faces])
    me.update()
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    if face_mat is not None:
        me.polygons.foreach_set("material_index", np.asarray(face_mat, np.int32))
    me.polygons.foreach_set("use_smooth", np.full(len(me.polygons), smooth))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    if any(origin):
        me.transform(Matrix.Translation(-Vector(origin)))
        ob.location = Vector(origin)
    return ob


def _eye(name, centre, k, mats):
    """Sclera sphere open at the limbus + cornea dome (one closed shell), iris + pupil disc 2 mm under it."""
    R = k["R"]
    lim = 0.025 * min(k["eye"], 1.1)                  # limbus radius = iris radius (12 mm iris at H 24 cm)
    ly = -math.sqrt(R * R - lim * lim)                # limbus plane (front is -Y)
    rc = 0.0325                                       # cornea curvature radius (7.8 mm)
    cy = ly + math.sqrt(rc * rc - lim * lim)          # cornea sphere centre
    # qa welds points closer than 0.05 cm and flags parallel faces whose centres share a 0.1 cm cell, so small
    # parts stay coarse and every pole is a cone, never a flat fan
    prof = [(0.0, R + 0.012)]                         # back pole pulled out into a cone (hidden in the socket)
    span = math.pi - math.asin(lim / R)
    prof += [(R * math.sin(span * i / 9), R * math.cos(span * i / 9)) for i in range(1, 9)]
    prof += [(lim, ly), (0.0, cy - rc)]               # limbus, then the cornea dome as one fan to its apex
    v1, f1, row = _lathe(prof, 16)
    fm = [1 if r >= 9 else 0 for r in row]           # 0 sclera, 1 cornea
    iris = [(0.0, ly + 0.025), (0.95 * lim, ly + 0.004), (0.3 * lim, ly + 0.0045), (0.0, ly + 0.0145)]  # closed: back cone, iris ring, pupil cone
    v2, f2, row2 = _lathe(iris, 12)
    base = len(v1)
    verts = v1 + v2
    faces = f1 + [[i + base for i in f] for f in f2]
    fm += [3 if r >= 2 else 2 for r in row2]          # 2 iris, 3 pupil
    verts = [(x + centre[0], y + centre[1], z + centre[2]) for x, y, z in verts]
    return _object(name, verts, faces, mats, fm, origin=centre)


def _arch(y0, z_top, z_bot, k, gum_z, upper):
    """Teeth (incisor, lateral, canine, 2 premolars per side) on a parabolic arch + a gum strip above/below."""
    widths = [0.034, 0.027, 0.031, 0.028, 0.028] if upper else [0.022, 0.024, 0.028, 0.028, 0.029]
    c = 30.0
    xs = np.linspace(0, 0.09, 400)
    ys = y0 + c * xs ** 2
    arc = np.concatenate([[0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))])
    scale = 0.86 * k["mw"] ** 0.5
    verts, faces = [], []
    h = z_top - z_bot
    for side in (1, -1):
        at = 0.002  # teeth stay > 0.05 cm apart (qa welds closer points)
        for w in widths:
            w *= scale
            mid = np.interp(at + w / 2, arc, xs)
            at += w
            ang = math.atan(2 * c * mid)
            pts, fs = _rounded_box((w * 0.85, 0.012 if w < 0.03 else 0.02, h))
            rot = np.array(Matrix.Rotation(side * ang, 3, "Z"))
            pts = pts @ rot.T + [side * mid, y0 + c * mid * mid + 0.006, (z_top + z_bot) / 2]
            n0 = len(verts)
            verts += pts.tolist()
            faces += [[i + n0 for i in f] for f in fs]
    tooth_faces = len(faces)
    # gum: a tube along the arch, 6-sided section
    path = np.linspace(-0.088, 0.088, 17)
    sec = [(math.cos(a) * 0.011, math.sin(a) * 0.009) for a in np.linspace(0, 2 * math.pi, 7)[:-1]]
    rows = []
    for x in path:
        y = y0 + c * x * x + 0.006
        t = np.array([1.0, 2 * c * x, 0])
        t /= np.linalg.norm(t)
        nrm = np.array([-t[1], t[0], 0])
        rows.append(len(verts))
        verts += [[x + nrm[0] * a, y + nrm[1] * a, gum_z + b] for a, b in sec]
    for i in range(len(rows) - 1):
        for j in range(6):
            a0, b0 = rows[i], rows[i + 1]
            faces.append([a0 + j, a0 + (j + 1) % 6, b0 + (j + 1) % 6, b0 + j])
    for r, x in ((rows[0], path[0]), (rows[-1], path[-1])):  # closed ends
        verts.append([x * 1.04, y0 + c * x * x + 0.006, gum_z])
        faces += [[r + j, r + (j + 1) % 6, len(verts) - 1] for j in range(6)]
    return verts, faces, tooth_faces


def _neck(k, segs=20, rows=10):
    """Closed neck stub, 0.5 H (12 cm) below the chin up into the skull: leans forward going up, with a
    sternocleidomastoid ridge per side from behind the ear down to the sternum. Cone caps (flat fans z-fight)."""
    rx, ry = 0.22 * k["width"], 0.22
    verts, faces, rings = [], [], []
    for r in range(rows):
        t = r / (rows - 1)                       # 0 bottom .. 1 top
        z, yc = -0.5 + 0.72 * t, 0.08 - 0.08 * t
        a = np.linspace(0, 2 * math.pi, segs, endpoint=False)
        bump = np.zeros(segs)
        for s in (1, -1):                        # muscle line: side-back at the top, front-centre at the bottom
            line = 0.35 + (-math.pi / 2 + 0.3 - 0.35) * (1 - t)
            line = line if s > 0 else math.pi - line
            gap = (a - line + math.pi) % (2 * math.pi) - math.pi
            bump += 0.07 * np.exp(-0.5 * (gap / 0.22) ** 2) * math.sin(math.pi * min(1.0, 0.15 + 0.85 * t))
        scale = (1 + bump) * (1 + 0.3 * (1 - t) ** 3)  # trapezius flare toward the shoulders
        rings.append(len(verts))
        verts += np.column_stack([rx * np.cos(a) * scale, yc + ry * np.sin(a) * scale, np.full(segs, z)]).tolist()
    for r in range(rows - 1):
        a0, b0 = rings[r], rings[r + 1]
        faces += [[a0 + j, a0 + (j + 1) % segs, b0 + (j + 1) % segs, b0 + j] for j in range(segs)]
    for ring, z, yc in ((rings[0], -0.56, 0.08), (rings[-1], 0.3, 0.0)):
        verts.append([0.0, yc, z])
        faces += [[ring + j, ring + (j + 1) % segs, len(verts) - 1] for j in range(segs)]
    return verts, faces


def scalp_faces(me):
    """Skin faces above the hairline (0.80 H at the forehead, 0.66 over the ears, 0.42 at the nape)."""
    out = []
    zmin = min(v.co.z for v in me.vertices)
    H = max(v.co.z for v in me.vertices) - zmin
    for q in me.polygons:
        if q.material_index != 0:
            continue
        c = q.center
        phi = abs(math.atan2(c.x, -c.y))
        line = float(np.interp(phi, [0, 0.6, 1.6, math.pi], [0.80, 0.78, 0.66, 0.42]))
        if (c.z - zmin) / H > line:
            out.append(q.index)
    return out


def _ear(side, centre, k):
    """Closed ear shell: helix rim, antihelix ridge, concha bowl and lobe on the front; flat back on the head."""
    S, J = 18, 6
    hh, ww = 0.135 * k["ears"], 0.068 * k["ears"]

    def outline(th):
        lobe = np.clip(-np.sin(th), 0, 1)
        return ww * np.cos(th) * (1 - 0.3 * lobe), hh * np.sin(th)

    def front_h(r, th):
        lobe = np.clip(-np.sin(th), 0, 1) ** 2
        rim = 0.034 * np.exp(-0.5 * ((r - 0.92) / 0.06) ** 2) + 0.022 * np.exp(-0.5 * ((r - 0.62) / 0.07) ** 2)  # helix, antihelix
        bowl = -0.016 * (1 - _smooth(0.35, 0.5, r))                                    # concha
        front = np.exp(-0.5 * (((th - math.pi) % (2 * math.pi) - math.pi) / 0.35) ** 2)  # toward the face (-Y)
        tragus = 0.02 * front * np.exp(-0.5 * ((r - 0.48) / 0.09) ** 2)
        return 0.018 + (rim + bowl) * (1 - lobe) + 0.008 * lobe + tragus

    th = np.linspace(0, 2 * math.pi, S, endpoint=False)
    bx, bz = outline(th)
    verts, faces = [], []
    rows = {"f": [], "b": []}
    for kind in ("f", "b"):
        rows[kind].append([len(verts)])
        verts.append((-0.008 if kind == "f" else -0.03, 0.0, 0.0))  # both centres are cones, not flat fans (the back one is inside the head)
        for j in range(1, J):
            r = (j / J) ** 0.6  # first ring at 0.34: tiny centre fans z-fight in qa
            hf = front_h(r, th)
            hb = -0.008 + (front_h(1.0, th) - 0.006 + 0.008) * r ** 2
            hx = hf if kind == "f" else hb
            rows[kind].append(list(range(len(verts), len(verts) + S)))
            verts += [(hx[i], bx[i] * r, bz[i] * r) for i in range(S)]
    rim = list(range(len(verts), len(verts) + S))
    hr = front_h(1.0, th) - 0.003
    verts += [(hr[i], bx[i], bz[i]) for i in range(S)]
    for kind in ("f", "b"):
        rs = rows[kind] + [rim]
        for i in range(len(rs) - 1):
            a, b = rs[i], rs[i + 1]
            for j in range(S):
                if len(a) == 1:
                    faces.append([a[0], b[j], b[(j + 1) % S]])
                else:
                    faces.append([a[j], b[j], b[(j + 1) % S], a[(j + 1) % S]])
    pts = np.array(verts)
    pts[:, 0] *= side
    rot = np.array(Matrix.Rotation(side * math.radians(-12), 3, "Z") @ Matrix.Rotation(math.radians(-14), 3, "X"))
    return (pts @ rot.T + centre).tolist(), faces


# ---- materials ---------------------------------------------------------------------------------------------


def _material(name, layers):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    base = layers[0]
    color = list(base["color"])[:3]
    rec = {"id": "m" + hashlib.sha1(name.encode()).hexdigest()[:6], "name": name, "base_color": color + [1.0],
           "metallic": 0.0, "roughness": base.get("roughness", 0.5), "emissive": [0, 0, 0], "emissive_strength": 0.0,
           "opacity": base.get("opacity", 1.0), "textures": {}, "spec": {"layers": layers}}
    m["forge"] = json.dumps(rec)
    build_material(m, graph(layers))
    m.diffuse_color = (*color, 1.0)
    return m


def materials(prefix, tone, iris):
    """Skin (SSS radius R 1.0 / G 0.35 / B 0.2 cm, roughness breakup, pore bump), lips, eye and mouth parts."""
    skin = (_LIGHT + (_DEEP - _LIGHT) * min(max(tone, 0.0), 1.0)).tolist()
    sss = {"type": "sss", "weight": 1.0, "radius": [1.0, 0.35, 0.2], "scale": 0.01}  # Forge cm -> metres
    lip = (np.array(skin) * [1.0, 0.62, 0.62]).tolist()
    spec = {
        "Skin": [{"type": "base", "color": skin, "roughness": 0.5},
                 {"type": "noise", "target": "roughness", "scale": 40.0, "detail": 4.0, "amount": 0.16},
                 {"type": "noise", "target": "color", "scale": 25.0, "detail": 6.0, "amount": 0.06, "color": [0.45, 0.12, 0.1]},
                 {"type": "noise", "target": "bump", "scale": 900.0, "detail": 2.0, "amount": 0.25}, sss],
        "Lips": [{"type": "base", "color": lip, "roughness": 0.38},
                 {"type": "noise", "target": "bump", "scale": 300.0, "detail": 2.0, "amount": 0.2}, sss],
        "MouthBag": [{"type": "base", "color": [0.22, 0.04, 0.04], "roughness": 0.35}, sss],
        "EyeSocket": [{"type": "base", "color": [0.16, 0.06, 0.05], "roughness": 0.2}],
        "Sclera": [{"type": "base", "color": [0.82, 0.78, 0.74], "roughness": 0.25},
                   {"type": "noise", "target": "color", "scale": 30.0, "detail": 8.0, "amount": 0.15, "color": [0.7, 0.25, 0.22]},
                   {"type": "sss", "weight": 0.5, "radius": [0.3, 0.15, 0.1], "scale": 0.01}],
        "Cornea": [{"type": "base", "color": [1.0, 1.0, 1.0], "roughness": 0.0, "opacity": 0.06},
                   {"type": "clearcoat", "weight": 1.0, "roughness": 0.0}],
        "Iris": [{"type": "base", "color": list(IRIS[iris]), "roughness": 0.4},
                 {"type": "noise", "target": "color", "scale": 220.0, "detail": 8.0, "amount": 0.45, "color": [0.02, 0.01, 0.0]}],
        "Pupil": [{"type": "base", "color": [0.004, 0.004, 0.004], "roughness": 0.3}],
        "Teeth": [{"type": "base", "color": [0.80, 0.76, 0.66], "roughness": 0.25},
                  {"type": "sss", "weight": 0.3, "radius": [0.3, 0.3, 0.25], "scale": 0.01}],
        "Gums": [{"type": "base", "color": [0.55, 0.17, 0.17], "roughness": 0.3}, sss],
        "Tongue": [{"type": "base", "color": [0.58, 0.2, 0.2], "roughness": 0.45},
                   {"type": "noise", "target": "bump", "scale": 400.0, "detail": 2.0, "amount": 0.3}, sss],
        "Caruncle": [{"type": "base", "color": [0.7, 0.3, 0.3], "roughness": 0.15}, sss],
        "TearLine": [{"type": "base", "color": [1.0, 1.0, 1.0], "roughness": 0.0, "opacity": 0.3},
                     {"type": "clearcoat", "weight": 1.0, "roughness": 0.0}],
        "Brow": [{"type": "base", "color": [0.035, 0.022, 0.014], "roughness": 0.55},
                 {"type": "noise", "target": "color", "scale": 300.0, "detail": 4.0, "amount": 0.3, "color": [0.09, 0.06, 0.04]}],
        "Lash": [{"type": "base", "color": [0.012, 0.009, 0.007], "roughness": 0.5}],
        "Ears": [{"type": "base", "color": (np.array(skin) * [1.0, 0.88, 0.86]).tolist(), "roughness": 0.5},
                 {"type": "noise", "target": "bump", "scale": 900.0, "detail": 2.0, "amount": 0.2}, sss],
    }
    return {k: _material("%s_%s" % (prefix, k), v) for k, v in spec.items()}


# ---- hair cards: brows and lashes --------------------------------------------------------------------------
# Cards are 2-3 segment strips. Neighbours alternate a +-0.45 rad twist so qa never sees coplanar neighbours, and
# every card stays wider than 0.05 cm so nothing welds.


def _strip(pts, side, width):
    pts, side = np.asarray(pts, float), np.asarray(side, float)
    m = len(pts)
    verts = np.concatenate([pts - side * width / 2, pts + side * width / 2]).tolist()
    return verts, [[i, i + 1, m + i + 1, m + i] for i in range(m - 1)]


def _spread(co, min_d, rounds=8):
    """Push the later point of any pair closer than min_d (H units) 1.5 min_d off the skin (-Y): overlapping
    hair cards would otherwise weld in qa."""
    co = co.copy()
    for _ in range(rounds):
        d = np.linalg.norm(co[:, None] - co[None], axis=2)
        d[np.tril_indices(len(co))] = 9
        close = np.unique(np.nonzero(d < min_d)[1])
        if not len(close):
            break
        co[close, 1] -= 1.5 * min_d
    return co.tolist()


def _brows(k, info):
    """Two staggered rows of 18 cards per brow along the arch: hairs rise at the head, sweep out to the tail."""
    out, f, n = [], k["f"], info["n"]
    thick = 1.0 + 0.3 * (k["brow"] - 1)
    for s in (1, -1):
        for row in range(3):
            for i in range(18):
                u = (i + 0.33 * row) / 18
                if row == 2 and u > 0.55:
                    continue  # the tail thins to two rows
                x = 0.052 + 0.17 * u
                z = 0.56 * f + 0.022 * math.sin(math.pi * min(u / 0.65, 1.0) * 0.5) - 0.012 * max(u - 0.65, 0) / 0.35 \
                    + (row - 1) * 0.006 * thick * (1.2 - 0.5 * u)
                ang = math.radians(75 - 70 * min(u / 0.6, 1.0) - 10 * max(u - 0.6, 0) / 0.4)  # up at the head, out at the tail
                d = np.array([s * math.cos(ang), math.sin(ang)])
                L = 0.03 * (0.8 + 0.2 * math.sin(math.pi * u))
                xz = np.array([[s * x, z]]) + np.outer(np.arange(3) / 2, d * L)
                pts = _surface(xz, k, n)
                # neighbours along a row and across rows sit on different depth levels: overlapping cards never weld
                pts[:, 1] -= 0.0012 + 0.003 * (i % 2 + 2 * row) + 0.0018 * np.arange(3) / 2
                tw = (0.45, -0.45, 0.9)[(i + row) % 3]
                perp = np.array([-d[1], 0.0, d[0]])
                side = perp * math.cos(tw) + np.array([0, -1.0, 0]) * math.sin(tw)
                v, fc = _strip(pts, np.tile(side, (3, 1)), 0.006 * thick)
                out.append((v, fc, 2))
    return out


def _lashes(me, k, info):
    """One card per lid-margin vertex: upper lashes 0.035 H curling up, lower 0.02 H pointing down."""
    out, ez = [], info["ez"]
    for name, s in (("eye_L", 1), ("eye_R", -1)):
        ring = np.array([tuple(me.vertices[i].co) for i in info["rings"][name][-1]])
        centre = np.array([s * k["ex"], 0.0, ez])
        inner_x = ring[np.argmin(np.abs(ring[:, 0])), 0]
        m = len(ring)
        made = 0
        for i, p in enumerate(ring):
            if abs(p[0] - inner_x) < 0.012:
                continue  # caruncle corner
            upper = p[2] > ez + k["tilt"] * (p[0] - centre[0]) * s / 0.0625
            outward = p - centre
            outward[1] = 0
            outward /= max(np.linalg.norm(outward), 1e-9)
            if upper:
                d, L, curl, root = outward * 0.2 + [0, -0.85, 0.45], 0.024, 0.35, p + [0, -0.002, 0.001]
            else:
                if i % 2:
                    continue  # lower lashes: sparser and shorter
                d, L, curl, root = outward * 0.2 + [0, -0.85, -0.4], 0.011, -0.1, p + [0, -0.004, -0.002]
            d = d / np.linalg.norm(d)
            t = np.arange(4) / 3 if upper else np.arange(2.0)  # short lower lashes: one quad (straight segments z-fight)
            pts = root + np.outer(t, d * L) + np.outer(t ** 2, [0, 0, curl * L])
            tan = ring[(i + 1) % m] - ring[i - 1]
            tan /= max(np.linalg.norm(tan), 1e-9)
            tw = 0.5 if made % 2 else -0.5  # alternate per card made (lower lashes skip vertices)
            made += 1
            side = tan * math.cos(tw) + np.cross(d, tan) * math.sin(tw)
            v, fc = _strip(pts, np.tile(side, (len(t), 1)), 0.0035)
            out.append((v, fc, 3))
    return out


# ---- assembly ----------------------------------------------------------------------------------------------


def _groups(ob, info, k):
    me = ob.data
    co = np.array([tuple(v.co) for v in me.vertices])
    x, y, z = co.T
    ax, f = np.abs(x), k["f"]
    front = np.clip((-y - 0.1) / 0.1, 0, 1)
    w = {
        "eyes": np.zeros(len(co)),
        "lips": np.where(_lips_mask(x, z, k), 1.0, 0.0) * front,
        "brows": _g(ax, z, 0.12, 0.565 * f, 0.06, 0.03) * front,
        "jaw": _smooth(0.32 * f, 0.12 * f, z) * np.clip((0.12 - y) / 0.2, 0, 1),
        "nose": _g(x, z, 0, 0.38 * f, 0.05 * k["nose_w"], 0.09) * front,
        "cheeks": _g(ax, z, 0.17, 0.36 * f, 0.06, 0.06) * front,
    }
    for name in ("eye_L", "eye_R"):
        w["eyes"][[i for r in info["rings"][name] for i in r] + info["bag"][name]] = 1.0
    w["lips"][info["bag"]["mouth"][0] + [i for r in info["rings"]["mouth"][2:] for i in r]] = 1.0
    for name, vals in w.items():
        g = ob.vertex_groups.new(name=name)
        for val in sorted({round(float(v), 2) for v in vals if v > 0.01}):
            g.add([i for i, v in enumerate(vals) if round(float(v), 2) == val], val, "REPLACE")


def _uv(me):
    """Cylindrical head UV: u = angle round the head (face centre 0.5), v = height."""
    co = np.array([tuple(v.co) for v in me.vertices])
    u = 0.5 + np.arctan2(co[:, 0], -co[:, 1]) / (2 * math.pi)
    v = (co[:, 2] - co[:, 2].min()) / max(np.ptp(co[:, 2]), 1e-9)
    layer = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        us = [u[me.loops[i].vertex_index] for i in poly.loop_indices]
        if max(us) - min(us) > 0.5:
            us = [a + 1 if a < 0.5 else a for a in us]
        for i, a in zip(poly.loop_indices, us):
            layer.data[i].uv = (a, v[me.loops[i].vertex_index])


def build_head(p):
    """All head objects in bpy, metres, chin on z=0 facing -Y. Returns {part: object} plus stats."""
    k = shape(p)
    unit = float(p.get("unit", 0.01))
    H = float(p.get("height_cm") or 24.0) * unit
    k["cell"] = 0.1 * 0.01 / H  # qa's 0.1 cm z-fight cell in head heights
    prefix = str(p.get("name") or "Head")
    mats = materials(prefix, float(p.get("tone", 0.3)), p.get("eye_color", "brown"))
    bm, info = build_shell(k)
    inner = {i for r in info["rings"]["mouth"][2:] for i in r}
    for face in bm.faces:
        if face.material_index == 0 and all(v.index in inner for v in face.verts):
            face.material_index = 1
    me = bpy.data.meshes.new(prefix)
    bm.to_mesh(me)
    bm.free()
    for m in ("Skin", "Lips", "MouthBag", "EyeSocket"):
        me.materials.append(mats[m])
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    head = bpy.data.objects.new(prefix, me)
    bpy.context.scene.collection.objects.link(head)
    _groups(head, info, k)
    _uv(me)
    parts = {"head": head}
    ez, R = info["ez"], k["R"]
    for name, s in (("L", 1), ("R", -1)):
        parts["eye_" + name] = _eye("%s_eye_%s" % (prefix, name), (s * k["ex"], k["ey"], ez), k,
                                    [mats[m] for m in ("Sclera", "Cornea", "Iris", "Pupil")])
    # caruncles + tear lines: one wet-parts object
    wv, wf, wm = [], [], []
    for name, s in (("eye_L", 1), ("eye_R", -1)):
        margin = np.array([tuple(me.vertices[i].co) for i in info["rings"][name][-1]])
        centre = np.array([s * k["ex"], k["ey"], ez])
        inner = margin[np.argmin(np.abs(margin[:, 0]))]
        cpos = inner + [s * 0.006, 0.002, -0.001]
        v, fc, _ = _lathe([(0, 0.009), (0.005, 0.0), (0, -0.009)], 6)  # coarse double cone: a fine one z-fights on a child
        n0 = len(wv)
        wv += [(cpos[0] + a * 1.3, cpos[1] + b * 0.5, cpos[2] + c) for a, b, c in v]
        wf += [[i + n0 for i in q] for q in fc]
        wm += [0] * len(fc)
        low = margin[(margin[:, 2] < ez + k["tilt"] * 0.5) & (np.abs(margin[:, 0] - inner[0]) > 0.014)]  # clear of the caruncle
        low = low[np.argsort(low[:, 0])]
        if len(low) >= 2:
            toward = centre - low
            toward /= np.linalg.norm(toward, axis=1, keepdims=True)
            n0 = len(wv)
            wv += (low + [0, -0.0005, 0]).tolist() + (low + toward * 0.0035).tolist()
            m = len(low)
            wf += [[n0 + i, n0 + i + 1, n0 + m + i + 1, n0 + m + i] for i in range(m - 1)]
            wm += [1] * (m - 1)
    for verts, faces, mat in _brows(k, info) + _lashes(me, k, info):
        n0 = len(wv)
        wv += verts
        wf += [[i + n0 for i in q] for q in faces]
        wm += [mat] * len(faces)
    for _ in range(3):  # each pass can undo the other a little: alternate until both hold
        wv = _spread(_unfight_arrays(np.array(wv), wf, k["cell"]), 0.0006 / H)
    parts["details"] = _object(prefix + "_details", wv, wf, [mats[m] for m in ("Caruncle", "TearLine", "Brow", "Lash")], wm)
    # teeth, gums, tongue
    ys, mz = info["ys"], info["mz"]
    tv, tf, nt = _arch(ys + 0.012, mz + 0.038, mz - 0.004, k, mz + 0.038, True)  # roots end mid-gum
    parts["teeth_U"] = _object(prefix + "_teeth_U", tv, tf, [mats["Teeth"], mats["Gums"]], [0] * nt + [1] * (len(tf) - nt))
    tv, tf, nt = _arch(ys + 0.016, mz - 0.008, mz - 0.042, k, mz - 0.042, False)
    parts["teeth_L"] = _object(prefix + "_teeth_L", tv, tf, [mats["Teeth"], mats["Gums"]], [0] * nt + [1] * (len(tf) - nt))
    pts, fs = _rounded_box((0.09 * k["mw"], 0.11, 0.034), 3)
    pts = pts + [0, ys + 0.085, mz - 0.022]
    parts["tongue"] = _object(prefix + "_tongue", pts, fs, [mats["Tongue"]])
    # ears: on the skull side, brow to nose base, a little behind the head centre
    ev, ef = [], []
    co = np.array([tuple(v.co) for v in me.vertices])
    ze = (0.555 + 0.30) / 2 * k["f"] + 0.01
    near = co[(np.abs(co[:, 2] - ze) < 0.03) & (np.abs(co[:, 1] - 0.03) < 0.04)]
    side_x = float(np.abs(near[:, 0]).max()) if len(near) else 0.3
    for s in (1, -1):
        v, fc = _ear(s, np.array([s * (side_x - 0.004), 0.03, ze]), k)
        n0 = len(ev)
        ev += v
        ef += [[i + n0 for i in q] for q in fc]
    parts["ears"] = _object(prefix + "_ears", ev, ef, [mats["Ears"]])
    nv, nf = _neck(k)
    parts["neck"] = _object(prefix + "_neck", nv, nf, [mats["Skin"]])
    for name in ("details", "teeth_U", "teeth_L", "tongue", "ears", "neck"):  # pivot on the part's base (qa); eyes keep a centre pivot to aim
        ob = parts[name]
        co = np.array([tuple(v.co) for v in ob.data.vertices])
        base = Vector(((co[:, 0].min() + co[:, 0].max()) / 2, (co[:, 1].min() + co[:, 1].max()) / 2, co[:, 2].min()))
        ob.data.transform(Matrix.Translation(-base))
        ob.location = base
    # head heights -> metres
    for ob in parts.values():
        ob.data.transform(Matrix.Scale(H, 4))
        ob.location = ob.location * H
    bpy.context.view_layer.update()
    return parts, info


def stats(parts):
    head = parts["head"].data
    quads = sum(1 for q in head.polygons if q.loop_total == 4)
    tris = sum(sum(q.loop_total - 2 for q in ob.data.polygons) for ob in parts.values())
    return {"tris": tris, "quads": round(100.0 * quads / len(head.polygons)), "groups": len(parts["head"].vertex_groups)}


def op_head(p):
    parts, _ = build_head({**p, "unit": float(p["scene"].get("unit", 0.01))})
    s = stats(parts)
    summary = "head %s %s %.1fk tris %d%% quads, eyes 2, teeth, mouth bag, %d groups" % (
        p.get("age", "adult"), p.get("sex", "neutral"), s["tris"] / 1000, s["quads"], s["groups"])
    return {"summary": summary, **s, "scalp": scalp_faces(parts["head"].data), "scene": bridge.dump(p["scene"])}
