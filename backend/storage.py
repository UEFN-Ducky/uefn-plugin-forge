"""Internal mesh blobs shared by scene metadata and undo snapshots.
Portable .forge files still contain complete, materialized mesh data.
"""
import hashlib
import json
from pathlib import Path

_refs = {}

def _folder(folder):
    return str(Path(folder).resolve())

def pack(data, folder):
    folder = Path(folder)
    cache = _refs.setdefault(_folder(folder), {})
    def mesh_ref(mesh):
        hit = cache.get(id(mesh))
        if hit is not None and hit[0] is mesh:
            return {"_forge_mesh_blob": hit[1]}
        encoded = json.dumps(mesh, separators=(",", ":")).encode()
        key = hashlib.sha256(encoded).hexdigest()
        dest = folder / "mesh-blobs" / (key + ".json")
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(encoded)
            tmp.replace(dest)
        cache[id(mesh)] = (mesh, key)
        return {"_forge_mesh_blob": key}
    out = dict(data)
    for collection in ("prototypes", "nodes"):
        out[collection] = [dict(item, mesh=mesh_ref(item["mesh"])) if item.get("mesh") else item
                           for item in data.get(collection, [])]
    return out

def unpack(data, folder):
    folder = Path(folder)
    cache = _refs.setdefault(_folder(folder), {})
    by_key = {entry[1]: entry[0] for entry in cache.values()}
    for collection in ("prototypes", "nodes"):
        for item in data.get(collection, []):
            mesh = item.get("mesh")
            if not isinstance(mesh, dict) or "_forge_mesh_blob" not in mesh:
                continue
            key = str(mesh["_forge_mesh_blob"])
            if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("Invalid mesh blob reference")
            raw = by_key.get(key)
            if raw is None:
                raw = json.loads((folder / "mesh-blobs" / (key + ".json")).read_text(encoding="utf-8"))
                by_key[key] = raw
            item["mesh"] = raw
            cache[id(raw)] = (raw, key)
    return data

def retain(data, folder):
    alive = {id(item["mesh"]) for collection in ("prototypes", "nodes")
             for item in data.get(collection, []) if item.get("mesh")}
    cache = _refs.get(_folder(folder), {})
    for key in list(cache):
        if key not in alive:
            del cache[key]
