"""HQ materials: layer-spec materials from 46 presets (or a texture folder), lint, and baking to engine-ready textures."""
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from ..engine.ops.materials import BASE_KEYS, DEFAULTS, graph, normalize
from . import engine, op

_PRESETS = Path(__file__).resolve().parents[2] / "assets" / "materials" / "presets.json"
_MAPS = ("base_color", "normal", "orm", "emissive", "opacity", "ao", "curvature", "id", "thickness")
_IMG = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".webp")
# filename token -> slot, read from the end of the name (Poly Haven, ambientCG, Substance, UE T_X_BC)
_SLOTS = {"orm": {"orm", "arm", "occlusionroughnessmetallic"},
          "normal": {"normal", "normalgl", "normaldx", "nor", "nrm", "norm", "n"},
          "roughness": {"roughness", "rough", "rgh"}, "metallic": {"metallic", "metalness", "metal", "met"},
          "ao": {"ao", "ambientocclusion", "occlusion", "occ"}, "emissive": {"emissive", "emission", "emit", "e"},
          "opacity": {"opacity", "alpha", "transparency"}, "height": {"height", "disp", "displacement", "bump", "h"},
          "base_color": {"basecolor", "albedo", "diffuse", "diff", "color", "col", "bc", "d", "base"}}
_TARGETS = ("ue", "dx", "unity", "godot", "gl")
_WORN = [{"type": "noise", "target": "roughness", "scale": 15, "amount": 0.15}, {"type": "scratches", "color": [0.6, 0.6, 0.62], "amount": 0.3},
         {"type": "edge_wear", "color": [0.6, 0.6, 0.62], "amount": 0.7, "metal": True}, {"type": "dirt", "amount": 0.4}]
# measured linear F0 for metals (physicallybased.info); ponytail: lives here until presets.json takes them
_MORE = {
    "iron": {"cat": "metal", "color": [0.56, 0.57, 0.58], "metallic": 1.0, "roughness": 0.5,
             "layers": [{"type": "noise", "target": "roughness", "scale": 12, "amount": 0.2}]},
    "aluminum": {"cat": "metal", "color": [0.91, 0.92, 0.92], "metallic": 1.0, "roughness": 0.3, "layers": []},
    "silver": {"cat": "metal", "color": [0.97, 0.96, 0.91], "metallic": 1.0, "roughness": 0.15, "layers": []},
    "titanium": {"cat": "metal", "color": [0.54, 0.5, 0.45], "metallic": 1.0, "roughness": 0.35, "layers": []},
    "painted_metal": {"cat": "metal", "color": [0.12, 0.25, 0.45], "metallic": 0.0, "roughness": 0.45, "layers": _WORN},
    "gloss": {"cat": "plastic", "color": [0.6, 0.6, 0.6], "metallic": 0.0, "roughness": 0.02,
              "layers": [{"type": "clearcoat", "weight": 1.0, "roughness": 0.02}]},
}


@lru_cache(maxsize=1)
def presets():
    return {**json.loads(_PRESETS.read_text(encoding="utf-8")), **_MORE}


def _pick(value, options, what):
    if value in options:
        return value
    close = difflib.get_close_matches(str(value), options, n=1)
    raise ValueError("No %s %r.%s %ss: %s" % (what, value, " Did you mean %r?" % close[0] if close else "", what.capitalize(), ", ".join(options)))


def _preset(name):
    """A preset name, or a category (metal, wood, stone, fabric, skin, plastic, ...) for its first preset."""
    found = presets()
    if name not in found:
        name = next((k for k, v in found.items() if v.get("cat") == name), name)
    return found[_pick(name, found, "preset")]


def _srgb(c):
    c = max(0.0, float(c))
    return 255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)


def _slot(stem):
    """(slot, convention) of a texture file stem: the last token naming a map wins; normals say gl or dx."""
    tokens = [t for t in re.split(r"[_\-. ]+", stem.lower()) if t]
    conv = "dx" if {"dx", "normaldx"} & set(tokens) or tokens[-1:] == ["n"] else "gl"
    for token in reversed(tokens[1:] or tokens):
        for slot, names in _SLOTS.items():
            if token in names:
                return slot, conv
    return None, conv


def lint(spec):
    """PBR range warnings for a spec: metallic 0 or 1, albedo sRGB 30-240 (metals 180+), maps in the right slot."""
    preset = presets().get(spec.get("preset") or "", {})
    if preset.get("cat") == "stylized":
        return []  # toon colours are art-directed
    base = {**DEFAULTS["base"], **spec["layers"][0]}
    out = []
    metal = float(base["metallic"])
    bright = max(_srgb(c) for c in list(base["color"])[:3])
    if 0.05 < metal < 0.95:
        out.append("metallic %.2f: real surfaces are 0 or 1; mask the mix" % metal)
    if metal >= 0.5 and bright < 180:
        out.append("metal albedo sRGB %d < 180: metals are bright, use roughness or dirt" % bright)
    if metal < 0.5 and not 30 <= bright <= 240:
        out.append("albedo sRGB %d outside 30-240 (charcoal 30, snow 240)" % bright)
    for layer in spec["layers"]:
        if layer["type"] == "image":
            slot = _slot(Path(layer["image"]).stem)[0]
            target = layer.get("target", "color")
            if (target == "color") != (slot in ("base_color", None)):
                out.append("%s looks like a %s map but feeds %s (colour space)" % (layer["image"], slot or "colour", target))
    return out


def texture_set(folder):
    """({slot: Path}, normal convention gl|dx) for a folder of maps named the Poly Haven / ambientCG / Substance / UE way."""
    folder = Path(str(folder))
    if not folder.is_dir():
        raise ValueError("textures=%s is not a folder of texture maps" % folder)
    found, conv = {}, "gl"
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in _IMG:
            continue
        slot, c = _slot(path.stem)
        if slot and (slot not in found or (slot == "normal" and c == "gl")):
            found[slot] = path
            if slot == "normal":
                conv = c
    if not found:
        raise ValueError("No texture maps in %s: name them *_BaseColor, *_Normal, *_Roughness, ... or T_X_BC/N/ORM" % folder)
    return found, conv


def import_set(scene, name, folder, target, base):
    """Copy a texture set into the scene as T_<name>_BC/N/ORM/E/O.png (ORM packed, normal in target's convention).
    Returns (Forge texture slots, image layers, slots skipped)."""
    from PIL import Image, ImageOps
    src, conv = texture_set(folder)
    out = scene.folder / "textures"
    out.mkdir(parents=True, exist_ok=True)
    stem = "T_%s_" % re.sub(r"[^A-Za-z0-9_-]", "_", name)[:48]
    files, layers = {}, []

    def save(img, suffix):
        files_name = stem + suffix + ".png"
        img.save(out / files_name)
        return files_name

    if "base_color" in src:
        files["base_color"] = save(Image.open(src["base_color"]).convert("RGB"), "BC")
        layers.append({"type": "image", "image": files["base_color"], "target": "color"})
    if "normal" in src:
        img, dx = Image.open(src["normal"]).convert("RGB"), target in ("ue", "dx")
        if (conv == "dx") != dx:
            r, g, b = img.split()
            img = Image.merge("RGB", (r, ImageOps.invert(g), b))
        files["normal"] = save(img, "N")
        layers.append({"type": "normal", "image": files["normal"], "dx": dx})
    if "orm" in src or {"ao", "roughness", "metallic"} & set(src):
        if "orm" in src:
            orm = Image.open(src["orm"]).convert("RGB")
        else:
            given = {k: Image.open(src[k]).convert("L") for k in ("ao", "roughness", "metallic") if k in src}
            size = max((img.size for img in given.values()), key=lambda s: s[0])
            fill = {"ao": 255, "roughness": round(255 * float(base.get("roughness", 0.5))), "metallic": round(255 * float(base.get("metallic", 0.0)))}
            orm = Image.merge("RGB", [given[k].resize(size) if k in given else Image.new("L", size, fill[k])
                                      for k in ("ao", "roughness", "metallic")])
        files["orm"] = save(orm, "ORM")
        layers.append({"type": "image", "image": files["orm"], "target": "orm"})
    if "emissive" in src:
        files["emissive"] = save(Image.open(src["emissive"]).convert("RGB"), "E")
        layers.append({"type": "image", "image": files["emissive"], "target": "emissive"})
    if "opacity" in src:
        files["opacity"] = save(Image.open(src["opacity"]).convert("L"), "O")
        layers.append({"type": "image", "image": files["opacity"], "target": "opacity"})
    return files, layers, sorted(set(src) - {"base_color", "normal", "orm", "ao", "roughness", "metallic", "emissive", "opacity"})


def expand(preset=None, overrides=None, layers=None):
    """{"preset"?, "layers": [...]} with the base layer first. Overrides patch the preset's base values
    (color, metallic, roughness, emissive, ...) or one layer type's params ({"edge_wear": {"amount": .9}})."""
    base, extra = {}, []
    if preset:
        found = _preset(preset)
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


@op("material", "material(material, preset?, overrides?, layers?, textures?=<folder>, target=ue, id?) layer-spec PBR material (preset may be a category: metal, wood, stone...); textures wires a map folder by suffix; layers: base,noise,edge_wear,dirt,triplanar,scratches,sss,clearcoat,normal,image; see skill forge/hq_materials")
def material(scene, p):
    name = str(p.get("material") or p.get("preset") or "")
    if not name:
        raise ValueError("Pass material=<name> and preset=<one of %s...>" % ", ".join(list(presets())[:5]))
    spec = expand(p.get("preset"), p.get("overrides"), p.get("layers"))
    files, skipped = {}, []
    if p.get("textures"):
        files, images, skipped = import_set(scene, name, p["textures"], _pick(p.get("target", "ue"), _TARGETS, "target"), spec["layers"][0])
        spec["layers"][1:1] = normalize(images)  # maps right after the base: wear layers still sit on top
    mat = _material(scene, name, spec)
    if files:
        mat.setdefault("textures", {}).update(files)
    if p.get("id"):
        from ..scene import _op_material_assign
        _op_material_assign(scene, {"id": p["id"], "material": mat["id"]})
    scene.touch_geom()
    warnings = lint(spec) + ["skipped %s map (no slot)" % s for s in skipped]
    extra = ", maps %s" % "/".join(files) if files else ""
    return {"summary": "%s: %d layers%s" % (mat["name"], len(spec["layers"]), extra), "ids": [mat["id"]],
            "stats": {"layers": len(spec["layers"]), "maps": len(files)}, "warnings": warnings}


def _high(scene, node, given):
    """(high_id, high_path): another scene object, a .blend sidecar path, or the object's sculpt_hd sidecar."""
    if not given:
        return None, None
    if given is True or given == "auto":
        path = scene.folder / "hd" / (node["id"] + ".blend")  # where sculpt_hd writes it
    elif str(given).lower().endswith(".blend"):
        path = Path(str(given))
    else:
        if scene.node(str(given)) is None:
            raise ValueError("from_high %r is no object. Pass an object id, a .blend path, or true for the sculpt_hd sidecar" % given)
        return str(given), None
    if not path.is_file():
        raise ValueError("No sculpt_hd sidecar at %s; run sculpt_hd first or pass from_high=<path.blend|object id>" % path)
    return None, str(path)


@op("bake", "bake(id, size=1024, maps?, from_high?, source=mesh|multires, cage?=<id>, extrusion=.03, ray=0, margin_px=16, target=ue, to=texture|vcol|lightmap) BaseColor/Normal/ORM/Emissive/Opacity + ao/curvature/id/thickness PNGs; reports miss % from high; lightmap = combined light on the uv(lightmap=true) set; see skill forge/baking", long=True)
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
    multires = _pick(p.get("source", "mesh"), ("mesh", "multires"), "source") == "multires"
    high_id, high_path = _high(scene, node, p.get("from_high") or multires)
    if multires and not high_path:
        raise ValueError("source=multires bakes from the sculpt_hd sidecar: drop from_high=<object id> or pass its .blend")
    cage = str(p.get("cage") or "")
    if cage and cage != "auto" and scene.node(cage) is None:
        raise ValueError("cage %r is no object: pass cage=auto or the id of a copy of the low-poly pushed out" % cage)
    cage = "" if cage == "auto" else cage
    textures = scene.folder / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    params = {"id": node["id"], "size": size, "target": target, "out_dir": str(textures),
              "textures_dir": str(textures), "high_id": high_id, "high_path": high_path, "cage_id": cage or None,
              "extrusion": float(p.get("extrusion", 0.03)), "ray": float(p.get("ray", 0)), "margin": int(p.get("margin_px", 16))}
    ids = [node["id"]] + [i for i in (high_id, cage) if i]
    if p.get("to") == "lightmap":
        r, changed = engine(scene, "bake_maps", [node["id"]], dict(params, high_id=None, high_path=None, to="lightmap",
                                                                     name=node.get("name") or node["id"], light=scene.data.get("light")))
        if r["unwrapped"]:
            scene.touch_geom()
        warn = ["UVs overlap %.1f%%: run uv(id, lightmap=true) first" % r["overlap_pct"]] if r["overlap_pct"] > 0.5 else []
        return {"summary": "lightmap %dpx %s on %s" % (size, r["lightmap"], r["device"]), "ids": [node["id"]],
                "path": str(textures / r["lightmap"]), "warnings": warn, "stats": {"size": size, "overlap_pct": r["overlap_pct"]}}
    if p.get("to") == "vcol":  # baked into point colours bake_<map>: no UVs, no textures (LOD tints, mobile AO)
        vmaps = list(maps or ["ao"])
        for m in vmaps:
            _pick(m, ("ao", "base_color", "curvature", "thickness", "id"), "vertex-colour map")
        r, changed = engine(scene, "bake_maps", ids, dict(params, maps=vmaps, to="vcol"))
        scene.touch_geom()
        return {"summary": "%s into vertex colours on %s" % (", ".join(r["vcol"]), r["device"]), "ids": changed or [node["id"]],
                "stats": {"maps": len(r["vcol"])}}
    if p.get("to", "texture") != "texture":
        raise ValueError("to=%r: use texture (default), vcol or lightmap" % p["to"])
    if multires:  # the normal comes from multires levels; other maps bake from the low-poly alone
        maps = list(maps or ["normal"])
        classic = [m for m in maps if m in _MAPS[:5] and m != "normal"]
        extra = [m for m in maps if m not in classic]
        result, merged = {"device": "CPU", "unwrapped": False}, {}
        if classic:
            result, _ = engine(scene, "bake", [node["id"]], dict(params, maps=classic, high_path=None))
            merged = result["textures"]
        more, _ = engine(scene, "bake_maps", ids, dict(params, maps=extra, source="multires"))
        for mid, files in more["textures"].items():
            merged.setdefault(mid, {}).update(files)
        result = dict(more, unwrapped=result["unwrapped"] or more["unwrapped"])
        return _done(scene, size, textures, merged, result, ", %d multires levels, miss %.1f%%" % (more["levels"], more["miss_pct"]))
    # the classic engine bake keeps its own cage and margin; a cage, ray, extrusion or margin moves normal to bake_maps
    tuned = any(k in p for k in ("cage", "extrusion", "ray", "margin_px"))
    classic = None if maps is None else [m for m in maps if m in _MAPS[:5] and not (m == "normal" and tuned)]
    extra = ["normal"] if maps is None and tuned else [m for m in maps or [] if m not in classic]
    result, merged = {"device": "CPU", "unwrapped": False}, {}
    if classic is None or classic:
        result, _ = engine(scene, "bake", ids, dict(params, maps=classic))
        merged = result["textures"]
    if extra or high_id or high_path:
        more, _ = engine(scene, "bake_maps", ids, dict(params, maps=extra))
        for mid, files in more["textures"].items():
            merged.setdefault(mid, {}).update(files)
        result = dict(more, unwrapped=result["unwrapped"] or more["unwrapped"])
    miss = result.get("miss_pct")
    return _done(scene, size, textures, merged, result, ", miss %.1f%%" % miss if miss is not None else "")


def _done(scene, size, textures, merged, result, note):
    mats = scene.materials()
    for mid, files in merged.items():
        if mid in mats:
            mats[mid].setdefault("textures", {}).update(files)
    scene.touch_geom()
    files = [f for fs in merged.values() for f in fs.values()]
    miss = result.get("miss_pct")
    warnings = ["%.1f%% of texels missed the high-poly: raise extrusion or ray, or use a cage" % miss] if miss and miss > 3 else []  # a clean bake still drops ~2% at cage corners
    return {"summary": "%d maps %dpx on %s%s (%s)" % (len(files), size, result["device"], note, ", ".join(files[:4])),
            "ids": list(merged), "path": str(textures), "warnings": warnings,
            "stats": {"size": size, "files": len(files), "uv": bool(result["unwrapped"]), "miss_pct": miss}}
