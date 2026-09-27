"""Turn a .blend into a Forge scene and a .forge file. Blender stays the reader."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .files import save_forge
from .scene import _safe, scenes_root

_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
_SCRIPT = Path(__file__).with_name("blend_export_script.py")


def _blender() -> Path:
    if _BLENDER.is_file():
        return _BLENDER
    root = Path(r"C:\Program Files\Blender Foundation")
    found = sorted(root.glob("*/blender.exe"))
    if not found:
        raise ValueError("Blender is not installed")
    return found[-1]


def import_blend(path: str, name: str = "") -> dict:
    src = Path(path)
    if not src.is_file():
        raise ValueError("Pick a .blend file that exists on disk")
    tmp = scenes_root().parent / "_import.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(_blender()), "--background", str(src), "--python", str(_SCRIPT), "--", str(tmp)],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0 or not tmp.is_file():
        tail = (proc.stderr or proc.stdout or "Blender export failed")[-500:]
        raise ValueError(tail)
    data = json.loads(tmp.read_text(encoding="utf-8"))
    tmp.unlink(missing_ok=True)
    scene_name = _safe(name or str(data.get("name") or src.stem))
    data["name"] = scene_name
    folder = scenes_root() / scene_name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / "scene.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    saved = save_forge(scene_name)
    objects = sum(1 for n in data["nodes"] if n.get("type") == "object")
    return {
        "name": scene_name,
        "opened": scene_name,
        "path": saved["path"],
        "objects": objects,
        "prototypes": len(data.get("prototypes") or []),
        "groups": sum(1 for n in data["nodes"] if n.get("type") == "group"),
    }
