"""Flat preview renders for the agent. No panel required.

ponytail: painter's algorithm (depth sort, no z-buffer). Intersecting triangles
can draw in the wrong order. Upgrade path is a z-buffer if a prop reads wrong.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .kernel.mesh import Mesh, vdot, vnorm, vsub

_VIEWS = {
    "three": (1.3, -1.6, 0.9),
    "front": (0.0, -2.2, 0.45),
    "top": (0.0, -0.01, 2.4),
    "side": (2.2, 0.0, 0.45),
}


def render_mesh(mesh: Mesh, view: str = "three", size: tuple[int, int] = (320, 240)) -> Image.Image:
    w, h = size
    im = Image.new("RGB", (w, h), (22, 22, 26))
    if not mesh.verts:
        return im
    lo, hi = mesh.bounds()
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    radius = max(hi[i] - lo[i] for i in range(3)) or 1.0
    eye_k = _VIEWS.get(view, _VIEWS["three"])
    eye = [center[i] + eye_k[i] * radius for i in range(3)]
    forward = vnorm(vsub(center, eye))
    world_up = [0.0, 0.0, 1.0]
    if abs(vdot(forward, world_up)) > 0.95:
        world_up = [0.0, 1.0, 0.0]
    right = vnorm(_cross(forward, world_up))
    up = vnorm(_cross(right, forward))
    light = vnorm(vadd_n(forward, up))
    tris = []
    for a, b, c, fi in mesh.triangles():
        n = mesh.face_normal(fi)
        shade = max(0.18, min(1.0, vdot(n, light)))
        mat = mesh.face_mat[fi] if fi < len(mesh.face_mat) else "default"
        pts = []
        depth = 0.0
        ok = True
        for vi in (a, b, c):
            rel = vsub(mesh.verts[vi], eye)
            z = vdot(rel, forward)
            if z < 1.0:
                ok = False
                break
            x = vdot(rel, right) / z
            y = vdot(rel, up) / z
            pts.append((w / 2 + x * w * 0.9, h / 2 - y * h * 0.9))
            depth += z
        if ok:
            tris.append((depth, pts, shade, mat))
    tris.sort(key=lambda t: -t[0])
    draw = ImageDraw.Draw(im)
    palette = {}
    for _depth, pts, shade, mat in tris:
        base = palette.setdefault(mat, _color(mat))
        col = tuple(int(max(0, min(255, c * shade))) for c in base)
        draw.polygon(pts, fill=col)
    return im


def render_png(mesh: Mesh, path: Path, views: list[str] | None = None) -> list[str]:
    views = views or ["three"]
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for i, view in enumerate(views):
        dest = path if len(views) == 1 else path.with_name(f"{path.stem}_{view}{path.suffix}")
        render_mesh(mesh, view).save(dest)
        out.append(str(dest))
    return out


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def vadd_n(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _color(mat: str) -> tuple[int, int, int]:
    h = abs(hash(mat))
    return (80 + (h % 140), 90 + ((h // 7) % 120), 110 + ((h // 13) % 100))
