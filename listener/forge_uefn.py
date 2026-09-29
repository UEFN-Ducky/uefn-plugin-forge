"""UEFN-side Forge commands: master material, mesh slots, texture compression."""

from __future__ import annotations

from typing import Any

import unreal

from listener.dispatch import register
from listener.project_paths import pin_project_folder


def _load(path: str):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        # import_asset returns ObjectName.ObjectName; load wants the package
        pkg = str(path).split(".")[0]
        asset = unreal.EditorAssetLibrary.load_asset(pkg)
    if asset is None:
        raise ValueError(f"Asset not found: {path}")
    return asset


_EXPR_ALIAS = {"Lerp": "LinearInterpolate"}


def _expr(mat, short: str, x: int, y: int):
    name = _EXPR_ALIAS.get(short, short)
    cls = getattr(unreal, f"MaterialExpression{name}", None)
    if cls is None:
        raise RuntimeError(f"UEFN has no MaterialExpression{name}")
    node = unreal.MaterialEditingLibrary.create_material_expression(mat, cls, x, y)
    if node is None:
        raise RuntimeError(f"could not create {short}")
    return node


def _name(node, value: str) -> None:
    node.set_editor_property("parameter_name", unreal.Name(value))


def _connect(src, src_out: str, dst, dst_in: str) -> None:
    unreal.MaterialEditingLibrary.connect_material_expressions(src, src_out, dst, dst_in)


def _to(src, src_out: str, prop) -> None:
    unreal.MaterialEditingLibrary.connect_material_property(src, src_out, prop)


def forge_build_master(folder: str = "", asset_name: str = "M_Forge_Master") -> dict:
    """One cook-safe master. Instances set color, roughness, metal, and optional textures."""
    folder = pin_project_folder(folder, default_leaf="Materials")
    full = f"{folder.rstrip('/')}/{asset_name}"
    existing = None
    for candidate in (full, f"{full}.{asset_name}"):
        loaded = unreal.EditorAssetLibrary.load_asset(candidate)
        if isinstance(loaded, unreal.Material):
            existing = loaded
            break
    if existing is not None:
        exprs = list(unreal.MaterialEditingLibrary.get_material_expressions(existing) or [])
        if len(exprs) >= 10:
            return {"material_path": str(existing.get_path_name()), "created": False, "folder": folder}
        unreal.EditorAssetLibrary.delete_asset(existing.get_path_name().split(".")[0])

    unreal.EditorAssetLibrary.make_directory(folder)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    mat = asset_tools.create_asset(asset_name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)

    base = _expr(mat, "VectorParameter", -800, 0)
    _name(base, "BaseColor")
    base.set_editor_property("default_value", unreal.LinearColor(0.75, 0.75, 0.78, 1))
    base_tex = _expr(mat, "TextureSampleParameter2D", -800, 200)
    _name(base_tex, "BaseColorTex")
    use_base = _expr(mat, "ScalarParameter", -800, 420)
    _name(use_base, "UseBaseColorTex")
    use_base.set_editor_property("default_value", 0.0)
    lerp_base = _expr(mat, "Lerp", -400, 120)
    _connect(base, "", lerp_base, "A")
    _connect(base_tex, "RGB", lerp_base, "B")
    _connect(use_base, "", lerp_base, "Alpha")
    _to(lerp_base, "", unreal.MaterialProperty.MP_BASE_COLOR)

    metal = _expr(mat, "ScalarParameter", -800, 560)
    _name(metal, "Metallic")
    metal.set_editor_property("default_value", 0.0)
    rough = _expr(mat, "ScalarParameter", -800, 700)
    _name(rough, "Roughness")
    rough.set_editor_property("default_value", 0.5)
    orm = _expr(mat, "TextureSampleParameter2D", -800, 860)
    _name(orm, "ORMTex")
    use_orm = _expr(mat, "ScalarParameter", -800, 1080)
    _name(use_orm, "UseORM")
    use_orm.set_editor_property("default_value", 0.0)
    mask_g = _expr(mat, "ComponentMask", -500, 860)
    mask_g.set_editor_property("r", False)
    mask_g.set_editor_property("g", True)
    mask_g.set_editor_property("b", False)
    mask_g.set_editor_property("a", False)
    mask_b = _expr(mat, "ComponentMask", -500, 1000)
    mask_b.set_editor_property("r", False)
    mask_b.set_editor_property("g", False)
    mask_b.set_editor_property("b", True)
    mask_b.set_editor_property("a", False)
    _connect(orm, "RGB", mask_g, "")
    _connect(orm, "RGB", mask_b, "")
    lerp_m = _expr(mat, "Lerp", -250, 640)
    lerp_r = _expr(mat, "Lerp", -250, 820)
    _connect(metal, "", lerp_m, "A")
    _connect(mask_b, "", lerp_m, "B")
    _connect(use_orm, "", lerp_m, "Alpha")
    _connect(rough, "", lerp_r, "A")
    _connect(mask_g, "", lerp_r, "B")
    _connect(use_orm, "", lerp_r, "Alpha")
    _to(lerp_m, "", unreal.MaterialProperty.MP_METALLIC)
    _to(lerp_r, "", unreal.MaterialProperty.MP_ROUGHNESS)

    emis = _expr(mat, "VectorParameter", -800, -220)
    _name(emis, "Emissive")
    emis.set_editor_property("default_value", unreal.LinearColor(0, 0, 0, 1))
    strength = _expr(mat, "ScalarParameter", -800, -40)
    _name(strength, "EmissiveStrength")
    strength.set_editor_property("default_value", 0.0)
    mul = _expr(mat, "Multiply", -400, -140)
    _connect(emis, "", mul, "A")
    _connect(strength, "", mul, "B")
    _to(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    normal_tex = _expr(mat, "TextureSampleParameter2D", -800, 1240)
    _name(normal_tex, "NormalTex")
    use_n = _expr(mat, "ScalarParameter", -800, 1460)
    _name(use_n, "UseNormalTex")
    use_n.set_editor_property("default_value", 0.0)
    flat = _expr(mat, "Constant3Vector", -500, 1240)
    flat.set_editor_property("constant", unreal.LinearColor(0, 0, 1, 1))
    lerp_n = _expr(mat, "Lerp", -250, 1300)
    _connect(flat, "", lerp_n, "A")
    _connect(normal_tex, "RGB", lerp_n, "B")
    _connect(use_n, "", lerp_n, "Alpha")
    _to(lerp_n, "", unreal.MaterialProperty.MP_NORMAL)

    layout = getattr(unreal.MaterialEditingLibrary, "layout_material_expressions", None)
    if callable(layout):
        layout(mat)
    unreal.MaterialEditingLibrary.recompile_material(mat)
    mat.modify(True)
    unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    return {"material_path": str(mat.get_path_name()), "created": True, "folder": folder}


def _scalar(mat, name: str, value: float, x: int, y: int):
    node = _expr(mat, "ScalarParameter", x, y)
    _name(node, name)
    node.set_editor_property("default_value", float(value))
    return node


def _tinted(mat, color_name: str, tex_name: str, use_name: str, y: int):
    """lerp(VectorParameter, TextureSample.RGB, UseTex): one layer's colour, texture optional."""
    color = _expr(mat, "VectorParameter", -1400, y)
    _name(color, color_name)
    color.set_editor_property("default_value", unreal.LinearColor(0.75, 0.75, 0.78, 1))
    tex = _expr(mat, "TextureSampleParameter2D", -1400, y + 160)
    _name(tex, tex_name)
    use = _scalar(mat, use_name, 0.0, -1400, y + 380)
    mix = _expr(mat, "Lerp", -1000, y + 100)
    _connect(color, "", mix, "A")
    _connect(tex, "RGB", mix, "B")
    _connect(use, "", mix, "Alpha")
    return mix


def _mix(mat, under, over, vc, channel: str, x: int, y: int):
    """lerp(under, over, VertexColor.channel): paint in that channel reveals `over`."""
    node = _expr(mat, "Lerp", x, y)
    _connect(under, "", node, "A")
    _connect(over, "", node, "B")
    _connect(vc, channel, node, "Alpha")
    return node


def forge_build_blend_master(folder: str = "", asset_name: str = "M_Forge_Blend") -> dict:
    """Base + three layers revealed by vertex colour R, G, B. Base parameters match M_Forge_Master."""
    folder = pin_project_folder(folder, default_leaf="Materials")
    full = f"{folder.rstrip('/')}/{asset_name}"
    for candidate in (full, f"{full}.{asset_name}"):
        loaded = unreal.EditorAssetLibrary.load_asset(candidate)
        if isinstance(loaded, unreal.Material):
            if len(list(unreal.MaterialEditingLibrary.get_material_expressions(loaded) or [])) >= 30:
                return {"material_path": str(loaded.get_path_name()), "created": False, "folder": folder}
            unreal.EditorAssetLibrary.delete_asset(loaded.get_path_name().split(".")[0])
            break
    unreal.EditorAssetLibrary.make_directory(folder)
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(asset_name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
    vc = _expr(mat, "VertexColor", -700, -400)
    color = _tinted(mat, "BaseColor", "BaseColorTex", "UseBaseColorTex", 0)
    orm = _expr(mat, "TextureSampleParameter2D", -1400, 2600)
    _name(orm, "ORMTex")
    use_orm = _scalar(mat, "UseORM", 0.0, -1400, 2820)
    rough = _expr(mat, "Lerp", -1000, 2700)
    _connect(_scalar(mat, "Roughness", 0.5, -1400, 2960), "", rough, "A")
    mask_g = _expr(mat, "ComponentMask", -1100, 2600)
    for ch, on in (("r", False), ("g", True), ("b", False), ("a", False)):
        mask_g.set_editor_property(ch, on)
    _connect(orm, "RGB", mask_g, "")
    _connect(mask_g, "", rough, "B")
    _connect(use_orm, "", rough, "Alpha")
    metal = _expr(mat, "Lerp", -1000, 3100)
    _connect(_scalar(mat, "Metallic", 0.0, -1400, 3100), "", metal, "A")
    mask_b = _expr(mat, "ComponentMask", -1100, 3200)
    for ch, on in (("r", False), ("g", False), ("b", True), ("a", False)):
        mask_b.set_editor_property(ch, on)
    _connect(orm, "RGB", mask_b, "")
    _connect(mask_b, "", metal, "B")
    _connect(use_orm, "", metal, "Alpha")
    for i, channel in ((1, "R"), (2, "G"), (3, "B")):
        y = 600 * i
        x = -400 + 120 * i
        layer = _tinted(mat, f"Layer{i}Color", f"Layer{i}Tex", f"UseLayer{i}Tex", y)
        color = _mix(mat, color, layer, vc, channel, x, y)
        rough = _mix(mat, rough, _scalar(mat, f"Layer{i}Roughness", 0.5, -700, y + 150), vc, channel, x, y + 150)
        metal = _mix(mat, metal, _scalar(mat, f"Layer{i}Metallic", 0.0, -700, y + 300), vc, channel, x, y + 300)
    _to(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
    _to(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    _to(metal, "", unreal.MaterialProperty.MP_METALLIC)
    emis = _expr(mat, "VectorParameter", -1400, -600)
    _name(emis, "Emissive")
    emis.set_editor_property("default_value", unreal.LinearColor(0, 0, 0, 1))
    glow = _expr(mat, "Multiply", -1000, -560)
    _connect(emis, "", glow, "A")
    _connect(_scalar(mat, "EmissiveStrength", 0.0, -1400, -420), "", glow, "B")
    _to(glow, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    normal_tex = _expr(mat, "TextureSampleParameter2D", -1400, 3400)
    _name(normal_tex, "NormalTex")
    flat = _expr(mat, "Constant3Vector", -1100, 3400)
    flat.set_editor_property("constant", unreal.LinearColor(0, 0, 1, 1))
    bump = _expr(mat, "Lerp", -1000, 3450)
    _connect(flat, "", bump, "A")
    _connect(normal_tex, "RGB", bump, "B")
    _connect(_scalar(mat, "UseNormalTex", 0.0, -1400, 3620), "", bump, "Alpha")
    _to(bump, "", unreal.MaterialProperty.MP_NORMAL)
    layout = getattr(unreal.MaterialEditingLibrary, "layout_material_expressions", None)
    if callable(layout):
        layout(mat)
    unreal.MaterialEditingLibrary.recompile_material(mat)
    mat.modify(True)
    unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    return {"material_path": str(mat.get_path_name()), "created": True, "folder": folder}


def forge_set_mesh_materials(mesh_path: str, materials: list[str]) -> dict:
    """Replace imported stub materials on a static or skeletal mesh, slot order matches the GLB."""
    mesh = _load(mesh_path)
    if isinstance(mesh, unreal.StaticMesh):
        prop = "static_materials"
    elif isinstance(mesh, unreal.SkeletalMesh):
        prop = "materials"
    else:
        raise ValueError(f"Not a StaticMesh or SkeletalMesh: {mesh_path}")
    slots = list(mesh.get_editor_property(prop) or [])
    applied = []
    for i, path in enumerate(materials or []):
        if i >= len(slots):
            break
        mat = _load(str(path))
        slots[i].set_editor_property("material_interface", mat)
        applied.append(str(path))
    mesh.set_editor_property(prop, slots)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    return {"mesh_path": mesh.get_path_name(), "slots": len(slots), "applied": applied}


def forge_import_skeletal(source_file: str, destination_path: str = "") -> dict:
    """Import a skinned GLB and sort what Interchange made: mesh, skeleton, physics, clips."""
    folder = pin_project_folder(destination_path, default_leaf="Forge")
    task = unreal.AssetImportTask()
    task.filename = source_file
    task.destination_path = folder
    task.replace_existing = True
    task.automated = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    # Interchange can finish without filling imported_object_paths; the folder is ours alone.
    paths = [str(p) for p in (task.imported_object_paths or [])] or list(
        unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False) or [])
    out: dict[str, Any] = {"folder": folder, "skeletal_mesh": "", "skeleton": "", "physics_asset": "",
                           "animations": [], "other": []}
    for path in paths:
        package = str(path).split(".")[0]
        asset = unreal.EditorAssetLibrary.load_asset(package)
        if isinstance(asset, unreal.SkeletalMesh):
            out["skeletal_mesh"] = package
        elif isinstance(asset, unreal.Skeleton):
            out["skeleton"] = package
        elif isinstance(asset, unreal.PhysicsAsset):
            out["physics_asset"] = package
        elif isinstance(asset, unreal.AnimSequence):
            out["animations"].append(package)
        else:
            out["other"].append(package)
    if not out["skeletal_mesh"]:
        raise RuntimeError(f"Import made no SkeletalMesh from {source_file}: {paths}")
    return out


def forge_set_texture_usage(texture_path: str, usage: str = "color") -> dict:
    """color = sRGB default, normal = normalmap, masks = linear masks (ORM)."""
    tex = _load(texture_path)
    usage = (usage or "color").lower()
    if usage == "normal":
        tex.set_editor_property("srgb", False)
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
    elif usage in {"masks", "orm", "linear"}:
        tex.set_editor_property("srgb", False)
        setting = getattr(unreal.TextureCompressionSettings, "TC_MASKS", None)
        if setting is None:
            setting = unreal.TextureCompressionSettings.TC_DEFAULT
        tex.set_editor_property("compression_settings", setting)
    else:
        tex.set_editor_property("srgb", True)
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
    unreal.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=False)
    return {"texture_path": tex.get_path_name(), "usage": usage}


register("forge_build_master")(forge_build_master)
register("forge_set_mesh_materials")(forge_set_mesh_materials)
register("forge_set_texture_usage")(forge_set_texture_usage)
register("forge_import_skeletal")(forge_import_skeletal)
register("forge_build_blend_master")(forge_build_blend_master)

try:  # older hosts have no plugin heavy-command opt-in
    from listener.tick import register_heavy

    register_heavy("forge_import_skeletal")
except Exception:
    pass
