"""GLB writer. UEFN import of a metres Y-up glTF comes back as centimetres Z-up.

Forge space is right-handed, Z up, centimetres: what the dock shows, and the same
axes Blender uses. glTF is right-handed, Y up, metres, so Forge (x, y, z) is written
as (x, z, -y) / 100: a quarter turn about X, no mirror, so winding, normals, and
rotations carry over as they are. This is what Blender's own glTF export writes.

UEFN's importer then mirrors glTF (x, y, z) m into UE (x, z, y) cm (spike:
ExampleProject1), which lands Forge (x, y, z) at UE (x, -y, z): the shape the dock
shows, in UE's left-handed space. A front at -Y in the dock faces +Y in UEFN, and a
biped stands like the UE mannequin, facing +Y with its left side on +X.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from .kernel.mesh import Mesh


def _to_gltf(p: list[float]) -> tuple[float, float, float]:
    return (p[0] / 100.0, p[2] / 100.0, -p[1] / 100.0)


def _quat_to_gltf(q) -> tuple[float, float, float, float]:
    """Forge (w, x, y, z) -> glTF [x, y, z, w], turned the same quarter turn as positions."""
    return (q[1], q[3], -q[2], q[0])


def _pad(data: bytes, n: int, ch: bytes) -> bytes:
    return data + ch * ((-len(data)) % n)


def _animations(skin: dict, add_blob, accs: list, names: list[str]) -> list[dict]:
    """Bake every clip frame by frame. UEFN resamples anyway, so LINEAR keys lose nothing."""
    from .rig import local_pose

    bones = skin["bones"]
    heads = {b["name"]: b["head"] for b in bones}
    out = []
    for clip in skin.get("clips") or []:
        fps = max(1, int(clip.get("fps") or 30))
        frames = max(1, int(clip.get("frames") or 1))
        times = [f / fps for f in range(frames + 1)]
        ti = add_blob(struct.pack(f"<{len(times)}f", *times))
        accs.append({"bufferView": ti, "componentType": 5126, "count": len(times), "type": "SCALAR",
                     "min": [times[0]], "max": [times[-1]]})
        t_acc = len(accs) - 1
        samplers, channels = [], []
        tracks = clip.get("tracks") or {}
        for j, bone in enumerate(bones):
            track = tracks.get(bone["name"])
            if not track:
                continue
            ph = heads.get(bone.get("parent"), [0.0, 0.0, 0.0])
            poses = [local_pose(clip, bone["name"], f) for f in range(frames + 1)]
            wanted = [("rotation", "VEC4", [_quat_to_gltf(q) for _, q, _ in poses])]
            if track.get("loc"):
                wanted.append(("translation", "VEC3", [_to_gltf([bone["head"][i] - ph[i] + loc[i] for i in range(3)]) for loc, _, _ in poses]))
            if track.get("scale"):
                wanted.append(("scale", "VEC3", [(s[0], s[2], s[1]) for _, _, s in poses]))
            for path, kind, values in wanted:
                flat = [c for v in values for c in v]
                vi = add_blob(struct.pack(f"<{len(flat)}f", *flat))
                accs.append({"bufferView": vi, "componentType": 5126, "count": len(values), "type": kind})
                samplers.append({"input": t_acc, "output": len(accs) - 1, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": 1 + j, "path": path}})
        if names and clip.get("morphs"):
            from .rig import morph_weights

            flat = [w for f in range(frames + 1) for w in morph_weights(clip, names, f)]
            vi = add_blob(struct.pack(f"<{len(flat)}f", *flat))
            accs.append({"bufferView": vi, "componentType": 5126, "count": len(flat), "type": "SCALAR"})
            samplers.append({"input": t_acc, "output": len(accs) - 1, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": 0, "path": "weights"}})
        if channels:
            out.append({"name": str(clip.get("name") or "Clip"), "samplers": samplers, "channels": channels})
    return out


def write_glb(mesh: Mesh, path: Path, materials: dict[str, dict] | None = None, skin: dict | None = None) -> Path:
    """One mesh, one primitive per material, names preserved as slot names.

    skin (from rig.skinned_part) adds a joint hierarchy, JOINTS_0/WEIGHTS_0 per
    vertex, inverse bind matrices, and one baked animation per clip. Joint nodes
    are 1..N in skin["bones"] order; rest frames only translate.
    """
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
    # Shape keys become morph targets on skinned meshes only; UEFN static meshes have no morphs.
    morph_names = sorted(n[3:] for n in mesh.attrs if n.startswith("sk:")) if skin else []
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
        corners = []
        for a, b, c, fi in group:
            tri_uv = mesh.tri_uv(fi, (a, b, c))
            for k, vi in enumerate((a, b, c)):
                positions.append(_to_gltf(mesh.verts[vi]))
                corners.append(vi)
                n = mesh.face_normal(fi)
                normals.append(_to_gltf([n[0] * 100, n[1] * 100, n[2] * 100]))
                uvs.append((tri_uv[2 * k], tri_uv[2 * k + 1]))
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
        attributes = {"POSITION": base, "NORMAL": base + 1, "TEXCOORD_0": base + 2}
        if skin:
            wide = len(skin["bones"]) > 255
            joint_fmt = "<4H" if wide else "<4B"
            ij = add_blob(b"".join(struct.pack(joint_fmt, *skin["joints"][vi]) for vi in corners), 34962)
            iw = add_blob(b"".join(struct.pack("<4f", *skin["weights"][vi]) for vi in corners), 34962)
            accs.append({"bufferView": ij, "componentType": 5123 if wide else 5121, "count": len(corners), "type": "VEC4"})
            accs.append({"bufferView": iw, "componentType": 5126, "count": len(corners), "type": "VEC4"})
            attributes.update(JOINTS_0=base + 4, WEIGHTS_0=base + 5)
        blend = mesh.attrs.get("col:blend")
        if blend:  # blend-layer masks: UEFN reads them as vertex colour
            ic = add_blob(b"".join(struct.pack("<4f", *blend[vi]) for vi in corners), 34962)
            accs.append({"bufferView": ic, "componentType": 5126, "count": len(corners), "type": "VEC4"})
            attributes["COLOR_0"] = len(accs) - 1
        primitive = {
            "attributes": attributes,
            "indices": base + 3,
            "material": mat_index[mat_id],
        }
        if morph_names:
            targets = []
            for name in morph_names:
                deltas = mesh.attrs["sk:" + name]
                moved = [_to_gltf(deltas[vi]) for vi in corners]
                it = add_blob(b"".join(struct.pack("<fff", *d) for d in moved), 34962)
                accs.append({"bufferView": it, "componentType": 5126, "count": len(moved), "type": "VEC3",
                             "min": [min(d[k] for d in moved) for k in range(3)], "max": [max(d[k] for d in moved) for k in range(3)]})
                targets.append({"POSITION": len(accs) - 1})
            primitive["targets"] = targets
        primitives.append(primitive)

    nodes: list[dict] = [{"mesh": 0, "name": path.stem}]
    roots = [0]
    skins: list[dict] = []
    animations: list[dict] = []
    if skin:
        bones = skin["bones"]
        index = {b["name"]: i for i, b in enumerate(bones)}
        heads = {b["name"]: b["head"] for b in bones}
        ibm = []
        for bone in bones:
            parent = bone.get("parent") if bone.get("parent") in index else None
            ph = heads[parent] if parent else [0.0, 0.0, 0.0]
            node = {"name": bone["name"], "translation": list(_to_gltf([bone["head"][i] - ph[i] for i in range(3)]))}
            kids = [1 + i for i, b in enumerate(bones) if b.get("parent") == bone["name"]]
            if kids:
                node["children"] = kids
            nodes.append(node)
            if parent is None:
                roots.append(len(nodes) - 1)
            hx, hy, hz = _to_gltf(bone["head"])
            ibm += [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -hx, -hy, -hz, 1]  # column-major inverse(T(head))
        ib = add_blob(struct.pack(f"<{len(ibm)}f", *ibm))
        accs.append({"bufferView": ib, "componentType": 5126, "count": len(bones), "type": "MAT4"})
        skins.append({"joints": [1 + i for i in range(len(bones))], "inverseBindMatrices": len(accs) - 1, "skeleton": roots[1]})
        nodes[0]["skin"] = 0
        animations = _animations(skin, add_blob, accs, morph_names)

    bin_data = b"".join(blobs)
    mesh_entry: dict = {"name": path.stem, "primitives": primitives}
    if morph_names:
        mesh_entry["weights"] = [0.0] * len(morph_names)
        mesh_entry["extras"] = {"targetNames": morph_names}  # importers name morph targets from this
    gltf = {
        "asset": {"version": "2.0", "generator": "ducky-forge"},
        "scene": 0,
        "scenes": [{"nodes": roots}],
        "nodes": nodes,
        "meshes": [mesh_entry],
        "materials": mat_list,
        "buffers": [{"byteLength": len(bin_data)}],
        "bufferViews": views,
        "accessors": accs,
    }
    if skins:
        gltf["skins"] = skins
    if animations:
        gltf["animations"] = animations
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode(), 4, b" ")
    bin_data = _pad(bin_data, 4, b"\x00")
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bin_data), 0x004E4942) + bin_data
    blob = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return path
