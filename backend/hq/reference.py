"""HQ reference ops: trace drawings into a 3D blockout and split it into colour parts. Maths lives in engine/ops/reference.py."""
from pathlib import Path

from ..kernel.mesh import Mesh
from . import op


def _call(name, params, timeout=600.0):
    from ..engine import client
    return client.call(name, params, timeout)


def _path(value, what):
    if not value:
        raise ValueError("Pass %s=<full path to a PNG or JPG drawing>" % what)
    path = Path(str(value)).expanduser()
    if not path.is_file():
        raise ValueError("No image at %s for %s. Pass a full path to an existing PNG or JPG" % (path, what))
    return str(path)


def _material(scene, name, color):
    from ..scene import _mat, _nid
    mat = next((m for m in scene.data["materials"] if m.get("name") == name), None)
    if mat is None:
        mat = _mat(_nid("m"), name, list(color) + [1.0])
        scene.data["materials"].append(mat)
    mat["base_color"] = list(color) + [1.0]
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


def _node(scene, ident):
    node = scene.node(ident) if ident else None
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No object %r. Objects: %s" % (ident, ", ".join(objs) or "none"))
    return node


@op("blockout_from_refs", "blockout_from_refs(front, side?, back?, height=100, name, smooth=2, res=48) trace silhouettes into a body")
def blockout_from_refs(scene, p):
    params = {"front": _path(p.get("front"), "front"), "height": p.get("height") or 100.0,
              "smooth": 2 if p.get("smooth") is None else p["smooth"], "res": p.get("res") or 48, "mode": p.get("mode")}
    if p.get("side"):
        params["side"] = _path(p["side"], "side")
    result = _call("blockout_from_refs", params)
    mesh = Mesh()
    mesh.verts, mesh.faces = result["verts"], result["faces"]
    mesh.face_mat = [_material(scene, "Body", (0.85, 0.6, 0.25))] * len(mesh.faces)
    node = _add_object(scene, str(p.get("name") or "Blockout"), mesh)
    warnings = list(result["warnings"])
    if p.get("back"):  # the back drawing mirrors the front silhouette: a large mismatch means a wrong pose or crop
        back = _call("blockout_from_refs", dict(params, front=_path(p["back"], "back"), side=None, mode="inflate"))
        w = lambda r: r["hi"][0] - r["lo"][0]
        if abs(w(back) - w(result)) > 0.25 * w(result):
            warnings.append("back drawing is %.0f cm wide vs %.0f cm front; check both are the same pose and crop" % (w(back), w(result)))
    lo, hi = result["lo"], result["hi"]
    return {"ids": [node["id"]], "quads": result["quads"], "mode": result["mode"], "warnings": warnings,
            "summary": "%s: %d quads (%s), bounds %s..%s cm" % (node["id"], result["quads"], result["mode"],
                                                              [round(c) for c in lo], [round(c) for c in hi])}


def _split(scene, node, mesh, mids, names):
    """One object per material, in place of the original."""
    made = []
    for mid, name in zip(mids, names):
        faces = [i for i, m in enumerate(mesh.face_mat) if m == mid]
        if not faces:
            continue
        used = sorted({v for i in faces for v in mesh.faces[i]})
        remap = {v: n for n, v in enumerate(used)}
        part = Mesh()
        part.verts = [mesh.verts[v] for v in used]
        part.faces = [[remap[v] for v in mesh.faces[i]] for i in faces]
        part.face_mat = [mid] * len(faces)
        if len(mesh.uvs) == len(mesh.faces):
            part.uvs = [mesh.uvs[i] for i in faces]
        child = _add_object(scene, "%s_%s" % (node.get("name") or node["id"], name), part)
        child["transform"] = dict(node["transform"])
        made.append(child["id"])
    scene.data["nodes"].remove(node)
    scene.touch_geom()
    return made


@op("ref_parts", "ref_parts(id, front, palette?=['#rrggbb'|{name,color}], k=6, split=false) colour the blockout from the drawing")
def ref_parts(scene, p):
    node = _node(scene, p.get("id"))
    world, mesh = scene.world_mesh(node), scene.local_mesh(node)
    if not mesh.faces:
        raise ValueError("%s has no faces to colour" % node["id"])
    points = [[sum(world.verts[v][c] for v in f) / len(f) for c in (0, 2)] for f in world.faces]
    lo = [min(v[c] for v in world.verts) for c in (0, 2)]
    hi = [max(v[c] for v in world.verts) for c in (0, 2)]
    result = _call("ref_parts", {"front": _path(p.get("front"), "front"), "palette": p.get("palette"), "k": p.get("k") or 6,
                                 "points": points, "bounds": {"lo": lo, "hi": hi}})
    mids = [_material(scene, name, color) for name, color in zip(result["names"], result["colors"])]
    mesh.face_mat = [mids[a] for a in result["assign"]]
    scene.write_mesh(node, mesh)
    ids = _split(scene, node, mesh, mids, result["names"]) if p.get("split") else [node["id"]]
    used = {a for a in result["assign"]}
    summary = ", ".join("%s %d" % (result["names"][a], result["assign"].count(a)) for a in sorted(used, key=lambda a: -result["assign"].count(a)))
    return {"ids": ids, "parts": len(used), "summary": ("split into %d objects: " if p.get("split") else "%d materials: ") % len(used) + summary}
