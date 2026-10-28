"""HQ groom ops: hair strands, hair cards, cloth bake and fur shells. Maths lives in engine/ops/groom.py + cloth.py."""
import difflib
import math
import zlib
from pathlib import Path

from .. import rig as rigs
from ..engine.ops import cloth as sheet
from ..engine.ops import groom as strand
from ..kernel.mesh import Mesh
from . import op

REGIONS = ("all", "top", "jaw", "back")
PINS = {"shoulders": 0.82, "waist": 0.55, "head": 0.92}


def _call(name, params, timeout=600.0):
    from ..engine import client
    return client.call(name, params, timeout)


def _node(scene, ident, what="id"):
    node = scene.node(ident) if ident else None
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No object %r for %s. Objects: %s" % (ident, what, ", ".join(objs) or "none"))
    return node


def _textures(scene):
    folder = Path(scene.folder) / "textures"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _material(scene, name, color, **extra):
    """Create or update the material called name; returns its id."""
    from ..scene import _mat, _nid
    mat = next((m for m in scene.data["materials"] if m.get("name") == name), None)
    if mat is None:
        mat = _mat(_nid("m"), name, list(color) + [1.0])
        scene.data["materials"].append(mat)
    mat.update(base_color=list(color) + [1.0], **extra)
    return mat["id"]


def _add_object(scene, name, mesh):
    from ..scene import _nid
    node = {"id": _nid("o"), "type": "object", "name": name, "parent": None, "layer": "default", "visible": True,
            "locked": False, "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
            "prototype": scene.adopt_mesh(mesh), "modifiers": [],
            "material": mesh.face_mat[0] if mesh.face_mat else "default"}
    scene.data["nodes"].append(node)
    scene.touch_geom()
    return node


def region_tris(mesh, region):
    """World triangles of a named region (all, top, jaw, back) or an explicit face list."""
    lo, hi = mesh.bounds()
    height = (hi[2] - lo[2]) or 1.0
    shape = None
    if isinstance(region, dict):  # {"sphere": [x,y,z,r]} or {"box": [x0,y0,z0,x1,y1,z1]}, world cm
        kind = next((k for k in region if k in ("sphere", "box")), None)
        want = {"sphere": 4, "box": 6}.get(kind)
        if kind is None or len(region[kind]) != want:
            raise ValueError("Region dict needs {'sphere': [x,y,z,r]} or {'box': [x0,y0,z0,x1,y1,z1]} in world cm, got %r" % (region,))
        shape = (kind, [float(c) for c in region[kind]])
        region = "all"
    picks = set(region) if isinstance(region, (list, tuple)) else None
    if picks is None and region not in REGIONS:
        close = difflib.get_close_matches(str(region), REGIONS, n=1)
        raise ValueError("No region %r.%s Regions: %s, or face indices" % (region, " Did you mean %r?" % close[0] if close else "", ", ".join(REGIONS)))
    tris = []
    for fi, face in enumerate(mesh.faces):
        pts = [mesh.verts[i] for i in face]
        n = [0.0, 0.0, 0.0]
        for i in range(len(pts)):  # Newell
            a, b = pts[i], pts[(i + 1) % len(pts)]
            n[0] += (a[1] - b[1]) * (a[2] + b[2])
            n[1] += (a[2] - b[2]) * (a[0] + b[0])
            n[2] += (a[0] - b[0]) * (a[1] + b[1])
        length = math.sqrt(sum(c * c for c in n)) or 1.0
        n = [c / length for c in n]
        z = sum(q[2] for q in pts) / len(pts)
        c = [sum(q[i] for q in pts) / len(pts) for i in range(3)]
        if shape is not None:
            s = shape[1]
            keep = math.dist(c, s[:3]) <= s[3] if shape[0] == "sphere" else all(min(s[i], s[i + 3]) <= c[i] <= max(s[i], s[i + 3]) for i in range(3))
        elif picks is not None:
            keep = fi in picks
        elif region == "top":
            keep = n[2] > 0.3 and z >= lo[2] + 0.6 * height
        elif region == "jaw":
            keep = n[1] < -0.1 and z <= lo[2] + 0.45 * height
        elif region == "back":
            keep = n[1] > 0.3
        else:
            keep = True
        if keep:
            tris += [[list(pts[0]), list(pts[k]), list(pts[k + 1])] for k in range(1, len(pts) - 1)]
    return tris


def _hair_params(scene, node, p):
    style = strand.style_of(p)
    region = p.get("region") or style["region"]
    keys = ("style", "color", "density", "length", "clump", "curl", "frizz")
    base = {k: p[k] for k in keys if p.get(k) is not None}
    base["region"] = region
    base["seed"] = int(p.get("seed") if p.get("seed") is not None else zlib.crc32(node["id"].encode()) & 0xFFFF)
    return base, region_tris(scene.world_mesh(node), region)


@op("hair", "hair(id, style=short|long|ponytail|afro|fur|beard, region?, color?) strands")
def hair(scene, p):
    node = _node(scene, p.get("id"))
    params, tris = _hair_params(scene, node, p)
    strand.rgb(params.get("color"))
    result = _call("hair", dict(params, tris=tris, name=(node.get("name") or node["id"]) + "_hair"))
    from ..scene import _nid
    grooms = scene.data.setdefault("grooms", [])
    rec = next((g for g in grooms if g["object"] == node["id"] and g["params"].get("style") == params.get("style")), None)
    if rec is None:
        rec = {"id": _nid("g"), "object": node["id"]}
        grooms.append(rec)
    rec.update(params=params, count=result["count"], points=result["points"],
               material=_material(scene, "Hair_" + str(params.get("style") or "short"), strand.rgb(params.get("color")), roughness=0.45))
    warnings = ["strands capped at %d; lower density or shrink the region" % strand.MAX_STRANDS] if result.get("capped") else []
    return {"ids": [node["id"]], "groom": rec["id"], "strands": result["count"],
            "summary": "%s: %d strands over %s cm2 (%s)" % (result["style"], result["count"], result["area"], params.get("color", "brown")),
            "warnings": warnings}


@op("hair_cards", "hair_cards(id, cards=120, atlas=1K) strands -> cards")
def hair_cards(scene, p):
    node = _node(scene, p.get("id"))
    grooms = [g for g in scene.data.get("grooms") or [] if g["object"] == node["id"] and (not p.get("groom") or g["id"] == p["groom"])]
    if not grooms:
        raise ValueError("No hair on %s. Run hair(id=%r, style=...) first" % (node["id"], node["id"]))
    rec = grooms[-1]
    tris = region_tris(scene.world_mesh(node), rec["params"]["region"])
    textures = _textures(scene)
    mane = rec["params"].get("style") == "mane"
    budget = p.get("tris") or (3200 if mane else 2800)
    result = _call("hair_cards", dict(rec["params"], tris=tris, cards=p.get("cards") or (220 if mane else 120), atlas=p.get("atlas") or "1K",
                                      budget=budget, folder=str(textures), stem="hair_" + rec["id"]))
    color = strand.rgb(rec["params"].get("color"))
    mid = _material(scene, "HairCards", color, roughness=0.45, opacity=1.0, alpha_mode="MASK", alpha_cutoff=0.35, double_sided=True,
                    textures={"base_color": result["color_file"], "groom_data": result["data_file"]})
    mesh = Mesh()
    mesh.verts, mesh.faces, mesh.uvs = result["verts"], result["faces"], result["uvs"]
    mesh.face_mat = [mid] * len(mesh.faces)
    cards = _add_object(scene, (node.get("name") or node["id"]) + "_cards", mesh)
    warnings = ["%d tris is over the %s budget; lower cards" % (result["tris"], budget)] if result["over_budget"] else []
    return {"ids": [cards["id"]], "tris": result["tris"], "cards": result["cards"], "atlas": result["atlas"],
            "summary": "%d cards, %d tris, %dK atlas (masked, two-sided)" % (result["cards"], result["tris"], result["atlas"] // 1024 or 1),
            "warnings": warnings}


# ---- cloth ------------------------------------------------------------------


def make_spheres(verts, near, reach, limit=40):
    """Sphere proxies around the vertices within reach of the pin point, at most ~limit of them."""
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    size = max(hi[i] - lo[i] for i in range(3)) / 8.0 or 1.0
    while True:
        cells = {}
        for v in verts:
            cells.setdefault(tuple(int(v[i] // size) for i in range(3)), []).append(v)
        out = []
        for group in cells.values():
            c = [sum(v[i] for v in group) / len(group) for i in range(3)]
            r = max(1.0, max(math.dist(c, v) for v in group) + 0.5)
            if math.dist(c, near) <= reach + r:
                out.append([round(c[0], 3), round(c[1], 3), round(c[2], 3), round(r, 3)])
        if len(out) <= limit:
            return out
        size *= 1.25


def _pin(pin, lo, hi):
    if isinstance(pin, (list, tuple)) and len(pin) == 3:
        return [float(v) for v in pin]
    key = str(pin or "shoulders")
    if key not in PINS:
        close = difflib.get_close_matches(key, PINS, n=1)
        raise ValueError("No pin %r.%s Pins: %s, or [x,y,z]" % (key, " Did you mean %r?" % close[0] if close else "", ", ".join(PINS)))
    return [(lo[0] + hi[0]) / 2, hi[1] - 0.1 * (hi[1] - lo[1]), lo[2] + PINS[key] * (hi[2] - lo[2])]


def _free_prefix(rig, base):
    taken = {b["name"] for b in rig["bones"]}
    prefix = base
    while any(n.startswith(prefix + "_") for n in taken):
        prefix += "2"
    return prefix


@op("cloth", "cloth(preset=cape|scarf|skirt|flag, pin, collide, frames=32, bake_to=bones|shapes) -> clip")
def cloth(scene, p):
    from ..scene import _nid
    pre = sheet.preset_of(p.get("preset"))
    cloth_id = p.get("id")
    cloth_node = _node(scene, cloth_id) if cloth_id else None
    collide = p.get("collide")
    collide = [collide] if isinstance(collide, str) else list(collide or [])
    if not collide:  # the biggest other object is the body
        others = [n for n in scene.data["nodes"] if n.get("type") == "object" and n is not cloth_node]
        if not others:
            raise ValueError("Nothing to collide with. Pass collide=<object id>")
        volume = lambda n: math.prod(max(1e-3, b - a) for a, b in zip(*scene.world_mesh(n).bounds()))
        collide = [max(others, key=volume)["id"]]
    body = [v for cid in collide for v in scene.world_mesh(_node(scene, cid, "collide")).verts]
    lo = [min(v[i] for v in body) for i in range(3)]
    hi = [max(v[i] for v in body) for i in range(3)]
    pin = _pin(p.get("pin"), lo, hi)
    mode = str(p.get("bake_to") or "bones")
    if mode not in ("bones", "shapes"):
        raise ValueError("bake_to %r is not bones or shapes" % mode)
    frames = int(p.get("frames") or 32)
    rig = None
    rig_id = p.get("rig") or ((_node(scene, collide[0]).get("skin") or {}).get("rig"))
    if rig_id:
        rig = rigs.find_rig(scene, rig_id)
    else:
        rig = {"id": _nid("r"), "name": "Cloth rig", "bones": [], "actions": []}
    prefix = _free_prefix(rig, pre["name"])
    verts = [list(v) for v in scene.world_mesh(cloth_node).verts] if cloth_node else None
    result = _call("cloth", {"preset": pre["name"], "pin": pin, "spheres": make_spheres(body, pin, pre["length"] + pre["width"]),
                             "frames": frames, "bake_to": mode, "verts": verts, "prefix": prefix, "sway": p.get("sway", 4.0)}, 600.0)
    warnings = []
    if cloth_node is None:
        mesh = Mesh()
        mesh.verts, mesh.faces = result["grid_verts"], result["grid_faces"]
        cols = result["cols"]
        mesh.uvs = [[[(i % cols) / (cols - 1), 1 - (i // cols) / result["segs"]] for i in f] for f in mesh.faces]
        mesh.face_mat = [_material(scene, "Cloth", (0.55, 0.1, 0.12), roughness=0.8, double_sided=True)] * len(mesh.faces)
        cloth_node = _add_object(scene, pre["name"], mesh)
    mesh = scene.local_mesh(cloth_node)
    if mode == "bones":
        bones = result["bones"]
        if rig["bones"]:  # hang the chain off the bone nearest the pin
            near = min(rig["bones"], key=lambda b: math.dist(b["head"], pin))
            bones[0]["parent"] = near["name"]
        rigs._add_bones(rig, bones)
        for k, bone in enumerate(bones):
            mesh.attrs["wt:" + bone["name"]] = [w.get(str(k), w.get(k, 0.0)) for w in result["weights"]]
        cloth_node["skin"] = {"rig": rig["id"], "paint": True}
        clip = {"id": _nid("a"), "name": pre["name"], "fps": 30, "frames": frames, "loop": False, "tracks": result["tracks"]}
        what = "%d-bone chain" % len(bones)
    else:
        if not rig["bones"]:
            rigs._add_bones(rig, [{"name": prefix + "_root", "parent": None, "head": [round(v, 3) for v in pin],
                                   "tail": [round(pin[0], 3), round(pin[1], 3), round(pin[2] + 5, 3)]}])
        root = rig["bones"][0]["name"]
        morphs = {}
        for name, deltas in result["shapes"].items():
            mesh.attrs["sk:" + name] = deltas
            f = int(name.rsplit("_", 1)[1])
            keys = ([[f - 1, [0.0], "linear"]] if f else []) + [[f, [1.0], "linear"], [f + 1, [0.0], "linear"]]
            morphs[name] = keys
        cloth_node["skin"] = {"rig": rig["id"], "bone": root}
        clip = {"id": _nid("a"), "name": pre["name"], "fps": 30, "frames": frames, "loop": False, "tracks": {}, "morphs": morphs}
        what = "%d shape keys" % len(morphs)
    scene.write_mesh(cloth_node, mesh)
    if rig not in scene.data.setdefault("rigs", []):
        scene.data["rigs"].append(rig)
    rig.setdefault("actions", []).append(clip)
    scene.touch_geom()
    if result["clearance"] is not None and result["clearance"] < 0:
        warnings.append("cloth penetrates the body by %.2f cm" % -result["clearance"])
    return {"ids": [cloth_node["id"], rig["id"]], "clip": clip["id"], "frames": frames, "clearance": result["clearance"] or 0.0,
            "summary": "%s: %s, %d-frame clip %s" % (pre["name"], what, frames, clip["id"]), "warnings": warnings}


# ---- fur --------------------------------------------------------------------


UEFN_BUDGET = 20000
PROXY_TRIS = 1500


@op("fur_shells", "fur_shells(id, layers=8, length=2, budget=20000, decimate_shells=true) skinned alpha-noise fur shells")
def fur_shells(scene, p):
    node = _node(scene, p.get("id"))
    mesh = scene.local_mesh(node)
    base_tris = mesh.tri_count()
    layers = max(1, min(32, int(p.get("layers") or 8)))
    budget = p.get("budget")
    budget = UEFN_BUDGET if budget is None and p.get("target", "uefn") == "uefn" else int(budget or 0)
    proxy = p.get("decimate_shells", True)
    proxy = PROXY_TRIS if proxy is True else int(proxy or 0)
    skin = mesh.face_mat[0] if mesh.face_mat else "default"
    base = (scene.materials().get(skin) or {}).get("base_color") or [0.75, 0.6, 0.45, 1]
    color = p.get("color") or base[:3]
    shaped = [list(map(list, uv)) for uv in mesh.uvs] if len(mesh.uvs) == len(mesh.faces) and all(len(u) == len(f) for u, f in zip(mesh.uvs, mesh.faces)) else None
    result = _call("fur_shells", {"verts": mesh.verts, "faces": mesh.faces, "uvs": shaped, "layers": layers, "length": p.get("length") or 2.0,
                                  "color": color, "folder": str(_textures(scene)), "seed": zlib.crc32(node["id"].encode()) & 0xFFFF,
                                  "proxy": proxy, "budget": budget, "base_tris": base_tris})
    warnings = []
    if result["layers"] < result["wanted"]:
        warnings.append("fur layers cut from %d to %d to fit %d tris (body %d + %d x %d per shell)" % (
            result["wanted"], result["layers"], budget, base_tris, result["layers"], result["per_layer"]))
    if result["layers"] < 1:
        return {"ids": [node["id"]], "layers": 0, "tris_added": 0, "summary": "no fur shells: body already uses %d of %d tris" % (base_tris, budget),
                "warnings": warnings + ["raise budget, or fur_bake for texture-only fur"]}
    layers = result["layers"]
    rgb = strand.rgb(color)
    shells = [_material(scene, "FurShell_%02d" % k, [c * (0.6 + 0.4 * k / layers) for c in rgb], roughness=0.9, opacity=1.0,
                        alpha_mode="MASK", alpha_cutoff=round(0.15 + 0.8 * k / layers, 3), double_sided=True,
                        textures={"base_color": result["noise_file"]}) for k in range(1, layers + 1)]
    count = len(mesh.verts)
    for key, values in list(mesh.attrs.items()):
        if isinstance(values, list) and len(values) == count:
            copy = [list(values[r]) if isinstance(values[r], list) else values[r] for r in result["rep"]]  # nearest-vertex transfer
            mesh.attrs[key] = values + copy * layers
    mesh.verts = mesh.verts + result["verts"]
    mesh.faces = mesh.faces + [list(f) for f in result["faces"]]
    mesh.uvs = [list(map(list, uv)) for uv in result["base_uvs"]] + result["uvs"]
    mesh.face_mat = list(mesh.face_mat) + [shells[k - 1] for k in result["shell"]]
    scene.write_mesh(node, mesh)
    return {"ids": [node["id"]], "layers": layers, "tris_added": mesh.tri_count() - base_tris,
            "tris": mesh.tri_count(), "summary": "%d fur shells, +%d tris (%d total)" % (layers, mesh.tri_count() - base_tris, mesh.tri_count()),
            "warnings": warnings}


@op("fur_bake", "fur_bake(id, size=1024) bake fur color + direction normal into the body's BaseColor and Normal")
def fur_bake(scene, p):
    node = _node(scene, p.get("id"))
    mesh = scene.local_mesh(node)
    mid = mesh.face_mat[0] if mesh.face_mat else None
    mat = next((m for m in scene.data["materials"] if m.get("id") == mid), None)
    if mat is None:
        raise ValueError("%s has no material to bake into. Give it one with material(...) first" % node["id"])
    color = p.get("color") or (mat.get("base_color") or [0.75, 0.6, 0.45])[:3]
    name = "".join(c for c in (node.get("name") or node["id"]) if c.isalnum() or c == "_") or node["id"]
    result = _call("fur_bake", {"size": int(p.get("size") or 1024), "color": color, "folder": str(_textures(scene)),
                                "color_file": "T_%s_BC.png" % name, "normal_file": "T_%s_N.png" % name,
                                "seed": zlib.crc32(node["id"].encode()) & 0xFFFF})
    mat.setdefault("textures", {}).update(base_color=result["color_file"], normal=result["normal_file"])
    return {"ids": [node["id"]], "size": result["size"], "textures": [result["color_file"], result["normal_file"]],
            "summary": "baked %s + %s at %d (fur color and direction normal on %s)" % (result["color_file"], result["normal_file"], result["size"], mat.get("name"))}
