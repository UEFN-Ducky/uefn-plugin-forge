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


def _has_params(exprs, names) -> bool:
    """True when every parameter name is on one of the material's expressions (an old master gets rebuilt)."""
    found = set()
    for node in exprs:
        try:
            found.add(str(node.get_editor_property("parameter_name")))
        except Exception:
            pass
    return set(names) <= found


def _sampler(node, kind: str) -> None:
    """Texture parameter sampler type: normal = TC_Normalmap source, masks = linear ORM, color = sRGB."""
    enum = getattr(unreal, "MaterialSamplerType", None)
    value = getattr(enum, {"normal": "SAMPLERTYPE_NORMAL", "masks": "SAMPLERTYPE_MASKS"}.get(kind, "SAMPLERTYPE_COLOR"), None)
    # The default texture must match the sampler type or the material fails to compile. Engine ships no linear
    # default for masks, so ORM keeps the Color sampler (forge_set_texture_usage makes the texture itself linear).
    if value is None or kind != "normal":
        return
    tex = unreal.EditorAssetLibrary.load_asset("/Engine/EngineMaterials/DefaultNormal")
    if tex is None:
        return  # keep the Color sampler; instance textures still import with the right compression
    node.set_editor_property("texture", tex)
    node.set_editor_property("sampler_type", value)


def _ao(mat, orm, use_orm, y: int) -> None:
    """Ambient occlusion = lerp(1, ORM.R, UseORM * AOStrength): the R channel of an ORM map."""
    mask_r = _expr(mat, "ComponentMask", -500, y)
    for channel, on in (("r", True), ("g", False), ("b", False), ("a", False)):
        mask_r.set_editor_property(channel, on)
    _connect(orm, "RGB", mask_r, "")
    strength = _scalar(mat, "AOStrength", 1.0, -800, y + 140)
    amount = _expr(mat, "Multiply", -350, y + 100)
    _connect(use_orm, "", amount, "A")
    _connect(strength, "", amount, "B")
    one = _expr(mat, "Constant", -350, y - 80)
    one.set_editor_property("r", 1.0)
    mix = _expr(mat, "Lerp", -150, y)
    _connect(one, "", mix, "A")
    _connect(mask_r, "", mix, "B")
    _connect(amount, "", mix, "Alpha")
    _to(mix, "", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)


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
        if _has_params(exprs, ("NormalTex", "UseNormalTex", "ORMTex", "UseORM", "AOStrength")):
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
    _sampler(orm, "masks")
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
    _ao(mat, orm, use_orm, 1700)

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
    _sampler(normal_tex, "normal")
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


def _fresh_material(folder: str, asset_name: str, marker: tuple):
    """(material, None) to fill in, or (None, result) when an up-to-date one exists."""
    folder = pin_project_folder(folder, default_leaf="Materials")
    full = f"{folder.rstrip('/')}/{asset_name}"
    existing = unreal.EditorAssetLibrary.load_asset(full)
    if isinstance(existing, unreal.Material):
        if _has_params(list(unreal.MaterialEditingLibrary.get_material_expressions(existing) or []), marker):
            return None, {"material_path": str(existing.get_path_name()), "created": False, "folder": folder}
        unreal.EditorAssetLibrary.delete_asset(full)
    unreal.EditorAssetLibrary.make_directory(folder)
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(asset_name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
    return mat, {"folder": folder}


def _finish(mat, info: dict) -> dict:
    layout = getattr(unreal.MaterialEditingLibrary, "layout_material_expressions", None)
    if callable(layout):
        layout(mat)
    unreal.MaterialEditingLibrary.recompile_material(mat)
    mat.modify(True)
    unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    return {"material_path": str(mat.get_path_name()), "created": True, **info}


def _normal(mat, y: int) -> None:
    """NormalTex / UseNormalTex -> Normal (flat when the switch is off)."""
    tex = _expr(mat, "TextureSampleParameter2D", -800, y)
    _name(tex, "NormalTex")
    _sampler(tex, "normal")
    use = _scalar(mat, "UseNormalTex", 0.0, -800, y + 220)
    flat = _expr(mat, "Constant3Vector", -500, y)
    flat.set_editor_property("constant", unreal.LinearColor(0, 0, 1, 1))
    mix = _expr(mat, "Lerp", -250, y + 60)
    _connect(flat, "", mix, "A")
    _connect(tex, "RGB", mix, "B")
    _connect(use, "", mix, "Alpha")
    _to(mix, "", unreal.MaterialProperty.MP_NORMAL)


def forge_build_hair(folder: str = "", asset_name: str = "M_Forge_Hair") -> dict:
    """Hair cards: masked, two-sided. BaseColorTex.A is the strand mask; instances set tint, roughness, normal."""
    mat, info = _fresh_material(folder, asset_name, ("BaseColor", "BaseColorTex", "NormalTex", "OpacityClip"))
    if mat is None:
        return info
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
    mat.set_editor_property("two_sided", True)
    tint = _tinted(mat, "BaseColor", "BaseColorTex", "UseBaseColorTex", 0)
    _sampler(_sampler_node(mat, "BaseColorTex"), "color")
    _to(tint, "", unreal.MaterialProperty.MP_BASE_COLOR)
    tex = _sampler_node(mat, "BaseColorTex")
    use = _sampler_node(mat, "UseBaseColorTex")
    one = _expr(mat, "Constant", -1000, 520)
    one.set_editor_property("r", 1.0)
    mask = _expr(mat, "Lerp", -700, 520)
    _connect(one, "", mask, "A")
    _connect(tex, "A", mask, "B")
    _connect(use, "", mask, "Alpha")
    _to(mask, "", unreal.MaterialProperty.MP_OPACITY_MASK)
    _to(_scalar(mat, "Roughness", 0.6, -700, 700), "", unreal.MaterialProperty.MP_ROUGHNESS)
    _to(_scalar(mat, "Specular", 0.5, -700, 840), "", unreal.MaterialProperty.MP_SPECULAR)
    clip = _scalar(mat, "OpacityClip", 0.33, -700, 980)  # marker; the cutoff itself is the material's clip value
    mat.set_editor_property("opacity_mask_clip_value", 0.33)
    del clip
    _normal(mat, 1100)
    return _finish(mat, info)


def _sampler_node(mat, name: str):
    """The parameter expression called name."""
    for node in unreal.MaterialEditingLibrary.get_material_expressions(mat) or []:
        try:
            if str(node.get_editor_property("parameter_name")) == name:
                return node
        except Exception:
            pass
    raise RuntimeError(f"no parameter {name}")


def forge_build_skin(folder: str = "", asset_name: str = "M_Forge_Skin", wrap: bool = False) -> dict:
    """Skin: Subsurface shading with SubsurfaceColor; falls back to a Fresnel wrap glow when UEFN refuses it."""
    mat, info = _fresh_material(folder, asset_name, ("BaseColor", "BaseColorTex", "NormalTex", "SubsurfaceColor"))
    if mat is None:
        return info
    subsurface = False
    if not wrap:
        try:
            mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_SUBSURFACE)
            subsurface = True
        except Exception:
            subsurface = False
    tint = _tinted(mat, "BaseColor", "BaseColorTex", "UseBaseColorTex", 0)
    _to(tint, "", unreal.MaterialProperty.MP_BASE_COLOR)
    sss = _expr(mat, "VectorParameter", -1400, 600)
    _name(sss, "SubsurfaceColor")
    sss.set_editor_property("default_value", unreal.LinearColor(0.8, 0.3, 0.2, 1))
    if subsurface:
        _to(sss, "", unreal.MaterialProperty.MP_SUBSURFACE_COLOR)
    else:  # wrap: edge glow of the subsurface tint, a stand-in for light bleeding through thin skin
        fresnel = _expr(mat, "Fresnel", -1000, 700)
        strength = _scalar(mat, "WrapStrength", 0.25, -1000, 840)
        glow = _expr(mat, "Multiply", -700, 720)
        _connect(sss, "", glow, "A")
        _connect(fresnel, "", glow, "B")
        scaled = _expr(mat, "Multiply", -450, 760)
        _connect(glow, "", scaled, "A")
        _connect(strength, "", scaled, "B")
        _to(scaled, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    rough = _scalar(mat, "Roughness", 0.55, -700, 1000)
    orm = _expr(mat, "TextureSampleParameter2D", -800, 1140)
    _name(orm, "ORMTex")
    _sampler(orm, "masks")
    use_orm = _scalar(mat, "UseORM", 0.0, -800, 1360)
    mask_g = _expr(mat, "ComponentMask", -500, 1140)
    for channel, on in (("r", False), ("g", True), ("b", False), ("a", False)):
        mask_g.set_editor_property(channel, on)
    _connect(orm, "RGB", mask_g, "")
    mix = _expr(mat, "Lerp", -250, 1100)
    _connect(rough, "", mix, "A")
    _connect(mask_g, "", mix, "B")
    _connect(use_orm, "", mix, "Alpha")
    _to(mix, "", unreal.MaterialProperty.MP_ROUGHNESS)
    _ao(mat, orm, use_orm, 1500)
    _normal(mat, 1740)
    return _finish(mat, dict(info, subsurface=subsurface))


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


def forge_set_mesh_options(mesh_path: str, nanite: bool = False, lod_count: int = 0, ratios: list | None = None) -> dict:
    """Static mesh: Nanite on, and/or auto-reduced LODs (lod_count = total LODs incl. LOD0; ratios = triangle share each)."""
    mesh = _load(mesh_path)
    if not isinstance(mesh, unreal.StaticMesh):
        raise ValueError(f"Not a StaticMesh: {mesh_path}")
    out: dict[str, Any] = {"mesh_path": mesh.get_path_name(), "nanite": False, "lods": 0}
    if nanite:
        settings = mesh.get_editor_property("nanite_settings")
        settings.set_editor_property("enabled", True)
        mesh.set_editor_property("nanite_settings", settings)
        out["nanite"] = True
    if lod_count > 0 and not nanite:  # Nanite meshes need no LOD chain
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        options = unreal.EditorScriptingMeshReductionOptions(auto_compute_lod_screen_size=True)
        steps = [max(0.02, min(1.0, float(r))) for r in (ratios or [])] or [0.5 ** i for i in range(lod_count)]
        options.reduction_settings = [
            unreal.EditorScriptingMeshReductionSettings(percent_triangles=r, screen_size=r) for r in steps]
        out["lods"] = int(sub.set_lods(mesh, options))  # total LODs including LOD0
    unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    return out


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
register("forge_build_hair")(forge_build_hair)
register("forge_set_mesh_options")(forge_set_mesh_options)
register("forge_build_skin")(forge_build_skin)

try:  # older hosts have no plugin heavy-command opt-in
    from listener.tick import register_heavy

    register_heavy("forge_import_skeletal")
except Exception:
    pass
