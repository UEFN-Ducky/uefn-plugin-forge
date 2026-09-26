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


def _expr(mat, short: str, x: int, y: int):
    cls = getattr(unreal, f"MaterialExpression{short}")
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
    existing = unreal.EditorAssetLibrary.load_asset(full)
    if existing is not None and isinstance(existing, unreal.Material):
        return {"material_path": str(existing.get_path_name()), "created": False, "folder": folder}

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


def forge_set_mesh_materials(mesh_path: str, materials: list[str]) -> dict:
    """Replace imported stub materials on a static mesh, slot order matches the GLB."""
    mesh = _load(mesh_path)
    if not isinstance(mesh, unreal.StaticMesh):
        raise ValueError(f"Not a StaticMesh: {mesh_path}")
    slots = list(mesh.get_editor_property("static_materials") or [])
    applied = []
    for i, path in enumerate(materials or []):
        if i >= len(slots):
            break
        mat = _load(str(path))
        slots[i].set_editor_property("material_interface", mat)
        applied.append(str(path))
    mesh.set_editor_property("static_materials", slots)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    return {"mesh_path": mesh.get_path_name(), "slots": len(slots), "applied": applied}


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
