"""HQ tufts: pointed flame tufts grown from a surface region and fused into one mass. The bpy work is engine/ops/tufts.py.

Lengths are Forge cm, world space. Defaults scale with the host's size, so tufts(id=head, mane="lion") just works.
"""
import difflib

from . import engine, op

MANES = ("lion",)
TIPS = ("flame",)
_KEYS = ("id", "region", "mane", "count", "length", "width", "curl", "layers", "tip", "seed", "budget", "sweep", "smooth", "wait")


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


@op("tufts", "tufts(id, region={sphere|ring|faces|material}|mane=lion, count, length, width, curl, layers=2, tip=flame, seed, budget=8000) pointed flame tufts fused into one mass")
def tufts(scene, p):
    unknown = [k for k in p if k not in _KEYS]
    if unknown:
        raise ValueError("Unknown param %r for tufts. Params: %s" % (unknown[0], ", ".join(_KEYS)))
    node = _host(scene, p.get("id"))
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
