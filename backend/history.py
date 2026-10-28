"""Bounded undo index: transforms are small patches, mesh snapshots live separately.
Legacy history.json is read only when undo reaches pre-upgrade edits.
"""
from __future__ import annotations
import copy
import json
import secrets
from pathlib import Path
from typing import Any
from . import storage
CAP = 20

def _index(path):
    return path.with_name("history-index.json")

_CACHE = {}  # index path -> (mtime_ns, data): read once per session, not on every edit

def _load(path):
    index = _index(path)
    try:
        stamp = index.stat().st_mtime_ns
    except OSError:
        return {"undo": [], "redo": [], "legacy": path.is_file(), "added": 0}
    hit = _CACHE.get(str(index))
    if hit is not None and hit[0] == stamp:
        return copy.deepcopy(hit[1])
    data = json.loads(index.read_text(encoding="utf-8"))
    _CACHE[str(index)] = (stamp, copy.deepcopy(data))
    return data

def _files(data):
    return {e.get("file") for stack in (data["undo"], data["redo"]) for e in stack} - {None}

def _save(path, data):
    index = _index(path)
    hit = _CACHE.get(str(index))
    if hit is None:
        index.parent.mkdir(parents=True, exist_ok=True)
    tmp = index.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(index)
    _CACHE[str(index)] = (index.stat().st_mtime_ns, copy.deepcopy(data))
    folder = path.parent / "history-snapshots"
    keep = _files(data)
    # Known index: unlink just the snapshots that fell off the stacks. The first save of a session sweeps strays.
    if hit is not None:
        stale = [folder / name for name in _files(hit[1]) - keep]
    else:
        stale = [item for item in (folder.glob("*.json") if folder.is_dir() else []) if item.name not in keep]
    for item in stale:
        try:
            item.unlink()
        except OSError:
            pass  # Stale snapshot cleanup must not fail a committed edit.

def _snapshot(path, state):
    folder = path.parent / "history-snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(12) + ".json"
    (folder / name).write_text(json.dumps(storage.pack(state, path.parent)), encoding="utf-8")
    return {"file": name, "rev": state.get("rev", 0)}

def _push(path, entry):
    data = _load(path)
    data["undo"] = (data["undo"] + [entry])[-CAP:]
    data["redo"] = []
    data["added"] = min(CAP, data.get("added", 0) + 1)
    if data["added"] == CAP:
        data["legacy"] = False
    _save(path, data)

def push(path: Path, state: dict[str, Any]) -> None:
    _push(path, _snapshot(path, state))

def push_transform(path, nid, transform, rev):
    _push(path, {"transform": copy.deepcopy(transform), "id": nid, "rev": rev})

def _legacy(path, data):
    if not data.get("legacy"):
        return
    old = json.loads(path.read_text(encoding="utf-8"))
    count = max(0, CAP - data.get("added", 0))
    rows = old.get("undo", [])
    data["undo"] = [{"legacy_index": i, "rev": rows[i].get("rev", 0)}
                    for i in range(max(0, len(rows)-count), len(rows))] + data["undo"]
    if not data.get("added") and not data["redo"]:
        data["redo"] = [{"legacy_index": i, "legacy_stack": "redo", "rev": e.get("rev", 0)}
                        for i,e in enumerate(old.get("redo", []))][-CAP:]
    data["legacy"] = False

def _resolve(path, entry):
    if "transform" in entry:
        return {"_transform_patch": copy.deepcopy(entry)}
    if "legacy_index" in entry:
        return json.loads(path.read_text(encoding="utf-8"))[entry.get("legacy_stack", "undo")][entry["legacy_index"]]
    return storage.unpack(json.loads((path.parent / "history-snapshots" / entry["file"]).read_text(encoding="utf-8")), path.parent)

def _inverse(path, entry, current):
    if "transform" in entry:
        node = next(n for n in current["nodes"] if n["id"] == entry["id"])
        return {"id": node["id"], "transform": copy.deepcopy(node["transform"]), "rev": current.get("rev", 0)}
    return _snapshot(path, current)

def _step(path, current, source, dest):
    data = _load(path)
    if not data[source] and (source == "undo" or not data.get("added")):
        _legacy(path, data)
    if not data[source]:
        return None
    entry = data[source][-1]
    result = _resolve(path, entry)
    inverse = _inverse(path, entry, current)
    data[source].pop()
    data[dest] = (data[dest] + [inverse])[-CAP:]
    _save(path, data)
    return result

def undo(path, current):
    return _step(path, current, "undo", "redo")

def redo(path, current):
    return _step(path, current, "redo", "undo")

def revert_to(path, current, rev):
    data = _load(path)
    _legacy(path, data)
    candidates = [i for i,e in enumerate(data["undo"]) if int(e.get("rev",0)) <= int(rev)]
    if not candidates:
        return None
    target = max(candidates)
    state = current
    for entry in reversed(data["undo"][target:]):
        restored = _resolve(path, entry)
        if "_transform_patch" in restored:
            patch = restored["_transform_patch"]
            state = dict(state, nodes=[dict(n, transform=patch["transform"]) if n["id"] == patch["id"] else n for n in state["nodes"]])
        else:
            state = restored
    data["undo"] = data["undo"][:target]
    data["redo"] = []
    _save(path, data)
    return state
