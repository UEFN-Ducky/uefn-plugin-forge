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
    path = scenes_root() / str(name or "") / "textures" / fname
    if not path.is_file():
        return {"ok": False, "error": "texture not found"}
    return {"ok": True, "mime": "image/png", "base64": base64.b64encode(path.read_bytes()).decode("ascii")}


def register_panel_rpcs(api: Any) -> None:
    def forge_send_bound(name: str = "", mode: str = "each", folder: str = "", collision: str = "simple", **_kwargs: Any) -> dict:
        try:
            result = send_to_uefn(str(name), api.listener, mode=mode, folder=folder, collision=collision)
            result["ok"] = True
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    api.register_panel_rpc("forge_call", forge_call)
    api.register_panel_rpc("forge_send", forge_send_bound)
    api.register_panel_rpc("forge_texture", forge_texture)
