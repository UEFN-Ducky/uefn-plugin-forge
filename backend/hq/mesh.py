"""HQ mesh ops: remesh, retopo, sculpt_hd, modifiers, lods. The bpy work is engine/ops/mesh.py.

Lengths (voxel, bevel width, stroke radius...) are Forge units (cm). node_data comes back from
the engine and forge_hq stores it on the node (node["hq"][op]).
"""
from pathlib import Path

from . import engine, op

NAME = "mesh"

# op -> {param: default}; unknown params are rejected with the valid list.
_DEFAULTS = {
    "remesh": {"voxel": 5.0, "adaptivity": 0.0},
    "retopo": {"faces": 4000, "symmetry": False},
    "sculpt_hd": {"levels": 3, "detail": "rock", "strokes": None, "path": None},
    "modifiers": {"stack": []},
    "lods": {"ratios": [1, 0.5, 0.25, 0.1], "nanite": False},
}


def params(name, given):
    given = given or {}
    bad = [k for k in given if k not in _DEFAULTS[name]]
    if bad:
        raise ValueError(f"Unknown param {bad[0]!r} for {name}. Params: {', '.join(_DEFAULTS[name])}")
    p = {**_DEFAULTS[name], **given}
    if name == "sculpt_hd" and not 1 <= int(p["levels"]) <= 6:
        raise ValueError("levels must be 1-6 (each level is 4x faces)")
    if name == "remesh" and float(p["voxel"]) <= 0:
        raise ValueError("voxel must be > 0 (cm, e.g. 5)")
    if name == "lods" and not (p["ratios"] and all(0 < float(r) <= 1 for r in p["ratios"])):
        raise ValueError("ratios must be fractions in (0, 1], e.g. [1, 0.5, 0.25, 0.1]")
    return p


def _go(scene, name, p):
    ident = str(p.get("id") or "")
    node = scene.node(ident) if ident else None
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No object %r for %s. Objects: %s" % (ident, name, ", ".join(objs) or "none"))
    q = params(name, {k: v for k, v in p.items() if k not in ("id", "wait")})
    if name == "sculpt_hd" and not q["path"]:
        folder = Path(scene.folder) / "hd"
        folder.mkdir(parents=True, exist_ok=True)
        q["path"] = str(folder / (ident + ".blend"))
    result, changed = engine(scene, name, [ident], q)
    return {**result, "ids": changed or [ident]}


@op("remesh", "remesh(id, voxel=5, adaptivity=0) voxel remesh to a watertight mesh; keeps materials, UVs, vertex colors")
def remesh(scene, p):
    return _go(scene, "remesh", p)


@op("retopo", "retopo(id, faces=4000, symmetry=false) quadriflow quad retopology; remesh first if the mesh is open")
def retopo(scene, p):
    return _go(scene, "retopo", p)


@op("sculpt_hd", "sculpt_hd(id, levels=3, detail=pores|scales|wood|cloth|rock, strokes?) high-res detail in a sidecar .blend; the base mesh stays")
def sculpt_hd(scene, p):
    return _go(scene, "sculpt_hd", p)


@op("modifiers", "modifiers(id, stack=[bevel|subsurf|solidify|weighted_normal|displace|array|mirror or {type, ...}]) non-destructive stack stored on the node")
def modifiers(scene, p):
    return _go(scene, "modifiers", p)


@op("lods", "lods(id, ratios=[1,.5,.25,.1], nanite=false) seam-preserving LOD chain, stored on the node for export")
def lods(scene, p):
    return _go(scene, "lods", p)
