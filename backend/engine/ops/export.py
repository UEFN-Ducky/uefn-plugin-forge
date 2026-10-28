"""Engine side of export: write FBX / glTF / Alembic from the loaded Forge scene. Runs in the bpy worker."""
from pathlib import Path

import bpy

from . import bridge


def _counts():
    # the glTF importer parks bone-shape helper meshes in glTF_not_exported
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and not any(c.name == "glTF_not_exported" for c in o.users_collection)]
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    skins = sum(1 for o in meshes if any(m.type == "ARMATURE" and m.object for m in o.modifiers))
    morphs = sum(max(0, len(o.data.shape_keys.key_blocks) - 1) for o in meshes if o.data.shape_keys)
    tris = sum(sum(max(0, len(p.vertices) - 2) for p in o.data.polygons) for o in meshes)
    return {"skins": skins, "clips": len([a for a in bpy.data.actions if a.get("forge_rig")]), "morphs": morphs,
            "tris": tris, "bones": sum(len(a.data.bones) for a in arms), "meshes": len(meshes)}


def _select(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _fbx(path, cfg):
    # NLA-less: every action becomes a take, so clips ship without strips to build.
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True, object_types={"MESH", "ARMATURE", "EMPTY"},
        axis_forward=cfg["axis"][0], axis_up=cfg["axis"][1], apply_scale_options=cfg["scale"],
        bake_space_transform=cfg["space"], use_mesh_modifiers=True, mesh_smooth_type="FACE",
        use_tspace=cfg["tangents"], add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X",
        bake_anim=True, bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False,
        bake_anim_use_all_bones=True, bake_anim_force_startend_keying=True, path_mode="COPY", embed_textures=False)


def _gltf(path, cfg):
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB" if cfg["format"] == "glb" else "GLTF_SEPARATE", use_selection=True,
        export_yup=True, export_apply=True, export_tangents=cfg["tangents"], export_skins=True,
        export_morph=True, export_morph_normal=True, export_animations=True, export_animation_mode="ACTIONS",
        export_materials="EXPORT", export_image_format="AUTO", export_normals=True)


def _abc(path, cfg):
    bpy.ops.wm.alembic_export(filepath=path, selected=True, export_hair=True, export_particles=True,
                              uvs=True, normals=True, curves_as_mesh=False, flatten=False,
                              start=int(bpy.context.scene.frame_start), end=int(bpy.context.scene.frame_end))


def _reimport(path):
    """Fresh scene, import the file back: counts that must match what was exported."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = Path(path).suffix.lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        bpy.ops.wm.alembic_import(filepath=path)
    return _counts()


def op_export(p):
    bridge.load(p["scene"])
    have = _counts()
    objs = [o for o in bpy.data.objects if o.type in ("MESH", "ARMATURE", "CURVES")]
    if not objs:
        raise ValueError("Nothing to export. Pass id of an object, or add one first")
    if all(o.type == "CURVES" for o in objs):
        p = dict(p, format="abc", path=str(Path(p["path"]).with_suffix(".abc")))
    elif p["format"] == "abc":
        objs = [o for o in objs if o.type == "CURVES"] or objs  # Alembic carries grooms and static meshes
    path = p["path"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _select(objs)
    {"fbx": _fbx, "glb": _gltf, "gltf": _gltf, "abc": _abc}[p["format"]](path, p)
    out = dict(have, path=path, format=p["format"], normal=p["normal"])
    if p.get("verify") and p["format"] != "abc":
        got = _reimport(path)
        bad = [k for k in ("meshes", "skins", "morphs", "bones") if got[k] != have[k]]
        out["verified"] = not bad
        if bad:
            out["warnings"] = ["reimport differs: " + ", ".join("%s %s->%s" % (k, have[k], got[k]) for k in bad)]
    return out
