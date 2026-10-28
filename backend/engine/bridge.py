"""Host side of the Forge <-> bpy bridge: pack only the objects an op needs, read results back.

Bulk arrays travel as .npy files (written and read here without numpy); everything stays in
Forge units, the engine converts at its edge (see engine/ops/bridge.py).
"""
import array
import ast
import shutil
import struct
import tempfile
from pathlib import Path

from .. import rig as rigs
from ..kernel.mesh import Mesh
from ..transforms import decompose, world
from . import client

_DESCR = {"d": "<f8", "i": "<i4"}
_CODE = {"<f8": "d", "<i4": "i", "<f4": "f"}


def write_npy(path, values, shape, kind="d"):
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %r, }" % (_DESCR[kind], tuple(shape))
    header += " " * (63 - (10 + len(header)) % 64) + "\n"
    Path(path).write_bytes(b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin1")
                           + array.array(kind, values).tobytes())


def read_npy(path):
    """(flat array.array, shape) for the little-endian float/int arrays the engine writes."""
    raw = Path(path).read_bytes()
    if raw[:6] != b"\x93NUMPY":
        raise ValueError("Not a .npy file: " + str(path))
    if raw[6] == 1:
        size, start = struct.unpack("<H", raw[8:10])[0], 10
    else:
        size, start = struct.unpack("<I", raw[8:12])[0], 12
    header = ast.literal_eval(raw[start:start + size].decode("latin1"))
    code = _CODE.get(header["descr"])
    if code is None or header["fortran_order"]:
        raise ValueError("Unsupported .npy layout %r" % header["descr"])
    values = array.array(code)
    values.frombytes(raw[start + size:])
    return values, tuple(header["shape"])


def _rows(values, width):
    return [list(values[i:i + width]) for i in range(0, len(values), width)]


def pack(scene, ids, folder):
    """Payload for the objects under ids (groups expand) plus the rigs they are bound to or name."""
    folder = Path(folder)
    nodes = rigs._objects_under(scene, list(ids))
    wanted = {(n.get("skin") or {}).get("rig") for n in nodes} | set(ids)
    rig_list = [r for r in scene.data.get("rigs") or [] if r["id"] in wanted]
    by_rig = {r["id"]: r for r in rig_list}
    materials = scene.materials()
    used, objects = {}, []
    for n, node in enumerate(nodes):
        mesh = scene.local_mesh(node)
        tag = "o%d_" % n

        def put(name, values, shape, kind="d"):
            write_npy(folder / (tag + name + ".npy"), values, shape, kind)
            return tag + name + ".npy"

        slots = list(dict.fromkeys(mesh.face_mat)) or ["default"]
        slot_of = {m: i for i, m in enumerate(slots)}
        loops = [i for face in mesh.faces for i in face]
        arrays = {
            "verts": put("verts", [c for v in mesh.verts for c in v], (len(mesh.verts), 3)),
            "sizes": put("sizes", [len(f) for f in mesh.faces], (len(mesh.faces),), "i"),
            "loops": put("loops", loops, (len(loops),), "i"),
            "face_mat": put("face_mat", [slot_of[m] for m in mesh.face_mat], (len(mesh.faces),), "i"),
            "uv": put("uv", [c for face in mesh.uvs for uv in face for c in uv[:2]], (len(loops), 2)),
        }
        entry = {"id": node["id"], "name": node.get("name") or node["id"], "matrix": world(scene, node["id"]),
                 "smooth": mesh.smooth, "mats": slots, "skin": node.get("skin"), "auto_weights": False}
        attrs = mesh.attrs
        if attrs.get("nrm:source"):
            arrays["normals"] = put("normals", [c for v in attrs["nrm:source"] for c in v], (len(mesh.verts), 3))
        colors = [k for k in attrs if k.startswith("col:")]
        if colors:
            entry["colors"] = [k[4:] for k in colors]
            arrays["colors"] = put("colors", [c for k in colors for v in attrs[k] for c in (list(v) + [1.0] * 4)[:4]],
                                   (len(colors), len(mesh.verts), 4))
        shapes = [k for k in attrs if k.startswith("sk:")]
        if shapes:
            entry["shapes"] = [k[3:] for k in shapes]
            arrays["shapes"] = put("shapes", [c for k in shapes for v in attrs[k] for c in v], (len(shapes), len(mesh.verts), 3))
        groups = [k for k in attrs if k.startswith("wt:")]
        skin = node.get("skin") or {}
        rig = by_rig.get(skin.get("rig"))
        if rig and not skin.get("paint"):
            # Auto and single-bone skins become real vertex groups; they come back as the same skin.
            bones = rigs.ordered(rig)
            joints, weights = rigs.bind_weights(scene.world_mesh(node).verts, bones, skin, attrs)
            matrix = [0.0] * (len(mesh.verts) * len(bones))
            for v, (js, ws) in enumerate(zip(joints, weights)):
                for j, w in zip(js, ws):
                    if w > 0:
                        matrix[v * len(bones) + j] += w
            entry.update(groups=[b["name"] for b in bones], auto_weights=True)
            arrays["weights"] = put("weights", matrix, (len(mesh.verts), len(bones)))
        elif groups:
            entry["groups"] = [k[3:] for k in groups]
            arrays["weights"] = put("weights", [attrs[k][v] for v in range(len(mesh.verts)) for k in groups],
                                    (len(mesh.verts), len(groups)))
        entry["arrays"] = arrays
        objects.append(entry)
        for mid in slots:
            if mid in materials:
                used[mid] = materials[mid]
    textures = scene.folder / "textures"
    return {"unit": 0.01, "folder": str(folder), "objects": objects, "rigs": rig_list,
            "materials": [{"forge": m, "textures": {slot: str(textures / name) for slot, name in (m.get("textures") or {}).items()}}
                          for m in used.values()]}


def read(folder, out):
    """Engine dump -> {objects: [{id, name, mesh (None when unchanged), skin, auto_weights, matrix}], rigs, materials}."""
    folder = Path(folder)
    objects = []
    for o in out.get("objects") or []:
        if o.get("unchanged"):
            objects.append({"id": o["id"], "name": o["name"], "mesh": None})
            continue
        a = {k: read_npy(folder / name)[0] for k, name in o["arrays"].items()}
        mesh = Mesh()
        mesh.verts = _rows(a["verts"], 3)
        faces, at = [], 0
        for size in a["sizes"]:
            faces.append(list(a["loops"][at:at + size]))
            at += size
        mesh.faces = faces
        mats = o["mats"]
        mesh.face_mat = [mats[i] if i < len(mats) else mats[0] for i in a["face_mat"]]
        uv = _rows(a["uv"], 2) if "uv" in a else [[0.0, 0.0] for _ in range(at)]
        at, mesh.uvs = 0, []
        for face in faces:
            mesh.uvs.append(uv[at:at + len(face)])
            at += len(face)
        mesh.smooth = bool(o.get("smooth"))
        count = len(mesh.verts)
        if "normals" in a:
            mesh.attrs["nrm:source"] = _rows(a["normals"], 3)
        for k, name in enumerate(o.get("colors") or []):
            mesh.attrs["col:" + name] = _rows(a["colors"][k * count * 4:(k + 1) * count * 4], 4)
        for k, name in enumerate(o.get("shapes") or []):
            mesh.attrs["sk:" + name] = _rows(a["shapes"][k * count * 3:(k + 1) * count * 3], 3)
        groups = o.get("groups") or []
        if groups and not o.get("auto_weights"):
            for g, name in enumerate(groups):
                mesh.attrs["wt:" + name] = [a["weights"][v * len(groups) + g] for v in range(count)]
        objects.append({"id": o["id"], "name": o["name"], "mesh": mesh, "skin": o.get("skin"),
                        "auto_weights": bool(o.get("auto_weights")), "matrix": o["matrix"]})
    return {"objects": objects, "rigs": out.get("rigs") or [], "materials": out.get("materials") or []}


def apply(scene, data):
    """Write read() results into the scene: changed meshes, new objects, rigs, materials. Returns changed ids."""
    from ..scene import _nid
    changed = []
    mats = scene.materials()
    for m in data["materials"]:
        if m["id"] not in mats:
            scene.data["materials"].append(m)
        elif mats[m["id"]] != m:
            mats[m["id"]].clear()
            mats[m["id"]].update(m)
    for o in data["objects"]:
        mesh = o["mesh"]
        if mesh is None:
            continue
        node = scene.node(o["id"])
        if node is None:
            t = decompose(o["matrix"])
            node = {"id": _nid("o"), "type": "object", "name": o["name"], "parent": None, "layer": "default",
                    "visible": True, "locked": False, "transform": t, "prototype": scene.adopt_mesh(mesh),
                    "modifiers": [], "material": mesh.face_mat[0] if mesh.face_mat else scene.data["materials"][0]["id"]}
            if o.get("skin"):
                node["skin"] = o["skin"]
            scene.data["nodes"].append(node)
        else:
            node["modifiers"] = []  # local_mesh baked them into what the engine saw
            scene.write_mesh(node, mesh)
            skin = o.get("skin")
            if skin and not o.get("auto_weights") and not skin.get("paint") and any(k.startswith("wt:") for k in mesh.attrs):
                skin = {"rig": skin.get("rig"), "paint": True}
            if skin:
                node["skin"] = skin
            else:
                node.pop("skin", None)
        changed.append(node["id"])
    existing = {r["id"]: r for r in scene.data.setdefault("rigs", [])}
    for r in data["rigs"]:
        old = existing.get(r.get("id"))
        if old is None:
            r["id"] = r.get("id") or _nid("r")
            scene.data["rigs"].append(r)
            changed.append(r["id"])
        elif old != r:
            old.clear()
            old.update(r)
            changed.append(r["id"])
    if changed:
        scene.touch_geom()
    return changed


def run(scene, op, ids, params=None, timeout=600.0):
    """Send the objects under ids to engine op `op`; returns (op result without its scene, read() data or None)."""
    folder = Path(tempfile.mkdtemp(prefix="hq-", dir=client.scratch()))
    try:
        result = client.call(op, {**(params or {}), "scene": pack(scene, ids, folder)}, timeout)
        dumped = result.pop("scene", None) if isinstance(result, dict) else None
        return result, (read(folder, dumped) if dumped else None)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
