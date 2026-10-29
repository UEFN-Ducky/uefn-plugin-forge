"""Panel RPCs for the Forge dock. The UI and the agent call the same scene ops."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .scene import dispatch, scenes_root
from .uefn import send_to_uefn


def forge_call(op: str = "", name: str = "", params: dict | None = None, actor: str = "user", **_kwargs: Any) -> dict:
    try:
        result = dispatch(str(name or ""), str(op or ""), dict(params or {}), actor=str(actor or "user"))
        result["ok"] = True
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def forge_texture(name: str = "", file: str = "", full: bool = False, **_kwargs: Any) -> dict:
    """full=true for texture paint, which writes the image back and must never lose resolution."""
    fname = Path(str(file or "")).name
    if not fname or fname in {".", ".."}:
        return {"ok": False, "error": "file required"}
    from .scene import _safe
    root = (scenes_root() / _safe(str(name or "")) / "textures").resolve()
    path = (root / fname).resolve()
    if not path.is_relative_to(root):
        return {"ok": False, "error": "texture path outside scene"}
    if not path.is_file():
        return {"ok": False, "error": "texture not found"}
    from .scene import dock_texture
    mime, data = ("image/png", path.read_bytes()) if full else dock_texture(path)
    return {"ok": True, "mime": mime, "base64": base64.b64encode(data).decode("ascii")}


def forge_projects(action: str = "list", name: str = "", new_name: str = "", scene: str = "", targets: Any = None,
                   confirm: bool = False, cards: bool = False, **_kwargs: Any) -> dict:
    """Dock project picker: forge_project's actions plus `active` (the dock's current project).
    list with cards=true returns every project with modified + thumb (the picker's grid); delete_scene takes scene=."""
    from . import projects
    try:
        if cards and (action or "list") == "list":
            return {"ok": True, "active": projects.active(), "projects": projects.list_projects(cards=True)}
        return {"ok": True, **projects.act(action, name, new_name, scene, targets, bool(confirm))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def forge_scene_thumb(name: str = "", data_url: str = "", **_kwargs: Any) -> dict:
    """scene_thumb_set: the dock posts a 320x180 JPEG data URL of the viewport for the picker card."""
    from .scene import set_thumb
    try:
        return {"ok": True, **set_thumb(str(name or ""), str(data_url or ""))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def forge_library(action: str = "list", kind: str = "", id: str = "", q: str = "", scene: str = "", project: str = "",
                  **kwargs: Any) -> dict:
    """Library panel: list -> {items:[{id,name,kind,thumb}]} (thumb is a data URL, '' when none); the rest is library.act."""
    from . import library
    try:
        if (action or "list").lower() != "list":
            args = {k: v for k, v in kwargs.items() if k in ("name", "from_scene", "object", "path", "pos", "force")}
            return {"ok": True, **library.act(action, kind, id, scene=scene, project=project, **args)}
        kind = (kind or "").lower()
        lib = library._lib(library._project(scene, project=project))
        idx = library._index(lib)
        needle = (q or "").strip().lower()
        items, made = [], 0
        for iid, item in idx["items"].items():
            name = item.get("name") or iid
            if (kind and item["kind"] != kind) or (needle and needle not in iid.lower() and needle not in str(name).lower()):
                continue
            thumb = lib / "thumbs" / (iid + ".png")
            if not thumb.is_file() and item["kind"] != "rig" and made < 6:  # a few per call: the panel asks again
                made += 1
                try:
                    library.act("thumb", id=iid, scene=scene, project=project)
                except Exception:
                    pass
            data = "data:image/png;base64," + base64.b64encode(thumb.read_bytes()).decode("ascii") if thumb.is_file() else ""
            items.append({"id": iid, "name": name, "kind": item["kind"], "thumb": data})
        return {"ok": True, "items": items[:60], "total": len(items)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def forge_submit(method: str = "forge_call", params: dict | None = None, **_kwargs) -> dict:
    from .jobs import submit
    methods = {"forge_call": forge_call, "forge_texture": forge_texture}
    fn = methods.get(method)
    if fn is None:
        return {"ok": False, "error": "Unknown Forge job method"}
    return submit(fn, dict(params or {}))


def forge_job(job: str = "", **_kwargs) -> dict:
    from .jobs import poll
    return poll(str(job))


def forge_shot_render_start(name: str = "", shot: str = "", fps: int = 30, width: int = 1920, height: int = 1080, **_kwargs: Any) -> dict:
    """The Shots panel's Render button: start the job and return; the panel draws its frames from forge_live."""
    from . import live_view
    try:
        job = live_view.start_render(str(name), str(shot), int(fps or 30), int(width or 1920), int(height or 1080))
        return {"ok": True, **live_view.job_result(job)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def forge_engine(action: str = "status", **_kwargs: Any) -> dict:
    """Dock engine chip: status, or install (also the Retry after an error)."""
    from .engine import runtime
    status = runtime.install() if action == "install" else runtime.engine_status()
    return {"ok": True, **status}


def register_panel_rpcs(api: Any) -> None:
    from . import live_view
    from .engine import client

    def forge_send_bound(name: str = "", mode: str = "each", folder: str = "", collision: str = "simple", place: bool = False, **_kwargs: Any) -> dict:
        try:
            result = send_to_uefn(str(name), api.listener, mode=mode, folder=folder, collision=collision, place=bool(place))
            result["ok"] = True
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    api.register_panel_rpc("forge_submit", forge_submit)
    from .jobs import cancel
    api.register_panel_rpc("forge_cancel", cancel)
    api.register_panel_rpc("forge_job", forge_job)
    api.register_panel_rpc("forge_call", forge_call)
    api.register_panel_rpc("forge_send", forge_send_bound)
    api.register_panel_rpc("forge_texture", forge_texture)
    api.register_panel_rpc("forge_projects", forge_projects)
    api.register_panel_rpc("scene_thumb_set", forge_scene_thumb)
    api.register_panel_rpc("forge_library", forge_library)
    api.register_panel_rpc("forge_live", live_view.live_poll)
    api.register_panel_rpc("forge_view_put", live_view.view_put)
    api.register_panel_rpc("forge_frame_put", live_view.frame_put)
    api.register_panel_rpc("forge_shot_render_start", forge_shot_render_start)
    api.register_panel_rpc("forge_engine", forge_engine)
    client.warm()
