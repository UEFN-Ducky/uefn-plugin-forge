"""export: one call writes the file a game engine wants (FBX, glTF, OBJ, STL, USDZ or Alembic) from the Blender engine."""
from pathlib import Path

from . import op, engine

# Per-engine recipe. normal = the green-channel convention the engine's importer expects.
# ucx: keep UCX_ hulls (UE), rename them to Godot's -colonly hint, or drop them (engines that ignore them).
TARGETS = {
    "ue5": {"format": "fbx", "normal": "dx", "axis": ["-Y", "Z"], "scale": "FBX_SCALE_ALL", "space": False, "ucx": "keep"},
    "unity": {"format": "fbx", "normal": "gl", "axis": ["-Z", "Y"], "scale": "FBX_SCALE_UNITS", "space": True, "ucx": "drop"},
    # Roblox: FBX in cm (the importer converts to studs), triangles only, <= 20k tris and 10k verts per mesh
    "roblox": {"format": "fbx", "normal": "gl", "axis": ["-Z", "Y"], "scale": "FBX_SCALE_UNITS", "space": True,
               "ucx": "drop", "triangulate": True, "tris": 20000, "verts": 10000},
    "godot": {"format": "glb", "normal": "gl", "ucx": "godot"},
    # web viewers: GLB with WebP textures, <= 30k tris and 5 MB
    "web": {"format": "glb", "normal": "gl", "ucx": "drop", "webp": 85, "tris": 30000, "mb": 5},
    "generic": {"format": "glb", "normal": "gl", "ucx": "drop"},
}
_EXT = {"fbx": ".fbx", "glb": ".glb", "gltf": ".gltf", "abc": ".abc", "obj": ".obj", "stl": ".stl", "usdz": ".usdz"}


def recipe(target, fmt=None):
    """Export settings for a target, with the file format overridden by fmt (fbx|glb|gltf|abc|obj|stl|usdz)."""
    if target not in TARGETS:
        import difflib
        close = difflib.get_close_matches(str(target), TARGETS, n=1)
        raise ValueError("No export target %r.%s Targets: %s" % (
            target, " Did you mean %r?" % close[0] if close else "", ", ".join(TARGETS)))
    out = dict(TARGETS[target], target=target)
    out.setdefault("axis", ["-Z", "Y"])
    if fmt:
        if fmt not in _EXT:
            raise ValueError("No format %r. Formats: %s" % (fmt, ", ".join(_EXT)))
        out["format"] = fmt
    return out


def limits(scene, ids, cfg):
    """Per-mesh tri and vert caps of the target, as warnings."""
    from .. import rig as rg
    out = []
    for n in rg._objects_under(scene, ids):
        if n.get("layer") == "collision":
            continue
        mesh = scene.world_mesh(n)
        tris, verts = mesh.tri_count(), len(mesh.verts)
        if cfg.get("tris") and tris > cfg["tris"]:
            out.append("%s %d tris > %dk" % (n.get("name"), tris, cfg["tris"] // 1000))
        if cfg.get("verts") and verts > cfg["verts"]:
            out.append("%s %d verts > %dk" % (n.get("name"), verts, cfg["verts"] // 1000))
    return out


@op("export", "export(id?, target=ue5|unity|roblox|godot|web|generic, format?=fbx|glb|gltf|abc|obj|stl|usdz, path?, "
              "tangents=true, verify=false, lods=false) engine-ready file with skin, morphs, clips, UCX_; grooms go to .abc", long=False)
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
    if p.get("lods"):  # stored lods() ratios become <name>_LOD0..n meshes (Unity LODGroup, UE LOD import)
        cfg["lods"] = {n.get("name") or n["id"]: (n.get("hq") or {}).get("lods", {}).get("ratios") or [1, 0.5, 0.25, 0.1]
                       for n in scene.data["nodes"] if n.get("type") == "object" and n.get("layer") != "collision"}
    warnings = limits(scene, ids, cfg)
    result, _ = engine(scene, "export", ids, cfg)
    warnings += result.get("warnings") or []
    if cfg.get("mb") and result.get("mb", 0) > cfg["mb"]:
        warnings.append("%.1f MB > %d MB" % (result["mb"], cfg["mb"]))
    out = {"summary": "%s -> %s, %d skin, %d clips, %d morphs%s%s" % (
        cfg["target"], Path(result["path"]).suffix, result["skins"], result["clips"], result["morphs"],
        ", %d LODs" % result["lods"] if result.get("lods") else "", ", %d UCX" % result["ucx"] if result.get("ucx") else ""),
        "path": result["path"], "stats": {k: result[k] for k in ("skins", "clips", "morphs", "tris", "bones")}}
    if "verified" in result:
        out["verified"] = result["verified"]
        out["mb"] = result.get("mb", 0)
    if warnings:  # also in the summary: a long result drops the warnings list
        out["warnings"] = warnings
        out["summary"] += "; " + warnings[0] + (" (+%d)" % (len(warnings) - 1) if len(warnings) > 1 else "")
    return out


@op("convert", "convert(path, to=glb|fbx|obj|stl|usdz|gltf, out?) import any fbx/glb/gltf/obj/stl/usd/abc file and write it "
               "in another format, no scene needed", long=False)
def convert(scene, p):
    src = Path(str(p.get("path") or "")).expanduser()
    if not src.is_file():
        raise ValueError("convert needs path=<full path to an fbx, glb, gltf, obj, stl, usd or abc file>")
    to = str(p.get("to") or "glb").lower().lstrip(".")
    if to not in _EXT or to == "abc":
        raise ValueError("No format %r to convert to. Formats: %s" % (to, ", ".join(k for k in _EXT if k != "abc")))
    dest = str(Path(p["out"]) if p.get("out") else src.with_suffix(_EXT[to]))
    if Path(dest).resolve() == src.resolve():
        raise ValueError("convert would overwrite %s; pass out=<new path>" % src.name)
    from ..engine import client
    got = client.call("convert", {"src": str(src), "path": dest, "format": to}, 600.0)
    return {"summary": "%s -> %s, %d meshes, %d tris" % (src.suffix, Path(dest).suffix, got["meshes"], got["tris"]),
            "path": dest, "tris": got["tris"]}
