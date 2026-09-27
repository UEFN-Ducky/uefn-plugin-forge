"""A .forge file is one zip: the scene plus its textures."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .scene import _safe, load, scenes_root


def files_root() -> Path:
    root = scenes_root().parent / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_files() -> list[dict]:
    out = []
    for path in sorted(files_root().glob("*.forge")):
        out.append({"name": path.stem, "path": str(path)})
    return out


def save_forge(name: str, dest: str = "") -> dict:
    scene = load(name)
    folder = scene.folder
    out = Path(dest) if dest else files_root() / f"{_safe(scene.name)}.forge"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(folder / "scene.json", "scene.json")
        textures = folder / "textures"
        if textures.is_dir():
            for path in textures.iterdir():
                if path.is_file():
                    zf.write(path, f"textures/{path.name}")
    return {"path": str(out), "name": scene.name}


def open_forge(path: str) -> dict:
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"No file {src}")
    with zipfile.ZipFile(src) as zf:
        raw = json.loads(zf.read("scene.json"))
        name = _safe(str(raw.get("name") or src.stem))
        raw["name"] = name
        dest = scenes_root() / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "scene.json").write_text(json.dumps(raw), encoding="utf-8")
        textures = dest / "textures"
        textures.mkdir(exist_ok=True)
        for info in zf.infolist():
            if not info.filename.startswith("textures/") or info.is_dir():
                continue
            target = textures / Path(info.filename).name
            target.write_bytes(zf.read(info))
    return {"name": name, "path": str(src)}
