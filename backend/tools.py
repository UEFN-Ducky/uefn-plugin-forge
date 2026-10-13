"""MCP tools. Same scene functions the panel calls."""

from __future__ import annotations

from typing import Any

from .scene import dispatch
from .uefn import export_glbs, export_skinned, send_to_uefn


def _given(**kwargs: Any) -> dict:
    """Only the arguments the caller actually set, so op defaults stay in one place."""
    return {k: v for k, v in kwargs.items() if v is not None and v != "" and v != []}


def _run(api: Any, name: str, op: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    before = None
    if op not in {"scene_list", "scene_new", "scene_get", "stats", "scene_payload", "check", "render", "rig_preview", "scene_import_preview"}:
        try:
            before = dispatch(name, "scene_get", {"brief": True}).get("rev")
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
    from pathlib import Path

    kept: list[str] = []
    media = ""
    error = ""
    try:
        from frontend.ui_web.tool_captures import save_capture_for_agents

        for item in paths:
            raw = Path(item).read_bytes()
            saved = save_capture_for_agents(raw, prefix="forge")
            if saved.get("ok") and saved.get("path"):
                kept.append(str(saved["path"]))
                media = media or str(saved.get("media_url") or "")
            elif not error:
                error = str(saved.get("error") or "Screenshot was not saved: no active chat.")
    except Exception as exc:
        error = str(exc)
    for item in paths:
        src = Path(item)
        if src.parent.name == "ducky_forge_captures":
            try:
                src.unlink()
            except OSError:
                pass
    if kept:
        return {**result, "paths": kept, "path": kept[0], "media_url": media}
    if error:
        return {**result, "paths": [], "error": error}
    return result


def register_tools(api: Any) -> None:
    host = {"listener": False}

    @api.tool(**host)
    def forge_import_mesh(path: str, name: str = "", destination: str = "", scale: float = 1.0, preview: bool = False) -> dict:
        """Inspect or import .blend/binary .fbx/.glb/.gltf. Requires local Blender.
        destination merges into an existing scene with one undo; preview writes no scene.
        Preserves static meshes, UVs, material slots and direct base-color textures.
        Existing scenes are retained; rigs/animation/procedural shaders are not imported.
        """
        from .blend_import import import_blend
        if preview:
            return _run(api, "", "scene_import_preview", {"path": path, "scale": scale})
        if destination:
            return _run(api, destination, "import_mesh", {"path": path, "scale": scale})
        from .scene import _LOCK
        with _LOCK:
            result = import_blend(path, name, scale=scale)
        api.changeset.record(command="forge_import_mesh", kind="forge_scene",
                             ident=result["name"], summary="Imported " + result["name"],
                             before=None, revertable="manual",
                             reason="Import creates a separate scene; existing scenes are unchanged.")
        return result

    @api.register_pipeline_node("forge.import_mesh")
    def node_import_mesh(ctx: dict) -> dict:
        params = {**(ctx.get("config") or {}), **(ctx.get("inputs") or {})}
        return forge_import_mesh(str(params.get("path") or ""), str(params.get("name") or ""), str(params.get("destination") or ""), float(params.get("scale", 1)), bool(params.get("preview", False)))

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
    def forge_add(name: str, kind: str, params: dict | None = None, pos: list | None = None, material: str = "", layer: str = "default", object_name: str = "", count: int = 1, rot: list | None = None, scale: list | None = None) -> dict:
        """Add a primitive (box, rounded_box, plane, grid, sphere, cylinder, cone, torus, capsule, wedge, stairs, arch, lathe, extrude), or count instances of one shared prototype. Centimetres; pos is the base centre; rot is degrees; material is a name or id."""
        body: dict = {"kind": kind, "params": params or {}, "layer": layer, "count": max(1, int(count or 1))}
        if pos:
            body["pos"] = pos
        if rot:
            body["rot"] = rot
        if scale:
            body["scale"] = scale
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
    def forge_group(name: str, ids: list, object_name: str = "Group", into: str = "") -> dict:
        """Parent objects under a new group, or nest them into an existing group id."""
        body: dict = {"ids": ids, "name": object_name, "combine": True}
        if into:
            body["into"] = into
        return _run(api, name, "group", body)

    @api.tool(**host)
    def forge_ungroup(name: str, id: str = "") -> dict:
        """Lift a group's children up to its parent and remove the group."""
        return _run(api, name, "ungroup", {"id": id})

    @api.tool(**host)
    def forge_layer(name: str, action: str = "add", id: str = "", object_name: str = "", ids: list | None = None, visible: bool | None = None, color: str = "") -> dict:
        """Add a layer, hide or show it, or assign objects onto it. Collision exports as UCX."""
        body: dict = {"action": action}
        if id:
            body["id"] = id
        if object_name:
            body["name"] = object_name
        if ids:
            body["ids"] = ids
        if visible is not None:
            body["visible"] = visible
        if color:
            body["color"] = color
        return _run(api, name, "layer", body)

    @api.tool(**host)
    def forge_join(name: str, ids: list, weld: bool = False) -> dict:
        """Combine objects into one mesh in world space. weld merges vertices closer than 0.05 cm. For one solid shell use forge_boolean union."""
        return _run(api, name, "join", {"ids": ids, "weld": weld})

    @api.tool(**host)
    def forge_separate(name: str, id: str = "") -> dict:
        """Split the selected object into one mesh per material."""
        return _run(api, name, "separate", {"id": id})

    @api.tool(**host)
    def forge_boolean(name: str, a: str, b: str | list, op: str = "difference") -> dict:
        """Solid boolean on closed meshes, concave included: difference, union, or intersect. b may be a list to cut many holes or fuse many parts in one step."""
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
    def forge_mesh_op(name: str, op: str, distance: float = 10, amount: float = 5, width: float = 4, segments: int = 1, faces: list | None = None, edges: list | None = None, smooth: bool = False, levels: int = 1, ratio: float = 0.5) -> dict:
        """Edit the selected object: extrude, inset, bevel, loopcut, subdivide (smooth=true rounds the whole object with Catmull-Clark, levels 1-4, for sculpting), decimate (ratio of vertices to keep, for budget), fill, merge, dissolve, delete, normals, shade, origin."""
        return _run(api, name, "mesh_op", {
            "op": op, "distance": distance, "amount": amount, "width": width,
            "segments": segments, "faces": faces or [], "edges": edges or [],
            "smooth": smooth, "levels": levels, "ratio": ratio,
        })

    @api.tool(**host)
    def forge_sculpt(name: str, id: str = "", brush: str = "draw", points: list | None = None, radius: float = 20, strength: float | None = None, mirror: str = "", invert: bool = False, shape: str = "", scale: float = 40, seed: int = 0, iterations: int = 2, spacing: float = 0.3) -> dict:
        """Sculpt an object. Stroke brushes along points (world cm, near the surface): draw, clay, inflate, crease (strength = cm at the centre), smooth, flatten, pinch (strength 0-1), grab (drags along the stroke). Whole-mesh brushes need no points: noise (strength cm, scale = feature size cm, seed), smooth_all (iterations, strength 0-1), inflate_all (strength cm). mirror="x" sculpts both sides of the object's own X=0. shape="Smile" sculpts into that shape key instead of the base mesh. Subdivide smooth first so there are vertices to move."""
        return _run(api, name, "sculpt", _given(id=id, brush=brush, points=points, radius=radius, strength=strength, mirror=mirror,
                                                invert=invert or None, shape=shape, scale=scale, seed=seed, iterations=iterations, spacing=spacing))

    @api.tool(**host)
    def forge_paint(
        name: str, id: str = "", mode: str = "blend", channel: str = "r", mask: str = "all", combine: str = "set",
        strength: float = 1.0, threshold: float | None = None, softness: float | None = None, direction: list | None = None,
        low: float | None = None, high: float | None = None, center: list | None = None, radius: float | None = None,
        faces: list | None = None, scale: float | None = None, seed: int = 0, contrast: float | None = None,
        material: str = "", color: list | str | None = None, points: list | None = None, size: int = 1024,
        fill: str = "", grain: float = 0, derive_maps: bool = False,
    ) -> dict:
        """Paint an object. mode="blend": fill channel r|g|b (the blend material's layer 1|2|3) with a procedural mask: all, up (top-facing; threshold, softness), down, facing (direction), height (local z from low to high cm fades out), noise (scale cm, seed, contrast), sphere (center world cm, radius), faces, cavity (creases). combine: set, add, subtract, max, min, multiply. mode="texture": paint the material's base-colour texture through UVs: faces=[...] fills those faces, points=[[x,y,z]...] with radius paints soft dabs, fill="all" starts a new solid texture of size px, grain adds noise. Colours are [r,g,b] 0-1 or "#rrggbb". Unwrap (forge_uv_project mode=smart) before texture painting."""
        return _run(api, name, "paint", _given(
            id=id, mode=mode, channel=channel, mask=mask, combine=combine, strength=strength, threshold=threshold,
            softness=softness, direction=direction, low=low, high=high, center=center, radius=radius, faces=faces,
            scale=scale, seed=seed, contrast=contrast, material=material, color=color, points=points, size=size,
            fill=fill, grain=grain or None, derive_maps=derive_maps or None,
        ))

    @api.tool(**host)
    def forge_shape(name: str, id: str = "", action: str = "add", key: str = "", source: str = "", new_name: str = "") -> dict:
        """Shape keys (morph targets). action: add (empty key to sculpt into with forge_sculpt shape=key), from_object (key = source object minus this one; duplicate, reshape without adding vertices, then use it as source), delete, rename (new_name). Animate with forge_anim key rows {"shape": key, "frame": f, "value": 0-1}. Needs a rig to reach UEFN (forge_rig template=single is enough)."""
        return _run(api, name, "shape", _given(id=id, action=action, key=key, source=source, new_name=new_name))

    @api.tool(**host)
    def forge_uv_transform(name: str, id: str = "", faces: list | None = None, offset: list | None = None, rotate: float = 0, scale: list | None = None) -> dict:
        """Move, rotate (degrees), or scale the UVs of faces (default all) about their UV centre."""
        return _run(api, name, "uv_transform", _given(id=id, faces=faces, offset=offset, rotate=rotate or None, scale=scale))

    @api.tool(**host)
    def forge_material_set(name: str, material: str = "", base_color: list | None = None, metallic: float | None = None, roughness: float | None = None, emissive: list | None = None, emissive_strength: float | None = None, opacity: float | None = None, layers: list | None = None) -> dict:
        """Create or update a PBR material. base_color is 0-1 RGB. layers=[matR, matG, matB] makes it a blend material: forge_paint mode=blend reveals those materials over this one, and Send builds it on M_Forge_Blend."""
        body: dict = {}
        if layers is not None:
            body["layers"] = layers
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
        """Assign a material (name or id) to an object (id, or the selection), or to specific faces."""
        return _run(api, name, "material_assign", {"material": material, "id": id, "faces": faces or []})

    @api.tool(**host)
    def forge_uv_project(name: str, mode: str = "box", density: float = 0.01, id: str = "", faces: list | None = None, angle: float = 66, margin: float = 0.01) -> dict:
        """UVs for the selected object (or id). mode=smart unwraps properly: charts by angle, packed into 0-1 with even texel density and margin; use it before texture painting or baking. box, planar, cylinder project tiling UVs (density = UV units per cm)."""
        return _run(api, name, "uv", _given(mode=mode, density=density, id=id, faces=faces, angle=angle, margin=margin))

    @api.tool(**host)
    def forge_texture_generate(name: str, prompt: str, material: str = "", tileable: bool = True, derive_maps: bool = True) -> dict:
        """Generate a texture with the saved OpenAI or Google key, optionally tileable, with normal and ORM maps."""
        return _run(api, name, "texture", {"prompt": prompt, "material": material, "name": material, "tileable": tileable, "derive_maps": derive_maps})

    @api.tool(**host)
    def forge_texture_import(name: str, path: str, material: str = "") -> dict:
        """Use a local image as a material's base color and derive normal and ORM maps."""
        return _run(api, name, "texture", {"path": path, "material": material, "name": material, "tileable": False, "derive_maps": True})

    @api.tool(**host)
    def forge_render(name: str, views: list | None = None, id: str = "", clip: str = "", frames: list | None = None) -> dict:
        """Render preview PNGs into this chat. Views: three, front (from -Y), side, top. With clip, renders the whole asset posed at each frame (default quarter points) so you can check a motion."""
        if clip:
            return _run(api, name, "render", _given(views=views or ["three"], id=id, clip=clip, frames=frames))
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
        """Write GLB files (metres, Y-up) next to the scene: SM_ statics, plus one SK_ per rig with its skeleton and every clip. Does not import into UEFN."""
        from .scene import load as load_scene

        scene = load_scene(name)
        files = export_glbs(scene, mode, scene.folder / "export")
        skinned = export_skinned(scene, scene.folder / "export")
        return {"files": [{"name": f["name"], "glb": f["glb"], "materials": f["materials"]} for f in files] + skinned}

    @api.tool(**host)
    def forge_batch(name: str, steps: list) -> dict:
        """Run many Forge edits as ONE undo step, in one call. Each step is {"tool": "forge_add", ...that tool's args without name}. "$0" in a later step is step 0's id; "$0.ids" is its id list. The first failing step undoes the whole batch. Allowed: add, edit, duplicate, delete, group, ungroup, layer, join, separate, boolean, modifier, select, move_elements, mesh_op, material_set, material_assign, uv_project, uv_transform, sculpt, paint, shape, rig, anim, fix."""
        return _run(api, name, "batch", {"steps": steps})

    @api.tool(**host)
    def forge_scene_merge(name: str, source: str, offset: list | None = None) -> dict:
        """Copy another Forge scene into this one: objects, groups, meshes, materials, textures, and rigs, moved by offset (cm)."""
        return _run(api, name, "merge_scene", _given(source=source, offset=offset))

    @api.tool(**host)
    def forge_rig(
        name: str, action: str = "template", template: str = "biped", rig: str = "", bone: str = "",
        parent: str = "", head: list | None = None, tail: list | None = None, new_name: str = "",
        ids: list | None = None, auto: bool = False, bones: list | None = None, count: int = 0,
        prefix: str = "", start: list | None = None, end: list | None = None, mode: str = "",
        value: float | None = None, strength: float | None = None, radius: float | None = None,
        points: list | None = None, faces: list | None = None, init: bool = False, normalize: bool = False,
    ) -> dict:
        """Skeleton and skinning. action: template (biped fits the selected objects: UE mannequin bone names, faces -Y, _l on +X, T-pose; chain: count bones from start to end, prefix names them; single: one bone at the base with the objects bound to it), new (empty rig, new_name), add_bone (bone, parent, head, tail), edit_bone (bone, head/tail/parent/new_name), delete_bone, bind (ids or selection; bone=one bone moves the whole object rigidly, auto=true spreads smooth weights over bones or all), weights (painted skin: seeds from the current binding the first time or with init=true; then bone + mode add|subtract|set|smooth with strength 0-1 and value, over points+radius (world cm), faces, or the whole object; normalize=true makes each vertex sum to 1), unbind, delete. Centimetres, Z-up."""
        return _run(api, name, "rig", _given(
            action=action, template=template, rig=rig, bone=bone, parent=parent, head=head, tail=tail,
            new_name=new_name, ids=ids, auto=auto or None, bones=bones, count=count or None, prefix=prefix,
            start=start, end=end, mode=mode, value=value, strength=strength, radius=radius, points=points,
            faces=faces, init=init or None, normalize=normalize or None,
        ))

    @api.tool(**host)
    def forge_anim(
        name: str, action: str = "generate", kind: str = "", clip: str = "", bone: str = "",
        bones: list | None = None, frame: float | None = None, rot: list | None = None, loc: list | None = None,
        scale: list | None = None, keys: list | None = None, interp: str = "", channel: str = "",
        frames: int = 0, fps: int = 0, loop: bool | None = None, amount: float = 0, axis: str = "",
        new_name: str = "", rig: str = "", shape: str = "", value: float | None = None, target: list | None = None,
        chain: int = 2, pole: list | None = None, keep_rotation: bool | None = None, span: list | None = None,
    ) -> dict:
        """Animation clips on a rig. action: generate (kind idle|walk|wave on a biped; sway down a bone chain; spin|bob|swing|open on one bone; amount scales it, axis x|y|z), new (new_name, frames, fps, loop), key (bone, frame, rot degrees / loc cm / scale; shape + value 0-1 keys a shape key; or keys=[{bone, frame, rot} or {shape, frame, value}] for many; interp smooth|linear|step), ik (bone = hand/foot, target = world cm for its head, chain = bones to bend, pole = world point the elbow/knee bends toward, keep_rotation holds the hand/foot orientation; frame, or span=[first, last] to pin it across frames), unkey (bone or shape, optional frame/channel), edit (frames, fps, loop, new_name), delete. Rotations are in the bone's parent frame. Check a motion with forge_render(clip=...)."""
        return _run(api, name, "anim", _given(
            action=action, kind=kind, clip=clip, bone=bone, bones=bones, frame=frame, rot=rot, loc=loc,
            scale=scale, keys=keys, interp=interp, channel=channel, frames=frames or None, fps=fps or None,
            loop=loop, amount=amount or None, axis=axis, new_name=new_name, rig=rig, shape=shape, value=value,
            target=target, chain=chain if action == "ik" else None, pole=pole, keep_rotation=keep_rotation, span=span,
        ))

    @api.tool(listener=True)
    def forge_send_to_uefn(name: str, mode: str = "each", folder: str = "", collision: str = "simple", place: bool = False, location: list | None = None) -> dict:
        """Import the scene into the open UEFN project: SM_ meshes, one SK_ skeletal mesh per rig with AS_ clips, MI_ materials on M_Forge_Master. place=true also puts them in the level (in front of the camera, or at location); rigged assets go on an Animated Mesh device playing their first clip."""
        return send_to_uefn(name, api.listener, mode=mode, folder=folder, collision=collision, place=place, location=location)
