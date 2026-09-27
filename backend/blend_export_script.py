"""Blender background script. Writes one Forge scene.json. Not imported by the plugin."""

import json
import math
import sys

import bpy

argv = sys.argv
out_path = argv[argv.index("--") + 1]
scene = bpy.context.scene
cm = float(scene.unit_settings.scale_length) * 100.0

parent_of = {}
for col in bpy.data.collections:
    for child in col.children:
        parent_of[child.name] = col.name

top = [c.name for c in scene.collection.children]
palette = ["#8aa", "#e85", "#6c8cff", "#7dba6a", "#d4a017", "#c46ad4", "#5ec8c8", "#d27b4a"]
layers = [{"id": "default", "name": "Default", "visible": True, "locked": False, "color": "#8aa"}]
layer_of = {}
for i, name in enumerate(top):
    lid = f"l{i}"
    layers.append({"id": lid, "name": name, "visible": True, "locked": False, "color": palette[i % len(palette)]})
    layer_of[name] = lid


def top_layer(name: str) -> str:
    seen = set()
    cur = name
    while cur in parent_of and cur not in seen:
        seen.add(cur)
        cur = parent_of[cur]
    return layer_of.get(cur, "default")


materials = []
mat_id = {}


def material_id(mat) -> str:
    if mat is None:
        return materials[0]["id"]
    hit = mat_id.get(mat.name)
    if hit:
        return hit
    color = [0.75, 0.75, 0.78, 1.0]
    metal = 0.0
    rough = 0.5
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            base = node.inputs.get("Base Color")
            if base is not None:
                c = base.default_value
                color = [round(float(c[0]), 4), round(float(c[1]), 4), round(float(c[2]), 4), round(float(c[3]), 4)]
            met = node.inputs.get("Metallic")
            rg = node.inputs.get("Roughness")
            if met is not None:
                metal = round(float(met.default_value), 4)
            if rg is not None:
                rough = round(float(rg.default_value), 4)
            break
    mid = f"m{len(materials)}"
    mat_id[mat.name] = mid
    materials.append({
        "id": mid, "name": mat.name, "base_color": color, "metallic": metal, "roughness": rough,
        "emissive": [0, 0, 0], "emissive_strength": 0.0, "opacity": 1.0, "textures": {},
    })
    return mid


materials.append({
    "id": "m0", "name": "Default", "base_color": [0.75, 0.75, 0.78, 1],
    "metallic": 0.0, "roughness": 0.5, "emissive": [0, 0, 0], "emissive_strength": 0.0,
    "opacity": 1.0, "textures": {},
})

groups = {col.name: f"g{i}" for i, col in enumerate(bpy.data.collections)}
nodes = []
for col in bpy.data.collections:
    gid = groups[col.name]
    parent = groups.get(parent_of.get(col.name, "")) or None
    nodes.append({
        "id": gid, "type": "group", "name": col.name, "parent": parent,
        "layer": top_layer(col.name), "visible": not col.hide_viewport, "locked": False,
        "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
        "combine": True,
    })

protos = {}
prototypes = []
objects = 0
for obj in bpy.data.objects:
    if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
        continue
    key = obj.data.name
    proto = protos.get(key)
    if proto is None:
        me = obj.data
        verts = [[round(v.co.x * cm, 2), round(v.co.y * cm, 2), round(v.co.z * cm, 2)] for v in me.vertices]
        faces = [list(poly.vertices) for poly in me.polygons if len(poly.vertices) >= 3]
        if not faces:
            continue
        pid = f"p{len(prototypes)}"
        proto = pid
        protos[key] = pid
        prototypes.append({
            "id": pid, "kind": "mesh", "params": {}, "key": key,
            "mesh": {"verts": verts, "faces": faces, "face_mat": [], "uvs": [], "smooth": False},
        })
    cols = [c.name for c in obj.users_collection if c.name in groups]
    parent = groups.get(cols[0], "") if cols else None
    layer = top_layer(cols[0]) if cols else "default"
    slot = obj.material_slots[0].material if obj.material_slots else None
    loc = obj.matrix_world.to_translation()
    eul = obj.matrix_world.to_euler("XYZ")
    scl = obj.matrix_world.to_scale()
    nodes.append({
        "id": f"o{objects}", "type": "object", "name": obj.name, "parent": parent or None,
        "layer": layer, "visible": not obj.hide_get(), "locked": False,
        "transform": {
            "pos": [round(loc.x * cm, 2), round(loc.y * cm, 2), round(loc.z * cm, 2)],
            "rot": [round(math.degrees(eul.x), 2), round(math.degrees(eul.y), 2), round(math.degrees(eul.z), 2)],
            "scale": [round(scl.x, 4), round(scl.y, 4), round(scl.z, 4)],
        },
        "prototype": proto, "modifiers": [], "material": material_id(slot),
    })
    objects += 1

data = {
    "name": bpy.path.display_name_from_filepath(bpy.data.filepath) or "Blend",
    "rev": 1, "mode": "object", "select_mode": "face",
    "selection": {"object": None, "verts": [], "edges": [], "faces": []},
    "layers": layers, "materials": materials, "prototypes": prototypes, "nodes": nodes,
    "geom": 1, "activity": [],
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, separators=(",", ":"))
print("FORGE_EXPORT", objects, len(prototypes), out_path)
