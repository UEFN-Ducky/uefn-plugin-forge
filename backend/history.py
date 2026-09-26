"""Snapshot undo. ponytail: whole-scene copies, capped at 20. Upgrade is a diff log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CAP = 20


def _load(path: Path) -> dict[str, list]:
    if not path.is_file():
        return {"undo": [], "redo": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"undo": [], "redo": []}
    return {"undo": list(data.get("undo") or []), "redo": list(data.get("redo") or [])}


def _save(path: Path, data: dict[str, list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def push(path: Path, state: dict[str, Any]) -> None:
    data = _load(path)
    data["undo"].append(state)
    data["undo"] = data["undo"][-CAP:]
    data["redo"] = []
    _save(path, data)


def undo(path: Path, current: dict[str, Any]) -> dict[str, Any] | None:
    data = _load(path)
    if not data["undo"]:
        return None
    data["redo"].append(current)
    prev = data["undo"].pop()
    _save(path, data)
    return prev


def redo(path: Path, current: dict[str, Any]) -> dict[str, Any] | None:
    data = _load(path)
    if not data["redo"]:
        return None
    data["undo"].append(current)
    nxt = data["redo"].pop()
    _save(path, data)
    return nxt


def revert_to(path: Path, current: dict[str, Any], rev: int) -> dict[str, Any] | None:
    """Restore the newest snapshot whose rev is <= rev, dropping later history."""
    data = _load(path)
    stack = data["undo"]
    found = None
    keep: list = []
    for state in stack:
        if int(state.get("rev") or 0) <= int(rev):
            keep.append(state)
            found = state
    if found is None:
        return None
    # The matching snapshot becomes current, so it leaves the undo stack.
    if keep and keep[-1] is found:
        keep.pop()
    data["undo"] = keep
    data["redo"] = []
    _save(path, data)
    return found
