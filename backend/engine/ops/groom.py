"""Hair strands, hair cards and fur shells. Pure stdlib maths (testable without Blender);
bpy is only touched, lazily, to mirror the strands as a Blender hair-curves object.

Everything is Forge centimetres, Z-up, -Y front, world space. The host (hq/groom.py) resolves the
region into world triangles and the object's mesh into plain lists, so ops here are JSON in, JSON out.
"""
import bisect
import math
import random
import struct
import zlib
from pathlib import Path

MAX_STRANDS = 150000
STYLES = {
    "short": dict(density=55, length=6, clump=0.2, curl=0.0, frizz=0.05, droop=0.2, points=6, region="top"),
    "long": dict(density=55, length=35, clump=0.3, curl=0.1, frizz=0.1, droop=1.0, points=10, region="top"),
    "ponytail": dict(density=55, length=40, clump=0.5, curl=0.05, frizz=0.05, droop=1.0, points=10, region="top", gather=True),
    "afro": dict(density=60, length=14, clump=0.1, curl=1.0, frizz=0.4, droop=0.0, points=8, region="top"),
    "fur": dict(density=100, length=3, clump=0.0, curl=0.0, frizz=0.1, droop=0.3, points=4, region="all"),
    "beard": dict(density=50, length=8, clump=0.3, curl=0.3, frizz=0.15, droop=0.7, points=6, region="jaw"),
    "mane": dict(density=120, length=28, clump=0.6, curl=0.15, frizz=0.12, droop=0.1, points=8, region="top"),
}
COLORS = {"black": (0.03, 0.025, 0.02), "brown": (0.22, 0.12, 0.06), "auburn": (0.35, 0.13, 0.07),
          "blonde": (0.8, 0.6, 0.28), "red": (0.55, 0.12, 0.05), "white": (0.9, 0.9, 0.88),
          "gray": (0.5, 0.5, 0.5), "blue": (0.1, 0.25, 0.8), "pink": (0.9, 0.35, 0.6)}


def rgb(color):
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        return tuple(float(c) for c in color[:3])
    name = str(color or "brown").lower()
    if name not in COLORS:
        import difflib
        close = difflib.get_close_matches(name, COLORS, n=1)
        raise ValueError("No hair color %r.%s Colors: %s, or [r,g,b]" % (name, " Did you mean %r?" % close[0] if close else "", ", ".join(COLORS)))
    return COLORS[name]


def style_of(p):
    name = str(p.get("style") or "short").lower()
    if name not in STYLES:
        import difflib
        close = difflib.get_close_matches(name, STYLES, n=1)
        raise ValueError("No hair style %r.%s Styles: %s" % (name, " Did you mean %r?" % close[0] if close else "", ", ".join(STYLES)))
    st = dict(STYLES[name])
    for key in ("density", "length", "clump", "curl", "frizz"):
        if p.get(key) is not None:
            st[key] = float(p[key])
    st["name"] = name
    return st


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _add(a, b, s=1.0):
    return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)


def region_area(tris):
    return sum(0.5 * math.sqrt(sum(c * c for c in _cross(_sub(t[1], t[0]), _sub(t[2], t[0])))) for t in tris)


def sample_roots(tris, count, rng):
    """count points spread over the triangles by area: [(point, outward normal)]."""
    cum, total, normals = [], 0.0, []
    for t in tris:
        c = _cross(_sub(t[1], t[0]), _sub(t[2], t[0]))
        area = 0.5 * math.sqrt(c[0] ** 2 + c[1] ** 2 + c[2] ** 2)
        total += area
        cum.append(total)
        normals.append(_norm(c))
    out = []
    for _ in range(count):
        k = min(bisect.bisect_left(cum, rng.random() * total), len(tris) - 1)
        a, b, c = tris[k]
        u, v = rng.random(), rng.random()
        if u + v > 1.0:
            u, v = 1.0 - u, 1.0 - v
        out.append(((a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v, a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v,
                     a[2] + (b[2] - a[2]) * u + (c[2] - a[2]) * v), normals[k]))
    return out


def _walk(root, n, length, st, rng, tie):
    points, count = st["points"], st["points"]
    pts = [root]
    if tie:
        lead = max(2, count // 3)
        for k in range(1, lead + 1):
            s = k / lead
            p = _add((root[0] + (tie[0] - root[0]) * s, root[1] + (tie[1] - root[1]) * s, root[2] + (tie[2] - root[2]) * s), n, math.sin(math.pi * s) * 2.0)
            pts.append(p)
        rest = count - 1 - lead
        step = length / max(1, rest)
        off = ((rng.random() - 0.5) * 1.6, (rng.random() - 0.5) * 1.6)
        for k in range(1, rest + 1):
            s = k / max(1, rest)
            pts.append((tie[0] + off[0] * s, tie[1] + off[1] * s, tie[2] - step * k))
        return pts
    d, step = n, length / (points - 1)
    if st.get("name") == "mane":  # out along the normal, then swept back (+Y) with lift on the crown and neck
        for k in range(1, points):
            t = k / (points - 1)
            d = _norm((d[0], d[1] + 0.35, d[2] + 0.12 * max(0.0, n[2]) - 0.04))
            pts.append(_add(pts[-1], d, step))
        return pts
    for k in range(1, points):
        t = k / (points - 1)
        d = _norm((d[0], d[1], d[2] - st["droop"] * 0.35 * (0.3 + t)))
        pts.append(_add(pts[-1], d, step))
    return pts


def build_strands(p):
    """List of strands (each a list of points, root first) for p = {tris, style, seed, density, ...}."""
    st = style_of(p)
    tris = [[tuple(v) for v in t] for t in p["tris"]]
    if not tris:
        raise ValueError("Region has no triangles. Regions: all, top, jaw, back, or face indices")
    rng = random.Random(int(p.get("seed", 1)))
    count = int(round(region_area(tris) * st["density"]))
    if count > MAX_STRANDS:
        count = MAX_STRANDS
    roots = sample_roots(tris, count, rng)
    zmid = sorted(r[0][2] for r in roots)[len(roots) // 2] if roots else 0.0
    tie = None
    if st.get("gather") and roots:
        xs, ys, zs = [r[0][0] for r in roots], [r[0][1] for r in roots], [r[0][2] for r in roots]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        radius = max(max(xs) - min(xs), max(ys) - min(ys)) / 2
        tie = (cx, cy + radius, max(zs) - 0.15 * (max(zs) - min(zs)))
    strands = []
    for root, n in roots:
        length = st["length"] * (0.85 + 0.3 * rng.random())
        if st["name"] == "mane" and n[1] < -0.3:  # fuller at the throat, shorter at the face edge
            length *= 1.25 if root[2] < zmid else 0.6
        strands.append(_walk(root, n, length, st, rng, tie))
    last = st["points"] - 1
    if st["clump"] > 0 and strands:  # pull tips toward the mean strand of a 1.5 cm cell on the region
        cells = {}
        for s in strands:
            cells.setdefault((int(s[0][0] // 1.5), int(s[0][1] // 1.5), int(s[0][2] // 1.5)), []).append(s)
        for group in cells.values():
            mean = [tuple(sum(s[k][i] for s in group) / len(group) for i in range(3)) for k in range(last + 1)]
            for s in group:
                for k in range(1, last + 1):
                    w = st["clump"] * k / last
                    s[k] = _add(s[k], _sub(mean[k], s[k]), w)
    for s in strands:
        phase = rng.random() * 6.283
        for k in range(1, last + 1):
            t = k / last
            if st["curl"] > 0:
                tan = _norm(_sub(s[min(k + 1, last)], s[k - 1]))
                u = _norm(_cross(tan, (0.0, 0.0, 1.0))) if abs(tan[2]) < 0.99 else (1.0, 0.0, 0.0)
                v = _cross(tan, u)
                r, a = st["curl"] * st["length"] * 0.08 * t, phase + k * 1.9
                s[k] = _add(_add(s[k], u, r * math.cos(a)), v, r * math.sin(a))
            if st["frizz"] > 0:
                f = st["frizz"] * st["length"] * 0.03 * t
                s[k] = (s[k][0] + (rng.random() - 0.5) * f, s[k][1] + (rng.random() - 0.5) * f, s[k][2] + (rng.random() - 0.5) * f)
    return strands


def _blender_curves(strands, name, color=None):
    """Mirror the strands as a Blender hair-curves object; False when bpy is not available."""
    try:
        import bpy
        curves = bpy.data.hair_curves.new(name)
        curves.add_curves([len(s) for s in strands])
        curves.attributes["position"].data.foreach_set("vector", [c for s in strands for pt in s for c in (x * 0.01 for x in pt)])
        obj = bpy.data.objects.new(name, curves)
        if color is not None:  # strands carry their base color, not the default grey
            mat = bpy.data.materials.new(name + "_mat")
            mat.diffuse_color = (*color, 1.0)
            mat.use_nodes = True
            mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*color, 1.0)
            curves.materials.append(mat)
        bpy.context.scene.collection.objects.link(obj)
        obj["forge_hair"] = True  # not a mesh: the mesh bridge ignores it
        return True
    except Exception:
        return False


def op_hair(p):
    strands = build_strands(p)
    st = style_of(p)
    mirrored = _blender_curves(strands, str(p.get("name") or "Hair"), rgb(p.get("color"))) if p.get("curves", True) else False
    return {"count": len(strands), "points": sum(len(s) for s in strands), "style": st["name"],
            "area": round(region_area(p["tris"]), 2), "density": st["density"], "blender_curves": mirrored,
            "capped": len(strands) >= MAX_STRANDS}


# ---- cards ------------------------------------------------------------------


def atlas_size(value):
    text = str(value if value is not None else "1K").upper()
    size = int(text[:-1]) * 1024 if text.endswith("K") else int(text)
    return max(128, min(4096, size))


def _png(path, w, h, rows, channels):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if channels == 4 else 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def bake_atlas(folder, size, color, variants, seed=1, stem="hair", root=0.55, inner=1.0):
    """color+alpha atlas (RGBA) and data atlas (R root-tip, G depth, B strand id). Returns (cell grid, color file, data file)."""
    rng = random.Random(seed)
    grid = max(1, math.ceil(math.sqrt(variants)))
    cs = size // grid
    base = rgb(color)
    col_rows = [bytearray(size * 4) for _ in range(size)]
    dat_rows = [bytearray(size * 3) for _ in range(size)]
    for cell in range(grid * grid):
        ox, oy = (cell % grid) * cs, (cell // grid) * cs
        count = max(4, cs // 5)
        hw0 = max(1.5, cs / count * 0.9)
        strands = [(rng.random() * cs, rng.random() * 6.283, rng.random()) for _ in range(count)]
        for x0, phase, ident in sorted(strands, key=lambda s: s[2]):
            for y in range(cs):
                v = y / max(1, cs - 1)
                xc = x0 + cs * 0.04 * math.sin(v * 6 + phase)
                hw = hw0 * (1 - 0.75 * v)
                col, dat = col_rows[oy + y], dat_rows[oy + y]
                for x in range(max(0, int(xc - hw)), min(cs, int(xc + hw) + 1)):
                    d = abs(x - xc) / hw
                    if d > 1:
                        continue
                    alpha = 255 if d < 0.85 else int((1 - d) / 0.15 * 255)
                    i = (ox + x) * 4
                    if alpha < col[i + 3]:
                        continue
                    shade = (root + (1 - root) * v) * (0.9 + 0.2 * ident) * (1 - 0.25 * d) * (inner if cell % 2 == 0 else 1.0)
                    col[i:i + 4] = bytes([min(255, int(base[0] * shade * 255)), min(255, int(base[1] * shade * 255)),
                                          min(255, int(base[2] * shade * 255)), alpha])
                    j = (ox + x) * 3
                    dat[j:j + 3] = bytes([int(255 * v), int(255 * (1 - d)), int(255 * ident)])
    Path(folder).mkdir(parents=True, exist_ok=True)
    _png(Path(folder) / (stem + "_color.png"), size, size, col_rows, 4)
    _png(Path(folder) / (stem + "_data.png"), size, size, dat_rows, 3)
    return grid, stem + "_color.png", stem + "_data.png"


def _resample(pts, count):
    lengths = [0.0]
    for a, b in zip(pts, pts[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1] or 1.0
    out, j = [], 0
    for k in range(count):
        s = total * k / (count - 1)
        while j < len(pts) - 2 and lengths[j + 1] < s:
            j += 1
        span = (lengths[j + 1] - lengths[j]) or 1.0
        t = min(1.0, max(0.0, (s - lengths[j]) / span))
        out.append(tuple(pts[j][i] + (pts[j + 1][i] - pts[j][i]) * t for i in range(3)))
    return out


def op_hair_cards(p):
    """Cluster strands, build one strip per cluster along its mean strand, bake the atlas."""
    strands = build_strands(p)
    cards = max(1, min(int(p.get("cards") or 120), len(strands)))
    budget = int(p.get("budget") or 2800)
    mane = style_of(p)["name"] == "mane"
    segs = max(1, min(16, budget // (cards * 2)))
    size = atlas_size(p.get("atlas"))
    cx = sum(s[0][0] for s in strands) / len(strands)
    cy = sum(s[0][1] for s in strands) / len(strands)
    order = sorted(strands, key=lambda s: math.atan2(s[0][1] - cy, s[0][0] - cx))
    if mane:  # a ring: three height bands (throat/cheek/crown), each swept around the head, so cards cover the band and not one slice
        zs = sorted(s[0][2] for s in strands)
        cut = [zs[len(zs) // 3], zs[2 * len(zs) // 3]]
        order = sorted(order, key=lambda s: sum(s[0][2] > c for c in cut))  # stable: angle order kept inside a band
    groups = [[] for _ in range(cards)]
    for i, s in enumerate(order):
        groups[i * cards // len(order)].append(s)
    variants = min(cards, 16)
    grid, color_file, data_file = bake_atlas(p["folder"], size, p.get("color"), variants, int(p.get("seed", 1)), str(p.get("stem") or "hair"),
                                            root=0.3 if mane else 0.55, inner=0.55 if mane else 1.0)  # manes: dark root, dark inner (even cells) under tawny outer cards
    cs = 1.0 / grid
    verts, faces, uvs = [], [], []
    for ci, group in enumerate(groups):
        n = len(group[0])
        mean = [tuple(sum(s[k][i] for s in group) / len(group) for i in range(3)) for k in range(n)]
        line = _resample(mean, segs + 1)
        out = _norm((mean[0][0] - cx, mean[0][1] - cy, 0.0))
        layer = ci % 2 if mane else 0  # mane: inner cards short and tucked in, outer cards long and wide
        if mane:
            scale = 0.7 + 0.3 * layer
            line = [tuple(line[0][i] + (q[i] - line[0][i]) * scale + out[i] * 1.2 * layer for i in range(3)) for q in line]
        xs, ys, zs = zip(*[s[0] for s in group])
        width = min(8.0, max(1.2, math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2)))
        if mane:
            width = min(10.0, width * (1.0 + 0.4 * layer))
        cell = ci % variants
        u0, u1 = (cell % grid) * cs + 0.01 * cs, (cell % grid + 1) * cs - 0.01 * cs
        top = (cell // grid) * cs
        base = len(verts)
        for j, pt in enumerate(line):
            t = j / segs
            tan = _norm(_sub(line[min(j + 1, segs)], line[max(j - 1, 0)]))
            side = _cross(tan, out)
            side = _norm(side) if sum(c * c for c in side) > 1e-6 else _norm(_cross(tan, (1.0, 0.0, 0.0)))
            half = width * (1 - 0.65 * t ** 1.2) / 2
            verts.append([round(pt[i] - side[i] * half, 3) for i in range(3)])
            verts.append([round(pt[i] + side[i] * half, 3) for i in range(3)])
        for j in range(segs):
            a, b, c, d = base + 2 * j, base + 2 * j + 1, base + 2 * j + 2, base + 2 * j + 3
            v0, v1 = 1 - (top + j / segs * cs), 1 - (top + (j + 1) / segs * cs)
            faces += [[a, c, d], [a, d, b]]
            uvs += [[[u0, v0], [u0, v1], [u1, v1]], [[u0, v0], [u1, v1], [u1, v0]]]
    return {"verts": verts, "faces": faces, "uvs": uvs, "tris": len(faces), "cards": cards, "segs": segs, "atlas": size,
            "color_file": color_file, "data_file": data_file, "over_budget": len(faces) > budget}


# ---- fur shells -------------------------------------------------------------


def _noise_png(path, size, color, seed):
    rng = random.Random(seed)
    grid = [[rng.random() for _ in range(size)] for _ in range(size)]
    for _ in range(2):  # two wrapped box blurs give soft clumps that still tile
        grid = [[(grid[y][x] + grid[y][(x + 1) % size] + grid[(y + 1) % size][x] + grid[y][x - 1] + grid[y - 1][x]) / 5
                 for x in range(size)] for y in range(size)]
    lo = min(min(r) for r in grid)
    hi = max(max(r) for r in grid) or 1.0
    base = [int(c * 255) for c in rgb(color)]
    rows = [b"".join(bytes(base + [int(255 * (v - lo) / ((hi - lo) or 1.0))]) for v in row) for row in grid]
    _png(path, size, size, rows, 4)


def _vertex_normals(verts, faces):
    acc = [[0.0, 0.0, 0.0] for _ in verts]
    for f in faces:
        n = [0.0, 0.0, 0.0]
        for i in range(len(f)):  # Newell
            a, b = verts[f[i]], verts[f[(i + 1) % len(f)]]
            n[0] += (a[1] - b[1]) * (a[2] + b[2])
            n[1] += (a[2] - b[2]) * (a[0] + b[0])
            n[2] += (a[0] - b[0]) * (a[1] + b[1])
        for i in f:
            for k in range(3):
                acc[i][k] += n[k]
    return [_norm(a) for a in acc]


def _tris(faces):
    return sum(len(f) - 2 for f in faces)


def decimate(verts, faces, uvs, target):
    """Vertex-clustering proxy of at most target tris: (verts, faces, uvs, rep). rep[i] = the original vertex a proxy
    vertex sits on, so per-vertex data (skin weights) transfers by index. Proxy verts stay on the original surface."""
    lo = [min(v[i] for v in verts) for i in range(3)]
    span = max(max(v[i] for v in verts) - lo[i] for i in range(3)) or 1.0
    size = span / math.sqrt(max(target, 1))
    for _ in range(14):
        cell = [tuple(int((v[i] - lo[i]) // size) for i in range(3)) for v in verts]
        ids = {}
        for c in cell:
            ids.setdefault(c, len(ids))
        vmap = [ids[c] for c in cell]
        seen, out_faces, out_uvs = set(), [], []
        for f, uv in zip(faces, uvs):
            ring = [(vmap[i], k) for k, i in enumerate(f)]
            ring = [r for j, r in enumerate(ring) if r[0] != ring[j - 1][0]]
            if len(ring) < 3 or len({r[0] for r in ring}) < 3:
                continue
            key = frozenset(r[0] for r in ring)
            if key in seen:
                continue
            seen.add(key)
            out_faces.append([r[0] for r in ring])
            out_uvs.append([uv[r[1]] for r in ring])
        tris = _tris(out_faces)
        if tris <= target:
            break
        size *= max(1.05, math.sqrt(tris / target))
    n = len(ids)
    sums, count = [[0.0, 0.0, 0.0] for _ in range(n)], [0] * n
    for v, m in zip(verts, vmap):
        count[m] += 1
        for i in range(3):
            sums[m][i] += v[i]
    best = [None] * n
    for k, (v, m) in enumerate(zip(verts, vmap)):
        d = sum((v[i] - sums[m][i] / count[m]) ** 2 for i in range(3))
        if best[m] is None or d < best[m][0]:
            best[m] = (d, k)
    rep = [b[1] for b in best]
    return [list(verts[r]) for r in rep], out_faces, out_uvs, rep


def op_fur_shells(p):
    """layers offset copies of the mesh along vertex normals; shell k gets material index k (1-based).
    With p["proxy"] (tris) the shells go on a decimated copy and rep maps their vertices to the body's;
    with p["budget"] the layer count is cut to fit budget - base_tris."""
    body_verts, body_faces = p["verts"], p["faces"]
    wanted = max(1, min(32, int(p.get("layers") or 8)))
    length = float(p.get("length") or 2.0)
    uvs = p.get("uvs")
    if not uvs:
        lo = [min(v[i] for v in body_verts) for i in range(3)]
        hi = [max(v[i] for v in body_verts) for i in range(3)]
        span = max(hi[0] - lo[0], hi[1] - lo[1]) or 1.0
        uvs = [[[(body_verts[i][0] - lo[0]) / span * 4, (body_verts[i][1] - lo[1]) / span * 4] for i in f] for f in body_faces]
    verts, faces, shell_uvs, rep = body_verts, body_faces, uvs, list(range(len(body_verts)))
    proxy = int(p.get("proxy") or 0)
    budget = p.get("budget")
    room = int(budget) - int(p.get("base_tris") or 0) if budget else None
    if proxy and _tris(faces) > proxy:
        verts, faces, shell_uvs, rep = decimate(body_verts, body_faces, uvs, proxy)
    per = _tris(faces) or 1
    layers = wanted
    if room is not None:
        layers = min(wanted, room // per)
        if layers < 1 and proxy and room >= 60:  # the proxy itself is too big: shrink it for one shell
            verts, faces, shell_uvs, rep = decimate(body_verts, body_faces, uvs, room)
            per, layers = _tris(faces) or 1, 1
        layers = max(0, layers)
    body_normals = _vertex_normals(body_verts, body_faces)  # proxy verts are body verts: offset along the BODY normal so a shell never leaves the skin by more than length
    normals = [body_normals[r] for r in rep]
    uvs, body_uvs = shell_uvs, uvs
    out_verts, out_faces, out_uvs, shell = [], [], [], []
    for k in range(1, layers + 1):
        base = len(out_verts)
        off = length * k / layers
        out_verts += [[round(v[i] + n[i] * off, 3) for i in range(3)] for v, n in zip(verts, normals)]
        out_faces += [[base + i for i in f] for f in faces]
        out_uvs += uvs
        shell += [k] * len(faces)
    folder = Path(p["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    _noise_png(folder / "fur_noise.png", 64, p.get("color"), int(p.get("seed", 1)))
    return {"verts": out_verts, "faces": out_faces, "uvs": out_uvs, "base_uvs": body_uvs, "shell": shell, "layers": layers,
            "wanted": wanted, "per_layer": per, "rep": rep, "noise_file": "fur_noise.png"}


# ---- fur bake ---------------------------------------------------------------


COATS = {"lion": dict(back=(0.64, 0.43, 0.2), belly=(0.92, 0.82, 0.62), dark=(0.2, 0.1, 0.045))}


def _clamp(x):
    return max(0.0, min(1.0, x))


def coat_masks(body, size):
    """Rasterise per-vertex coat tones into UV space: (belly, dark) bytearrays of size*size, 255 = full.
    belly: down-facing skin and inner legs; dark: mane base (high, front) and tail tuft (far back)."""
    verts, faces, uvs = body["verts"], body["faces"], body["uvs"]
    normals = _vertex_normals(verts, faces)
    lo = [min(v[i] for v in verts) for i in range(3)]
    span = [(max(v[i] for v in verts) - lo[i]) or 1.0 for i in range(3)]
    cx = lo[0] + span[0] / 2
    tones = []
    for v, n in zip(verts, normals):
        h, f = (v[2] - lo[2]) / span[2], (v[1] - lo[1]) / span[1]
        belly = _clamp((0.15 - n[2]) / 0.7)
        if n[0] * (v[0] - cx) < -0.2:  # facing the body's midline: inner leg
            belly = max(belly, 0.6 * _clamp((0.4 - h) / 0.2))
        mane = _clamp(((1 - f) - 0.7) / 0.15) * _clamp((h - 0.5) / 0.2)
        tones.append((belly, max(0.8 * mane, _clamp((f - 0.93) / 0.05))))
    belly_map, dark_map = bytearray(size * size), bytearray(size * size)
    for face, uv in zip(faces, uvs):
        for k in range(1, len(face) - 1):
            idx = (0, k, k + 1)
            pts = [(uv[i][0] * (size - 1), (1 - uv[i][1]) * (size - 1)) for i in idx]
            (x0, y0), (x1, y1), (x2, y2) = pts
            area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(area) < 1e-9:
                continue
            for y in range(max(0, int(min(y0, y1, y2))), min(size - 1, int(max(y0, y1, y2)) + 1) + 1):
                for x in range(max(0, int(min(x0, x1, x2))), min(size - 1, int(max(x0, x1, x2)) + 1) + 1):
                    w1 = ((x - x0) * (y2 - y0) - (x2 - x0) * (y - y0)) / area
                    w2 = ((x1 - x0) * (y - y0) - (x - x0) * (y1 - y0)) / area
                    w0 = 1 - w1 - w2
                    if min(w0, w1, w2) < -0.02:
                        continue
                    t = [tones[face[i]] for i in idx]
                    belly_map[y * size + x] = int(255 * _clamp(w0 * t[0][0] + w1 * t[1][0] + w2 * t[2][0]))
                    dark_map[y * size + x] = int(255 * _clamp(w0 * t[0][1] + w1 * t[1][1] + w2 * t[2][1]))
    return belly_map, dark_map


def op_fur_bake(p):
    """Strand-streak BaseColor and a matching fur-direction normal map (tangent space, strands run down V).
    coat="lion" with p["body"] (verts, faces, per-face uvs) paints tawny back, cream belly and inner legs, dark mane base and tail tuft."""
    size = max(64, min(4096, int(p.get("size") or 1024)))
    rng = random.Random(int(p.get("seed", 1)))
    coat = COATS[p["coat"]] if p.get("coat") else None
    base = rgb(p.get("color")) if coat is None else coat["back"]
    belly_map, dark_map = coat_masks(p["body"], size) if coat else (None, None)
    height = [bytearray(size) for _ in range(size)]
    for _ in range(size * size // 36):
        x, y0, n, b = rng.randrange(size), rng.randrange(size), rng.randint(6, 22), rng.randint(90, 255)
        for k in range(n):
            row = height[(y0 + k) % size]
            v = int(b * (0.55 + 0.45 * math.sin(math.pi * (k + 0.5) / n)))  # strands taper at both ends
            if v > row[x]:
                row[x] = v
            if v * 0.6 > row[(x + 1) % size]:
                row[(x + 1) % size] = int(v * 0.6)
    cell = max(8, size // 16)
    tint = [[0.85 + 0.3 * rng.random() for _ in range(size // cell + 1)] for _ in range(size // cell + 1)]
    color_rows, normal_rows = [], []
    for y in range(size):
        crow, nrow = bytearray(), bytearray()
        up, down, here = height[y - 1], height[(y + 1) % size], height[y]
        for x in range(size):
            shade = (0.45 + 0.55 * here[x] / 255) * tint[y // cell][x // cell]
            if coat:
                b, d = belly_map[y * size + x] / 255, dark_map[y * size + x] / 255
                tone = [(a + (c - a) * b) * (1 - d) + e * d for a, c, e in zip(coat["back"], coat["belly"], coat["dark"])]
            else:
                tone = base
            crow += bytes(min(255, int(c * shade * 255)) for c in tone)
            nx = max(-0.7, min(0.7, (here[x - 1] - here[(x + 1) % size]) / 255.0 * 1.5))
            ny = max(-0.7, min(0.7, (up[x] - down[x]) / 255.0 * 0.5))
            nz = math.sqrt(max(0.05, 1 - nx * nx - ny * ny))
            nrow += bytes((int((nx * 0.5 + 0.5) * 255), int((ny * 0.5 + 0.5) * 255), int((nz * 0.5 + 0.5) * 255)))
        color_rows.append(crow)
        normal_rows.append(nrow)
    folder = Path(p["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    _png(folder / p["color_file"], size, size, color_rows, 3)
    _png(folder / p["normal_file"], size, size, normal_rows, 3)
    return {"size": size, "color_file": p["color_file"], "normal_file": p["normal_file"]}
