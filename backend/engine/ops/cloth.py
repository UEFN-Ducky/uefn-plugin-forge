"""Cloth: a pinned verlet sheet collides with sphere proxies, then bakes to a bone chain or to shape keys.

Stdlib only (testable without Blender). Forge centimetres, Z-up, world space. Rotations use Forge's
Rz*Ry*Rx Euler degrees, each bone's rotation relative to its parent, so the clip plays in backend.rig.
"""
import math

GRAVITY = 981.0
SUB, ITER, PREROLL, THICK = 4, 10, 45, 1.0
PRESETS = {
    "cape": dict(length=90, width=50, segs=6, cols=7, along=(0, 0, -1), across=(1, 0, 0), wind=0.0),
    "scarf": dict(length=60, width=14, segs=6, cols=3, along=(0, 0, -1), across=(1, 0, 0), wind=0.0),
    "skirt": dict(length=45, width=70, segs=4, cols=9, along=(0, 0, -1), across=(1, 0, 0), wind=0.0),
    "flag": dict(length=90, width=60, segs=6, cols=7, along=(1, 0, 0), across=(0, 0, 1), wind=1.0),
}


def preset_of(name):
    key = str(name or "cape").lower()
    if key not in PRESETS:
        import difflib
        close = difflib.get_close_matches(key, PRESETS, n=1)
        raise ValueError("No cloth preset %r.%s Presets: %s" % (key, " Did you mean %r?" % close[0] if close else "", ", ".join(PRESETS)))
    return dict(PRESETS[key], name=key)


def _grid(pre, pin):
    rows, cols = pre["segs"] + 1, pre["cols"]
    dl, dw = pre["length"] / pre["segs"], pre["width"] / (cols - 1)
    a, c = pre["along"], pre["across"]
    return [[[pin[i] + a[i] * r * dl + c[i] * (k - (cols - 1) / 2) * dw for i in range(3)] for k in range(cols)] for r in range(rows)]


def simulate(p):
    """{rest: [r][c] points, frames: [f][r][c] points, clearance, pre} for the pinned first row."""
    pre = preset_of(p.get("preset"))
    pin = [float(v) for v in p["pin"]]
    rows, cols = pre["segs"] + 1, pre["cols"]
    fps = int(p.get("fps") or 30)
    frames = max(2, min(240, int(p.get("frames") or 32)))
    sway = float(p.get("sway", 4.0))
    spheres = [[float(v) for v in s] for s in p.get("spheres") or []]
    rest = _grid(pre, pin)
    pos = [[list(q) for q in row] for row in rest]
    old = [[list(q) for q in row] for row in rest]
    links = []
    for r in range(rows):
        for c in range(cols):
            for dr, dc in ((1, 0), (0, 1), (1, 1), (1, -1), (2, 0)):
                r2, c2 = r + dr, c + dc
                if r2 < rows and 0 <= c2 < cols:
                    links.append((r, c, r2, c2, math.dist(rest[r][c], rest[r2][c2])))
    dt = 1.0 / fps / SUB
    clearance = [1e9]

    def step(t):
        wind = pre["wind"] * (300 + 250 * math.sin(t * 3.0))
        for r in range(1, rows):
            for c in range(cols):
                q, o = pos[r][c], old[r][c]
                vx, vy, vz = (q[0] - o[0]) * 0.995, (q[1] - o[1]) * 0.995, (q[2] - o[2]) * 0.995
                o[:] = q
                q[0] += vx
                q[1] += vy + wind * dt * dt * (1 + 0.3 * math.sin(r * 0.8 + t * 5.0))
                q[2] += vz - GRAVITY * dt * dt
        shift = sway * math.sin(2 * math.pi * t / (frames / fps))
        for c in range(cols):
            pos[0][c] = [rest[0][c][0] + shift, rest[0][c][1], rest[0][c][2]]
        for it in range(ITER):
            for r, c, r2, c2, length in links:
                a, b = pos[r][c], pos[r2][c2]
                d = [b[i] - a[i] for i in range(3)]
                dist = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) or 1e-9
                k = (dist - length) / dist
                wa, wb = (0.0 if r == 0 else 1.0), (0.0 if r2 == 0 else 1.0)
                if wa + wb == 0:
                    continue
                wa, wb = wa / (wa + wb), wb / (wa + wb)
                for i in range(3):
                    a[i] += d[i] * k * wa
                    b[i] -= d[i] * k * wb
            if it % 2 == 0:  # ponytail: collide every other pass, the last pass always collides
                continue
            for r in range(1, rows):
                for c in range(cols):
                    q = pos[r][c]
                    for s in spheres:
                        d = [q[0] - s[0], q[1] - s[1], q[2] - s[2]]
                        dist = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
                        if dist < s[3] + THICK:
                            if dist < 1e-6:
                                d, dist = [0.0, 1.0, 0.0], 1.0
                            for i in range(3):
                                q[i] = s[i] + d[i] / dist * (s[3] + THICK)

    t = 0.0
    for _ in range(PREROLL * SUB):
        step(t)
        t += dt
    out = []
    for _ in range(frames):
        for _ in range(SUB):
            step(t)
            t += dt
        out.append([[[round(v, 4) for v in q] for q in row] for row in pos])
        for row in pos[1:]:
            for q in row:
                for s in spheres:
                    clearance[0] = min(clearance[0], math.dist(q, s[:3]) - s[3])
    return {"rest": rest, "frames": out, "clearance": clearance[0] if spheres else None, "pre": pre}


# ---- baking -----------------------------------------------------------------


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _swing(u, v):
    """Smallest rotation taking direction u to direction v."""
    def unit(w):
        n = math.sqrt(sum(c * c for c in w)) or 1.0
        return [c / n for c in w]
    u, v = unit(u), unit(v)
    c = max(-1.0, min(1.0, sum(a * b for a, b in zip(u, v))))
    ax = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    s = math.sqrt(sum(a * a for a in ax))
    if s < 1e-9:
        return [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]] if c > 0 else [[-1.0, 0, 0], [0, -1.0, 0], [0, 0, 1.0]]
    x, y, z = (a / s for a in ax)
    t, ca, sa = 1 - c, c, s
    return [[t * x * x + ca, t * x * y - sa * z, t * x * z + sa * y],
            [t * x * y + sa * z, t * y * y + ca, t * y * z - sa * x],
            [t * x * z - sa * y, t * y * z + sa * x, t * z * z + ca]]


def _euler(m):
    """Rz*Ry*Rx degrees of a rotation matrix."""
    if abs(m[2][0]) < 0.99999:
        return [math.degrees(math.atan2(m[2][1], m[2][2])), math.degrees(-math.asin(m[2][0])), math.degrees(math.atan2(m[1][0], m[0][0]))]
    return [math.degrees(math.atan2(-m[1][2], m[1][1])), math.degrees(-math.asin(max(-1.0, min(1.0, m[2][0])))), 0.0]


def _centers(grid):
    return [[sum(q[i] for q in row) / len(row) for i in range(3)] for row in grid]


def bones_of(sim, prefix="cloth"):
    """The bone chain at rest: one bone per row segment."""
    c = _centers(sim["rest"])
    return [{"name": "%s_%02d" % (prefix, i + 1), "parent": None if i == 0 else "%s_%02d" % (prefix, i),
             "head": [round(v, 3) for v in c[i]], "tail": [round(v, 3) for v in c[i + 1]]} for i in range(len(c) - 1)]


def bake_bones(sim, prefix="cloth"):
    """({bone: {rot, loc?}}, bones): swing of every segment per frame, root also follows the pin."""
    bones = bones_of(sim, prefix)
    rest = _centers(sim["rest"])
    tracks = {b["name"]: {"rot": []} for b in bones}
    tracks[bones[0]["name"]]["loc"] = []
    for f, grid in enumerate(sim["frames"]):
        c = _centers(grid)
        parent = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
        for i, b in enumerate(bones):
            world = _swing([rest[i + 1][k] - rest[i][k] for k in range(3)], [c[i + 1][k] - c[i][k] for k in range(3)])
            local = _mul([list(r) for r in zip(*parent)], world)
            tracks[b["name"]]["rot"].append([f, [round(v, 3) for v in _euler(local)], "linear"])
            parent = world
        tracks[bones[0]["name"]]["loc"].append([f, [round(c[0][k] - rest[0][k], 3) for k in range(3)], "linear"])
    return tracks, bones


def weights_for(sim, verts):
    """Per-vertex weights to the chain by position along the sheet: [{bone index: weight}]."""
    pre, rest = sim["pre"], sim["rest"]
    a, segs = pre["along"], pre["segs"]
    origin = _centers(rest)[0]
    seg_len = pre["length"] / segs
    out = []
    for v in verts:
        s = sum((v[i] - origin[i]) * a[i] for i in range(3)) / seg_len
        s = max(0.0, min(float(segs), s))
        j = min(int(s), segs - 1)
        frac = s - j
        out.append({j: 1.0} if j == segs - 1 and frac >= 1.0 - 1e-9 else {j: 1.0 - frac, j + 1: frac} if j + 1 < segs else {j: 1.0})
    return out


def bake_shapes(sim, verts, max_shapes=32):
    """({shape name: per-vertex world delta}, {frame: shape name}) sampled over the clip."""
    pre, rest = sim["pre"], sim["rest"]
    rows, cols = len(rest), pre["cols"]
    origin = _centers(rest)[0]
    seg_len, a, c = pre["length"] / pre["segs"], pre["along"], pre["across"]
    coords = []
    for v in verts:
        r = max(0.0, min(rows - 1.0, sum((v[i] - origin[i]) * a[i] for i in range(3)) / seg_len))
        k = max(0.0, min(cols - 1.0, sum((v[i] - origin[i]) * c[i] for i in range(3)) / pre["width"] * (cols - 1) + (cols - 1) / 2))
        coords.append((r, k))
    step = max(1, math.ceil(len(sim["frames"]) / max_shapes))
    shapes, names = {}, {}
    for f in range(0, len(sim["frames"]), step):
        grid = sim["frames"][f]
        deltas = []
        for r, k in coords:
            r0, k0 = min(int(r), rows - 2), min(int(k), cols - 2)
            fr, fk = r - r0, k - k0
            d = [0.0, 0.0, 0.0]
            for dr, dk, w in ((0, 0, (1 - fr) * (1 - fk)), (1, 0, fr * (1 - fk)), (0, 1, (1 - fr) * fk), (1, 1, fr * fk)):
                for i in range(3):
                    d[i] += w * (grid[r0 + dr][k0 + dk][i] - rest[r0 + dr][k0 + dk][i])
            deltas.append([round(x, 3) for x in d])
        name = "cloth_%02d" % f
        shapes[name], names[f] = deltas, name
    return shapes, names


def op_cloth(p):
    sim = simulate(p)
    pre = sim["pre"]
    out = {"preset": pre["name"], "frame_count": len(sim["frames"]), "clearance": sim["clearance"],
           "rest": sim["rest"], "cols": pre["cols"], "segs": pre["segs"]}
    mode = str(p.get("bake_to") or "bones")
    verts = p.get("verts")
    if verts is None:  # no cloth object: hand back the sheet itself as a quad grid
        rows, cols = pre["segs"] + 1, pre["cols"]
        verts = [q for row in sim["rest"] for q in row]
        out["grid_faces"] = [[r * cols + c, r * cols + c + 1, (r + 1) * cols + c + 1, (r + 1) * cols + c]
                             for r in range(rows - 1) for c in range(cols - 1)]
        out["grid_verts"] = verts
    if mode == "bones":
        tracks, bones = bake_bones(sim, str(p.get("prefix") or "cloth"))
        out.update(bones=bones, tracks=tracks, weights=weights_for(sim, verts))
    elif mode == "shapes":
        shapes, names = bake_shapes(sim, verts)
        out.update(shapes=shapes, shape_frames={str(k): v for k, v in names.items()})
    else:
        raise ValueError("bake_to %r is not bones or shapes" % mode)
    return out
