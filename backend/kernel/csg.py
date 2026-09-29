"""Solid booleans with a BSP tree, after Evan Wallace's csg.js (MIT).

Works on any closed mesh, concave included; UVs are carried through the cuts.
An open mesh still returns a result, but it will have holes.
ponytail: pure Python, fine for props up to a few thousand faces. Cut pieces stay
split along the other mesh's planes (T-junctions). Upgrade path is manifold3d when
dense meshes need clean topology.
"""

from __future__ import annotations

from .mesh import Mesh, vdot

_EPS = 1e-4
_COPLANAR, _FRONT, _BACK, _SPANNING = 0, 1, 2, 3


def _plane(pts: list) -> tuple[list, float] | None:
    nx = ny = nz = 0.0
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    if ln < 1e-9:
        return None
    n = [nx / ln, ny / ln, nz / ln]
    return n, vdot(n, pts[0])


class _Poly:
    __slots__ = ("pts", "uvs", "mat", "n", "w", "data")

    def __init__(self, pts, uvs, mat, n, w, data=None):
        self.pts, self.uvs, self.mat, self.n, self.w = pts, uvs, mat, n, w
        self.data = data  # per-point attribute rows, same length as pts, or None

    def flipped(self) -> "_Poly":
        return _Poly(self.pts[::-1], self.uvs[::-1], self.mat, [-c for c in self.n], -self.w,
                     self.data[::-1] if self.data else None)


def _lerp_row(a: list, b: list, t: float) -> list:
    return [[x + (y - x) * t for x, y in zip(p, q)] if isinstance(p, list) else p + (q - p) * t for p, q in zip(a, b)]


def _split(n, w, poly: _Poly, cof: list, cob: list, front: list, back: list) -> None:
    types = []
    kind = _COPLANAR
    for p in poly.pts:
        t = n[0] * p[0] + n[1] * p[1] + n[2] * p[2] - w
        k = _BACK if t < -_EPS else _FRONT if t > _EPS else _COPLANAR
        kind |= k
        types.append(k)
    if kind == _COPLANAR:
        (cof if vdot(n, poly.n) > 0 else cob).append(poly)
    elif kind == _FRONT:
        front.append(poly)
    elif kind == _BACK:
        back.append(poly)
    else:
        f, b, fu, bu = [], [], [], []
        fd, bd = ([], []) if poly.data else (None, None)
        count = len(poly.pts)
        for i in range(count):
            j = (i + 1) % count
            ti, tj = types[i], types[j]
            vi, vj, ui, uj = poly.pts[i], poly.pts[j], poly.uvs[i], poly.uvs[j]
            if ti != _BACK:
                f.append(vi)
                fu.append(ui)
                if fd is not None:
                    fd.append(poly.data[i])
            if ti != _FRONT:
                b.append(vi)
                bu.append(ui)
                if bd is not None:
                    bd.append(poly.data[i])
            if (ti | tj) == _SPANNING:
                d = [vj[0] - vi[0], vj[1] - vi[1], vj[2] - vi[2]]
                t = (w - vdot(n, vi)) / vdot(n, d)
                v = [vi[0] + d[0] * t, vi[1] + d[1] * t, vi[2] + d[2] * t]
                u = [ui[0] + (uj[0] - ui[0]) * t, ui[1] + (uj[1] - ui[1]) * t]
                f.append(v)
                fu.append(u)
                b.append(v[:])
                bu.append(u[:])
                if fd is not None:
                    row = _lerp_row(poly.data[i], poly.data[j], t)
                    fd.append(row)
                    bd.append(row)
        if len(f) >= 3:
            front.append(_Poly(f, fu, poly.mat, poly.n, poly.w, fd))
        if len(b) >= 3:
            back.append(_Poly(b, bu, poly.mat, poly.n, poly.w, bd))


class _Node:
    __slots__ = ("n", "w", "front", "back", "polys")

    def __init__(self, polys: list | None = None):
        self.n, self.w, self.front, self.back, self.polys = None, 0.0, None, None, []
        if polys:
            self.build(polys)

    def nodes(self) -> list["_Node"]:
        out, stack = [], [self]
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(c for c in (node.front, node.back) if c is not None)
        return out

    def build(self, polys: list) -> None:
        stack = [(self, polys)]
        while stack:
            node, items = stack.pop()
            if not items:
                continue
            if node.n is None:
                node.n, node.w = items[0].n, items[0].w
            front, back = [], []
            for poly in items:
                _split(node.n, node.w, poly, node.polys, node.polys, front, back)
            if front:
                node.front = node.front or _Node()
                stack.append((node.front, front))
            if back:
                node.back = node.back or _Node()
                stack.append((node.back, back))

    def invert(self) -> None:
        for node in self.nodes():
            node.polys = [p.flipped() for p in node.polys]
            if node.n is not None:
                node.n, node.w = [-c for c in node.n], -node.w
            node.front, node.back = node.back, node.front

    def clip_polys(self, polys: list) -> list:
        """Drop the parts of polys inside this solid."""
        out, stack = [], [(self, polys)]
        while stack:
            node, items = stack.pop()
            if node.n is None:
                out.extend(items)
                continue
            front, back = [], []
            for poly in items:
                _split(node.n, node.w, poly, front, back, front, back)
            if node.front is not None:
                stack.append((node.front, front))
            else:
                out.extend(front)
            if node.back is not None:
                stack.append((node.back, back))
        return out

    def clip_to(self, other: "_Node") -> None:
        for node in self.nodes():
            node.polys = other.clip_polys(node.polys)

    def all_polys(self) -> list:
        return [p for node in self.nodes() for p in node.polys]


def _polys(mesh: Mesh, names: list[str]) -> list[_Poly]:
    from .mesh import attr_default
    from .ops import triangulate_ngons

    flat = mesh.copy()
    triangulate_ngons(flat, only_nonplanar=True, tol=0.01, concave=True)  # BSP needs planar, convex faces
    cols = [flat.attrs.get(name) for name in names]
    out = []
    for fi, face in enumerate(flat.faces):
        pts = [flat.verts[i][:] for i in face]
        plane = _plane(pts)
        if plane is None:
            continue
        uvs = flat.uvs[fi] if fi < len(flat.uvs) and len(flat.uvs[fi]) == len(face) else [[0.0, 0.0] for _ in face]
        data = [[col[i] if col else attr_default(name) for col, name in zip(cols, names)] for i in face] if names else None
        out.append(_Poly(pts, [u[:] for u in uvs], flat.face_mat[fi] if fi < len(flat.face_mat) else "default", *plane, data))
    return out


def boolean(a: Mesh, b: Mesh, op: str = "difference") -> Mesh:
    op = (op or "difference").lower()
    if op not in {"union", "difference", "intersect"}:
        raise ValueError("boolean op must be union, difference, or intersect")
    names = sorted(set(a.attrs) | set(b.attrs))
    na, nb = _Node(_polys(a, names)), _Node(_polys(b, names))
    if op == "union":
        na.clip_to(nb)
        nb.clip_to(na)
        nb.invert()
        nb.clip_to(na)
        nb.invert()
        na.build(nb.all_polys())
    elif op == "difference":
        na.invert()
        na.clip_to(nb)
        nb.clip_to(na)
        nb.invert()
        nb.clip_to(na)
        nb.invert()
        na.build(nb.all_polys())
        na.invert()
    else:
        na.invert()
        nb.clip_to(na)
        nb.invert()
        na.clip_to(nb)
        nb.clip_to(na)
        na.build(nb.all_polys())
        na.invert()
    from .ops import merge_by_distance

    mesh = Mesh()
    cols: list[list] = [[] for _ in names]
    for poly in na.all_polys():
        mesh.add_face([mesh.add_vert(p) for p in poly.pts], poly.mat, poly.uvs)
        for row in poly.data or []:
            for col, value in zip(cols, row):
                col.append(value[:] if isinstance(value, list) else value)
    mesh.attrs = dict(zip(names, cols))
    if not mesh.faces:
        raise ValueError(f"The {op} left nothing. Check that the shapes overlap.")
    merge_by_distance(mesh, 0.05)
    return mesh
