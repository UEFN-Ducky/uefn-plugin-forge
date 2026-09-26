"""Convex-convex booleans by clipping faces to half-spaces.

ponytail: O(faces^2) and only correct when both meshes are convex (boxes, spheres,
cylinders, cones, capsules, wedges). Non-convex inputs raise. Upgrade path is
manifold3d if a prop needs a real boolean.
"""

from __future__ import annotations

from .mesh import Mesh, Vec3, vdot, vsub

_EPS = 1e-4


def _convex(mesh: Mesh, eps: float = 0.05) -> bool:
    if len(mesh.faces) < 4:
        return False
    for fi, face in enumerate(mesh.faces):
        n = mesh.face_normal(fi)
        p0 = mesh.verts[face[0]]
        for v in mesh.verts:
            # outward normal: every vertex is on the inside (negative) side
            if vdot(n, vsub(v, p0)) > eps:
                return False
    return True


def _side(v: Vec3, n: Vec3, p0: Vec3) -> float:
    return vdot(n, vsub(v, p0))


def _hit(a: Vec3, b: Vec3, sa: float, sb: float) -> Vec3:
    den = sa - sb
    t = 0.0 if abs(den) < 1e-12 else sa / den
    t = min(1.0, max(0.0, t))
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]


def _split(poly: list[Vec3], n: Vec3, p0: Vec3) -> tuple[list[Vec3], list[Vec3]]:
    """Split by an outward plane. Inside is the negative side."""
    if len(poly) < 3:
        return [], []
    sides = [_side(v, n, p0) for v in poly]
    if all(s <= _EPS for s in sides):
        # Fully inside, or lying on the plane. On-plane is the solid boundary, not outside.
        return [p[:] for p in poly], []
    if all(s >= -_EPS for s in sides):
        return [], [p[:] for p in poly]
    inside: list[Vec3] = []
    outside: list[Vec3] = []
    for i, cur in enumerate(poly):
        nxt = poly[(i + 1) % len(poly)]
        sc, sn = _side(cur, n, p0), _side(nxt, n, p0)
        cur_in, cur_out = sc <= _EPS, sc >= -_EPS
        if cur_in:
            inside.append(cur[:])
        if cur_out:
            outside.append(cur[:])
        if (sc > _EPS and sn < -_EPS) or (sc < -_EPS and sn > _EPS):
            p = _hit(cur, nxt, sc, sn)
            inside.append(p)
            outside.append(p[:])
    if len(inside) < 3:
        inside = []
    if len(outside) < 3:
        outside = []
    return inside, outside


def _planes(mesh: Mesh) -> list[tuple[Vec3, Vec3]]:
    return [(mesh.face_normal(fi), mesh.verts[mesh.faces[fi][0]][:]) for fi in range(len(mesh.faces))]


def _fragments(mesh: Mesh, planes: list[tuple[Vec3, Vec3]], want: str) -> list[tuple[list[Vec3], str]]:
    """want 'inside' keeps the part in the convex volume; 'outside' keeps the rest."""
    out: list[tuple[list[Vec3], str]] = []
    for fi, face in enumerate(mesh.faces):
        mat = mesh.face_mat[fi] if mesh.face_mat else "default"
        current = [[mesh.verts[i][:] for i in face]]
        outside: list[list[Vec3]] = []
        for n, p0 in planes:
            nxt: list[list[Vec3]] = []
            for poly in current:
                inn, outp = _split(poly, n, p0)
                if inn:
                    nxt.append(inn)
                if outp:
                    outside.append(outp)
            current = nxt
            if not current and want == "inside":
                break
        if want == "inside":
            for poly in current:
                if len(poly) >= 3:
                    out.append((poly, mat))
        else:
            for poly in outside:
                if len(poly) >= 3:
                    out.append((poly, mat))
    return out


def _to_mesh(frags: list[tuple[list[Vec3], str]], flip: bool) -> Mesh:
    from .ops import merge_by_distance

    mesh = Mesh()
    for poly, mat in frags:
        pts = list(reversed(poly)) if flip else poly
        if len(pts) < 3:
            continue
        mesh.add_face([mesh.add_vert(p) for p in pts], mat)
    merge_by_distance(mesh, 0.05)
    return mesh


def boolean(a: Mesh, b: Mesh, op: str = "difference") -> Mesh:
    op = (op or "difference").lower()
    if op not in {"union", "difference", "intersect"}:
        raise ValueError("boolean op must be union, difference, or intersect")
    if not _convex(a) or not _convex(b):
        raise ValueError("boolean needs two convex meshes (box, sphere, cylinder, cone, capsule, wedge)")
    pa, pb = _planes(a), _planes(b)
    if op == "intersect":
        parts = _fragments(a, pb, "inside") + _fragments(b, pa, "inside")
        flip_second = False
        mesh = _to_mesh(parts, False)
    elif op == "union":
        mesh = _to_mesh(_fragments(a, pb, "outside") + _fragments(b, pa, "outside"), False)
    else:
        # The cap is B's surface strictly inside A. Faces of B that merely lie on
        # A's boundary are coplanar duplicates, not the cut.
        cap_frags = [frag for frag in _fragments(b, pa, "inside") if _strictly_inside(frag[0], pa)]
        mesh = _to_mesh(_fragments(a, pb, "outside"), False)
        mesh.append(_to_mesh(cap_frags, True))
        from .ops import merge_by_distance

        merge_by_distance(mesh, 0.05)
    return mesh


def _strictly_inside(poly: list[Vec3], planes: list[tuple[Vec3, Vec3]], eps: float = 0.05) -> bool:
    if not poly:
        return False
    n = float(len(poly))
    c = [sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n, sum(p[2] for p in poly) / n]
    return all(_side(c, normal, p0) < -eps for normal, p0 in planes)
