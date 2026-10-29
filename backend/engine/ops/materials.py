"""Material layer spec -> Blender shader nodes.

graph(layers) is pure Python (the host imports it to validate); build() and the ops need bpy.
A layer is a type name or {"type": ..., **params}; layers apply in order to one running
state (colour, roughness, metallic, height, ...), so a worn painted steel is four short layers.
Node refs are {"n": node id, "o": output name or "#index"}; any other input value is a constant.
"""
import contextlib
import difflib
import json
from pathlib import Path

BASE_KEYS = ("color", "metallic", "roughness", "emissive", "emissive_strength", "opacity")

DEFAULTS = {
    "base": {"color": [0.8, 0.8, 0.8], "metallic": 0.0, "roughness": 0.5, "emissive": None,
             "emissive_strength": 1.0, "opacity": 1.0},
    "noise": {"scale": 6.0, "detail": 8.0, "amount": 0.3, "target": "color", "color": None},
    "edge_wear": {"color": [0.55, 0.55, 0.58], "amount": 0.7, "width": 0.5, "scale": 8.0, "metal": True},
    "dirt": {"color": [0.08, 0.06, 0.04], "amount": 0.6, "scale": 4.0, "distance": 0.15},
    "triplanar": {"image": "", "scale": 1.0, "blend": 0.2, "target": "color"},
    "scratches": {"color": [0.7, 0.7, 0.7], "amount": 0.5, "scale": 30.0, "stretch": 20.0},
    "sss": {"weight": 0.5, "radius": [1.0, 0.4, 0.25], "scale": 0.1},
    "clearcoat": {"weight": 1.0, "roughness": 0.05},
    "normal": {"image": "", "strength": 1.0, "bump": 0.3, "dx": False},
    "image": {"image": "", "target": "color", "strength": 1.0},
}
_TARGETS = {"noise": ("color", "roughness", "bump"), "triplanar": ("color", "roughness", "bump"),
            "image": ("color", "roughness", "metallic", "orm", "emissive", "opacity")}


def normalize(layers):
    """Validated copies: [{"type": ..., **given params}]. Errors name the fix."""
    out = []
    for item in layers or []:
        layer = {"type": item} if isinstance(item, str) else dict(item)
        kind = layer.pop("type", None) or layer.pop("t", None)
        if kind not in DEFAULTS:
            close = difflib.get_close_matches(str(kind), DEFAULTS, n=1)
            raise ValueError("No layer %r.%s Layers: %s" % (kind, " Did you mean %r?" % close[0] if close else "", ", ".join(DEFAULTS)))
        bad = [k for k in layer if k not in DEFAULTS[kind]]
        if bad:
            raise ValueError("Layer %s has no %r. Params: %s" % (kind, bad[0], ", ".join(DEFAULTS[kind])))
        target = layer.get("target")
        if target is not None and target not in _TARGETS.get(kind, ()):
            raise ValueError("Layer %s target %r: use %s" % (kind, target, "/".join(_TARGETS.get(kind, ()))))
        if kind in ("triplanar", "image") and not layer.get("image"):
            raise ValueError("Layer %s needs image=<file in the scene textures>" % kind)
        out.append({"type": kind, **layer})
    return out


class _G:
    def __init__(self):
        self.nodes = []

    def add(self, kind, ins=None, props=None, out="#0"):
        nid = "n%d" % len(self.nodes)
        self.nodes.append({"id": nid, "type": kind, "props": props or {}, "in": ins or {}})
        return {"n": nid, "o": out}

    def mixc(self, fac, a, b):
        return self.add("ShaderNodeMix", {"#0": fac, "#6": a, "#7": b}, {"data_type": "RGBA"}, "#2")

    def mixf(self, fac, a, b):
        return self.add("ShaderNodeMix", {"#0": fac, "#2": a, "#3": b}, {"data_type": "FLOAT"}, "#0")

    def math(self, op, a, b=0.0, clamp=False):
        return self.add("ShaderNodeMath", {"#0": a, "#1": b}, {"operation": op, "use_clamp": clamp})

    def ramp(self, fac, lo, hi):
        return self.add("ShaderNodeValToRGB", {"#0": fac}, {"stops": [[lo, [0, 0, 0, 1]], [hi, [1, 1, 1, 1]]]})

    def coords(self, scale=1.0, stretch=None):
        src = self.add("ShaderNodeTexCoord", out="Object")
        s = [scale, scale * (stretch or 1.0), scale]
        return self.add("ShaderNodeMapping", {"Vector": src, "Scale": s}, out="Vector")

    def noise(self, scale, detail, stretch=None):
        return self.add("ShaderNodeTexNoise", {"Vector": self.coords(1.0, stretch), "Scale": scale, "Detail": detail}, out="Fac")


def _base(g, s, p):
    s["color"] = list(p["color"])[:3] + [1.0]
    s["metal"], s["rough"], s["alpha"] = p["metallic"], p["roughness"], p["opacity"]
    if p["emissive"] is not None:
        s["emit"], s["emit_str"] = list(p["emissive"])[:3] + [1.0], p["emissive_strength"]


def _noise(g, s, p):
    n = g.noise(p["scale"], p["detail"])
    if p["target"] == "color":
        fac = g.math("MULTIPLY", n, p["amount"])
        s["color"] = g.mixc(fac, s["color"], (p["color"] or [0, 0, 0]) + [1.0] * (4 - len(p["color"] or [0, 0, 0])))
    elif p["target"] == "roughness":
        s["rough"] = g.math("ADD", s["rough"], g.math("MULTIPLY", g.math("SUBTRACT", n, 0.5), p["amount"]), True)
    else:
        s["height"] = g.math("ADD", s["height"] if s["height"] else 0.0, g.math("MULTIPLY", n, p["amount"]))


def _edge_wear(g, s, p):
    point = g.add("ShaderNodeNewGeometry", out="Pointiness")  # Cycles-only, so wear is baked, not live
    mask = g.ramp(point, 0.5, 0.52 + 0.3 * (1.0 - p["width"]))
    breakup = g.ramp(g.noise(p["scale"], 6.0), 0.35, 0.65)
    fac = g.math("MULTIPLY", g.math("MULTIPLY", mask, breakup), p["amount"], True)
    s["color"] = g.mixc(fac, s["color"], list(p["color"])[:3] + [1.0])
    s["rough"] = g.mixf(fac, s["rough"], 0.45)
    if p["metal"]:
        s["metal"] = g.mixf(fac, s["metal"], 1.0)


def _dirt(g, s, p):
    ao = g.add("ShaderNodeAmbientOcclusion", {"Distance": p["distance"]}, {"samples": 8}, out="AO")
    cavity = g.ramp(g.math("SUBTRACT", 1.0, ao), 0.2, 0.8)
    breakup = g.ramp(g.noise(p["scale"], 5.0), 0.3, 0.7)
    fac = g.math("MULTIPLY", g.math("MULTIPLY", cavity, breakup), p["amount"], True)
    s["color"] = g.mixc(fac, s["color"], list(p["color"])[:3] + [1.0])
    s["rough"] = g.mixf(fac, s["rough"], 0.9)
    s["metal"] = g.mixf(fac, s["metal"], 0.0)


def _triplanar(g, s, p):
    image = g.add("ShaderNodeTexImage", {"Vector": g.coords(p["scale"])},
                  {"image": p["image"], "projection": "BOX", "projection_blend": p["blend"],
                   "noncolor": p["target"] != "color"}, "Color")
    if p["target"] == "color":
        s["color"] = image
    elif p["target"] == "roughness":
        s["rough"] = g.math("ADD", image, 0.0, True)
    else:
        s["height"] = g.math("ADD", s["height"] if s["height"] else 0.0, image)


def _scratches(g, s, p):
    n = g.noise(p["scale"], 3.0, p["stretch"])
    fac = g.math("MULTIPLY", g.ramp(n, 0.52, 0.56), p["amount"], True)
    s["color"] = g.mixc(fac, s["color"], list(p["color"])[:3] + [1.0])
    s["rough"] = g.mixf(fac, s["rough"], 0.25)
    s["height"] = g.math("ADD", s["height"] if s["height"] else 0.0, g.math("MULTIPLY", fac, -0.3))


def _sss(g, s, p):
    s["extra"].update({"Subsurface Weight": p["weight"], "Subsurface Radius": list(p["radius"])[:3],
                       "Subsurface Scale": p["scale"]})


def _clearcoat(g, s, p):
    s["extra"].update({"Coat Weight": p["weight"], "Coat Roughness": p["roughness"]})


def _normal(g, s, p):
    s["bump"] = p["bump"]
    if p["image"]:
        image = g.add("ShaderNodeTexImage", props={"image": p["image"], "noncolor": True}, out="Color")
        if p["dx"]:  # DirectX green points down; Blender wants OpenGL
            rgb = g.add("ShaderNodeSeparateColor", {"Color": image})
            green = g.math("SUBTRACT", 1.0, {"n": rgb["n"], "o": "#1"})
            image = g.add("ShaderNodeCombineColor", {"#0": rgb, "#1": green, "#2": {"n": rgb["n"], "o": "#2"}}, out="Color")
        s["normal"] = g.add("ShaderNodeNormalMap", {"Color": image, "Strength": p["strength"]}, {"space": "TANGENT"}, "Normal")


def _image(g, s, p):
    """A UV-mapped texture into one channel; orm splits G roughness, B metallic (R AO stays in the texture)."""
    image = g.add("ShaderNodeTexImage", props={"image": p["image"], "noncolor": p["target"] not in ("color", "emissive")}, out="Color")
    if p["target"] == "color":
        s["color"] = image
    elif p["target"] == "roughness":
        s["rough"] = image
    elif p["target"] == "metallic":
        s["metal"] = image
    elif p["target"] == "orm":
        rgb = g.add("ShaderNodeSeparateColor", {"Color": image})
        s["rough"], s["metal"] = {"n": rgb["n"], "o": "#1"}, {"n": rgb["n"], "o": "#2"}
    elif p["target"] == "emissive":
        s["emit"], s["emit_str"] = image, p["strength"]
    else:
        s["alpha"] = image


_APPLY = {"base": _base, "noise": _noise, "edge_wear": _edge_wear, "dirt": _dirt, "triplanar": _triplanar,
          "scratches": _scratches, "sss": _sss, "clearcoat": _clearcoat, "normal": _normal, "image": _image}


def graph(layers):
    """{"nodes": [...], "out": {Principled input: constant or ref}} for a layer list."""
    g = _G()
    s = {"color": [0.8, 0.8, 0.8, 1.0], "rough": 0.5, "metal": 0.0, "alpha": 1.0, "height": None, "normal": None,
         "bump": 0.3, "emit": None, "emit_str": 0.0, "extra": {}}
    for layer in normalize(layers):
        _APPLY[layer["type"]](g, s, {**DEFAULTS[layer["type"]], **layer})
    out = {"Base Color": s["color"], "Roughness": s["rough"], "Metallic": s["metal"], "Alpha": s["alpha"]}
    if s["emit"] is not None:
        out.update({"Emission Color": s["emit"], "Emission Strength": s["emit_str"]})
    normal = s["normal"]
    if s["height"] is not None:
        ins = {"Height": s["height"], "Strength": s["bump"], "Distance": 0.02}
        if normal:
            ins["Normal"] = normal
        normal = g.add("ShaderNodeBump", ins, out="Normal")
    if normal:
        out["Normal"] = normal
    out.update(s["extra"])
    return {"nodes": g.nodes, "out": out}


# ---- bpy side ----------------------------------------------------------------


def _key(k):
    return int(k[1:]) if isinstance(k, str) and k.startswith("#") else k


def _set(sock, value):
    cur = sock.default_value
    if hasattr(cur, "__len__"):
        values = list(value) if isinstance(value, (list, tuple)) else [value] * 3
        sock.default_value = (values + [1.0] * len(cur))[:len(cur)]
    else:
        sock.default_value = float(value)


def _prop(node, key, value, folder):
    import bpy
    if key == "stops":
        elements = node.color_ramp.elements
        while len(elements) < len(value):
            elements.new(1.0)
        for element, (pos, color) in zip(elements, value):
            element.position, element.color = pos, color
    elif key == "image":
        path = Path(folder or ".") / value
        if not path.is_file():
            raise ValueError("Image %r is not in %s; import it with forge_texture_import" % (value, folder))
        node.image = bpy.data.images.load(str(path), check_existing=True)
    elif key == "noncolor":
        if value and node.image:
            node.image.colorspace_settings.name = "Non-Color"
    else:
        setattr(node, key, value)


def build(mat, compiled, folder=None):
    """Replace mat's node tree with `compiled` (graph() output) feeding one Principled BSDF."""
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    made = {}

    def put(sock, value):
        if isinstance(value, dict):
            tree.links.new(made[value["n"]].outputs[_key(value["o"])], sock)
        else:
            _set(sock, value)

    for spec in compiled["nodes"]:
        node = tree.nodes.new(spec["type"])
        # image before noncolor: dict order is insertion order, image is added first
        for key, value in spec["props"].items():
            _prop(node, key, value, folder)
        made[spec["id"]] = node
        for key, value in spec["in"].items():
            put(node.inputs[_key(key)], value)
    for key, value in compiled["out"].items():
        put(bsdf.inputs[key], value)
    alpha = compiled["out"].get("Alpha", 1.0)
    if isinstance(alpha, dict) or float(alpha or 1.0) < 1.0:
        try:
            mat.surface_render_method = "BLENDED"
        except (AttributeError, TypeError):
            pass
    return len(tree.nodes)


def build_all(folder=None):
    """Build every bpy material whose Forge record carries a spec: {name: node count}."""
    import bpy
    built = {}
    for mat in bpy.data.materials:
        spec = json.loads(mat.get("forge") or "{}").get("spec")
        if spec:
            built[mat.name] = build(mat, graph(spec["layers"]), folder)
    return built


def op_material_check(p):
    """Compile the specs of the materials on the sent objects in Blender: node counts per material."""
    from . import bridge
    bridge.load(p["scene"])
    return {"built": build_all(p.get("textures_dir"))}


# ---- extra bake maps: ao, curvature, id, thickness, cage normal, miss % --------
# ponytail: beside bake.py's op_bake because that file is another lane; fold in when one owner has both.

EXTRA = {"ao": "AO", "curvature": "CRV", "id": "ID", "thickness": "TH", "normal": "N"}


def _hue(name):
    import colorsys
    import hashlib
    h = int(hashlib.sha1(name.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return tuple(colorsys.hsv_to_rgb(h, 0.8, 0.9)) + (1.0,)


def _maker(kind, diag):
    """fn(tree, material name) -> the colour socket the bake reads for one map kind."""
    def make(tree, name):
        if kind == "curvature":  # Pointiness: 0.5 flat, above convex, below concave
            geo = tree.nodes.new("ShaderNodeNewGeometry")
            rng = tree.nodes.new("ShaderNodeMapRange")
            rng.inputs["From Min"].default_value, rng.inputs["From Max"].default_value = 0.42, 0.58
            tree.links.new(geo.outputs["Pointiness"], rng.inputs["Value"])
            return rng.outputs["Result"]
        if kind == "thickness":  # AO cast inward: thin parts see their own back side; white = thick
            ao = tree.nodes.new("ShaderNodeAmbientOcclusion")
            ao.inside, ao.only_local, ao.samples = True, True, 16
            ao.inputs["Distance"].default_value = diag * 0.25
            return ao.outputs["AO"]
        rgb = tree.nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = _hue(name) if kind == "id" else (1.0, 1.0, 1.0, 1.0)
        return rgb.outputs[0]
    return make


def _out_node(tree):
    return next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)


@contextlib.contextmanager
def _route(mat, make):
    """mat emits make()'s colour instead of its surface, restored after."""
    tree = mat.node_tree
    before = {n.name for n in tree.nodes}
    out = _out_node(tree)
    old = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
    emit = tree.nodes.new("ShaderNodeEmission")
    tree.links.new(make(tree, mat.name), emit.inputs["Color"])
    tree.links.new(emit.outputs[0], out.inputs["Surface"])
    try:
        yield
    finally:
        for node in [n for n in tree.nodes if n.name not in before]:
            tree.nodes.remove(node)
        if old is not None:
            tree.links.new(old, out.inputs["Surface"])


@contextlib.contextmanager
def _swap(ob, make):
    """Every slot of ob (the high-poly) shows make()'s colour, keyed by its original material name."""
    import bpy
    me = ob.data
    keep = list(me.materials)
    temps = []
    for i in range(max(1, len(keep))):
        mat = bpy.data.materials.new("hq_src")
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()
        out, emit = tree.nodes.new("ShaderNodeOutputMaterial"), tree.nodes.new("ShaderNodeEmission")
        src = keep[i].name if i < len(keep) and keep[i] is not None else "slot%d" % i
        tree.links.new(make(tree, src), emit.inputs["Color"])
        tree.links.new(emit.outputs[0], out.inputs["Surface"])
        temps.append(mat)
    me.materials.clear()
    for mat in temps:
        me.materials.append(mat)
    try:
        yield
    finally:
        me.materials.clear()
        for mat in keep:
            me.materials.append(mat)
        for mat in temps:
            bpy.data.materials.remove(mat)


def _bake_px(ob, mats, size, kind, high, margin, **kw):
    """bake._bake with a margin: {material name: float (size*size, 4)}, images in Non-Color."""
    import bpy
    import numpy as np
    from .bake import _select
    images, nodes = {}, {}
    for mat in mats:
        img = bpy.data.images.new("hq_bake", size, size, alpha=False)
        img.colorspace_settings.name = "Non-Color"
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        mat.node_tree.nodes.active = tex
        images[mat.name], nodes[mat.name] = img, tex
    try:
        _select(*([high, ob] if high is not None else [ob]), active=ob)
        bpy.ops.object.bake(type=kind, margin=margin, margin_type="EXTEND", use_clear=True,
                            use_selected_to_active=high is not None, **kw)
        out = {}
        for name, img in images.items():
            px = np.empty(size * size * 4, np.float32)
            img.pixels.foreach_get(px)
            out[name] = px.reshape(-1, 4)
    finally:
        for mat in mats:
            mat.node_tree.nodes.remove(nodes[mat.name])
        for img in images.values():
            bpy.data.images.remove(img)
    return out


def op_bake_maps(p):
    """ao, curvature, id, thickness and a cage/ray normal, plus miss % (texels whose rays found no high-poly)."""
    import bpy
    from . import bridge
    from .bake import _device, _ensure_uv, _high, _safe, _save
    size, margin = int(p["size"]), int(p.get("margin", 16))
    objs, _ = bridge.load(p["scene"])
    build_all(p.get("textures_dir"))
    low = objs[p["id"]]
    mats = [m for m in low.data.materials if m is not None]
    if not mats:
        raise ValueError("%s has no material; run material first" % low.name)
    high = _high(p, objs)
    unwrapped = _ensure_uv(low)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    device = _device(size)
    diag = low.dimensions.length
    kw = {}
    if high is not None:
        kw = {"cage_extrusion": float(p.get("extrusion", 0.03)) * diag, "max_ray_distance": float(p.get("ray", 0)) * diag}
        if p.get("cage_id"):
            kw.update(use_cage=True, cage_object=objs[p["cage_id"]].name)

    def emit(kind, samples=1, edge=margin, src=high):
        scene.cycles.samples = samples
        make = _maker(kind, diag)
        with contextlib.ExitStack() as stack:
            if src is not None:
                stack.enter_context(_swap(src, make))
            else:
                for mat in mats:
                    stack.enter_context(_route(mat, make))
            return _bake_px(low, mats, size, "EMIT", src, edge, **(kw if src is not None else {}))

    baked = {}
    for kind in p.get("maps") or []:
        if kind not in EXTRA:
            raise ValueError("bake_maps has no %r. Maps: %s" % (kind, ", ".join(EXTRA)))
        scene.cycles.samples = 16
        if kind == "normal":
            baked[kind] = _bake_px(low, mats, size, "NORMAL", high, margin, normal_space="TANGENT", **kw)
            if p.get("target", "ue") in ("ue", "dx"):  # DirectX green points down
                for arr in baked[kind].values():
                    arr[:, 1] = 1.0 - arr[:, 1]
        elif kind == "ao":
            baked[kind] = _bake_px(low, mats, size, "AO", high, margin, **kw)
        else:
            baked[kind] = emit(kind, 16 if kind == "thickness" else 1)
    miss = None
    if high is not None:  # no margin: dilation would paint over the very holes we count
        mask = emit("mask", edge=0, src=None)
        hit = emit("mask", edge=0)
        inside = lost = 0
        for n in mask:
            m = (mask[n][:, 0] > 0.5).reshape(size, size)
            m[1:-1, 1:-1] &= m[:-2, 1:-1] & m[2:, 1:-1] & m[1:-1, :-2] & m[1:-1, 2:]  # island borders: half-covered texels
            m[[0, -1], :] = m[:, [0, -1]] = False
            inside += int(m.sum())
            lost += int((m & (hit[n][:, 0] < 0.5).reshape(size, size)).sum())
        miss = round(100.0 * lost / max(1, inside), 2)
    out_dir = Path(p["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    textures = {}
    for mat in mats:
        record = json.loads(mat.get("forge") or "{}")
        files = {}
        for kind, per in baked.items():
            fname = "T_%s_%s.png" % (_safe(record.get("name") or mat.name), EXTRA[kind])
            _save(out_dir / fname, per[mat.name], kind == "id")
            files[kind] = fname
        textures[record.get("id") or mat.name] = files
    if high is not None and not p.get("high_id"):
        bpy.data.objects.remove(high, do_unlink=True)  # a sidecar reference never comes back as a Forge object
    out = {"textures": textures, "size": size, "device": device, "unwrapped": unwrapped, "miss_pct": miss}
    if unwrapped:
        out["scene"] = bridge.dump(p["scene"])
    return out
