"""Export a Forge scene and land it in UEFN as SM_ meshes plus MI_ materials."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .gltf import write_glb
from .kernel.mesh import Mesh
from .kernel.ops import set_origin
from .scene import Scene, load

Listener = Callable[..., Any]


def _call(listener: Listener, command: str, params: dict | None = None, timeout: float = 120) -> dict:
    try:
        result = listener(command, params or {}, timeout)
    except TypeError:
        result = listener(command, params or {})
    if not isinstance(result, dict):
        return {"result": result}
    return result


def collect_exports(scene: Scene, mode: str = "each") -> list[dict[str, Any]]:
    """One record per static mesh we will import. Collision-layer objects become UCX parts."""
    mode = (mode or "each").lower()
    objects = [n for n in scene.data["nodes"] if n.get("type") == "object" and n.get("visible", True)]
    collision = [n for n in objects if n.get("layer") == "collision"]
    visible = [n for n in objects if n.get("layer") != "collision"]

    def bake(nodes: list[dict]) -> Mesh:
        mesh = Mesh()
        for n in nodes:
            mesh.append(scene.world_mesh(n))
        if mesh.verts:
            set_origin(mesh, "base")
        return mesh

    def ucx_for(parent: str | None) -> list[Mesh]:
        parts = []
        for n in collision:
            if parent is None or n.get("parent") == parent or not n.get("parent"):
                part = scene.world_mesh(n)
                if part.verts:
                    set_origin(part, "base")
                    parts.append(part)
        return parts

    exports: list[dict[str, Any]] = []
    if mode == "all":
        mesh = bake(visible)
        exports.append({"name": scene.name, "mesh": mesh, "collision": [scene.world_mesh(n) for n in collision if n]})
    elif mode == "group":
        groups = [n for n in scene.data["nodes"] if n.get("type") == "group"]
        grouped: set[str] = set()
        for g in groups:
            kids = [n for n in visible if n.get("parent") == g["id"]]
            if not kids:
                continue
            grouped.update(n["id"] for n in kids)
            exports.append({"name": g.get("name") or g["id"], "mesh": bake(kids), "collision": ucx_for(g["id"])})
        loose = [n for n in visible if n["id"] not in grouped]
        for n in loose:
            exports.append({"name": n.get("name") or n["id"], "mesh": bake([n]), "collision": ucx_for(n.get("parent"))})
    else:
        # One SM per shared prototype. Instances stay in the viewer.
        seen: set[str] = set()
        for n in visible:
            key = scene.display_key(n)
            if key in seen:
                continue
            seen.add(key)
            mesh = scene.shaped_mesh(n)
            if mesh.verts:
                set_origin(mesh, "base")
            exports.append({"name": n.get("name") or n["id"], "mesh": mesh, "collision": ucx_for(n.get("parent"))})
    return [e for e in exports if e["mesh"].verts]


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_" else "_" for ch in name.strip())
    return cleaned[:48] or "Forge"


def export_glbs(scene: Scene, mode: str, folder: Path) -> list[dict[str, Any]]:
    mats = scene.materials()
    out = []
    for item in collect_exports(scene, mode):
        name = "SM_" + _safe_name(str(item["name"]))
        path = write_glb(item["mesh"], folder / f"{name}.glb", mats)
        order = []
        seen = set()
        for mat in item["mesh"].face_mat:
            if mat not in seen:
                seen.add(mat)
                order.append(mat)
        out.append({"name": name, "glb": str(path), "materials": order, "collision": item["collision"]})
    return out


def _imported(result: dict, needle: str) -> str | None:
    paths = (result or {}).get("imported") or []
    for path in paths:
        if needle in str(path):
            return str(path).split(".")[0]
    return str(paths[0]).split(".")[0] if paths else None


def send_to_uefn(
    scene_name: str,
    listener: Listener,
    *,
    mode: str = "each",
    folder: str = "",
    collision: str = "simple",
) -> dict[str, Any]:
    scene = load(scene_name)
    info = _call(listener, "get_project_info", {})
    root = str(info.get("content_root") or "").rstrip("/")
    if not root:
        raise RuntimeError("UEFN has no project open")
    dest = folder.strip() or f"{root}/Forge"
    master = _call(listener, "forge_build_master", {"folder": f"{dest}/Materials", "asset_name": "M_Forge_Master"})
    master_path = str(master.get("material_path") or "").split(".")[0]
    if not master_path:
        raise RuntimeError(f"Could not build M_Forge_Master: {master}")

    work = scene.folder / "export"
    items = export_glbs(scene, mode, work)
    mats = scene.materials()
    landed = []
    tex_cache: dict[str, str] = {}

    for item in items:
        imported = _call(listener, "import_asset", {
            "source_file": str(Path(item["glb"])),
            "destination_path": dest,
            "replace_existing": True,
        }, timeout=180)
        mesh_path = _imported(imported, "StaticMeshes") or _imported(imported, item["name"])
        if not mesh_path:
            raise RuntimeError(f"Import did not return a mesh for {item['name']}: {imported}")
        mi_paths = []
        for index, mid in enumerate(item["materials"]):
            mat = mats.get(mid) or {}
            mi_name = f"MI_{item['name']}_{_safe_name(str(mat.get('name') or mid))}"
            made = _call(listener, "create_material_instance", {
                "asset_name": mi_name,
                "parent_material_path": master_path,
                "folder": f"{dest}/Materials",
            })
            mi = str(made.get("material_instance_path") or "").split(".")[0]
            color = list(mat.get("base_color") or [0.75, 0.75, 0.78, 1])
            _call(listener, "set_material_instance_vector", {"material_instance_path": mi, "param_name": "BaseColor", "color": color[:4]})
            emis = list(mat.get("emissive") or [0, 0, 0])
            _call(listener, "set_material_instance_vector", {"material_instance_path": mi, "param_name": "Emissive", "color": emis[:3] + [1]})
            _call(listener, "set_material_instance_scalar", {"material_instance_path": mi, "param_name": "Metallic", "value": float(mat.get("metallic") or 0)})
            _call(listener, "set_material_instance_scalar", {"material_instance_path": mi, "param_name": "Roughness", "value": float(mat.get("roughness") if mat.get("roughness") is not None else 0.5)})
            _call(listener, "set_material_instance_scalar", {"material_instance_path": mi, "param_name": "EmissiveStrength", "value": float(mat.get("emissive_strength") or 0)})
            for slot, usage, flag in (
                ("base_color", "color", "UseBaseColorTex"),
                ("normal", "normal", "UseNormalTex"),
                ("orm", "masks", "UseORM"),
            ):
                fname = (mat.get("textures") or {}).get(slot)
                if not fname:
                    continue
                src = scene.folder / "textures" / fname
                if not src.is_file():
                    continue
                if fname not in tex_cache:
                    tex_imp = _call(listener, "import_asset", {
                        "source_file": str(src),
                        "destination_path": f"{dest}/Textures",
                        "replace_existing": True,
                    })
                    tex_path = _imported(tex_imp, "Textures") or _imported(tex_imp, src.stem)
                    if tex_path:
                        _call(listener, "forge_set_texture_usage", {"texture_path": tex_path, "usage": usage})
                        tex_cache[fname] = tex_path
                if fname in tex_cache:
                    param = {"base_color": "BaseColorTex", "normal": "NormalTex", "orm": "ORMTex"}[slot]
                    _call(listener, "set_material_instance_texture", {
                        "material_instance_path": mi, "param_name": param, "texture_path": tex_cache[fname],
                    })
                    _call(listener, "set_material_instance_scalar", {"material_instance_path": mi, "param_name": flag, "value": 1.0})
            mi_paths.append(mi)
        _call(listener, "forge_set_mesh_materials", {"mesh_path": mesh_path, "materials": mi_paths})
        if collision != "none":
            # ponytail: collision-layer meshes are counted, not imported as UCX_.
            # GLB import does not attach UCX_ children. Simple convex is the fallback.
            # Upgrade: one FBX containing the render mesh plus UCX_ parts.
            _call(listener, "set_mesh_collision", {"asset_path": mesh_path, "collision_preset": "CTF_UseSimpleAsComplex"})
        valid = _call(listener, "validate_uefn_asset", {"asset_path": mesh_path})
        info_mesh = _call(listener, "get_static_mesh_info", {"asset_path": mesh_path})
        landed.append({
            "mesh": mesh_path,
            "materials": mi_paths,
            "valid": valid,
            "info": info_mesh,
            "collision_parts": len(item["collision"]),
        })
    return {"folder": dest, "master": master_path, "assets": landed, "count": len(landed)}
