"""Mesh checks and the safe auto-fixes. Both the panel and forge_check use this."""

from __future__ import annotations

from typing import Any

from .kernel.mesh import Mesh, vlen, vsub
from .kernel.ops import merge_by_distance, recalculate_normals, set_origin, triangulate_ngons
from .uv import missing_uvs, project

TRI_BUDGET = 20000


def _issue(kind: str, message: str, obj: str, *, severity: str = "warn", elements: dict | None = None, fix: str | None = None) -> dict[str, Any]:
    return {
        "id": kind,
        "severity": severity,
        "message": message,
        "object": obj,
        "elements": elements or {},
        "fix": fix,
    }


def check_mesh(mesh: Mesh, obj_id: str, *, has_collision: bool, pivot_is_base: bool) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    edge_count: dict[tuple[int, int], int] = {}
    for face in mesh.faces:
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            edge_count[key] = edge_count.get(key, 0) + 1
    boundary = [e for e, n in edge_count.items() if n == 1]
    nonmanifold = [e for e, n in edge_count.items() if n > 2]
    if boundary:
        issues.append(_issue("boundary", f"{len(boundary)} open edges", obj_id, elements={"edges": boundary[:40]}))
    if nonmanifold:
        issues.append(_issue("nonmanifold", f"{len(nonmanifold)} non-manifold edges", obj_id, severity="error", elements={"edges": nonmanifold[:40]}))
    degenerate = []
    for fi, face in enumerate(mesh.faces):
        if len(set(face)) < 3:
            degenerate.append(fi)
            continue
        # zero area
        if vlen(mesh.face_normal(fi)) == 0 and len(face) >= 3:
            degenerate.append(fi)
    # face_normal returns a normalized vector or a default [0,0,1] when length is ~0.
    # Detect zero area from the raw edge cross of the first corner.
    degenerate = []
    for fi, face in enumerate(mesh.faces):
        if len(set(face)) < 3:
            degenerate.append(fi)
            continue
        a, b, c = (mesh.verts[face[0]], mesh.verts[face[1]], mesh.verts[face[2]])
        cross = vlen([
            (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
            (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
            (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
        ])
        if cross < 1e-4:
            degenerate.append(fi)
    if degenerate:
        issues.append(_issue("degenerate", f"{len(degenerate)} zero-area faces", obj_id, severity="error", elements={"faces": degenerate[:40]}, fix="degenerate"))
    dup = 0
    for i, v in enumerate(mesh.verts):
        for w in mesh.verts[i + 1:i + 8]:
            if vlen(vsub(v, w)) < 0.05:
                dup += 1
                break
    if dup:
        issues.append(_issue("duplicates", f"{dup} vertices within 0.05 cm of another", obj_id, fix="duplicates"))
    used = {i for face in mesh.faces for i in face}
    loose = len(mesh.verts) - len(used)
    if loose:
        issues.append(_issue("loose", f"{loose} unused vertices", obj_id, fix="loose"))
    ngons = [fi for fi, face in enumerate(mesh.faces) if len(face) > 4]
    if ngons:
        issues.append(_issue("ngon", f"{len(ngons)} n-gons", obj_id, elements={"faces": ngons[:40]}, fix="ngon"))
    missing = missing_uvs(mesh)
    if missing:
        issues.append(_issue("uv", f"{len(missing)} faces missing UVs", obj_id, elements={"faces": missing[:40]}, fix="uv"))
    tris = mesh.tri_count()
    if tris > TRI_BUDGET:
        issues.append(_issue("budget", f"{tris} triangles (budget {TRI_BUDGET})", obj_id, severity="error"))
    if not pivot_is_base:
        issues.append(_issue("pivot", "Pivot is not on the base", obj_id, fix="pivot"))
    lo, hi = mesh.bounds()
    height = hi[2] - lo[2]
    if height > 2000 or max(hi[0] - lo[0], hi[1] - lo[1]) > 5000:
        issues.append(_issue("scale", f"Bounds {round(hi[0]-lo[0])} x {round(hi[1]-lo[1])} x {round(height)} cm look far off a Fortnite prop", obj_id))
    if not has_collision:
        issues.append(_issue("collision", "No collision layer objects and simple collision not chosen yet", obj_id))
    return issues


def fix_mesh(mesh: Mesh, kinds: set[str]) -> dict[str, int]:
    done: dict[str, int] = {}
    if "duplicates" in kinds or "all" in kinds:
        done["duplicates"] = merge_by_distance(mesh, 0.05)
    if "degenerate" in kinds or "all" in kinds:
        drop = []
        for fi, face in enumerate(mesh.faces):
            if len(set(face)) < 3:
                drop.append(fi)
                continue
            a, b, c = (mesh.verts[face[0]], mesh.verts[face[1]], mesh.verts[face[2]])
            cross = vlen([
                (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
            ])
            if cross < 1e-4:
                drop.append(fi)
        if drop:
            from .kernel.ops import delete_faces

            delete_faces(mesh, drop)
        done["degenerate"] = len(drop)
    if "normals" in kinds or "all" in kinds:
        recalculate_normals(mesh)
        done["normals"] = len(mesh.faces)
    if "ngon" in kinds or "all" in kinds:
        done["ngon"] = triangulate_ngons(mesh, only_nonplanar=False)
    if "uv" in kinds or "all" in kinds:
        done["uv"] = project(mesh, "box")
    if "pivot" in kinds or "all" in kinds:
        set_origin(mesh, "base")
        done["pivot"] = 1
    if "loose" in kinds or "all" in kinds:
        before = len(mesh.verts)
        mesh.drop_unused()
        done["loose"] = before - len(mesh.verts)
    return done
