"""Internal mesh blobs shared by scene metadata and undo snapshots.
Portable .forge files still contain complete, materialized mesh data.

Blobs are binary `.fmesh` (header JSON with counts + zlib'd float64/uint32 arrays (float64 keeps undo/redo byte-exact)); old `.json` blobs still load
and are replaced by `.fmesh` on the next save. Meshes the binary layout can't hold exactly fall back to `.json`.
"""
import hashlib
import json
import struct
import zlib
from pathlib import Path

try:
    import numpy as np
except ImportError:  # ponytail: pure-python path is ~10x slower to load; numpy ships with the engine
    np = None
from array import array

_refs = {}
_MAGIC = b"FMSH1"
_MESH_KEYS = {"verts", "faces", "face_mat", "uvs", "smooth", "attrs"}

def _folder(folder):
    return str(Path(folder).resolve())

def _binary_ok(mesh):
    """True when the mesh is rectangular enough for the flat layout."""
    if not set(mesh) <= _MESH_KEYS:
        return False
    verts, faces = mesh.get("verts") or [], mesh.get("faces") or []
    uvs, mats = mesh.get("uvs") or [], mesh.get("face_mat") or []
    if any(len(v) != 3 for v in verts) or len(uvs) != len(faces) or len(mats) != len(faces):
        return False
    if any(len(u) != len(f) or any(len(c) != 2 for c in u) for f, u in zip(faces, uvs)):
        return False
    if not all(isinstance(m, str) for m in mats):
        return False
    for vals in (mesh.get("attrs") or {}).values():
        if len(vals) != len(verts):
            return False
        widths = {len(v) if isinstance(v, list) else 0 for v in vals}
        if len(widths) > 1:
            return False
    return True

def _f32(rows):
    return array("d", [c for r in rows for c in r])

def encode(mesh):
    """Mesh dict -> .fmesh bytes, or None when the mesh needs the JSON fallback."""
    if not _binary_ok(mesh):
        return None
    verts, faces = mesh.get("verts") or [], mesh.get("faces") or []
    palette = sorted(set(mesh.get("face_mat") or []))
    index = {m: i for i, m in enumerate(palette)}
    attrs = []
    parts = [_f32(verts), array("I", [i for f in faces for i in f]),
             array("I", [len(f) for f in faces]), array("d", [c for u in mesh.get("uvs") or [] for p in u for c in p]),
             array("I", [index[m] for m in mesh.get("face_mat") or []])]
    for name, vals in (mesh.get("attrs") or {}).items():
        width = len(vals[0]) if vals and isinstance(vals[0], list) else 0
        attrs.append([name, width])
        parts.append(_f32(vals) if width else array("d", vals))
    header = json.dumps({"v": len(verts), "f": len(faces), "c": sum(len(f) for f in faces), "smooth": bool(mesh.get("smooth")),
                         "mats": palette, "attrs": attrs, "sizes": [len(p) for p in parts]}, separators=(",", ":")).encode()
    body = zlib.compress(b"".join(p.tobytes() for p in parts), 1)
    return _MAGIC + struct.pack("<I", len(header)) + header + body

def decode(blob):
    if blob[:5] != _MAGIC:
        raise ValueError("Not an .fmesh blob")
    (hlen,) = struct.unpack_from("<I", blob, 5)
    head = json.loads(blob[9:9 + hlen])
    raw = zlib.decompress(blob[9 + hlen:])
    off = 0
    def take(n, code):
        nonlocal off
        a = array(code)
        a.frombytes(raw[off:off + n * a.itemsize])
        off += n * a.itemsize
        return a
    nv, nf, nc = head["v"], head["f"], head["c"]
    verts, idx, lens, uv, mat = take(nv * 3, "d"), take(nc, "I"), take(nf, "I"), take(nc * 2, "d"), take(nf, "I")
    mats = head["mats"]
    if np is not None:
        v_list = np.frombuffer(verts, np.float64).reshape(-1, 3).tolist()
        lens_np = np.frombuffer(lens, np.uint32)
        idx_np = np.frombuffer(idx, np.uint32).astype(np.int64)
        uv_np = np.frombuffer(uv, np.float64).reshape(-1, 2)
        # uniform tris/quads: one reshape instead of a slice per face
        if nf and (lens_np == lens_np[0]).all():
            k = int(lens_np[0])
            faces = idx_np.reshape(-1, k).tolist()
            uvs = uv_np.reshape(-1, k, 2).tolist()
        else:
            uv_all, idx_all, faces, uvs, p = uv_np.tolist(), idx_np.tolist(), [], [], 0
            for n in lens_np.tolist():
                faces.append(idx_all[p:p + n]); uvs.append(uv_all[p:p + n]); p += n
    else:
        v_list = [[float(verts[i]), float(verts[i + 1]), float(verts[i + 2])] for i in range(0, nv * 3, 3)]
        uv_all = [[float(uv[i]), float(uv[i + 1])] for i in range(0, nc * 2, 2)]
        faces, uvs, p = [], [], 0
        for n in lens:
            faces.append(list(idx[p:p + n])); uvs.append(uv_all[p:p + n]); p += n
    out = {"verts": v_list, "faces": faces, "face_mat": [mats[i] for i in mat], "uvs": uvs, "smooth": head["smooth"]}
    if head["attrs"]:
        out["attrs"] = {}
        for (name, width), size in zip(head["attrs"], head["sizes"][5:]):
            a = take(size, "d")
            if np is not None:
                arr = np.frombuffer(a, np.float64)
                out["attrs"][name] = arr.reshape(-1, width).tolist() if width else arr.tolist()
            else:
                out["attrs"][name] = ([list(map(float, a[i:i + width])) for i in range(0, size, width)] if width
                                      else [float(x) for x in a])
    return out

def _write(dest, data):
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)

def load_blob(key, folder):
    """Read one mesh blob by key: .fmesh first, then the legacy .json."""
    base = Path(folder) / "mesh-blobs" / key
    fm = base.with_suffix(".fmesh")
    if fm.is_file():
        return decode(fm.read_bytes())
    return json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))

class LazyMesh(dict):
    """Mesh placeholder from unpack(lazy=True): reads its blob on first dict access. json.dumps needs dict(mesh) first."""
    def __init__(self, key, folder):
        super().__init__()
        self.key, self.folder, self.loaded = key, folder, False
    def _load(self):
        if not self.loaded:
            self.loaded = True
            super().update(load_blob(self.key, self.folder))
        return self
    def __getitem__(self, k): return dict.__getitem__(self._load(), k)
    def get(self, k, d=None): return dict.get(self._load(), k, d)
    def __contains__(self, k): return dict.__contains__(self._load(), k)
    def __iter__(self): return dict.__iter__(self._load())
    def __len__(self): return dict.__len__(self._load())
    def __bool__(self): return True
    def keys(self): return dict.keys(self._load())
    def values(self): return dict.values(self._load())
    def items(self): return dict.items(self._load())
    def __setitem__(self, k, v): dict.__setitem__(self._load(), k, v)

def pack(data, folder):
    folder = Path(folder)
    cache = _refs.setdefault(_folder(folder), {})
    def mesh_ref(mesh):
        if isinstance(mesh, LazyMesh) and not mesh.loaded and _folder(mesh.folder) == _folder(folder):
            return {"_forge_mesh_blob": mesh.key}  # untouched since load: blob is already on disk
        hit = cache.get(id(mesh))
        if hit is not None and hit[0] is mesh:
            return {"_forge_mesh_blob": hit[1]}
        plain = dict(mesh)
        encoded = encode(plain)
        if encoded is not None:
            key = hashlib.sha256(encoded).hexdigest()
            _write(folder / "mesh-blobs" / (key + ".fmesh"), encoded)
        else:
            encoded = json.dumps(plain, separators=(",", ":")).encode()
            key = hashlib.sha256(encoded).hexdigest()
            _write(folder / "mesh-blobs" / (key + ".json"), encoded)
        cache[id(mesh)] = (mesh, key)
        return {"_forge_mesh_blob": key}
    out = dict(data)
    for collection in ("prototypes", "nodes"):
        out[collection] = [dict(item, mesh=mesh_ref(item["mesh"])) if item.get("mesh") else item
                           for item in data.get(collection, [])]
    return out

def unpack(data, folder, lazy=False):
    """Replace blob refs with meshes. lazy=True gives LazyMesh placeholders so the scene header loads without mesh I/O."""
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
                raw = LazyMesh(key, folder) if lazy else load_blob(key, folder)
                by_key[key] = raw
            item["mesh"] = raw
            if (folder / "mesh-blobs" / (key + ".fmesh")).is_file():  # legacy .json stays uncached so the next pack migrates it
                cache[id(raw)] = (raw, key)
    return data

def retain(data, folder):
    alive = {id(item["mesh"]) for collection in ("prototypes", "nodes")
             for item in data.get(collection, []) if item.get("mesh")}
    cache = _refs.get(_folder(folder), {})
    for key in list(cache):
        if key not in alive:
            del cache[key]
