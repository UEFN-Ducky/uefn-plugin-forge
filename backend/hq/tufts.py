"""HQ tufts: pointed flame tufts grown from a surface region and fused into one mass. The bpy work is engine/ops/tufts.py.

Lengths are Forge cm, world space. Defaults scale with the host's size, so tufts(id=head, mane="lion") just works.
"""
import difflib

from . import engine, op

MANES = ("lion",)
TIPS = ("flame",)
STYLES = ("leaf", "flame")
TARGETS = ("head", "tail")
_KEYS = ("id", "region", "mane", "count", "length", "width", "curl", "layers", "tip", "seed", "budget", "sweep", "smooth", "wait",
         "style", "rows", "face_ring", "peak", "beard", "target", "at", "dir", "material")


def _host(scene, ident):
    node = scene.node(str(ident or ""))
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No object %r for tufts. Objects: %s" % (ident, ", ".join(objs) or "none"))
    return node


def _lion(lo, hi):
    """Ring around the face + crown + chest bib, from the head's world bounds."""
    cx, cy, cz = [(a + b) / 2 for a, b in zip(lo, hi)]
    r = max(b - a for a, b in zip(lo, hi)) / 2
    return [{"ring": [cx, cy, cz, 0.55 * r, 0.5 * r]},
            {"sphere": [cx, cy, cz + 0.8 * r, 0.55 * r]},
            {"sphere": [cx, cy - 0.35 * r, cz - 0.7 * r, 0.5 * r]}], r


def _regions(scene, mesh, region, mane):
    lo, hi = mesh.bounds()
    r = max(b - a for a, b in zip(lo, hi)) / 2 or 1.0
    if mane:
        if mane not in MANES:
            close = difflib.get_close_matches(str(mane), MANES, n=1)
            raise ValueError("No mane %r.%s Manes: %s" % (mane, " Did you mean %r?" % close[0] if close else "", ", ".join(MANES)))
        return _lion(lo, hi)[0], r
    if region is None:
        raise ValueError("Give region={'sphere':[x,y,z,r]} | {'ring':[x,y,z,r,w]} | {'faces':[...]} | {'material':name} (or a list of them), or mane='lion'")
    specs = []
    for item in (region if isinstance(region, list) and region and isinstance(region[0], dict) else [region]):
        if isinstance(item, list):  # bare face list
            item = {"faces": item}
        if not isinstance(item, dict):
            raise ValueError("region must be a dict like {'sphere': [x,y,z,r]}, got %r" % (item,))
        for kind, value in item.items():
            if kind == "sphere" and len(value) == 4 or kind == "ring" and len(value) == 5:
                specs.append({kind: [float(v) for v in value]})
            elif kind == "faces":
                specs.append({"faces": [int(i) for i in value]})
            elif kind == "material":
                ids = {m["id"] for m in scene.data["materials"] if value in (m["id"], m.get("name"))}
                if not ids:
                    raise ValueError("No material %r. Materials: %s" % (value, ", ".join(m.get("name", m["id"]) for m in scene.data["materials"])))
                specs.append({"faces": [i for i, m in enumerate(mesh.face_mat) if m in ids]})
            else:
                raise ValueError("Bad region %r: sphere needs 4 numbers [x,y,z,r], ring 5 [x,y,z,r,w], faces a list, material a name" % (item,))
    return specs, r


def _leaf(scene, node, p, target, mane):
    """style=leaf: layered leaf tufts (mane=lion on a head, or target=tail at a tip point)."""
    mesh = scene.world_mesh(node)
    if target == "tail":
        at = p.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 3):
            raise ValueError("target='tail' needs at=[x,y,z], the tail tip in world cm (e.g. the tail bone's tail point)")
        lo, hi = mesh.bounds()
        r = max(b - a for a, b in zip(lo, hi)) / 2 or 1.0
        q = {"count": 14, "rows": 2, "face_ring": 0, "peak": 0, "beard": 0, "at": [float(v) for v in at]}
        if p.get("dir") is not None:
            q["dir"] = [float(v) for v in p["dir"]]
        length = 0.18 * r
    else:
        if not mane:
            raise ValueError("style='leaf' grows a mane: pass mane='lion' (or target='tail' with at=[x,y,z]; style='flame' takes a region)")
        _, r = _regions(scene, mesh, None, mane)
        q = {"count": 160, "rows": 6, "face_ring": 18, "peak": 7, "beard": 9}
        length = 0.55 * r
    for k in ("count", "rows", "face_ring", "peak", "beard"):
        if k in p:
            q[k] = int(p[k])
    q.update(style="leaf", target=target, length=float(p.get("length", length)), curl=float(p.get("curl", 0.15)),
             seed=int(p.get("seed", 1)))
    if p.get("width") is not None:
        q["width"] = float(p["width"])
    if p.get("material"):
        q["material"] = str(p["material"])
    fixed = q["face_ring"] + q["peak"] + q["beard"]
    if min(q["face_ring"], q["peak"], q["beard"]) < 0 or q["rows"] < 2 or q["length"] <= 0 or q.get("width", 1) <= 0:
        raise ValueError("face_ring, peak, beard >= 0; rows >= 2; length > 0 cm; width > 0 cm")
    if q["count"] < fixed + (q["rows"] if target == "head" else 1):
        raise ValueError("count %d is too small for face_ring+peak+beard=%d plus %d rows: raise count (default 160) or lower those"
                         % (q["count"], fixed, q["rows"]))
    result, changed = engine(scene, "tufts", [node["id"]], q)
    return {**result, "ids": changed or [node["id"]]}


@op("tufts", "tufts(id, mane=lion|target=tail+at, style=leaf, count=160, rows=6, length, face_ring=18, peak=7, beard=9) layered leaf tufts (default); style=flame + region|mane for fused flames (layers, tip, budget)")
def tufts(scene, p):
    unknown = [k for k in p if k not in _KEYS]
    if unknown:
        raise ValueError("Unknown param %r for tufts. Params: %s" % (unknown[0], ", ".join(_KEYS)))
    node = _host(scene, p.get("id"))
    target = p.get("target", "head")
    if target not in TARGETS:
        raise ValueError("No target %r. Targets: %s" % (target, ", ".join(TARGETS)))
    style = p.get("style") or ("leaf" if p.get("mane") or target == "tail" else "flame")
    if style not in STYLES:
        raise ValueError("No style %r. Styles: %s" % (style, ", ".join(STYLES)))
    if style == "leaf":
        mane = p.get("mane")
        if mane and mane not in MANES:
            _regions(scene, scene.world_mesh(node), None, mane)  # raises with the did-you-mean
        return _leaf(scene, node, p, target, mane)
    if p.get("tip", "flame") not in TIPS:
        raise ValueError("No tip %r. Tips: %s" % (p.get("tip"), ", ".join(TIPS)))
    specs, r = _regions(scene, scene.world_mesh(node), p.get("region"), p.get("mane"))
    q = {"regions": specs, "count": int(p.get("count", 140)), "length": float(p.get("length", 0.4 * r)),
         "width": float(p.get("width", 0.22 * r)), "curl": float(p.get("curl", 0.3)), "layers": int(p.get("layers", 2)),
         "seed": int(p.get("seed", 1)), "budget": int(p.get("budget", 8000)), "sweep": float(p.get("sweep", 0.35)),
         "smooth": int(p.get("smooth", 4))}
    if q["count"] < 1 or q["length"] <= 0 or q["width"] <= 0 or q["budget"] < 200:
        raise ValueError("count >= 1, length > 0 cm, width > 0 cm, budget >= 200 tris")
    result, changed = engine(scene, "tufts", [node["id"]], q)
    return {**result, "ids": changed or [node["id"]]}
