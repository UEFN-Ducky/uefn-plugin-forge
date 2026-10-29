"""Look-dev light presets rendered in Eevee: studio, product, dramatic, soft, neutral, outdoor (noon, golden, night).

Lights are placed relative to the camera (azimuth 0 = from the camera, + = camera left) and sized
to the object: soft suns whose angle matches an area light about the object's size, so the
energy needs no falloff tuning. Ground, framing and alpha cut-outs come from render.py.
"""
import math
from pathlib import Path

import bpy
from mathutils import Vector

from . import bridge, render
from .materials import build_all

# (name, azimuth deg, elevation deg, energy, softness deg); world (rgb, strength); floor grey
PRESETS = {
    "studio": {"lights": [("key", 45, 35, 2.2, 25), ("fill", -50, 15, 0.7, 40), ("rim", 160, 45, 1.4, 15)],
               "world": ((0.18, 0.18, 0.18), 0.4), "floor": 0.18},
    "product": {"lights": [("key", 35, 50, 2.0, 45), ("fill", -60, 20, 1.0, 60), ("rim", 180, 40, 1.0, 20)],
                "world": ((0.8, 0.8, 0.8), 0.6), "floor": 0.8},
    "dramatic": {"lights": [("key", 80, 25, 3.0, 6), ("fill", -80, 10, 0.15, 30), ("rim", 200, 30, 2.5, 8)],
                 "world": ((0.02, 0.02, 0.025), 0.1), "floor": 0.05},
    "soft": {"lights": [("key", 20, 60, 1.6, 60), ("fill", -40, 30, 0.9, 60)],
             "world": ((0.35, 0.35, 0.35), 0.7), "floor": 0.4},
    "neutral": {"lights": [], "world": ((1.0, 1.0, 1.0), 1.0), "floor": 0.5},
}
# outdoor: sun elevation, energy, colour; sky colour, strength; exposure
ENVS = {"noon": (60, 4.0, (1.0, 0.97, 0.92), (0.45, 0.6, 0.9), 1.0, 0.0),
        "golden": (8, 2.5, (1.0, 0.62, 0.35), (0.9, 0.55, 0.35), 0.5, 0.3),
        "night": (35, 0.35, (0.55, 0.65, 1.0), (0.02, 0.03, 0.08), 0.3, 1.5)}
TRANSFORMS = {"agx": "AgX", "neutral": "Khronos PBR Neutral", "standard": "Standard"}
QUALITY = {"preview": 16, "final": 64, "product": 128}


def _sun(name, direction, energy, soft, color=(1.0, 1.0, 1.0)):
    light = bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN"))
    light.data.energy = energy
    light.data.angle = math.radians(soft)
    light.data.color = color
    light.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(light)


def _dir(cam_dir, az, el):
    """Unit vector from the object toward a light at azimuth az (from the camera, + = left) and elevation el."""
    base = math.atan2(cam_dir.y, cam_dir.x) + math.radians(az)
    e = math.radians(el)
    return Vector((math.cos(base) * math.cos(e), math.sin(base) * math.cos(e), math.sin(e)))


def _world(rgb, strength):
    world = bpy.data.worlds.new("lookdev")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = tuple(rgb) + (1.0,)
    bg.inputs["Strength"].default_value = strength
    bpy.context.scene.world = world


def op_light(p):
    """Render the asked objects once under a preset: {path, preset}."""
    objs, _ = bridge.load(p["scene"])
    meshes = [o for o in objs.values() if o.type == "MESH"]
    if not meshes:
        raise ValueError("Nothing to light: the asked ids have no meshes")
    build_all(p.get("textures_dir"))  # layer specs (wear, dirt) show up, not just the base colour
    render._cutouts()
    lo, hi = render._bounds(meshes)
    center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 1e-4)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = QUALITY[p.get("quality", "preview")]
    scene.view_settings.view_transform = TRANSFORMS[p.get("transform", "agx")]
    scene.render.image_settings.file_format = "PNG"
    w, h = [int(v) for v in p.get("size") or (640, 480)]
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = w, h, 100
    render._ground(lo, hi, radius)
    d = Vector(render._DIRS.get(p.get("view", "three"), render._DIRS["three"])).normalized()
    env = p.get("env")
    if env:
        el, energy, color, sky, strength, exposure = ENVS[env]
        _world(sky, strength)
        _sun("sun", _dir(d, 60, el), energy, 3, color)
        scene.view_settings.exposure = exposure
        floor = 0.3
    else:
        preset = PRESETS[p.get("preset", "studio")]
        _world(*preset["world"])
        for name, az, el, energy, soft in preset["lights"]:
            _sun(name, _dir(d, az, el), energy, soft)
        floor = preset["floor"]
    bsdf = next(n for n in bpy.data.materials["ground"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (floor, floor, floor, 1.0)
    cam = bpy.data.objects.new("lookdev", bpy.data.cameras.new("lookdev"))
    cam.data.lens = 50
    scene.collection.objects.link(cam)
    scene.camera = cam
    half = math.atan(18.0 / 50.0)
    narrow = math.atan(math.tan(half) * min(w, h) / max(w, h))
    cam.location = center + d * (radius * 1.1 / math.sin(narrow))
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    dest = Path(p["out_dir"]) / ("light_%s.png" % (env or p.get("preset", "studio")))
    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(dest)
    bpy.ops.render.render(write_still=True)
    return {"path": str(dest)}
