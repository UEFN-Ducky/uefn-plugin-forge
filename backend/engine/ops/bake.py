"""Bake a material to engine-ready PNGs: BaseColor, Normal, ORM (R AO, G rough, B metal), Emissive, Opacity.

Value maps (colour, roughness, metallic, emission, alpha) are baked by routing the BSDF input
through an Emission shader, so they are the material's raw values, never lit. Normal and AO
come from the high-poly when one is given (a sidecar .blend or another object).
"""
import contextlib
import json
import math
import re
from pathlib import Path

SUFFIX = {"base_color": "BC", "normal": "N", "orm": "ORM", "emissive": "E", "opacity": "O"}
_SRGB = ("base_color", "emissive")


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:48] or "Mat"


_GPU = []  # [backend or "CPU"] once probed: the probe and the GPU kernel load cost seconds, so do them once
_GPU_MIN = 1024  # below this the GPU's start-up costs more than the bake


def _device(size):
    """Cycles on the first GPU backend present for big bakes, else CPU. Returns what it used."""
    import bpy
    scene = bpy.context.scene
    if size < _GPU_MIN:
        scene.cycles.device = "CPU"
        return "CPU"
    if not _GPU:
        _GPU.append(_probe(scene))
    scene.cycles.device = "CPU" if _GPU[0] == "CPU" else "GPU"
    return _GPU[0]


def _probe(scene):
    import bpy
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = kind
            except TypeError:
                continue
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type == kind]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type == kind
                scene.cycles.device = "GPU"
                return kind
        prefs.compute_device_type = "NONE"
    except Exception:  # no cycles prefs in this build: CPU is always there
        pass
    scene.cycles.device = "CPU"
    return "CPU"


def _select(*objects, active):
    import bpy
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active


def _ensure_uv(ob):
    """Smart-unwrap when the object has no real UVs (all zero). True when it did."""
    import bpy
    import numpy as np
    me = ob.data
    if me.uv_layers:
        uv = np.empty(len(me.loops) * 2, np.float32)
        me.uv_layers.active.data.foreach_get("uv", uv)
        if len(uv) and float(np.ptp(uv)) > 1e-6:
            return False
    else:
        me.uv_layers.new(name="UVMap")
    _select(ob, active=ob)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    return True


def _bsdf(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


@contextlib.contextmanager
def _emit(mat, socket):
    """Route one Principled input to the material output through an Emission shader."""
    tree = mat.node_tree
    bsdf = _bsdf(mat)
    out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    sock = bsdf.inputs[socket]
    emit = tree.nodes.new("ShaderNodeEmission")
    if sock.is_linked:
        tree.links.new(sock.links[0].from_socket, emit.inputs["Color"])
    else:
        value = sock.default_value
        emit.inputs["Color"].default_value = tuple(value)[:3] + (1.0,) if hasattr(value, "__len__") else (value, value, value, 1.0)
    if socket == "Emission Color":
        emit.inputs["Strength"].default_value = bsdf.inputs["Emission Strength"].default_value
    tree.links.new(emit.outputs[0], out.inputs["Surface"])
    try:
        yield
    finally:
        tree.nodes.remove(emit)
        tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])


def _bake(ob, mats, size, kind, noncolor, high=None, **kw):
    """One Cycles bake into a fresh image per material: {material name: float array (size*size, 4)}."""
    import bpy
    import numpy as np
    images, nodes = {}, {}
    for mat in mats:
        img = bpy.data.images.new("hq_bake", size, size, alpha=False)
        img.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        mat.node_tree.nodes.active = tex
        images[mat.name], nodes[mat.name] = img, tex
    try:
        if high is not None:
            _select(high, ob, active=ob)
        else:
            _select(ob, active=ob)
        bpy.ops.object.bake(type=kind, margin=8, margin_type="EXTEND", use_clear=True,
                            use_selected_to_active=high is not None, **kw)
        out = {}
        for name, img in images.items():
            pixels = np.empty(size * size * 4, np.float32)
            img.pixels.foreach_get(pixels)
            out[name] = pixels.reshape(-1, 4)
    finally:
        for mat in mats:
            mat.node_tree.nodes.remove(nodes[mat.name])
        for img in images.values():
            bpy.data.images.remove(img)
    return out


def _bake_input(ob, mats, size, socket, noncolor):
    with contextlib.ExitStack() as stack:
        for mat in mats:
            stack.enter_context(_emit(mat, socket))
        return _bake(ob, mats, size, "EMIT", noncolor)


def _save(path, rgba, srgb):
    import bpy
    import numpy as np
    size = int(round(len(rgba) ** 0.5))
    rgba = np.array(rgba, np.float32)
    rgba[:, 3] = 1.0
    img = bpy.data.images.new("hq_out", size, size, alpha=False)
    img.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
    img.pixels.foreach_set(np.clip(rgba, 0.0, 1.0).ravel())
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _high(p, objs):
    """The high-poly bpy object (linked into the scene) or None."""
    import bpy
    if p.get("high_id"):
        return objs[p["high_id"]]
    if not p.get("high_path"):
        return None
    with bpy.data.libraries.load(str(p["high_path"]), link=False) as (src, dst):
        dst.objects = list(src.objects)
    found = [o for o in dst.objects if o is not None and o.type == "MESH"]
    if not found:
        raise ValueError("%s holds no mesh to bake from" % p["high_path"])
    high = max(found, key=lambda o: len(o.data.polygons))
    bpy.context.scene.collection.objects.link(high)
    return high


def _wanted(forge, maps):
    if maps:
        return list(maps)
    out = ["base_color", "normal", "orm"]
    if forge.get("emissive_strength") and any(forge.get("emissive") or []):
        out.append("emissive")
    if float(forge.get("opacity", 1.0)) < 1.0:
        out.append("opacity")
    return out


def op_bake(p):
    import bpy
    import numpy as np
    from . import bridge
    from .materials import build_all
    size = int(p.get("size", 1024))
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
    forge = {m.name: json.loads(m.get("forge") or "{}") for m in mats}
    # one sample is exact for value maps and normals; only AO nodes inside a dirt layer need a few
    dirty = any(l.get("type") == "dirt" for f in forge.values() for l in (f.get("spec") or {}).get("layers", []))
    samples = 8 if dirty else 1
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    device = _device(size)
    wanted = {name: _wanted(f, p.get("maps")) for name, f in forge.items()}
    need = set().union(*wanted.values())
    out_dir = Path(p["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    cage = max(low.dimensions) * 0.05
    baked = {}
    if "base_color" in need:
        baked["base_color"] = _bake_input(low, mats, size, "Base Color", False)
    if "normal" in need:
        scene.cycles.samples = max(samples, 16)  # fewer samples jitter the island edges; flat faces are still instant
        baked["normal"] = _bake(low, mats, size, "NORMAL", True, high, normal_space="TANGENT",
                                cage_extrusion=cage, max_ray_distance=0.0)
        if p.get("target", "ue") in ("ue", "dx"):  # DirectX green points down
            for arr in baked["normal"].values():
                arr[:, 1] = 1.0 - arr[:, 1]
    if "orm" in need:
        scene.cycles.samples = 16
        ao = _bake(low, mats, size, "AO", True, high, cage_extrusion=cage, max_ray_distance=0.0) if high is not None \
            else _bake(low, mats, size, "AO", True)
        scene.cycles.samples = samples
        rough = _bake_input(low, mats, size, "Roughness", True)
        metal = _bake_input(low, mats, size, "Metallic", True)
        baked["orm"] = {}
        for name in ao:
            orm = np.ones_like(ao[name])
            orm[:, 0], orm[:, 1], orm[:, 2] = ao[name][:, 0], rough[name][:, 0], metal[name][:, 0]
            baked["orm"][name] = orm
    if "emissive" in need:
        baked["emissive"] = _bake_input(low, mats, size, "Emission Color", False)
    if "opacity" in need:
        baked["opacity"] = {n: np.repeat(a[:, :1], 4, axis=1) for n, a in _bake_input(low, mats, size, "Alpha", True).items()}
    textures = {}
    for mat in mats:
        record = forge[mat.name]
        mid = record.get("id") or mat.name
        files = {}
        for slot in wanted[mat.name]:
            if slot not in baked:
                raise ValueError("No map %r. Maps: %s" % (slot, ", ".join(SUFFIX)))
            fname = "T_%s_%s.png" % (_safe(record.get("name") or mat.name), SUFFIX[slot])
            _save(out_dir / fname, baked[slot][mat.name], slot in _SRGB)
            files[slot] = fname
        textures[mid] = files
    if high is not None:
        bpy.data.objects.remove(high, do_unlink=True)  # reference only; never comes back as a Forge object
    return {"textures": textures, "size": size, "device": device, "unwrapped": unwrapped,
            "scene": bridge.dump(p["scene"])}
