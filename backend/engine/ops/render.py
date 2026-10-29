"""Eevee look renders: the asked objects, a 3-point light rig, a soft-shadow ground and a neutral world.

Hair cards and fur shells are ordinary mesh faces here, so they render with their alpha textures.
The host (backend/render.py) tiles the per-view PNGs this op writes. Forge units in, Forge views out.
"""
import math
from pathlib import Path

import bpy
from mathutils import Vector

from . import bridge, toon

_DIRS = {"three": (1.3, -1.6, 0.9), "front": (0.0, -2.2, 0.45), "top": (0.0, -0.01, 2.4), "side": (2.2, 0.0, 0.45)}
_QUALITY = {"look": 16, "final": 64}


def _bounds(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def _cutouts():
    """Textures that carry alpha (hair cards, leaves) drive the material's Alpha."""
    for mat in bpy.data.materials:
        tree = mat.node_tree
        bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if tree else None
        for node in (tree.nodes if tree else []):
            if bsdf and node.type == "TEX_IMAGE" and node.label == "base_color" and node.image and node.image.depth == 32:
                tree.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])


def _ground(lo, hi, radius):
    c = (lo + hi) / 2
    s = radius * 3
    me = bpy.data.meshes.new("ground")
    me.from_pydata([(c.x - s, c.y - s, lo.z), (c.x + s, c.y - s, lo.z), (c.x + s, c.y + s, lo.z), (c.x - s, c.y + s, lo.z)], [], [(0, 1, 2, 3)])
    mat = bpy.data.materials.new("ground")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (0.42, 0.42, 0.44, 1)
    bsdf.inputs["Roughness"].default_value = 0.95
    me.materials.append(mat)
    obj = bpy.data.objects.new("ground", me)
    bpy.context.scene.collection.objects.link(obj)


def _world():
    world = bpy.data.worlds.new("look")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    sky = world.node_tree.nodes.new("ShaderNodeTexSky")  # soft studio-like sky, no HDRI file needed
    sky.sun_disc = False
    world.node_tree.links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.35
    bpy.context.scene.world = world


def _rig(center, radius, cam_dir, right, up):
    """Key over the camera's left shoulder, fill from the right, rim from behind."""
    forward = -cam_dir
    # Oct 28 2026: 3.5 + 1.2 + 2.5 under the Standard view transform clipped a golden lion to near white.
    for name, vec, energy in (("key", up * 0.8 - forward - right * 0.4, 2.0), ("fill", right * 0.9 - forward * 0.6, 0.55),
                              ("rim", forward * 0.8 + up * 0.7 - right * 0.3, 1.0)):
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN"))
        light.data.energy = energy
        light.data.angle = math.radians(12)  # wide sun = soft shadows
        light.rotation_euler = (-vec).to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.collection.objects.link(light)


def op_render(p):
    quality = p.get("quality", "look")
    if quality not in _QUALITY:
        raise ValueError("quality=%r is not a render quality. Use one of: %s" % (quality, ", ".join(_QUALITY)))
    objs, _arms = bridge.load(p["scene"])
    meshes = [o for o in objs.values() if o.type == "MESH"]
    if not meshes:
        raise ValueError("Nothing to render: the asked ids have no meshes")
    _cutouts()
    toon.apply(p)
    lo, hi = _bounds(meshes)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = _QUALITY[quality]
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    w, h = [int(v) for v in p["size"]]
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = w, h, 100
    _world()
    if p.get("style") == "toon":
        toon.flat_world()
    if p.get("style") != "toon":  # a toon render is lines and bands on a flat backdrop, no floor to outline
        _ground(lo, hi, radius)
    cam_data = bpy.data.cameras.new("look")
    cam_data.lens = 50
    cam_data.sensor_fit = "AUTO"
    cam = bpy.data.objects.new("look", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    half = math.atan(18.0 / 50.0)  # sensor 36 mm fits the long side
    narrow = math.atan(math.tan(half) * min(w, h) / max(w, h))  # the short side limits the fit
    dist = radius * 1.1 / math.sin(narrow)
    out_dir = Path(p["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for view in p.get("views") or ["three"]:
        d = Vector(_DIRS.get(view, _DIRS["three"])).normalized()
        cam.location = center + d * dist
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        mw = cam.matrix_world.to_3x3()
        right, up = mw @ Vector((1, 0, 0)), mw @ Vector((0, 1, 0))
        for o in [o for o in bpy.data.objects if o.type == "LIGHT"]:
            bpy.data.objects.remove(o)
        _rig(center, radius, d, right, up)
        dest = out_dir / ("%s_%s.png" % (p.get("stem", "look"), view))
        scene.render.filepath = str(dest)
        bpy.ops.render.render(write_still=True)
        paths.append(str(dest))
    return {"paths": paths}
