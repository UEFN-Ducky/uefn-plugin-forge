"""Engine side of export: write FBX / glTF / OBJ / STL / USDZ / Alembic from the loaded Forge scene. Runs in the bpy worker."""
from pathlib import Path

import bpy

from . import bridge


def _counts():
    # the glTF importer parks bone-shape helper meshes in glTF_not_exported
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and not any(c.name == "glTF_not_exported" for c in o.users_collection)
              and not o.name.startswith("UCX_") and "-colonly" not in o.name]
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    skins = sum(1 for o in meshes if any(m.type == "ARMATURE" and m.object for m in o.modifiers))
    morphs = sum(max(0, len(o.data.shape_keys.key_blocks) - 1) for o in meshes if o.data.shape_keys)
    tris = sum(sum(max(0, len(p.vertices) - 2) for p in o.data.polygons) for o in meshes)
    return {"skins": skins, "clips": len([a for a in bpy.data.actions if a.get("forge_rig")]), "morphs": morphs,
            "tris": tris, "bones": sum(len(a.data.bones) for a in arms), "meshes": len(meshes)}


def _size(objs):
    """World bounding-box size in metres of these meshes."""
    pts = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    return [max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)] if pts else [0.0, 0.0, 0.0]


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
        use_tspace=cfg["tangents"], use_triangles=bool(cfg.get("triangulate")), add_leaf_bones=False,
        primary_bone_axis="Y", secondary_bone_axis="X",
        bake_anim=True, bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False,
        bake_anim_use_all_bones=True, bake_anim_force_startend_keying=True, path_mode="COPY", embed_textures=False)


def _gltf(path, cfg):
    extra = {"export_image_format": "WEBP", "export_image_quality": int(cfg["webp"])} if cfg.get("webp") else {"export_image_format": "AUTO"}
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB" if cfg["format"] == "glb" else "GLTF_SEPARATE", use_selection=True,
        export_yup=True, export_apply=True, export_tangents=cfg["tangents"], export_skins=True,
        export_morph=True, export_morph_normal=True, export_animations=True, export_animation_mode="ACTIONS",
        export_materials="EXPORT", export_normals=True, **extra)


def _abc(path, cfg):
    bpy.ops.wm.alembic_export(filepath=path, selected=True, export_hair=True, export_particles=True,
                              uvs=True, normals=True, curves_as_mesh=False, flatten=False,
                              start=int(bpy.context.scene.frame_start), end=int(bpy.context.scene.frame_end))


def _obj(path, cfg):
    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True, forward_axis="NEGATIVE_Z", up_axis="Y",
                          apply_modifiers=True, export_materials=True, export_triangulated_mesh=False)


def _stl(path, cfg):
    # millimetres: what slicers expect
    bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, global_scale=1000.0, apply_modifiers=True,
                          ascii_format=False, forward_axis="Y", up_axis="Z")


def _usd(path, cfg):
    bpy.ops.wm.usd_export(filepath=path, selected_objects_only=True, export_animation=True, export_materials=True)


_WRITERS = {"fbx": _fbx, "glb": _gltf, "gltf": _gltf, "abc": _abc, "obj": _obj, "stl": _stl, "usdz": _usd}


def _import(path):
    ext = Path(path).suffix.lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path, forward_axis="NEGATIVE_Z", up_axis="Y")
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path, global_scale=0.001, forward_axis="Y", up_axis="Z")
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        bpy.ops.wm.usd_import(filepath=path)
    elif ext == ".abc":
        bpy.ops.wm.alembic_import(filepath=path)
    else:
        raise ValueError("Can't read %s files. Formats: fbx glb gltf obj stl usd usdz abc" % ext)


def _reimport(path):
    """Fresh scene, import the file back: counts that must match what was exported."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _import(path)
    out = _counts()
    out["size"] = _size([o for o in bpy.data.objects if o.type == "MESH" and not o.name.startswith("UCX_")
                         and not any(c.name == "glTF_not_exported" for c in o.users_collection)])
    out["actions"] = len(bpy.data.actions)
    out["arm_scale"] = max((max(abs(s) for s in a.matrix_world.to_scale()) for a in bpy.data.objects if a.type == "ARMATURE"), default=1.0)
    out["missing"] = sum(1 for im in bpy.data.images if im.source == "FILE" and not im.packed_file and not im.has_data
                         and not Path(bpy.path.abspath(im.filepath)).is_file())
    return out


def _collision(objs, mode):
    """UCX_ hulls: keep (UE), rename to Godot's -colonly hint, or leave them out."""
    ucx = [o for o in objs if o.name.startswith("UCX_")]
    if mode == "godot":
        for o in ucx:
            o.name = o.name[4:] + "-colonly"
    elif mode == "drop":
        objs = [o for o in objs if o not in ucx]
    return objs, len(ucx) if mode != "drop" else 0


def _lods(objs, table):
    """<name>_LOD0 plus decimated copies for each mesh named in table {name: ratios}."""
    out, made = list(objs), 0
    for obj in objs:
        ratios = table.get(obj.name)
        if obj.type != "MESH" or not ratios:
            continue
        base = obj.name
        obj.name = base + "_LOD0"
        for i, r in enumerate(ratios[1:], 1):
            dup = obj.copy()
            dup.data = obj.data.copy()
            dup.name = "%s_LOD%d" % (base, i)
            bpy.context.scene.collection.objects.link(dup)
            dec = dup.modifiers.new("lod", "DECIMATE")
            dec.ratio = max(0.01, min(1.0, float(r)))
            out.append(dup)
            made += 1
    return out, made + (1 if made else 0)


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
    size = _size([o for o in objs if o.type == "MESH" and not o.name.startswith("UCX_")])
    objs, ucx = _collision(objs, p.get("ucx", "drop"))
    lods = 0
    if p.get("lods"):
        objs, lods = _lods(objs, p["lods"])
    path = p["path"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _select(objs)
    _WRITERS[p["format"]](path, p)
    mb = round(Path(path).stat().st_size / 1048576.0, 2)
    out = dict(have, path=path, format=p["format"], normal=p["normal"], mb=mb, ucx=ucx, lods=lods)
    if p.get("verify") and p["format"] != "abc":
        got = _reimport(path)
        static = p["format"] in ("obj", "stl")  # no skin, morphs or bones in these formats
        keys = () if lods else ("meshes",) if static else ("meshes", "skins", "morphs", "bones")
        bad = [k for k in keys if got[k] != have[k]]
        warnings = ["reimport differs: " + ", ".join("%s %s->%s" % (k, have[k], got[k]) for k in bad)] if bad else []
        drift = max((abs(a - b) / max(a, 1e-6) for a, b in zip(size, got["size"]) if a > 1e-4), default=0.0)
        if drift > 0.02 and p["format"] != "stl":
            warnings.append("size %s -> %s cm" % ("x".join("%g" % round(v * 100) for v in size),
                                                    "x".join("%g" % round(v * 100) for v in got["size"])))
        if abs(got["arm_scale"] - 1.0) > 0.01:
            warnings.append("armature scale %g (expect 1)" % round(got["arm_scale"], 3))
        if have["clips"] and not got["actions"] and not static:
            warnings.append("clips lost on reimport")
        if got["missing"]:
            warnings.append("%d missing images" % got["missing"])
        if mb > 8:
            warnings.append("%.1f MB file" % mb)
        out["verified"] = not warnings
        if warnings:
            out["warnings"] = warnings
    return out


def op_convert(p):
    """Import any supported file into an empty scene and write it as p['format']."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _import(p["src"])
    objs = [o for o in bpy.data.objects if o.type in ("MESH", "ARMATURE", "CURVES", "EMPTY")]
    if not objs:
        raise ValueError("No meshes in %s" % Path(p["src"]).name)
    Path(p["path"]).parent.mkdir(parents=True, exist_ok=True)
    _select(objs)
    cfg = {"format": p["format"], "axis": ["-Z", "Y"], "scale": "FBX_SCALE_UNITS", "space": False, "tangents": True}
    _WRITERS[p["format"]](p["path"], cfg)
    counts = _counts()
    return {"path": p["path"], "meshes": counts["meshes"], "tris": counts["tris"]}
