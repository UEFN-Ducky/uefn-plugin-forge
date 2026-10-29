"""HQ mesh ops: remesh, retopo, sculpt_hd, modifiers, lods, boolean, hardsurface, sculpt, origin.
The bpy work is engine/ops/mesh.py.

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
    "boolean": {"cutters": [], "mode": "difference", "solver": "exact", "hide": True},
    "hardsurface": {"preset": "panel", "at": None, "size": None, "depth": None, "count": None, "radius": None},
    "sculpt": {"ops": []},
    "origin": {"at": "bottom", "ground": False},
}
STACKS = ("midpoly", "subd", "render")
KITS = ("panel", "vent", "bolt", "grille")


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
    if name == "modifiers" and isinstance(p["stack"], str) and p["stack"] not in STACKS:
        raise ValueError("stack is a list or a preset: %s" % ", ".join(STACKS))
    if name == "boolean":
        if not p["cutters"] or not isinstance(p["cutters"], list):
            raise ValueError("cutters: a list of Forge ids or {shape: box|cylinder|sphere, size, at}")
        if p["mode"] not in ("difference", "union", "intersect") or p["solver"] not in ("exact", "manifold", "fast"):
            raise ValueError("mode: difference|union|intersect, solver: exact|manifold|fast")
    if name == "hardsurface" and p["preset"] not in KITS:
        raise ValueError("preset must be one of: %s" % ", ".join(KITS))
    if name == "hardsurface" and p["count"] is not None and not 1 <= int(p["count"]) <= 16:
        raise ValueError("count must be 1-16")
    if name == "sculpt" and not p["ops"]:
        raise ValueError('ops is empty, e.g. ["remesh:2", "inflate:1", "smooth:3", "symmetrize:+x"]')
    if name == "origin" and p["at"] not in ("bottom", "center", "corner"):
        raise ValueError("at must be bottom, center or corner")
    return p


def _node(scene, ident, name):
    node = scene.node(ident) if ident else None
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No object %r for %s. Objects: %s" % (ident, name, ", ".join(objs) or "none"))
    return node


def _go(scene, name, p):
    ident = str(p.get("id") or "")
    node = _node(scene, ident, name)
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


@op("boolean", "boolean(id, cutters=[ids|{shape:box|cylinder|sphere, size, at, rot}], mode=difference|union|intersect, solver=exact|manifold|fast, hide=true) applied boolean + weld/normals cleanup; id cutters are hidden")
def boolean(scene, p):
    ident = str(p.get("id") or "")
    _node(scene, ident, "boolean")
    q = params("boolean", {k: v for k, v in p.items() if k not in ("id", "wait")})
    ids = [c for c in q["cutters"] if not isinstance(c, dict)]
    for c in ids:
        _node(scene, str(c), "boolean cutter")
    result, changed = engine(scene, "boolean", [ident] + ids, {**q, "id": ident})
    if q["hide"]:
        for c in ids:
            scene.node(str(c))["visible"] = False
    return {**result, "ids": [ident]}


@op("hardsurface", "hardsurface(id, preset=panel|vent|bolt|grille, at=[x,y,z]?, size?, depth?, count?, radius?) detail kit cut into (bolts: joined onto) the surface nearest at (default top centre), along its normal; cm")
def hardsurface(scene, p):
    return _go(scene, "hardsurface", p)


@op("sculpt", "sculpt(id, ops=[remesh:cm, inflate:cm, smooth:n, symmetrize:+x, detail:{strength,scale,seed}, multires:n]) headless sculpt chain, run in order")
def sculpt(scene, p):
    return _go(scene, "sculpt", p)


@op("origin", "origin(id, at=bottom|center|corner, ground=false) move the pivot (the object stays put); ground drops its lowest point to z=0")
def origin(scene, p):
    out = _go(scene, "origin", p)
    node = scene.node(str(p["id"]))
    t = dict(node["transform"])
    t["pos"] = [a + b for a, b in zip(t["pos"], out.pop("move"))]  # ponytail: world shift, assumes no parent
    node["transform"] = t
    scene.touch_geom()
    return out
