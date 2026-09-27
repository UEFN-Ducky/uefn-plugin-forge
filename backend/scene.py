"""The scene both the panel and the agent edit. Stored under AppData, never in the island."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from . import history
from .kernel.csg import boolean as csg_boolean
from .kernel.mesh import Mesh, build_primitive
from .check import check_mesh, fix_mesh
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

_LOCK = threading.Lock()
_READ = {"stats", "scene_get", "scene_list", "scene_payload"}
_LOOK = {"check", "render"}


def scenes_root() -> Path:
    env = os.environ.get("FORGE_SCENES_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "UEFN-Ducky" / "forge" / "scenes"


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


def _indexed(mesh: Mesh, pid: str) -> dict[str, Any]:
    """Shared vertices. Flat faces keep a vertex per corner so edges stay sharp."""
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []
    face_ids: list[int] = []
    corner: dict[tuple, int] = {}

    def add(vi: int, nrm: list[float], uv: list[float]) -> int:
        key = (vi, round(nrm[0], 3), round(nrm[1], 3), round(nrm[2], 3))
        hit = corner.get(key)
        if hit is not None:
            return hit
        idx = len(positions) // 3
        positions.extend(mesh.verts[vi])
        normals.extend(nrm)
        uvs.extend(uv)
        corner[key] = idx
        return idx

    for a, b, c, fi in mesh.triangles():
        nrm = mesh.face_normal(fi)
        face = mesh.faces[fi]
        uvf = mesh.uvs[fi] if fi < len(mesh.uvs) else []

        def uv_of(vi: int) -> list[float]:
            try:
                return uvf[face.index(vi)]
            except ValueError:
                return [0.0, 0.0]

        indices.extend((add(a, nrm, uv_of(a)), add(b, nrm, uv_of(b)), add(c, nrm, uv_of(c))))
        face_ids.append(fi)
    return {
        "id": pid, "positions": positions, "normals": normals, "uvs": uvs,
        "indices": indices, "face_ids": face_ids, "tris": len(face_ids),
    }


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

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.folder.name)

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "scene.json.tmp"
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        tmp.replace(self.folder / "scene.json")

    def _hist(self) -> Path:
        return self.folder / "history.json"

    def _snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data))

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
        for n in self.data["nodes"]:
            if n["id"] == nid:
                return n
        return None

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
        if mat and not (proto and proto.get("mesh")):
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
        mesh = self.local_mesh(node)
        node["modifiers"] = []
        self.write_mesh(node, mesh)
        return mesh

    def _active(self) -> dict[str, Any]:
        node = self.node(self.data["selection"].get("object"))
        if node is None or node.get("type") != "object":
            raise ValueError("Select an object first")
        if node.get("locked"):
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
            mesh = self._mesh_of(proto)
            meshes[pid] = mesh
            lo, hi = mesh.bounds()
            tris_of[pid] = mesh.tri_count()
            bounds_of[pid] = (lo, hi)
            if not proto.get("mesh"):
                prim_of[pid] = {"kind": proto.get("kind"), "params": proto.get("params") or {}}
        return prim_of, tris_of, bounds_of, meshes

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
                "prototype": pid,
            })
            add_n({
                "id": n["id"], "type": "object", "name": n.get("name"),
                "parent": n.get("parent"), "layer": n.get("layer"),
                "visible": n.get("visible", True), "locked": bool(n.get("locked")),
                "transform": n.get("transform"), "prototype": pid,
                "primitive": prim_of.get(pid), "modifiers": mods or [],
                "material": n.get("material"), "tris": tris,
            })
        return nodes, objects

    def payload(self) -> dict[str, Any]:
        """One indexed mesh per prototype, plus a flat instance transform list."""
        prim_of, tris_of, _bounds, meshes = self._catalog()
        geos: dict[str, dict[str, Any]] = {}
        instances: list[list] = []
        nodes: list[dict] = []
        add_i = instances.append
        add_n = nodes.append
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
            add_n({
                "id": n["id"], "type": "object", "name": n.get("name"),
                "parent": n.get("parent"), "layer": n.get("layer"),
                "visible": n.get("visible", True), "locked": bool(n.get("locked")),
                "transform": n.get("transform"), "prototype": pid,
                "primitive": prim_of.get(pid), "modifiers": mods or [],
                "material": n.get("material"), "tris": tris,
            })
            if not n.get("visible", True):
                continue
            if mirrored:
                key = self.display_key(n)
                if key not in geos:
                    geos[key] = _indexed(self.shaped_mesh(n), key)
                places = _placements(n)
            elif mods:
                key = pid
                if key not in geos:
                    geos[key] = _indexed(meshes[key] if key in meshes else self.base_mesh(n), key)
                places = _placements(n)
            else:
                key = pid
                if key not in geos:
                    geos[key] = _indexed(meshes[key] if key in meshes else self.base_mesh(n), key)
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
        info = self._header(nodes, [])
        info["prototypes"] = list(geos.values())
        info["instances"] = instances
        last = (self.data.get("activity") or [None])[-1]
        info["flash"] = None
        if last and last.get("kind") == "edit" and last.get("actor") == "agent":
            info["flash"] = {"ids": last.get("ids") or [], "op": last.get("op"), "summary": last.get("summary")}
        return info


def load(name: str) -> Scene:
    folder = scenes_root() / _safe(name)
    path = folder / "scene.json"
    if not path.is_file():
        raise ValueError(f"No Forge scene named {name!r}")
    return Scene(json.loads(path.read_text(encoding="utf-8")), folder)


def create(name: str) -> Scene:
    folder = scenes_root() / _safe(name)
    if (folder / "scene.json").is_file():
        raise ValueError(f"Scene {name!r} already exists")
    scene = Scene(_blank(_safe(name)), folder)
    scene.save()
    return scene


def list_scenes() -> list[dict[str, Any]]:
    root = scenes_root()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if (p / "scene.json").is_file()):
        try:
            data = json.loads((folder / "scene.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"name": data.get("name") or folder.name, "rev": data.get("rev") or 0})
    return out


def dispatch(name: str, op: str, params: dict | None = None, actor: str = "agent") -> dict[str, Any]:
    params = dict(params or {})
    op = (op or "").strip()
    with _LOCK:
        if op == "scene_list":
            return {"scenes": list_scenes()}
        if op == "scene_new":
            scene = create(str(params.get("name") or "Scene"))
            return scene.summary()
        scene = load(name)
        fn = _OPS.get(op)
        if fn is None:
            raise ValueError(f"Unknown forge op {op!r}")
        editing = op not in _READ and op not in {"undo", "redo", "revert"}
        before = scene._snapshot() if editing and op not in _LOOK else None
        result = fn(scene, params)
        if editing:
            ids = list(result.get("ids") or [])
            scene.commit(
                actor, op, str(result.get("summary") or op), ids,
                kind="look" if op in _LOOK else "edit", snapshot=before,
            )
        result["rev"] = scene.data.get("rev")
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
    mat = str(p.get("material") or scene.data["materials"][0]["id"])
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
                "pos": [origin[0] + (i % stride) * 120.0, origin[1] + (i // stride) * 120.0, origin[2]],
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


def _op_edit(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene.node(str(p.get("id") or scene.data["selection"].get("object")))
    if node is None:
        raise ValueError("object not found")
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
    off = list(p.get("offset") or [20, 0, 0])
    copy["transform"]["pos"] = [pos[i] + off[i] for i in range(3)]
    scene.data["nodes"].append(copy)
    scene.data["selection"]["object"] = copy["id"]
    scene.touch_geom()
    return {"summary": f"Duplicate {node.get('name')}", "ids": [copy["id"]]}


def _op_delete(scene: Scene, p: dict) -> dict[str, Any]:
    ids = set(p.get("ids") or [scene.data["selection"].get("object")])
    ids.discard(None)
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] not in ids and n.get("parent") not in ids]
    if scene.data["selection"].get("object") in ids:
        scene.data["selection"]["object"] = None
    scene._gc_protos()
    scene.touch_geom()
    return {"summary": f"Delete {len(ids)}", "ids": list(ids)}


def _op_group(scene: Scene, p: dict) -> dict[str, Any]:
    ids = [str(i) for i in (p.get("ids") or []) if i]
    into = p.get("into")
    if into:
        for nid in ids:
            node = scene.node(nid)
            if node and node["id"] != into:
                node["parent"] = into
        scene.touch_geom()
        return {"summary": "Nest", "ids": ids}
    gid = _nid("g")
    scene.data["nodes"].append({
        "id": gid, "type": "group", "name": str(p.get("name") or "Group"),
        "parent": p.get("parent"), "layer": "default", "visible": True, "locked": False,
        "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
        "combine": bool(p.get("combine", True)),
    })
    for nid in ids:
        node = scene.node(nid)
        if node and node["id"] != gid:
            node["parent"] = gid
    scene.data["selection"]["object"] = gid
    scene.touch_geom()
    return {"summary": "Group", "ids": [gid], "id": gid}


def _op_ungroup(scene: Scene, p: dict) -> dict[str, Any]:
    gid = str(p.get("id") or scene.data["selection"].get("object") or "")
    group = scene.node(gid)
    if not group or group.get("type") != "group":
        raise ValueError("Select a group to ungroup")
    parent = group.get("parent")
    for node in scene.data["nodes"]:
        if node.get("parent") == gid:
            node["parent"] = parent
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
    if "visible" in p:
        scene.touch_geom()
    return {"summary": f"Layer {layer.get('name')}", "ids": [lid]}


def _op_select(scene: Scene, p: dict) -> dict[str, Any]:
    sel = scene.data["selection"]
    if p.get("object"):
        sel["object"] = p["object"]
    if "mode" in p:
        scene.data["mode"] = p["mode"]
    if "select_mode" in p:
        scene.data["select_mode"] = p["select_mode"]
    for key in ("verts", "edges", "faces"):
        if key in p:
            sel[key] = p[key]
    query = str(p.get("query") or "")
    node = scene.node(sel.get("object"))
    if query and node and node.get("type") == "object":
        mesh = scene.base_mesh(node)
        if query == "top":
            sel["faces"] = [fi for fi in range(len(mesh.faces)) if mesh.face_normal(fi)[2] > 0.7]
            scene.data["select_mode"] = "face"
        elif query == "all":
            sel["faces"] = list(range(len(mesh.faces)))
    return {"summary": "Select", "ids": [sel.get("object")], "selection": sel}


def _op_move(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    mesh = scene._bake(node)
    kind = str(p.get("kind") or scene.data.get("select_mode") or "vert")
    n = move_elements(mesh, kind, _sel_ids(scene, p), p.get("delta") or [0, 0, 0])
    scene.write_mesh(node, mesh)
    return {"summary": f"Move {n}", "ids": [node["id"]]}


def _op_mesh(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    mesh = scene._bake(node)
    which = str(p.get("op") or "")
    sel = scene.data["selection"]
    faces = list(p.get("faces") or sel.get("faces") or [])
    if which == "extrude":
        extrude_faces(mesh, faces, float(p.get("distance", 10)))
    elif which == "inset":
        inset_faces(mesh, faces, float(p.get("amount", 5)))
    elif which == "bevel":
        edges = p.get("edges") or sel.get("edges") or mesh.edge_keys()
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
        t = node["transform"]
        t["pos"] = [t["pos"][i] + shift[i] for i in range(3)]
    else:
        raise ValueError(f"Unknown mesh op {which!r}")
    scene.write_mesh(node, mesh)
    return {"summary": which or "mesh", "ids": [node["id"]], "tris": mesh.tri_count()}


def _op_bool(scene: Scene, p: dict) -> dict[str, Any]:
    a = scene.node(str(p.get("a")))
    b = scene.node(str(p.get("b")))
    if not a or not b:
        raise ValueError("boolean needs a and b object ids")
    result = csg_boolean(scene.world_mesh(a), scene.world_mesh(b), str(p.get("op") or "difference"))
    node = {
        "id": _nid("o"), "type": "object", "name": f"{a.get('name')}_{p.get('op') or 'cut'}",
        "parent": a.get("parent"), "layer": a.get("layer") or "default",
        "visible": True, "locked": False,
        "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]},
        "prototype": scene.adopt_mesh(result), "modifiers": [],
        "material": a.get("material"),
    }
    drop = {a["id"], b["id"]}
    scene.data["nodes"] = [n for n in scene.data["nodes"] if n["id"] not in drop]
    scene.data["nodes"].append(node)
    scene.data["selection"]["object"] = node["id"]
    scene._gc_protos()
    scene.touch_geom()
    return {"summary": f"Boolean {p.get('op') or 'difference'}", "ids": [node["id"]]}


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
    ids = [str(i) for i in (p.get("ids") or [])]
    nodes = [scene.node(i) for i in ids]
    nodes = [n for n in nodes if n and n.get("type") == "object"]
    if len(nodes) < 2:
        raise ValueError("join needs two or more objects")
    mesh = Mesh()
    for n in nodes:
        mesh.append(scene.world_mesh(n))
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
    return {"summary": f"Join {len(nodes)}", "ids": [node["id"]]}


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
    mid = str(p.get("id") or "")
    found = None
    for m in scene.data["materials"]:
        if m["id"] == mid or m["name"] == p.get("name"):
            found = m
            break
    if found is None:
        found = _mat(_nid("m"), str(p.get("name") or "Material"), p.get("base_color"))
        scene.data["materials"].append(found)
    for key in ("name", "base_color", "metallic", "roughness", "emissive", "emissive_strength", "opacity"):
        if key in p:
            found[key] = p[key]
    if p.get("textures"):
        found["textures"] = dict(p["textures"])
    return {"summary": f"Material {found['name']}", "ids": [found["id"]], "material": found}


def _op_material_assign(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    mid = str(p.get("material") or "")
    if not any(m["id"] == mid for m in scene.data["materials"]):
        raise ValueError("material not in this scene")
    faces = p.get("faces")
    if not faces:
        node["material"] = mid
        scene.touch_geom()
    else:
        mesh = scene._bake(node)
        for fi in faces:
            if 0 <= int(fi) < len(mesh.face_mat):
                mesh.face_mat[int(fi)] = mid
        scene.write_mesh(node, mesh)
    return {"summary": "Assign material", "ids": [node["id"]]}


def _op_uv(scene: Scene, p: dict) -> dict[str, Any]:
    node = scene._active()
    mesh = scene._bake(node)
    n = project(mesh, str(p.get("mode") or "box"), p.get("faces"), float(p.get("density", 0.01)))
    scene.write_mesh(node, mesh)
    return {"summary": f"UV {p.get('mode') or 'box'} on {n} faces", "ids": [node["id"]]}


def _op_undo(scene: Scene, _p: dict) -> dict[str, Any]:
    prev = history.undo(scene._hist(), scene._snapshot())
    if prev is None:
        return {"summary": "Nothing to undo", "ids": []}
    scene.data = prev
    scene.save()
    return {"summary": "Undo", "ids": []}


def _op_redo(scene: Scene, _p: dict) -> dict[str, Any]:
    nxt = history.redo(scene._hist(), scene._snapshot())
    if nxt is None:
        return {"summary": "Nothing to redo", "ids": []}
    scene.data = nxt
    scene.save()
    return {"summary": "Redo", "ids": []}


def _op_revert(scene: Scene, p: dict) -> dict[str, Any]:
    target = history.revert_to(scene._hist(), scene._snapshot(), int(p.get("rev") or 0))
    if target is None:
        return {"summary": "No snapshot at that rev", "ids": []}
    scene.data = target
    scene.save()
    return {"summary": f"Revert to rev {p.get('rev')}", "ids": []}


def _op_get(scene: Scene, p: dict) -> dict[str, Any]:
    if p.get("brief"):
        data = scene.data
        return {
            "summary": "scene", "ids": [], "name": scene.name,
            "rev": data.get("rev"), "geom": data.get("geom") or 0,
            "mode": data.get("mode"), "select_mode": data.get("select_mode"),
            "selection": data.get("selection"),
        }
    info = scene.summary()
    info["summary"] = "scene"
    info["ids"] = []
    return info


def _op_payload(scene: Scene, _p: dict) -> dict[str, Any]:
    info = scene.payload()
    info["summary"] = "payload"
    info["ids"] = []
    return info


def _op_stats(scene: Scene, _p: dict) -> dict[str, Any]:
    info = scene.summary()
    return {"summary": "stats", "ids": [], "objects": info["objects"], "rev": info["rev"]}


def _captures() -> Path:
    env = os.environ.get("FORGE_CAPTURES_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "UEFN-Ducky" / "tool_captures"


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


def _op_render(scene: Scene, p: dict) -> dict[str, Any]:
    from .render import render_png

    views = list(p.get("views") or ["three"])
    paths = []
    folder = _captures()
    for n in scene.data["nodes"]:
        if n.get("type") != "object" or not n.get("visible", True):
            continue
        if p.get("id") and n["id"] != p["id"]:
            continue
        dest = folder / f"forge_{scene.name}_{n['id']}.png"
        paths.extend(render_png(scene.world_mesh(n), dest, views))
    return {"summary": f"Render {len(paths)}", "ids": [], "paths": paths, "kind": "look"}


def _op_texture(scene: Scene, p: dict) -> dict[str, Any]:
    import base64
    from io import BytesIO

    from PIL import Image

    from .textures import generate_image, save_set

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
    saved = save_set(
        scene.folder / "textures",
        mat["name"].replace(" ", "_"),
        color,
        tileable=bool(p.get("tileable", True)),
        derive=bool(p.get("derive_maps", True)),
        metallic=float(mat.get("metallic") or 0),
    )
    # store filenames relative to the scene so the panel can ask for bytes
    rel = {slot: Path(path).name for slot, path in saved.items()}
    mat["textures"] = rel
    return {"summary": f"Texture {mat['name']}", "ids": [mat["id"]], "textures": rel, "material": mat}


_OPS = {
    "scene_get": _op_get,
    "scene_payload": _op_payload,
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
    "texture": _op_texture,
}
