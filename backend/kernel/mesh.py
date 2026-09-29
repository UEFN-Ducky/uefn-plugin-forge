"""Polygon mesh plus the primitive generators. Triangulated only for display and export."""

from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

Vec3 = list[float]
Vec2 = list[float]


def vadd(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def vsub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def vmul(a: Sequence[float], s: float) -> Vec3:
    return [a[0] * s, a[1] * s, a[2] * s]


def vdot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def vlen(a: Sequence[float]) -> float:
    return math.sqrt(vdot(a, a))


def vnorm(a: Sequence[float]) -> Vec3:
    n = vlen(a)
    if n < 1e-12:
        return [0.0, 0.0, 1.0]
    return [a[0] / n, a[1] / n, a[2] / n]


def lerp(a: Sequence[float], b: Sequence[float], t: float) -> Vec3:
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]


def attr_default(name: str):
    """Per-vertex attribute kinds: wt:<group> weight, sk:<key> shape delta, col:<name> RGBA."""
    if name.startswith("sk:"):
        return [0.0, 0.0, 0.0]
    if name.startswith("col:"):
        return [0.0, 0.0, 0.0, 1.0]
    return 0.0


def attr_mix(name: str, values: list, src):
    """Value for a new vertex: default (None), a copy (index), or a blend ([(index, weight), ...])."""
    if src is None:
        return attr_default(name)
    if isinstance(src, int):
        v = values[src]
        return v[:] if isinstance(v, list) else v
    first = values[src[0][0]]
    if isinstance(first, list):
        out = [0.0] * len(first)
        for i, w in src:
            for k, c in enumerate(values[i]):
                out[k] += w * c
        return out
    return sum(w * values[i] for i, w in src)


def flip_side(name: str) -> str:
    """wt:upperarm_l <-> wt:upperarm_r, so a mirrored half weights to the other side's bones."""
    for a, b in (("_l", "_r"), ("_r", "_l"), ("_L", "_R"), ("_R", "_L")):
        if name.startswith("wt:") and name.endswith(a):
            return name[: -len(a)] + b
    return name


class Mesh:
    def __init__(self) -> None:
        self.verts: list[Vec3] = []
        self.faces: list[list[int]] = []
        self.face_mat: list[str] = []
        self.uvs: list[list[Vec2]] = []
        self.smooth: bool = False
        self.attrs: dict[str, list] = {}

    def add_vert(self, p: Sequence[float], src=None) -> int:
        """src carries per-vertex attributes: an index to copy, or [(index, weight), ...] to blend."""
        self.verts.append([float(p[0]), float(p[1]), float(p[2])])
        for name, values in self.attrs.items():
            values.append(attr_mix(name, values, src))
        return len(self.verts) - 1

    def keep(self, order: Sequence[int]) -> None:
        """Keep only these vertex indices, in this order, attributes included. Faces are the caller's job."""
        self.verts = [self.verts[i][:] for i in order]
        self.attrs = {name: [vals[i][:] if isinstance(vals[i], list) else vals[i] for i in order] for name, vals in self.attrs.items()}

    def ensure_attr(self, name: str) -> list:
        values = self.attrs.get(name)
        if values is None or len(values) != len(self.verts):
            values = self.attrs[name] = [attr_default(name) for _ in self.verts]
        return values

    def add_face(self, idxs: Sequence[int], mat: str = "default", uvs: Sequence[Sequence[float]] | None = None) -> int:
        face = [int(i) for i in idxs]
        if len(set(face)) < 3:
            return -1
        self.faces.append(face)
        self.face_mat.append(mat or "default")
        if uvs is None or len(uvs) != len(face):
            self.uvs.append(_uv_for_face(self.verts, face))
        else:
            self.uvs.append([[float(u[0]), float(u[1])] for u in uvs])
        return len(self.faces) - 1

    def tri_uv(self, fi: int, tri: Sequence[int]) -> list[float]:
        """Flat [u, v, ...] where face fi meets each vertex of tri; zeros when the face has no UVs of its own
        (stored meshes from a .blend import carry none)."""
        face = self.faces[fi]
        uvs = self.uvs[fi] if fi < len(self.uvs) else ()
        if len(uvs) != len(face):
            return [0.0] * (2 * len(tri))
        return [float(c) for v in tri for c in uvs[face.index(v)][:2]]

    def copy(self) -> "Mesh":
        m = Mesh()
        m.verts = [v[:] for v in self.verts]
        m.faces = [f[:] for f in self.faces]
        m.face_mat = self.face_mat[:]
        m.uvs = [[u[:] for u in face] for face in self.uvs]
        m.smooth = self.smooth
        m.attrs = {name: [v[:] if isinstance(v, list) else v for v in vals] for name, vals in self.attrs.items()}
        return m

    def to_dict(self) -> dict:
        out = {
            "verts": self.verts,
            "faces": self.faces,
            "face_mat": self.face_mat,
            "uvs": self.uvs,
            "smooth": self.smooth,
        }
        if self.attrs:  # absent when empty, so meshes without attributes keep their stored bytes
            out["attrs"] = self.attrs
        return out

    @classmethod
    def from_dict(cls, data: dict | None) -> "Mesh":
        m = cls()
        if not data:
            return m
        m.verts = [[float(x) for x in v] for v in data.get("verts") or []]
        m.faces = [[int(i) for i in f] for f in data.get("faces") or []]
        mats = list(data.get("face_mat") or [])
        while len(mats) < len(m.faces):
            mats.append("default")
        m.face_mat = [str(x) for x in mats[: len(m.faces)]]
        uvs = list(data.get("uvs") or [])
        m.uvs = []
        for i, face in enumerate(m.faces):
            raw = uvs[i] if i < len(uvs) else None
            if raw and len(raw) == len(face):
                m.uvs.append([[float(u[0]), float(u[1])] for u in raw])
            else:
                m.uvs.append([[0.0, 0.0] for _ in face])
        m.smooth = bool(data.get("smooth"))
        for name, vals in (data.get("attrs") or {}).items():
            if len(vals) == len(m.verts):
                m.attrs[str(name)] = [[float(c) for c in v] if isinstance(v, list) else float(v) for v in vals]
        return m

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.verts:
            return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        xs = [v[0] for v in self.verts]
        ys = [v[1] for v in self.verts]
        zs = [v[2] for v in self.verts]
        return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]

    def centroid(self) -> Vec3:
        if not self.verts:
            return [0.0, 0.0, 0.0]
        n = float(len(self.verts))
        return [sum(v[0] for v in self.verts) / n, sum(v[1] for v in self.verts) / n, sum(v[2] for v in self.verts) / n]

    def face_normal(self, fi: int) -> Vec3:
        return _newell(self.verts, self.faces[fi])

    def face_center(self, fi: int) -> Vec3:
        f = self.faces[fi]
        n = float(len(f))
        return [
            sum(self.verts[i][0] for i in f) / n,
            sum(self.verts[i][1] for i in f) / n,
            sum(self.verts[i][2] for i in f) / n,
        ]

    def tri_count(self) -> int:
        return sum(max(0, len(f) - 2) for f in self.faces)

    def triangles(self) -> list[tuple[int, int, int, int]]:
        """Returns (i0, i1, i2, face_index). Fans tris and quads; ear-clips concave n-gons.

        ponytail: quads are assumed convex so dense meshes skip the test.
        """
        out: list[tuple[int, int, int, int]] = []
        for fi, face in enumerate(self.faces):
            if len(face) < 3:
                continue
            if len(face) >= 5:
                out.extend((a, b, c, fi) for a, b, c in face_triangles(self.verts, face))
                continue
            a = face[0]
            for k in range(1, len(face) - 1):
                out.append((a, face[k], face[k + 1], fi))
        return out

    def edge_keys(self) -> list[tuple[int, int]]:
        seen: set[tuple[int, int]] = set()
        out: list[tuple[int, int]] = []
        for face in self.faces:
            for i, a in enumerate(face):
                b = face[(i + 1) % len(face)]
                key = (a, b) if a < b else (b, a)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    def translate(self, delta: Sequence[float]) -> None:
        for v in self.verts:
            v[0] += delta[0]
            v[1] += delta[1]
            v[2] += delta[2]

    def scale(self, s: Sequence[float], origin: Sequence[float] | None = None) -> None:
        o = origin or [0.0, 0.0, 0.0]
        for v in self.verts:
            v[0] = o[0] + (v[0] - o[0]) * s[0]
            v[1] = o[1] + (v[1] - o[1]) * s[1]
            v[2] = o[2] + (v[2] - o[2]) * s[2]
        for name, vals in self.attrs.items():
            if name.startswith("sk:"):  # shape deltas are directions: scale, never offset
                for d in vals:
                    d[0], d[1], d[2] = d[0] * s[0], d[1] * s[1], d[2] * s[2]

    def transform(self, pos: Sequence[float], rot_deg: Sequence[float], scale: Sequence[float]) -> None:
        r = _euler(rot_deg)
        for v in self.verts:
            x, y, z = v[0] * scale[0], v[1] * scale[1], v[2] * scale[2]
            v[0] = r[0][0] * x + r[0][1] * y + r[0][2] * z + pos[0]
            v[1] = r[1][0] * x + r[1][1] * y + r[1][2] * z + pos[1]
            v[2] = r[2][0] * x + r[2][1] * y + r[2][2] * z + pos[2]
        for name, vals in self.attrs.items():
            if name.startswith("sk:"):
                for d in vals:
                    x, y, z = d[0] * scale[0], d[1] * scale[1], d[2] * scale[2]
                    d[0] = r[0][0] * x + r[0][1] * y + r[0][2] * z
                    d[1] = r[1][0] * x + r[1][1] * y + r[1][2] * z
                    d[2] = r[2][0] * x + r[2][1] * y + r[2][2] * z

    def append(self, other: "Mesh") -> None:
        base = len(self.verts)
        for name in set(self.attrs) | set(other.attrs):
            mine = self.attrs.get(name)
            if mine is None:
                mine = self.attrs[name] = [attr_default(name) for _ in range(base)]
            theirs = other.attrs.get(name)
            if theirs:
                mine.extend((v[:] if isinstance(v, list) else v) for v in theirs)
            else:
                mine.extend(attr_default(name) for _ in other.verts)
        self.verts.extend(v[:] for v in other.verts)
        for i, face in enumerate(other.faces):
            self.faces.append([base + j for j in face])
            self.face_mat.append(other.face_mat[i] if i < len(other.face_mat) else "default")
            uvs = other.uvs[i] if i < len(other.uvs) else []
            self.uvs.append([u[:] for u in uvs] if len(uvs) == len(face) else _uv_for_face(self.verts, self.faces[-1]))

    def drop_unused(self) -> None:
        used = sorted({i for face in self.faces for i in face})
        remap = {old: new for new, old in enumerate(used)}
        self.keep(used)
        self.faces = [[remap[i] for i in face] for face in self.faces]

    def orient_convex(self) -> None:
        """Flip faces so normals point away from the vertex centroid. Convex meshes only."""
        c = self.centroid()
        self.orient_away(lambda _fc: c)

    def orient_away(self, inside_at: Callable[[Vec3], Vec3]) -> None:
        for fi, face in enumerate(self.faces):
            n = self.face_normal(fi)
            fc = self.face_center(fi)
            inside = inside_at(fc)
            if vdot(n, vsub(fc, inside)) < 0:
                face.reverse()
                if fi < len(self.uvs):
                    self.uvs[fi].reverse()


def _newell(verts: list[Vec3], face: Sequence[int]) -> Vec3:
    nx = ny = nz = 0.0
    n = len(face)
    for i in range(n):
        a = verts[face[i]]
        b = verts[face[(i + 1) % n]]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return vnorm([nx, ny, nz])


def _turn(verts: list[Vec3], face: Sequence[int]) -> Callable[[int, int, int], float]:
    """2D cross product of o->p, o->q on the face's plane, positive for a turn the face's way."""
    n = _newell(verts, face)
    ax = max(range(3), key=lambda i: abs(n[i]))
    a, b = [i for i in range(3) if i != ax]
    pts = {i: (verts[i][a], verts[i][b]) for i in face}
    count = len(face)
    area = sum(pts[face[k]][0] * pts[face[(k + 1) % count]][1] - pts[face[(k + 1) % count]][0] * pts[face[k]][1] for k in range(count))
    s = 1.0 if area >= 0 else -1.0

    def turn(o: int, p: int, q: int) -> float:
        return s * ((pts[p][0] - pts[o][0]) * (pts[q][1] - pts[o][1]) - (pts[p][1] - pts[o][1]) * (pts[q][0] - pts[o][0]))

    return turn


def is_convex(verts: list[Vec3], face: Sequence[int]) -> bool:
    turn = _turn(verts, face)
    return all(turn(face[k - 1], face[k], face[(k + 1) % len(face)]) >= -1e-9 for k in range(len(face)))


def face_triangles(verts: list[Vec3], face: Sequence[int]) -> list[tuple[int, int, int]]:
    """Triangles of one planar face in its own winding: a fan when convex, ear clipping when not."""
    idx = list(face)
    if is_convex(verts, face):
        return [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
    turn = _turn(verts, face)
    tris: list[tuple[int, int, int]] = []
    while len(idx) > 3:
        for k in range(len(idx)):
            p0, p1, p2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if turn(p0, p1, p2) <= 1e-12:
                continue
            if any(turn(p0, p1, q) >= 0 and turn(p1, p2, q) >= 0 and turn(p2, p0, q) >= 0 for q in idx if q not in (p0, p1, p2)):
                continue
            tris.append((p0, p1, p2))
            idx.pop(k)
            break
        else:
            break  # self-intersecting outline: fan the rest rather than loop forever
    tris.extend((idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1))
    return tris


def _uv_for_face(verts: list[Vec3], face: Sequence[int]) -> list[Vec2]:
    if len(face) < 3:
        return [[0.0, 0.0] for _ in face]
    n = _newell(verts, face)
    ax = max(range(3), key=lambda i: abs(n[i]))
    axes = [i for i in range(3) if i != ax]
    pts = [(verts[i][axes[0]], verts[i][axes[1]]) for i in face]
    min_u = min(p[0] for p in pts)
    min_v = min(p[1] for p in pts)
    max_u = max(p[0] for p in pts)
    max_v = max(p[1] for p in pts)
    du = max_u - min_u or 1.0
    dv = max_v - min_v or 1.0
    return [[(p[0] - min_u) / du, (p[1] - min_v) / dv] for p in pts]


def _euler(rot_deg: Sequence[float]) -> list[list[float]]:
    """Intrinsic XYZ, degrees. Returns a 3x3 row-major matrix."""
    rx, ry, rz = (math.radians(float(rot_deg[i])) if i < len(rot_deg) else 0.0 for i in range(3))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # R = Rz * Ry * Rx
    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def _quad(m: Mesh, a: int, b: int, c: int, d: int, mat: str) -> None:
    m.add_face([a, b, c, d], mat)


def box(w: float = 100.0, d: float = 100.0, h: float = 100.0, mat: str = "default") -> Mesh:
    x0, x1 = -w / 2.0, w / 2.0
    y0, y1 = -d / 2.0, d / 2.0
    m = Mesh()
    for z in (0.0, h):
        for y in (y0, y1):
            for x in (x0, x1):
                m.add_vert((x, y, z))
    # 0 (-x,-y,0) 1 (+x,-y,0) 2 (-x,+y,0) 3 (+x,+y,0)
    # 4 (-x,-y,h) 5 (+x,-y,h) 6 (-x,+y,h) 7 (+x,+y,h)
    _quad(m, 0, 1, 3, 2, mat)  # bottom, fixed by orient
    _quad(m, 4, 6, 7, 5, mat)
    _quad(m, 0, 4, 5, 1, mat)
    _quad(m, 3, 7, 6, 2, mat)
    _quad(m, 0, 2, 6, 4, mat)
    _quad(m, 1, 5, 7, 3, mat)
    m.orient_convex()
    return m


def plane(w: float = 100.0, d: float = 100.0, mat: str = "default") -> Mesh:
    return grid(w, d, 1, 1, mat)


def grid(w: float = 100.0, d: float = 100.0, seg_x: int = 4, seg_y: int = 4, mat: str = "default") -> Mesh:
    seg_x = max(1, int(seg_x))
    seg_y = max(1, int(seg_y))
    m = Mesh()
    ids: list[list[int]] = []
    for iy in range(seg_y + 1):
        row = []
        y = -d / 2.0 + d * iy / seg_y
        for ix in range(seg_x + 1):
            x = -w / 2.0 + w * ix / seg_x
            row.append(m.add_vert((x, y, 0.0)))
        ids.append(row)
    for iy in range(seg_y):
        for ix in range(seg_x):
            _quad(m, ids[iy][ix], ids[iy][ix + 1], ids[iy + 1][ix + 1], ids[iy + 1][ix], mat)
    return m


def _ring(m: Mesh, radius: float, z: float, seg: int, y_scale: float = 1.0) -> list[int]:
    ids = []
    for i in range(seg):
        a = math.tau * i / seg
        ids.append(m.add_vert((math.cos(a) * radius, math.sin(a) * radius * y_scale, z)))
    return ids


def cylinder(radius: float = 40.0, height: float = 100.0, seg: int = 16, mat: str = "default") -> Mesh:
    seg = max(3, int(seg))
    m = Mesh()
    bot = _ring(m, radius, 0.0, seg)
    top = _ring(m, radius, height, seg)
    bc = m.add_vert((0.0, 0.0, 0.0))
    tc = m.add_vert((0.0, 0.0, height))
    for i in range(seg):
        j = (i + 1) % seg
        m.add_face([bot[i], bot[j], top[j], top[i]], mat)
        m.add_face([bc, bot[j], bot[i]], mat)
        m.add_face([tc, top[i], top[j]], mat)
    m.orient_convex()
    return m


def cone(radius: float = 40.0, height: float = 100.0, seg: int = 16, mat: str = "default") -> Mesh:
    seg = max(3, int(seg))
    m = Mesh()
    bot = _ring(m, radius, 0.0, seg)
    bc = m.add_vert((0.0, 0.0, 0.0))
    tip = m.add_vert((0.0, 0.0, height))
    for i in range(seg):
        j = (i + 1) % seg
        m.add_face([bot[i], bot[j], tip], mat)
        m.add_face([bc, bot[j], bot[i]], mat)
    m.orient_convex()
    return m


def sphere(radius: float = 50.0, seg: int = 16, rings: int = 8, mat: str = "default") -> Mesh:
    seg = max(3, int(seg))
    rings = max(2, int(rings))
    m = Mesh()
    rows: list[list[int]] = []
    for r in range(rings + 1):
        v = r / rings
        phi = math.pi * v
        z = radius * math.cos(phi) + radius  # sit on z=0
        rad = radius * math.sin(phi)
        if r == 0 or r == rings:
            rows.append([m.add_vert((0.0, 0.0, z))])
        else:
            rows.append(_ring(m, rad, z, seg))
    for r in range(rings):
        a, b = rows[r], rows[r + 1]
        if len(a) == 1 and len(b) == seg:
            for i in range(seg):
                m.add_face([a[0], b[i], b[(i + 1) % seg]], mat)
        elif len(b) == 1 and len(a) == seg:
            for i in range(seg):
                m.add_face([a[i], b[0], a[(i + 1) % seg]], mat)
        else:
            for i in range(seg):
                j = (i + 1) % seg
                m.add_face([a[i], a[j], b[j], b[i]], mat)
    m.smooth = True
    m.orient_convex()
    return m


def torus(major: float = 40.0, minor: float = 12.0, seg: int = 16, rings: int = 8, mat: str = "default") -> Mesh:
    seg = max(3, int(seg))
    rings = max(3, int(rings))
    m = Mesh()
    rows: list[list[int]] = []
    for i in range(seg):
        theta = math.tau * i / seg
        ct, st = math.cos(theta), math.sin(theta)
        row = []
        for j in range(rings):
            phi = math.tau * j / rings
            cp, sp = math.cos(phi), math.sin(phi)
            rad = major + minor * cp
            # ring in XY, tube around it, then lift so the bottom sits on z=0
            row.append(m.add_vert((rad * ct, rad * st, minor * sp + major + minor)))
        rows.append(row)
    for i in range(seg):
        ni = (i + 1) % seg
        for j in range(rings):
            nj = (j + 1) % rings
            m.add_face([rows[i][j], rows[ni][j], rows[ni][nj], rows[i][nj]], mat)

    def inside(fc: Vec3) -> Vec3:
        # nearest point on the major circle, raised to the torus mid height
        flat = vnorm([fc[0], fc[1], 0.0])
        return [flat[0] * major, flat[1] * major, major + minor]

    m.smooth = True
    m.orient_away(inside)
    return m


def capsule(radius: float = 25.0, height: float = 60.0, seg: int = 12, mat: str = "default") -> Mesh:
    """Cylinder of `height` plus hemispheres. Total height is height + 2*radius."""
    seg = max(3, int(seg))
    rings = max(2, seg // 2)
    m = Mesh()
    # bottom hemisphere, then cylinder rings, then top hemisphere
    rows: list[list[int]] = []
    for r in range(rings + 1):
        phi = math.pi * 0.5 * r / rings  # 0 at bottom pole to pi/2 at equator
        z = radius - radius * math.cos(phi)
        rad = radius * math.sin(phi)
        if r == 0:
            rows.append([m.add_vert((0.0, 0.0, z))])
        else:
            rows.append(_ring(m, rad, z, seg))
    equator = rows[-1]
    top_z = radius + height
    top_eq = _ring(m, radius, top_z, seg)
    for r in range(rings, 0, -1):
        phi = math.pi * 0.5 * r / rings
        z = top_z + radius * math.cos(phi)
        rad = radius * math.sin(phi)
        if r == rings:
            continue
        rows.append(_ring(m, rad, z, seg))
    pole = m.add_vert((0.0, 0.0, top_z + radius))
    # bottom cap faces
    for r in range(rings):
        a, b = rows[r], rows[r + 1]
        if len(a) == 1:
            for i in range(seg):
                m.add_face([a[0], b[(i + 1) % seg], b[i]], mat)
        else:
            for i in range(seg):
                j = (i + 1) % seg
                m.add_face([a[i], a[j], b[j], b[i]], mat)
    for i in range(seg):
        j = (i + 1) % seg
        m.add_face([equator[i], equator[j], top_eq[j], top_eq[i]], mat)
    # top hemisphere rows were appended after equator row index `rings`
    hemi = rows[rings:]  # starts at equator duplicate? rows[rings] is equator
    # rows[0]=pole ... rows[rings]=equator. We did not append more into rows after.
    # Rebuild top from top_eq down using the rings we skipped. Simpler second pass:
    prev = top_eq
    for r in range(rings - 1, 0, -1):
        phi = math.pi * 0.5 * r / rings
        z = top_z + radius * math.cos(phi)
        rad = radius * math.sin(phi)
        ring = _ring(m, rad, z, seg)
        for i in range(seg):
            j = (i + 1) % seg
            m.add_face([prev[i], prev[j], ring[j], ring[i]], mat)
        prev = ring
    for i in range(seg):
        m.add_face([prev[i], pole, prev[(i + 1) % seg]], mat)
    m.smooth = True
    m.orient_convex()
    return m


def wedge(w: float = 100.0, d: float = 100.0, h: float = 100.0, mat: str = "default") -> Mesh:
    """Ramp: high at -Y, low (z=0) at +Y."""
    x0, x1 = -w / 2.0, w / 2.0
    y0, y1 = -d / 2.0, d / 2.0
    m = Mesh()
    a = m.add_vert((x0, y0, 0.0))
    b = m.add_vert((x1, y0, 0.0))
    c = m.add_vert((x1, y1, 0.0))
    d0 = m.add_vert((x0, y1, 0.0))
    e = m.add_vert((x0, y0, h))
    f = m.add_vert((x1, y0, h))
    m.add_face([a, b, c, d0], mat)
    m.add_face([a, e, f, b], mat)
    m.add_face([e, a, d0], mat)  # may be wrong, orient fixes convex
    m.add_face([b, f, c], mat)
    m.add_face([e, d0, c, f], mat)
    m.orient_convex()
    return m


def stairs(w: float = 100.0, d: float = 120.0, h: float = 100.0, steps: int = 4, mat: str = "default") -> Mesh:
    steps = max(1, int(steps))
    m = Mesh()
    sd = d / steps
    sh = h / steps
    for i in range(steps):
        step = box(w, sd, sh * (i + 1), mat)
        step.translate((0.0, -d / 2.0 + sd * (i + 0.5), 0.0))
        m.append(step)
    return m


def arch(w: float = 120.0, d: float = 40.0, h: float = 180.0, thickness: float = 20.0, seg: int = 8, mat: str = "default") -> Mesh:
    """Doorway arch: two jambs and a semicircular top, extruded along Y."""
    seg = max(2, int(seg))
    thickness = min(thickness, w / 2.0 - 1.0, h / 2.0)
    spring = h - w / 2.0
    if spring < thickness:
        spring = thickness
    m = Mesh()
    # outer and inner profiles in XZ, from left base, up, around the arc, down the right
    outer: list[tuple[float, float]] = [(-w / 2.0, 0.0), (-w / 2.0, spring)]
    inner: list[tuple[float, float]] = [(-w / 2.0 + thickness, 0.0), (-w / 2.0 + thickness, spring)]
    for i in range(seg + 1):
        a = math.pi * i / seg
        # i=0 at +X? we walk from left (pi) to right (0)
        ang = math.pi - a
        outer.append((math.cos(ang) * (w / 2.0), spring + math.sin(ang) * (w / 2.0)))
        inner.append((math.cos(ang) * (w / 2.0 - thickness), spring + math.sin(ang) * (w / 2.0 - thickness)))
    outer.append((w / 2.0, 0.0))
    inner.append((w / 2.0 - thickness, 0.0))
    # close along the ground? no, it's an arch opening. Extrude the strip between outer and inner.
    y0, y1 = -d / 2.0, d / 2.0
    n = len(outer)
    back = []
    front = []
    for p in outer + inner:
        back.append(m.add_vert((p[0], y0, p[1])))
    for p in outer + inner:
        front.append(m.add_vert((p[0], y1, p[1])))
    # side quads along the strip, both long edges
    for i in range(n - 1):
        # outer wall
        _quad(m, back[i], back[i + 1], front[i + 1], front[i], mat)
        # inner wall (reverse)
        oi, oj = n + i, n + i + 1
        _quad(m, back[oi], front[oi], front[oj], back[oj], mat)
    # end caps: quad between outer[i] and inner[i]
    for i in range(n - 1):
        _quad(m, back[i], back[n + i], back[n + i + 1], back[i + 1], mat)
        _quad(m, front[i], front[i + 1], front[n + i + 1], front[n + i], mat)
    return m


def lathe(profile: Sequence[Sequence[float]], seg: int = 16, mat: str = "default") -> Mesh:
    """Rotate an (radius, z) profile around Z. Profile is open; caps if an end radius is 0."""
    seg = max(3, int(seg))
    pts = [(float(p[0]), float(p[1])) for p in profile]
    if len(pts) < 2:
        return Mesh()
    m = Mesh()
    rows: list[list[int]] = []
    for r, z in pts:
        if r <= 1e-8:
            rows.append([m.add_vert((0.0, 0.0, z))] * seg)  # one pole, so caps are real triangles
        else:
            rows.append([m.add_vert((r * math.cos(math.tau * i / seg), r * math.sin(math.tau * i / seg), z)) for i in range(seg)])
    for i in range(seg):
        j = (i + 1) % seg
        for k in range(len(pts) - 1):
            face = [rows[k][i], rows[k][j], rows[k + 1][j], rows[k + 1][i]]
            m.add_face([v for n, v in enumerate(face) if v != face[n - 1]], mat)  # add_face drops a pole-to-pole sliver
    m.smooth = True
    # Convex only when the profile radius is monotone-ish. Orient against centroid is
    # right for vases that contain the centroid; skip when the centroid is outside.
    c = m.centroid()
    if c[0] * c[0] + c[1] * c[1] < (max(p[0] for p in pts) * 0.5) ** 2:
        m.orient_convex()
    return m


def extrude_polygon(points: Sequence[Sequence[float]], depth: float = 20.0, mat: str = "default") -> Mesh:
    """Extrude a CCW XY polygon along +Z by `depth`. Base sits on z=0."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 3:
        return Mesh()
    m = Mesh()
    bot = [m.add_vert((x, y, 0.0)) for x, y in pts]
    top = [m.add_vert((x, y, depth)) for x, y in pts]
    m.add_face(list(reversed(bot)), mat)
    m.add_face(top, mat)
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        m.add_face([bot[i], bot[j], top[j], top[i]], mat)
    return m


def build_primitive(kind: str, params: dict | None = None, mat: str = "default") -> Mesh:
    p = dict(params or {})
    p.pop("mat", None)
    kind = (kind or "box").lower().replace("-", "_")
    if kind == "rounded_box":
        from .ops import bevel_edges

        radius = float(p.get("radius", 64.0))
        m = box(float(p.get("w", 512)), float(p.get("d", 512)), float(p.get("h", 512)), mat)
        bevel_edges(m, m.edge_keys(), radius, segments=max(1, int(p.get("segments", 1))))
        return m
    table = {
        "box": lambda: box(p.get("w", 512), p.get("d", 512), p.get("h", 512), mat),
        "plane": lambda: plane(p.get("w", 512), p.get("d", 512), mat),
        "grid": lambda: grid(p.get("w", 512), p.get("d", 512), int(p.get("seg_x", 4)), int(p.get("seg_y", 4)), mat),
        "sphere": lambda: sphere(p.get("radius", 256), int(p.get("seg", 16)), int(p.get("rings", 8)), mat),
        "cylinder": lambda: cylinder(p.get("radius", 256), p.get("height", 512), int(p.get("seg", 16)), mat),
        "cone": lambda: cone(p.get("radius", 256), p.get("height", 512), int(p.get("seg", 16)), mat),
        "torus": lambda: torus(p.get("major", 256), p.get("minor", 64), int(p.get("seg", 16)), int(p.get("rings", 8)), mat),
        "capsule": lambda: capsule(p.get("radius", 128), p.get("height", 256), int(p.get("seg", 12)), mat),
        "wedge": lambda: wedge(p.get("w", 512), p.get("d", 512), p.get("h", 512), mat),
        "stairs": lambda: stairs(p.get("w", 512), p.get("d", 512), p.get("h", 512), int(p.get("steps", 4)), mat),
        "arch": lambda: arch(p.get("w", 512), p.get("d", 128), p.get("h", 512), p.get("thickness", 64), int(p.get("seg", 8)), mat),
        "lathe": lambda: lathe(p.get("profile") or [[0, 0], [128, 0], [192, 192], [96, 384], [0, 512]], int(p.get("seg", 16)), mat),
        "extrude": lambda: extrude_polygon(p.get("points") or [[-256, -128], [256, -128], [256, 128], [-256, 128]], p.get("depth", 64), mat),
    }
    fn = table.get(kind)
    if fn is None:
        raise ValueError(f"Unknown primitive {kind!r}. Known: {', '.join(sorted(table) + ['rounded_box'])}")
    return fn()


def edge_loops(mesh: Mesh) -> list[list[tuple[int, int]]]:
    """Quad edge loops. Each loop is a list of undirected edges."""
    faces_of: dict[tuple[int, int], list[int]] = {}
    for fi, face in enumerate(mesh.faces):
        if len(face) != 4:
            continue
        for i, a in enumerate(face):
            b = face[(i + 1) % 4]
            key = (a, b) if a < b else (b, a)
            faces_of.setdefault(key, []).append(fi)

    def opposite(fi: int, edge: tuple[int, int]) -> tuple[int, int] | None:
        face = mesh.faces[fi]
        if len(face) != 4:
            return None
        a, b = edge
        try:
            ia = face.index(a)
        except ValueError:
            return None
        # rotate so edge starts at ia
        if face[(ia + 1) % 4] == b:
            c, d = face[(ia + 2) % 4], face[(ia + 3) % 4]
            return (c, d) if c < d else (d, c)
        if face[(ia - 1) % 4] == b:
            c, d = face[(ia + 1) % 4], face[(ia + 2) % 4]
            return (c, d) if c < d else (d, c)
        return None

    seen: set[tuple[int, int]] = set()
    loops: list[list[tuple[int, int]]] = []
    for start in list(faces_of):
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        for direction in (1, -1):
            cur = start
            while True:
                nxt = None
                for fi in faces_of.get(cur, []):
                    opp = opposite(fi, cur)
                    if opp and opp not in seen and opp in faces_of:
                        nxt = opp
                        break
                    if opp and opp == start and direction == 1 and len(loop) > 2:
                        nxt = start
                        break
                if nxt is None or nxt == start:
                    break
                if nxt in seen:
                    break
                seen.add(nxt)
                if direction == 1:
                    loop.append(nxt)
                else:
                    loop.insert(0, nxt)
                cur = nxt
        loops.append(loop)
    return loops
