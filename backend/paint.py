"""The AI's painting: procedural blend masks and texture painting through UVs.

Blend masks live in the col:blend vertex attribute: R, G, B each reveal one layer of
a blend material over its base. They ship to UEFN as vertex colours.
Texture painting writes the material's base colour PNG through the mesh's UVs; a dab
is shaded by 3D distance on every triangle it touches, so it crosses UV seams cleanly.
"""

from __future__ import annotations

import math
from typing import Sequence

from PIL import Image, ImageDraw

from .kernel.mesh import Mesh
from .kernel.sculpt import falloff, fbm, vertex_normals

MASKS = ("all", "up", "down", "facing", "height", "noise", "sphere", "faces", "cavity")
MODES = ("set", "add", "subtract", "max", "min", "multiply")


def mask_values(mesh: Mesh, mask: str, p: dict) -> list[float]:
    """0..1 per vertex for a procedural mask. Coordinates are the mesh's local centimetres."""
    mask = (mask or "all").lower()
    n = len(mesh.verts)
    if mask == "all":
        return [1.0] * n
    if mask in ("up", "down", "facing"):
        axis = {"up": [0.0, 0.0, 1.0], "down": [0.0, 0.0, -1.0]}.get(mask) or [float(c) for c in p.get("direction") or [0, 0, 1]]
        ln = math.sqrt(sum(c * c for c in axis)) or 1.0
        axis = [c / ln for c in axis]
        start = float(p.get("threshold", 0.5))  # dot product where coverage begins
        soft = max(1e-3, float(p.get("softness", 0.3)))
        out = []
        for nrm in vertex_normals(mesh):
            d = sum(nrm[a] * axis[a] for a in range(3))
            out.append(max(0.0, min(1.0, (d - start) / soft + 0.5)))
        return out
    if mask == "height":
        lo, hi = mesh.bounds()
        z0 = float(p["low"]) if p.get("low") is not None else lo[2]
        z1 = float(p["high"]) if p.get("high") is not None else lo[2] + (hi[2] - lo[2]) * 0.3
        span = (z1 - z0) or 1e-6
        return [max(0.0, min(1.0, 1.0 - (v[2] - z0) / span)) for v in mesh.verts]
    if mask == "noise":
        scale = float(p.get("scale", 40.0))
        seed = int(p.get("seed", 0))
        contrast = float(p.get("contrast", 2.0))
        return [max(0.0, min(1.0, 0.5 + fbm(v, scale, seed, 3) * contrast)) for v in mesh.verts]
    if mask == "sphere":
        centre = [float(c) for c in p.get("center") or p.get("point") or [0, 0, 0]]
        radius = float(p.get("radius", 50.0))
        return [falloff(math.dist(v, centre), radius) for v in mesh.verts]
    if mask == "faces":
        picked = {int(f) for f in p.get("faces") or []}
        out = [0.0] * n
        for fi in picked:
            if 0 <= fi < len(mesh.faces):
                for vi in mesh.faces[fi]:
                    out[vi] = 1.0
        return out
    if mask == "cavity":
        # Vertices sitting below the average of their neighbours are in creases: grime and rust collect there.
        from .kernel.sculpt import neighbors

        adj = neighbors(mesh)
        normals = vertex_normals(mesh)
        depth = []
        for i, v in enumerate(mesh.verts):
            if not adj[i]:
                depth.append(0.0)
                continue
            avg = [sum(mesh.verts[j][a] for j in adj[i]) / len(adj[i]) for a in range(3)]
            depth.append(sum((avg[a] - v[a]) * normals[i][a] for a in range(3)))
        scale = float(p.get("scale", 0)) or (max(abs(d) for d in depth) or 1.0)
        return [max(0.0, min(1.0, d / scale)) for d in depth]
    raise ValueError(f"mask is one of {', '.join(MASKS)}")


def apply_mask(mesh: Mesh, channel: int, values: Sequence[float], mode: str = "set", strength: float = 1.0) -> int:
    """Combine a 0..1 mask into one channel of col:blend."""
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}")
    col = mesh.ensure_attr("col:blend")
    k = max(0.0, min(1.0, float(strength)))
    for i, m in enumerate(values):
        m *= k
        old = col[i][channel]
        new = {"set": m, "add": old + m, "subtract": old - m, "max": max(old, m), "min": min(old, m), "multiply": old * m}[mode]
        col[i][channel] = max(0.0, min(1.0, new))
    return len(values)


# ---- texture painting ------------------------------------------------------


def _hex(color) -> tuple[int, int, int, int]:
    if isinstance(color, str):
        c = color.lstrip("#")
        if len(c) == 6:
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)
        raise ValueError("colour must be #rrggbb or [r, g, b] 0-1")
    vals = [float(v) for v in color]
    if len(vals) < 3:
        raise ValueError("colour needs three values")
    return tuple(int(max(0.0, min(1.0, v)) * 255 + 0.5) for v in (vals + [1.0])[:4])


def new_canvas(size: int, color) -> Image.Image:
    size = max(16, min(4096, int(size)))
    return Image.new("RGBA", (size, size), _hex(color))


def fill_faces(img: Image.Image, mesh: Mesh, faces: Sequence[int], color) -> int:
    """Flood the UV area of these faces with one colour: "paint these faces red"."""
    w, h = img.size
    draw = ImageDraw.Draw(img)
    rgba = _hex(color)
    count = 0
    for fi in faces:
        if 0 <= int(fi) < len(mesh.faces):
            uv = mesh.uvs[int(fi)]
            draw.polygon([(u * w, (1 - v) * h) for u, v in uv], fill=rgba)
            count += 1
    return count


def dabs(img: Image.Image, mesh: Mesh, points: Sequence[Sequence[float]], radius: float, color, strength: float = 1.0,
         spacing: float = 0.3) -> int:
    """Round soft dabs along a 3D stroke, rasterized into every triangle within reach."""
    from .kernel.sculpt import _dabs

    w, h = img.size
    px = img.load()
    rgba = _hex(color)
    k = max(0.0, min(1.0, float(strength)))
    tris = mesh.triangles()
    spheres = []
    for a, b, c, fi in tris:
        pa, pb, pc = mesh.verts[a], mesh.verts[b], mesh.verts[c]
        centre = [(pa[i] + pb[i] + pc[i]) / 3 for i in range(3)]
        spheres.append((centre, max(math.dist(centre, pa), math.dist(centre, pb), math.dist(centre, pc))))
    painted = 0
    for centre, _ in _dabs(points, radius, spacing):
        for t, (a, b, c, fi) in enumerate(tris):
            sc, sr = spheres[t]
            if math.dist(sc, centre) > sr + radius:
                continue
            uv = mesh.tri_uv(fi, (a, b, c))
            ua, ub, uc = uv[0:2], uv[2:4], uv[4:6]
            pa, pb, pc = mesh.verts[a], mesh.verts[b], mesh.verts[c]
            xs = [ua[0] * w, ub[0] * w, uc[0] * w]
            ys = [(1 - ua[1]) * h, (1 - ub[1]) * h, (1 - uc[1]) * h]
            den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(den) < 1e-12:
                continue
            for y in range(max(0, int(min(ys))), min(h, int(max(ys)) + 1)):
                for x in range(max(0, int(min(xs))), min(w, int(max(xs)) + 1)):
                    cx, cy = x + 0.5, y + 0.5
                    l0 = ((ys[1] - ys[2]) * (cx - xs[2]) + (xs[2] - xs[1]) * (cy - ys[2])) / den
                    l1 = ((ys[2] - ys[0]) * (cx - xs[2]) + (xs[0] - xs[2]) * (cy - ys[2])) / den
                    l2 = 1 - l0 - l1
                    if l0 < -0.01 or l1 < -0.01 or l2 < -0.01:
                        continue
                    p = [pa[i] * l0 + pb[i] * l1 + pc[i] * l2 for i in range(3)]
                    f = falloff(math.dist(p, centre), radius) * k * rgba[3] / 255
                    if f <= 0:
                        continue
                    old = px[x, y]
                    px[x, y] = tuple(int(old[i] + (rgba[i] - old[i]) * f + 0.5) for i in range(3)) + (255,)
                    painted += 1
    return painted


def grain(img: Image.Image, amount: float = 0.08, scale: float = 8.0, seed: int = 0) -> None:
    """Soft noise over the whole texture, so flat paint reads as a surface."""
    from .kernel.sculpt import noise3

    w, h = img.size
    px = img.load()
    for y in range(h):
        for x in range(w):
            n = (noise3(x / scale, y / scale, 0.5, seed) * 2 - 1) * amount * 255
            r, g, b, a = px[x, y]
            px[x, y] = (int(max(0, min(255, r + n))), int(max(0, min(255, g + n))), int(max(0, min(255, b + n))), a)
