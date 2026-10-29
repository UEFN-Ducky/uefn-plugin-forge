"""HQ paint_art: hand-painted textures. Directional strokes over baked AO, and reference art projected onto the model."""
from pathlib import Path

from . import engine, op

_FLOWS = ("down", "normal", "radial", "tufts")


def _object(scene, p):
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object") or ""))
    if node is None or node.get("type") != "object":
        raise ValueError("Pass the id of the object to paint")
    return node


def _size(p):
    size = int(p.get("size", 2048))
    if size < 64 or size > 4096 or size & (size - 1):
        raise ValueError("size %s must be a power of two from 64 to 4096" % size)
    return size


def _own_material(scene, node, base):
    """Id of a material only this object uses; makes one when it has none or shares the scene default."""
    from .materials import _material, expand
    from ..scene import _op_material_assign
    mid = node.get("material")
    shared = sum(1 for n in scene.data["nodes"] if n.get("material") == mid) > 1
    if mid in scene.materials() and not shared and mid != scene.data["materials"][0]["id"]:
        return mid
    spec = expand(None, None, [{"type": "base", "color": list(base)[:3]}])
    mat = _material(scene, "%s_Paint" % (node.get("name") or node["id"]), spec)
    _op_material_assign(scene, {"id": node["id"], "material": mat["id"]})
    return mat["id"]


def _run(scene, name, p, params):
    node = _object(scene, p)
    base = p.get("base") or [0.8, 0.55, 0.2]
    if isinstance(base, str):
        h = base.lstrip("#")
        base = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)] if len(h) == 6 else [0.8, 0.55, 0.2]
    mid = _own_material(scene, node, [c / 255.0 if max(base) > 1 else c for c in base])
    textures = scene.folder / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    params = {**params, "id": node["id"], "material": mid, "size": _size(p), "out_dir": str(textures), "target": p.get("target", "ue"),
              "seed": int(p.get("seed", 1)), "debug": bool(p.get("debug"))}
    for key in ("width", "length", "density", "ao", "rim", "flow"):
        if p.get(key) is not None:
            params[key] = p[key]
    result, _ = engine(scene, name, [node["id"]], params)
    mats = scene.materials()
    for tid, files in result["textures"].items():
        if tid in mats:
            mats[tid].setdefault("textures", {}).update(files)
    scene.touch_geom()
    return node, result, textures


@op("paint_strokes", "paint_strokes(id, base, shadow, highlight, flow=down|normal|radial|tufts, density=1, width, length, ao=0.6, rim=0.3, size=2048) AO shading + tapered brush strokes along the flow; writes BaseColor+Normal; see skill forge/hq", long=True)
def paint_strokes(scene, p):
    flow = p.get("flow", "down")
    if flow not in _FLOWS:
        raise ValueError("flow %r unknown. Use one of: %s" % (flow, ", ".join(_FLOWS)))
    params = {k: p[k] for k in ("base", "shadow", "highlight") if p.get(k) is not None}
    node, r, textures = _run(scene, "paint_strokes", p, params)
    return {"summary": "painted %s %dpx %s: %d strokes, %d%% covered" % (node.get("name") or node["id"], r["size"], flow, r["strokes"], r["cover"] * 100),
            "ids": [node["id"]], "path": str(textures), "stats": {"strokes": r["strokes"], "size": r["size"], "uv": bool(r["unwrapped"])}}


@op("project_ref", "project_ref(id, images={front: path, back: path, side?: path}, blend=soft|hard, fill=strokes|flat, size=2048) paint reference art onto the model from its views; see skill forge/hq", long=True)
def project_ref(scene, p):
    images = p.get("images")
    if not isinstance(images, dict) or not images:
        raise ValueError("Pass images={front: path, back: path, side: path} (absolute paths to the reference pictures)")
    for view, path in images.items():
        if not Path(str(path)).is_file():
            raise ValueError("images[%s] = %s is not a file; pass the absolute path of the picture" % (view, path))
    params = {"images": {k: str(v) for k, v in images.items()}, "blend": p.get("blend", "soft"), "fill": p.get("fill", "strokes")}
    node, r, textures = _run(scene, "project_ref", p, params)
    return {"summary": "projected %s onto %s %dpx; %d%% seen, %d strokes fill the rest" % (", ".join(r["views"]), node.get("name") or node["id"], r["size"], r["seen"] * 100, r["strokes"]),
            "ids": [node["id"]], "path": str(textures), "stats": {"seen": r["seen"], "strokes": r["strokes"], "size": r["size"]}}


_PRESETS = ("fur_warm", "mane", "cream", "pink")


@op("paint_bake", "paint_bake(ids|id, presets=fur_warm|mane|cream|pink or [per slot] or {material: preset}, stroke=dash|grain, flow=tufts, name?, size=2048) paint every material slot (bands, crevices, dashes), join parts, bake ONE texture + ONE material; see skill forge/hq", long=True)
def paint_bake(scene, p):
    ids = [str(i) for i in (p.get("ids") or [p.get("id") or scene.data["selection"].get("object") or ""])]
    stroke = p.get("stroke", "dash")
    if stroke not in ("dash", "grain"):
        raise ValueError("stroke %r unknown. Use dash (short tapered dashes) or grain (long streaks)" % stroke)
    given = p.get("presets")
    for name in ([given] if isinstance(given, str) else list(given.values()) if isinstance(given, dict) else given or []):
        if name not in _PRESETS:
            raise ValueError("preset %r unknown. Use one of: %s" % (name, ", ".join(_PRESETS)))
    size = _size(p)
    if len(ids) > 1:
        from ..scene import _op_join
        ids = [_op_join(scene, {"ids": ids})["id"]]
    node = _object(scene, {"id": ids[0]})
    name = str(p.get("name") or node.get("name") or "Paint")
    from .materials import _material, expand
    mat = _material(scene, "M_%s_Paint" % name, expand(None, None, [{"type": "base", "color": [1.0, 1.0, 1.0]}]))
    textures = scene.folder / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    params = {"id": node["id"], "out_mat": mat["id"], "name": name, "size": size, "out_dir": str(textures),
              "seed": int(p.get("seed", 1)), "debug": bool(p.get("debug")), "stroke": stroke, "presets": given}
    for key in ("flow", "width", "length", "density", "ao", "rim"):
        if p.get(key) is not None:
            params[key] = p[key]
    result, _ = engine(scene, "paint_bake", [node["id"]], params)
    mesh = scene._bake(node)
    mesh.face_mat = [mat["id"]] * len(mesh.face_mat)  # one material for the whole object
    scene.write_mesh(node, mesh)
    from ..scene import _op_material_assign
    _op_material_assign(scene, {"id": node["id"], "material": mat["id"]})
    scene.materials()[mat["id"]].setdefault("textures", {}).update(result["textures"][mat["id"]])
    scene.touch_geom()
    return {"summary": "ok paint_bake %d %s, %d presets, 1 material%s" % (result["size"], result["texture"], len(result["presets"]), ", mane roots from tuft colour" if result.get("tuft") else ""),
            "ids": [node["id"]], "stats": {"strokes": result["strokes"], "stroke": stroke}}
