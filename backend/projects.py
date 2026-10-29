"""Forge projects: forge/projects/<Project>/{project.json, scenes/<Scene>/, library/}.

A project is just a folder of scenes plus a shared library; it is tied to no engine (`targets` are free tags).
Listing reads project.json and the first bytes of each scene.json, never the meshes.
FORGE_SCENES_DIR keeps today's flat layout for tests (projects then live in <dir>/_projects);
FORGE_PROJECTS_DIR forces project mode in a temp folder; FORGE_HOME moves the whole forge folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

DEFAULT = "My Scenes"
_DONE: set[str] = set()


def _base() -> Path:
    env = os.environ.get("FORGE_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "UEFN-Ducky" / "forge"


def flat_mode() -> bool:
    return not os.environ.get("FORGE_PROJECTS_DIR") and bool(os.environ.get("FORGE_SCENES_DIR"))


def projects_root() -> Path:
    env = os.environ.get("FORGE_PROJECTS_DIR")
    if env:
        return Path(env)
    flat = os.environ.get("FORGE_SCENES_DIR")
    return Path(flat) / "_projects" if flat else _base() / "projects"


def safe_project(name: str) -> str:
    cleaned = re.sub(r"[^\w\- ]", "_", (name or "").strip()).strip()
    if not cleaned:
        raise ValueError("project name required")
    return cleaned[:64]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _make(name: str, targets=None, default_scene: str = "") -> Path:
    folder = projects_root() / name
    (folder / "scenes").mkdir(parents=True, exist_ok=True)
    (folder / "library").mkdir(exist_ok=True)
    if not (folder / "project.json").is_file():
        _write_json(folder / "project.json", {
            "name": name, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "targets": _tags(targets), "default_scene": default_scene})
    return folder


def _tags(targets) -> list[str]:
    if isinstance(targets, str):
        targets = targets.split(",")
    return list(dict.fromkeys(str(t).strip().lower() for t in (targets or []) if str(t).strip()))


def _migrate() -> None:
    """Flat forge/scenes/* -> projects/My Scenes/scenes/*, once. Nothing is deleted, only moved."""
    root = projects_root()
    key = str(root)
    if key in _DONE:
        return
    _DONE.add(key)
    if flat_mode() or os.environ.get("FORGE_PROJECTS_DIR"):
        return
    marker = _base() / "scenes-migrated.json"
    old = _base() / "scenes"
    if marker.is_file() or not old.is_dir():
        _make(DEFAULT)
        return
    mapped: dict[str, str] = {}
    dest = _make(DEFAULT)
    for child in sorted(old.iterdir()):
        target = dest / "scenes" / child.name
        if target.exists():
            mapped[child.name] = "(kept in forge/scenes: %s already exists in %s)" % (child.name, DEFAULT)
            continue
        shutil.move(str(child), str(target))
        mapped[child.name] = "%s/%s" % (DEFAULT, child.name)
    for extra in ("files", "videos"):  # files.py / live_view.py sit next to the scenes folder
        src, dst = _base() / extra, dest / extra
        if src.is_dir() and not dst.exists():
            shutil.move(str(src), str(dst))
    try:
        old.rmdir()
    except OSError:
        pass
    _write_json(marker, mapped)


def ensure() -> Path:
    _migrate()
    root = projects_root()
    root.mkdir(parents=True, exist_ok=True)
    if not flat_mode() and not any(p.is_dir() and not p.name.startswith(".") for p in root.iterdir()):
        _make(DEFAULT)
    return root


def exists(name: str) -> bool:
    return (projects_root() / safe_project(name) / "project.json").is_file()


def require(name: str) -> str:
    ensure()
    name = safe_project(name)
    if not exists(name):
        names = [p["name"] for p in list_projects()]
        raise ValueError("No project %r. Projects: %s. Create one with forge_project(action=\"new\", name=...)"
                         % (name, ", ".join(names) or "none"))
    return name


def active() -> str:
    ensure()
    try:
        name = (projects_root() / ".active").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    if name and exists(name):
        return name
    names = [p["name"] for p in list_projects()]
    return DEFAULT if DEFAULT in names or not names else names[0]


def set_active(name: str) -> str:
    name = require(name)
    (projects_root() / ".active").write_text(name, encoding="utf-8")
    return name


def scenes_dir(project: str | None = None) -> Path:
    """Scenes folder of `project` (default: the active one; the flat override folder in flat mode)."""
    if project is None and flat_mode():
        return Path(os.environ["FORGE_SCENES_DIR"])
    ensure()
    return projects_root() / (safe_project(project) if project else active()) / "scenes"


def read_project(name: str) -> dict[str, Any]:
    data = json.loads((projects_root() / name / "project.json").read_text(encoding="utf-8"))
    data.setdefault("name", name)
    data.setdefault("targets", [])
    data.setdefault("default_scene", "")
    return data


def _thumb_url(folder: Path) -> str | None:
    path = folder / "thumb.jpg"
    if not path.is_file():
        return None
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _card(folder: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Picker card extras: modified (epoch s), objects (from the last thumbnail, else None), thumb (data URL or None)."""
    try:
        objects = json.loads((folder / "thumb.json").read_text(encoding="utf-8")).get("objects")
    except (OSError, ValueError, AttributeError):
        objects = None
    return {**row, "modified": int((folder / "scene.json").stat().st_mtime), "objects": objects, "thumb": _thumb_url(folder)}


def list_projects(cards: bool = False) -> list[dict[str, Any]]:
    """cards=True (the panel picker) adds modified and thumb: the most recently modified scene's thumbnail."""
    root = ensure()
    out = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(".") or not (folder / "project.json").is_file():
            continue
        try:
            info = read_project(folder.name)
        except (OSError, json.JSONDecodeError):
            continue
        scenes = folder / "scenes"
        found = [p for p in scenes.iterdir() if (p / "scene.json").is_file()] if scenes.is_dir() else []
        row = {"name": folder.name, "scenes": len(found), "targets": info["targets"], "default_scene": info["default_scene"]}
        if cards:
            latest = max(found, key=lambda p: (p / "scene.json").stat().st_mtime_ns, default=None)
            row["modified"] = int((latest / "scene.json").stat().st_mtime) if latest else int((folder / "project.json").stat().st_mtime)
            row["thumb"] = _thumb_url(latest) if latest else None
        out.append(row)
    return out


_NAME = re.compile(rb'"name"\s*:\s*"([^"\\]*)"')
_REV = re.compile(rb'"rev"\s*:\s*(\d+)')


def _header(path: Path, fallback: str) -> dict[str, Any]:
    with open(path, "rb") as fh:
        head = fh.read(4096)
    name, rev = _NAME.search(head), _REV.search(head)
    if name and rev:
        return {"name": name[1].decode("utf-8", "replace") or fallback, "rev": int(rev[1])}
    data = json.loads(path.read_text(encoding="utf-8"))  # unusual key order: pay for the full read once
    return {"name": data.get("name") or fallback, "rev": data.get("rev") or 0}


def list_scenes(project: str | None = None, cards: bool = False) -> list[dict[str, Any]]:
    """[{name, rev}]; cards=True (the panel picker) adds modified, objects and thumb per scene."""
    root = scenes_dir(require(project) if project else None)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if (p / "scene.json").is_file()):
        try:
            row = _header(folder / "scene.json", folder.name)
            out.append(_card(folder, row) if cards else row)
        except (OSError, ValueError):
            continue
    return out


def new_project(name: str, targets=None) -> dict[str, Any]:
    ensure()
    name = safe_project(name)
    if exists(name):
        raise ValueError("Project %r already exists. Use forge_project(action=\"open\", name=%r)" % (name, name))
    _make(name, targets)
    return {"name": name, "targets": _tags(targets)}


def rename_project(name: str, new_name: str) -> str:
    name, new_name = require(name), safe_project(new_name)
    if exists(new_name) or (projects_root() / new_name).exists():
        raise ValueError("Project %r already exists. Pick another new_name" % new_name)
    was_active = active() == name
    (projects_root() / name).rename(projects_root() / new_name)
    info = read_project(new_name)
    info["name"] = new_name
    _write_json(projects_root() / new_name / "project.json", info)
    if was_active:
        set_active(new_name)
    _forget_all()
    return new_name


def delete_project(name: str) -> str:
    """Moves the folder to projects/.trash/<name>-<stamp>; returns that path."""
    name = require(name)
    if [p["name"] for p in list_projects()] == [name]:
        raise ValueError("Can't delete %r: it is the last project and Forge needs one. "
                         "Create another with forge_project(action=\"new\", name=...) first" % name)
    trash = projects_root() / ".trash"
    trash.mkdir(exist_ok=True)
    dest = trash / ("%s-%s" % (name, time.strftime("%Y%m%d-%H%M%S")))
    shutil.move(str(projects_root() / name), str(dest))
    _forget_all()
    return str(dest)


def move_scene(scene: str, to_project: str) -> str:
    """`scene` is "Project/Scene" or a plain name in the active project; returns the new "Project/Scene"."""
    proj, _, short = scene.rpartition("/")
    src_proj = require(proj) if proj else active()
    dst_proj = require(to_project)
    short = _safe_scene(short)
    src = projects_root() / src_proj / "scenes" / short
    dst = projects_root() / dst_proj / "scenes" / short
    if not (src / "scene.json").is_file():
        raise ValueError("No scene %r in project %r. Scenes: %s" % (
            short, src_proj, ", ".join(s["name"] for s in list_scenes(src_proj)) or "none"))
    if dst.exists():
        raise ValueError("Project %r already has a scene %r. Rename one first" % (dst_proj, short))
    shutil.move(str(src), str(dst))
    relink_scene(src, dst, short)
    _forget_all()
    return "%s/%s" % (dst_proj, short)


def act(action: str = "list", name: str = "", new_name: str = "", scene: str = "", targets=None,
        confirm: bool = False, project: str = "") -> dict[str, Any]:
    """forge_project tool and the panel RPC share this. Results stay small: names and counts only."""
    action = (action or "list").strip().lower()
    name = name or project
    acts = ("list", "new", "open", "rename", "delete", "delete_scene", "move_scene", "active")
    if action not in acts:
        raise ValueError("Unknown action %r. Actions: %s" % (action, ", ".join(acts)))
    if action in ("list", "active"):
        rows = list_projects()
        shown = [{"name": p["name"], "scenes": p["scenes"], "targets": p["targets"]} for p in rows[:12]]
        out: dict[str, Any] = {"active": active(), "projects": shown}
        if len(rows) > 12:
            out["more"] = len(rows) - 12
        return out
    if action == "new":
        out = new_project(name, targets)
        set_active(out["name"])
        return {**out, "active": out["name"], "summary": "Created project %s" % out["name"]}
    if action == "open":
        return {"active": set_active(name), "scenes": [s["name"] for s in list_scenes(name)][:20]}
    if action == "rename":
        if not new_name:
            raise ValueError("Pass new_name for rename")
        return {"name": rename_project(name, new_name), "active": active()}
    if action == "delete_scene":
        target = scene or name
        if not target:
            raise ValueError("delete_scene needs name = the scene (\"Project/Scene\" or a plain name in the active project)")
        from . import scene as scenes

        return scenes.delete_scene(target, confirm)
    if action == "delete":
        require(name)
        if not confirm:
            raise ValueError("Deleting project %r moves it to the trash; pass confirm=true to do it" % name)
        return {"trashed": safe_project(name), "to": delete_project(name), "active": active()}
    if not scene or not (name or new_name):
        raise ValueError("move_scene needs scene (\"Project/Scene\" or a plain name) and name = the destination project")
    return {"scene": move_scene(scene, name or new_name)}


def relink_scene(old: Path, new: Path, name: str) -> None:
    """A scene folder moved or was renamed: every project library's `used_by` record follows it."""
    for folder in projects_root().iterdir() if projects_root().is_dir() else []:
        path = folder / "library" / "index.json"
        if not path.is_file():
            continue
        try:
            idx = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hit = False
        for rec in (idx.get("links") or {}).values():
            if str(old) in rec:
                rec[str(new)] = {**rec.pop(str(old)), "name": name}
                hit = True
        if hit:
            _write_json(path, idx)


# ---- linked library parts: stored once in the project library, never in the scene ----------------
# A linked part's prototype is {lib: <part id>, mesh: ...} and its library is the scene's own project. The mesh lives in
# memory only: strip_lib() drops it before a scene or an undo snapshot is written, resolve_libs() fills it back.
# Prototypes whose library part is gone keep their embedded mesh when saved, so nothing is ever lost.

_PARTS: dict[tuple[str, str], tuple[tuple[int, int], dict]] = {}


def _part_file(project: str, iid: str) -> Path | None:
    folder = projects_root() / project / "library" / "parts" / iid
    for name in ("mesh.fmesh", "mesh.json"):
        if (folder / name).is_file():
            return folder / name
    return None


def project_of(folder) -> str:
    """projects/<P>/scenes/<Scene> -> P; '' for a scene outside a project (flat override folders)."""
    folder = Path(folder)
    return folder.parent.parent.name if folder.parent.name == "scenes" else ""


def strip_lib(data: dict, folder) -> dict:
    """`data` without the meshes of linked prototypes whose library part exists (a shallow copy; data is untouched)."""
    protos = data.get("prototypes") or []
    if not any(p.get("lib") and p.get("mesh") for p in protos):
        return data
    project = project_of(folder)
    return {**data, "prototypes": [
        {**p, "mesh": None} if project and p.get("lib") and p.get("mesh") and _part_file(project, p["lib"]) else p
        for p in protos]}


def _part_mesh(project: str, iid: str) -> dict:
    path = _part_file(project, iid)
    if path is None:
        raise FileNotFoundError("%s/%s" % (project, iid))
    st = path.stat()
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _PARTS.get((project, iid))
    if hit is None or hit[0] != stamp:
        from . import storage

        mesh = storage.decode(path.read_bytes()) if path.suffix == ".fmesh" else json.loads(path.read_text(encoding="utf-8"))
        hit = _PARTS[(project, iid)] = (stamp, mesh)
    return hit[1]


def resolve_libs(data: dict, folder) -> list[str]:
    """Fill the mesh of every linked prototype from its library; returns warnings for parts that went missing.
    One decoded copy per library part is shared by every scene; only face_mat is remapped to this scene's materials."""
    warnings: list[str] = []
    project = project_of(folder)
    for proto in data.get("prototypes") or []:
        if not project or not proto.get("lib") or proto.get("mesh"):
            continue
        iid = proto["lib"]
        try:
            base = _part_mesh(project, iid)
        except (OSError, ValueError, KeyError):
            from .kernel.mesh import build_primitive

            proto["mesh"] = build_primitive("box", {}, mat="default").to_dict()
            warnings.append("Library part %r is missing from project %r: showing a placeholder box. "
                            "Restore it with forge_library(action=\"add\", ...) or make the node local" % (iid, project))
            continue
        ids = {m["lib"].split(":", 1)[1]: m["id"] for m in data.get("materials") or []
               if str(m.get("lib") or "").startswith(iid + ":")}
        proto["mesh"] = {**base, "face_mat": [ids.get(m, m) for m in base.get("face_mat") or []]}
    return warnings


def _safe_scene(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (name or "").strip())[:64] or _bad()


def _bad():
    raise ValueError("scene name required")


def _forget_all() -> None:
    from . import scene
    scene._OPEN.clear()
