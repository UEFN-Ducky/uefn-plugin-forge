"""Reference tracing: silhouette visual hull and colour parts from drawings. Pure stdlib maths (testable
without Blender); bpy is only used, lazily, to decode JPG and big images.

Forge centimetres, Z-up, -Y front. Images: front view = image right is +X; side view faces image RIGHT
(front toward the right edge, so image right is -Y). Everything is JSON in, JSON out.
"""
import math
import struct
import zlib
from pathlib import Path

LIMIT = 320  # longest image side kept after decoding; silhouettes and colours need no more
PURE_PNG_PIXELS = 16_000_000


# ---- image decoding -----------------------------------------------------------------------------


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _decode_png(raw):
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, plte, meta = 8, [], None, None
    while pos < len(raw):
        n, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            meta = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + n
    w, h, depth, ctype, _, _, interlace = meta
    if depth != 8 or interlace or ctype not in (0, 2, 3, 4, 6):
        raise ValueError("PNG must be 8-bit and not interlaced; re-save it as a plain 8-bit PNG or JPG")
    if w * h > PURE_PNG_PIXELS:
        raise ValueError("Image is %dx%d; shrink it under 4000x4000 first" % (w, h))
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    data, stride = zlib.decompress(b"".join(idat)), w * ch
    step = max(1, math.ceil(max(w, h) / LIMIT))
    prev, px, at = bytearray(stride), {}, 0
    for y in range(h):
        kind, row = data[at], bytearray(data[at + 1:at + 1 + stride])
        at += 1 + stride
        if kind == 1:
            for i in range(ch, stride):
                row[i] = (row[i] + row[i - ch]) & 255
        elif kind == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                row[i] = (row[i] + (((row[i - ch] if i >= ch else 0) + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                row[i] = (row[i] + _paeth(row[i - ch] if i >= ch else 0, prev[i], prev[i - ch] if i >= ch else 0)) & 255
        prev = row
        if y % step == 0:
            px[y] = row
    out = []
    for y in range(0, h, step):
        row = px[y]
        for x in range(0, w, step):
            o = x * ch
            if ctype == 6:
                out.append((row[o], row[o + 1], row[o + 2], row[o + 3]))
            elif ctype == 2:
                out.append((row[o], row[o + 1], row[o + 2], 255))
            elif ctype == 0:
                out.append((row[o], row[o], row[o], 255))
            elif ctype == 4:
                out.append((row[o], row[o], row[o], row[o + 1]))
            else:
                i = row[o] * 3
                out.append((plte[i], plte[i + 1], plte[i + 2], 255))
    return math.ceil(w / step), math.ceil(h / step), out


def _decode_bpy(path):
    import bpy
    import numpy as np
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        arr = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(arr)
    finally:
        bpy.data.images.remove(img)
    arr = arr.reshape(h, w, 4)[::-1]  # bpy rows run bottom-up
    step = max(1, math.ceil(max(w, h) / LIMIT))
    arr = (arr[::step, ::step] * 255.0 + 0.5).astype(np.uint8)
    # bpy returns linear-ish floats for sRGB files already converted by the loader: use as stored
    return arr.shape[1], arr.shape[0], [tuple(int(c) for c in p) for p in arr.reshape(-1, 4)]


def load_image(path):
    """(w, h, [(r,g,b,a)] row-major, top row first), longest side <= LIMIT."""
    path = Path(str(path))
    if not path.is_file():
        raise ValueError("No image at %s. Pass a full path to a PNG or JPG" % path)
    raw = path.read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            return _decode_png(raw)
        except ValueError:
            pass
    try:
        return _decode_bpy(path)
    except ImportError:
        raise ValueError("%s needs the Blender engine to decode (JPG or exotic PNG). Re-save it as an 8-bit PNG or start the engine" % path.name)


# ---- silhouettes --------------------------------------------------------------------------------


def _border(w, h):
    return [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]


def foreground(w, h, px):
    """bytearray w*h, 1 = subject. Alpha when the image has any, else distance from the border colour."""
    if any(p[3] < 250 for p in px):
        return bytearray(1 if p[3] >= 128 else 0 for p in px)
    votes = {}
    for x, y in _border(w, h):
        p = px[y * w + x]
        votes.setdefault((p[0] >> 3, p[1] >> 3, p[2] >> 3), []).append(p)
    group = max(votes.values(), key=len)
    bg = [sum(p[i] for p in group) / len(group) for i in range(3)]
    return bytearray(1 if max(abs(p[i] - bg[i]) for i in range(3)) > 40 else 0 for p in px)


def _components(mask, w, h):
    seen, best = bytearray(w * h), []
    for s in range(w * h):
        if mask[s] and not seen[s]:
            comp, stack = [], [s]
            seen[s] = 1
            while stack:
                c = stack.pop()
                comp.append(c)
                x, y = c % w, c // w
                for n, ok in ((c - 1, x > 0), (c + 1, x < w - 1), (c - w, y > 0), (c + w, y < h - 1)):
                    if ok and mask[n] and not seen[n]:
                        seen[n] = 1
                        stack.append(n)
            if len(comp) > len(best):
                best = comp
    return best


def silhouette(w, h, px):
    """(mask, w, h, bbox): biggest blob, holes filled, cropped to its bounding box."""
    fg = foreground(w, h, px)
    comp = _components(fg, w, h)
    if not comp:
        raise ValueError("No subject found in the image. Use a drawing on a plain light background, or a PNG with transparency")
    blob = bytearray(w * h)
    for c in comp:
        blob[c] = 1
    outside, stack = bytearray(w * h), [y * w + x for x, y in _border(w, h) if not blob[y * w + x]]
    for s in stack:
        outside[s] = 1
    while stack:
        c = stack.pop()
        x, y = c % w, c // w
        for n, ok in ((c - 1, x > 0), (c + 1, x < w - 1), (c - w, y > 0), (c + w, y < h - 1)):
            if ok and not blob[n] and not outside[n]:
                outside[n] = 1
                stack.append(n)
    filled = bytearray(0 if o else 1 for o in outside)
    xs = [c % w for c in comp]
    ys = [c // w for c in comp]
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    crop = bytearray()
    for y in range(y0, y1):
        crop += filled[y * w + x0:y * w + x1]
    return crop, x1 - x0, y1 - y0, (x0, y0, x1, y1)


def grid_from(mask, w, h, nx, nz):
    """cols[i][k] (k counts up from the bottom) by nearest sampling of a top-down mask."""
    return [[mask[(h - 1 - min(h - 1, int((k + 0.5) * h / nz))) * w + min(w - 1, int((i + 0.5) * w / nx))] for k in range(nz)]
            for i in range(nx)]


def distance(cols):
    """Chamfer distance (cells) from each filled cell to the nearest empty one; the grid edge counts as empty."""
    nx, nz = len(cols), len(cols[0])
    big = 1e9
    d = [[0.0 if not cols[i][k] else big for k in range(nz)] for i in range(nx)]
    for i in range(nx):
        for k in range(nz):
            if d[i][k]:
                best = d[i][k]
                for di, dk, c in ((-1, 0, 1.0), (0, -1, 1.0), (-1, -1, 1.414), (-1, 1, 1.414)):
                    a, b = i + di, k + dk
                    best = min(best, (d[a][b] if 0 <= a < nx and 0 <= b < nz else 0.0) + c)
                d[i][k] = best
    for i in range(nx - 1, -1, -1):
        for k in range(nz - 1, -1, -1):
            if d[i][k]:
                best = d[i][k]
                for di, dk, c in ((1, 0, 1.0), (0, 1, 1.0), (1, 1, 1.414), (1, -1, 1.414)):
                    a, b = i + di, k + dk
                    best = min(best, (d[a][b] if 0 <= a < nx and 0 <= b < nz else 0.0) + c)
                d[i][k] = best
    return d


# ---- volume -> mesh -----------------------------------------------------------------------------


def surface(filled, nx, ny, nz):
    """Closed quad surface of a voxel set: (corner coords, quads with outward winding)."""
    ids, verts, quads = {}, [], []

    def vert(c):
        if c not in ids:
            ids[c] = len(verts)
            verts.append(c)
        return ids[c]

    for (i, j, k) in filled:
        base = (i, j, k)
        for a in range(3):
            u, v = (a + 1) % 3, (a + 2) % 3
            for sign in (1, -1):
                n = list(base)
                n[a] += sign
                if tuple(n) in filled:
                    continue
                corner = []
                for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    c = list(base)
                    c[a] += 1 if sign > 0 else 0
                    c[u] += du
                    c[v] += dv
                    corner.append(vert(tuple(c)))
                quads.append(corner if sign > 0 else corner[::-1])
    return verts, quads


def smooth(verts, quads, rounds):
    """Taubin smoothing (shrink, then inflate) so the voxel steps round off without the body deflating."""
    near = [set() for _ in verts]
    for q in quads:
        for a in range(4):
            near[q[a]].add(q[(a + 1) % 4])
            near[q[a]].add(q[(a - 1) % 4])
    pts = [list(v) for v in verts]
    for _ in range(max(0, rounds)):
        for lam in (0.5, -0.53):
            nxt = []
            for i, p in enumerate(pts):
                ring = near[i]
                avg = [sum(pts[n][c] for n in ring) / len(ring) for c in range(3)]
                nxt.append([p[c] + lam * (avg[c] - p[c]) for c in range(3)])
            pts = nxt
    return pts


def hull_volume(front, side, nx, nz):
    """Voxels where the front column and the side column both cover the row."""
    ny = len(side)
    return {(i, j, k) for i in range(nx) for k in range(nz) if front[i][k] for j in range(ny) if side[j][k]}, ny


def inflate_volume(front, nx, nz):
    """Round depth from the front silhouette alone: a circular cross-section that is deepest in the middle."""
    d = distance(front)
    big = max(max(col) for col in d)
    half = [[0.0] * nz for _ in range(nx)]
    for i in range(nx):
        for k in range(nz):
            if front[i][k]:
                t = min(max(d[i][k] - 0.5, 0.0), big)
                half[i][k] = max(0.5, math.sqrt(max(t * (2 * big - t), 0.0)))
    ny = 2 * int(math.ceil(big)) + 2
    mid = ny / 2.0
    vol = {(i, j, k) for i in range(nx) for k in range(nz) if front[i][k] for j in range(ny) if abs(j + 0.5 - mid) <= half[i][k]}
    return vol, ny


def project_xz(vol, nx, nz):
    cols = [[0] * nz for _ in range(nx)]
    for i, _, k in vol:
        cols[i][k] = 1
    return cols


# ---- colour parts -------------------------------------------------------------------------------


def lab(c):
    """sRGB 0..255 -> CIE Lab (D65)."""
    lin = [((v / 255.0 + 0.055) / 1.055) ** 2.4 if v > 10.31475 else v / 255.0 / 12.92 for v in c[:3]]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = [t ** (1 / 3.0) if t > 0.008856 else 7.787 * t + 16 / 116.0 for t in (x, y, z)]
    return (116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2]))


def _d2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def kmeans(points, k, rounds=12):
    """Farthest-first start (deterministic), then Lloyd steps. Returns the centres."""
    k = max(1, min(k, len(points)))
    centres = [points[0]]
    while len(centres) < k:
        centres.append(max(points, key=lambda q: min(_d2(q, c) for c in centres)))
    for _ in range(rounds):
        sums = [[0.0, 0.0, 0.0, 0] for _ in centres]
        for q in points:
            s = sums[min(range(len(centres)), key=lambda i: _d2(q, centres[i]))]
            s[0] += q[0]
            s[1] += q[1]
            s[2] += q[2]
            s[3] += 1
        moved = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) if s[3] else centres[i] for i, s in enumerate(sums)]
        if all(_d2(a, b) < 1e-3 for a, b in zip(moved, centres)):
            break
        centres = moved
    return centres


def parse_color(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return tuple(float(v) for v in value[:3])
    raise ValueError("Palette colour %r is not '#rrggbb' or [r,g,b] (0-255). Example: palette=['#e8a030', '#ffffff']" % (value,))


def name_clusters(labs, areas):
    """Role names from colour: Outline (darkest), Ear (pink), Muzzle (lightest), then Body / Mane by area."""
    names, free = [None] * len(labs), set(range(len(labs)))

    def take(name, pick):
        cands = [i for i in free if pick(i)]
        if cands and name not in names:
            i = max(cands, key=lambda c: areas[c])
            names[i] = name
            free.discard(i)

    take("Outline", lambda i: labs[i][0] < 28)
    take("Ear", lambda i: labs[i][1] > 22 and labs[i][0] > 55)
    take("Muzzle", lambda i: labs[i][0] > 82 and abs(labs[i][1]) < 15)
    for name in ("Body", "Mane"):
        cands = sorted(free, key=lambda i: -areas[i])
        if cands:
            names[cands[0]] = name
            free.discard(cands[0])
    for n, i in enumerate(sorted(free)):
        names[i] = "Part%d" % (n + 1)
    return names


def op_ref_parts(p):
    k = max(2, min(12, int(p.get("k") or 6)))
    w, h, px = load_image(p["front"])
    mask, cw, ch, (x0, y0, x1, y1) = silhouette(w, h, px)
    inner = [(px[(y0 + y) * w + x0 + x][:3]) for y in range(ch) for x in range(cw) if mask[y * cw + x]]
    pal = p.get("palette")
    given_names = None
    if pal:
        given_names = [e.get("name") if isinstance(e, dict) else None for e in pal]
        centres_rgb = [parse_color(e.get("color") if isinstance(e, dict) else e) for e in pal]
        centres = [lab(c) for c in centres_rgb]
    else:
        step = max(1, len(inner) // 3000)
        centres = kmeans([lab(c) for c in inner[::step]], k)
        centres_rgb = None
    labs_px = [lab(c) for c in inner]  # per-pixel Lab, same order as inner

    def nearest(q):
        return min(range(len(centres)), key=lambda i: _d2(q, centres[i]))

    index = {}
    n = 0
    for y in range(ch):
        for x in range(cw):
            if mask[y * cw + x]:
                index[(x, y)] = nearest(labs_px[n])
                n += 1
    area = [0] * len(centres)
    sums = [[0.0, 0.0, 0.0] for _ in centres]
    for pos, ci in index.items():
        area[ci] += 1
        c = px[(y0 + pos[1]) * w + x0 + pos[0]]
        for a in range(3):
            sums[ci][a] += c[a]
    if centres_rgb is None:
        centres_rgb = [tuple(s[a] / area[i] for a in range(3)) if area[i] else (0, 0, 0) for i, s in enumerate(sums)]
    keep = [i for i in range(len(centres)) if area[i] or pal]
    names = name_clusters([centres[i] for i in keep], [area[i] for i in keep])
    names = [(given_names[i] if given_names and given_names[i] else names[n]) for n, i in enumerate(keep)]
    lo, hi = p["bounds"]["lo"], p["bounds"]["hi"]
    assign = []
    for (x, z) in p["points"]:  # world (x, z) -> pixel in the traced crop
        u = (x - lo[0]) / ((hi[0] - lo[0]) or 1.0)
        v = 1.0 - (z - lo[1]) / ((hi[1] - lo[1]) or 1.0)
        cx, cy = min(cw - 1, max(0, int(u * cw))), min(ch - 1, max(0, int(v * ch)))
        votes = {}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                ci = index.get((cx + dx, cy + dy))
                if ci is not None:
                    votes[ci] = votes.get(ci, 0) + (2 if dx == dy == 0 else 1)
        if not votes:  # outside the silhouette: the colour of the closest filled pixel
            ci = min(index, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cy) ** 2)
            votes = {index[ci]: 1}
        assign.append(keep.index(max(votes, key=votes.get)) if max(votes, key=votes.get) in keep else 0)
    return {"names": names, "colors": [[round(c / 255.0, 4) for c in centres_rgb[i]] for i in keep], "assign": assign,
            "area": [area[i] for i in keep]}


def op_blockout_from_refs(p):
    res = max(12, min(96, int(p.get("res") or 48)))
    height = float(p.get("height") or 100.0)
    if height <= 0:
        raise ValueError("height must be above 0 cm, got %s" % height)
    w, h, px = load_image(p["front"])
    mask, cw, ch, _ = silhouette(w, h, px)
    nz = res
    nx = max(2, round(nz * cw / ch))
    front = grid_from(mask, cw, ch, nx, nz)
    front = [[front[i][k] or front[nx - 1 - i][k] for k in range(nz)] for i in range(nx)]  # mirror: one half drives both
    warnings, mode = [], "inflate"
    vol = ny = None
    if p.get("side") and p.get("mode") != "inflate":
        sw, sh, spx = load_image(p["side"])
        smask, scw, sch, _ = silhouette(sw, sh, spx)
        ny0 = max(2, round(nz * scw / sch))
        side = grid_from(smask, scw, sch, ny0, nz)
        vol, ny = hull_volume(front, side, nx, nz)
        area = sum(map(sum, front))
        cover = sum(sum(c) for c in project_xz(vol, nx, nz)) / float(area or 1)
        if cover < 0.85:  # the side drawing disagrees with the front (a three-quarter view, a different pose)
            warnings.append("side does not match front (covers %d%%); used rounded depth. Pass a true side view" % round(cover * 100))
            vol = None
        else:
            mode = "hull"
    elif p.get("side"):
        mode = "inflate"
    if vol is None:
        vol, ny = inflate_volume(front, nx, nz)
    if not vol:
        raise ValueError("The traced volume is empty. Check the front image has a clear subject")
    verts, quads = surface(vol, nx, ny, nz)
    pts = smooth(verts, quads, int(p.get("smooth", 2)))
    vs = height / float(nz)
    out = [[round((x - nx / 2.0) * vs, 3), round(-(y - ny / 2.0) * vs, 3), round(z * vs, 3)] for x, y, z in pts]
    lo = [min(v[c] for v in out) for c in range(3)]
    hi = [max(v[c] for v in out) for c in range(3)]
    return {"verts": out, "faces": quads, "mode": mode, "quads": len(quads), "lo": lo, "hi": hi, "warnings": warnings,
            "grid": [nx, ny, nz]}
