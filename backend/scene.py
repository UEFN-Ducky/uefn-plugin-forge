"""The scene both the panel and the agent edit. Stored under AppData, never in the island."""

from __future__ import annotations

import array
import base64
import difflib
import json
import math
import copy
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from . import history, projects, storage
from .kernel.csg import boolean as csg_boolean
from .kernel.mesh import Mesh, build_primitive
from .check import check_mesh, fix_mesh
from .rig import find_clip, find_rig, issues as rig_issues, merge_rigs, op_anim, op_preview, op_rig, posed_meshes
from .kernel.ops import (
    apply_modifiers,
    bevel_edges,
    bridge_loops,
    delete_faces,
    delete_verts,
    dissolve_edges,
    extrude_faces,
    fill_boundary,
    flip_faces,
    inset_faces,
    loop_cut,
    merge_by_distance,
    merge_center,
    mirror_mesh,
    move_elements,
    recalculate_normals,
    separate_by_material,
    set_origin,
    set_smooth,
    subdivide,
)
from .uv import project
from .shots import op_camera, op_cameras, op_shot

_LOCK = threading.Lock()
_READ = {"look", "cameras", "stats", "scene_get", "scene_list", "scene_payload", "scene_geom_part", "scene_save", "scene_open", "scene_files", "scene_import_blend", "rig_preview", "mesh_data"}
# One dock message. Bigger buffers stay on the scene and load in pieces; every triangle is still sent.
_GEOM_PART = 1024 * 1024
_LOOK = {"check", "render"}
_OPEN: dict[str, "Scene"] = {}


def scenes_root() -> Path:
    """Scenes folder of the active project (the flat FORGE_SCENES_DIR folder when that override is set)."""
    return projects.scenes_dir()


def _folder(name: str) -> Path:
    """'Project/Scene' or a plain name in the active project."""
    project, _, short = (name or "").rpartition("/")
    return (projects.scenes_dir(projects.require(project)) if project else scenes_root()) / _safe(short)


def _safe(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (name or "").strip())
    if not cleaned:
        raise ValueError("scene name required")
    return cleaned[:64]


def _nid(prefix: str) -> str:
    return prefix + secrets.token_hex(3)


def _blank(name: str) -> dict[str, Any]:
    mat = _nid("m")
    return {
        "name": name,
        "rev": 1,
        "mode": "object",
        "select_mode": "face",
        "selection": {"object": None, "verts": [], "edges": [], "faces": []},
        "layers": [
            {"id": "default", "name": "Default", "visible": True, "locked": False, "color": "#8aa"},
            {"id": "collision", "name": "Collision", "visible": True, "locked": False, "color": "#e85"},
        ],
        "materials": [_mat(mat, "Default")],
        "prototypes": [],
        "nodes": [],
        "rigs": [],
        "geom": 1,
        "activity": [],
    }


def _proto_key(kind: str, params: dict) -> str:
    return json.dumps([kind, params], sort_keys=True, separators=(",", ":"))


def _placements(node: dict) -> list[dict]:
    t = node.get("transform") or {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]}
    out = [t]
    for mod in node.get("modifiers") or []:
        if str(mod.get("type") or "") != "array":
            continue
        count = max(1, int(mod.get("count") or 2))
        off = list(mod.get("offset") or [100, 0, 0])
        while len(off) < 3:
            off.append(0.0)
        nxt = []
        for base in out:
            pos, rot, scale = base["pos"], base["rot"], base["scale"]
            for i in range(count):
                nxt.append({
                    "pos": [pos[0] + off[0] * i, pos[1] + off[1] * i, pos[2] + off[2] * i],
                    "rot": rot,
                    "scale": scale,
                })
        out = nxt
    return out


def _b64(values: array.array) -> str:
    return base64.b64encode(values.tobytes()).decode("ascii")


# Oct 28 2026: a 168k-tri lion (fur shells merged in) froze Ducky when the dock opened it.
# Like Blender's viewport, heavy meshes are shown as a lighter preview; the full mesh stays
# on disk for HQ ops, render and send.
PREVIEW_TRIS = 60000


def _preview(mesh: Mesh, limit: int = PREVIEW_TRIS) -> Mesh:
    """Vertex-clustered copy under `limit` triangles (one pass over the mesh per try)."""
    if mesh.tri_count() <= limit or not mesh.verts:
        return mesh
    lo, hi = mesh.bounds()
    size = max(hi[i] - lo[i] for i in range(3)) or 1.0
    cells = max(8, int((limit ** 0.5) * 1.6))
    has_uv = len(mesh.uvs) == len(mesh.faces)
    out = mesh
    for _ in range(10):
        cell = size / cells
        slot: dict = {}
        verts: list = []
        remap = []
        for v in mesh.verts:
            key = (int((v[0] - lo[0]) / cell), int((v[1] - lo[1]) / cell), int((v[2] - lo[2]) / cell))
            i = slot.get(key)
            if i is None:
                i = slot[key] = len(verts)
                verts.append([float(v[0]), float(v[1]), float(v[2])])
            remap.append(i)
        faces, mats, uvs = [], [], []
        for fi, face in enumerate(mesh.faces):
            ring, corners = [], []
            for ci, vi in enumerate(face):
                r = remap[vi]
                if r not in ring:
                    ring.append(r)
                    if has_uv:
                        corners.append(mesh.uvs[fi][ci])
            if len(ring) < 3:
                continue
            faces.append(ring)
            mats.append(mesh.face_mat[fi] if fi < len(mesh.face_mat) else "default")
            if has_uv:
                uvs.append(corners)
        out = Mesh()
        out.verts, out.faces, out.face_mat, out.uvs, out.smooth = verts, faces, mats, uvs, mesh.smooth
        if out.tri_count() <= limit:
            return out
        cells = max(8, int(cells * 0.7))
    return out


def _indexed(mesh: Mesh, pid: str, uv: bool = False) -> dict[str, Any]:
    """Packed float32 positions and uint32 indices. Smooth meshes get averaged normals in the view.

    uv adds per-corner UVs (6 floats per triangle) so the dock can show textures;
    a col:blend attribute adds per-vertex blend masks (3 floats each).
    """
    pos = array.array("f")
    idx = array.array("I")
    faces = array.array("I")
    corners = array.array("f")
    for v in mesh.verts:
        pos.extend((float(v[0]), float(v[1]), float(v[2])))
    for a, b, c, fi in mesh.triangles():
        idx.extend((int(a), int(b), int(c)))
        faces.append(int(fi))
        if uv:
            corners.extend(mesh.tri_uv(fi, (a, b, c)))
    out = {"id": pid, "pb": _b64(pos), "ib": _b64(idx), "fb": _b64(faces), "tris": len(faces)}
    if mesh.smooth:
        out["smooth"] = True
    if uv:
        out["ub"] = _b64(corners)
    if any(m and m != "default" for m in mesh.face_mat):
        slots = list(dict.fromkeys(mesh.face_mat))
        slot_index = {m: i for i, m in enumerate(slots)}
        groups = []
        for ti, fi in enumerate(faces):
            slot = slot_index.get(mesh.face_mat[fi] if fi < len(mesh.face_mat) else "default", 0)
            if groups and groups[-1][2] == slot:
                groups[-1][1] += 3
            else:
                groups.append([ti * 3, 3, slot])
        out["material_slots"], out["groups"] = slots, groups
    normals = mesh.attrs.get("nrm:source")
    if normals and len(normals) == len(mesh.verts):
        out["nb"] = _b64(array.array("f", (c for n in normals for c in n)))
    colors = mesh.attrs.get("col:color")
    if colors and len(colors) == len(mesh.verts):
        out["vcb"] = _b64(array.array("f", (c for color in colors for c in color[:3])))
    blend = mesh.attrs.get("col:blend")
    if blend and len(blend) == len(mesh.verts):
        out["cb"] = _b64(array.array("f", (c for rgba in blend for c in rgba[:3])))
    return out


def _mat(mid: str, name: str, color: list[float] | None = None) -> dict[str, Any]:
    return {
        "id": mid,
        "name": name,
        "base_color": color or [0.75, 0.75, 0.78, 1],
        "metallic": 0.0,
        "roughness": 0.5,
        "emissive": [0, 0, 0],
        "emissive_strength": 0.0,
        "opacity": 1.0,
        "textures": {},
    }


class Scene:
    def __init__(self, data: dict[str, Any], folder: Path):
        self.data = data
        self.folder = folder
        self._packed = None
        self._packed_key = None
        self._packed_data = None
        self._stamp = None
        self._node_source = None
        self._node_count = -1
        self._node_index = {}
        self._transforms = {}
        self._catalog_cache = {}
        self._geometry_cache = {}
        self._payload_versions = {}
        self._geom_hold = {}
        self._stamp_transforms = True  # transforms.json may exist from an earlier session until the first save

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.folder.name)

    def save(self) -> None:
        if self._stamp is None:
            self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "scene.json.tmp"
        tmp.write_text(json.dumps(storage.pack(self.data, self.folder)), encoding="utf-8")
        tmp.replace(self.folder / "scene.json")
        self._stamp = (self.folder / "scene.json").stat().st_mtime_ns
        if self._transforms or self._stamp_transforms:
            (self.folder / "transforms.json").unlink(missing_ok=True)
        self._transforms = {}
        self._stamp_transforms = False
        storage.retain(self.data, self.folder)

    def save_transform(self, nid):
        """Atomically persist small overrides against the unchanged scene baseline."""
        self._transforms[nid] = copy.deepcopy(self.node(nid)["transform"])
        record = {"base_stamp": self._stamp, "transforms": self._transforms,
                  "header": {k: self.data.get(k) for k in
                             ("rev", "geom", "activity", "selection", "mode", "select_mode")}}
        path = self.folder / "transforms.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)

    def patch_payload_transform(self, nid):
        if self._packed is None or self._packed_data is not self.data:
            return
        node = self.node(nid)
        places = iter(_placements(node))
        rows = []
        for row in self._packed["instances"]:
            if row[0] == nid:
                t = next(places)
                row = row[:3] + t["pos"] + t["rot"] + t["scale"] + row[12:]
            rows.append(row)
        self._packed = dict(self._packed, instances=rows, nodes=[
            dict(n, transform=copy.deepcopy(node["transform"])) if n["id"] == nid else n
            for n in self._packed["nodes"]])
        self._packed_key = self.data.get("geom")
        self._payload_versions[self._packed_key] = dict(self._packed)
        while len(self._payload_versions) > 3:
            self._payload_versions.pop(next(iter(self._payload_versions)))

    def _hist(self) -> Path:
        return self.folder / "history.json"

    def _snapshot(self) -> dict[str, Any]:
        # Authored geometry is immutable: mesh operations obtain their own Mesh copy.
        memo = {id(item["mesh"]): item["mesh"] for collection in ("prototypes", "nodes")
                for item in self.data.get(collection, []) if item.get("mesh")}
        return copy.deepcopy(self.data, memo)

    def _note(self, actor: str, op: str, summary: str, ids: list, kind: str) -> None:
        self.data["rev"] = int(self.data.get("rev") or 0) + 1
        feed = list(self.data.get("activity") or [])
        feed.append({
            "rev": self.data["rev"],
            "actor": actor,
            "op": op,
            "summary": summary,
            "ids": ids,
            "kind": kind,
            "ts": time.time(),
        })
        self.data["activity"] = feed[-40:]

    def commit(self, actor: str, op: str, summary: str, ids: list | None = None, *, kind: str = "edit", snapshot: dict | None = None) -> None:
        if kind == "edit":
            history.push(self._hist(), snapshot if snapshot is not None else self._snapshot())
        self._note(actor, op, summary, list(ids or []), kind)
        self.save()

    def node(self, nid: str | None) -> dict[str, Any] | None:
        if not nid:
            return None
        nodes = self.data["nodes"]
        if self._node_source is not nodes or self._node_count != len(nodes):
            self._node_index = {n["id"]: n for n in nodes}
            self._node_source = nodes
            self._node_count = len(nodes)
        return self._node_index.get(nid)

    def materials(self) -> dict[str, dict]:
        return {m["id"]: m for m in self.data["materials"]}

    def touch_geom(self) -> None:
        self.data["geom"] = int(self.data.get("geom") or 0) + 1

    def proto(self, node: dict | None) -> dict[str, Any] | None:
        if not node:
            return None
        self._ensure_proto(node)
        pid = node.get("prototype")
        for p in self.data.get("prototypes") or []:
            if p.get("id") == pid:
                return p
        return None

    def user_count(self, pid: str | None) -> int:
        if not pid:
            return 0
        return sum(1 for n in self.data["nodes"] if n.get("prototype") == pid)

    def _ensure_proto(self, node: dict) -> None:
        if node.get("type") != "object" or node.get("prototype"):
            return
        self.data.setdefault("prototypes", [])
        if node.get("mesh"):
            pid = _nid("p")
            self.data["prototypes"].append({
                "id": pid, "kind": "mesh", "params": {}, "key": "", "mesh": node.pop("mesh"),
            })
            node["prototype"] = pid
            node.pop("primitive", None)
            return
        prim = node.get("primitive") or {"kind": "box", "params": {}}
        proto = self.share_primitive(str(prim.get("kind") or "box"), prim.get("params") or {})
        node["prototype"] = proto["id"]
        node.pop("primitive", None)
        node.pop("mesh", None)

    def share_primitive(self, kind: str, params: dict | None) -> dict[str, Any]:
        self.data.setdefault("prototypes", [])
        key = _proto_key(kind, params or {})
        for p in self.data["prototypes"]:
            if p.get("key") == key and not p.get("mesh"):
                return p
        proto = {"id": _nid("p"), "kind": kind, "params": dict(params or {}), "key": key, "mesh": None}
        self.data["prototypes"].append(proto)
        return proto

    def adopt_mesh(self, mesh: Mesh) -> str:
        self.data.setdefault("prototypes", [])
        pid = _nid("p")
        self.data["prototypes"].append({
            "id": pid, "kind": "mesh", "params": {}, "key": "", "mesh": mesh.to_dict(),
        })
        return pid

    def write_mesh(self, node: dict, mesh: Mesh) -> None:
        """Copy-on-write. Shared prototypes stay shared until this object is edited."""
        # Geometry edits recompute normals; untouched shared imports keep theirs.
        mesh.attrs.pop("nrm:source", None)
        proto = self.proto(node)
        if proto and self.user_count(proto["id"]) <= 1:
            proto["mesh"] = mesh.to_dict()
            proto["kind"] = "mesh"
            proto["params"] = {}
            proto["key"] = ""
        else:
            node["prototype"] = self.adopt_mesh(mesh)
        node.pop("mesh", None)
        node.pop("primitive", None)
        node.pop("material_override", None)
        self.touch_geom()

    def _gc_protos(self) -> None:
        used = {n.get("prototype") for n in self.data["nodes"]}
        self.data["prototypes"] = [p for p in self.data.get("prototypes") or [] if p.get("id") in used]

    def _mesh_of(self, proto: dict, material: str = "default") -> Mesh:
        if proto.get("mesh"):
            mesh = Mesh.from_dict(proto["mesh"])
        else:
            mesh = build_primitive(str(proto.get("kind") or "box"), proto.get("params") or {}, mat=material or "default")
        return mesh

    def base_mesh(self, node: dict) -> Mesh:
        proto = self.proto(node)
        if proto:
            mesh = self._mesh_of(proto, node.get("material") or "default")
        elif node.get("mesh"):
            mesh = Mesh.from_dict(node["mesh"])
        elif node.get("primitive"):
            prim = node["primitive"]
            mesh = build_primitive(str(prim.get("kind") or "box"), prim.get("params") or {}, mat=node.get("material") or "default")
        else:
            mesh = Mesh()
        mat = node.get("material")
        if mat and node.get("material_override"):
            mesh.face_mat = [mat] * len(mesh.faces)
        elif mat and not (proto and proto.get("mesh")):
            mesh.face_mat = [mat if m in ("", "default") else m for m in mesh.face_mat]
        return mesh

    def shaped_mesh(self, node: dict) -> Mesh:
        """Prototype plus mirror. Array stays instances and is not copied here."""
        mesh = self.base_mesh(node)
        for mod in node.get("modifiers") or []:
            if str(mod.get("type") or "") == "mirror":
                mesh = mirror_mesh(mesh, str(mod.get("axis") or "x"), float(mod.get("weld", 0.05)))
        return mesh

    def display_key(self, node: dict) -> str:
        proto = self.proto(node)
        pid = str((proto or {}).get("id") or node.get("id"))
        mirrors = [m for m in (node.get("modifiers") or []) if str(m.get("type") or "") == "mirror"]
        if not mirrors:
            return pid
        return pid + "|m|" + json.dumps(mirrors, sort_keys=True, separators=(",", ":"))

    def local_mesh(self, node: dict) -> Mesh:
        return apply_modifiers(self.base_mesh(node), node.get("modifiers") or [])

    def world_mesh(self, node: dict) -> Mesh:
        mesh = self.local_mesh(node)
        chain = []
        cur: dict | None = node
        seen: set[str] = set()
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            chain.append(cur)
            cur = self.node(cur.get("parent"))
        for nd in chain:
            t = nd.get("transform") or {}
            mesh.transform(t.get("pos") or [0, 0, 0], t.get("rot") or [0, 0, 0], t.get("scale") or [1, 1, 1])
        return mesh

    def _bake(self, node: dict) -> Mesh:
        """The object's own editable mesh, modifiers applied. Callers write it back when done."""
        mesh = self.local_mesh(node)
        if node.get("modifiers"):
            node["modifiers"] = []
            self.write_mesh(node, mesh)
        return mesh

    def is_locked(self, node: dict) -> bool:
        layers = {l["id"] for l in self.data.get("layers", []) if l.get("locked")}
        seen = set()
        while node and node["id"] not in seen:
            seen.add(node["id"])
            if node.get("locked") or node.get("layer") in layers:
                return True
            node = self.node(node.get("parent"))
        return False

    def last_object(self) -> dict[str, Any] | None:
        """The most recently added object: the target when nothing is selected."""
        return next((n for n in reversed(self.data["nodes"]) if n.get("type") == "object"), None)

    def _active(self) -> dict[str, Any]:
        sel = self.data["selection"].get("object")
        node = self.node(sel) if sel else self.last_object()
        if node is None or node.get("type") != "object":
            raise ValueError("Select an object first")
        if self.is_locked(node):
            raise ValueError(f"{node.get('name')} is locked")
        return node

    def _proto_by_id(self) -> dict[str, dict]:
        return {p["id"]: p for p in self.data.get("prototypes") or []}

    def _catalog(self) -> tuple[dict, dict, dict, dict]:
        for n in self.data["nodes"]:
            if n.get("type") == "object" and not n.get("prototype"):
                self._ensure_proto(n)
        prim_of: dict[str, dict] = {}
        tris_of: dict[str, int] = {}
        bounds_of: dict[str, tuple] = {}
        meshes: dict[str, Mesh] = {}
        for proto in self.data.get("prototypes") or []:
            pid = proto["id"]
            raw = proto.get("mesh")
            signature = (id(raw), proto.get("kind"), json.dumps(proto.get("params") or {}, sort_keys=True))
            entry = self._catalog_cache.get(pid)
            if entry is None or entry[0] != signature:
                if raw:
                    # Read-only view for packing/stats. Modeling still uses base_mesh copies.
                    mesh = Mesh()
                    mesh.verts = raw.get("verts", [])
                    mesh.faces = raw.get("faces", [])
                    mesh.face_mat = raw.get("face_mat", [])
                    mesh.uvs = raw.get("uvs", [])
                    mesh.smooth = bool(raw.get("smooth"))
                    mesh.attrs = raw.get("attrs") or {}
                else:
                    mesh = self._mesh_of(proto)
                entry = (signature, mesh, mesh.bounds(), mesh.tri_count(), raw)
                self._catalog_cache[pid] = entry
            _, mesh, bounds, triangles, _raw = entry
            meshes[pid] = mesh
            tris_of[pid] = triangles
            bounds_of[pid] = bounds
            if not proto.get("mesh"):
                prim_of[pid] = {"kind": proto.get("kind"), "params": proto.get("params") or {}}
        for key in list(self._catalog_cache):
            if key not in meshes:
                del self._catalog_cache[key]
        return prim_of, tris_of, bounds_of, meshes

    def packed_geometry(self, key, mesh, uv=False):
        cached = self._geometry_cache.get(key)
        if cached is None or cached[0] is not mesh or ("ub" in cached[1]) != uv:
            full = mesh.tri_count()
            view = _preview(mesh)
            packed = _indexed(view, key, uv)
            if view is not mesh:
                packed["preview"], packed["full_tris"] = True, full
            packed["revision"] = secrets.token_hex(8)
            self._geometry_cache[key] = (mesh, packed)
            return packed
        return cached[1]

    def _dock_proto(self, packed: dict) -> dict:
        """Copy a packed mesh for the dock. Leave the cache intact.

        ponytail: buffers over one panel message are held here and fetched in
        ``_GEOM_PART`` slices. Ceiling is transfer time, not missing triangles.
        """
        out = dict(packed)
        parts: dict[str, int] = {}
        for key in ("pb", "ib", "fb", "ub", "cb", "nb", "vcb"):
            val = out.get(key)
            if isinstance(val, str) and len(val) > _GEOM_PART:
                self._geom_hold[(str(packed.get("id") or ""), key)] = val
                parts[key] = len(val)
                del out[key]
        if parts:
            out["parts"] = parts
            out["part_size"] = _GEOM_PART
        return out

    def uv_materials(self) -> set[str]:
        """Materials the dock must draw with UVs: a texture or blend layers."""
        return {m["id"] for m in self.data.get("materials") or []
                if (m.get("textures") or {}).get("base_color") or any(m.get("layers") or [])}

    def summary(self) -> dict[str, Any]:
        prim_of, tris_of, bounds_of, _meshes = self._catalog()
        nodes, objects = self._views(prim_of, tris_of, bounds_of)
        return self._header(nodes, objects)

    def _header(self, nodes: list, objects: list) -> dict[str, Any]:
        return {
            "name": self.name,
            "rev": self.data.get("rev"),
            "geom": self.data.get("geom") or 0,
            "mode": self.data.get("mode"),
            "select_mode": self.data.get("select_mode"),
            "selection": self.data.get("selection"),
            "layers": self.data.get("layers"),
            "materials": self.data.get("materials"),
            "rigs": self.data.get("rigs") or [],
            "nodes": nodes,
            "objects": objects,
            "activity": self.data.get("activity") or [],
        }

    def _views(self, prim_of: dict, tris_of: dict, bounds_of: dict) -> tuple[list, list]:
        nodes: list[dict] = []
        objects: list[dict] = []
        add_n = nodes.append
        add_o = objects.append
        for n in self.data["nodes"]:
            if n.get("type") != "object":
                add_n({
                    "id": n.get("id"), "type": n.get("type"), "name": n.get("name"),
                    "parent": n.get("parent"), "layer": n.get("layer"),
                    "visible": n.get("visible", True), "locked": bool(n.get("locked")),
                    "transform": n.get("transform"),
                })
                continue
            pid = n.get("prototype") or ""
            mods = n.get("modifiers") or ()
            tris = tris_of.get(pid, 0)
            if mods:
                copies = 1
                for mod in mods:
                    if str(mod.get("type") or "") == "array":
                        copies *= max(1, int(mod.get("count") or 2))
                tris *= copies
            lo, hi = bounds_of.get(pid, ([0, 0, 0], [0, 0, 0]))
            add_o({
                "id": n["id"], "name": n.get("name"), "tris": tris,
                "bounds": [lo, hi], "layer": n.get("layer"), "parent": n.get("parent"),
                "prototype": pid, "skin": n.get("skin"),
            })
            add_n({
                "id": n["id"], "type": "object", "name": n.get("name"),
                "parent": n.get("parent"), "layer": n.get("layer"),
                "visible": n.get("visible", True), "locked": bool(n.get("locked")),
                "transform": n.get("transform"), "prototype": pid,
                "primitive": prim_of.get(pid), "modifiers": mods or [],
                "material": n.get("material"), "tris": tris, "skin": n.get("skin"),
            })
        return nodes, objects

    def payload(self) -> dict[str, Any]:
        """One indexed mesh per prototype, plus a flat instance transform list."""
        cache_key = self.data.get("geom")
        if self._packed_data is self.data and self._packed_key == cache_key and self._packed is not None:
            info = self._header(self._packed["nodes"], [])
            info.update(self._packed)
            return self._payload_activity(info)
        prim_of, tris_of, _bounds, meshes = self._catalog()
        geos: dict[str, dict[str, Any]] = {}
        instances: list[list] = []
        nodes: list[dict] = []
        add_i = instances.append
        add_n = nodes.append
        # Only meshes some object draws with a textured or blend material carry UVs: they double the
        # payload and make the dock expand every corner, so a big scene with one textured prop stays light.
        uv_mats = self.uv_materials()
        uv_pids = {n.get("prototype") or n["id"] for n in self.data["nodes"]
                   if n.get("type") == "object" and n.get("material") in uv_mats} if uv_mats else set()
        uv_pids.update(pid for pid, mesh in meshes.items() if uv_mats.intersection(mesh.face_mat))
        for n in self.data["nodes"]:
            if n.get("type") != "object":
                add_n({
                    "id": n.get("id"), "type": n.get("type"), "name": n.get("name"),
                    "parent": n.get("parent"), "layer": n.get("layer"),
                    "visible": n.get("visible", True), "locked": bool(n.get("locked")),
                    "transform": n.get("transform"),
                })
                continue
            pid = n.get("prototype") or n["id"]
            mods = n.get("modifiers")
            tris = tris_of.get(pid, 0)
            mirrored = False
            if mods:
                copies = 1
                for mod in mods:
                    kind = str(mod.get("type") or "")
                    if kind == "array":
                        copies *= max(1, int(mod.get("count") or 2))
                    elif kind == "mirror":
                        mirrored = True
                tris *= copies
            view = n.copy()
            view.pop("hq", None)  # HQ op data (LODs, sidecars) is for export, not the dock
            view["primitive"] = prim_of.get(pid)
            view["tris"] = tris
            add_n(view)
            if not n.get("visible", True):
                continue
            uv = pid in uv_pids
            if mirrored:
                key = self.display_key(n)
                if key not in geos:
                    source = self._catalog_cache.get(pid)
                    cached = self._geometry_cache.get(key)
                    if cached is not None and cached[0] is source and ("ub" in cached[1]) == uv:
                        geos[key] = cached[1]
                    else:
                        geos[key] = _indexed(self.shaped_mesh(n), key, uv)
                        geos[key]["revision"] = secrets.token_hex(8)
                        self._geometry_cache[key] = (source, geos[key])
                places = _placements(n)
            elif mods:
                key = pid
                if key not in geos:
                    geos[key] = self.packed_geometry(key, meshes[key] if key in meshes else self.base_mesh(n), uv)
                places = _placements(n)
            else:
                key = pid
                if key not in geos:
                    geos[key] = self.packed_geometry(key, meshes[key] if key in meshes else self.base_mesh(n), uv)
                tr = n["transform"]
                pos, rot, scale = tr["pos"], tr["rot"], tr["scale"]
                add_i([
                    n["id"], key, n.get("parent") or "",
                    pos[0], pos[1], pos[2], rot[0], rot[1], rot[2],
                    scale[0], scale[1], scale[2], n.get("material") or "",
                ])
                continue
            parent = n.get("parent") or ""
            mat = n.get("material") or ""
            nid = n["id"]
            for place in places:
                pos, rot, scale = place["pos"], place["rot"], place["scale"]
                add_i([
                    nid, key, parent,
                    pos[0], pos[1], pos[2], rot[0], rot[1], rot[2],
                    scale[0], scale[1], scale[2], mat,
                ])
        for key in list(self._geometry_cache):
            if key not in geos:
                del self._geometry_cache[key]
        self._geom_hold = {}
        info = self._header(nodes, [])
        info["prototypes"] = [self._dock_proto(g) for g in geos.values()]
        info["instances"] = instances
        self._packed_key = cache_key
        self._packed_data = self.data
        self._packed = {key: info[key] for key in ("nodes", "prototypes", "instances")}
        return self._payload_activity(info)

    def _payload_activity(self, info: dict) -> dict:
        self._payload_versions[info.get("geom", 0)] = dict(info)
        while len(self._payload_versions) > 3:
            self._payload_versions.pop(next(iter(self._payload_versions)))
        last = (self.data.get("activity") or [None])[-1]
        info["flash"] = None
        if last and last.get("kind") == "edit" and last.get("actor") == "agent":
            info["flash"] = {"ids": last.get("ids") or [], "op": last.get("op"), "summary": last.get("summary")}
        return info


_KEYS: dict[str, str] = {}  # folder -> resolved folder; resolve() is a syscall per call on Windows


def _key(folder: Path) -> str:
    raw = str(folder)
    hit = _KEYS.get(raw)
    if hit is None:
        hit = _KEYS[raw] = str(folder.resolve())
    return hit


def forget(name: str) -> None:
    _OPEN.pop(_key(_folder(name)), None)


def load(name: str) -> Scene:
    folder = _folder(name)
    key = _key(folder)
    path = folder / "scene.json"
    hit = _OPEN.get(key)
    if hit is not None and path.is_file() and hit._stamp == path.stat().st_mtime_ns:
        return hit
    if not path.is_file():
        raise ValueError(f"No Forge scene named {name!r}")
    scene = Scene(storage.unpack(json.loads(path.read_text(encoding="utf-8")), folder), folder)
    scene._stamp = path.stat().st_mtime_ns
    changes = folder / "transforms.json"
    if changes.is_file():
        record = json.loads(changes.read_text(encoding="utf-8"))
        if record.get("base_stamp") == scene._stamp:
            scene._transforms = record.get("transforms", {})
            for nid, transform in scene._transforms.items():
                node = scene.node(nid)
                if node is not None:
                    node["transform"] = transform
            scene.data.update(record.get("header", {}))
    _OPEN[key] = scene
    return scene


def create(name: str) -> Scene:
    folder = _folder(name)
    if (folder / "scene.json").is_file():
        raise ValueError(f"Scene {name!r} already exists")
    scene = Scene(_blank(_safe(name.rpartition("/")[2])), folder)
    scene.save()
    return scene


def list_scenes(project: str | None = None) -> list[dict[str, Any]]:
    return projects.list_scenes(project)


def _rename_scene(name: str, p: dict) -> dict[str, Any]:
    """Rename a scene inside its project: the folder moves with its history, library refs follow."""
    sc = load(name)
    new = _safe(str(p.get("new_name") or p.get("name") or "").rpartition("/")[2])
    old = sc.folder
    full = (name.rpartition("/")[0] + "/" + new) if "/" in name else new
    if new == old.name:
        return {"summary": f"Scene is already named {new}", "name": full, "ids": []}
    dest = old.with_name(new)
    if dest.exists() and not os.path.samefile(dest, old):  # samefile: a case-only rename on Windows
        raise ValueError(f"Scene {new!r} already exists in this project. Pick another new_name")
    sc.save()
    _OPEN.pop(_key(old), None)
    old.rename(dest)
    sc.folder = dest
    sc.data["name"] = new
    sc.save()
    _OPEN[_key(dest)] = sc
    projects.relink_scene(old, dest, new)
    return {"summary": f"Renamed {old.name} to {new}", "name": full, "ids": []}


def _no_such(kind: str, name: Any, options) -> ValueError:
    """'No bone 'hand_left'. Did you mean 'hand_l'? Bones: ...' with at most 8 options."""
    options = list(dict.fromkeys(str(o) for o in options if o))
    close = difflib.get_close_matches(str(name), options, n=1, cutoff=0.5)
    hint = f" Did you mean {close[0]!r}?" if close else ""
    listed = ", ".join(options[:8]) + (f" (+{len(options) - 8} more)" if len(options) > 8 else "")
    return ValueError(f"No {kind} {str(name)!r}.{hint} {kind[0].upper() + kind[1:]}s: {listed or 'none'}")


def _hint_error(scene: Scene, exc: Exception) -> Exception:
    """rig.py's 'No bone/clip/rig' errors, upgraded with the closest name."""
    m = re.match(r"No (bone|clip|rig) '([^']*)'", str(exc))
    if not m:
        return exc
    rigs = scene.data.get("rigs") or []
    options = {"rig": [r.get("name") for r in rigs],
               "bone": [b["name"] for r in rigs for b in r.get("bones") or []],
               "clip": [c.get("name") for r in rigs for c in r.get("actions") or []]}[m[1]]
    return _no_such(m[1], m[2], options)


def dispatch(name: str, op: str, params: dict | None = None, actor: str = "agent") -> dict[str, Any]:
    params = dict(params or {})
    op = (op or "").strip()
    with _LOCK:
        if op == "scene_import_preview":
            from .blend_import import import_blend
            return import_blend(str(params.get("path") or ""), preview=True, scale=params.get("scale", 1))
        if op == "scene_list":
            return {"scenes": list_scenes(params.get("project") or None)}
        if op == "scene_new":
            project = str(params.get("project") or "")
            short = str(params.get("name") or "Scene")
            summary = create(f"{project}/{short}" if project else short).summary()
            return {**summary, "name": f"{projects.require(project)}/{summary['name']}"} if project else summary
        if op == "scene_rename":
            return _rename_scene(name, params)
        scene = load(name)
        fn = _OPS.get(op)
        if fn is None:
            raise _no_such("op", op, sorted(_OPS))
        if op == "select":
            result = fn(scene, params)
            # Selection is session state: observable, without geometry/history/disk work.
            scene.data["rev"] = int(scene.data.get("rev") or 0) + 1
            result["mode"] = scene.data["mode"]
            result["select_mode"] = scene.data["select_mode"]
            result["rev"] = scene.data.get("rev")
            result["name"] = scene.name
            return result
        if op == "edit" and set(params) <= {"id", "pos", "rot", "scale", "transform"}:
            return _edit_transform(scene, params, actor)
        if op == "hq" and str(params.get("op") or "help") == "help":
            return fn(scene, params)
        editing = op not in _READ and op not in {"undo", "redo", "revert"}
        before = scene._snapshot() if editing and op not in _LOOK else None
        try:
            result = fn(scene, params)
        except Exception as exc:
            # A failed mesh operation may already have baked a prototype. Cached scenes
            # must roll back just as a fresh disk load did before session caching.
            if before is not None:
                scene.data = before
            better = _hint_error(scene, exc)
            if better is not exc:
                raise better from None
            raise
        if op == "hq" and (result.get("state") or result.get("job") or result.get("ok") is False):
            editing = False  # engine installing, job queued or failed: the scene did not change yet
        if editing:
            ids = list(result.get("ids") or [])
            scene.commit(
                actor, op, str(result.get("summary") or op), ids,
                kind="look" if op in _LOOK else "edit", snapshot=before,
            )
        result["rev"] = scene.data.get("rev")
        result["geom"] = scene.data.get("geom") or 0
        result["name"] = scene.name
        return result


def _sel_ids(scene: Scene, params: dict) -> list:
    if params.get("ids"):
        return list(params["ids"])
    sel = scene.data["selection"]
    mode = params.get("kind") or scene.data.get("select_mode")
    if mode == "vert":
        return list(sel.get("verts") or [])
    if mode == "edge":
        return list(sel.get("edges") or [])
    return list(sel.get("faces") or [])


def _op_add(scene: Scene, p: dict) -> dict[str, Any]:
    kind = str(p.get("kind") or "box")
    params = dict(p.get("params") or {})
    mat = _material_ref(scene, str(p["material"])) if p.get("material") else scene.data["materials"][0]["id"]
    proto = scene.share_primitive(kind, params)
    count = max(1, int(p.get("count") or 1))
    base_name = str(p.get("name") or kind.replace("_", " ").title())
    origin = list(p.get("pos") or [0, 0, 0])
    stride = int(count ** 0.5) or 1
    ids = []
    first = None
    for i in range(count):
        node = {
            "id": _nid("o"),
            "type": "object",
            "name": base_name if count == 1 else f"{base_name} {i + 1}",
            "parent": p.get("parent"),
            "layer": str(p.get("layer") or "default"),
            "visible": True,
            "locked": False,
            "transform": {
                "pos": [origin[0] + (i % stride) * 512.0, origin[1] + (i // stride) * 512.0, origin[2]],
                "rot": list(p.get("rot") or [0, 0, 0]),
                "scale": list(p.get("scale") or [1, 1, 1]),
            },
            "prototype": proto["id"],
            "modifiers": [],
            "material": mat,
        }
        scene.data["nodes"].append(node)
        ids.append(node["id"])
        first = first or node["id"]
    scene.data["selection"]["object"] = first
    scene.touch_geom()
    return {"summary": f"Add {base_name}" + (f" x{count}" if count > 1 else ""), "ids": ids, "id": first}


def _transform_result(scene, nid, summary):
    return {"summary": summary, "ids": [nid], "name": scene.name,
            "rev": scene.data["rev"], "geom": scene.data["geom"],
            "transforms": {nid: copy.deepcopy(scene.node(nid)["transform"])}}


def _edit_transform(scene, p, actor):
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object")))
    if node is None:
        raise ValueError("object not found")
    if scene.is_locked(node):
        raise ValueError("Unlock the object or its layer before editing")
    before = copy.deepcopy(node["transform"])
    transform = copy.deepcopy(before)
    supplied = dict(p.get("transform") or {})
    supplied.update({k: p[k] for k in ("pos", "rot", "scale") if k in p})
    for key in ("pos", "rot", "scale"):
        if key not in supplied:
            continue
        values = [float(v) for v in supplied[key]]
        if len(values) != 3 or not all(math.isfinite(v) for v in values):
            raise ValueError("Transform requires three finite numbers per axis")
        transform[key] = values
    if transform == before:
        return _transform_result(scene, node["id"], "Transform unchanged")
    history.push_transform(scene._hist(), node["id"], before, scene.data["rev"])
    node["transform"] = transform
    scene.touch_geom()
    scene._note(actor, "edit", f"Move {node.get('name')}", [node["id"]], "edit")
    scene.save_transform(node["id"])
    scene.patch_payload_transform(node["id"])
    return _transform_result(scene, node["id"], f"Transform {node.get('name')}")


def _op_edit(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object")))
    if node is None:
        raise ValueError("object not found")
    if scene.is_locked(node) and any(key in p for key in ("parent", "pos", "rot", "scale", "transform", "params", "material")):
        raise ValueError("Unlock the object or its layer before editing")
    if "parent" in p:
        _validate_parent(scene, node["id"], p["parent"])
        if p["parent"] != node.get("parent"):
            node["transform"] = _reparent_transform(scene, node, p["parent"])
    for key in ("name", "parent", "layer", "visible", "locked", "material"):
        if key in p:
            node[key] = p[key] or None if key == "parent" else p[key]
    if p.get("transform"):
        node["transform"] = {
            "pos": list(p["transform"].get("pos") or node["transform"]["pos"]),
            "rot": list(p["transform"].get("rot") or node["transform"]["rot"]),
            "scale": list(p["transform"].get("scale") or node["transform"]["scale"]),
        }
    if p.get("pos") or p.get("rot") or p.get("scale"):
        t = node["transform"]
        if "pos" in p:
            t["pos"] = list(p["pos"])
        if "rot" in p:
            t["rot"] = list(p["rot"])
        if "scale" in p:
            t["scale"] = list(p["scale"])
    if "params" in p:
        proto = scene.proto(node)
        if proto and not proto.get("mesh"):
            params = dict(proto.get("params") or {})
            params.update(p["params"])
            if scene.user_count(proto["id"]) > 1:
                node["prototype"] = scene.share_primitive(str(proto.get("kind") or "box"), params)["id"]
            else:
                proto["params"] = params
                proto["key"] = _proto_key(str(proto.get("kind") or "box"), params)
    scene.touch_geom()
    return {"summary": f"Edit {node.get('name')}", "ids": [node["id"]]}


def _op_duplicate(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object")))
    if node is None:
        raise ValueError("object not found")
    copy = json.loads(json.dumps(node))
    copy["id"] = _nid("o" if node.get("type") == "object" else "g")
    copy["name"] = str(node.get("name") or "Object") + " copy"
    pos = list((copy.get("transform") or {}).get("pos") or [0, 0, 0])
    off = list(p.get("offset") or [512, 0, 0])
    copy["transform"]["pos"] = [pos[i] + off[i] for i in range(3)]
    scene.data["nodes"].append(copy)
    scene.data["selection"]["object"] = copy["id"]
    scene.touch_geom()
    return {"summary": f"Duplicate {node.get('name')}", "ids": [copy["id"]]}


def _op_delete(scene: Scene, p: dict) -> dict[str, Any]:
    ids = set(p.get("ids") or [scene.data["selection"].get("object")])
    ids.discard(None)
    children = {}
    for n in scene.data["nodes"]:
        children.setdefault(n.get("parent"), []).append(n["id"])
    pending = list(ids)
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in ids:
                ids.add(child)
                pending.append(child)
    if any(scene.is_locked(n) for n in scene.data["nodes"] if n["id"] in ids):
        raise ValueError("Unlock objects or layers before deleting")
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] not in ids and n.get("parent") not in ids]
    if scene.data["selection"].get("object") in ids:
        scene.data["selection"]["object"] = None
    scene._gc_protos()
    scene.touch_geom()
    return {"summary": f"Delete {len(ids)}", "ids": list(ids)}


def _validate_parent(scene: Scene, nid: str, parent: str | None) -> None:
    seen = {nid}
    while parent:
        if parent in seen:
            raise ValueError("A group cannot contain itself or its ancestors")
        seen.add(parent)
        node = scene.node(parent)
        if not node or node.get("type") != "group":
            raise ValueError("Parent must be an existing group")
        parent = node.get("parent")


def _reparent_transform(scene: Scene, node: dict, parent: str | None) -> dict:
    from .transforms import world, inverse, multiply, decompose
    return decompose(multiply(inverse(world(scene, parent)), world(scene, node["id"])))


def _op_group(scene: Scene, p: dict) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(i) for i in p.get("ids", []) if i))
    nodes = [scene.node(nid) for nid in ids]
    if any(n is None for n in nodes):
        raise ValueError("Select existing objects or groups first")
    if any(scene.is_locked(n) for n in nodes):
        raise ValueError("Unlock objects before grouping")
    # Selected descendants remain inside their selected ancestors.
    selected = set(ids)
    def nested(n):
        parent = n.get("parent")
        seen = set()
        while parent:
            if parent in seen:
                raise ValueError("Scene hierarchy contains a cycle")
            seen.add(parent)
            if parent in selected:
                return True
            ancestor = scene.node(parent)
            parent = ancestor.get("parent") if ancestor else None
        return False
    nodes = [n for n in nodes if not nested(n)]
    into = p.get("into")
    gid = into or _nid("g")
    target_parent = into or p.get("parent")
    if not into:
        _validate_parent(scene, gid, target_parent)
    for node in nodes:
        _validate_parent(scene, node["id"], target_parent)
    transforms = [_reparent_transform(scene, node, target_parent) for node in nodes]
    if not into:
        scene.data["nodes"].append({
            "id": gid, "type": "group", "name": str(p.get("name") or "Group"),
            "parent": p.get("parent"), "layer": "default", "visible": True, "locked": False,
            "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
            "combine": bool(p.get("combine", True)),
        })
    for node, transform in zip(nodes, transforms):
        node["parent"] = gid
        node["transform"] = transform
    scene.data["selection"]["object"] = gid
    scene.touch_geom()
    return {"summary": "Nest" if into else "Group", "ids": [gid], "id": gid}


def _op_ungroup(scene: Scene, p: dict) -> dict[str, Any]:
    gid = str(p.get("id") or scene.data["selection"].get("object") or "")
    group = scene.node(gid)
    if not group or group.get("type") != "group":
        raise ValueError("Select a group to ungroup")
    parent = group.get("parent")
    children = [n for n in scene.data["nodes"] if n.get("parent") == gid]
    if scene.is_locked(group) or any(scene.is_locked(n) for n in children):
        raise ValueError("Unlock the group and its children before ungrouping")
    transforms = [_reparent_transform(scene, n, parent) for n in children]
    for node, transform in zip(children, transforms):
        node["parent"] = parent
        node["transform"] = transform
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] != gid]
    if scene.data["selection"].get("object") == gid:
        scene.data["selection"]["object"] = parent
    scene.touch_geom()
    return {"summary": "Ungroup", "ids": [gid]}


def _op_layer(scene: Scene, p: dict) -> dict[str, Any]:
    layers = scene.data.setdefault("layers", [])
    action = str(p.get("action") or "set")
    if action == "add":
        lid = _nid("l")
        layers.append({
            "id": lid, "name": str(p.get("name") or "Layer"),
            "visible": True, "locked": False, "color": str(p.get("color") or "#6c8cff"),
        })
        scene.touch_geom()
        return {"summary": "Add layer", "ids": [lid], "id": lid}
    if action == "assign":
        lid = str(p.get("id") or p.get("layer") or "default")
        ids = [str(i) for i in (p.get("ids") or [scene.data["selection"].get("object")]) if i]
        for nid in ids:
            node = scene.node(nid)
            if node:
                node["layer"] = lid
        scene.touch_geom()
        return {"summary": "Assign layer", "ids": ids}
    lid = str(p.get("id") or "")
    layer = next((item for item in layers if item.get("id") == lid), None)
    if layer is None:
        raise ValueError("layer not found")
    for key in ("name", "visible", "locked", "color"):
        if key in p:
            layer[key] = p[key]
    scene.touch_geom()
    return {"summary": f"Layer {layer.get('name')}", "ids": [lid]}


def _op_select(scene: Scene, p: dict) -> dict[str, Any]:
    sel = scene.data["selection"]
    if "mode" in p and p["mode"] not in ("object", "edit", "sculpt", "weight", "vpaint", "tpaint"):
        raise ValueError("Invalid modeling mode")
    if "select_mode" in p and p["select_mode"] not in ("face", "edge", "vert"):
        raise ValueError("Invalid selection mode")
    if "object" in p:
        target = p.get("object") or None
        if target and scene.node(target) is None:
            raise ValueError("object not found")
        if target != sel.get("object"):
            for key in ("verts", "edges", "faces"):
                sel[key] = []
        sel["object"] = target
    if "mode" in p:
        scene.data["mode"] = p["mode"]
    if "select_mode" in p:
        scene.data["select_mode"] = p["select_mode"]
    for key in ("verts", "edges", "faces"):
        if key in p:
            sel[key] = list(p[key] or [])
    query = str(p.get("query") or "")
    node = scene.node(sel.get("object"))
    if query and node and node.get("type") == "object":
        mesh = scene.base_mesh(node)
        if query == "top":
            sel["faces"] = [fi for fi in range(len(mesh.faces)) if mesh.face_normal(fi)[2] > 0.7]
            scene.data["select_mode"] = "face"
        elif query == "all":
            sel["faces"] = list(range(len(mesh.faces)))
    return {"summary": "Select", "ids": [sel.get("object")], "selection": copy.deepcopy(sel)}


def _op_move(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    mesh = scene._bake(node)
    kind = str(p.get("kind") or scene.data.get("select_mode") or "vert")
    n = move_elements(mesh, kind, _sel_ids(scene, p), p.get("delta") or [0, 0, 0])
    scene.write_mesh(node, mesh)
    return {"summary": f"Move {n}", "ids": [node["id"]]}


def _face_edges(mesh: Mesh, faces: list) -> list[list[int]]:
    edges: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    for fi in faces:
        if not (0 <= int(fi) < len(mesh.faces)):
            continue
        face = mesh.faces[int(fi)]
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            if key not in seen:
                seen.add(key)
                edges.append([a, b])
    return edges


def _op_mesh(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    which = str(p.get("op") or "")
    # Whole-object reshapes: no face selection needed.
    if which == "subdivide" and p.get("smooth"):
        from .kernel.sculpt import catmull_clark

        levels = max(1, min(4, int(p.get("levels", 1))))
        mesh = catmull_clark(scene._bake(node), levels)
        scene.write_mesh(node, mesh)
        return {"summary": f"Smooth subdivide x{levels}", "ids": [node["id"]], "tris": mesh.tri_count()}
    if which == "decimate":
        from .kernel.sculpt import decimate

        mesh = scene._bake(node)
        before = mesh.tri_count()
        decimate(mesh, float(p.get("ratio", 0.5)))
        scene.write_mesh(node, mesh)
        return {"summary": f"Decimate {before} to {mesh.tri_count()} triangles", "ids": [node["id"]], "tris": mesh.tri_count()}
    sel = scene.data["selection"]
    faces = list(p.get("faces") or sel.get("faces") or [])
    if which == "delete" and p.get("kind") == "edge":
        edges = {tuple(sorted(edge)) for edge in (p.get("edges") or sel.get("edges") or [])}
        if not edges:
            raise ValueError("Click an edge in Edit mode first")
        base = scene.base_mesh(node)
        faces = [fi for fi, face in enumerate(base.faces) if any(tuple(sorted((a, face[(i+1) % len(face)]))) in edges for i, a in enumerate(face))]
    if which in {"extrude", "inset", "subdivide", "delete"} and not faces and not (which == "delete" and (p.get("verts") or sel.get("verts"))):
        raise ValueError("Click a face in Edit mode first (agent: pass faces=[...] or forge_select(query='top'))")
    if which == "bevel" and not (p.get("edges") or sel.get("edges") or faces):
        raise ValueError("Click an edge, or a face, then Bevel (agent: pass edges=[[a, b]] or faces=[...])")
    if which == "loopcut" and not (p.get("edge") or sel.get("edges")):
        raise ValueError("Click an edge, then Loop cut (agent: forge_select(edges=[[a, b]]) first)")
    mesh = scene._bake(node)
    if which == "extrude":
        extrude_faces(mesh, faces, float(p.get("distance", 10)))
    elif which == "inset":
        inset_faces(mesh, faces, float(p.get("amount", 5)))
    elif which == "bevel":
        edges = p.get("edges") or sel.get("edges") or []
        if not edges and faces:
            edges = _face_edges(mesh, faces)
        if not edges:
            raise ValueError("Click an edge, or a face, then Bevel")
        bevel_edges(mesh, edges, float(p.get("width", 4)), int(p.get("segments", 1)))
    elif which == "loopcut":
        loop_cut(mesh, (p.get("edge") or (sel.get("edges") or [None])[0]), float(p.get("t", 0.5)))
    elif which == "subdivide":
        subdivide(mesh, faces or None)
    elif which == "bridge":
        bridge_loops(mesh, p.get("loop_a") or [], p.get("loop_b") or [], node.get("material") or "default")
    elif which == "fill":
        fill_boundary(mesh, node.get("material") or "default")
    elif which == "merge":
        if p.get("mode") == "center":
            merge_center(mesh, p.get("verts") or sel.get("verts") or [])
        else:
            merge_by_distance(mesh, float(p.get("distance", 0.05)))
    elif which == "dissolve":
        dissolve_edges(mesh, p.get("edges") or sel.get("edges") or [])
    elif which == "delete":
        kind = str(p.get("kind") or "face")
        if kind == "vert":
            delete_verts(mesh, p.get("verts") or sel.get("verts") or [])
        else:
            delete_faces(mesh, faces)
    elif which == "normals":
        if p.get("flip"):
            flip_faces(mesh, faces or None)
        else:
            recalculate_normals(mesh)
    elif which == "shade":
        set_smooth(mesh, bool(p.get("smooth", True)))
    elif which == "origin":
        shift = set_origin(mesh, str(p.get("mode") or "base"))
        t = dict(node["transform"])
        t["pos"] = [t["pos"][i] + shift[i] for i in range(3)]
        node["transform"] = t
    else:
        raise _no_such("mesh op", which, ["extrude", "inset", "bevel", "loopcut", "subdivide", "decimate", "bridge", "fill",
                                         "merge", "dissolve", "delete", "normals", "shade", "origin"])
    scene.write_mesh(node, mesh)
    return {"summary": which or "mesh", "ids": [node["id"]], "tris": mesh.tri_count()}


def _mesh_in_parent(scene: Scene, mesh: Mesh, parent: str | None) -> Mesh:
    if parent:
        from .transforms import inverse, world
        m = inverse(world(scene, parent))
        mesh.verts = [[sum(m[i][j] * vertex[j] for j in range(3)) + m[i][3] for i in range(3)] for vertex in mesh.verts]
        for name, deltas in mesh.attrs.items():
            if name.startswith("sk:"):  # directions: linear part only
                mesh.attrs[name] = [[sum(m[i][j] * d[j] for j in range(3)) for i in range(3)] for d in deltas]
    return mesh


def _op_bool(scene: Scene, p: dict) -> dict[str, Any]:
    """a op b. b may be a list: cut many holes, or union many parts, in one step."""
    a = scene.node(str(p.get("a")))
    targets = p.get("b") if isinstance(p.get("b"), list) else [p.get("b")]
    cutters = [scene.node(str(b)) for b in targets]
    if not a or not cutters or any(b is None for b in cutters):
        raise ValueError("boolean needs a and b object ids")
    if any(b is a or b.get("type") != "object" for b in cutters) or a.get("type") != "object":
        raise ValueError("Boolean needs two different mesh objects")
    if scene.is_locked(a) or any(scene.is_locked(b) for b in cutters):
        raise ValueError("Unlock both objects before combining")
    result = scene.world_mesh(a)
    for b in cutters:
        result = csg_boolean(result, scene.world_mesh(b), str(p.get("op") or "difference"))
    result = _mesh_in_parent(scene, result, a.get("parent"))
    node = {
        "id": _nid("o"), "type": "object", "name": f"{a.get('name')}_{p.get('op') or 'cut'}",
        "parent": a.get("parent"), "layer": a.get("layer") or "default",
        "visible": True, "locked": False,
        "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
        "prototype": scene.adopt_mesh(result), "modifiers": [],
        "material": a.get("material"),
    }
    drop = {a["id"]} | {b["id"] for b in cutters}
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] not in drop]
    scene.data["nodes"].append(node)
    scene.data["selection"]["object"] = node["id"]
    scene._gc_protos()
    scene.touch_geom()
    return {"summary": f"Boolean {p.get('op') or 'difference'}", "ids": [node["id"]], "id": node["id"]}


def _op_modifier(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    mods = list(node.get("modifiers") or [])
    action = str(p.get("action") or "add")
    if action == "add":
        mods.append({"type": p.get("type") or "mirror", "axis": p.get("axis") or "x", "count": p.get("count") or 3, "offset": p.get("offset") or [120, 0, 0]})
    elif action == "apply":
        mesh = scene.local_mesh(node)
        scene.write_mesh(node, mesh)
        mods = []
    elif action == "clear":
        mods = []
    node["modifiers"] = mods
    scene.touch_geom()
    return {"summary": f"Modifier {action}", "ids": [node["id"]]}


def _op_join(scene: Scene, p: dict) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(i) for i in (p.get("ids") or [])))
    nodes = [scene.node(i) for i in ids]
    nodes = [n for n in nodes if n and n.get("type") == "object"]
    if len(nodes) < 2:
        raise ValueError("join needs two or more objects")
    if any(scene.is_locked(n) for n in nodes):
        raise ValueError("Unlock objects before joining")
    mesh = Mesh()
    for n in nodes:
        mesh.append(scene.world_mesh(n))
    if p.get("weld"):
        merge_by_distance(mesh, float(p.get("distance", 0.05)))
    mesh = _mesh_in_parent(scene, mesh, nodes[0].get("parent"))
    node = {
        "id": _nid("o"), "type": "object", "name": str(nodes[0].get("name") or "Joined"),
        "parent": nodes[0].get("parent"), "layer": nodes[0].get("layer") or "default",
        "visible": True, "locked": False,
        "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
        "prototype": scene.adopt_mesh(mesh), "modifiers": [],
        "material": nodes[0].get("material"),
    }
    drop = {n["id"] for n in nodes}
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] not in drop]
    scene.data["nodes"].append(node)
    scene.data["selection"]["object"] = node["id"]
    scene._gc_protos()
    scene.touch_geom()
    return {"summary": f"Join {len(nodes)}", "ids": [node["id"]], "id": node["id"]}


def _op_separate(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    parts = separate_by_material(scene.local_mesh(node))
    if len(parts) <= 1:
        return {"summary": "Nothing to separate", "ids": [node["id"]]}
    ids = []
    for i, part in enumerate(parts):
        child = json.loads(json.dumps(node))
        child["id"] = _nid("o")
        child["name"] = f"{node.get('name')}_{i + 1}"
        child.pop("mesh", None)
        child.pop("primitive", None)
        child["prototype"] = scene.adopt_mesh(part)
        child["modifiers"] = []
        child["material"] = part.face_mat[0] if part.face_mat else node.get("material")
        scene.data["nodes"].append(child)
        ids.append(child["id"])
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] != node["id"]]
    scene._gc_protos()
    scene.touch_geom()
    return {"summary": f"Separate into {len(ids)}", "ids": ids}


def _op_material_set(scene: Scene, p: dict) -> dict[str, Any]:
    # The tools pass the AI's reference (an id or a name) as both id and name: update that
    # material, never add a same-named twin, and never rename it to its own id.
    ref = str(p.get("id") or "")
    mats = scene.data["materials"]
    found = next((m for m in mats if m["id"] == ref), None) or next((m for m in mats if m["name"] == (ref or p.get("name"))), None)
    if found is None:
        found = _mat(_nid("m"), str(p.get("name") or "Material"), p.get("base_color"))
        mats.append(found)
    for key in ("name", "base_color", "metallic", "roughness", "emissive", "emissive_strength", "opacity"):
        if key in p and not (key == "name" and p[key] == ref):
            found[key] = p[key]
    if p.get("textures"):
        found["textures"] = dict(p["textures"])
    if "layers" in p:
        # Up to three materials revealed over this one by blend paint R, G, B.
        layers = []
        for ref in list(p.get("layers") or [])[:3]:
            layer = next((m for m in scene.data["materials"] if m["id"] == ref or m["name"] == ref), None) if ref else None
            if ref and layer is None:
                raise ValueError(f"No material {ref!r} to use as a layer")
            if layer is found:
                raise ValueError("A material cannot be its own layer")
            layers.append(layer["id"] if layer else "")
        found["layers"] = layers
    scene.touch_geom()
    return {"summary": f"Material {found['name']}", "ids": [found["id"]], "material": found}


def _material_ref(scene: Scene, ref: str) -> str:
    """The id of the material the caller means, by id or by name."""
    for m in scene.data["materials"]:
        if ref in (m["id"], m["name"]):
            return m["id"]
    err = _no_such("material", ref, [m["name"] for m in scene.data["materials"]])
    raise ValueError(f"{err} (forge_material_set makes a new one)")


def _op_material_assign(scene: Scene, p: dict) -> dict[str, Any]:
    node = _object(scene, p)
    mid = _material_ref(scene, str(p.get("material") or ""))
    faces = p.get("faces")
    if not faces:
        node["material"] = mid
        node["material_override"] = True
        scene.touch_geom()
    else:
        mesh = scene._bake(node)
        for fi in faces:
            if 0 <= int(fi) < len(mesh.face_mat):
                mesh.face_mat[int(fi)] = mid
        scene.write_mesh(node, mesh)
    return {"summary": "Assign material", "ids": [node["id"]]}


def _op_uv(scene: Scene, p: dict) -> dict[str, Any]:
    from .uv import smart_unwrap, texel_density

    node = _object(scene, p) if p.get("id") else scene._active()
    mesh = scene._bake(node)
    mode = str(p.get("mode") or "box")
    if mode == "smart":
        charts = smart_unwrap(mesh, p.get("faces") or None, float(p.get("angle", 66)), float(p.get("margin", 0.01)))
        scene.write_mesh(node, mesh)
        return {"summary": f"Unwrap {node.get('name')}: {charts} charts", "ids": [node["id"]], "charts": charts,
                "px_per_cm_1024": round(texel_density(mesh), 2)}
    n = project(mesh, mode, p.get("faces"), float(p.get("density", 0.01)))
    scene.write_mesh(node, mesh)
    return {"summary": f"UV {mode} on {n} faces", "ids": [node["id"]]}


def _restore(scene: Scene, target: dict, label: str) -> None:
    if "_transform_patch" in target:
        patch = target["_transform_patch"]
        node = scene.node(patch["id"])
        if node is None:
            raise ValueError("Undo target no longer exists")
        node["transform"] = patch["transform"]
        scene.touch_geom()
        scene._note("user", label.lower(), label, [node["id"]], "look")
        scene.save_transform(node["id"])
        scene.patch_payload_transform(node["id"])
        return _transform_result(scene, node["id"], label)
    rev, geom = scene.data.get("rev", 0), scene.data.get("geom", 0)
    scene.data = target
    scene.data["rev"] = rev
    scene.data["geom"] = geom + 1
    scene._note("user", label.lower(), label, [], "look")
    scene.save()


def _op_undo(scene: Scene, _p: dict) -> dict[str, Any]:
    prev = history.undo(scene._hist(), scene.data)
    if prev is None:
        return {"summary": "Nothing to undo", "ids": []}
    result = _restore(scene, prev, "Undo")
    if result: return result
    return {"summary": "Undo", "ids": []}


def _op_redo(scene: Scene, _p: dict) -> dict[str, Any]:
    nxt = history.redo(scene._hist(), scene.data)
    if nxt is None:
        return {"summary": "Nothing to redo", "ids": []}
    result = _restore(scene, nxt, "Redo")
    if result: return result
    return {"summary": "Redo", "ids": []}


def _op_revert(scene: Scene, p: dict) -> dict[str, Any]:
    target = history.revert_to(scene._hist(), scene.data, int(p.get("rev") or 0))
    if target is None:
        return {"summary": "No snapshot at that rev", "ids": []}
    _restore(scene, target, "Revert")
    return {"summary": f"Revert to rev {p.get('rev')}", "ids": []}


def _op_save_file(scene: Scene, p: dict) -> dict[str, Any]:
    from .files import save_forge

    saved = save_forge(scene.name, str(p.get("path") or ""))
    return {"summary": f"Saved {saved['path']}", "ids": [], "path": saved["path"], "file_name": saved["name"]}


def _op_open_file(_scene: Scene, p: dict) -> dict[str, Any]:
    from .files import open_forge

    opened = open_forge(str(p.get("path") or ""))
    forget(opened["name"])
    return {"summary": f"Opened {opened['name']}", "ids": [], "opened": opened["name"], "path": opened["path"]}


def _op_list_files(_scene: Scene, _p: dict) -> dict[str, Any]:
    from .files import list_files

    return {"summary": "files", "ids": [], "files": list_files()}


def _op_import_blend(_scene: Scene, p: dict) -> dict[str, Any]:
    from .blend_import import import_blend

    imported = import_blend(str(p.get("path") or ""), str(p.get("name") or ""), scale=p.get("scale", 1))
    forget(imported["name"])
    return {"summary": f"Imported {imported['name']}", "ids": [], **imported}


def _op_import_mesh(scene: Scene, p: dict) -> dict[str, Any]:
    from .blend_import import import_blend
    return import_blend(str(p.get("path") or ""), target=scene, scale=p.get("scale", 1))


def _op_get(scene: Scene, p: dict) -> dict[str, Any]:
    if p.get("brief"):
        data = scene.data
        nodes = data.get("nodes") or []
        protos = data.get("prototypes") or []
        return {
            "summary": "scene", "ids": [], "name": scene.name,
            "rev": data.get("rev"), "geom": data.get("geom") or 0,
            "mode": data.get("mode"), "select_mode": data.get("select_mode"),
            "selection": data.get("selection"),
            "objects": sum(1 for n in nodes if n.get("type") == "object"),
            "prototypes": len(protos),
            "smooth": sum(1 for p in protos if (p.get("mesh") or {}).get("smooth")),
        }
    info = scene.summary()
    info["summary"] = "scene"
    info["ids"] = []
    return info


def _op_geom_part(scene: Scene, p: dict) -> dict[str, Any]:
    pid = str(p.get("id") or "")
    field = str(p.get("field") or "")
    blob = scene._geom_hold.get((pid, field))
    if not isinstance(blob, str):
        raise ValueError("mesh piece is not loaded")
    index = int(p.get("index") or 0)
    start = index * _GEOM_PART
    if start < 0 or start >= len(blob):
        raise ValueError("mesh piece is out of range")
    return {"data": blob[start:start + _GEOM_PART], "summary": "piece", "ids": []}


def _op_payload(scene: Scene, _p: dict) -> dict[str, Any]:
    previous = scene._payload_versions.get(_p.get("since_geom"))
    info = scene.payload()
    if previous is not None:
        def patch(old, new, key):
            before = {key(item): item for item in old}
            after = {key(item): item for item in new}
            result = {"remove": [i for i in before if i not in after],
                      "upsert": [item for i, item in after.items() if i not in before or before[i] != item]}
            if list(before) != list(after):
                result["order"] = list(after)
            return result
        def groups(rows):
            out = {}
            for row in rows:
                out.setdefault(row[0], []).append(row)
            return list(out.values())
        info["node_patch"] = patch(previous["nodes"], info.pop("nodes"), lambda n: n["id"])
        info["instance_patch"] = patch(groups(previous["instances"]), groups(info.pop("instances")), lambda rows: rows[0][0])
        info["base_geom"] = _p["since_geom"]
    known = _p.get("known") or {}
    if known:
        info["prototypes"] = [{"id": p["id"], "revision": p.get("revision"), "cached": True}
                              if p.get("revision") and known.get(p["id"]) == p["revision"] else p
                              for p in info["prototypes"]]
    info["summary"] = "payload"
    info["ids"] = []
    return info


def _op_stats(scene: Scene, _p: dict) -> dict[str, Any]:
    info = scene.summary()
    return {"summary": "stats", "ids": [], "objects": info["objects"], "rev": info["rev"]}


def _captures() -> Path:
    """Staging dir for a render. The host moves the PNG into the chat folder."""
    env = os.environ.get("FORGE_CAPTURES_DIR")
    if env:
        path = Path(env)
    else:
        import tempfile

        path = Path(tempfile.gettempdir()) / "ducky_forge_captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _op_check(scene: Scene, p: dict) -> dict[str, Any]:
    has_col = any(n.get("layer") == "collision" for n in scene.data["nodes"])
    issues: list[dict[str, Any]] = []
    for n in scene.data["nodes"]:
        if n.get("type") != "object":
            continue
        mesh = scene.local_mesh(n)
        lo, _hi = mesh.bounds()
        pivot_base = abs(lo[2]) < 0.5 and abs((lo[0] + _hi[0]) / 2) < 1 and abs((lo[1] + _hi[1]) / 2) < 1
        issues.extend(check_mesh(mesh, n["id"], has_collision=has_col or bool(p.get("collision")), pivot_is_base=pivot_base))
    for rig in scene.data.get("rigs") or []:
        issues.extend(rig_issues(scene, rig))
    return {"summary": f"{len(issues)} issues", "ids": [], "issues": issues, "count": len(issues)}


def _op_fix(scene: Scene, p: dict) -> dict[str, Any]:
    kinds = p.get("issues") or "all"
    if kinds == "all" or kinds == ["all"]:
        wanted = {"all"}
    elif isinstance(kinds, str):
        wanted = {kinds}
    else:
        wanted = set(kinds)
    fixed = []
    target = p.get("id") or scene.data["selection"].get("object")
    for n in scene.data["nodes"]:
        if n.get("type") != "object":
            continue
        if target and n["id"] != target:
            continue
        mesh = scene._bake(n)
        before = mesh.tri_count()
        done = fix_mesh(mesh, wanted)
        scene.write_mesh(n, mesh)
        fixed.append({"id": n["id"], "before_tris": before, "after_tris": mesh.tri_count(), "fixes": done})
    return {"summary": f"Fix {len(fixed)}", "ids": [f["id"] for f in fixed], "fixed": fixed}


def _style(value: Any) -> str:
    """'pbr' (default, the engine's empty style) or 'toon'."""
    v = str(value or "pbr").strip().lower()
    if v not in ("pbr", "toon"):
        raise ValueError(f"style={value!r} is not a render style. Use pbr or toon")
    return "" if v == "pbr" else v


def _op_render(scene: Scene, p: dict) -> dict[str, Any]:
    """The whole asset in one image (or one object with id), or with a clip, posed at each frame."""
    from . import render as rd
    from .render import render_png

    style = _style(p.get("style"))
    views = list(p.get("views") or ["three"])
    paths = []
    folder = _captures()
    mats = scene.materials()
    nodes = [n for n in scene.data["nodes"] if n.get("type") == "object" and n.get("visible", True)
             and (not p.get("id") or n["id"] == p["id"])]
    if p.get("clip"):
        rig = find_rig(scene, str(p.get("rig") or ""))
        clip = find_clip(rig, str(p["clip"]))
        frames = [float(f) for f in (p.get("frames") or [0, clip["frames"] / 4, clip["frames"] / 2, clip["frames"] * 3 / 4])]
        for f in frames:
            dest = folder / f"forge_{scene.name}_{_safe(clip['name'])}_f{round(f)}.png"
            paths.extend(render_png(posed_meshes(scene, nodes, rig, clip, f), dest, views, mats, scene.folder / "textures", style=style))
        return _noted({"summary": f"Render {clip['name']} at {len(frames)} frames", "ids": [], "paths": paths, "kind": "look"}, rd)
    # Oct 28 2026: one image per object showed a lion's body and its hair cards as two separate pictures.
    mesh = Mesh()
    for n in nodes:
        mesh.append(scene.world_mesh(n))
    dest = folder / f"forge_{scene.name}_{p.get('id') or 'all'}.png"
    paths.extend(render_png(mesh, dest, views, mats, scene.folder / "textures", style=style))
    return _noted({"summary": f"Render {len(paths)}", "ids": [], "paths": paths, "kind": "look"}, rd)


def _noted(result: dict, rd) -> dict:
    """A fallback picture says so in the result, so an AI never judges quality from it."""
    if rd.last_note:
        result["seen"] = (result.get("seen", "") + "; " if result.get("seen") else "") + rd.last_note
    return result


_LOOK_VIEWS = {"ai": ["three", "front", "side", "top"]}


def _op_look(scene: Scene, p: dict) -> dict[str, Any]:
    """One tiled preview plus a 'seen' line: bounds cm, tris, materials, check issues. look = a view, "ai", or ids."""
    from .render import _VIEWS, render_tiles

    look = p.get("look") or "three"
    style = p.get("style")
    if isinstance(look, dict):  # {view, style}
        style = look.get("style") or style
        look = look.get("view") or "three"
    style = _style(style)
    ids = set(look) if isinstance(look, list) else set()
    views = ["three"] if ids else _LOOK_VIEWS.get(look, [look])
    bad = [v for v in views if v not in _VIEWS]
    if bad:
        raise ValueError(f"look={look!r} is not a view. Use one of: {', '.join([*_VIEWS, 'ai'])}, or a list of ids")

    def wanted(n):
        while n is not None:
            if n["id"] in ids:
                return True
            n = scene.node(n.get("parent"))
        return False

    nodes = [n for n in scene.data["nodes"] if n.get("type") == "object" and n.get("visible", True)
             and (not ids or wanted(n))]
    mesh = Mesh()
    for n in nodes:
        mesh.append(scene.world_mesh(n))
    mats = scene.materials()
    lo, hi = mesh.bounds()
    used = sorted({mats.get(m, {}).get("name") or m for m in mesh.face_mat})
    keep = {n["id"] for n in nodes}
    issues = [i for i in _op_check(scene, {})["issues"] if not ids or i.get("object") in keep]
    kinds: dict = {}
    for i in issues:
        kinds[i["id"]] = kinds.get(i["id"], 0) + 1
    seen = "%s cm, %d tris, %s, %s" % (
        "x".join(str(round(hi[k] - lo[k])) for k in range(3)), mesh.tri_count(), ", ".join(used[:4]) or "no materials",
        "; ".join(f"{k} x{c}" for k, c in list(kinds.items())[:3]) or "no issues")
    from . import render as rd

    path = render_tiles(mesh, _captures() / f"forge_{scene.name}_look.png", views, mats, scene.folder / "textures",
                        style=style)
    return _noted({"summary": seen, "seen": seen, "ids": [], "paths": [path]}, rd)


def _op_texture(scene: Scene, p: dict) -> dict[str, Any]:
    from io import BytesIO

    from PIL import Image

    from .textures import generate_image

    mid = str(p.get("material") or "")
    mat = next((m for m in scene.data["materials"] if m["id"] == mid or (p.get("name") and m["name"] == p.get("name"))), None)
    if mat is None:
        mat = _mat(_nid("m"), str(p.get("name") or "Texture"))
        scene.data["materials"].append(mat)
    if p.get("png_base64"):
        raw = str(p["png_base64"])
        if "," in raw[:80]:
            raw = raw.split(",", 1)[1]
        color = Image.open(BytesIO(base64.b64decode(raw))).convert("RGB")
    elif p.get("path"):
        color = Image.open(p["path"]).convert("RGB")
    else:
        color = generate_image(str(p.get("prompt") or ""))
    rel = _save_texture(scene, mat, color, tileable=bool(p.get("tileable", True)), derive=bool(p.get("derive_maps", True)))
    return {"summary": f"Texture {mat['name']}", "ids": [mat["id"]], "textures": rel, "material": mat}


def _save_texture(scene: Scene, mat: dict, color, *, tileable: bool, derive: bool) -> dict:
    """Write a new texture file set for mat, keep its other slots, and drop files nothing points at."""
    from .textures import save_set

    before = dict(mat.get("textures") or {})
    saved = save_set(
        scene.folder / "textures",
        _safe(mat["name"]),
        color,
        tileable=tileable,
        derive=derive,
        metallic=float(mat.get("metallic") or 0),
        tag="_" + secrets.token_hex(3),
    )
    # store filenames relative to the scene so the panel can ask for bytes
    rel = {slot: Path(path).name for slot, path in saved.items()}
    mat["textures"] = {**before, **rel}
    scene.touch_geom()
    _gc_textures(scene, keep=set(before.values()))
    return mat["textures"]


def _texture_refs(data: dict) -> set[str]:
    return {name for m in data.get("materials") or [] for name in (m.get("textures") or {}).values()}


def _gc_textures(scene: Scene, keep: set[str]) -> None:
    """Delete texture files no state points at. keep holds this edit's old files, which undo still needs."""
    folder = scene.folder / "textures"
    if not folder.is_dir():
        return
    refs = set(keep) | _texture_refs(scene.data)
    try:
        for snap in (scene.folder / "history-snapshots").glob("*.json"):
            refs |= _texture_refs(json.loads(snap.read_text(encoding="utf-8")))
        legacy = scene.folder / "history.json"
        if legacy.is_file():
            old = json.loads(legacy.read_text(encoding="utf-8"))
            for state in (old.get("undo") or []) + (old.get("redo") or []):
                refs |= _texture_refs(state)
    except (OSError, ValueError):
        return  # unreadable history: keeping stray files beats losing an undo target
    for item in folder.iterdir():
        if item.is_file() and item.name not in refs:
            try:
                item.unlink()
            except OSError:
                pass


def _object(scene: Scene, p: dict, *, edit: bool = True) -> dict:
    nid = str(p.get("id") or scene.data["selection"].get("object") or "")
    node = (scene.node(nid) or next((n for n in scene.data["nodes"] if n.get("name") == nid), None)) if nid else scene.last_object()
    if node is None or node.get("type") != "object":
        if nid:
            raise _no_such("object", nid, [n.get("name") for n in scene.data["nodes"] if n.get("type") == "object"])
        raise ValueError("No object yet: forge_add(kind='box') first")
    if edit and scene.is_locked(node):
        raise ValueError(f"{node.get('name')} is locked")
    return node


def _to_local(scene: Scene, node: dict, points) -> tuple[list, float]:
    from .transforms import to_local

    return to_local(scene, node["id"], points)


_DENSE_VERTS = 64  # fewer than this and a brush or vertex mask has nothing to move: a box becomes 98 after x2


def _dense(mesh: Mesh, step, how: str) -> tuple[Mesh, list]:
    """Subdivide a too-coarse mesh (up to x4) so sculpt and blend paint work; meshes with keys or weights stay."""
    if len(mesh.verts) >= _DENSE_VERTS or any(a.startswith(("sk:", "wt:")) for a in mesh.attrs):
        return mesh, []
    levels = 0
    while len(mesh.verts) < _DENSE_VERTS and levels < 4:
        mesh = step(mesh) or mesh
        levels += 1
    return mesh, [f"{how} x{levels} ({len(mesh.verts)} verts)"]


def _needs_unwrap(mesh: Mesh) -> bool:
    """UVs missing, outside 0-1, or overlapping (total area over 1): texture paint would smear."""
    area = 0.0
    for fi, face in enumerate(mesh.faces):
        uvs = mesh.uvs[fi] if fi < len(mesh.uvs) else []
        if len(uvs) != len(face):
            return True
        if any(not (-1e-4 <= c <= 1.0001) for uv in uvs for c in uv[:2]):
            return True
        area += abs(sum(uvs[i][0] * uvs[i - 1][1] - uvs[i - 1][0] * uvs[i][1] for i in range(len(uvs)))) / 2
    return area > 1.0001


def _op_sculpt(scene: Scene, p: dict) -> dict[str, Any]:
    from .kernel.sculpt import catmull_clark, displace_noise, smooth_all, stroke, vertex_normals

    node = _object(scene, p)
    mesh = scene._bake(node)
    brush = str(p.get("brush") or "draw")
    shape = str(p.get("shape") or "")
    mesh, warnings = _dense(mesh, lambda m: catmull_clark(m, 1), "smooth-subdivided") if not shape else (mesh, [])
    if shape and f"sk:{shape}" not in mesh.attrs:
        raise ValueError(f"No shape key {shape!r} on {node.get('name')}; forge_shape(action='add') first")
    _, k = _to_local(scene, node, [])
    whole = brush in ("noise", "smooth_all", "inflate_all")
    if whole and shape:  # run on the keyed shape; the key keeps what moved
        base = mesh.verts
        mesh.verts = [[b[a] + d[a] for a in range(3)] for b, d in zip(base, mesh.attrs[f"sk:{shape}"])]
    if brush == "noise":
        n = displace_noise(mesh, float(p.get("strength", 5)) * k, float(p.get("scale", 40)) * k, int(p.get("seed", 0)), int(p.get("octaves", 3)))
    elif brush == "smooth_all":
        n = smooth_all(mesh, int(p.get("iterations", 2)), float(p.get("strength", 0.5)))
    elif brush == "inflate_all":
        amount = float(p.get("strength", 2)) * k
        normals = vertex_normals(mesh)
        mesh.verts = [[v[a] + normals[i][a] * amount for a in range(3)] for i, v in enumerate(mesh.verts)]
        n = len(mesh.verts)
    else:
        points, k = _to_local(scene, node, p.get("points") or [])
        if not points:
            raise ValueError("points: one or more [x, y, z] in world centimetres, near the surface")
        cm_brush = brush in ("draw", "clay", "inflate", "crease")
        n = stroke(mesh, points, brush, float(p.get("radius", 20)) * k,
                   float(p.get("strength", 3 if cm_brush else 0.5)) * (k if cm_brush else 1.0),
                   target=f"sk:{shape}" if shape else "pos", mirror=str(p.get("mirror") or ""),
                   spacing=float(p.get("spacing", 0.3)), invert=bool(p.get("invert")))
    if whole and shape:
        mesh.attrs[f"sk:{shape}"] = [[v[a] - b[a] for a in range(3)] for v, b in zip(mesh.verts, base)]
        mesh.verts = base
    scene.write_mesh(node, mesh)
    return {"summary": f"Sculpt {brush} on {node.get('name')}: {n} vertices", "ids": [node["id"]], "id": node["id"], "verts": n,
            **({"warnings": warnings} if warnings else {})}


def _op_values(scene: Scene, p: dict) -> dict[str, Any]:
    """The dock's finished stroke: exact values for the vertices it touched."""
    from .kernel.sculpt import set_values

    node = _object(scene, p)
    target = str(p.get("target") or "pos")
    width = 3 if target == "pos" or target.startswith("sk:") else 4 if target.startswith("col:") else 1
    if target != "pos" and not target.startswith(("sk:", "wt:", "col:")):
        raise ValueError("target is pos, sk:<key>, wt:<bone>, or col:<name>")
    idx = array.array("I", base64.b64decode(str(p.get("idx") or "")))
    values = array.array("f", base64.b64decode(str(p.get("values") or "")))
    mesh = scene._bake(node)
    if "count" in p and int(p["count"]) != len(mesh.verts):
        raise ValueError(f"{node.get('name')} changed while you painted; the stroke was not applied")
    n = set_values(mesh, target, idx, values, width)
    scene.write_mesh(node, mesh)
    return {"summary": f"Stroke on {node.get('name')}: {n} vertices", "ids": [node["id"]], "id": node["id"]}


def _op_shape(scene: Scene, p: dict) -> dict[str, Any]:
    from .rig import _bone_name

    node = _object(scene, p)
    action = str(p.get("action") or "add")
    mesh = scene._bake(node)
    key = _bone_name(p.get("key") or p.get("new_name") or "Key")
    name = f"sk:{key}"
    if action == "add":
        if name in mesh.attrs:
            raise ValueError(f"Shape key {key!r} already exists")
        mesh.ensure_attr(name)
    elif action == "from_object":
        src = scene.node(str(p.get("source") or ""))
        if src is None or src.get("type") != "object":
            raise ValueError("source must be another object with the same vertices, such as an edited duplicate")
        other = scene.local_mesh(src)
        if len(other.verts) != len(mesh.verts):
            raise ValueError(f"{src.get('name')} has {len(other.verts)} vertices, {node.get('name')} has {len(mesh.verts)}; shape it from a duplicate without adding or removing vertices")
        mesh.attrs[name] = [[b[a] - v[a] for a in range(3)] for v, b in zip(mesh.verts, other.verts)]
    elif action == "delete":
        if mesh.attrs.pop(name, None) is None:
            raise ValueError(f"No shape key {key!r}")
    elif action == "rename":
        new = _bone_name(p.get("new_name"))
        if name not in mesh.attrs:
            raise ValueError(f"No shape key {key!r}")
        mesh.attrs[f"sk:{new}"] = mesh.attrs.pop(name)
        key = new
    else:
        raise ValueError("shape action is add, from_object, delete, or rename")
    scene.write_mesh(node, mesh)
    keys = sorted(n[3:] for n in mesh.attrs if n.startswith("sk:"))
    return {"summary": f"Shape {action} {key}", "ids": [node["id"]], "id": node["id"], "keys": keys}


def _op_mesh_data(scene: Scene, p: dict) -> dict[str, Any]:
    """Everything a brush mode in the dock needs: local positions, triangles, corner UVs, attributes."""
    node = _object(scene, p, edit=False)
    mesh = scene.local_mesh(node)
    tris = mesh.triangles()

    def b64(kind: str, values) -> str:
        return base64.b64encode(array.array(kind, values).tobytes()).decode("ascii")

    corner_uv = []
    for a, b, c, fi in tris:
        corner_uv.extend(mesh.tri_uv(fi, (a, b, c)))
    wanted = [str(n) for n in p.get("attrs") or []]
    attrs = {}
    for name in wanted:
        vals = mesh.attrs.get(name)
        if vals is not None:
            attrs[name] = b64("f", (c for v in vals for c in (v if isinstance(v, list) else [v])))
    return {
        "summary": "mesh data", "ids": [], "id": node["id"], "count": len(mesh.verts), "smooth": mesh.smooth,
        "modifiers": bool(node.get("modifiers")),
        "pb": b64("f", (c for v in mesh.verts for c in v)),
        "ib": b64("I", (i for t in tris for i in t[:3])),
        "fb": b64("I", (t[3] for t in tris)),
        "ub": b64("f", corner_uv),
        "fm": mesh.face_mat,
        "names": sorted(mesh.attrs),
        "attrs": attrs,
    }


def _op_uv_transform(scene: Scene, p: dict) -> dict[str, Any]:
    from .uv import transform_uvs

    node = _object(scene, p)
    mesh = scene._bake(node)
    n = transform_uvs(mesh, p.get("faces") or None, p.get("offset") or [0, 0], float(p.get("rotate", 0)), p.get("scale") or [1, 1])
    scene.write_mesh(node, mesh)
    return {"summary": f"UV transform on {n} faces", "ids": [node["id"]]}


def _op_paint(scene: Scene, p: dict) -> dict[str, Any]:
    from io import BytesIO

    from PIL import Image

    from . import paint

    node = _object(scene, p)
    mode = str(p.get("mode") or "blend")
    warnings: list = []
    if mode == "blend":
        mesh, warnings = _dense(scene._bake(node), lambda m: subdivide(m) and m, "subdivided")
        channel = {"r": 0, "g": 1, "b": 2}.get(str(p.get("channel") or "r").lower())
        if channel is None:
            raise ValueError("channel is r, g, or b (the blend material's layer 1, 2, or 3)")
        params = dict(p)
        if p.get("center"):
            params["center"] = _to_local(scene, node, [p["center"]])[0][0]
        if p.get("radius"):
            params["radius"] = float(p["radius"]) * _to_local(scene, node, [])[1]
        values = paint.mask_values(mesh, str(p.get("mask") or "all"), params)
        paint.apply_mask(mesh, channel, values, str(p.get("combine") or "set"), float(p.get("strength", 1.0)))
        scene.write_mesh(node, mesh)
        return {"summary": f"Blend {p.get('mask') or 'all'} into {'rgb'[channel]} on {node.get('name')}", "ids": [node["id"]],
                **({"warnings": warnings} if warnings else {})}
    if mode != "texture":
        raise ValueError("mode is blend or texture")
    mats = scene.materials()
    mid = str(p.get("material") or node.get("material") or "")
    mat = mats.get(mid) or next((m for m in scene.data["materials"] if m["name"] == mid), None)
    if mat is None:
        raise _no_such("material", mid, [m["name"] for m in scene.data["materials"]])
    size = int(p.get("size") or 1024)
    current = (mat.get("textures") or {}).get("base_color")
    path = scene.folder / "textures" / current if current else None
    if p.get("fill") == "all" or path is None or not path.is_file():
        start = p.get("color") if p.get("fill") == "all" else mat.get("base_color")
        img = paint.new_canvas(size, start or [0.75, 0.75, 0.78])
    else:
        img = Image.open(BytesIO(path.read_bytes())).convert("RGBA")
    base = scene._bake(node)
    if _needs_unwrap(base):
        from .uv import smart_unwrap

        smart_unwrap(base)
        scene.write_mesh(node, base)
        warnings.append(f"smart-unwrapped {node.get('name')} first")
    mesh = scene.local_mesh(node)
    done = []
    if p.get("faces"):
        done.append(f"{paint.fill_faces(img, mesh, p['faces'], p.get('color') or [1, 1, 1])} faces")
    if p.get("points"):
        pts, k = _to_local(scene, node, p["points"])
        done.append(f"{paint.dabs(img, mesh, pts, float(p.get('radius', 10)) * k, p.get('color') or [1, 1, 1], float(p.get('strength', 1)))} px")
    if p.get("grain"):
        paint.grain(img, float(p["grain"]), float(p.get("grain_scale", 8)), int(p.get("seed", 0)))
        done.append("grain")
    _save_texture(scene, mat, img.convert("RGB"), tileable=False, derive=bool(p.get("derive_maps")))
    return {"summary": f"Paint {mat['name']}: {', '.join(done) or 'new texture'}", "ids": [mat["id"]], "material": mat,
            **({"warnings": warnings} if warnings else {})}


_BATCH = {
    "forge_add": "add", "forge_edit": "edit", "forge_duplicate": "duplicate", "forge_delete": "delete",
    "forge_group": "group", "forge_ungroup": "ungroup", "forge_layer": "layer", "forge_join": "join",
    "forge_separate": "separate", "forge_boolean": "boolean", "forge_modifier": "modifier",
    "forge_select": "select", "forge_move_elements": "move", "forge_mesh_op": "mesh_op",
    "forge_material_set": "material_set", "forge_material_assign": "material_assign",
    "forge_uv_project": "uv", "forge_rig": "rig", "forge_anim": "anim", "forge_fix": "fix",
    "forge_sculpt": "sculpt", "forge_paint": "paint", "forge_shape": "shape", "forge_uv_transform": "uv_transform",
    "forge_hq": "hq",
}


def _ref(value: Any, results: list[dict]) -> Any:
    """"$2" is step 2's id; "$2.ids" (or any field) is that field of step 2's result."""
    if isinstance(value, dict):
        return {k: _ref(v, results) for k, v in value.items()}
    if isinstance(value, list):
        out = []
        for item in value:
            got = _ref(item, results)
            if isinstance(got, list) and isinstance(item, str):
                out.extend(got)  # ["$0.ids", "$3"] flattens into one id list
            else:
                out.append(got)
        return out
    if not (isinstance(value, str) and value[:1] == "$" and value[1:2].isdigit()):
        return value
    head, _, field = value[1:].partition(".")
    k = int(head)
    if k >= len(results):
        raise ValueError(f"{value} points at a step that has not run yet")
    got = results[k]
    if field:
        field, _, index = field.partition(".")
        if field not in got:
            raise ValueError(f"{value}: step {k} returned no {field!r}")
        if not index:
            return got[field]
        items = got[field]
        try:
            return items[int(index)]  # "$0.ids.1": one id; negative counts from the end
        except (ValueError, TypeError, IndexError):
            count = len(items) if isinstance(items, list) else 0
            raise ValueError(f"{value}: step {k} has {count} {field} (use .0 to .{count - 1}, or -1 for the last)") from None
    if got.get("id"):
        return got["id"]
    if got.get("ids"):
        return got["ids"][0]
    raise ValueError(f"{value}: step {k} returned no id")


def _op_hq(scene: Scene, p: dict) -> dict[str, Any]:
    """One Blender-engine op (backend.hq); dispatch commits it as one undo step."""
    from .hq import compact, run_hq
    params = dict(p.get("params") or {})
    for key in ("id", "wait", "verbose"):
        if p.get(key):
            params.setdefault(key, p[key])
    op = str(p.get("op") or "help")
    if op == "help":
        return run_hq(scene, op, params)  # help is capped by hq itself; the 300-char result budget would cut it
    verbose = bool(params.pop("verbose", False))
    result = run_hq(scene, op, {**params, "verbose": True})  # full, so node_data survives to be stored
    data = result.pop("node_data", None)
    if isinstance(data, dict):
        # ponytail: a long op queued as a job stores nothing here; run it with wait=True (batches do) to keep its data.
        nid = params.get("id") or next(iter(result.get("ids") or []), "")
        node = scene.node(str(nid))
        if node is None:
            raise ValueError(f"HQ op {op!r} returned node_data but no object id to store it on")
        node["hq"] = {**(node.get("hq") or {}), op: copy.deepcopy(data)}
    return result if verbose or result.get("job") or result.get("state") else compact(result)


def _op_batch(scene: Scene, p: dict) -> dict[str, Any]:
    """Many edits, one undo step. Any failure rolls the whole batch back (dispatch restores the snapshot)."""
    steps = p.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list of {tool, ...args}")
    if len(steps) > 500:
        raise ValueError("Keep one batch under 500 steps")
    results: list[dict] = []
    ids: list = []
    warnings: list = []
    for k, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"step {k} must be an object like {{\"tool\": \"forge_add\", \"kind\": \"box\"}}")
        args = dict(step)
        tool = str(args.pop("tool", "") or "")
        op = _BATCH.get(tool) or (tool if tool in _BATCH.values() else "")
        if not op:
            raise ValueError(f"step {k}: {tool!r} cannot run in a batch. Allowed: {', '.join(sorted(_BATCH))}")
        try:
            args = _ref(args, results)
            if "object_name" in args:
                args["name"] = args.pop("object_name")
            if op == "material_set" and "material" in args:
                args.setdefault("id", args["material"])
                args.setdefault("name", args.pop("material"))
            if op == "group":
                args.setdefault("combine", True)
            if op == "hq":
                args["wait"] = True  # long HQ ops run inline so the batch stays one undo step
            result = _OPS[op](scene, args)
        except Exception as exc:
            raise ValueError(f"step {k} ({tool}) failed: {_hint_error(scene, exc)}. The whole batch was undone.") from None
        results.append(result)
        ids.extend(result.get("ids") or [])
        warnings.extend(f"step {k}: {w}" for w in result.get("warnings") or [])
    brief = [{key: r[key] for key in ("summary", "id", "clip", "bones") if r.get(key)} for r in results]
    out = {"summary": f"Batch of {len(steps)}; last: {results[-1].get('summary') or steps[-1].get('tool')}",
           "ids": list(dict.fromkeys(i for i in ids if i)), "steps": brief}
    if warnings:
        out["warnings"] = warnings
    return out


def _op_merge(scene: Scene, p: dict, source_scene=None) -> dict[str, Any]:
    """Copy another scene's objects, groups, meshes, materials, textures, and rigs into this one."""
    import shutil

    source = str(p.get("source") or "")
    if source_scene is None and (not source or _safe(source) == _safe(scene.name)):
        raise ValueError("source must be another scene")
    src = source_scene if source_scene is not None else load(source)
    data = copy.deepcopy(src.data)
    offset = [float(v) for v in (p.get("offset") or [0, 0, 0])][:3]
    if len(offset) != 3 or not all(math.isfinite(v) for v in offset):
        raise ValueError("offset needs three numbers")
    mat_map: dict[str, str] = {}
    tex_dir = scene.folder / "textures"
    for mat in data.get("materials") or []:
        mat_map[mat["id"]] = mat["id"] = _nid("m")
        for slot, fname in list((mat.get("textures") or {}).items()):
            src_file = src.folder / "textures" / Path(fname).name
            if not src_file.is_file():
                continue
            tex_dir.mkdir(parents=True, exist_ok=True)
            dest, n = tex_dir / src_file.name, 2
            while dest.exists():
                dest, n = tex_dir / f"{src_file.stem}_{n}{src_file.suffix}", n + 1
            shutil.copyfile(src_file, dest)
            mat["textures"][slot] = dest.name
    proto_map: dict[str, str] = {}
    for proto in data.get("prototypes") or []:
        proto_map[proto["id"]] = proto["id"] = _nid("p")
        if proto.get("mesh"):
            proto["mesh"] = dict(proto["mesh"], face_mat=[mat_map.get(m, m) for m in proto["mesh"].get("face_mat") or []])
    layers = scene.data.setdefault("layers", [])
    layer_map = {l["id"]: l["id"] for l in data.get("layers") or [] if any(o["id"] == l["id"] for o in layers)}
    for layer in data.get("layers") or []:
        if layer["id"] not in layer_map:
            layer_map[layer["id"]] = layer["id"] = _nid("l")
            layers.append(layer)
    node_map: dict[str, dict] = {}
    for node in data["nodes"]:
        node_map[node["id"]] = node
        node["id"] = _nid("o" if node.get("type") == "object" else "g")
    roots = []
    for node in data["nodes"]:
        parent = node_map.get(node.get("parent"))
        node["parent"] = parent["id"] if parent else None
        if node.get("prototype"):
            node["prototype"] = proto_map.get(node["prototype"], node["prototype"])
        if node.get("material"):
            node["material"] = mat_map.get(node["material"], node["material"])
        node["layer"] = layer_map.get(node.get("layer"), node.get("layer") or "default")
        if parent is None:
            t = node.setdefault("transform", {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]})
            t["pos"] = [t["pos"][i] + offset[i] for i in range(3)]
            roots.append(node["id"])
    scene.data["materials"].extend(data.get("materials") or [])
    scene.data.setdefault("prototypes", []).extend(data.get("prototypes") or [])
    scene.data["nodes"].extend(data["nodes"])
    scene.data.setdefault("rigs", []).extend(merge_rigs(data.get("rigs"), node_map, offset))
    scene.touch_geom()
    return {"summary": f"Merge {src.name}: {len(data['nodes'])} entries", "ids": roots}


_OPS = {
    "rig": op_rig,
    "anim": op_anim,
    "rig_preview": op_preview,
    "batch": _op_batch,
    "hq": _op_hq,
    "merge_scene": _op_merge,
    "sculpt": _op_sculpt,
    "values": _op_values,
    "shape": _op_shape,
    "mesh_data": _op_mesh_data,
    "uv_transform": _op_uv_transform,
    "paint": _op_paint,
    "scene_get": _op_get,
    "scene_payload": _op_payload,
    "scene_geom_part": _op_geom_part,
    "stats": _op_stats,
    "add": _op_add,
    "edit": _op_edit,
    "duplicate": _op_duplicate,
    "delete": _op_delete,
    "group": _op_group,
    "ungroup": _op_ungroup,
    "layer": _op_layer,
    "select": _op_select,
    "move": _op_move,
    "mesh_op": _op_mesh,
    "boolean": _op_bool,
    "modifier": _op_modifier,
    "join": _op_join,
    "separate": _op_separate,
    "material_set": _op_material_set,
    "material_assign": _op_material_assign,
    "uv": _op_uv,
    "undo": _op_undo,
    "redo": _op_redo,
    "revert": _op_revert,
    "check": _op_check,
    "fix": _op_fix,
    "render": _op_render,
    "look": _op_look,
    "texture": _op_texture,
    "scene_save": _op_save_file,
    "scene_open": _op_open_file,
    "scene_files": _op_list_files,
    "scene_import_blend": _op_import_blend,
    "import_mesh": _op_import_mesh,
    "camera": op_camera,
    "shot": op_shot,
    "cameras": op_cameras,
}
