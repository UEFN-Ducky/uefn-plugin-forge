"""MCP tools. Same scene functions the panel calls."""

from __future__ import annotations

from typing import Any

import json

from . import live_view
from .hq import compact
from .scene import dispatch, load as load_scene
from .uefn import export_glbs, export_skinned, send_to_uefn


def _given(**kwargs: Any) -> dict:
    """Only the arguments the caller actually set, so op defaults stay in one place."""
    return {k: v for k, v in kwargs.items() if v is not None and v != "" and v != []}


# Reads return what the agent asked to see; every edit returns ok, summary, ids, stats, warnings, path.
_FULL = {"scene_list", "scene_new", "scene_get", "stats", "scene_payload", "check", "render", "rig_preview",
         "scene_import_preview", "cameras"}


def _brief(op: str, result: dict) -> dict:
    if op != "batch":
        return compact(result)
    ids = list(result.get("ids") or [])
    small = compact({k: v for k, v in result.items() if k != "ids"})
    small["ids"] = ids
    while len(json.dumps(small)) > 600 and len(ids) > 1:  # batch budget: 600 chars
        ids = ids[:len(ids) // 2]
        small["ids"] = ids + ["+%d" % (len(result["ids"]) - len(ids))]
    return small


def _look(name: str, look: Any) -> dict:
    """One tiled image and a seen line for the scene after an edit."""
    got = _attach_captures(dispatch(name, "look", {"look": look}, actor="agent"))
    out = {"seen": got.get("seen", "")}
    if got.get("path") or got.get("paths"):
        out["path"] = got.get("path") or got["paths"][0]
    if got.get("error"):
        out["look_error"] = got["error"][:120]
    return out


# ponytail: one "last scene" per Forge process, not per chat; key it by chat id if two chats share one Forge.
_LAST = {"name": ""}


def _scene_name(name: str) -> str:
    """name, or the scene the agent last worked in."""
    name = name or _LAST["name"]
    if not name:
        raise ValueError("Pass name: the Forge scene to work in (forge_scene_list shows them, forge_scene_new makes one)")
    return name


def _rig_for_send(name: str) -> list[str]:
    """Shape keys only reach UEFN on a skeletal mesh: keyed objects with no rig get template=single first."""
    from .scene import load

    scene = load(name)
    keyed = [n["id"] for n in scene.data["nodes"] if n.get("type") == "object" and not n.get("skin")
             and any(a.startswith("sk:") for a in scene.local_mesh(n).attrs)]
    if not keyed:
        return []
    if scene.data.get("rigs"):
        return [f"shape keys on {', '.join(keyed[:4])} need a rig: forge_rig(action='bind', ids=[...]) and send again"]
    dispatch(name, "rig", {"action": "template", "template": "single", "ids": keyed}, actor="agent")
    return ["added rig template=single so the shape keys reach UEFN"]


def _run(api: Any, name: str, op: str, params: dict | None = None, look: Any = "") -> dict:
    params = dict(params or {})
    if op not in {"scene_list", "scene_new", "scene_import_preview"}:
        name = _scene_name(name)
    verbose = bool(params.pop("verbose", False)) if op != "hq" else True  # forge_hq compacts itself
    before = None
    if op not in {"scene_list", "scene_new", "scene_get", "stats", "scene_payload", "check", "render", "rig_preview", "scene_import_preview"}:
        try:
            before = load_scene(name).data.get("rev")  # the session-cached Scene: no second dispatch per call
        except Exception:
            before = None
    if op == "scene_list":
        result = dispatch("", "scene_list", {}, actor="agent")
    elif op == "scene_new":
        result = dispatch("", "scene_new", params, actor="agent")
        name = str(result.get("name") or name)
    else:
        result = dispatch(name, op, params, actor="agent")
    if name:
        _LAST["name"] = name
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
    if name and op not in {"scene_list", "scene_new", "cameras"}:
        live_view.note_activity(name, str(result.get("summary") or op), list(result.get("ids") or []))
    result = result if verbose or op in _FULL else _brief(op, result)
    return {**result, **_look(name, look)} if look and name else result


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
    live_view.bind(api)
    from . import library
    library.register(api)

    @api.tool(**host)
    def forge_import_mesh(path: str, name: str = "", destination: str = "", scale: float = 1.0, preview: bool = False) -> dict:
        """Import .blend/.fbx/.glb/.gltf as a scene, or into destination; preview=true only inspects."""
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
    def forge_scene_list(project: str = "") -> dict:
        """List Forge scenes of a project (default: the active one)."""
        return _run(api, "", "scene_list", {"project": project})

    @api.tool(**host)
    def forge_scene_new(name: str, project: str = "") -> dict:
        """Create an empty Forge scene (in project, default the active one)."""
        return _run(api, name, "scene_new", {"name": name, "project": project})

    @api.tool(**host)
    def forge_project(action: str = "list", name: str = "", new_name: str = "", scene: str = "",
                      targets: list[str] | None = None, confirm: bool = False) -> dict:
        """Projects hold scenes and a shared library: action=list|new|open|rename|delete(confirm=true)|move_scene."""
        from . import projects
        try:
            return projects.act(action, name, new_name, scene, targets, confirm)
        except ValueError as exc:
            return {"error": str(exc)[:280]}

    @api.tool(**host)
    def forge_scene_get(name: str = "") -> dict:
        """Scene tree, selection, materials, bounds. Read-only."""
        return _run(api, name, "scene_get")

    @api.tool(**host)
    def forge_undo(name: str = "") -> dict:
        """Undo the last edit."""
        return _run(api, name, "undo")

    @api.tool(**host)
    def forge_redo(name: str = "") -> dict:
        """Redo the last undone edit."""
        return _run(api, name, "redo")

    @api.tool(**host)
    def forge_add(name: str = "", kind: str = "box", params: dict | None = None, pos: list | None = None, material: str = "", layer: str = "default", object_name: str = "", count: int = 1, rot: list | None = None, scale: list | None = None, look: Any = "") -> dict:
        """Add box|rounded_box|plane|grid|sphere|cylinder|cone|torus|capsule|wedge|stairs|arch|lathe|extrude. cm, pos=base centre, rot deg, count=instances."""
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
        return _run(api, name, "add", body, look)

    @api.tool(**host)
    def forge_edit(name: str = "", id: str = "", pos: list | None = None, rot: list | None = None, scale: list | None = None, params: dict | None = None, layer: str = "", object_name: str = "", parent: str = "", locked: bool | None = None, look: Any = "") -> dict:
        """Edit transform, params, name, layer, parent or lock. Rot degrees."""
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
        return _run(api, name, "edit", body, look)

    @api.tool(**host)
    def forge_duplicate(name: str = "", id: str = "", offset: list | None = None, look: Any = "") -> dict:
        """Duplicate an object (offset cm, default [20,0,0])."""
        return _run(api, name, "duplicate", {"id": id, "offset": offset or [20, 0, 0]}, look)

    @api.tool(**host)
    def forge_delete(name: str = "", ids: list | None = None, look: Any = "") -> dict:
        """Delete objects (default: selection)."""
        return _run(api, name, "delete", {"ids": ids or []}, look)

    @api.tool(**host)
    def forge_group(name: str = "", ids: list | None = None, object_name: str = "Group", into: str = "", look: Any = "") -> dict:
        """Group ids under a new group, or into an existing group."""
        body: dict = {"ids": ids or [], "name": object_name, "combine": True}
        if into:
            body["into"] = into
        return _run(api, name, "group", body, look)

    @api.tool(**host)
    def forge_ungroup(name: str = "", id: str = "") -> dict:
        """Remove a group, keeping its children."""
        return _run(api, name, "ungroup", {"id": id})

    @api.tool(**host)
    def forge_layer(name: str = "", action: str = "add", id: str = "", object_name: str = "", ids: list | None = None, visible: bool | None = None, color: str = "") -> dict:
        """Layers: add, show/hide (visible), assign ids. Collision exports as UCX."""
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
    def forge_join(name: str = "", ids: list | None = None, weld: bool = False, look: Any = "") -> dict:
        """Merge objects into one mesh; weld fuses verts closer than 0.05 cm."""
        return _run(api, name, "join", {"ids": ids or [], "weld": weld}, look)

    @api.tool(**host)
    def forge_separate(name: str = "", id: str = "") -> dict:
        """Split an object into one mesh per material."""
        return _run(api, name, "separate", {"id": id})

    @api.tool(**host)
    def forge_boolean(name: str = "", a: str = "", b: str | list = "", op: str = "difference", look: Any = "") -> dict:
        """Solid boolean difference|union|intersect; b may be a list."""
        return _run(api, name, "boolean", {"a": a, "b": b, "op": op}, look)

    @api.tool(**host)
    def forge_modifier(name: str = "", action: str = "add", type: str = "mirror", axis: str = "x", count: int = 3, offset: list | None = None, look: Any = "") -> dict:
        """Mirror or array modifier: add|apply|clear."""
        return _run(api, name, "modifier", {"action": action, "type": type, "axis": axis, "count": count, "offset": offset or [120, 0, 0]}, look)

    @api.tool(**host)
    def forge_select(name: str = "", object: str = "", query: str = "", faces: list | None = None, verts: list | None = None, edges: list | None = None, mode: str = "", select_mode: str = "") -> dict:
        """Set the selection (object, faces, verts, edges); query='top' picks upward faces."""
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
    def forge_move_elements(name: str = "", delta: list | None = None, kind: str = "face", ids: list | None = None) -> dict:
        """Move selected verts|edges|faces by a cm delta."""
        return _run(api, name, "move", {"delta": delta or [0, 0, 0], "kind": kind, "ids": ids or []})

    @api.tool(**host)
    def forge_mesh_op(name: str = "", op: str = "", distance: float = 10, amount: float = 5, width: float = 4, segments: int = 1, faces: list | None = None, edges: list | None = None, smooth: bool = False, levels: int = 1, ratio: float = 0.5, look: Any = "") -> dict:
        """extrude|inset|bevel|loopcut|subdivide|decimate|fill|merge|dissolve|delete|normals|shade|origin. See forge/modeling_recipes."""
        return _run(api, name, "mesh_op", {
            "op": op, "distance": distance, "amount": amount, "width": width,
            "segments": segments, "faces": faces or [], "edges": edges or [],
            "smooth": smooth, "levels": levels, "ratio": ratio,
        }, look)

    @api.tool(**host)
    def forge_sculpt(name: str = "", id: str = "", brush: str = "draw", points: list | None = None, radius: float = 20, strength: float | None = None, mirror: str = "", invert: bool = False, shape: str = "", scale: float = 40, seed: int = 0, iterations: int = 2, spacing: float = 0.3, look: Any = "") -> dict:
        """Sculpt draw|clay|inflate|crease|smooth|flatten|pinch|grab along points; noise|smooth_all|inflate_all. See forge/sculpt_shapes."""
        return _run(api, name, "sculpt", _given(id=id, brush=brush, points=points, radius=radius, strength=strength, mirror=mirror,
                                                invert=invert or None, shape=shape, scale=scale, seed=seed, iterations=iterations, spacing=spacing), look)

    @api.tool(**host)
    def forge_paint(
        name: str = "", id: str = "", mode: str = "blend", channel: str = "r", mask: str = "all", combine: str = "set",
        strength: float = 1.0, threshold: float | None = None, softness: float | None = None, direction: list | None = None,
        low: float | None = None, high: float | None = None, center: list | None = None, radius: float | None = None,
        faces: list | None = None, scale: float | None = None, seed: int = 0, contrast: float | None = None,
        material: str = "", color: list | str | None = None, points: list | None = None, size: int = 1024,
        fill: str = "", grain: float = 0, derive_maps: bool = False, look: Any = "",
    ) -> dict:
        """Paint mode=blend (channel r|g|b, mask up|down|facing|height|noise|sphere|faces|cavity) or texture (faces|points|fill). See forge/paint_uv."""
        return _run(api, name, "paint", _given(
            id=id, mode=mode, channel=channel, mask=mask, combine=combine, strength=strength, threshold=threshold,
            softness=softness, direction=direction, low=low, high=high, center=center, radius=radius, faces=faces,
            scale=scale, seed=seed, contrast=contrast, material=material, color=color, points=points, size=size,
            fill=fill, grain=grain or None, derive_maps=derive_maps or None,
        ), look)

    @api.tool(**host)
    def forge_shape(name: str = "", id: str = "", action: str = "add", key: str = "", source: str = "", new_name: str = "", look: Any = "") -> dict:
        """Shape keys: add|from_object|delete|rename. See forge/sculpt_shapes."""
        return _run(api, name, "shape", _given(id=id, action=action, key=key, source=source, new_name=new_name), look)

    @api.tool(**host)
    def forge_uv_transform(name: str = "", id: str = "", faces: list | None = None, offset: list | None = None, rotate: float = 0, scale: list | None = None) -> dict:
        """Move, rotate (deg) or scale face UVs (default all)."""
        return _run(api, name, "uv_transform", _given(id=id, faces=faces, offset=offset, rotate=rotate or None, scale=scale))

    @api.tool(**host)
    def forge_material_set(name: str = "", material: str = "", base_color: list | None = None, metallic: float | None = None, roughness: float | None = None, emissive: list | None = None, emissive_strength: float | None = None, opacity: float | None = None, layers: list | None = None, look: Any = "") -> dict:
        """Create/update a PBR material (0-1 RGB); layers=[R,G,B mats] makes a blend material. See forge/paint_uv."""
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
        return _run(api, name, "material_set", body, look)

    @api.tool(**host)
    def forge_material_assign(name: str = "", material: str = "", id: str = "", faces: list | None = None, look: Any = "") -> dict:
        """Assign a material to an object (default: selection) or faces."""
        return _run(api, name, "material_assign", {"material": material, "id": id, "faces": faces or []}, look)

    @api.tool(**host)
    def forge_uv_project(name: str = "", mode: str = "box", density: float = 0.01, id: str = "", faces: list | None = None, angle: float = 66, margin: float = 0.01, look: Any = "") -> dict:
        """UVs: smart (unwrap for paint/bake)|box|planar|cylinder. See forge/paint_uv."""
        return _run(api, name, "uv", _given(mode=mode, density=density, id=id, faces=faces, angle=angle, margin=margin), look)

    @api.tool(**host)
    def forge_texture_generate(name: str = "", prompt: str = "", material: str = "", tileable: bool = True, derive_maps: bool = True) -> dict:
        """AI-generate a texture (tileable) with normal and ORM maps."""
        return _run(api, name, "texture", {"prompt": prompt, "material": material, "name": material, "tileable": tileable, "derive_maps": derive_maps})

    @api.tool(**host)
    def forge_texture_import(name: str = "", path: str = "", material: str = "") -> dict:
        """Use a local image as base color; derives normal and ORM."""
        return _run(api, name, "texture", {"path": path, "material": material, "name": material, "tileable": False, "derive_maps": True})

    @api.tool(**host)
    def forge_render(name: str = "", views: list | None = None, id: str = "", clip: str = "", frames: list | None = None,
                     style: str = "pbr") -> dict:
        """Render PNGs: three|front|side|top|left|back|three_back|az0..az315; clip= renders frames of a motion; style=pbr|toon|wire|islands|silhouette|checker|xray."""
        if clip:
            return _run(api, name, "render", _given(views=views or ["three"], id=id, clip=clip, frames=frames, style=style))
        return _run(api, name, "render", {"views": views or ["three", "front", "top"], "id": id, "style": style})

    @api.tool(**host)
    def forge_check(name: str = "") -> dict:
        """Report holes, flipped faces, missing UVs, tri budget, pivot, scale."""
        return _run(api, name, "check")

    @api.tool(**host)
    def forge_fix(name: str = "", issues: str = "all", id: str = "", look: Any = "") -> dict:
        """Auto-fix safe check issues."""
        return _run(api, name, "fix", {"issues": issues, "id": id}, look)

    @api.tool(**host)
    def forge_stats(name: str = "") -> dict:
        """Triangle counts and bounds (cm)."""
        return _run(api, name, "stats")

    @api.tool(**host)
    def forge_export(name: str = "", mode: str = "each") -> dict:
        """Write GLBs: SM_ statics, SK_ per rig with clips. No UEFN import. See forge/send_to_game."""
        from .scene import load as load_scene

        scene = load_scene(_scene_name(name))
        files = export_glbs(scene, mode, scene.folder / "export")
        skinned = export_skinned(scene, scene.folder / "export")
        return {"files": [{"name": f["name"], "glb": f["glb"], "materials": f["materials"]} for f in files] + skinned}

    @api.tool(**host)
    def forge_batch(name: str = "", steps: list | None = None, verbose: bool = False, look: Any = "") -> dict:
        """Many edits as one undo step: steps=[{tool, ...args}], "$0"/"$0.ids" refs; look=three|ai|ids|{view,style:toon|wire|islands|silhouette|checker|xray,px:128..1024} adds 1 image. See forge/modeling_recipes."""
        return _run(api, name, "batch", {"steps": steps, "verbose": verbose}, look)

    @api.tool(**host)
    def forge_hq(name: str = "", op: str = "help", id: str = "", params: dict | None = None, look: Any = "") -> dict:
        """Blender-engine HQ op on an object (remesh, bake, hair...); op="help" lists them. See forge/hq."""
        return _run(api, name, "hq", _given(op=op, id=id, params=params), look)

    @api.tool(**host)
    def forge_scene_merge(name: str = "", source: str = "", offset: list | None = None, look: Any = "") -> dict:
        """Copy another scene into this one, moved by offset cm."""
        return _run(api, name, "merge_scene", _given(source=source, offset=offset), look)

    @api.tool(**host)
    def forge_rig(
        name: str = "", action: str = "template", template: str = "biped", rig: str = "", bone: str = "",
        parent: str = "", head: list | None = None, tail: list | None = None, new_name: str = "",
        ids: list | None = None, auto: bool = False, bones: list | None = None, count: int = 0,
        prefix: str = "", start: list | None = None, end: list | None = None, mode: str = "",
        value: float | None = None, strength: float | None = None, radius: float | None = None,
        points: list | None = None, faces: list | None = None, init: bool = False, normalize: bool = False, look: Any = "",
    ) -> dict:
        """Skeleton and skinning: template|new|add_bone|edit_bone|delete_bone|bind|weights|unbind|delete. See forge/rigging."""
        return _run(api, name, "rig", _given(
            action=action, template=template, rig=rig, bone=bone, parent=parent, head=head, tail=tail,
            new_name=new_name, ids=ids, auto=auto or None, bones=bones, count=count or None, prefix=prefix,
            start=start, end=end, mode=mode, value=value, strength=strength, radius=radius, points=points,
            faces=faces, init=init or None, normalize=normalize or None,
        ), look)

    @api.tool(**host)
    def forge_anim(
        name: str = "", action: str = "generate", kind: str = "", clip: str = "", bone: str = "",
        bones: list | None = None, frame: float | None = None, rot: list | None = None, loc: list | None = None,
        scale: list | None = None, keys: list | None = None, interp: str = "", channel: str = "",
        frames: int = 0, fps: int = 0, loop: bool | None = None, amount: float = 0, axis: str = "",
        new_name: str = "", rig: str = "", shape: str = "", value: float | None = None, target: list | None = None,
        chain: int = 2, pole: list | None = None, keep_rotation: bool | None = None, span: list | None = None, look: Any = "",
    ) -> dict:
        """Clips on a rig: generate|new|key|ik|unkey|edit|delete. See forge/animation."""
        return _run(api, name, "anim", _given(
            action=action, kind=kind, clip=clip, bone=bone, bones=bones, frame=frame, rot=rot, loc=loc,
            scale=scale, keys=keys, interp=interp, channel=channel, frames=frames or None, fps=fps or None,
            loop=loop, amount=amount or None, axis=axis, new_name=new_name, rig=rig, shape=shape, value=value,
            target=target, chain=chain if action == "ik" else None, pole=pole, keep_rotation=keep_rotation, span=span,
        ), look)

    @api.tool(**host)
    def forge_camera(name: str = "", action: str = "get", pos: list | None = None, target: list | None = None, fov: float | None = None,
                     seconds: float = 1.0, ids: list | None = None, drive: bool = False, doing: str = "", camera_name: str = "") -> dict:
        """Your camera: get|goto|look_at|frame|orbit|people|ping|save|cameras; drive=true moves the viewport. See forge/camera."""
        name = _scene_name(name)
        result = live_view.camera(name, action, pos=pos, target=target, fov=fov, seconds=seconds, ids=ids, drive=drive,
                                  doing=doing, camera_name=camera_name)
        if action == "save":
            api.changeset.record(command="forge_camera", kind="forge_scene", ident=name, summary=str(result.get("summary") or "camera"),
                                 before=None, revertable="manual", reason="Use forge_undo to step back. Forge keeps its own history.")
        return result

    @api.tool(**host)
    def forge_view(name: str = "", camera: Any = "", width: int = 960, height: int = 540, clean: bool = False) -> dict:
        """Scene picture via the open Forge tab; camera ai|viewport|saved name|{pos,target,fov}."""
        name = _scene_name(name)
        result = live_view.view(name, camera, width, height, clean)
        live_view.note_activity(name, "looking", [])
        return _attach_captures(result) if result.get("ok") else result

    @api.tool(**host)
    def forge_shot(name: str = "", action: str = "add", shot: str = "", shot_name: str = "", t: float | None = None,
                   pos: list | None = None, target: list | None = None, fov: float | None = None, from_view: str = "",
                   duration: float | None = None, ease: str = "", camera: str = "") -> dict:
        """Keyed camera shots: add|key|unkey|edit|delete. See forge/camera."""
        name = _scene_name(name)
        params = _given(action=action, shot=shot, shot_name=shot_name, t=t, pos=pos, target=target, fov=fov,
                        duration=duration, ease=ease)
        if camera:
            params["camera"] = camera
        if from_view:
            params.update(live_view.resolve_pose(name, from_view))
        return _run(api, name, "shot", params)

    @api.tool(**host)
    def forge_shot_render(name: str = "", shot: str = "", fps: int = 30, width: int = 1920, height: int = 1080, imk_project: str = "",
                          job: str = "", wait_seconds: float = 300) -> dict:
        """Render a shot to MP4 via the open Forge tab; returns job when slow, call again with job."""
        name = _scene_name(name)
        result = live_view.render_shot(name, shot, fps, width, height, imk_project, job, wait_seconds)
        if not job:
            live_view.note_activity(name, "filming " + str(result.get("shot") or shot), [])
        return result

    @api.tool(listener=True)
    def forge_send_to_uefn(name: str = "", mode: str = "each", folder: str = "", collision: str = "simple", place: bool = False, location: list | None = None, verbose: bool = False) -> dict:
        """Import into UEFN: SM_, SK_ + AS_, MI_; place=true puts them in the level. See forge/send_to_game."""
        name = _scene_name(name)
        warnings = _rig_for_send(name)
        result = send_to_uefn(name, api.listener, mode=mode, folder=folder, collision=collision, place=place, location=location,
                              verbose=verbose)
        return {**result, "warnings": [*(result.get("warnings") or []), *warnings]} if warnings else result
