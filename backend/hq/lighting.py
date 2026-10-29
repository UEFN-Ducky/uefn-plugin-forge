"""HQ look-dev lighting: one Eevee still of the asked objects under a light preset or an outdoor time of day."""
from . import engine, op
from .materials import _pick

# names only: the values live in engine/ops/lighting.py, which needs bpy
PRESETS = ("studio", "product", "dramatic", "soft", "neutral")
ENVS = ("noon", "golden", "night")
QUALITY = ("preview", "final", "product")
TRANSFORMS = ("agx", "neutral", "standard")


@op("light", "light(ids?|id?, preset=studio|product|dramatic|soft|neutral, env?=noon|golden|night, view=three|front|side|top, quality=preview|final|product, transform=agx|neutral|standard, size=[640,480]) look-dev still; see skill forge/lighting")
def light(scene, p):
    params = {"preset": _pick(p.get("preset", "studio"), PRESETS, "preset"),
              "env": _pick(p["env"], ENVS, "env") if p.get("env") else None,
              "view": _pick(p.get("view", "three"), ("three", "front", "side", "top"), "view"),
              "quality": _pick(p.get("quality", "preview"), QUALITY, "quality"),
              "transform": _pick(p.get("transform", "agx"), TRANSFORMS, "transform"),
              "size": list(p.get("size") or (640, 480)), "out_dir": str(scene.folder / "renders"),
              "textures_dir": str(scene.folder / "textures")}
    from ..scene import is_collision  # late: scene imports hq
    ids = p.get("ids") or ([p["id"]] if p.get("id") else [
        n["id"] for n in scene.data["nodes"]
        if n.get("type") == "object" and (p.get("show_collision") or not is_collision(n))])
    for nid in ids:
        if scene.node(str(nid)) is None:
            raise ValueError("No object %r to light" % nid)
    if not ids:
        raise ValueError("Nothing to light: add an object first")
    r, _ = engine(scene, "light", [str(i) for i in ids], params)
    scene.data["light"] = {k: params[k] for k in ("preset", "env", "transform")}  # for look/render to pick up
    what = params["env"] or params["preset"]
    return {"summary": "%s light, %s view, %s: %s" % (what, params["view"], params["transform"], r["path"]), "path": r["path"]}
