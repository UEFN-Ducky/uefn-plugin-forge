"""Flat preview renders for the agent. No panel required.

ponytail: painter's algorithm (depth sort, no z-buffer). Back faces draw first so
a closed solid's far side never lands on top; intersecting triangles can still
draw in the wrong order. Upgrade path is a z-buffer if a prop reads wrong.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from .kernel.mesh import Mesh, vdot, vnorm, vsub

# Which renderer made the last image: "eevee" or "fallback renderer".
last_renderer = "eevee"
# "" after an Eevee render, else "fallback renderer: <reason>"; callers put it in their result and `seen`.
last_note = ""


def _fell_back(reason: str) -> None:
    global last_note
    last_note = "fallback renderer: " + (reason.strip().splitlines() or ["unknown"])[-1][:160]

_VIEWS = {
    "three": (1.3, -1.6, 0.9),
    "front": (0.0, -2.2, 0.45),
    "top": (0.0, -0.01, 2.4),
    "side": (2.2, 0.0, 0.45),
    "left": (-2.2, 0.0, 0.45),
    "back": (0.0, 2.2, 0.45),
    "three_back": (-1.3, 1.6, 0.9),
}
# Turntable stills: az0 is the front, then every 45 degrees clockwise seen from above.
_VIEWS.update({f"az{a}": (2.2 * math.sin(math.radians(a)), -2.2 * math.cos(math.radians(a)), 0.45) for a in range(0, 360, 45)})


def render_mesh(mesh: Mesh, view: str = "three", size: tuple[int, int] = (320, 240), materials: dict | None = None,
                textures: Path | None = None) -> Image.Image:
    """textures is the scene's texture folder: painted and generated base colours show through the UVs."""
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
    # Key light over the camera's left shoulder: faces toward the viewer read, and sides differ.
    light = vnorm([up[i] * 0.8 - forward[i] - right[i] * 0.4 for i in range(3)])
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
            facing = vdot(n, vsub(mesh.verts[a], eye)) < 0
            tris.append((facing, -depth, pts, shade, mat, (a, b, c), fi))
    tris.sort(key=lambda t: (t[0], t[1]))
    draw = ImageDraw.Draw(im)
    palette: dict = {}
    images: dict = {}
    blend = mesh.attrs.get("col:blend")
    for facing, _depth, pts, shade, mat, corners, fi in tris:
        base = palette.setdefault(mat, _color(mat, materials))
        if blend:
            mixed = _blended(base, mat, materials, [blend[v] for v in corners])
            if mixed is not base:
                draw.polygon(pts, fill=_shaded(mixed, shade))
                continue
        tex = _texture(mat, materials, textures, images) if facing else None
        uv = mesh.tri_uv(fi, corners) if tex is not None else None
        if tex is None or not _paste(im, tex, pts, [uv[0:2], uv[2:4], uv[4:6]], shade):
            draw.polygon(pts, fill=_shaded(base, shade))
    return im


def _engine_views(mesh: Mesh, views: list[str], size: tuple[int, int], materials: dict | None, textures: Path | None,
                  quality: str = "look", cold_ok: bool = True, style: str = "") -> list[Path] | None:
    """Eevee PNGs (one per view, in a temp folder the caller deletes), or None when the engine is missing or fails.

    cold_ok=False only uses an already-running worker (and starts it in the background), so a cold call never waits.
    """
    global last_renderer, last_note
    last_renderer = "fallback renderer"
    if not mesh.verts:
        _fell_back("empty mesh")
        return None
    if os.environ.get("FORGE_SOFT_RENDER"):
        _fell_back("FORGE_SOFT_RENDER is set; unset it for Eevee")
        return None
    try:
        from .engine import bridge, client, runtime
        state = runtime.engine_status()["state"]
        if state != "ready":
            _fell_back("Blender engine is %s; install it from the Forge panel" % state)
            return None
        # A small mesh on a cold worker would wait seconds for a start; a big one is slower in the soft renderer anyway.
        if not cold_ok and not client._worker.alive() and len(mesh.faces) < 2000:
            client.warm()
            _fell_back("engine is starting; look again in a few seconds")
            return None
        folder = Path(tempfile.mkdtemp(prefix="look-", dir=client.scratch()))
    except Exception as exc:
        _fell_back("%s: %s" % (type(exc).__name__, exc))
        return None
    try:
        materials = materials or {}
        slots = list(dict.fromkeys(mesh.face_mat)) or ["default"]
        slot_of = {m: i for i, m in enumerate(slots)}
        loops = [i for face in mesh.faces for i in face]
        put = lambda name, values, shape, kind="d": (bridge.write_npy(folder / (name + ".npy"), values, shape, kind), name + ".npy")[1]
        arrays = {
            "verts": put("verts", [c for v in mesh.verts for c in v], (len(mesh.verts), 3)),
            "sizes": put("sizes", [len(f) for f in mesh.faces], (len(mesh.faces),), "i"),
            "loops": put("loops", loops, (len(loops),), "i"),
            "face_mat": put("face_mat", [slot_of[m] for m in mesh.face_mat], (len(mesh.faces),), "i"),
            "uv": put("uv", [c for face in mesh.uvs for uv in face for c in uv[:2]], (len(loops), 2)),
        }
        mats = []
        for mid in slots:
            forge = dict(materials.get(mid) or {})
            forge.setdefault("id", mid)
            if "base_color" not in forge:
                forge["base_color"] = [c / 255 for c in _color(mid, materials)] + [1.0]
            tex = {s: str(textures / n) for s, n in (forge.get("textures") or {}).items()} if textures else {}
            mats.append({"forge": forge, "textures": tex})
        identity = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
        payload = {"unit": 0.01, "folder": str(folder), "materials": mats, "rigs": [],
                   "objects": [{"id": "look", "name": "look", "matrix": identity, "smooth": mesh.smooth, "mats": slots, "arrays": arrays}]}
        result = client.call("render", {"scene": payload, "views": views, "size": list(size), "quality": quality,
                                        "out_dir": str(folder), "stem": "v", "style": style}, timeout=120.0)
        paths = [Path(p) for p in result["paths"]]
        if len(paths) != len(views) or not all(p.is_file() for p in paths):
            raise ValueError("engine wrote no image")
        last_renderer, last_note = "eevee", ""
        return paths
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        _fell_back("%s: %s" % (type(exc).__name__, exc))
        return None


def _stamp(im: Image.Image) -> Image.Image:
    """The fallback reason, written on the image so a viewer never mistakes it for an Eevee render."""
    ImageDraw.Draw(im).text((4, 4), last_note, fill=(255, 200, 60))
    return im


def render_png(mesh: Mesh, path: Path, views: list[str] | None = None, materials: dict | None = None,
               textures: Path | None = None, style: str = "") -> list[str]:
    views = views or ["three"]
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    shots = _engine_views(mesh, views, (480, 360), materials, textures, cold_ok=False, style=style)  # ponytail: first png call is the soft one
    for i, view in enumerate(views):
        dest = path if len(views) == 1 else path.with_name(f"{path.stem}_{view}{path.suffix}")
        if shots:
            shutil.copyfile(shots[i], dest)
        else:
            _stamp(render_mesh(mesh, view, materials=materials, textures=textures)).save(dest)
        out.append(str(dest))
    if shots:
        shutil.rmtree(shots[0].parent, ignore_errors=True)
    return out


def render_tiles(mesh: Mesh, path: Path, views: list[str], materials: dict | None = None,
                 textures: Path | None = None, long_side: int = 768, quality: str = "look", style: str = "") -> str:
    """One PNG, long side long_side px: a single view, or the views tiled two across.

    quality="final" is 64 samples at 1024 px for marketing shots; the default look is 16 samples.
    """
    if quality == "final":
        long_side = 1024
    cols = 1 if len(views) == 1 else 2
    rows = (len(views) + cols - 1) // cols
    tw = long_side // cols
    th = tw * 3 // 4
    if th * rows > long_side:
        th = long_side // rows
    sheet = Image.new("RGB", (tw * cols, th * rows), (22, 22, 26))
    shots = _engine_views(mesh, views, (tw, th), materials, textures, quality, style=style)
    for i, view in enumerate(views):
        if shots:
            with Image.open(shots[i]) as tile:
                tile = tile.convert("RGB")
        else:
            tile = _stamp(render_mesh(mesh, view, (tw, th), materials, textures))
        sheet.paste(tile, ((i % cols) * tw, (i // cols) * th))
    if shots:
        shutil.rmtree(shots[0].parent, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return str(path)


def _shaded(col, shade: float) -> tuple[int, int, int]:
    return tuple(int(max(0, min(255, c * shade))) for c in col)


def _blended(base, mat: str, materials: dict | None, rows: list) -> tuple:
    """The triangle's colour with its blend layers mixed in by its corners' mean mask; base itself when unmasked."""
    col = base
    for ch, layer in enumerate(((materials or {}).get(mat, {}).get("layers") or [])[:3]):
        t = sum(r[ch] for r in rows) / len(rows)
        if layer and t > 0.01:
            over = _color(layer, materials)
            col = tuple(col[i] + (over[i] - col[i]) * t for i in range(3))
    return col


def _texture(mat: str, materials: dict | None, folder: Path | None, cache: dict):
    name = ((materials or {}).get(mat, {}).get("textures") or {}).get("base_color")
    if not name or folder is None:
        return None
    if name not in cache:
        try:
            cache[name] = Image.open(folder / name).convert("RGB")
        except OSError:
            cache[name] = None
    return cache[name]


def _paste(im: Image.Image, tex: Image.Image, pts: list, uvs: list, shade: float) -> bool:
    """One triangle with its texture patch: an affine map from screen pixels to texture pixels.

    ponytail: affine per triangle, so large triangles seen at a steep angle swim a little; fine at preview size.
    """
    (x0, y0), (x1, y1), (x2, y2) = pts
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-9:
        return False
    tw, th = tex.size
    s = [(u * tw, (1 - v) * th) for u, v in uvs]
    left, top = int(min(x0, x1, x2)), int(min(y0, y1, y2))
    size = (int(max(x0, x1, x2)) + 1 - left, int(max(y0, y1, y2)) + 1 - top)
    coeffs = []
    for k in (0, 1):  # texture x, then texture y, as b*x + c*y + a over the patch's own pixels
        d1, d2 = s[1][k] - s[0][k], s[2][k] - s[0][k]
        b = (d1 * (y2 - y0) - d2 * (y1 - y0)) / det
        c = (d2 * (x1 - x0) - d1 * (x2 - x0)) / det
        coeffs += [b, c, s[0][k] + b * (left - x0) + c * (top - y0)]
    patch = tex.transform(size, Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BILINEAR)
    patch = patch.point([int(i * min(1.0, shade)) for i in range(256)] * 3)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in pts], fill=255)
    im.paste(patch, (left, top), mask)
    return True


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _color(mat: str, materials: dict | None = None) -> tuple[int, int, int]:
    authored = (materials or {}).get(mat, {}).get("base_color")
    if authored:
        # Lift darks a little so shading stays readable on near-black materials.
        return tuple(int(40 + 215 * max(0.0, min(1.0, float(c)))) for c in authored[:3])
    h = abs(hash(mat))
    return (80 + (h % 140), 90 + ((h // 7) % 120), 110 + ((h // 13) % 100))
