"""Blender background script. Writes one Forge scene.json. Not imported by the plugin."""

import json
import math
import sys

import bpy

argv = sys.argv
out_path = argv[argv.index("--") + 1]
from pathlib import Path
source = Path(argv[argv.index("--") + 2]) if len(argv) > argv.index("--") + 2 else None
if source and source.suffix.lower() != ".blend":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if source.suffix.lower() == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(source))
    else:
        bpy.ops.import_scene.gltf(filepath=str(source))
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
    if mat.use_nodes and mat.node_tree:
        for shader in mat.node_tree.nodes:
            if shader.type != "BSDF_PRINCIPLED":
                continue
            socket = shader.inputs.get("Base Color")
            if socket and socket.is_linked:
                image_node = socket.links[0].from_node
                # Blender represents glTF texture × vertex color with a multiply node.
                if image_node.type in {"MIX", "MIX_RGB"} and image_node.blend_type == "MULTIPLY":
                    sources = [link.from_node for inp in image_node.inputs for link in inp.links]
                    images = [n for n in sources if n.type == "TEX_IMAGE"]
                    colors = [n for n in sources if n.type in {"VERTEX_COLOR", "ATTRIBUTE"}]
                    if len(images) == 1 and colors and len(images) + len(colors) == len(sources):
                        image_node = images[0]
                if image_node.type == "TEX_IMAGE" and image_node.image:
                    image = image_node.image
                    texture_dir = Path(out_path).parent / "textures"
                    texture_dir.mkdir(exist_ok=True)
                    target = texture_dir / (mid + "_base_color.png")
                    original_path, original_format = image.filepath_raw, image.file_format
                    try:
                        image.filepath_raw = str(target)
                        image.file_format = "PNG"
                        image.save()
                        materials[-1]["textures"]["base_color"] = target.name
                    finally:
                        image.filepath_raw, image.file_format = original_path, original_format
            break
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

def xform(obj) -> dict:
    # Local to the Blender parent so empty groups keep the real assembly.
    rel = obj.matrix_world if obj.parent is None or obj.parent.name not in ids else obj.parent.matrix_world.inverted_safe() @ obj.matrix_world
    loc, rotation, scl = rel.decompose()
    from mathutils import Matrix
    reconstructed = Matrix.LocRotScale(loc, rotation, scl)
    if any(abs(rel[r][c] - reconstructed[r][c]) > 1e-5 for r in range(3) for c in range(3)):
        raise ValueError(f"Unsupported sheared transform on {obj.name}; bake its transform before import")
    eul = rotation.to_euler("XYZ")
    return {
        "pos": [float(v) * cm for v in loc],
        "rot": [math.degrees(v) for v in eul],
        "scale": [float(v) for v in scl],
    }


def col_parent(obj) -> str | None:
    cols = [c.name for c in obj.users_collection if c.name in groups]
    return groups.get(cols[0]) if cols else None


keep = [obj for obj in scene.objects if obj.type == "EMPTY" or (obj.type == "MESH" and obj.data and obj.data.polygons)]
ids = {obj.name: (f"e{i}" if obj.type == "EMPTY" else f"o{i}") for i, obj in enumerate(keep)}
# Keep authored collision/LODs available, but do not overlay them on the primary mesh.
import re
hidden_helpers = set()
for obj in keep:
    match = re.match(r"^(.*)_LOD([1-9][0-9]*)$", obj.name, re.I)
    if obj.type == "MESH" and (obj.name.upper().startswith(("UCX_", "UBX_", "USP_", "UCP_")) or (match and match[1] + "_LOD0" in ids)):
        hidden_helpers.add(obj.name)


def parent_id(obj) -> str | None:
    if obj.parent is not None and obj.parent.name in ids:
        return ids[obj.parent.name]
    return col_parent(obj)


for obj in keep:
    if obj.type != "EMPTY":
        continue
    cols = [c.name for c in obj.users_collection if c.name in groups]
    nodes.append({
        "id": ids[obj.name], "type": "group", "name": obj.name, "parent": parent_id(obj),
        "layer": top_layer(cols[0]) if cols else "default", "visible": not obj.hide_get() and obj.name not in hidden_helpers, "locked": False,
        "transform": xform(obj), "combine": True,
    })

depsgraph = None
protos = {}
prototypes = []
objects = 0
for obj in keep:
    if obj.type != "MESH":
        continue
    key = obj.name + ":eval" if obj.modifiers else obj.data.name + ":" + str([s.material.name if s.material else "" for s in obj.material_slots])
    proto = protos.get(key)
    if proto is None:
        me = obj.data
        if obj.modifiers:
            if depsgraph is None:
                depsgraph = bpy.context.evaluated_depsgraph_get()
            me = obj.evaluated_get(depsgraph).to_mesh()
        faces = [list(poly.vertices) for poly in me.polygons if len(poly.vertices) >= 3]
        if not faces:
            if obj.modifiers and depsgraph is not None:
                obj.evaluated_get(depsgraph).to_mesh_clear()
            continue
        # Split only where corner normals or colors differ; retain authored hard edges.
        verts, faces, normals, colors = [], [], [], []
        color_layer = me.color_attributes.active_color if me.color_attributes else None
        corner_map = {}
        for polygon in me.polygons:
            if len(polygon.vertices) < 3:
                continue
            face = []
            for li in polygon.loop_indices:
                vi = me.loops[li].vertex_index
                normal = tuple(float(v) for v in me.corner_normals[li].vector)
                color = tuple(float(v) for v in color_layer.data[li if color_layer.domain == "CORNER" else vi].color) if color_layer else (1, 1, 1, 1)
                key_corner = (vi, normal, color)
                if key_corner not in corner_map:
                    corner_map[key_corner] = len(verts)
                    verts.append([float(v) * cm for v in me.vertices[vi].co])
                    normals.append(list(normal))
                    colors.append(list(color))
                face.append(corner_map[key_corner])
            faces.append(face)
        pid = f"p{len(prototypes)}"
        proto = pid
        protos[key] = pid
        prototypes.append({
            "id": pid, "kind": "mesh", "params": {}, "key": key,
            "mesh": {
                "verts": verts, "faces": faces,
                "face_mat": [material_id(obj.material_slots[p.material_index].material if p.material_index < len(obj.material_slots) else None) for p in me.polygons if len(p.vertices) >= 3],
                "uvs": [[[float(me.uv_layers.active.data[i].uv.x), float(me.uv_layers.active.data[i].uv.y)] for i in p.loop_indices] for p in me.polygons if len(p.vertices) >= 3] if me.uv_layers.active else [],
                "smooth": True,
                "attrs": {"nrm:source": normals, **({"col:color": colors} if color_layer else {})},
            },
        })
        if obj.modifiers and depsgraph is not None:
            obj.evaluated_get(depsgraph).to_mesh_clear()
    cols = [c.name for c in obj.users_collection if c.name in groups]
    slot = obj.material_slots[0].material if obj.material_slots else None
    nodes.append({
        "id": ids[obj.name], "type": "object", "name": obj.name, "parent": parent_id(obj),
        "layer": top_layer(cols[0]) if cols else "default", "visible": not obj.hide_get() and obj.name not in hidden_helpers, "locked": False,
        "transform": xform(obj),
        "prototype": proto, "modifiers": [], "material": material_id(slot),
    })
    objects += 1

data = {
    "name": bpy.path.display_name_from_filepath(bpy.data.filepath) or "Blend",
    "rev": 1, "mode": "object", "select_mode": "face",
    "selection": {"object": None, "verts": [], "edges": [], "faces": []},
    "layers": layers, "materials": materials, "prototypes": prototypes, "nodes": nodes,
    "geom": 1, "activity": [],
    "import_warnings": ["Static snapshot: rigs, animation, shape keys and procedural shaders are not transferred; unsupported texture channels use material factors."] + ([f"{len(hidden_helpers)} collision or alternate LOD meshes retained but hidden."] if hidden_helpers else []),
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, separators=(",", ":"))
print("FORGE_EXPORT", objects, len(prototypes), out_path)
