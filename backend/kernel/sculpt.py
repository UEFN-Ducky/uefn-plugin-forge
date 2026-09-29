"""Brushes, smooth subdivision, noise, and decimation. The AI's side of sculpting and painting.

The dock runs the same brush shapes in JavaScript for the user's strokes and commits
the resulting values, so each stroke is computed once, by whichever side drew it.
Brush centres and radii are in the mesh's own (local) centimetres.
"""

from __future__ import annotations

import math
from typing import Sequence

from .mesh import Mesh, Vec3, attr_default, attr_mix, vnorm

SCULPT = ("draw", "clay", "inflate", "smooth", "flatten", "grab", "pinch", "crease")
PAINT = ("add", "subtract", "set", "smooth")


def vertex_normals(mesh: Mesh) -> list[Vec3]:
    acc = [[0.0, 0.0, 0.0] for _ in mesh.verts]
    for face in mesh.faces:
        nx = ny = nz = 0.0
        for i, a in enumerate(face):  # Newell, not normalized, so bigger faces weigh more
            p, q = mesh.verts[a], mesh.verts[face[(i + 1) % len(face)]]
            nx += (p[1] - q[1]) * (p[2] + q[2])
            ny += (p[2] - q[2]) * (p[0] + q[0])
            nz += (p[0] - q[0]) * (p[1] + q[1])
        for a in face:
            acc[a][0] += nx
            acc[a][1] += ny
            acc[a][2] += nz
    return [vnorm(n) for n in acc]


def neighbors(mesh: Mesh) -> list[set[int]]:
    adj: list[set[int]] = [set() for _ in mesh.verts]
    for face in mesh.faces:
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            adj[a].add(b)
            adj[b].add(a)
    return adj


def falloff(d: float, r: float) -> float:
    t = max(0.0, 1.0 - d / r)
    return t * t * (3.0 - 2.0 * t)


class _Grid:
    """Uniform grid over positions for radius queries. Built once per stroke.

    ponytail: positions that move more than one cell during a stroke can be missed
    by later dabs; rebuild per dab if long grab strokes need it.
    """

    def __init__(self, pts: list, cell: float):
        self.pts, self.cell, self.cells = pts, max(cell, 1e-6), {}
        for i, p in enumerate(pts):
            self.cells.setdefault(self._key(p), []).append(i)

    def _key(self, p) -> tuple[int, int, int]:
        c = self.cell
        return (math.floor(p[0] / c), math.floor(p[1] / c), math.floor(p[2] / c))

    def near(self, c, r: float) -> list[tuple[int, float]]:
        kx, ky, kz = self._key(c)
        reach = max(1, math.ceil(r / self.cell))
        out = []
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for dz in range(-reach, reach + 1):
                    for i in self.cells.get((kx + dx, ky + dy, kz + dz), ()):
                        p = self.pts[i]
                        d = math.sqrt((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2)
                        if d < r:
                            out.append((i, d))
        return out


def _dabs(points: Sequence[Sequence[float]], radius: float, spacing: float) -> list[tuple[list, list]]:
    """Dab centres along a polyline, each with the stroke direction since the last dab (for grab)."""
    pts = [[float(c) for c in p] for p in points]
    if len(pts) == 1:
        return [(pts[0], [0.0, 0.0, 0.0])]
    step = max(radius * spacing, 1e-3)
    out, prev = [], pts[0]
    for a, b in zip(pts, pts[1:]):
        seg = [b[i] - a[i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in seg))
        n = max(1, int(math.ceil(length / step)))
        for k in range(1, n + 1):
            here = [a[i] + seg[i] * k / n for i in range(3)]
            out.append((here, [here[i] - prev[i] for i in range(3)]))
            prev = here
    return out


def stroke(mesh: Mesh, points, brush: str = "draw", radius: float = 20.0, strength: float = 0.5, *,
           target: str = "pos", channel: int | None = None, value: float = 1.0, mirror: str = "",
           spacing: float = 0.3, invert: bool = False) -> int:
    """Apply one brush stroke. target is "pos", "sk:<key>", "wt:<bone>", or "col:<name>" (+ channel).

    Sculpt brushes use strength as centimetres at the centre (smooth, flatten, pinch: 0..1).
    Paint brushes (add, subtract, set, smooth) use strength 0..1.
    Returns how many vertices changed.
    """
    radius = float(radius)
    if radius <= 0 or not math.isfinite(radius):
        raise ValueError("radius must be a positive number of centimetres")
    painting = target.startswith(("wt:", "col:"))
    kinds = PAINT if painting else SCULPT
    if brush not in kinds:
        raise ValueError(f"brush is one of {', '.join(kinds)}")
    shape = mesh.ensure_attr(target) if target.startswith("sk:") else None
    values = mesh.ensure_attr(target) if painting else None
    if target.startswith("col:") and channel is None:
        raise ValueError("A colour attribute needs channel 0-3 (r, g, b, a)")
    pts = [[v[i] + (shape[k][i] if shape else 0.0) for i in range(3)] for k, v in enumerate(mesh.verts)]
    normals = vertex_normals(mesh)
    adj = neighbors(mesh) if brush in ("smooth",) else None
    grid = _Grid(pts, radius)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(mirror or "").lower()[:1])
    sign = -1.0 if invert else 1.0
    touched: set[int] = set()
    for centre, move in _dabs(points, radius, spacing):
        centres = [(centre, move)]
        if axis is not None:
            flip = centre[:]
            flip[axis] = -flip[axis]
            fmove = move[:]
            fmove[axis] = -fmove[axis]
            if abs(centre[axis]) > radius * 0.1:
                centres.append((flip, fmove))
        for c, m in centres:
            hits = grid.near(c, radius)
            if not hits:
                continue
            weights = [(i, falloff(d, radius)) for i, d in hits]
            touched.update(i for i, _ in weights)
            if painting:
                _paint_dab(values, channel, weights, brush, strength, value, adj)
            else:
                _sculpt_dab(pts, normals, weights, brush, strength * sign, c, m, adj)
    if not painting:
        for i in touched:
            if shape is not None:
                shape[i] = [pts[i][k] - mesh.verts[i][k] for k in range(3)]
            else:
                mesh.verts[i] = pts[i]
    return len(touched)


def _area(normals, pts, weights):
    n = [0.0, 0.0, 0.0]
    c = [0.0, 0.0, 0.0]
    total = 0.0
    for i, f in weights:
        for k in range(3):
            n[k] += normals[i][k] * f
            c[k] += pts[i][k] * f
        total += f
    total = total or 1.0
    return vnorm(n), [c[k] / total for k in range(3)]


def _sculpt_dab(pts, normals, weights, brush, s, centre, move, adj) -> None:
    if brush == "smooth":
        k = max(0.0, min(1.0, abs(s)))
        new = {i: [sum(pts[j][a] for j in adj[i]) / len(adj[i]) for a in range(3)] for i, _ in weights if adj[i]}
        for i, f in weights:
            if i in new:
                pts[i] = [pts[i][a] + (new[i][a] - pts[i][a]) * k * f for a in range(3)]
        return
    n, c = _area(normals, pts, weights)
    for i, f in weights:
        p = pts[i]
        if brush == "draw":
            d = [n[a] * s * f for a in range(3)]
        elif brush == "inflate":
            d = [normals[i][a] * s * f for a in range(3)]
        elif brush == "clay":
            plane = [c[a] + n[a] * s for a in range(3)]
            h = sum((plane[a] - p[a]) * n[a] for a in range(3))
            d = [n[a] * h * f * 0.5 for a in range(3)] if h * (1 if s >= 0 else -1) > 0 else [0.0, 0.0, 0.0]
        elif brush == "flatten":
            h = sum((p[a] - c[a]) * n[a] for a in range(3))
            k = max(0.0, min(1.0, abs(s)))
            d = [-n[a] * h * k * f for a in range(3)]
        elif brush == "grab":
            d = [move[a] * f for a in range(3)]
        else:  # pinch / crease pull toward the centre along the surface
            k = max(0.0, min(1.0, abs(s))) if brush == "pinch" else 0.3
            to = [centre[a] - p[a] for a in range(3)]
            along = sum(to[a] * n[a] for a in range(3))
            d = [(to[a] - n[a] * along) * k * f for a in range(3)]
            if brush == "crease":
                d = [d[a] - n[a] * s * f for a in range(3)]
        pts[i] = [p[a] + d[a] for a in range(3)]


def _paint_dab(values, channel, weights, brush, strength, value, adj) -> None:
    k = max(0.0, min(1.0, float(strength)))

    def get(i):
        return values[i][channel] if channel is not None else values[i]

    def put(i, v):
        v = max(0.0, min(1.0, v))
        if channel is not None:
            values[i][channel] = v
        else:
            values[i] = v

    if brush == "smooth":
        avg = {i: sum(get(j) for j in adj[i]) / len(adj[i]) for i, _ in weights if adj and adj[i]}
        for i, f in weights:
            if i in avg:
                put(i, get(i) + (avg[i] - get(i)) * k * f)
        return
    for i, f in weights:
        v = get(i)
        if brush == "add":
            put(i, v + k * f)
        elif brush == "subtract":
            put(i, v - k * f)
        else:
            put(i, v + (float(value) - v) * k * f)


# ---- whole-mesh operations -------------------------------------------------


def _hash(ix: int, iy: int, iz: int, seed: int) -> float:
    h = (ix * 374761393 + iy * 668265263 + iz * 2147483647 + seed * 144269504) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def noise3(x: float, y: float, z: float, seed: int = 0) -> float:
    """Smooth value noise in 0..1."""
    ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
    fx, fy, fz = x - ix, y - iy, z - iz
    ux, uy, uz = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy), fz * fz * (3 - 2 * fz)

    def at(dx, dy, dz):
        return _hash(ix + dx, iy + dy, iz + dz, seed)

    x00 = at(0, 0, 0) + (at(1, 0, 0) - at(0, 0, 0)) * ux
    x10 = at(0, 1, 0) + (at(1, 1, 0) - at(0, 1, 0)) * ux
    x01 = at(0, 0, 1) + (at(1, 0, 1) - at(0, 0, 1)) * ux
    x11 = at(0, 1, 1) + (at(1, 1, 1) - at(0, 1, 1)) * ux
    y0 = x00 + (x10 - x00) * uy
    y1 = x01 + (x11 - x01) * uy
    return y0 + (y1 - y0) * uz


def fbm(p, scale: float, seed: int = 0, octaves: int = 3) -> float:
    """Layered noise in about -1..1. scale is the feature size in centimetres."""
    total, amp, freq, norm = 0.0, 1.0, 1.0 / max(scale, 1e-6), 0.0
    for o in range(max(1, int(octaves))):
        total += amp * (noise3(p[0] * freq, p[1] * freq, p[2] * freq, seed + o * 17) * 2 - 1)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def displace_noise(mesh: Mesh, amount: float = 5.0, scale: float = 40.0, seed: int = 0, octaves: int = 3) -> int:
    """Push every vertex along its normal by layered noise. Rocks, bark, lumpy ground."""
    normals = vertex_normals(mesh)
    for i, v in enumerate(mesh.verts):
        h = fbm(v, scale, seed, octaves) * amount
        mesh.verts[i] = [v[a] + normals[i][a] * h for a in range(3)]
    return len(mesh.verts)


def smooth_all(mesh: Mesh, iterations: int = 1, strength: float = 0.5) -> int:
    adj = neighbors(mesh)
    k = max(0.0, min(1.0, float(strength)))
    for _ in range(max(1, int(iterations))):
        avg = [[sum(mesh.verts[j][a] for j in adj[i]) / len(adj[i]) for a in range(3)] if adj[i] else mesh.verts[i]
               for i in range(len(mesh.verts))]
        mesh.verts = [[v[a] + (avg[i][a] - v[a]) * k for a in range(3)] for i, v in enumerate(mesh.verts)]
    return len(mesh.verts)


def _mid(a: list, b: list) -> list:
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]


def catmull_clark(mesh: Mesh, levels: int = 1) -> Mesh:
    """Smooth subdivision: every face becomes quads and the surface rounds off. Attributes interpolate."""
    for _ in range(max(1, min(4, int(levels)))):
        verts, faces = mesh.verts, mesh.faces
        nv = len(verts)
        face_pts = [[sum(verts[i][a] for i in f) / len(f) for a in range(3)] for f in faces]
        edge_faces: dict[tuple[int, int], list[int]] = {}
        for fi, f in enumerate(faces):
            for i, a in enumerate(f):
                b = f[(i + 1) % len(f)]
                edge_faces.setdefault((a, b) if a < b else (b, a), []).append(fi)
        edge_index = {key: nv + len(faces) + k for k, key in enumerate(edge_faces)}
        edge_pts = []
        for (a, b), fs in edge_faces.items():
            if len(fs) == 2:
                edge_pts.append([(verts[a][k] + verts[b][k] + face_pts[fs[0]][k] + face_pts[fs[1]][k]) / 4 for k in range(3)])
            else:
                edge_pts.append([(verts[a][k] + verts[b][k]) / 2 for k in range(3)])
        v_faces: list[list[int]] = [[] for _ in verts]
        v_edges: list[list[tuple[int, int]]] = [[] for _ in verts]
        for fi, f in enumerate(faces):
            for i in f:
                v_faces[i].append(fi)
        for key in edge_faces:
            v_edges[key[0]].append(key)
            v_edges[key[1]].append(key)
        moved = []
        for i, p in enumerate(verts):
            boundary = [e for e in v_edges[i] if len(edge_faces[e]) == 1]
            if boundary:
                if len(boundary) == 2:
                    o = [e[0] if e[1] == i else e[1] for e in boundary]
                    moved.append([(6 * p[k] + verts[o[0]][k] + verts[o[1]][k]) / 8 for k in range(3)])
                else:
                    moved.append(p[:])
                continue
            n = len(v_faces[i])
            if n < 3:
                moved.append(p[:])
                continue
            fa = [sum(face_pts[f][k] for f in v_faces[i]) / n for k in range(3)]
            ra = [sum((verts[e[0]][k] + verts[e[1]][k]) / 2 for e in v_edges[i]) / len(v_edges[i]) for k in range(3)]
            moved.append([(fa[k] + 2 * ra[k] + (n - 3) * p[k]) / n for k in range(3)])
        out = Mesh()
        out.verts = moved + face_pts + edge_pts
        out.attrs = {}
        for name, vals in mesh.attrs.items():
            col = [v[:] if isinstance(v, list) else v for v in vals]
            col += [attr_mix(name, vals, [(i, 1.0 / len(f)) for i in f]) for f in faces]
            col += [attr_mix(name, vals, [(a, 0.5), (b, 0.5)]) for a, b in edge_faces]
            out.attrs[name] = col
        for fi, f in enumerate(faces):
            uv = mesh.uvs[fi] if fi < len(mesh.uvs) and len(mesh.uvs[fi]) == len(f) else [[0.0, 0.0] for _ in f]
            cu = [sum(u[0] for u in uv) / len(f), sum(u[1] for u in uv) / len(f)]
            count = len(f)
            for i, a in enumerate(f):
                nxt, prv = f[(i + 1) % count], f[i - 1]
                e_next = edge_index[(a, nxt) if a < nxt else (nxt, a)]
                e_prev = edge_index[(prv, a) if prv < a else (a, prv)]
                out.faces.append([a, e_next, nv + fi, e_prev])
                out.face_mat.append(mesh.face_mat[fi])
                out.uvs.append([uv[i][:], _mid(uv[i], uv[(i + 1) % count]), cu, _mid(uv[i - 1], uv[i])])
        out.smooth = True
        mesh = out
    return mesh


def decimate(mesh: Mesh, ratio: float = 0.5) -> int:
    """Cut vertices to about ratio by merging each grid cell into its average. Returns vertices removed.

    ponytail: vertex clustering, fast and robust but blunt on thin details. Upgrade path
    is quadric edge collapse for hero assets.
    """
    from .ops import _remap_faces

    ratio = max(0.01, min(1.0, float(ratio)))
    target = max(8, int(len(mesh.verts) * ratio))
    if target >= len(mesh.verts):
        return 0
    lo, hi = mesh.bounds()
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3))) or 1.0

    def clusters(cell):
        out: dict[tuple, list[int]] = {}
        for i, p in enumerate(mesh.verts):
            out.setdefault((math.floor(p[0] / cell), math.floor(p[1] / cell), math.floor(p[2] / cell)), []).append(i)
        return out

    small, big = diag / 10000.0, diag
    for _ in range(24):
        mid = (small + big) / 2
        if len(clusters(mid)) > target:
            small = mid
        else:
            big = mid
    groups = list(clusters(big).values())
    remap: dict[int, int] = {}
    order = []
    for g in groups:
        keep = g[0]
        mesh.verts[keep] = [sum(mesh.verts[i][a] for i in g) / len(g) for a in range(3)]
        for name, vals in mesh.attrs.items():
            vals[keep] = attr_mix(name, vals, [(i, 1.0 / len(g)) for i in g])
        for i in g:
            remap[i] = len(order)
        order.append(keep)
    before = len(mesh.verts)
    mesh.keep(order)
    _remap_faces(mesh, remap)
    seen, faces, mats, uvs = set(), [], [], []
    for fi, f in enumerate(mesh.faces):
        key = tuple(sorted(f))
        if key in seen:
            continue
        seen.add(key)
        faces.append(f)
        mats.append(mesh.face_mat[fi])
        uvs.append(mesh.uvs[fi])
    mesh.faces, mesh.face_mat, mesh.uvs = faces, mats, uvs
    mesh.drop_unused()
    return before - len(mesh.verts)


def set_values(mesh: Mesh, target: str, idx: Sequence[int], values: Sequence[float], width: int) -> int:
    """Write a stroke the dock already computed: positions ("pos"), shape deltas, or attribute values."""
    n = len(mesh.verts)
    if len(values) != len(idx) * width:
        raise ValueError("values do not match the vertex list")
    if any(not (0 <= int(i) < n) for i in idx):
        raise ValueError("vertex index out of range; the mesh changed since the stroke began")
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("values must be finite numbers")
    if target == "pos":
        for k, i in enumerate(idx):
            mesh.verts[int(i)] = [float(values[k * 3 + a]) for a in range(3)]
        return len(idx)
    col = mesh.ensure_attr(target)
    default = attr_default(target)
    for k, i in enumerate(idx):
        row = [float(values[k * width + a]) for a in range(width)]
        col[int(i)] = row if isinstance(default, list) else row[0]
    return len(idx)
