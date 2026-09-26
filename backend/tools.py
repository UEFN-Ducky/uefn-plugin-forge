"""MCP tools. Same scene functions the panel calls."""

from __future__ import annotations

from typing import Any

from .scene import dispatch
from .uefn import export_glbs, send_to_uefn


def _run(api: Any, name: str, op: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    before = None
    if op not in {"scene_list", "scene_new", "scene_get", "stats", "scene_payload", "check", "render"}:
        try:
            before = dispatch(name, "scene_get", {}).get("rev")
        except Exception:
            before = None
    if op == "scene_list":
        result = dispatch("", "scene_list", {}, actor="agent")
    elif op == "scene_new":
        result = dispatch("", "scene_new", params, actor="agent")
        name = str(result.get("name") or name)
    else:
        result = dispatch(name, op, params, actor="agent")
    if before is not None and op not in {"undo", "redo", "revert"}:
        api.changeset.record(
            command=f"forge_{op}",
            kind="forge_scene",
            ident=name or str(result.get("name") or ""),
            summary=str(result.get("summary") or op),
            before={"rev": before},
            revertable="manual",
            reason="Use forge_undo to step back. Forge keeps its own history.",
        )
    if op == "render":
        result = _attach_captures(result)
    return result


def _attach_captures(result: dict) -> dict:
    paths = list(result.get("paths") or [])
    if not paths:
        return result
    try:
        from pathlib import Path

        from frontend.ui_web.tool_captures import save_capture_for_agents

        raw = Path(paths[0]).read_bytes()
        saved = save_capture_for_agents(raw, prefix="forge")
        result = {**result, "path": saved.get("path") or paths[0], "media_url": saved.get("media_url") or ""}
    except Exception:
        result = {**result, "path": paths[0]}
    return result


def register_tools(api: Any) -> None:
    host = {"listener": False}

    @api.tool(**host)
    def forge_scene_list() -> dict:
        """List Forge scenes on this machine."""
        return _run(api, "", "scene_list")

    @api.tool(**host)
    def forge_scene_new(name: str) -> dict:
        """Create an empty Forge scene."""
        return _run(api, name, "scene_new", {"name": name})

    @api.tool(**host)
    def forge_scene_get(name: str) -> dict:
        """Scene tree, selection, materials, and per-object bounds. Read-only."""
        return _run(api, name, "scene_get")

    @api.tool(**host)
    def forge_undo(name: str) -> dict:
        """Undo the last Forge edit."""
        return _run(api, name, "undo")

    @api.tool(**host)
    def forge_redo(name: str) -> dict:
        """Redo a Forge edit."""
        return _run(api, name, "redo")

    @api.tool(**host)
    def forge_add(name: str, kind: str, params: dict | None = None, pos: list | None = None, material: str = "", layer: str = "default", object_name: str = "") -> dict:
        """Add a primitive: box, rounded_box, plane, grid, sphere, cylinder, cone, torus, capsule, wedge, stairs, arch, lathe, extrude. Sizes are centimetres."""
        body: dict = {"kind": kind, "params": params or {}, "layer": layer}
        if pos:
            body["pos"] = pos
        if material:
            body["material"] = material
        if object_name:
            body["name"] = object_name
        return _run(api, name, "add", body)

    @api.tool(**host)
    def forge_edit(name: str, id: str = "", pos: list | None = None, rot: list | None = None, scale: list | None = None, params: dict | None = None, layer: str = "", object_name: str = "", parent: str = "", locked: bool | None = None) -> dict:
        """Edit an object's transform, primitive params, name, layer, parent, or lock. Rot is degrees."""
        body: dict = {}
        if id:
            body["id"] = id
        if pos:
            body["pos"] = pos
        if rot:
            body["rot"] = rot
        if scale:
            body["scale"] = scale
        if params:
            body["params"] = params
        if layer:
            body["layer"] = layer
        if object_name:
            body["name"] = object_name
        if parent:
            body["parent"] = parent
        if locked is not None:
            body["locked"] = locked
        return _run(api, name, "edit", body)

    @api.tool(**host)
    def forge_duplicate(name: str, id: str = "", offset: list | None = None) -> dict:
        """Duplicate an object. Offset defaults to 20 cm on X."""
        return _run(api, name, "duplicate", {"id": id, "offset": offset or [20, 0, 0]})

    @api.tool(**host)
    def forge_delete(name: str, ids: list | None = None) -> dict:
        """Delete objects by id. Defaults to the selection."""
        return _run(api, name, "delete", {"ids": ids or []})

    @api.tool(**host)
    def forge_group(name: str, ids: list, object_name: str = "Group") -> dict:
        """Parent objects under a new group. Groups can export as one mesh."""
        return _run(api, name, "group", {"ids": ids, "name": object_name, "combine": True})

    @api.tool(**host)
    def forge_join(name: str, ids: list) -> dict:
        """Combine objects into one mesh in world space."""
        return _run(api, name, "join", {"ids": ids})

    @api.tool(**host)
    def forge_separate(name: str, id: str = "") -> dict:
        """Split the selected object into one mesh per material."""
        return _run(api, name, "separate", {"id": id})

    @api.tool(**host)
    def forge_boolean(name: str, a: str, b: str, op: str = "difference") -> dict:
        """Convex boolean: difference, union, or intersect. Both objects must be convex."""
        return _run(api, name, "boolean", {"a": a, "b": b, "op": op})

    @api.tool(**host)
    def forge_modifier(name: str, action: str = "add", type: str = "mirror", axis: str = "x", count: int = 3, offset: list | None = None) -> dict:
        """Mirror or array modifier. action is add, apply, or clear."""
        return _run(api, name, "modifier", {"action": action, "type": type, "axis": axis, "count": count, "offset": offset or [120, 0, 0]})

    @api.tool(**host)
    def forge_select(name: str, object: str = "", query: str = "", faces: list | None = None, verts: list | None = None, edges: list | None = None, mode: str = "", select_mode: str = "") -> dict:
        """Set the shared selection. query 'top' selects upward faces so the next edit hits them."""
        body: dict = {}
        if object:
            body["object"] = object
        if query:
            body["query"] = query
        if faces is not None:
            body["faces"] = faces
        if verts is not None:
            body["verts"] = verts
        if edges is not None:
            body["edges"] = edges
        if mode:
            body["mode"] = mode
        if select_mode:
            body["select_mode"] = select_mode
        return _run(api, name, "select", body)

    @api.tool(**host)
    def forge_move_elements(name: str, delta: list, kind: str = "face", ids: list | None = None) -> dict:
        """Move the current verts, edges, or faces by a centimetre delta."""
        return _run(api, name, "move", {"delta": delta, "kind": kind, "ids": ids or []})

    @api.tool(**host)
    def forge_mesh_op(name: str, op: str, distance: float = 10, amount: float = 5, width: float = 4, segments: int = 1, faces: list | None = None, edges: list | None = None) -> dict:
        """Edit the selected object: extrude, inset, bevel, loopcut, subdivide, fill, merge, dissolve, delete, normals, shade, origin."""
        return _run(api, name, "mesh_op", {
            "op": op, "distance": distance, "amount": amount, "width": width,
            "segments": segments, "faces": faces or [], "edges": edges or [],
        })

    @api.tool(**host)
    def forge_material_set(name: str, material: str = "", base_color: list | None = None, metallic: float | None = None, roughness: float | None = None, emissive: list | None = None, emissive_strength: float | None = None, opacity: float | None = None) -> dict:
        """Create or update a PBR material. base_color is 0-1 RGB."""
        body: dict = {}
        if material:
            body["name"] = material
            body["id"] = material
        if base_color is not None:
            body["base_color"] = base_color
        if metallic is not None:
            body["metallic"] = metallic
        if roughness is not None:
            body["roughness"] = roughness
        if emissive is not None:
            body["emissive"] = emissive
        if emissive_strength is not None:
            body["emissive_strength"] = emissive_strength
        if opacity is not None:
            body["opacity"] = opacity
        return _run(api, name, "material_set", body)

    @api.tool(**host)
    def forge_material_assign(name: str, material: str, id: str = "", faces: list | None = None) -> dict:
        """Assign a material to an object, or to specific faces."""
        return _run(api, name, "material_assign", {"material": material, "id": id, "faces": faces or []})

    @api.tool(**host)
    def forge_uv_project(name: str, mode: str = "box", density: float = 0.01) -> dict:
        """Project UVs: box, planar, or cylinder. density is UV units per centimetre."""
        return _run(api, name, "uv", {"mode": mode, "density": density})

    @api.tool(**host)
    def forge_texture_generate(name: str, prompt: str, material: str = "", tileable: bool = True, derive_maps: bool = True) -> dict:
        """Generate a texture with the saved OpenAI or Google key, optionally tileable, with normal and ORM maps."""
        return _run(api, name, "texture", {"prompt": prompt, "material": material, "name": material, "tileable": tileable, "derive_maps": derive_maps})

    @api.tool(**host)
    def forge_texture_import(name: str, path: str, material: str = "") -> dict:
        """Use a local image as a material's base color and derive normal and ORM maps."""
        return _run(api, name, "texture", {"path": path, "material": material, "name": material, "tileable": False, "derive_maps": True})

    @api.tool(**host)
    def forge_render(name: str, views: list | None = None, id: str = "") -> dict:
        """Render a preview PNG into tool_captures so you can see the scene without the panel."""
        return _run(api, name, "render", {"views": views or ["three", "front", "top"], "id": id})

    @api.tool(**host)
    def forge_check(name: str) -> dict:
        """Report holes, flipped or zero-area faces, missing UVs, triangle budget, pivot, and scale."""
        return _run(api, name, "check")

    @api.tool(**host)
    def forge_fix(name: str, issues: str = "all", id: str = "") -> dict:
        """Auto-fix safe issues. Returns before and after triangle counts."""
        return _run(api, name, "fix", {"issues": issues, "id": id})

    @api.tool(**host)
    def forge_stats(name: str) -> dict:
        """Triangle counts and bounds in centimetres."""
        return _run(api, name, "stats")

    @api.tool(**host)
    def forge_export(name: str, mode: str = "each") -> dict:
        """Write GLB files (metres, Y-up) next to the scene. Does not import into UEFN."""
        from .scene import load as load_scene

        scene = load_scene(name)
        files = export_glbs(scene, mode, scene.folder / "export")
        return {"files": [{"name": f["name"], "glb": f["glb"], "materials": f["materials"]} for f in files]}

    @api.tool(listener=True)
    def forge_send_to_uefn(name: str, mode: str = "each", folder: str = "", collision: str = "simple") -> dict:
        """Import the scene into the open UEFN project as SM_ meshes and MI_ materials on M_Forge_Master."""
        return send_to_uefn(name, api.listener, mode=mode, folder=folder, collision=collision)
