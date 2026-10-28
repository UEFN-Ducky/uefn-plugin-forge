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


def forge_texture(name: str = "", file: str = "", **_kwargs: Any) -> dict:
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
    return {"ok": True, "mime": "image/png", "base64": base64.b64encode(path.read_bytes()).decode("ascii")}


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
    api.register_panel_rpc("forge_live", live_view.live_poll)
    api.register_panel_rpc("forge_view_put", live_view.view_put)
    api.register_panel_rpc("forge_frame_put", live_view.frame_put)
    api.register_panel_rpc("forge_shot_render_start", forge_shot_render_start)
    api.register_panel_rpc("forge_engine", forge_engine)
    client.warm()
