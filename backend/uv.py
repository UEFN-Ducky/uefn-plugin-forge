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
