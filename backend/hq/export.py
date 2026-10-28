"""export: one call writes the file a game engine wants (FBX, glTF or Alembic) from the Blender engine."""
from pathlib import Path

from . import op, engine

# Per-engine recipe. normal = the green-channel convention the engine's importer expects.
TARGETS = {
    "ue5": {"format": "fbx", "normal": "dx", "axis": ["-Y", "Z"], "scale": "FBX_SCALE_ALL", "space": False},
    "unity": {"format": "fbx", "normal": "gl", "axis": ["-Z", "Y"], "scale": "FBX_SCALE_UNITS", "space": True},
    "godot": {"format": "glb", "normal": "gl"},
    "generic": {"format": "glb", "normal": "gl"},
}
_EXT = {"fbx": ".fbx", "glb": ".glb", "gltf": ".gltf", "abc": ".abc"}


def recipe(target, fmt=None):
    """Export settings for a target, with the file format overridden by fmt (fbx|glb|gltf|abc)."""
    if target not in TARGETS:
        import difflib
        close = difflib.get_close_matches(str(target), TARGETS, n=1)
        raise ValueError("No export target %r.%s Targets: %s" % (
            target, " Did you mean %r?" % close[0] if close else "", ", ".join(TARGETS)))
    out = dict(TARGETS[target], target=target)
    if fmt:
        if fmt not in _EXT:
            raise ValueError("No format %r. Formats: %s" % (fmt, ", ".join(_EXT)))
        out["format"] = fmt
    return out


@op("export", "export(id?, target=ue5|unity|godot|generic, format?=fbx|glb|gltf|abc, path?, tangents=true) "
              "engine-ready file with skin, morphs, clips; groom curves go to .abc", long=False)
def export(scene, p):
    cfg = recipe(p.get("target", "generic"), p.get("format"))
    ids = p.get("id") or p.get("ids")
    ids = [ids] if isinstance(ids, str) else list(ids or [])
    if not ids:
        ids = [n["id"] for n in scene.data["nodes"] if not n.get("parent")]
        ids += [r["id"] for r in scene.data.get("rigs") or []]
    path = p.get("path")
    cfg["tangents"] = bool(p.get("tangents", True))
    # format may flip to abc inside the engine when the selection is only curves
    cfg["path"] = str(Path(path) if path else Path(scene.folder) / "exports" / (scene.name + _EXT[cfg["format"]]))
    cfg["verify"] = bool(p.get("verify", False))
    result, _ = engine(scene, "export", ids, cfg)
    out = {"summary": "%s -> %s, %d skin, %d clips, %d morphs" % (
        cfg["target"], Path(result["path"]).suffix, result["skins"], result["clips"], result["morphs"]),
        "path": result["path"], "stats": {k: result[k] for k in ("skins", "clips", "morphs", "tris", "bones")}}
    if result.get("warnings"):
        out["warnings"] = result["warnings"]
    return out
