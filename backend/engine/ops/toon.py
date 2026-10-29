"""Toon look for Eevee renders: hull-outline materials cull their front side, cel ramp over the lit colour, Freestyle lines.

Called by ops/render.py. A material is toon when its forge dict has `toon` ({bands, outline}); the outline hull
(hq/toon.py) has `toon_outline`. Both ship in the material JSON the bridge stores on every bpy material.
"""
import json

import bpy

_FLOOR = 0.55  # darkest band, so shadows stay readable on a golden fur


def _forge(mat):
    try:
        return json.loads(mat.get("forge") or "{}")
    except ValueError:
        return {}


def _ink(color):
    """The outline colour as the screen shows it: squared, since the linear brown 0.3/0.13/0.05 reads pale in sRGB."""
    return tuple(c * c for c in color[:3]) + (1.0,)


def flat_world():
    """Plain light-grey backdrop instead of the studio sky, so lines and bands read against it."""
    bg = next(n for n in bpy.context.scene.world.node_tree.nodes if n.type == "BACKGROUND")
    for link in list(bg.inputs["Color"].links):
        bpy.context.scene.world.node_tree.links.remove(link)
    bg.inputs["Color"].default_value = (0.8, 0.8, 0.82, 1)
    bg.inputs["Strength"].default_value = 1.0


def _ramp(tree, bands):
    node = tree.nodes.new("ShaderNodeValToRGB")
    ramp = node.color_ramp
    ramp.interpolation = "CONSTANT"
    while len(ramp.elements) < bands:
        ramp.elements.new(0.5)
    for i, e in enumerate(ramp.elements):
        e.position = i / bands
        v = _FLOOR + (1 - _FLOOR) * (i / max(1, bands - 1))
        e.color = (v, v, v, 1)
    return node


def _cel(mat, bands):
    tree = mat.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    out = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if bsdf is None or out is None:
        return
    base = bsdf.inputs["Base Color"]
    if base.is_linked:  # painted texture keeps showing through the bands
        source = base.links[0].from_socket
    else:
        rgb = tree.nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = base.default_value
        source = rgb.outputs[0]
    lit = tree.nodes.new("ShaderNodeBsdfDiffuse")
    to_rgb = tree.nodes.new("ShaderNodeShaderToRGB")
    ramp = _ramp(tree, bands)
    gain = tree.nodes.new("ShaderNodeMath")
    gain.operation = "MULTIPLY"
    gain.inputs[1].default_value = 1.6  # sun strengths in the rig land well under 1 on a diffuse
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
    mix.inputs[0].default_value = 1.0
    emit = tree.nodes.new("ShaderNodeEmission")
    links = tree.links
    links.new(lit.outputs[0], to_rgb.inputs[0])
    sep = tree.nodes.new("ShaderNodeSeparateColor")
    links.new(to_rgb.outputs[0], sep.inputs[0])
    links.new(sep.outputs[0], gain.inputs[0])
    links.new(gain.outputs[0], ramp.inputs[0])
    links.new(source, mix.inputs[6])
    links.new(ramp.outputs[0], mix.inputs[7])
    links.new(mix.outputs[2], emit.inputs[0])
    links.new(emit.outputs[0], out.inputs["Surface"])


def _outline(mat):
    tree = mat.node_tree
    out = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if out is None or bsdf is None:
        return
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.inputs[0].default_value = _ink(bsdf.inputs["Base Color"].default_value)
    tree.links.new(emit.outputs[0], out.inputs["Surface"])
    mat.use_backface_culling = True  # the hull's flipped faces: only the rim behind the silhouette survives


def apply(p):
    """Outline hulls always cull; style=toon also cels the other materials and turns on Freestyle."""
    toon = p.get("style") == "toon"
    for mat in list(bpy.data.materials):
        forge = _forge(mat)
        if forge.get("toon_outline"):
            _outline(mat)
        elif toon and mat.use_nodes and mat.node_tree:
            _cel(mat, int((forge.get("toon") or {}).get("bands") or p.get("bands") or 3))
    if not toon:
        return
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    layer = bpy.context.view_layer
    layer.use_freestyle = True
    settings = layer.freestyle_settings
    for old in list(settings.linesets):
        settings.linesets.remove(old)
    lines = settings.linesets.new("toon")
    lines.select_silhouette = lines.select_border = lines.select_crease = True
    style = bpy.data.linestyles.new("toon")
    style.color = _ink(p.get("outline_color") or (0.3, 0.13, 0.05))[:3]
    style.thickness = float(p.get("line_px") or 2.5)
    lines.linestyle = style
