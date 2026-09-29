"""Mesh edits shared by the panel and the agent. Selection is index lists."""

from __future__ import annotations

import math
from typing import Sequence

from .mesh import Mesh, Vec3, attr_mix, flip_side, lerp, vadd, vcross, vdot, vlen, vmul, vnorm, vsub


def _faces_or_all(mesh: Mesh, face_ids: Sequence[int] | None) -> list[int]:
    if not face_ids:
        return list(range(len(mesh.faces)))
    return [int(i) for i in face_ids if 0 <= int(i) < len(mesh.faces)]


def move_elements(mesh: Mesh, kind: str, ids: Sequence, delta: Sequence[float]) -> int:
    kind = (kind or "vert").lower()
    touched: set[int] = set()
    if kind == "vert":
        touched = {int(i) for i in ids if 0 <= int(i) < len(mesh.verts)}
    elif kind == "face":
        for fi in _faces_or_all(mesh, ids):
            touched.update(mesh.faces[fi])
    elif kind == "edge":
        for edge in ids:
            if len(edge) >= 2:
                a, b = int(edge[0]), int(edge[1])
                if 0 <= a < len(mesh.verts):
                    touched.add(a)
                if 0 <= b < len(mesh.verts):
                    touched.add(b)
    for i in touched:
        mesh.verts[i] = vadd(mesh.verts[i], delta)
    return len(touched)


def extrude_faces(mesh: Mesh, face_ids: Sequence[int] | None, distance: float) -> list[int]:
    selected = _faces_or_all(mesh, face_ids)
    if not selected or abs(distance) < 1e-8:
        return []
    old_faces = {fi: mesh.faces[fi][:] for fi in selected}
    counts: dict[tuple[int, int], int] = {}
    for fi in selected:
        face = old_faces[fi]
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    vert_n: dict[int, Vec3] = {}
    for fi in selected:
        n = mesh.face_normal(fi)
        for vi in old_faces[fi]:
            vert_n[vi] = vadd(vert_n.get(vi, [0.0, 0.0, 0.0]), n)
    dup: dict[int, int] = {}
    for vi, nsum in vert_n.items():
        dup[vi] = mesh.add_vert(vadd(mesh.verts[vi], vmul(vnorm(nsum), float(distance))), vi)
    new_faces: list[int] = []
    for fi in selected:
        face = old_faces[fi]
        mesh.faces[fi] = [dup[i] for i in face]
        new_faces.append(fi)
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            if counts.get(key, 0) != 1:
                continue
            idx = mesh.add_face([a, dup[a], dup[b], b], mesh.face_mat[fi])
            if idx >= 0:
                new_faces.append(idx)
    return new_faces


def inset_faces(mesh: Mesh, face_ids: Sequence[int] | None, amount: float) -> list[int]:
    """Radial inset (toward the face centroid). ponytail: not a constant-width edge offset."""
    selected = _faces_or_all(mesh, face_ids)
    amount = abs(float(amount))
    if not selected or amount < 1e-8:
        return []
    made: list[int] = []
    snapshot = [(fi, mesh.faces[fi][:], mesh.face_mat[fi]) for fi in selected]
    for fi, face, mat in snapshot:
        c = mesh.face_center(fi)
        inner: list[int] = []
        for vi in face:
            d = vsub(mesh.verts[vi], c)
            ln = vlen(d)
            inner.append(mesh.add_vert(c if ln <= amount else vadd(c, vmul(d, (ln - amount) / ln)), vi))
        mesh.faces[fi] = inner
        from .mesh import _uv_for_face

        mesh.uvs[fi] = _uv_for_face(mesh.verts, inner)
        for i, a in enumerate(face):
            j = (i + 1) % len(face)
            idx = mesh.add_face([a, face[j], inner[j], inner[i]], mat)
            if idx >= 0:
                made.append(idx)
        made.append(fi)
    return made


def bevel_edges(mesh: Mesh, edges: Sequence[Sequence[int]], width: float, segments: int = 1) -> int:
    """Chamfer selected edges. Extra segments repeat a thinner chamfer on the new strip."""
    width = abs(float(width))
    segments = max(1, int(segments))
    edge_set = set()
    for e in edges:
        if len(e) >= 2:
            a, b = int(e[0]), int(e[1])
            edge_set.add((a, b) if a < b else (b, a))
    if not edge_set or width < 1e-8:
        return 0
    step = width / segments
    total = 0
    for _ in range(segments):
        total += _chamfer_once(mesh, edge_set, step)
        if segments > 1:
            edge_set = _short_edges(mesh, step * 2.5)
    return total


def _short_edges(mesh: Mesh, limit: float) -> set[tuple[int, int]]:
    out = set()
    for a, b in mesh.edge_keys():
        if vlen(vsub(mesh.verts[a], mesh.verts[b])) <= limit:
            out.add((a, b))
    return out


def _chamfer_once(mesh: Mesh, edge_set: set[tuple[int, int]], width: float) -> int:
    """Cut selected corners and weld the new points so the mesh stays closed.

    Each (vertex, neighbor) cut is one shared point. Face loops use the chord
    across the cut, so corners stay filled instead of opening a hole.
    """
    from .mesh import _uv_for_face

    def selected(a: int, b: int) -> bool:
        return ((a, b) if a < b else (b, a)) in edge_set

    weld: dict[tuple[int, int], int] = {}

    def cut(origin: int, toward: int) -> int:
        key = (origin, toward)
        hit = weld.get(key)
        if hit is None:
            point, t = _offset_along(mesh, origin, toward, width)
            hit = mesh.add_vert(point, [(origin, 1.0 - t), (toward, t)])
            weld[key] = hit
        return hit

    new_loops: list[list[int]] = []
    corners: dict[int, list[int]] = {}
    for face in mesh.faces:
        loop: list[int] = []
        n = len(face)
        for i, vi in enumerate(face):
            prev = face[(i - 1) % n]
            nxt = face[(i + 1) % n]
            sel_in = selected(prev, vi)
            sel_out = selected(vi, nxt)
            if sel_in:
                vid = cut(vi, prev)
                loop.append(vid)
                corners.setdefault(vi, []).append(vid)
            if sel_out:
                vid = cut(vi, nxt)
                loop.append(vid)
                corners.setdefault(vi, []).append(vid)
            if not sel_in and not sel_out:
                loop.append(vi)
        compact: list[int] = []
        for vid in loop:
            if not compact or compact[-1] != vid:
                compact.append(vid)
        if len(compact) >= 2 and compact[0] == compact[-1]:
            compact = compact[:-1]
        new_loops.append(compact)
    changed = 0
    for fi, loop in enumerate(new_loops):
        if len(loop) >= 3:
            mesh.faces[fi] = loop
            mesh.uvs[fi] = _uv_for_face(mesh.verts, loop)
            changed += 1
    mat = mesh.face_mat[0] if mesh.face_mat else "default"
    for origin, vids in corners.items():
        changed += _corner_face(mesh, origin, vids, mat)
    mesh.drop_unused()
    return changed


def _corner_face(mesh: Mesh, origin: int, vids: list[int], mat: str) -> int:
    """Fill the hole where three or more cut edges meet."""
    uniq: list[int] = []
    for vid in vids:
        if vid not in uniq:
            uniq.append(vid)
    if len(uniq) < 3 or origin >= len(mesh.verts):
        return 0
    center = _avg([mesh.verts[vid] for vid in uniq])
    outward = vsub(mesh.verts[origin], center)
    if vlen(outward) < 1e-8:
        return 0
    axis = vnorm(outward)
    tmp = (1.0, 0.0, 0.0) if abs(axis[2]) > 0.9 else (0.0, 0.0, 1.0)
    tang = vnorm(vcross(axis, tmp))
    bit = vcross(axis, tang)
    ordered = sorted(uniq, key=lambda vid: math.atan2(
        vdot(vsub(mesh.verts[vid], center), bit),
        vdot(vsub(mesh.verts[vid], center), tang),
    ))
    return 1 if mesh.add_face(ordered, mat) >= 0 else 0


def _offset_along(mesh: Mesh, origin: int, toward: int, width: float) -> tuple[Vec3, float]:
    a = mesh.verts[origin]
    b = mesh.verts[toward]
    d = vsub(b, a)
    ln = vlen(d)
    if ln < 1e-8:
        return a[:], 0.0
    t = min(0.45, width / ln)
    return lerp(a, b, t), t


def subdivide(mesh: Mesh, face_ids: Sequence[int] | None = None) -> int:
    selected = set(_faces_or_all(mesh, face_ids))
    mid: dict[tuple[int, int], int] = {}

    def midpoint(a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        if key not in mid:
            mid[key] = mesh.add_vert(lerp(mesh.verts[a], mesh.verts[b], 0.5), [(a, 0.5), (b, 0.5)])
        return mid[key]

    old = [(fi, mesh.faces[fi][:], mesh.face_mat[fi]) for fi in range(len(mesh.faces))]
    mesh.faces = []
    mesh.face_mat = []
    mesh.uvs = []
    for fi, face, mat in old:
        if fi not in selected or len(face) < 3:
            mesh.add_face(face, mat)
            continue
        mids = [midpoint(face[i], face[(i + 1) % len(face)]) for i in range(len(face))]
        center = mesh.add_vert(_avg([mesh.verts[i] for i in face]), [(i, 1.0 / len(face)) for i in face])
        for i, vi in enumerate(face):
            mesh.add_face([vi, mids[i], center, mids[i - 1]], mat)
    return len(mid)


def _avg(pts: Sequence[Sequence[float]]) -> Vec3:
    n = float(len(pts)) or 1.0
    return [sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n]


def loop_cut(mesh: Mesh, edge: Sequence[int] | None = None, t: float = 0.5) -> int:
    from .mesh import edge_loops

    loops = edge_loops(mesh)
    if not loops:
        return 0
    chosen = loops[0]
    if edge and len(edge) >= 2:
        key = _k(int(edge[0]), int(edge[1]))
        for loop in loops:
            if key in loop:
                chosen = loop
                break
    t = min(0.95, max(0.05, float(t)))
    split: dict[tuple[int, int], int] = {}
    for a, b in chosen:
        f = t if a < b else 1.0 - t
        split[(a, b)] = mesh.add_vert(lerp(mesh.verts[a], mesh.verts[b], f), [(a, 1.0 - f), (b, f)])
    faces = mesh.faces[:]
    mats = mesh.face_mat[:]
    mesh.faces = []
    mesh.face_mat = []
    mesh.uvs = []
    cuts = 0
    for face, mat in zip(faces, mats):
        if len(face) != 4:
            mesh.add_face(face, mat)
            continue
        a, b, c, d = face
        ab, bc, cd, da = _k(a, b) in split, _k(b, c) in split, _k(c, d) in split, _k(d, a) in split
        if ab and cd:
            mesh.add_face([a, split[_k(a, b)], split[_k(c, d)], d], mat)
            mesh.add_face([split[_k(a, b)], b, c, split[_k(c, d)]], mat)
            cuts += 1
        elif bc and da:
            mesh.add_face([a, b, split[_k(b, c)], split[_k(d, a)]], mat)
            mesh.add_face([split[_k(d, a)], split[_k(b, c)], c, d], mat)
            cuts += 1
        else:
            mesh.add_face(face, mat)
    return cuts


def _k(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def merge_by_distance(mesh: Mesh, dist: float = 0.05) -> int:
    dist = abs(float(dist))
    if dist <= 0 or not mesh.verts:
        return 0
    cell = dist
    buckets: dict[tuple[int, int, int], int] = {}
    remap = list(range(len(mesh.verts)))
    kept: list[Vec3] = []
    source: list[int] = []  # the first vertex of each cluster keeps its attributes
    merged = 0
    for i, v in enumerate(mesh.verts):
        key = (int(v[0] // cell), int(v[1] // cell), int(v[2] // cell))
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    j = buckets.get((key[0] + dx, key[1] + dy, key[2] + dz))
                    if j is not None and vlen(vsub(v, kept[j])) <= dist:
                        found = j
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            buckets[key] = len(kept)
            remap[i] = len(kept)
            kept.append(v[:])
            source.append(i)
        else:
            remap[i] = found
            merged += 1
    mesh.keep(source)
    from .mesh import _uv_for_face

    faces, mats, uvs = [], [], []
    for fi, face in enumerate(mesh.faces):
        nf, nuv = [], []
        for k, vi in enumerate(face):
            ri = remap[vi]
            if nf and nf[-1] == ri:
                continue
            nf.append(ri)
            if fi < len(mesh.uvs) and k < len(mesh.uvs[fi]):
                nuv.append(mesh.uvs[fi][k])
        if len(nf) >= 2 and nf[0] == nf[-1]:
            nf, nuv = nf[:-1], nuv[:-1]
        if len(set(nf)) < 3:
            continue
        faces.append(nf)
        mats.append(mesh.face_mat[fi] if fi < len(mesh.face_mat) else "default")
        uvs.append(nuv if len(nuv) == len(nf) else _uv_for_face(mesh.verts, nf))
    mesh.faces, mesh.face_mat, mesh.uvs = faces, mats, uvs
    return merged


def merge_center(mesh: Mesh, vert_ids: Sequence[int]) -> int:
    ids = [int(i) for i in vert_ids if 0 <= int(i) < len(mesh.verts)]
    if len(ids) < 2:
        return 0
    c = _avg([mesh.verts[i] for i in ids])
    keep = ids[0]
    mesh.verts[keep] = c
    for name, vals in mesh.attrs.items():
        vals[keep] = attr_mix(name, vals, [(i, 1.0 / len(ids)) for i in ids])
    drop = set(ids[1:])
    remap: dict[int, int] = {}
    order: list[int] = []
    for i in range(len(mesh.verts)):
        if i in drop:
            continue
        remap[i] = len(order)
        order.append(i)
    keep_new = remap[keep]
    for i in drop:
        remap[i] = keep_new
    mesh.keep(order)
    _remap_faces(mesh, remap)
    return len(drop)


def _remap_faces(mesh: Mesh, remap: dict[int, int]) -> None:
    from .mesh import _uv_for_face

    faces, mats, uvs = [], [], []
    for fi, face in enumerate(mesh.faces):
        src = mesh.uvs[fi] if fi < len(mesh.uvs) and len(mesh.uvs[fi]) == len(face) else None
        nf, nuv = [], []
        for k, vi in enumerate(face):
            ri = remap.get(vi, vi)
            if not nf or nf[-1] != ri:
                nf.append(ri)
                nuv.append(src[k] if src else None)
        if len(nf) >= 2 and nf[0] == nf[-1]:
            nf, nuv = nf[:-1], nuv[:-1]
        if len(set(nf)) < 3:
            continue
        faces.append(nf)
        mats.append(mesh.face_mat[fi])
        uvs.append(nuv if src else _uv_for_face(mesh.verts, nf))  # keep authored UVs through a merge
    mesh.faces, mesh.face_mat, mesh.uvs = faces, mats, uvs


def delete_faces(mesh: Mesh, face_ids: Sequence[int]) -> int:
    drop = {int(i) for i in face_ids}
    keep = [i for i in range(len(mesh.faces)) if i not in drop]
    mesh.faces = [mesh.faces[i] for i in keep]
    mesh.face_mat = [mesh.face_mat[i] for i in keep]
    mesh.uvs = [mesh.uvs[i] for i in keep]
    before = len(mesh.verts)
    mesh.drop_unused()
    return len(drop) if before else len(drop)


def delete_verts(mesh: Mesh, vert_ids: Sequence[int]) -> int:
    drop = {int(i) for i in vert_ids}
    faces = [fi for fi, face in enumerate(mesh.faces) if any(v in drop for v in face)]
    delete_faces(mesh, faces)
    return len(drop)


def dissolve_edges(mesh: Mesh, edges: Sequence[Sequence[int]]) -> int:
    removed = 0
    pending = [e for e in edges]
    for edge in pending:
        if len(edge) < 2:
            continue
        a, b = int(edge[0]), int(edge[1])
        owners = [fi for fi, face in enumerate(mesh.faces) if a in face and b in face]
        if len(owners) != 2:
            continue
        f0, f1 = owners
        merged = _splice(mesh.faces[f0], mesh.faces[f1], a, b)
        if not merged:
            continue
        from .mesh import _uv_for_face

        mesh.faces[f0] = merged
        mesh.uvs[f0] = _uv_for_face(mesh.verts, merged)
        hi = max(f0, f1)
        del mesh.faces[hi]
        del mesh.face_mat[hi]
        del mesh.uvs[hi]
        removed += 1
    return removed


def _splice(face_a: list[int], face_b: list[int], a: int, b: int) -> list[int] | None:
    if a not in face_a or b not in face_a or a not in face_b or b not in face_b:
        return None
    ib = face_b.index(b)
    rb = face_b[ib:] + face_b[:ib]
    if len(rb) < 3:
        return None
    if a not in rb[1:]:
        return None
    # chain is the verts of face_b between b and a, excluding both
    ia = rb.index(a)
    chain = rb[1:ia]
    out: list[int] = []
    n = len(face_a)
    for i, v in enumerate(face_a):
        out.append(v)
        nxt = face_a[(i + 1) % n]
        if {v, nxt} == {a, b}:
            step = chain if v == b else list(reversed(chain))
            out.extend(step)
    clean: list[int] = []
    for v in out:
        if not clean or clean[-1] != v:
            clean.append(v)
    if len(clean) >= 2 and clean[0] == clean[-1]:
        clean = clean[:-1]
    return clean if len(set(clean)) >= 3 else None


def fill_boundary(mesh: Mesh, mat: str = "default") -> int:
    counts: dict[tuple[int, int], int] = {}
    directed: dict[int, list[int]] = {}
    for face in mesh.faces:
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    boundary: list[tuple[int, int]] = []
    for face in mesh.faces:
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            if counts.get(key) == 1:
                boundary.append((a, b))
                directed.setdefault(a, []).append(b)
    if not boundary:
        return 0
    used: set[int] = set()
    made = 0
    for start, _ in boundary:
        if start in used:
            continue
        loop = [start]
        cur = start
        for _guard in range(len(boundary) + 2):
            nxts = [n for n in directed.get(cur, []) if n not in loop]
            if not nxts:
                break
            nxt = nxts[0]
            if nxt == start:
                break
            loop.append(nxt)
            cur = nxt
        used.update(loop)
        if len(loop) >= 3:
            mesh.add_face(list(reversed(loop)), mat)
            made += 1
    return made


def bridge_loops(mesh: Mesh, loop_a: Sequence[int], loop_b: Sequence[int], mat: str = "default") -> int:
    a = [int(i) for i in loop_a]
    b = [int(i) for i in loop_b]
    if len(a) < 2 or len(a) != len(b):
        raise ValueError("bridge needs two loops with the same vertex count")
    for i in range(len(a)):
        j = (i + 1) % len(a)
        mesh.add_face([a[i], a[j], b[j], b[i]], mat)
    return len(a)


def flip_faces(mesh: Mesh, face_ids: Sequence[int] | None = None) -> int:
    ids = _faces_or_all(mesh, face_ids)
    for fi in ids:
        mesh.faces[fi].reverse()
        if fi < len(mesh.uvs):
            mesh.uvs[fi].reverse()
    return len(ids)


def recalculate_normals(mesh: Mesh) -> None:
    mesh.orient_convex()


def set_smooth(mesh: Mesh, smooth: bool) -> None:
    mesh.smooth = bool(smooth)


def separate_by_material(mesh: Mesh) -> list[Mesh]:
    groups: dict[str, Mesh] = {}
    sources: dict[str, list[int]] = {}
    for fi, face in enumerate(mesh.faces):
        mat = mesh.face_mat[fi]
        dst = groups.setdefault(mat, Mesh())
        src = sources.setdefault(mat, [])
        remap: dict[int, int] = {}
        nf = []
        for vi in face:
            if vi not in remap:
                remap[vi] = dst.add_vert(mesh.verts[vi])
                src.append(vi)
            nf.append(remap[vi])
        dst.add_face(nf, mat, mesh.uvs[fi] if fi < len(mesh.uvs) else None)
        dst.smooth = mesh.smooth
    for mat, dst in groups.items():
        dst.attrs = {name: [vals[i][:] if isinstance(vals[i], list) else vals[i] for i in sources[mat]] for name, vals in mesh.attrs.items()}
    return list(groups.values())


def set_origin(mesh: Mesh, mode: str = "base") -> Vec3:
    """Shift verts so the origin is at the base or the center. Returns that shift."""
    lo, hi = mesh.bounds()
    if mode == "center":
        origin = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    else:
        origin = [(lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2]]
    mesh.translate([-origin[0], -origin[1], -origin[2]])
    return origin


def mirror_mesh(mesh: Mesh, axis: str = "x", weld: float = 0.05) -> Mesh:
    ai = {"x": 0, "y": 1, "z": 2}.get(axis.lower(), 0)
    out = mesh.copy()
    dup = mesh.copy()
    for v in dup.verts:
        v[ai] = -v[ai]
    for face in dup.faces:
        face.reverse()
    for uvs in dup.uvs:
        uvs.reverse()
    for name, vals in dup.attrs.items():
        if name.startswith("sk:"):
            for d in vals:
                d[ai] = -d[ai]
    # The mirrored half is the other side of the body: its weights belong to the _r bones and back.
    dup.attrs = {flip_side(name): vals for name, vals in dup.attrs.items()}
    out.append(dup)
    merge_by_distance(out, weld)
    return out


def array_mesh(mesh: Mesh, count: int = 3, offset: Sequence[float] = (110.0, 0.0, 0.0)) -> Mesh:
    count = max(1, int(count))
    out = Mesh()
    off = [float(offset[0]) if len(offset) > 0 else 0.0, float(offset[1]) if len(offset) > 1 else 0.0, float(offset[2]) if len(offset) > 2 else 0.0]
    for i in range(count):
        part = mesh.copy()
        part.translate(vmul(off, float(i)))
        out.append(part)
    out.smooth = mesh.smooth
    return out


def apply_modifiers(mesh: Mesh, modifiers: Sequence[dict]) -> Mesh:
    out = mesh.copy()
    for mod in modifiers or []:
        kind = str(mod.get("type") or "")
        if kind == "mirror":
            out = mirror_mesh(out, str(mod.get("axis") or "x"), float(mod.get("weld", 0.05)))
        elif kind == "array":
            out = array_mesh(out, int(mod.get("count") or 2), mod.get("offset") or [100, 0, 0])
    return out


def triangulate_ngons(mesh: Mesh, only_nonplanar: bool = True, tol: float = 0.5, concave: bool = False) -> int:
    """Split n-gons into triangles. only_nonplanar keeps flat faces; concave=True still splits flat concave ones."""
    from .mesh import _newell, _uv_for_face, face_triangles, is_convex

    made = 0
    faces, mats, uvs = [], [], []
    for fi, face in enumerate(mesh.faces):
        keep = len(face) <= 3 or (only_nonplanar and _planar(mesh, face, tol, _newell))
        if keep and not (concave and len(face) > 3 and not is_convex(mesh.verts, face)):
            faces.append(face)
            mats.append(mesh.face_mat[fi])
            uvs.append(mesh.uvs[fi] if fi < len(mesh.uvs) else [])
            continue
        src = mesh.uvs[fi] if fi < len(mesh.uvs) else []
        for tri in face_triangles(mesh.verts, face):
            faces.append(list(tri))
            mats.append(mesh.face_mat[fi])
            if len(src) == len(face):
                uvs.append([src[face.index(v)] for v in tri])
            else:
                uvs.append(_uv_for_face(mesh.verts, list(tri)))
            made += 1
    mesh.faces, mesh.face_mat, mesh.uvs = faces, mats, uvs
    return made


def _planar(mesh: Mesh, face: Sequence[int], tol: float, newell) -> bool:
    if len(face) <= 3:
        return True
    normal = newell(mesh.verts, face)
    p0 = mesh.verts[face[0]]
    return all(abs(vdot(normal, vsub(mesh.verts[vi], p0))) <= tol for vi in face[1:])
