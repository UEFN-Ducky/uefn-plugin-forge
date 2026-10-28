"""HQ generate: text-to-3D creatures from Tripo or Meshy, cleaned to a game-ready, rig-ready mesh.

generate     prompt -> provider job -> GLB -> a new object in the scene (spends the user's credits)
cleanup      generated mesh -> real size, front -Y, remesh, quad retopo, smart UV, texture baked on the new UVs
creature_from_prompt  generate, cleanup, rig_template, auto_weights, clip, look
"""
import json
import re
import tempfile
from pathlib import Path

from . import HQ_OPS, op

PROVIDERS = {
    "tripo": ("tripo_text_to_3d", "tripo_balance"),
    "meshy": ("meshy_text_to_3d", "meshy_balance"),
}
POSES = {"T": "T-pose, arms out", "A": "A-pose", "stand": "standing naturally"}
FRONTS = {"-y": 0.0, "+y": 180.0, "+x": -90.0, "-x": 90.0}  # yaw that turns that axis into -Y
RIGS = {"quadruped": ("quadruped", 110.0), "biped": ("ue5_manny", 180.0)}
_COST_KEYS = ("consumed_credits", "credits_consumed", "credits_used", "credits_spent", "cost_credits", "credits", "cost")
_MODEL = re.compile(r"\.(glb|gltf)$", re.I)


def _call(tool, args):
    """The installed Tripo/Meshy plugin tool, through the plugin api Forge was registered with."""
    from .. import live_view
    api = live_view._api
    if api is None or not hasattr(api, "call_tool"):
        raise ValueError("Forge cannot reach the %s plugin from here. Run it inside Ducky with the Tripo/Meshy plugin installed, "
                         "or import a GLB with forge_import_mesh" % tool.split("_")[0])
    return api.call_tool(tool, args)


def _plain(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _walk(value):
    value = _plain(value)
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def _model_path(result):
    """The first local .glb/.gltf path anywhere in a provider result."""
    for item in _walk(result):
        if isinstance(item, str) and _MODEL.search(item.split("?")[0]) and not item.startswith(("http://", "https://")):
            return item
    return ""


def _number(result, keys):
    for item in _walk(result):
        if isinstance(item, dict):
            for key in keys:
                if isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool):
                    return float(item[key])
    return None


def _balance(provider):
    try:
        return _number(_call(PROVIDERS[provider][1], {}), ("balance", "credits", "remaining"))
    except Exception:  # noqa: BLE001 — cost reporting only; never blocks a paid job that already ran
        return None


def _name(prompt):
    words = re.findall(r"[A-Za-z]+", prompt.split(",")[0])
    return (words[-1].title() if words else "Creature") + "_gen"


def _objects_under(scene, roots):
    keep, pending = [], list(roots)
    while pending:
        node = scene.node(pending.pop())
        if node is None:
            continue
        if node.get("type") == "object":
            keep.append(node["id"])
        pending.extend(n["id"] for n in scene.data["nodes"] if n.get("parent") == node["id"])
    return keep


@op("generate", "generate(prompt, provider='tripo'|'meshy', style='realistic', pose='T'|'A'|'stand', name?) text-to-3D "
                "creature imported as <Name>_gen; SPENDS provider credits (~20), one attempt, never retried; see skill forge/generate", long=True)
def generate(scene, p):
    prompt = str(p.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Pass prompt, e.g. generate(prompt='adult male lion, standing')")
    provider = str(p.get("provider") or "tripo").lower()
    if provider not in PROVIDERS:
        raise ValueError("No provider %r. Providers: %s" % (provider, ", ".join(PROVIDERS)))
    pose = str(p.get("pose") or "stand")
    if pose not in POSES:
        raise ValueError("No pose %r. Poses: %s" % (pose, ", ".join(POSES)))
    style = str(p.get("style") or "realistic")
    full = "%s, %s style, %s, single subject, plain background" % (prompt, style, POSES[pose])
    out_dir = Path(scene.folder) / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    before = _balance(provider)
    tool = PROVIDERS[provider][0]
    # One call, no retry: a failed or half-finished job may already have billed. confirm_spend: calling this op is the consent.
    result = _call(tool, {"prompt": full, "confirm_spend": True, "wait": True, "output_dir": str(out_dir)})
    for item in _walk(result):
        if isinstance(item, dict) and (item.get("error") or item.get("ok") is False):
            raise ValueError("%s failed: %s. Check the plugin's API key and credits; no retry was made because it may have billed" % (
                tool, str(item.get("error") or item.get("summary") or item)[:160]))
    path = _model_path(result)
    if not path or not Path(path).is_file():
        raise ValueError("%s finished but returned no local GLB. Look in %s or run tripo_download / meshy_status for the task, "
                         "then forge_import_mesh the file" % (tool, out_dir))
    after = _balance(provider)
    # The balance drop covers every stage billed (Meshy bills preview and refine apart); the reported figure is the fallback.
    credits = round(before - after, 2) if before is not None and after is not None else _number(result, _COST_KEYS)

    from ..blend_import import import_blend
    merged = import_blend(path, target=scene)
    ids = _objects_under(scene, merged.get("ids") or [])
    if not ids:
        raise ValueError("The generated file had no mesh objects (%s)" % path)
    scene.node(ids[0])["name"] = str(p.get("name") or _name(prompt))
    tris = sum(scene.local_mesh(scene.node(i)).tri_count() for i in ids)
    textured = any((m.get("textures") or {}).get("base_color") for m in scene.data.get("materials") or [])
    out = {"summary": "%s: %s tris%s via %s%s" % (scene.node(ids[0])["name"], tris, ", textured" if textured else "", provider,
                                                  ", %g credits" % credits if credits is not None else ""),
           "ids": ids, "tris": tris, "textured": textured, "path": path}
    if credits is not None:
        out["credits"] = credits
    if after is not None:
        out["balance"] = after
    return out


# ---- cleanup ---------------------------------------------------------------------


def _real_size(scene, node, height, front):
    """Bake the node's transform into its mesh, scale to height cm, face -Y, stand on z=0 centred in x/y."""
    mesh = scene.world_mesh(node)
    lo, hi = mesh.bounds()
    if hi[2] - lo[2] <= 1e-9:
        raise ValueError("%s has no height to scale" % node.get("name"))
    k = float(height) / (hi[2] - lo[2])
    mesh.transform([0, 0, 0], [0, 0, FRONTS[front]], [k, k, k])
    lo, hi = mesh.bounds()
    mesh.transform([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]], [0, 0, 0], [1, 1, 1])
    scene.write_mesh(node, mesh)
    node["parent"] = None
    node["transform"] = {"pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]}
    return k


@op("cleanup", "cleanup(id, faces=12000, height=110, front='-y'|'+y'|'+x'|'-x', size=2048, voxel?) generated mesh -> real size, "
               "front -Y, remesh, quad retopo, smart UV, texture baked from the original; see skill forge/generate", long=True)
def cleanup(scene, p):
    from ..scene import _op_delete, _op_duplicate, _op_uv
    nid = str(p.get("id") or "")
    node = scene.node(nid)
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No object %r to clean up. Objects: %s" % (nid, ", ".join(objs) or "none"))
    front = str(p.get("front") or "-y").lower()
    if front not in FRONTS:
        raise ValueError("No front %r. Fronts: %s (the axis the creature faces now)" % (front, ", ".join(FRONTS)))
    faces, size = int(p.get("faces", 12000)), int(p.get("size", 2048))
    height = float(p.get("height") or 110.0)
    start = scene.local_mesh(node).tri_count()

    _real_size(scene, node, height, front)
    high = _op_duplicate(scene, {"id": nid, "offset": [0, 0, 0]})["ids"][0]  # untouched source for the bake
    scene.node(high).update(name=str(node.get("name")) + "_high", visible=False)
    scene.data["selection"]["object"] = nid
    try:
        voxel = float(p.get("voxel") or max(0.3, height / 150.0))
        HQ_OPS["remesh"](scene, {"id": nid, "voxel": voxel})
        HQ_OPS["retopo"](scene, {"id": nid, "faces": faces})
        _op_uv(scene, {"id": nid, "mode": "smart"})
        baked = HQ_OPS["bake"](scene, {"id": nid, "size": size, "from_high": high, "maps": ["base_color"]})
    finally:
        _op_delete(scene, {"ids": [high]})
    mesh = scene.local_mesh(scene.node(nid))
    quads = sum(len(f) == 4 for f in mesh.faces)
    mats = scene.materials()
    texture = next((m["textures"]["base_color"] for m in mats.values() if (m.get("textures") or {}).get("base_color")), "")
    return {"summary": "%s: %d tris -> %d faces (%d%% quads), %dpx bake" % (node.get("name"), start, len(mesh.faces),
                                                                          100 * quads // max(1, len(mesh.faces)), size),
            "ids": [nid], "faces": len(mesh.faces), "quads": quads, "tris": mesh.tri_count(), "texture": texture,
            "path": baked.get("path", "")}


# ---- recipe ----------------------------------------------------------------------


@op("creature_from_prompt", "creature_from_prompt(prompt, kind='quadruped'|'biped', height?, clip='walk', provider='tripo', faces=12000) "
                            "generate, cleanup, rig_template, auto_weights, clip: a walking creature in one call; SPENDS credits",
    long=True)
def creature_from_prompt(scene, p):
    kind = str(p.get("kind") or "quadruped")
    if kind not in RIGS:
        raise ValueError("No kind %r. Kinds: %s" % (kind, ", ".join(RIGS)))
    template, default_height = RIGS[kind]
    gen_args = {k: p[k] for k in ("prompt", "provider", "style", "pose", "name") if p.get(k)}
    if kind == "biped":
        gen_args.setdefault("pose", "A")
    made = generate(scene, gen_args)
    nid = made["ids"][0]
    cleaned = cleanup(scene, {"id": nid, "faces": int(p.get("faces", 12000)), "height": float(p.get("height") or default_height)})
    rigged = HQ_OPS["rig_template"](scene, {"template": template, "ids": [nid]})
    HQ_OPS["auto_weights"](scene, {"ids": [nid]})
    walked = HQ_OPS["clip"](scene, {"kind": str(p.get("clip") or "walk"), "rig": rigged["id"]})
    out = {"summary": "%s: %d faces, %d bones, %s" % (scene.node(nid)["name"], cleaned["faces"], rigged["bones"],
                                                      walked["summary"].split(":")[0]),
           "ids": [nid, rigged["id"]], "faces": cleaned["faces"], "bones": rigged["bones"]}
    for key in ("credits", "balance"):
        if key in made:
            out[key] = made[key]
    try:
        from ..scene import _op_look
        seen = _op_look(scene, {"look": "three"})
        if seen.get("path") or seen.get("paths"):
            out["path"] = seen.get("path") or seen["paths"][0]
    except Exception as exc:  # noqa: BLE001 — the creature is built; the picture is a bonus
        out.setdefault("warnings", []).append("look failed: %s" % str(exc)[:80])
    return out
