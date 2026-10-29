"""HQ vehicles: car_part (every car part on its own, interchangeable), car (a whole car assembled from
those parts, rigged for UE / UEFN vehicles) and car_swap (rebuild one part in place). The bpy work is
engine/ops/vehicle.py.

Lengths are Forge cm. Car frame: -Y is the front, +X the car's left, Z up, tyres on z=0.
Every part's origin is its mount point; node["sockets"] names it (Hub, Hinge, Mount, Column, Pivot).
"""
import copy
import functools
import re

from . import HQ_OPS, engine, op
from .mesh import lods as _lods
from .qa import _pick, hull

KINDS = ("wheel", "rim", "tire", "brake_disc", "caliper", "headlight", "taillight", "grille", "mirror", "door", "hood",
         "trunk", "bumper_front", "bumper_rear", "fender", "spoiler", "diffuser", "exhaust", "seat", "steering_wheel",
         "dashboard", "engine", "suspension", "license_plate", "wiper", "window")
STYLES = ("sport", "sedan", "suv", "truck", "classic", "scifi")
BODIES = ("sedan", "coupe", "hatch", "suv", "pickup", "limo", "sports", "van", "classic", "kart")
SPOKES = ("5", "10", "mesh", "dish")
SOCKETS = {"wheel": "Hub", "rim": "Hub", "tire": "Hub", "brake_disc": "Hub", "caliper": "Hub", "fender": "Hub",
           "suspension": "Hub", "door": "Hinge", "hood": "Hinge", "trunk": "Hinge", "steering_wheel": "Column", "wiper": "Pivot"}
_PART_KEYS = ("kind", "style", "tire", "spokes", "tread", "disc", "brake", "width", "height", "length", "side", "text",
              "region", "label", "front", "rear", "paint", "seed", "name", "at", "materials")
_CAR_KEYS = ("body", "style", "length_cm", "width_cm", "height_cm", "wheelbase_cm", "track_cm", "doors", "parts",
             "interior", "paint", "seed", "name", "at", "materials")
MATERIALS = ("hero", "full")  # hero: 5 slots (paint, glass, chrome, rubber, emissive + red lamp mask); full: 11
_SWAP_KEYS = ("car", "slot") + tuple(k for k in _PART_KEYS if k not in ("name", "at", "side", "paint"))
_WHEEL_SLOTS = {"rim": "wheel", "tire": "wheel", "brake_disc": "wheel", "wheel": "wheel"}


def tire_size(code):
    """'225/45R17' -> (width, sidewall, rim radius, outer radius) in cm."""
    m = re.fullmatch(r"\s*(\d{2,3})\s*/\s*(\d{2,3})\s*[A-Za-z-]*\s*(\d{1,2}(?:\.\d+)?)\s*", str(code))
    if not m:
        raise ValueError("tire %r: use a size code like 225/45R17 (width mm / aspect %% R rim inches)" % (code,))
    w = int(m[1]) / 10.0
    side = w * int(m[2]) / 100.0
    rim = float(m[3]) * 2.54 / 2
    return w, side, rim, rim + side


def _check(p, keys, name):
    bad = [k for k in p if k not in keys and k not in ("wait", "id")]
    if bad:
        raise ValueError("Unknown param %r for %s. Params: %s" % (bad[0], name, ", ".join(keys)))


def _paint(value):
    if value in (None, ""):
        return None
    if isinstance(value, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        h = value.lstrip("#")
        return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [float(c) for c in value[:3]]
    raise ValueError("paint is [r, g, b] (0-1) or '#rrggbb'")


def _spec(p):
    """Validated part spec from car_part / car_swap params."""
    q = {k: v for k, v in p.items() if k in _PART_KEYS and k not in ("paint",) and v is not None}
    q["kind"] = _pick(str(q.get("kind") or ""), KINDS, "part kind")
    q["style"] = _pick(str(q.get("style") or "sedan"), STYLES, "style")
    if q.get("tire") is not None:
        tire_size(q["tire"])
    if q.get("spokes") is not None:
        s = str(q["spokes"])
        if s not in SPOKES and not (s.isdigit() and 2 <= int(s) <= 20):
            raise ValueError("spokes is 5, 10, mesh, dish or a count 2-20")
        q["spokes"] = s
    if q.get("side", "left") not in ("left", "right"):
        raise ValueError("side is left or right (left = +X, the car's left)")
    for k in ("width", "height", "length", "disc"):
        if q.get(k) is not None and not 0 < float(q[k]) < 2000:
            raise ValueError("%s is cm (disc is mm), > 0" % k)
    if q.get("tread") is not None and not 16 <= int(q["tread"]) <= 128:
        raise ValueError("tread is the block count around the tire, 16-128")
    return q


def _mats(scene):
    return copy.deepcopy({m["id"]: m for m in scene.data["materials"] if str(m["id"]).startswith("mcar_")})


def _keep_mats(scene, before, paint):
    """A rebuild never resets materials the scene already has (except paint when given)."""
    for m in scene.data["materials"]:
        old = before.get(m["id"])
        if old is not None and not (paint and m["id"] == "mcar_paint"):
            m.clear()
            m.update(old)


def _adopt(scene, changed, info):
    """New engine objects -> {name: node}, with sockets and car_part data from the engine info."""
    out = {}
    for nid in changed:
        node = scene.node(nid)
        if node is None or node.get("type") != "object" or node.get("name") not in info:
            continue
        meta = info[node["name"]]
        node["sockets"] = {meta.get("socket", "Mount"): {"pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0]}}
        if meta.get("chassis"):
            node["sockets"]["Chassis"] = {"pos": list(meta["chassis"]), "rot": [0.0, 0.0, 0.0]}
        if meta.get("slot"):
            node["car_slot"] = meta["slot"]
        if meta.get("params") is not None:
            node["car_part"] = {"kind": meta["kind"], "side": meta.get("side", "left"), "params": meta["params"]}
        out[node["name"]] = node
    return out


def _r(x):
    return ("%.1fk" % (x / 1000.0)) if x >= 1000 else str(x)


@op("car_part", "car_part(kind=wheel|rim|tire|brake_disc|caliper|headlight|taillight|grille|mirror|door|hood|trunk|bumper_front|"
                "bumper_rear|fender|spoiler|diffuser|exhaust|seat|steering_wheel|dashboard|engine|suspension|license_plate|wiper|window, "
                "style=sport|sedan|suv|truck|classic|scifi, tire=225/45R17, spokes=5|10|mesh|dish|n, side=left|right, width|height|length cm, "
                "disc mm, text?, materials=hero|full, at?, name?) one part in real cm, origin = its socket (Hub/Hinge/Mount); see skill forge/vehicles")
def car_part(scene, p):
    _check(p, _PART_KEYS, "car_part")
    q = _spec(p)
    paint = _paint(p.get("paint"))
    spec = dict(q, name=q.get("name") or "SM_" + "".join(w.title() for w in q["kind"].split("_")),
                at=[float(v) for v in (q.get("at") or [0, 0, 0])])
    before = _mats(scene)
    mats = _pick(str(p.get("materials") or "hero"), MATERIALS, "materials")
    result, changed = engine(scene, "car_part", [], {"parts": [spec], "paint": paint, "materials": mats})
    _keep_mats(scene, before, paint)
    info = result["parts"]
    params = {k: v for k, v in spec.items() if k not in ("name", "at", "side", "kind")}
    for meta in info.values():
        meta.update(params=params, side=q.get("side", "left"))
    nodes = _adopt(scene, changed, info)
    meta = next(iter(info.values()))
    extra = ""
    if q["kind"] in ("wheel", "tire"):
        extra = " %s r=%.1fcm, tread %d," % (spec.get("tire") or "", meta["r"], meta["tread"])
        if not spec.get("tire"):
            extra = " r=%.1fcm, tread %d," % (meta["r"], meta["tread"])
    elif q["kind"] in ("rim", "wheel") or spec.get("spokes"):
        extra = " spokes %s," % (spec.get("spokes") or "style")
    summary = "car_part %s %s%s %s tris, socket %s" % (q["kind"], q["style"], extra, _r(meta["tris"]), meta["socket"])
    return {"summary": summary, "ids": [n["id"] for n in nodes.values()], "tris": meta["tris"]}


def _ucx_material(scene):
    """Alpha 0: look/light renders draw every object, so a grey UCX hull read as a white-out over hood and glass.
    UE ignores UCX materials. ponytail: renders should skip the collision layer; drop this when they do."""
    if not any(m["id"] == "mcar_collision" for m in scene.data["materials"]):
        scene.data["materials"].append({"id": "mcar_collision", "name": "Car_Collision", "base_color": [0.9, 0.5, 0.3, 1],
                                        "metallic": 0.0, "roughness": 1.0, "emissive": [0, 0, 0], "emissive_strength": 0.0,
                                        "opacity": 0.0, "textures": {}})
    return "mcar_collision"


def _collision(scene, nodes, gid, name):
    from ..scene import _nid
    pts = [v for n in nodes if n.get("car_slot", "").startswith("body_") for v in scene.world_mesh(n).verts]
    if not pts:
        pts = [v for n in nodes for v in scene.world_mesh(n).verts]
    ucx = hull(pts, 48)
    group = scene.node(gid)
    pos = group["transform"]["pos"]
    for v in ucx.verts:
        v[:] = [v[i] - pos[i] for i in range(3)]
    ucx.face_mat = [_ucx_material(scene)] * len(ucx.faces)  # faces carry their own material; the node's is a fallback
    node = {"id": _nid("o"), "type": "object", "name": "UCX_%s_01" % name, "parent": gid, "layer": "collision", "visible": True,
            "locked": False, "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
            "prototype": scene.adopt_mesh(ucx), "modifiers": [], "material": _ucx_material(scene)}
    scene.data["nodes"].append(node)
    return node


def car_nodes(scene, gid):
    return [n for n in scene.data["nodes"] if n.get("parent") == gid and n.get("type") == "object" and n.get("layer") != "collision"]


def measure(scene, nodes):
    """(length, width, height) cm of the car without mirrors, plus total tris."""
    lo, hi, tris = [1e9] * 3, [-1e9] * 3, 0
    for n in nodes:
        mesh = scene.world_mesh(n)
        tris += mesh.tri_count()
        if "mirror" in n.get("car_slot", "") or not mesh.verts:
            continue
        a, b = mesh.bounds()
        lo = [min(x, y) for x, y in zip(lo, a)]
        hi = [max(x, y) for x, y in zip(hi, b)]
    return hi[1] - lo[1], hi[0] - lo[0], hi[2] - lo[2], tris


@op("car", "car(body=sedan|coupe|hatch|suv|pickup|limo|sports|van|classic|kart, style?, length_cm?, wheelbase_cm?, track_cm?, "
           "doors?, parts={rim:'mesh', tire:'245/40R18', spoiler:'sport', engine:false, ...}, interior=true, paint?, "
           "materials=hero(5 slots)|full(11), name?, at?) "
           "whole car: body panels with 4 mm gaps + car_part parts, FL/FR/RL/RR_wheel + door bones, LODs + UCX; see skill forge/vehicles")
def car(scene, p):
    _check(p, _CAR_KEYS, "car")
    body = _pick(str(p.get("body") or "sedan"), BODIES, "body")
    q = {k: p[k] for k in _CAR_KEYS if p.get(k) is not None}
    q["body"] = body
    if q.get("style"):
        q["style"] = _pick(str(q["style"]), STYLES, "style")
    parts = dict(q.get("parts") or {})
    for kind in parts:
        if kind not in KINDS and not kind.endswith("_style"):
            _pick(kind, KINDS, "parts key")
    if parts.get("tire"):
        tire_size(parts["tire"])
    for k in ("length_cm", "width_cm", "height_cm", "wheelbase_cm", "track_cm"):
        if q.get(k) is not None and not 20 < float(q[k]) < 3000:
            raise ValueError("%s is cm, 20-3000" % k)
    if q.get("length_cm") and q.get("wheelbase_cm") and float(q["wheelbase_cm"]) > float(q["length_cm"]) * 0.9:
        raise ValueError("wheelbase_cm must be under 90% of length_cm")
    paint = _paint(q.pop("paint", None))
    q["materials"] = _pick(str(q.get("materials") or "hero"), MATERIALS, "materials")
    name = str(q.get("name") or "Car_" + body.title())
    q["name"] = name.replace(" ", "_")
    at = [float(v) for v in (q.pop("at", None) or [0, 0, 0])]
    before = _mats(scene)
    result, changed = engine(scene, "car", [], dict(q, paint=paint))
    _keep_mats(scene, before, paint)
    info = result["parts"]
    nodes = _adopt(scene, changed, info)
    from ..scene import _nid
    gid = _nid("g")
    scene.data["nodes"].append({"id": gid, "type": "group", "name": q["name"], "parent": None, "layer": "default", "visible": True,
                                "locked": False, "transform": {"pos": at, "rot": [0, 0, 0], "scale": [1, 1, 1]}, "combine": True,
                                "car": {"body": body, "style": result.get("style"), "dims": result["dims"],
                                                                                  "materials": q["materials"]}})
    rig = {"id": _nid("r"), "name": q["name"] + "_Rig", "actions": [],
           "bones": [dict(b, head=[h + o for h, o in zip(b["head"], at)], tail=[t + o for t, o in zip(b["tail"], at)]) for b in result["bones"]]}
    scene.data.setdefault("rigs", []).append(rig)
    for n in nodes.values():
        n["parent"] = gid
        n["skin"] = {"rig": rig["id"], "bone": info[n["name"]].get("bone") or "root"}
    members = list(nodes.values())
    big = max((n for n in members if n.get("car_slot", "").startswith("body_")), key=lambda n: scene.local_mesh(n).tri_count(),
              default=members[0])
    lod = _lods(scene, {"id": big["id"]})
    for n in members:
        n["hq"] = {**(n.get("hq") or {}), "lods": copy.deepcopy(lod["node_data"])}
    ucx = _collision(scene, members, gid, q["name"])
    scene.touch_geom()
    length, width, height, tris = measure(scene, members)
    wheels = sum(1 for n in members if n.get("car_slot", "").startswith("wheel_"))
    summary = "car %s %dx%dx%dcm, %d parts, %d wheels rigged, %s tris, LOD x%d, UCX 1" % (
        body, round(length), round(width), round(height), len(members), wheels, _r(tris), len(lod["node_data"]["ratios"]))
    return {"summary": summary, "ids": [gid, rig["id"], ucx["id"]], "tris": tris, "parts": len(members)}


@op("car_swap", "car_swap(car=<group id>, slot=rim|tire|wheel|headlight|taillight|grille|mirror|bumper_front|bumper_rear|spoiler|"
                "diffuser|exhaust|seat|steering_wheel|dashboard|engine|license_plate|wiper|caliper or an exact slot like mirror_L, "
                "kind?, style?, spokes?, tire?, width?...) rebuild that part in place: same socket transform, parent and bone")
def car_swap(scene, p):
    _check(p, _SWAP_KEYS, "car_swap")
    gid = str(p.get("car") or "")
    group = scene.node(gid)
    if group is None:  # the car's group name works too (the newest car of that name)
        group = next((n for n in reversed(scene.data["nodes"]) if n.get("car") and n.get("name") == gid), None)
        gid = group["id"] if group else gid
    if group is None or not group.get("car"):
        cars = ["%s(%s)" % (n["id"], n.get("name")) for n in scene.data["nodes"] if n.get("car")]
        raise ValueError("No car %r. Cars: %s" % (gid, ", ".join(cars) or "none; build one with car(body=sedan)"))
    slot = str(p.get("slot") or "")
    family = _WHEEL_SLOTS.get(slot, slot)
    members = car_nodes(scene, gid)
    targets = [n for n in members if n.get("car_part") and (n.get("car_slot") == family or n.get("car_slot", "").startswith(family + "_"))]
    if not targets:
        slots = sorted({n["car_slot"].rsplit("_", 1)[0] if n["car_slot"].startswith(("wheel_", "caliper_")) else n["car_slot"]
                        for n in members if n.get("car_part")})
        raise ValueError("No swappable slot %r on %s. Slots: %s (body panels are part of the shell)" % (slot, group["name"], ", ".join(slots)))
    over = {k: v for k, v in p.items() if k in _PART_KEYS and k not in ("car", "slot") and v is not None}
    specs = []
    for n in targets:
        cp = n["car_part"]
        kind = over.get("kind") or cp["kind"]
        spec = _spec(dict(cp["params"], **dict(over, kind=kind)))
        # built at its world spot so the engine's sliver pass sees qa's z-fight cells; the old transform is copied back below
        at = [a + b for a, b in zip(group["transform"]["pos"], n["transform"]["pos"])]
        specs.append(dict(spec, side=cp["side"], name=n["name"], at=at))
    before = _mats(scene)
    mats = _pick(str(over.get("materials") or group["car"].get("materials") or "hero"), MATERIALS, "materials")
    result, changed = engine(scene, "car_part", [], {"parts": specs, "materials": mats})
    _keep_mats(scene, before, None)
    info = result["parts"]
    for spec, n in zip(specs, targets):
        meta = info.get(n["name"])
        if meta is not None:
            meta.update(slot=n["car_slot"], side=spec["side"], params={k: v for k, v in spec.items() if k not in ("name", "at", "side", "kind")})
    fresh = _adopt(scene, changed, info)
    ids = []
    for n in targets:
        new = fresh[n["name"]]
        for key in ("transform", "parent", "skin", "hq", "layer"):
            if key in n:
                new[key] = copy.deepcopy(n[key])
        scene.data["nodes"].remove(n)
        ids.append(new["id"])
    scene.touch_geom()
    kinds = sorted({s["kind"] for s in specs})
    detail = ", ".join("%s=%s" % (k, over[k]) for k in ("spokes", "tire", "style", "kind") if k in over)
    return {"summary": "car_swap %s: %d x %s rebuilt%s, sockets kept" % (slot, len(ids), "/".join(kinds), (" (" + detail + ")") if detail else ""),
            "ids": ids}


_qa_collision = HQ_OPS["collision"]


@functools.wraps(_qa_collision)
def _collision_op(scene, p):
    """collision on a car group would hull every part (58 extra UCX); car() already made its hull, so skip it."""
    ids = p.get("ids") or ([p["id"]] if p.get("id") else [])
    cars = [n for n in map(scene.node, ids) if n and n.get("car")]
    if not cars:
        return _qa_collision(scene, p)
    said = "; ".join("collision: car %s already has %d UCX hull(s), skipped" % (
        g["name"], sum(1 for n in scene.data["nodes"] if n.get("parent") == g["id"] and n.get("layer") == "collision")) for g in cars)
    rest = [i for i in ids if i not in {g["id"] for g in cars}]
    if not rest:
        return {"summary": said, "ids": []}
    out = _qa_collision(scene, dict(p, id=None, ids=rest))
    return dict(out, summary=said + "; " + out["summary"])


HQ_OPS["collision"] = _collision_op
