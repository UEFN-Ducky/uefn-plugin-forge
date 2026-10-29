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
        jaw_fwd=0.11 * (1 + 0.15 * s["chin"]), f=ag["face"], brow=sx["brow"] * ag["brow"] * (1 + 0.4 * s["brow"]),
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
    gonial = 0.07 * np.exp(-((dz + 0.6) / 0.18) ** 2) * (1 - back)              # jaw angle
    rx = 0.31 * k["width"] * (1 - k["taper"] * low ** 2 + 0.09 * up + gonial)
    x = np.sign(dx) * np.abs(dx) ** (0.9 - 0.3 * up) * rx                      # upper skull: round crown, not an egg
    y = np.sign(dy) * np.abs(dy) ** 0.85 * np.where(dy < 0, 0.35, 0.385 * k["depth"])
    z = np.sign(dz) * np.abs(dz) ** np.where(dz > 0, 1.2, 0.95) * np.where(dz > 0, 0.53, 0.47)
    y = y - k["jaw_fwd"] * low ** 1.5 * front                                   # mandible and chin come forward
    z = z + low ** 1.5 * (0.16 * _smooth(-0.55, 0.3, dy) + 0.30 * back)       # under-jaw rises to the skull base
    y = y + 0.03 * up ** 3 * front                                             # forehead slopes to the crown
    y = y + 0.035 * np.exp(-((dz - 0.25) / 0.3) ** 2) * back                    # occiput
    return np.stack([x, y, z + 0.47], 1)


def _norm(k):
    """(z0, scale) so the skull spans z 0..1."""
    i = np.arange(20000) + 0.5
    phi, th = np.arccos(1 - 2 * i / 20000), math.pi * (1 + 5 ** 0.5) * i
    pts = _raw(np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1) @ k["rot"], k)
    lo, hi = pts[:, 2].min(), pts[:, 2].max()
    return float(lo), float(1.0 / (hi - lo))


def _skull(p, k, n):
    q = _raw(p, k)
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
    d = d - 0.02 * k["brow"] * _g(ax, z, 0.115, 0.585 * f, 0.07, 0.022)         # brow ridge
    d = d - 0.012 * k["brow"] * _g(x, z, 0, 0.57 * f, 0.03, 0.03)               # glabella
    zt, zr = 0.322 * f, 0.56 * f                                                # nose tip, root (nasion)
    t = np.clip((zr - z) / (zr - zt), 0, 1)
    reach = 0.085 * k["nose_l"] * (0.15 * k["bridge"] + 0.85 * t ** 1.15)
    wid = (0.018 + 0.022 * t) * k["nose_w"]
    below = np.where(z < zt, np.exp(-0.5 * ((z - zt) / (0.016 * f)) ** 2), 1.0)
    above = np.where(z > zr, np.exp(-0.5 * ((z - zr) / 0.02) ** 2), 1.0)
    d = d - reach * np.exp(-0.5 * (x / wid) ** 2) * below * above
    d = d - 0.02 * k["nose_l"] * _g(x, z, 0, zt, 0.02 * k["nose_w"], 0.02)       # tip lobule
    d = d - 0.035 * k["nose"] * _g(ax, z, 0.045 * k["nose_w"], 0.312 * f, 0.018 * k["nose_w"], 0.018)  # alae
    d = d + 0.006 * _g(ax, z, 0.024 * k["nose_w"], 0.298 * f, 0.009, 0.006)    # nostril shadow
    d = d - 0.03 * _g(x, z, 0, 0.215 * f, 0.11 * k["mw"], 0.065)                 # maxilla / dental arch
    d = d - 0.003 * _g(ax, z, 0.009, 0.25 * f, 0.003, 0.018)                     # philtrum ridges
    d = d - 0.012 * k["lips"] * _g(x, z, 0, 0.217 * f, 0.07 * k["mw"], 0.011)   # upper lip
    d = d - 0.015 * k["lips"] * _g(x, z, 0, 0.19 * f, 0.06 * k["mw"], 0.013)    # lower lip
    d = d + 0.008 * _g(x, z, 0, 0.152 * f, 0.05, 0.012)                         # mentolabial sulcus
    d = d - 0.022 * k["chin"] * _g(x, z, 0, 0.065 * f, 0.05, 0.035)             # chin
    d = d - 0.02 * k["cheek"] * _g(ax, z, 0.19, 0.43 * f, 0.05, 0.035)          # cheekbones
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


def _angles(cyc, cx, cz, hx, hz):
    return np.array([math.atan2((v.co.z - cz) / hz, (v.co.x - cx) / hx) for v in cyc])


def build_shell(k):
    """Head shell bmesh plus the loop bookkeeping: (bm, info)."""
    n = _norm(k)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=RES - 1, use_grid_fill=True)
    f, ex, R = k["f"], k["ex"], k["R"]
    ez, mz = 0.5 * f, 0.205 * f
    eye_box = lambda s: tuple(sorted((s * (ex - 0.095), s * (ex + 0.095)))) + (ez - 0.06, ez + 0.07)  # noqa: E731
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
        th = _angles(cyc, cx, ez, 0.095, 0.065)
        c, sn = np.cos(th), np.sin(th)
        a = 0.0625 * k["eye"]
        out_c = c * s  # + = toward the temple
        lid_z = np.where(sn > 0, 0.022 * k["eye"] * (1 - 0.15 * out_c), 0.016 * k["eye"])
        target = np.column_stack([cx + a * c, ez + lid_z * sn + k["tilt"] * out_c])
        start = np.array([(v.co.x, v.co.z) for v in cyc])
        prev, rings = cyc, []
        for r in range(1, EYE_RINGS + 1):
            t = 1 - (1 - r / EYE_RINGS) ** 1.4
            lid = 0.0012 if r == EYE_RINGS else 0.003 if r == EYE_RINGS - 1 else 0.004
            pts = _surface(start + (target - start) * t, k, n, lid)
            prev = _ring(bm, prev, pts, 0, [])
            rings.append(prev)
        centre = np.array([cx, k["ey"], ez])
        inner = [centre + (np.array(tuple(v.co)) - centre) * 0.65 for v in prev]
        sock = _ring(bm, prev, inner, 3, info["mat_faces"][3])
        _cap(bm, sock, centre + [0, 0.3 * R, 0], 3)
        info["rings"][name] = rings
        info["bag"][name] = sock
    # mouth loops: outer ring = nasolabial loop, inner rings = lips, last = lip seam, then the mouth bag
    cyc = cycles["mouth"]
    th = _angles(cyc, 0.0, (mouth_box[2] + mouth_box[3]) / 2, 0.15, 0.0725)
    c, sn = np.cos(th), np.sin(th)
    w = 0.10 * k["mw"]
    lip_h = np.where(sn > 0, 0.034, 0.04) * k["lips"] * (1 - 0.25 * np.abs(c) ** 4)
    bow = np.where(sn > 0, -0.004 * np.exp(-0.5 * (c / 0.12) ** 2), 0.0)  # cupid's bow dip at the centre
    lips = {3: np.column_stack([1.06 * w * c, mz + (lip_h + bow) * sn]),        # vermilion border
            4: np.column_stack([1.03 * w * c, mz + 0.5 * lip_h * sn]),          # fullest part of the lip
            5: np.column_stack([w * c, mz + 0.0015 * sn])}                      # seam
    start = np.array([(v.co.x, v.co.z) for v in cyc])
    prev, rings = cyc, []
    for r in range(1, MOUTH_RINGS + 1):
        xz = lips[r] if r in lips else start + (lips[3] - start) * (r / 3)
        pts = _surface(xz, k, n)
        if r == 4:
            pts[:, 1] -= 0.004 * k["lips"] * np.abs(sn) ** 0.5  # lips roll forward
        if r == MOUTH_RINGS:
            pts[:, 1] += 0.006  # the seam tucks between the lips
        prev = _ring(bm, prev, pts, 0, [])
        rings.append(prev)
    seam = np.array([tuple(v.co) for v in prev])
    ys = float(seam[:, 1].min())
    bag_rings = [
        np.column_stack([seam[:, 0] * 0.97, seam[:, 1] + 0.010, mz + (seam[:, 2] - mz) * 1.5]),
        np.column_stack([0.085 * k["mw"] * c, np.full_like(c, ys + 0.045), mz + 0.032 * sn]),
        np.column_stack([0.075 * k["mw"] * c, np.full_like(c, ys + 0.095), mz - 0.006 + 0.04 * sn]),
        np.column_stack([0.045 * k["mw"] * c, np.full_like(c, ys + 0.14), mz + 0.022 * sn]),
    ]
    bag = []
    for i, pts in enumerate(bag_rings):
        prev = _ring(bm, prev, pts, 1 if i == 0 else 2, info["mat_faces"][1 if i == 0 else 2])
        bag.append(prev)
    _cap(bm, prev, (0, ys + 0.155, mz), 2)
    info["rings"]["mouth"] = rings
    info["bag"]["mouth"] = bag
    info.update(ys=ys, mz=mz, ez=ez, n=n)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.verts.index_update()
    info["rings"] = {name: [[v.index for v in r] for r in rs] for name, rs in info["rings"].items()}
    info["bag"] = {name: ([[v.index for v in r] for r in b] if name == "mouth" else [v.index for v in b])
                   for name, b in info["bag"].items()}
    return bm, info


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
    prof = [(0.0, R)]
    for i in range(1, 12):
        a = math.pi * i / 12 * (math.pi - math.asin(lim / R)) / math.pi
        prof.append((R * math.sin(a), R * math.cos(a)))
    prof.append((lim, ly))
    top = math.asin(lim / rc)
    for i in range(1, 4):
        a = top * (1 - i / 4)
        prof.append((rc * math.sin(a), cy - rc * math.cos(a)))
    prof.append((0.0, cy - rc))
    v1, f1, row = _lathe(prof, 24)
    fm = [1 if r >= 12 else 0 for r in row]          # 0 sclera, 1 cornea
    iris = [(lim * (1 - i / 5), ly + 0.002 + 0.0012 * (i / 5) ** 2) for i in range(5)] + [(0.0, ly + 0.0034)]
    v2, f2, row2 = _lathe(iris, 24)
    base = len(v1)
    verts = v1 + v2
    faces = f1 + [[i + base for i in f] for f in f2]
    fm += [3 if r >= 3 else 2 for r in row2]          # 2 iris, 3 pupil (inner 40 %)
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
        at = 0.0
        for w in widths:
            w *= scale
            mid = np.interp(at + w / 2, arc, xs)
            at += w
            ang = math.atan(2 * c * mid)
            pts, fs = _rounded_box((w * 0.95, 0.012 if w < 0.03 else 0.02, h))
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
    return verts, faces, tooth_faces


def _ear(side, centre, k):
    """Closed ear shell: helix rim, antihelix ridge, concha bowl and lobe on the front; flat back on the head."""
    S, J = 18, 6
    hh, ww = 0.135 * k["ears"], 0.068 * k["ears"]

    def outline(th):
        lobe = np.clip(-np.sin(th), 0, 1)
        return ww * np.cos(th) * (1 - 0.3 * lobe), hh * np.sin(th)

    def front_h(r, th):
        lobe = np.clip(-np.sin(th), 0, 1) ** 2
        rim = 0.026 * np.exp(-0.5 * ((r - 0.93) / 0.06) ** 2) + 0.018 * np.exp(-0.5 * ((r - 0.62) / 0.08) ** 2)
        bowl = -0.012 * (1 - _smooth(0.35, 0.5, r))
        return 0.012 + (rim + bowl) * (1 - lobe) + 0.004 * lobe

    th = np.linspace(0, 2 * math.pi, S, endpoint=False)
    bx, bz = outline(th)
    verts, faces = [], []
    rows = {"f": [], "b": []}
    for kind in ("f", "b"):
        rows[kind].append([len(verts)])
        verts.append((0.012 if kind == "f" else -0.008, 0.0, 0.0))
        for j in range(1, J):
            r = j / J
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
    }
    return {k: _material("%s_%s" % (prefix, k), v) for k, v in spec.items()}


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
        "brows": _g(ax, z, 0.12, 0.6 * f, 0.06, 0.03) * front,
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
        v, fc, _ = _lathe([(0, 0.004)] + [(0.004 * math.sin(math.pi * i / 4), 0.004 * math.cos(math.pi * i / 4)) for i in range(1, 4)] + [(0, -0.004)], 8)
        n0 = len(wv)
        wv += [(cpos[0] + a * 1.4, cpos[1] + b, cpos[2] + c * 1.2) for a, b, c in v]
        wf += [[i + n0 for i in q] for q in fc]
        wm += [0] * len(fc)
        low = margin[margin[:, 2] < ez + k["tilt"] * 0.5]
        low = low[np.argsort(low[:, 0])]
        if len(low) >= 2:
            toward = centre - low
            toward /= np.linalg.norm(toward, axis=1, keepdims=True)
            n0 = len(wv)
            wv += (low + [0, -0.0005, 0]).tolist() + (low + toward * 0.0025).tolist()
            m = len(low)
            wf += [[n0 + i, n0 + i + 1, n0 + m + i + 1, n0 + m + i] for i in range(m - 1)]
            wm += [1] * (m - 1)
    parts["eyewet"] = _object(prefix + "_eyewet", wv, wf, [mats["Caruncle"], mats["TearLine"]], wm)
    # teeth, gums, tongue
    ys, mz = info["ys"], info["mz"]
    tv, tf, nt = _arch(ys + 0.012, mz + 0.03, mz - 0.004, k, mz + 0.038, True)
    parts["teeth_U"] = _object(prefix + "_teeth_U", tv, tf, [mats["Teeth"], mats["Gums"]], [0] * nt + [1] * (len(tf) - nt))
    tv, tf, nt = _arch(ys + 0.016, mz - 0.008, mz - 0.036, k, mz - 0.042, False)
    parts["teeth_L"] = _object(prefix + "_teeth_L", tv, tf, [mats["Teeth"], mats["Gums"]], [0] * nt + [1] * (len(tf) - nt))
    pts, fs = _rounded_box((0.09 * k["mw"], 0.11, 0.034), 3)
    pts = pts + [0, ys + 0.085, mz - 0.022]
    parts["tongue"] = _object(prefix + "_tongue", pts, fs, [mats["Tongue"]])
    # ears: on the skull side, brow to nose base, a little behind the head centre
    ev, ef = [], []
    co = np.array([tuple(v.co) for v in me.vertices])
    ze = (0.585 + 0.30) / 2 * k["f"] + 0.01
    near = co[(np.abs(co[:, 2] - ze) < 0.03) & (np.abs(co[:, 1] - 0.03) < 0.04)]
    side_x = float(np.abs(near[:, 0]).max()) if len(near) else 0.3
    for s in (1, -1):
        v, fc = _ear(s, np.array([s * (side_x - 0.004), 0.03, ze]), k)
        n0 = len(ev)
        ev += v
        ef += [[i + n0 for i in q] for q in fc]
    parts["ears"] = _object(prefix + "_ears", ev, ef, [mats["Skin"]])
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
    return {"summary": summary, **s, "scene": bridge.dump(p["scene"])}
