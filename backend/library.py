"""Project library: one shared copy of each material, texture, part and rig that every scene of the project can link.

projects/<P>/library/ materials.json, textures/, parts/<id>/{mesh.fmesh,meta.json}, rigs/<id>.json, thumbs/, index.json

A linked scene entry is tagged `lib: <id>` (node, prototype, material or rig). Re-adding an id updates the library and
refreshes every linked scene, like Blender linked data; make_local drops the tag and leaves an editable copy.
The library holds the one authored copy; a scene keeps a tagged derived copy so the dock and renderer need no hook.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any

from . import projects, scene as S, storage
from .kernel.mesh import Mesh, build_primitive

KINDS = ("material", "texture", "part", "rig")
ACTIONS = ("list", "add", "link", "make_local", "remove", "thumb")
_SHOWN = 30


def _project(*names: str, project: str = "") -> str:
    if project:
        return projects.require(project)
    for name in names:
        head, sep, _ = (name or "").rpartition("/")
        if sep:
            return projects.require(head)
    return projects.active()


def _lib(project: str) -> Path:
    projects.ensure()
    folder = projects.projects_root() / project / "library"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _index(lib: Path) -> dict:
    idx = _read(lib / "index.json", {})
    idx.setdefault("items", {})
    idx.setdefault("links", {})
    return idx


def _save_index(lib: Path, idx: dict) -> None:
    projects._write_json(lib / "index.json", idx)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:32]


def _copy(src: Path, dst: Path) -> None:
    if src.is_file() and (not dst.is_file() or dst.stat().st_size != src.stat().st_size):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _used(idx: dict, iid: str) -> int:
    return sum(len(r["refs"]) for r in idx["links"].get(iid, {}).values())


def _item(idx: dict, iid: str) -> dict:
    item = idx["items"].get(iid)
    if item is None:
        raise S._no_such("library item", iid, idx["items"])
    return item


# ---- library side ----------------------------------------------------------------


def _master(mat: dict, src_tex: Path, lib: Path, prefix: str) -> dict:
    """A material without scene ids; its texture files are copied into the library as <prefix>__<file>."""
    out = {k: copy.deepcopy(v) for k, v in mat.items() if k not in ("id", "lib", "textures", "layers")}
    out["textures"] = {}
    for slot, fname in (mat.get("textures") or {}).items():
        name = "%s__%s" % (prefix, Path(fname).name)
        if not (src_tex / Path(fname).name).is_file():
            continue
        _copy(src_tex / Path(fname).name, lib / "textures" / name)
        out["textures"][slot] = name
    return out


def _sweep(lib: Path, iid: str, keep: set[str]) -> None:
    for f in (lib / "textures").glob(iid + "__*") if (lib / "textures").is_dir() else []:
        if f.name not in keep:
            f.unlink(missing_ok=True)


def _save_mesh(folder: Path, data: dict) -> None:
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    blob = storage.encode(data)
    if blob is not None:
        (folder / "mesh.fmesh").write_bytes(blob)
    else:
        (folder / "mesh.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _load_mesh(folder: Path) -> dict:
    fm = folder / "mesh.fmesh"
    return storage.decode(fm.read_bytes()) if fm.is_file() else json.loads((folder / "mesh.json").read_text(encoding="utf-8"))


def _pick(items: list, ref: str, what: str) -> dict:
    found = next((i for i in items if ref and ref in (i.get("id"), i.get("name"))), None)
    if found is None and not ref and items:
        found = items[0]
    if found is None:
        raise S._no_such(what, ref or "(none)", [i.get("name") for i in items])
    return found


def _add(lib: Path, idx: dict, kind: str, iid: str, name: str, from_scene: str, ref: str, path: str, slot: str) -> str:
    keep: set[str] = set()
    if kind == "texture":
        src = Path(path or "")
        if not src.is_file():
            raise ValueError("kind=texture needs path=<an image file>")
        fname = "%s__%s" % (iid, src.name)
        _copy(src, lib / "textures" / fname)
        keep.add(fname)
        idx["items"][iid]["file"] = fname
    else:
        if not from_scene:
            raise ValueError("Pass from_scene=<scene> to copy the %s from" % kind)
        src_scene = S.load(from_scene)
        if kind == "material":
            mat = _pick(src_scene.data["materials"], ref, "material")
            master = _master(mat, src_scene.folder / "textures", lib, iid)
            keep.update(master["textures"].values())
            mats = _read(lib / "materials.json", {})
            mats[iid] = master
            projects._write_json(lib / "materials.json", mats)
        elif kind == "part":
            node = S._object(src_scene, {"id": ref}, edit=False)
            data = src_scene.local_mesh(node).to_dict()
            fallback = node.get("material") or src_scene.data["materials"][0]["id"]
            data["face_mat"] = [m if m not in ("", "default") else fallback for m in data["face_mat"]]
            by_id = src_scene.materials()
            masters = {}
            for mid in dict.fromkeys(data["face_mat"]):
                if mid in by_id:
                    masters[mid] = _master(by_id[mid], src_scene.folder / "textures", lib, iid)
                    keep.update(masters[mid]["textures"].values())
            _save_mesh(lib / "parts" / iid, data)
            projects._write_json(lib / "parts" / iid / "meta.json", {"name": name, "materials": masters})
        else:
            rig = _pick(src_scene.data.get("rigs") or [], ref, "rig")
            projects._write_json(lib / "rigs" / (iid + ".json"), {k: v for k, v in rig.items() if k not in ("id", "lib")})
    _sweep(lib, iid, keep)
    return "%s %s" % (kind, iid)


# ---- scene side ------------------------------------------------------------------


def _sync_material(sc: S.Scene, lib: Path, tag: str, master: dict) -> dict:
    mats = sc.data["materials"]
    mat = next((m for m in mats if m.get("lib") == tag), None)
    if mat is None:
        mat = S._mat(S._nid("m"), master.get("name") or tag)
        mats.append(mat)
    for key, value in master.items():
        if key != "textures":
            mat[key] = copy.deepcopy(value)
    mat["lib"] = tag
    mat["textures"] = {}
    for slot, fname in (master.get("textures") or {}).items():
        _copy(lib / "textures" / fname, sc.folder / "textures" / fname)
        mat["textures"][slot] = fname
    return mat


def _sync_part(sc: S.Scene, lib: Path, iid: str) -> dict:
    meta = _read(lib / "parts" / iid / "meta.json", {})
    data = _load_mesh(lib / "parts" / iid)
    idmap = {mid: _sync_material(sc, lib, "%s:%s" % (iid, mid), master)["id"] for mid, master in (meta.get("materials") or {}).items()}
    data["face_mat"] = [idmap.get(m, m) for m in data["face_mat"]]
    protos = sc.data.setdefault("prototypes", [])
    proto = next((p for p in protos if p.get("lib") == iid), None)
    if proto is None:
        proto = {"id": S._nid("p"), "kind": "mesh", "params": {}, "key": "", "lib": iid}
        protos.append(proto)
    proto["mesh"] = data
    return {"proto": proto, "mat": next(iter(idmap.values()), sc.data["materials"][0]["id"]), "name": meta.get("name") or iid}


def _sync_rig(sc: S.Scene, lib: Path, iid: str) -> dict:
    rig = _read(lib / "rigs" / (iid + ".json"), {})
    rigs = sc.data.setdefault("rigs", [])
    have = next((r for r in rigs if r.get("lib") == iid), None)
    new = copy.deepcopy(rig)
    new["id"] = have["id"] if have else S._nid("r")
    new["lib"] = iid
    for clip in new.get("actions") or []:
        clip["id"] = S._nid("a")
    rigs[:] = [new if r is have else r for r in rigs] if have else rigs + [new]
    return new


def _refs_alive(sc: S.Scene, kind: str, refs: list) -> list:
    if kind == "part":
        return [r for r in refs if (sc.node(r) or {}).get("lib")]
    if kind == "rig":
        return [r for r in refs if any(x["id"] == r and x.get("lib") for x in sc.data.get("rigs") or [])]
    return [r for r in refs if any(m["id"] == r and m.get("lib") for m in sc.data["materials"])]


def _record(idx: dict, iid: str, sc: S.Scene, refs: list) -> None:
    rec = idx["links"].setdefault(iid, {}).setdefault(str(sc.folder), {"name": sc.name, "refs": []})
    rec["refs"] = list(dict.fromkeys(rec["refs"] + refs))


def _link(lib: Path, idx: dict, iid: str, scene_name: str, ref: str, pos) -> str:
    if not scene_name:
        raise ValueError("Pass scene=<the scene to link %s into>" % iid)
    kind = _item(idx, iid)["kind"]
    sc = S.load(scene_name)
    before = sc._snapshot()
    ids: list = []
    if kind == "part":
        built = _sync_part(sc, lib, iid)
        node = {"id": S._nid("o"), "type": "object", "name": built["name"], "parent": None, "layer": "default",
                "visible": True, "locked": False, "lib": iid,
                "transform": {"pos": list(pos or [0, 0, 0]), "rot": [0, 0, 0], "scale": [1, 1, 1]},
                "prototype": built["proto"]["id"], "modifiers": [], "material": built["mat"]}
        sc.data["nodes"].append(node)
        sc.data["selection"]["object"] = node["id"]
        ids = [node["id"]]
    elif kind == "rig":
        ids = [_sync_rig(sc, lib, iid)["id"]]
    else:
        if kind == "texture":
            master = {"name": iid, "base_color": [1, 1, 1, 1], "textures": {"base_color": idx["items"][iid]["file"]}}
        else:
            master = _read(lib / "materials.json", {})[iid]
        mat = _sync_material(sc, lib, iid, master)
        ids = [mat["id"]]
        target = sc.node(ref) if ref else None
        if target is not None:
            target["material"], target["material_override"] = mat["id"], True
    sc.touch_geom()
    sc.commit("agent", "library", "Link %s" % iid, ids, snapshot=before)
    _record(idx, iid, sc, ids)
    return "Linked %s %s into %s (%s)" % (kind, iid, sc.name, ids[0])


def _refresh(lib: Path, idx: dict, iid: str) -> int:
    """Push the library copy of iid to every scene that links it; scenes or entries that vanished are dropped."""
    kind, done = idx["items"][iid]["kind"], 0
    for key, rec in list(idx["links"].get(iid, {}).items()):
        try:
            sc = S.load(rec["name"])
        except (ValueError, OSError):
            del idx["links"][iid][key]
            continue
        refs = _refs_alive(sc, kind, rec["refs"])
        if not refs:
            del idx["links"][iid][key]
            continue
        before = sc._snapshot()
        if kind == "part":
            built = _sync_part(sc, lib, iid)
            for r in refs:
                sc.node(r)["prototype"] = built["proto"]["id"]
        elif kind == "rig":
            _sync_rig(sc, lib, iid)
        else:
            master = {"name": iid, "base_color": [1, 1, 1, 1], "textures": {"base_color": idx["items"][iid].get("file", "")}} \
                if kind == "texture" else _read(lib / "materials.json", {})[iid]
            _sync_material(sc, lib, iid, master)
        sc.touch_geom()
        sc.commit("agent", "library", "Update linked %s" % iid, refs, snapshot=before)
        rec["refs"] = refs
        done += 1
    return done


def _detach(sc: S.Scene, ref: str) -> tuple[str, str]:
    """Turn one linked node, material or rig of sc into an editable local copy. Returns (library id, kind)."""
    node = sc.node(ref) or next((n for n in sc.data["nodes"] if n.get("name") == ref and n.get("lib")), None)
    if node is not None and node.get("lib"):
        iid = node.pop("lib")
        proto = sc.proto(node)
        data = dict(proto["mesh"]) if proto and proto.get("mesh") else None
        if data is not None:
            mats, clone = sc.materials(), {}
            for mid in dict.fromkeys(data["face_mat"]):
                if mats.get(mid, {}).get("lib"):
                    local = {k: copy.deepcopy(v) for k, v in mats[mid].items() if k != "lib"}
                    local["id"] = clone[mid] = S._nid("m")
                    sc.data["materials"].append(local)
            data["face_mat"] = [clone.get(m, m) for m in data["face_mat"]]
            node["prototype"] = S._nid("p")
            sc.data["prototypes"].append({"id": node["prototype"], "kind": "mesh", "params": {}, "key": "", "mesh": data})
            node["material"] = clone.get(node.get("material"), node.get("material"))
        sc._gc_protos()
        return iid, "part"
    for m in sc.data["materials"]:
        if ref in (m["id"], m["name"]) and m.get("lib"):
            return str(m.pop("lib")).split(":")[0], "material"
    for r in sc.data.get("rigs") or []:
        if ref in (r["id"], r.get("name")) and r.get("lib"):
            return str(r.pop("lib")), "rig"
    linked = [n["name"] for n in sc.data["nodes"] if n.get("lib")] + [m["name"] for m in sc.data["materials"] if m.get("lib")]
    raise S._no_such("linked entry", ref, linked)


def _make_local(lib: Path, idx: dict, scene_name: str, ref: str) -> str:
    if not scene_name or not ref:
        raise ValueError("Pass scene=<scene> and object=<linked object, material or rig id>")
    sc = S.load(scene_name)
    before = sc._snapshot()
    iid, kind = _detach(sc, ref)
    sc.touch_geom()
    sc.commit("agent", "library", "Make %s local" % iid, [ref], snapshot=before)
    rec = idx["links"].get(iid, {}).get(str(sc.folder))
    if rec:
        rec["refs"] = _refs_alive(sc, kind, rec["refs"])
        if not rec["refs"]:
            del idx["links"][iid][str(sc.folder)]
    return "%s is now a local copy in %s; it no longer follows the library" % (ref, sc.name)


def _remove(lib: Path, idx: dict, iid: str, force: bool) -> str:
    item = _item(idx, iid)
    used = _used(idx, iid)
    if used and not force:
        raise ValueError("%s is linked %d times. make_local each one first, or pass force=true to detach them all" % (iid, used))
    for key, rec in list(idx["links"].get(iid, {}).items()):
        try:
            sc = S.load(rec["name"])
        except (ValueError, OSError):
            continue
        before = sc._snapshot()
        for r in _refs_alive(sc, item["kind"], rec["refs"]):
            _detach(sc, r)
        sc.touch_geom()
        sc.commit("agent", "library", "Detach %s" % iid, [], snapshot=before)
    idx["links"].pop(iid, None)
    idx["items"].pop(iid)
    mats = _read(lib / "materials.json", {})
    if mats.pop(iid, None) is not None:
        projects._write_json(lib / "materials.json", mats)
    shutil.rmtree(lib / "parts" / iid, ignore_errors=True)
    (lib / "rigs" / (iid + ".json")).unlink(missing_ok=True)
    (lib / "thumbs" / (iid + ".png")).unlink(missing_ok=True)
    _sweep(lib, iid, set())
    return "Removed %s" % iid


def _thumb(lib: Path, idx: dict, iid: str) -> dict:
    """128 px thumbnail: an Eevee render when the engine is warm, the soft renderer otherwise."""
    from .render import render_tiles

    item = _item(idx, iid)
    dest = lib / "thumbs" / (iid + ".png")
    if item["kind"] == "rig":
        raise ValueError("Rigs have no thumbnail; list shows their name")
    if item["kind"] == "texture":
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(lib / "textures" / item["file"]) as im:
            im = im.convert("RGB")
            im.thumbnail((128, 128))
            im.save(dest)
    else:
        if item["kind"] == "part":
            mesh = Mesh.from_dict(_load_mesh(lib / "parts" / iid))
            mats = {m: dict(v, id=m) for m, v in (_read(lib / "parts" / iid / "meta.json", {}).get("materials") or {}).items()}
        else:
            mesh = build_primitive("sphere", {}, mat="m")
            mats = {"m": dict(_read(lib / "materials.json", {})[iid], id="m")}
        render_tiles(mesh, dest, ["three"], mats, lib / "textures", long_side=128)
    return {"summary": "Thumb %s" % iid, "path": str(dest)}


# ---- entry points ----------------------------------------------------------------


def act(action: str = "list", kind: str = "", id: str = "", name: str = "", from_scene: str = "", object: str = "",
        scene: str = "", path: str = "", pos: list | None = None, force: bool = False, project: str = "") -> dict:
    action, kind = (action or "list").lower(), (kind or "").lower()
    if action not in ACTIONS:
        raise ValueError("No action %r. Actions: %s" % (action, ", ".join(ACTIONS)))
    if kind and kind not in KINDS:
        raise ValueError("No kind %r. Kinds: %s" % (kind, ", ".join(KINDS)))
    lib = _lib(_project(scene, from_scene, project=project))
    with S._LOCK:
        idx = _index(lib)
        if action == "list":
            ids = [i for i, v in idx["items"].items() if not kind or v["kind"] == kind]
            counts: dict = {}
            for i in ids:
                counts[idx["items"][i]["kind"]] = counts.get(idx["items"][i]["kind"], 0) + 1
            rows = ["%s %s x%d" % (i, idx["items"][i]["kind"], _used(idx, i)) for i in ids[:_SHOWN]]
            more = " (+%d more)" % (len(ids) - _SHOWN) if len(ids) > _SHOWN else ""
            return {"summary": "%d items: %s%s" % (len(ids), ", ".join("%d %s" % (n, k) for k, n in counts.items()) or "none", more),
                    "items": rows}
        if action == "thumb":
            return _thumb(lib, idx, id)
        if action == "add":
            if not kind:
                raise ValueError("Pass kind=material|texture|part|rig")
            ref = object or ""
            iid = _slug(id or name or ref or Path(path).stem)
            if not iid:
                raise ValueError("Pass id=<short id> or name=<name> for the new library item")
            old = idx["items"].get(iid)
            if old and old["kind"] != kind:
                raise ValueError("%r is already a library %s; pick another id" % (iid, old["kind"]))
            idx["items"][iid] = {"kind": kind, "name": name or old and old["name"] or ref or iid, "rev": (old or {}).get("rev", 0) + 1,
                                 **({"file": old["file"]} if old and old.get("file") else {})}
            try:
                _add(lib, idx, kind, iid, idx["items"][iid]["name"], from_scene, ref, path, "base_color")
            except Exception:
                if old:
                    idx["items"][iid] = old
                else:
                    idx["items"].pop(iid, None)
                raise
            pushed = _refresh(lib, idx, iid) if old else 0
            _save_index(lib, idx)
            return {"summary": "%s %s %s%s" % ("Updated" if old else "Added", kind, iid,
                                               ", refreshed %d scene(s)" % pushed if pushed else ""), "id": iid}
        if action == "link":
            msg = _link(lib, idx, _slug(id), scene, object, pos)
        elif action == "make_local":
            msg = _make_local(lib, idx, scene, object)
        else:
            msg = _remove(lib, idx, _slug(id), bool(force))
        _save_index(lib, idx)
        return {"summary": msg}


def register(api: Any) -> None:
    host = {"listener": False}

    @api.tool(**host)
    def forge_library(action: str = "list", kind: str = "", id: str = "", name: str = "", from_scene: str = "",
                      object: str = "", scene: str = "", path: str = "", pos: list | None = None, force: bool = False,
                      project: str = "") -> dict:
        """Shared project library: action=list|add|link|make_local|remove|thumb, kind=material|texture|part|rig. add copies object (a material, object or rig of from_scene, or path= an image) as id; link puts it in scene (edits to the library update every linked scene); make_local(scene, object) makes an editable copy."""
        try:
            return act(action, kind, id, name, from_scene, object, scene, path, pos, force, project)
        except ValueError as exc:
            return {"error": str(exc)[:280]}

    def rpc(**kwargs: Any) -> dict:
        try:
            return {"ok": True, **act(**{k: v for k, v in kwargs.items() if k in act.__code__.co_varnames[:act.__code__.co_argcount]})}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    api.register_panel_rpc("forge_library", rpc)
