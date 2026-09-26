"""GLB writer. UEFN import of a metres Y-up glTF comes back as centimetres Z-up.

Spike (ExampleProject1): glTF (x, y, z) metres -> UE (x, z, y) cm.
So UE (x, y, z) cm -> glTF (x/100, z/100, y/100). The Y/Z swap flips winding,
so indices are reversed.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from .kernel.mesh import Mesh


def _ue_to_gltf(p: list[float]) -> tuple[float, float, float]:
    return (p[0] / 100.0, p[2] / 100.0, p[1] / 100.0)


def _pad(data: bytes, n: int, ch: bytes) -> bytes:
    return data + ch * ((-len(data)) % n)


def write_glb(mesh: Mesh, path: Path, materials: dict[str, dict] | None = None) -> Path:
    """One mesh, one primitive per material, names preserved as slot names."""
    materials = materials or {}
    tris = mesh.triangles()
    by_mat: dict[str, list[tuple[int, int, int, int]]] = {}
    for tri in tris:
        mat = mesh.face_mat[tri[3]] if tri[3] < len(mesh.face_mat) else "default"
        by_mat.setdefault(mat, []).append(tri)

    blobs: list[bytes] = []
    views: list[dict] = []
    accs: list[dict] = []

    def add_blob(raw: bytes, target: int | None = None) -> int:
        off = sum(len(b) for b in blobs)
        padn = (-off) % 4
        if padn:
            blobs.append(b"\x00" * padn)
            off += padn
        blobs.append(raw)
        view: dict = {"buffer": 0, "byteOffset": off, "byteLength": len(raw)}
        if target:
            view["target"] = target
        views.append(view)
        return len(views) - 1

    primitives = []
    mat_list = []
    mat_index: dict[str, int] = {}
    for mat_id, group in by_mat.items():
        if mat_id not in mat_index:
            mat_index[mat_id] = len(mat_list)
            src = materials.get(mat_id) or {}
            color = list(src.get("base_color") or [0.8, 0.8, 0.8, 1])
            while len(color) < 4:
                color.append(1.0)
            name = str(src.get("name") or mat_id)
            mat_list.append({
                "name": name,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [float(color[0]), float(color[1]), float(color[2]), float(color[3])],
                    "metallicFactor": float(src.get("metallic") or 0),
                    "roughnessFactor": float(src.get("roughness") if src.get("roughness") is not None else 0.5),
                },
                "doubleSided": True,
            })
        positions = []
        normals = []
        uvs = []
        indices = []
        for a, b, c, fi in group:
            face = mesh.faces[fi]
            uv_face = mesh.uvs[fi] if fi < len(mesh.uvs) else []
            # reversed winding: c, b, a in UE order becomes the glTF triangle
            for vi in (c, b, a):
                positions.append(_ue_to_gltf(mesh.verts[vi]))
                n = mesh.face_normal(fi)
                normals.append(_ue_to_gltf([n[0] * 100, n[1] * 100, n[2] * 100]))
                try:
                    corner = face.index(vi)
                    uv = uv_face[corner] if corner < len(uv_face) else [0.0, 0.0]
                except ValueError:
                    uv = [0.0, 0.0]
                uvs.append((float(uv[0]), float(uv[1])))
                indices.append(len(positions) - 1)
        raw_p = b"".join(struct.pack("<fff", *p) for p in positions)
        raw_n = b"".join(struct.pack("<fff", *p) for p in normals)
        raw_u = b"".join(struct.pack("<ff", *p) for p in uvs)
        raw_i = b"".join(struct.pack("<I", i) for i in indices)
        xs, ys, zs = zip(*positions)
        ip = add_blob(raw_p, 34962)
        inn = add_blob(raw_n, 34962)
        iu = add_blob(raw_u, 34962)
        ii = add_blob(raw_i, 34963)
        base = len(accs)
        accs.append({"bufferView": ip, "componentType": 5126, "count": len(positions), "type": "VEC3",
                     "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]})
        accs.append({"bufferView": inn, "componentType": 5126, "count": len(normals), "type": "VEC3"})
        accs.append({"bufferView": iu, "componentType": 5126, "count": len(uvs), "type": "VEC2"})
        accs.append({"bufferView": ii, "componentType": 5125, "count": len(indices), "type": "SCALAR"})
        primitives.append({
            "attributes": {"POSITION": base, "NORMAL": base + 1, "TEXCOORD_0": base + 2},
            "indices": base + 3,
            "material": mat_index[mat_id],
        })

    bin_data = b"".join(blobs)
    gltf = {
        "asset": {"version": "2.0", "generator": "ducky-forge"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": path.stem}],
        "meshes": [{"name": path.stem, "primitives": primitives}],
        "materials": mat_list,
        "buffers": [{"byteLength": len(bin_data)}],
        "bufferViews": views,
        "accessors": accs,
    }
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode(), 4, b" ")
    bin_data = _pad(bin_data, 4, b"\x00")
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bin_data), 0x004E4942) + bin_data
    blob = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return path
