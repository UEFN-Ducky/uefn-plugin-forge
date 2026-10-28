"""Export a Forge scene and land it in UEFN as SM_/SK_ meshes, AS_ clips, and MI_ materials."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from .gltf import write_glb
from .kernel.mesh import Mesh
from .kernel.ops import set_origin
from .rig import skinned_part
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


def _visible_objects(scene: Scene) -> list[dict]:
    by_id = {n["id"]: n for n in scene.data["nodes"]}
    hidden_layers = {layer["id"] for layer in scene.data.get("layers", []) if layer.get("visible") is False}
    def visible_in_hierarchy(node):
        seen = set()
        while node and node["id"] not in seen:
            seen.add(node["id"])
            if node.get("visible") is False or node.get("layer") in hidden_layers:
                return False
            node = by_id.get(node.get("parent"))
        return node is None
    return [n for n in scene.data["nodes"] if n.get("type") == "object" and visible_in_hierarchy(n)]


def collect_exports(scene: Scene, mode: str = "each") -> list[dict[str, Any]]:
    """One record per exported static mesh; collision parts use the simple-collision fallback.

    Objects bound to a rig are left out: collect_skinned ships them as one SK_ mesh.
    """
    mode = (mode or "each").lower()
    objects = _visible_objects(scene)
    collision = [n for n in objects if n.get("layer") == "collision"]
    visible = [n for n in objects if n.get("layer") != "collision" and not n.get("skin")]

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

    def stored(nodes: list[dict]) -> dict:
        """The first node's HQ lods settings ({ratios, nanite}) among nodes, or {}."""
        return next((n["hq"]["lods"] for n in nodes if (n.get("hq") or {}).get("lods")), {})

    exports: list[dict[str, Any]] = []
    if mode == "all":
        mesh = bake(visible)
        exports.append({"name": scene.name, "mesh": mesh, "collision": [scene.world_mesh(n) for n in collision if n],
                        "lods": stored(visible)})
    elif mode == "group":
        groups = [n for n in scene.data["nodes"] if n.get("type") == "group"]
        grouped: set[str] = set()
        for g in groups:
            kids = [n for n in visible if n.get("parent") == g["id"]]
            if not kids:
                continue
            grouped.update(n["id"] for n in kids)
            exports.append({"name": g.get("name") or g["id"], "mesh": bake(kids), "collision": ucx_for(g["id"]),
                            "lods": stored(kids)})
        loose = [n for n in visible if n["id"] not in grouped]
        for n in loose:
            exports.append({"name": n.get("name") or n["id"], "mesh": bake([n]), "collision": ucx_for(n.get("parent")),
                            "lods": stored([n])})
    else:
        # One SM per shared shape/material combination. Instances stay in the viewer.
        seen: set[tuple] = set()
        for n in visible:
            key = (scene.display_key(n), n.get("material"), bool(n.get("material_override")))
            if key in seen:
                continue
            seen.add(key)
            mesh = scene.shaped_mesh(n)
            if mesh.verts:
                set_origin(mesh, "base")
            exports.append({"name": n.get("name") or n["id"], "mesh": mesh, "collision": ucx_for(n.get("parent")),
                            "lods": stored([n])})
    return [e for e in exports if e["mesh"].verts]


def collect_skinned(scene: Scene) -> list[dict[str, Any]]:
    """One skeletal mesh per rig: every visible object bound to it, pivot at the base centre."""
    objects = [n for n in _visible_objects(scene) if n.get("layer") != "collision" and n.get("skin")]
    rigs = [r for r in scene.data.get("rigs") or [] if any(n["skin"].get("rig") == r["id"] for n in objects)]
    out = []
    for rig in rigs:
        part = skinned_part(scene, rig, [n for n in objects if n["skin"].get("rig") == rig["id"]])
        shift = set_origin(part["mesh"], "base")
        for bone in part["bones"]:
            bone["head"] = [bone["head"][i] - shift[i] for i in range(3)]
            bone["tail"] = [bone["tail"][i] - shift[i] for i in range(3)]
        name = scene.name if len(rigs) == 1 else f"{scene.name}_{rig['name']}"
        out.append({"name": name, **part})
    return out


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_" else "_" for ch in name.strip())
    return cleaned[:48] or "Forge"


def export_skinned(scene: Scene, folder: Path) -> list[dict[str, Any]]:
    mats = scene.materials()
    out = []
    for item in collect_skinned(scene):
        name = "SK_" + _safe_name(item["name"])
        path = write_glb(item["mesh"], folder / f"{name}.glb", mats, skin=item)
        order = list(dict.fromkeys(item["mesh"].face_mat))
        out.append({"name": name, "glb": str(path), "materials": order,
                    "bones": len(item["bones"]), "clips": [c["name"] for c in item["clips"]]})
    return out


def export_glbs(scene: Scene, mode: str, folder: Path) -> list[dict[str, Any]]:
    mats = scene.materials()
    out = []
    used_names = set()
    for item in collect_exports(scene, mode):
        name = "SM_" + _safe_name(str(item["name"]))
        base = name
        suffix = 2
        while name.casefold() in used_names:
            name = f"{base}_{suffix}"
            suffix += 1
        used_names.add(name.casefold())
        path = write_glb(item["mesh"], folder / f"{name}.glb", mats)
        order = []
        seen = set()
        for mat in item["mesh"].face_mat:
            if mat not in seen:
                seen.add(mat)
                order.append(mat)
        out.append({"name": name, "glb": str(path), "materials": order, "collision": item["collision"],
                    "lods": item.get("lods") or {}})
    return out


def _imported(result: dict, needle: str) -> str | None:
    paths = (result or {}).get("imported") or []
    for path in paths:
        if needle in str(path):
            return str(path).split(".")[0]
    return str(paths[0]).split(".")[0] if paths else None


def _texture(listener: Listener, scene: Scene, dest: str, tex_cache: dict, fname: str, usage: str) -> str:
    """Import one texture file once per send; returns its asset path or ''."""
    if not fname:
        return ""
    src = scene.folder / "textures" / fname
    if not src.is_file():
        return ""
    if fname not in tex_cache:
        tex_imp = _call(listener, "import_asset", {"source_file": str(src), "destination_path": f"{dest}/Textures", "replace_existing": True})
        tex_path = _imported(tex_imp, "Textures") or _imported(tex_imp, src.stem)
        if not tex_path:
            return ""
        _call(listener, "forge_set_texture_usage", {"texture_path": tex_path, "usage": usage})
        tex_cache[fname] = tex_path
    return tex_cache[fname]


# material "shader" field -> (listener command, master asset); everything else uses M_Forge_Master
SPECIAL_MASTERS = {"hair": ("forge_build_hair", "M_Forge_Hair"), "skin": ("forge_build_skin", "M_Forge_Skin")}


def _instances(listener: Listener, scene: Scene, item: dict, dest: str, masters: dict, tex_cache: dict) -> list[str]:
    """One MI_ per material slot: on M_Forge_Master, or on M_Forge_Blend when the material has layers."""
    mats = scene.materials()
    mi_paths = []

    def scalar(mi, name, value):
        _call(listener, "set_material_instance_scalar", {"material_instance_path": mi, "param_name": name, "value": float(value)})

    def vector(mi, name, value):
        _call(listener, "set_material_instance_vector", {"material_instance_path": mi, "param_name": name, "color": list(value)[:4]})

    def texture(mi, name, flag, fname, usage):
        path = _texture(listener, scene, dest, tex_cache, fname, usage)
        if path:
            _call(listener, "set_material_instance_texture", {"material_instance_path": mi, "param_name": name, "texture_path": path})
            scalar(mi, flag, 1.0)

    for mid in item["materials"]:
        mat = mats.get(mid) or {}
        layers = [mats.get(lid) for lid in (mat.get("layers") or [])[:3]]
        if any(layers) and "blend" not in masters:
            built = _call(listener, "forge_build_blend_master", {"folder": f"{dest}/Materials", "asset_name": "M_Forge_Blend"})
            masters["blend"] = str(built.get("material_path") or "").split(".")[0]
        parent = masters["blend"] if any(layers) and masters.get("blend") else masters["base"]
        kind = str(mat.get("shader") or "").lower()
        if kind in SPECIAL_MASTERS and not any(layers):
            if kind not in masters:
                command, asset = SPECIAL_MASTERS[kind]
                built = _call(listener, command, {"folder": f"{dest}/Materials", "asset_name": asset})
                masters[kind] = str(built.get("material_path") or "").split(".")[0]
            parent = masters[kind] or parent
        mi_name = f"MI_{item['name']}_{_safe_name(str(mat.get('name') or mid))}"
        made = _call(listener, "create_material_instance", {
            "asset_name": mi_name,
            "parent_material_path": parent,
            "folder": f"{dest}/Materials",
        })
        mi = str(made.get("material_instance_path") or "").split(".")[0]
        vector(mi, "BaseColor", list(mat.get("base_color") or [0.75, 0.75, 0.78, 1]))
        vector(mi, "Emissive", list(mat.get("emissive") or [0, 0, 0])[:3] + [1])
        if mat.get("subsurface_color"):
            vector(mi, "SubsurfaceColor", list(mat["subsurface_color"]) + [1])
        scalar(mi, "Metallic", mat.get("metallic") or 0)
        scalar(mi, "Roughness", mat.get("roughness") if mat.get("roughness") is not None else 0.5)
        scalar(mi, "EmissiveStrength", mat.get("emissive_strength") or 0)
        textures = mat.get("textures") or {}
        texture(mi, "BaseColorTex", "UseBaseColorTex", textures.get("base_color"), "color")
        texture(mi, "NormalTex", "UseNormalTex", textures.get("normal"), "normal")
        texture(mi, "ORMTex", "UseORM", textures.get("orm"), "masks")
        if parent == masters.get("blend"):
            for i, layer in enumerate(layers, start=1):
                if not layer:
                    continue
                vector(mi, f"Layer{i}Color", list(layer.get("base_color") or [0.75, 0.75, 0.78, 1]))
                scalar(mi, f"Layer{i}Roughness", layer.get("roughness") if layer.get("roughness") is not None else 0.5)
                scalar(mi, f"Layer{i}Metallic", layer.get("metallic") or 0)
                texture(mi, f"Layer{i}Tex", f"UseLayer{i}Tex", (layer.get("textures") or {}).get("base_color"), "color")
        mi_paths.append(mi)
    return mi_paths


def _in_front_of_camera(listener: Listener) -> list[float]:
    cam = _call(listener, "get_viewport_camera", {})
    loc, rot = cam.get("location") or {}, cam.get("rotation") or {}
    yaw = math.radians(float(rot.get("yaw") or 0))
    return [float(loc.get("x") or 0) + math.cos(yaw) * 800, float(loc.get("y") or 0) + math.sin(yaw) * 800, float(loc.get("z") or 0)]


def _place(listener: Listener, landed: list[dict], location: list | None) -> tuple[list, list]:
    """Put each asset in the level: SM_ as a mesh actor, SK_ on an Animated Mesh device playing its first clip."""
    placed, notes = [], []
    base = [float(v) for v in location] if location else _in_front_of_camera(listener)
    device = None
    if any(a.get("animations") for a in landed):
        try:
            rows = _call(listener, "animated_mesh_capabilities", {}).get("device_assets") or []
            device = f"{rows[0]['path']}.{rows[0]['name']}_C" if rows else None
        except Exception as exc:
            notes.append(f"Animated Mesh device lookup failed ({exc}); enable the Animation plugin to auto-wire clips.")
    for i, asset in enumerate(landed):
        at = [base[0] + i * 400.0, base[1], base[2]]
        label = Path(asset["mesh"]).name
        clips = asset.get("animations") or []
        try:
            if clips and device:
                spawned = _call(listener, "spawn_actor", {"asset_path": device, "location": at, "label": label, "folder": "Forge"})
                label = (spawned.get("actor") or {}).get("label") or label
                wired = _call(listener, "configure_animated_mesh", {
                    "actor_path": label, "skeletal_mesh_path": asset["mesh"], "anim_path": clips[0], "loop": True,
                })
                if not wired.get("ok"):
                    notes.append(f"{label}: device placed; set its fields by hand ({wired.get('results')})")
            else:
                spawned = _call(listener, "spawn_actor", {"asset_path": asset["mesh"], "location": at, "label": label, "folder": "Forge"})
                label = (spawned.get("actor") or {}).get("label") or label
                if clips:
                    notes.append(f"{label}: placed as a skeletal mesh; an Animated Mesh device is what plays {Path(clips[0]).name} in game.")
            try:
                _call(listener, "snap_actor_to_ground", {"actor_path": label})
            except Exception:
                pass  # No ground under the camera is fine; the actor stays where it spawned.
            placed.append({"label": label, "location": at, "clip": clips[0] if clips else None})
        except Exception as exc:
            notes.append(f"Could not place {label}: {exc}")
    return placed, notes


def _names(paths) -> list[str]:
    return [str(p).split("/")[-1].split(".")[0] for p in paths if p]


def brief(out: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Asset names only, under `limit` chars: {ok, folder, assets, bad?, placed?}."""
    names, bad = [], []
    for a in out["assets"]:
        names += _names([a["mesh"]] + a.get("animations", []) + a["materials"])
        if not (a.get("valid") or {}).get("ok", True):
            bad.append(_names([a["mesh"]])[0])
    short = {"ok": not bad, "folder": out["folder"].rsplit("/", 1)[-1], "assets": names}
    if bad:
        short["invalid"] = bad
    if out.get("placed"):
        short["placed"] = len(out["placed"])
    while len(json.dumps(short)) > limit and len(short["assets"]) > 1:
        short["assets"] = short["assets"][:-2] + ["+%d" % (len(names) - len(short["assets"]) + 2)]
    return short


def send_to_uefn(
    scene_name: str,
    listener: Listener,
    *,
    mode: str = "each",
    folder: str = "",
    collision: str = "simple",
    place: bool = False,
    location: list | None = None,
    nanite: bool = False,
    lods: int = 0,
    verbose: bool = True,
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
    skinned = export_skinned(scene, work)
    landed = []
    tex_cache: dict[str, str] = {}
    masters = {"base": master_path}

    for item in skinned:
        got = _call(listener, "forge_import_skeletal", {
            "source_file": item["glb"], "destination_path": f"{dest}/{item['name']}",
        }, timeout=300)
        mesh_path = str(got.get("skeletal_mesh") or "")
        if not mesh_path:
            raise RuntimeError(f"Import did not return a skeletal mesh for {item['name']}: {got}")
        mi_paths = _instances(listener, scene, item, dest, masters, tex_cache)
        _call(listener, "forge_set_mesh_materials", {"mesh_path": mesh_path, "materials": mi_paths})
        landed.append({
            "mesh": mesh_path,
            "skeleton": got.get("skeleton"),
            "physics_asset": got.get("physics_asset"),
            "animations": got.get("animations") or [],
            "materials": mi_paths,
            "valid": _call(listener, "validate_uefn_asset", {"asset_path": mesh_path}),
        })

    for item in items:
        imported = _call(listener, "import_asset", {
            "source_file": str(Path(item["glb"])),
            "destination_path": dest,
            "replace_existing": True,
        }, timeout=180)
        mesh_path = _imported(imported, "StaticMeshes") or _imported(imported, item["name"])
        if not mesh_path:
            raise RuntimeError(f"Import did not return a mesh for {item['name']}: {imported}")
        mi_paths = _instances(listener, scene, item, dest, masters, tex_cache)
        _call(listener, "forge_set_mesh_materials", {"mesh_path": mesh_path, "materials": mi_paths})
        if collision != "none":
            # ponytail: collision-layer meshes are counted, not imported as UCX_.
            # GLB import does not attach UCX_ children. Simple convex is the fallback.
            # Upgrade: one FBX containing the render mesh plus UCX_ parts.
            _call(listener, "set_mesh_collision", {"asset_path": mesh_path, "collision_preset": "CTF_UseSimpleAsComplex"})
        stored = item.get("lods") or {}
        ratios = list(stored.get("ratios") or [])
        want_nanite = bool(nanite or stored.get("nanite"))
        count = int(lods) or len(ratios)  # an explicit lods argument overrides the stored ratios
        if want_nanite or count:
            options = {"mesh_path": mesh_path, "nanite": want_nanite, "lod_count": count}
            if ratios and not lods:
                options["ratios"] = ratios
            _call(listener, "forge_set_mesh_options", options)
        valid = _call(listener, "validate_uefn_asset", {"asset_path": mesh_path})
        info_mesh = _call(listener, "get_static_mesh_info", {"asset_path": mesh_path})
        landed.append({
            "mesh": mesh_path,
            "materials": mi_paths,
            "valid": valid,
            "info": info_mesh,
            "collision_parts": len(item["collision"]),
        })
    out = {"folder": dest, "master": master_path, "assets": landed, "count": len(landed)}
    if place:
        out["placed"], out["notes"] = _place(listener, landed, location)
    return out if verbose else brief(out)
