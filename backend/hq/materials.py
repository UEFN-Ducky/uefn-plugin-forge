"""HQ materials: layer-spec materials from 40 presets, and baking them to engine-ready textures."""
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from ..engine.ops.materials import BASE_KEYS, DEFAULTS, graph, normalize
from . import engine, op

_PRESETS = Path(__file__).resolve().parents[2] / "assets" / "materials" / "presets.json"
_MAPS = ("base_color", "normal", "orm", "emissive", "opacity")
_TARGETS = ("ue", "dx", "unity", "godot", "gl")


@lru_cache(maxsize=1)
def presets():
    return json.loads(_PRESETS.read_text(encoding="utf-8"))


def _pick(value, options, what):
    if value in options:
        return value
    close = difflib.get_close_matches(str(value), options, n=1)
    raise ValueError("No %s %r.%s %ss: %s" % (what, value, " Did you mean %r?" % close[0] if close else "", what.capitalize(), ", ".join(options)))


def expand(preset=None, overrides=None, layers=None):
    """{"preset"?, "layers": [...]} with the base layer first. Overrides patch the preset's base values
    (color, metallic, roughness, emissive, ...) or one layer type's params ({"edge_wear": {"amount": .9}})."""
    base, extra = {}, []
    if preset:
        found = presets()[_pick(preset, presets(), "preset")]
        base = {k: v for k, v in found.items() if k in BASE_KEYS}
        extra = found.get("layers") or []
    items = normalize(layers if layers is not None else extra)
    patch = dict(overrides or {})
    for key in [k for k in patch if k in BASE_KEYS]:
        base[key] = patch.pop(key)
    for kind in [k for k in patch if k in DEFAULTS and k != "base"]:
        for item in items:
            if item["type"] == kind:
                item.update(patch[kind])
        patch.pop(kind)
    if patch:
        raise ValueError("Override %r unknown. Use %s or a layer type (%s)" % (next(iter(patch)), ", ".join(BASE_KEYS), ", ".join(k for k in DEFAULTS if k != "base")))
    first = next((i for i, item in enumerate(items) if item["type"] == "base"), None)
    if first is None:
        items.insert(0, {"type": "base", **base})
    else:
        items[first] = {**items[first], **base}
    normalize(items)
    graph(items)  # compile check: raises on a bad layer before anything is stored
    spec = {"layers": items}
    if preset:
        spec["preset"] = preset
    return spec


def _material(scene, name, spec):
    from ..scene import _op_material_set
    base = spec["layers"][0]
    fields = {"name": name}
    if "color" in base:
        fields["base_color"] = list(base["color"])[:3] + [1.0]
    for key in ("metallic", "roughness", "emissive_strength", "emissive", "opacity"):
        if key in base and base[key] is not None:
            fields[key] = base[key]
    mat = _op_material_set(scene, fields)["material"]
    mat["spec"] = spec
    return mat


@op("material", "material(material, preset?, overrides?, layers?, id?) layer-spec PBR material; layers: base,noise,edge_wear,dirt,triplanar,scratches,sss,clearcoat,normal; see skill forge/materials")
def material(scene, p):
    name = str(p.get("material") or p.get("preset") or "")
    if not name:
        raise ValueError("Pass material=<name> and preset=<one of %s...>" % ", ".join(list(presets())[:5]))
    spec = expand(p.get("preset"), p.get("overrides"), p.get("layers"))
    mat = _material(scene, name, spec)
    if p.get("id"):
        from ..scene import _op_material_assign
        _op_material_assign(scene, {"id": p["id"], "material": mat["id"]})
    scene.touch_geom()
    return {"summary": "%s: %d layers" % (mat["name"], len(spec["layers"])), "ids": [mat["id"]],
            "stats": {"layers": len(spec["layers"])}}


def _high(scene, node, given):
    """(high_id, high_path): another scene object, a .blend sidecar path, or the object's sculpt_hd sidecar."""
    if not given:
        return None, None
    if given is True or given == "auto":
        path = scene.folder / "sculpt_hd" / (node["id"] + ".blend")
    elif str(given).lower().endswith(".blend"):
        path = Path(str(given))
    else:
        if scene.node(str(given)) is None:
            raise ValueError("from_high %r is no object. Pass an object id, a .blend path, or true for the sculpt_hd sidecar" % given)
        return str(given), None
    if not path.is_file():
        raise ValueError("No sculpt_hd sidecar at %s; run sculpt_hd first or pass from_high=<path.blend|object id>" % path)
    return None, str(path)


@op("bake", "bake(id, size=1024, maps?, from_high?, target=ue) bake to BaseColor/Normal/ORM/Emissive/Opacity PNGs into material.textures; see skill forge/materials", long=True)
def bake(scene, p):
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object") or ""))
    if node is None or node.get("type") != "object":
        raise ValueError("Pass the id of the object to bake")
    size = int(p.get("size", 1024))
    if size < 64 or size > 8192 or size & (size - 1):
        raise ValueError("size %s must be a power of two from 64 to 8192" % size)
    maps = p.get("maps")
    if isinstance(maps, str):
        maps = _MAPS if maps == "all" else [m for m in re.split(r"[,\s]+", maps) if m]
    for m in maps or []:
        _pick(m, _MAPS, "map")
    target = _pick(p.get("target", "ue"), _TARGETS, "target")
    high_id, high_path = _high(scene, node, p.get("from_high"))
    textures = scene.folder / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    params = {"id": node["id"], "size": size, "maps": maps, "target": target, "out_dir": str(textures),
              "textures_dir": str(textures), "high_id": high_id, "high_path": high_path}
    result, changed = engine(scene, "bake", [node["id"]] + ([high_id] if high_id else []), params)
    mats = scene.materials()
    for mid, files in result["textures"].items():
        if mid in mats:
            mats[mid].setdefault("textures", {}).update(files)
    scene.touch_geom()
    files = [f for fs in result["textures"].values() for f in fs.values()]
    return {"summary": "%d maps %dpx on %s (%s)" % (len(files), size, result["device"], ", ".join(files[:4])),
            "ids": list(result["textures"]), "path": str(textures),
            "stats": {"size": size, "files": len(files), "uv": bool(result["unwrapped"])}}
