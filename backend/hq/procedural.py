"""HQ procedural ops: scatter, gn, sdf_blend, drop, procedural. The bpy work is engine/ops/procedural.py.

Lengths are Forge units (cm), except gn socket values, which are Blender's (metres).
"""
from ..transforms import decompose
from . import engine, op
from .mesh import _node

NAME = "procedural"

_DEFAULTS = {
    "scatter": {"on": None, "items": [], "density": 20.0, "count": None, "seed": 1, "scale": [0.8, 1.2],
                "align": "normal", "max_slope": 90.0, "path": None, "spacing": 50.0, "name": None},
    "gn": {"id": None, "preset": None, "spec": None, "inputs": None, "name": None},
    "sdf_blend": {"ids": [], "fillet": 2.0, "voxel": 1.0, "name": None, "hide": True},
    "drop": {"ids": [], "onto": [], "frames": 60},
    "procedural": {"kind": "building", "floors": 4, "width": None, "depth": None, "height": None, "floor_height": 300.0,
                   "bay": 300.0, "thickness": None, "opening": "none", "steps": 12, "rise": 17.5, "run": 28.0,
                   "length": 400.0, "spacing": 100.0, "path": None, "radius": 5.0, "sag": 40.0, "at": None, "name": None},
}
KINDS = ("building", "wall", "floor", "stairs", "fence", "pipe", "cable")
GN_PRESETS = ("pebbles", "wire")


def params(name, given):
    given = {k: v for k, v in (given or {}).items() if k != "wait"}
    bad = [k for k in given if k not in _DEFAULTS[name]]
    if bad:
        raise ValueError(f"Unknown param {bad[0]!r} for {name}. Params: {', '.join(_DEFAULTS[name])}")
    p = {**_DEFAULTS[name], **given}
    if name == "scatter":
        if not p["items"]:
            raise ValueError("items: the Forge ids to copy")
        if not (p["on"] or p["path"]):
            raise ValueError("give on=<surface id> or path=[[x,y,z], ...]")
        if p["align"] not in ("normal", "up"):
            raise ValueError("align must be normal or up")
    if name == "gn" and not (p["spec"] or p["preset"] in GN_PRESETS):
        raise ValueError("give spec={nodes, links} or preset: %s" % ", ".join(GN_PRESETS))
    if name == "sdf_blend" and len(p["ids"]) < 2:
        raise ValueError("ids: 2+ objects to blend")
    if name == "sdf_blend" and not 0 < float(p["voxel"]) <= float(p["fillet"]) * 4:
        raise ValueError("voxel must be > 0 and no bigger than 4x fillet (cm)")
    if name == "drop" and not p["ids"]:
        raise ValueError("ids: the objects that fall")
    if name == "procedural":
        if p["kind"] not in KINDS:
            raise ValueError("kind must be one of: %s" % ", ".join(KINDS))
        if not 1 <= int(p["floors"]) <= 40 or not 1 <= int(p["steps"]) <= 100:
            raise ValueError("floors 1-40, steps 1-100")
    return {k: v for k, v in p.items() if v is not None}


def _objects(scene, ids, name):
    for i in ids:
        _node(scene, str(i), name)
    return [str(i) for i in ids]


@op("scatter", "scatter(on|path, items=[ids], density=20 /m2, count?, seed=1, scale=[.8,1.2], align=normal|up, max_slope=90, spacing=50, name?) copies merged into one new object; path=[[x,y,z]..] arrays along a line")
def scatter(scene, p):
    q = params("scatter", p)
    ids = _objects(scene, list(q["items"]) + ([q["on"]] if q.get("on") else []), "scatter")
    result, changed = engine(scene, "scatter", ids, q)
    return {**result, "ids": [c for c in changed if c not in ids]}


@op("gn", "gn(id?, preset=pebbles|wire or spec={nodes:[{id,type,in,props}], links:[[\"a.Out\",\"b.In\"]]}, inputs={\"node.Socket\": v}, name?) geometry-nodes graph applied to id (or a new object); sockets in metres")
def gn(scene, p):
    q = params("gn", p)
    ids = _objects(scene, [q["id"]], "gn") if q.get("id") else []
    result, changed = engine(scene, "gn", ids, q)
    return {**result, "ids": changed}


@op("sdf_blend", "sdf_blend(ids, fillet=2, voxel=1, name?, hide=true) union into one watertight mesh with rounded seams where parts meet; sources hidden")
def sdf_blend(scene, p):
    q = params("sdf_blend", p)
    ids = _objects(scene, q["ids"], "sdf_blend")
    result, changed = engine(scene, "sdf_blend", ids, q)
    if q["hide"]:
        for i in ids:
            scene.node(i)["visible"] = False
    return {**result, "ids": [c for c in changed if c not in ids]}


@op("drop", "drop(ids, onto=[ids], frames=60) rigid-body settle: ids fall as convex hulls onto the onto meshes and keep where they land")
def drop(scene, p):
    q = params("drop", p)
    ids = _objects(scene, list(q["ids"]) + list(q["onto"]), "drop")
    result, _ = engine(scene, "drop", ids, q)
    for fid, m in result.pop("matrices").items():
        scene.node(fid)["transform"] = decompose(m)  # ponytail: world matrix, assumes no parent
    scene.touch_geom()
    return {**result, "ids": list(q["ids"])}


@op("procedural", "procedural(kind=building|wall|floor|stairs|fence|pipe|cable, floors=4, width, depth, opening=none|door|window, steps=12, length, path, radius=5, sag=40, at, name?) one SM_ mesh; kits sit on a corner; cm")
def procedural(scene, p):
    q = params("procedural", {k: v for k, v in p.items() if k != "id"})
    result, changed = engine(scene, "procedural", [], q)
    return {**result, "ids": changed}
