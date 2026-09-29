"""UV projections. ponytail: box / planar / cylindrical only — not a real unwrap."""

from __future__ import annotations

import math

from .kernel.mesh import Mesh, vnorm


def project(mesh: Mesh, mode: str = "box", face_ids: list[int] | None = None, density: float = 0.01) -> int:
    """density is UV units per centimetre. 0.01 -> 100 cm fills 0..1."""
    mode = (mode or "box").lower()
    ids = list(range(len(mesh.faces))) if not face_ids else [i for i in face_ids if 0 <= i < len(mesh.faces)]
    density = float(density) or 0.01
    for fi in ids:
        face = mesh.faces[fi]
        if mode == "planar":
            n = mesh.face_normal(fi)
            axis = max(range(3), key=lambda i: abs(n[i]))
        elif mode == "cylinder":
            axis = 2
        else:
            n = mesh.face_normal(fi)
            axis = max(range(3), key=lambda i: abs(n[i]))
        uvs = []
        for vi in face:
            p = mesh.verts[vi]
            if mode == "cylinder":
                u = (math.atan2(p[1], p[0]) / math.tau) + 0.5
                v = p[2] * density
            elif axis == 2:
                u, v = p[0] * density, p[1] * density
            elif axis == 1:
                u, v = p[0] * density, p[2] * density
            else:
                u, v = p[1] * density, p[2] * density
            uvs.append([u, v])
        mesh.uvs[fi] = uvs
    return len(ids)


def smart_unwrap(mesh: Mesh, face_ids: list[int] | None = None, angle: float = 66.0, margin: float = 0.01) -> int:
    """Charts by facing direction, flattened, rotated tight, packed into 0..1 at one texel density.

    Same idea as Blender's Smart UV Project. Returns the number of charts.
    ponytail: planar charts, not conformal (LSCM); curved organic charts stretch a
    little. Upgrade path is LSCM per chart.
    """
    ids = list(range(len(mesh.faces))) if not face_ids else [i for i in face_ids if 0 <= i < len(mesh.faces)]
    if not ids:
        return 0
    normals = {fi: mesh.face_normal(fi) for fi in ids}
    area = {fi: _face_area(mesh, fi) for fi in ids}
    limit = math.cos(math.radians(max(1.0, min(89.0, float(angle)))))
    dirs: list[list[float]] = []
    for fi in sorted(ids, key=lambda f: -area[f]):
        n = normals[fi]
        if not any(n[0] * d[0] + n[1] * d[1] + n[2] * d[2] > limit for d in dirs):
            dirs.append(n)
    owner = {fi: max(range(len(dirs)), key=lambda k: sum(normals[fi][a] * dirs[k][a] for a in range(3))) for fi in ids}
    # Charts: faces that share an edge and a direction.
    parent = {fi: fi for fi in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for fi in ids:
        face = mesh.faces[fi]
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            edge_faces.setdefault((a, b) if a < b else (b, a), []).append(fi)
    for fs in edge_faces.values():
        for other in fs[1:]:
            if owner[other] == owner[fs[0]]:
                parent[find(other)] = find(fs[0])
    charts: dict[int, list[int]] = {}
    for fi in ids:
        charts.setdefault(find(fi), []).append(fi)
    flat = []
    for faces in charts.values():
        d = dirs[owner[faces[0]]]
        u_axis = vnorm(_cross(d, [0.0, 0.0, 1.0] if abs(d[2]) < 0.9 else [0.0, 1.0, 0.0]))
        v_axis = _cross(d, u_axis)
        pts = {fi: [(sum(mesh.verts[vi][a] * u_axis[a] for a in range(3)), sum(mesh.verts[vi][a] * v_axis[a] for a in range(3)))
                    for vi in mesh.faces[fi]] for fi in faces}
        allp = [p for ps in pts.values() for p in ps]
        best = None
        for step in range(18):  # rotate to the tightest box, 5-degree steps
            t = math.radians(step * 5)
            c, s = math.cos(t), math.sin(t)
            xs = [x * c - y * s for x, y in allp]
            ys = [x * s + y * c for x, y in allp]
            box = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if best is None or box < best[0]:
                best = (box, c, s, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        _, c, s, x0, y0, w, h = best
        placed = {fi: [(x * c - y * s - x0, x * s + y * c - y0) for x, y in ps] for fi, ps in pts.items()}
        if h > w:  # stand tall charts on their side so shelves pack flatter
            placed = {fi: [(y, w - x) for x, y in ps] for fi, ps in placed.items()}
            w, h = h, w
        flat.append({"faces": placed, "w": w, "h": h})
    # Shelf packing in centimetres, then one uniform scale into 0..1 keeps texel density even.
    total = sum((c["w"] + 1e-6) * (c["h"] + 1e-6) for c in flat)
    gap = math.sqrt(total) * max(0.0, float(margin)) * 2
    widest = max(c["w"] for c in flat)
    ordered = sorted(flat, key=lambda c: -c["h"])
    best = None
    for factor in (1.0, 1.15, 1.3, 1.5, 1.8, 2.2, 3.0):  # keep the shelf width that packs squarest
        width = max(widest, math.sqrt(total) * factor)
        x = y = row = 0.0
        spots = []
        for chart in ordered:
            if x > 0 and x + chart["w"] > width:
                x, y, row = 0.0, y + row + gap, 0.0
            spots.append((x, y))
            x += chart["w"] + gap
            row = max(row, chart["h"])
        extent = max(max(sx + c["w"] for (sx, _), c in zip(spots, ordered)), max(sy + c["h"] for (_, sy), c in zip(spots, ordered)))
        if best is None or extent < best[0]:
            best = (extent, spots)
    extent = best[0] or 1.0
    for chart, spot in zip(ordered, best[1]):
        chart["at"] = spot
    for chart in flat:
        ox, oy = chart["at"]
        for fi, ps in chart["faces"].items():
            mesh.uvs[fi] = [[(px + ox) / extent, (py + oy) / extent] for px, py in ps]
    return len(flat)


def transform_uvs(mesh: Mesh, face_ids: list[int] | None = None, offset=(0.0, 0.0), rotate: float = 0.0, scale=(1.0, 1.0)) -> int:
    """Move, turn, or scale UVs of faces about their UV centre."""
    ids = list(range(len(mesh.faces))) if not face_ids else [i for i in face_ids if 0 <= i < len(mesh.faces)]
    pts = [u for fi in ids for u in mesh.uvs[fi]]
    if not pts:
        return 0
    cx = sum(u[0] for u in pts) / len(pts)
    cy = sum(u[1] for u in pts) / len(pts)
    c, s = math.cos(math.radians(float(rotate))), math.sin(math.radians(float(rotate)))
    sx, sy = float(scale[0]), float(scale[1])
    for fi in ids:
        out = []
        for u, v in mesh.uvs[fi]:
            x, y = (u - cx) * sx, (v - cy) * sy
            out.append([cx + x * c - y * s + float(offset[0]), cy + x * s + y * c + float(offset[1])])
        mesh.uvs[fi] = out
    return len(ids)


def texel_density(mesh: Mesh, size: int = 1024) -> float:
    """Texture pixels per centimetre, averaged by area. 5-10 px/cm reads crisp at player distance."""
    world = uv = 0.0
    for fi, face in enumerate(mesh.faces):
        world += _face_area(mesh, fi)
        uvs = mesh.uvs[fi]
        uv += abs(sum(uvs[i][0] * uvs[(i + 1) % len(uvs)][1] - uvs[(i + 1) % len(uvs)][0] * uvs[i][1] for i in range(len(uvs)))) / 2
    return math.sqrt(uv / world) * size if world > 0 else 0.0


def _face_area(mesh: Mesh, fi: int) -> float:
    face = mesh.faces[fi]
    nx = ny = nz = 0.0
    for i, a in enumerate(face):
        p, q = mesh.verts[a], mesh.verts[face[(i + 1) % len(face)]]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    return math.sqrt(nx * nx + ny * ny + nz * nz) / 2


def _cross(a, b) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def missing_uvs(mesh: Mesh) -> list[int]:
    bad = []
    for fi, face in enumerate(mesh.faces):
        uvs = mesh.uvs[fi] if fi < len(mesh.uvs) else []
        if len(uvs) != len(face) or all(abs(u[0]) < 1e-8 and abs(u[1]) < 1e-8 for u in uvs):
            # all-zero still counts as present if the face genuinely sits at the origin
            # of its projection. Treat a face as missing only when the array is short.
            if len(uvs) != len(face):
                bad.append(fi)
    return bad
