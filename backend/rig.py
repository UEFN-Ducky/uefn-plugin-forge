"""Skeletons, skin binding, and animation clips.

One pose sampler feeds the panel preview, forge_render, and the GLB writer, so the
motion in the dock is the motion UEFN imports.

Bones are {name, parent, head, tail} in scene centimetres, Z-up. Rest frames are
world-axis aligned at each head (BVH style): a key rot=[0, 30, 0] always means 30
degrees about the bone's Y in its parent's posed frame, the same Rz*Ry*Rx order
objects use. The biped template faces -Y (the front view) with _l bones on +X.
ponytail: no bone roll and no Y-along-bone axes. Add roll when a rig needs twist
bones or engine IK that expects bone-aligned axes.
"""

from __future__ import annotations

import array
import base64
import copy
import math
import secrets
from typing import Any

from .kernel.mesh import Mesh, _euler, vdot, vlen, vsub
from .transforms import decompose, multiply

_I = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
CHANNELS = ("rot", "loc", "scale")
INTERPS = ("smooth", "linear", "step")
KINDS = ("idle", "walk", "wave", "sway", "spin", "bob", "swing", "open")
_AXIS = {"x": 0, "y": 1, "z": 2}


def _nid(prefix: str) -> str:
    return prefix + secrets.token_hex(3)


def _vec(value: Any, what: str) -> list[float]:
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        raise ValueError(f"{what} needs three numbers") from None
    if len(out) != 3 or not all(math.isfinite(v) for v in out):
        raise ValueError(f"{what} needs three finite numbers")
    return out


def _bone_name(name: Any) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(name or "").strip())[:64]
    if not cleaned:
        raise ValueError("bone name required")
    return cleaned


# ---- lookups ---------------------------------------------------------------


def find_rig(scene, rid: str = "") -> dict:
    items = scene.data.get("rigs") or []
    if not items:
        raise ValueError("No rig yet. forge_rig(action='template', template='biped') or action='new'.")
    if not rid:
        return items[0]
    for rig in items:
        if rig["id"] == rid or rig.get("name") == rid:
            return rig
    raise ValueError(f"No rig {rid!r}")


def find_bone(rig: dict, name: str) -> dict:
    for bone in rig["bones"]:
        if bone["name"] == name:
            return bone
    known = ", ".join(b["name"] for b in rig["bones"][:40])
    raise ValueError(f"No bone {name!r} in {rig.get('name')}. Bones: {known or 'none'}")


def find_clip(rig: dict, cid: str = "") -> dict:
    clips = rig.setdefault("actions", [])
    if not cid:
        if len(clips) == 1:
            return clips[0]
        raise ValueError("Name the clip." if clips else "No clips yet. forge_anim(action='generate') or action='new'.")
    for clip in clips:
        if clip["id"] == cid or clip.get("name") == cid:
            return clip
    raise ValueError(f"No clip {cid!r}. Clips: {', '.join(c['name'] for c in clips) or 'none'}")


def ordered(rig: dict) -> list[dict]:
    """Parents before children. A missing parent makes a bone a root."""
    bones = rig.get("bones") or []
    names = {b["name"] for b in bones}
    out: list[dict] = []
    placed: set[str] = set()
    pending = list(bones)
    while pending:
        rest = [b for b in pending if b.get("parent") in names and b.get("parent") not in placed]
        ready = [b for b in pending if b not in rest]
        if not ready:
            raise ValueError("Bone hierarchy contains a cycle")
        out.extend(ready)
        placed.update(b["name"] for b in ready)
        pending = rest
    return out


def export_bones(rig: dict) -> list[dict]:
    """ordered() plus one synthetic root when there are several, because UE refuses multi-root skeletons."""
    bones = [dict(b) for b in ordered(rig)]
    names = {b["name"] for b in bones}
    roots = [b for b in bones if b.get("parent") not in names]
    if len(roots) <= 1:
        return bones
    root = "root" if "root" not in names else "forge_root"
    for bone in roots:
        bone["parent"] = root
    return [{"name": root, "parent": None, "head": [0.0, 0.0, 0.0], "tail": [0.0, -10.0, 0.0]}] + bones


def children(rig: dict, name: str) -> list[str]:
    return [b["name"] for b in rig["bones"] if b.get("parent") == name]


# ---- math ------------------------------------------------------------------


def _quat(rot_deg) -> tuple[float, float, float, float]:
    """Rz*Ry*Rx Euler degrees -> (w, x, y, z), the same order as kernel _euler."""
    hx, hy, hz = (math.radians(float(a)) / 2.0 for a in rot_deg)
    cx, sx, cy, sy, cz, sz = math.cos(hx), math.sin(hx), math.cos(hy), math.sin(hy), math.cos(hz), math.sin(hz)
    return (cz * cy * cx + sz * sy * sx, cz * cy * sx - sz * sy * cx,
            cz * sy * cx + sz * cy * sx, sz * cy * cx - cz * sy * sx)


def _qmat(q) -> list[list[float]]:
    w, x, y, z = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def _slerp(a, b, t: float):
    d = sum(p * q for p, q in zip(a, b))
    if d < 0:
        b, d = tuple(-v for v in b), -d
    if d > 0.9995:
        out = [p + (q - p) * t for p, q in zip(a, b)]
    else:
        th = math.acos(min(1.0, d))
        s = math.sin(th)
        out = [(math.sin((1 - t) * th) * p + math.sin(t * th) * q) / s for p, q in zip(a, b)]
    n = math.sqrt(sum(v * v for v in out)) or 1.0
    return tuple(v / n for v in out)


def euler_of(*rot_degs) -> list[float]:
    """Compose rotations left to right (first is outermost) into one Euler triple."""
    m = [row[:] for row in _I]
    for rot in rot_degs:
        r = _euler(rot)
        m = multiply(m, [r[0] + [0.0], r[1] + [0.0], r[2] + [0.0], [0.0, 0.0, 0.0, 1.0]])
    return [round(v, 4) for v in decompose(m)["rot"]]


def _sample(keys: list | None, frame: float, channel: str):
    """Value of one channel at a frame. Rotations come back as quaternions."""
    if not keys:
        return None
    conv = _quat if channel == "rot" else list
    if frame <= keys[0][0]:
        return conv(keys[0][1])
    if frame >= keys[-1][0]:
        return conv(keys[-1][1])
    for k in range(len(keys) - 1):
        f0, f1 = keys[k][0], keys[k + 1][0]
        if f0 <= frame <= f1:
            t = (frame - f0) / (f1 - f0) if f1 > f0 else 1.0
            interp = keys[k][2] if len(keys[k]) > 2 else "smooth"
            if interp == "step":
                t = 1.0 if frame >= f1 else 0.0
            elif interp == "smooth":
                t = t * t * (3 - 2 * t)
            if channel == "rot":
                return _slerp(_quat(keys[k][1]), _quat(keys[k + 1][1]), t)
            return [a + (b - a) * t for a, b in zip(keys[k][1], keys[k + 1][1])]
    return conv(keys[-1][1])


def local_pose(clip: dict | None, bone: str, frame: float) -> tuple[list, tuple, list]:
    """(loc offset, quaternion, scale) of one bone. Rest when the clip has no track."""
    track = ((clip or {}).get("tracks") or {}).get(bone) or {}
    loc = _sample(track.get("loc"), frame, "loc") or [0.0, 0.0, 0.0]
    rot = _sample(track.get("rot"), frame, "rot") or (1.0, 0.0, 0.0, 0.0)
    scl = _sample(track.get("scale"), frame, "scale") or [1.0, 1.0, 1.0]
    return loc, rot, scl


def skin_matrices(bones: list[dict], clip: dict | None = None, frame: float = 0.0) -> list[list[list[float]]]:
    """world_pose * inverse(rest) per bone, in `bones` order (parents first). Identity at rest."""
    world: dict[str, list] = {}
    heads: dict[str, list] = {}
    out = []
    for bone in bones:
        head, parent = bone["head"], bone.get("parent")
        base = world.get(parent, _I)
        ph = heads.get(parent, [0.0, 0.0, 0.0])
        loc, q, scl = local_pose(clip, bone["name"], frame)
        r = _qmat(q)
        local = [[r[i][0] * scl[0], r[i][1] * scl[1], r[i][2] * scl[2], head[i] - ph[i] + loc[i]] for i in range(3)]
        world[bone["name"]] = multiply(base, local + [[0.0, 0.0, 0.0, 1.0]])
        heads[bone["name"]] = head
        m = world[bone["name"]]
        out.append([[m[i][0], m[i][1], m[i][2], m[i][3] - (m[i][0] * head[0] + m[i][1] * head[1] + m[i][2] * head[2])]
                    for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]])
    return out


def deform(mesh: Mesh, joints: list, weights: list, mats: list) -> Mesh:
    out = mesh.copy()
    for i, v in enumerate(mesh.verts):
        x = y = z = 0.0
        for j, w in zip(joints[i], weights[i]):
            if w <= 0.0:
                continue
            m = mats[j]
            x += w * (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2] + m[0][3])
            y += w * (m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2] + m[1][3])
            z += w * (m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2] + m[2][3])
        out.verts[i] = [x, y, z]
    return out


def _seg_dist(p, a, b) -> float:
    ab = vsub(b, a)
    den = vdot(ab, ab)
    t = 0.0 if den < 1e-9 else max(0.0, min(1.0, vdot(vsub(p, a), ab) / den))
    return vlen(vsub(p, [a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t]))


def bind_weights(verts: list, bones: list[dict], skin: dict, attrs: dict | None = None) -> tuple[list, list]:
    """Up to four (joint, weight) pairs per vertex, indexed into `bones`.

    Painted skins read wt:<bone> vertex attributes; a vertex painted to nothing falls
    back to its nearest bone so it never collapses to the origin.
    ponytail: envelope weights (inverse distance^4 to each bone segment), not bone
    heat. Good for blocky game characters; upgrade to heat diffusion when shoulders
    and hips need hand-tuned falloff.
    """
    index = {b["name"]: i for i, b in enumerate(bones)}
    if skin.get("bone"):
        j = index.get(skin["bone"])
        if j is None:
            raise ValueError(f"Bound bone {skin['bone']!r} no longer exists")
        return [(j, 0, 0, 0)] * len(verts), [(1.0, 0.0, 0.0, 0.0)] * len(verts)
    if skin.get("paint"):
        groups = [(index[name[3:]], vals) for name, vals in (attrs or {}).items()
                  if name.startswith("wt:") and name[3:] in index and len(vals) == len(verts)]
        joints, weights, empty = [], [], []
        for i in range(len(verts)):
            pairs = sorted(((vals[i], j) for j, vals in groups if vals[i] > 1e-4), reverse=True)[:4]
            total = sum(w for w, _ in pairs)
            if total <= 0:
                empty.append(i)
                pairs, total = [(1.0, 0)], 1.0
            pairs += [(0.0, 0)] * (4 - len(pairs))
            joints.append(tuple(j for _, j in pairs))
            weights.append(tuple(w / total for w, _ in pairs))
        if empty:
            near_j, near_w = bind_weights([verts[i] for i in empty], bones, {"rig": skin.get("rig"), "auto": True})
            for k, i in enumerate(empty):
                joints[i], weights[i] = near_j[k], near_w[k]
        return joints, weights
    names = list(skin.get("bones") or []) or [b["name"] for b in bones if b["name"].lower() not in ("root", "forge_root")]
    segs = [(index[b["name"]], b["head"], b["tail"]) for b in bones if b["name"] in names]
    if not segs:
        raise ValueError("Auto skin needs at least one deforming bone")
    joints, weights = [], []
    for v in verts:
        scored = sorted(((max(_seg_dist(v, a, b), 0.5) ** -4, j) for j, a, b in segs), reverse=True)[:4]
        total = sum(w for w, _ in scored)
        kept = [(w, j) for w, j in scored if w / total >= 0.02]
        total = sum(w for w, _ in kept)
        kept += [(0.0, 0)] * (4 - len(kept))
        joints.append(tuple(j for _, j in kept))
        weights.append(tuple(w / total for w, _ in kept))
    return joints, weights


def rig_height(rig: dict) -> float:
    zs = [p[2] for b in rig["bones"] for p in (b["head"], b["tail"])]
    return (max(zs) - min(zs)) if zs else 180.0


# ---- scene helpers ---------------------------------------------------------


def _objects_under(scene, ids: list) -> list[dict]:
    """Objects in ids, groups expanded to every descendant object."""
    kids: dict = {}
    for n in scene.data["nodes"]:
        kids.setdefault(n.get("parent"), []).append(n)
    out, seen, pending = [], set(), list(ids)
    while pending:
        node = scene.node(pending.pop())
        if node is None or node["id"] in seen:
            continue
        seen.add(node["id"])
        if node.get("type") == "object":
            out.append(node)
        pending.extend(child["id"] for child in kids.get(node["id"], []))
    return out


def _ids(scene, p: dict) -> list:
    ids = [i for i in (p.get("ids") or []) if i]
    if ids:
        return ids
    sel = scene.data["selection"].get("object")
    return [sel] if sel else []


def bound_objects(scene, rig: dict) -> list[dict]:
    return [n for n in scene.data["nodes"] if n.get("type") == "object" and (n.get("skin") or {}).get("rig") == rig["id"]]


def _bounds(scene, nodes: list[dict]) -> tuple[list, list] | None:
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for n in nodes:
        a, b = scene.world_mesh(n).bounds()
        lo = [min(lo[i], a[i]) for i in range(3)]
        hi = [max(hi[i], b[i]) for i in range(3)]
    return (lo, hi) if nodes and lo[0] <= hi[0] else None


# ---- templates -------------------------------------------------------------

# name, parent, head, tail as fractions of height. Faces -Y; the character's left is +X.
_BIPED = (
    ("root", None, (0, 0, 0), (0, -0.1, 0)),
    ("pelvis", "root", (0, 0, 0.53), (0, 0, 0.58)),
    ("spine_01", "pelvis", (0, 0, 0.58), (0, 0, 0.66)),
    ("spine_02", "spine_01", (0, 0, 0.66), (0, 0, 0.74)),
    ("spine_03", "spine_02", (0, 0, 0.74), (0, 0, 0.82)),
    ("neck_01", "spine_03", (0, 0, 0.82), (0, 0, 0.87)),
    ("head", "neck_01", (0, 0, 0.87), (0, 0, 1.0)),
    ("clavicle_l", "spine_03", (0.02, 0, 0.8), (0.1, 0, 0.8)),
    ("upperarm_l", "clavicle_l", (0.1, 0, 0.8), (0.26, 0, 0.8)),
    ("lowerarm_l", "upperarm_l", (0.26, 0, 0.8), (0.41, 0, 0.8)),
    ("hand_l", "lowerarm_l", (0.41, 0, 0.8), (0.48, 0, 0.8)),
    ("thigh_l", "pelvis", (0.06, 0, 0.53), (0.06, 0, 0.28)),
    ("calf_l", "thigh_l", (0.06, 0, 0.28), (0.06, 0, 0.05)),
    ("foot_l", "calf_l", (0.06, 0, 0.05), (0.06, -0.08, 0.01)),
    ("ball_l", "foot_l", (0.06, -0.08, 0.01), (0.06, -0.13, 0.01)),
)
_ARM = ("clavicle", "upperarm", "lowerarm", "hand")


def _biped(lo: list, hi: list) -> list[dict]:
    h = (hi[2] - lo[2]) or 180.0
    cx, cy, base = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]
    span = hi[0] - lo[0]
    # A T-posed mesh is wider than it is tall-ish: stretch the arms to its fingertips.
    arm = (span / 2) / (0.48 * h) if span > 0.7 * h else 1.0
    out = []
    for name, parent, head, tail in _BIPED:
        sides = ((name, parent, 1),) if not name.endswith("_l") else ((name, parent, 1), (name[:-2] + "_r", parent[:-2] + "_r" if parent.endswith("_l") else parent, -1))
        for bname, bparent, sign in sides:
            k = arm if bname.split("_")[0] in _ARM else 1.0

            def at(f):
                return [round(cx + sign * f[0] * h * k, 3), round(cy + f[1] * h, 3), round(base + f[2] * h, 3)]

            out.append({"name": bname, "parent": bparent, "head": at(head), "tail": at(tail)})
    return out


def _chain(start: list, end: list, count: int, prefix: str, parent: str | None) -> list[dict]:
    count = max(1, min(64, int(count)))
    pts = [[start[i] + (end[i] - start[i]) * k / count for i in range(3)] for k in range(count + 1)]
    out = []
    for k in range(count):
        out.append({"name": f"{prefix}_{k + 1:02d}", "parent": parent if k == 0 else out[-1]["name"],
                    "head": [round(v, 3) for v in pts[k]], "tail": [round(v, 3) for v in pts[k + 1]]})
    return out


# ---- rig op ----------------------------------------------------------------


def _rename_bone(scene, rig: dict, old: str, new: str) -> None:
    for bone in rig["bones"]:
        if bone["name"] == old:
            bone["name"] = new
        if bone.get("parent") == old:
            bone["parent"] = new
    for clip in rig.get("actions") or []:
        tracks = clip.get("tracks") or {}
        if old in tracks:
            tracks[new] = tracks.pop(old)
    for node in bound_objects(scene, rig):
        skin = node["skin"]
        if skin.get("bone") == old:
            skin["bone"] = new
        if old in (skin.get("bones") or []):
            skin["bones"] = [new if b == old else b for b in skin["bones"]]


def _add_bones(rig: dict, bones: list[dict]) -> list[str]:
    taken = {b["name"] for b in rig["bones"]}
    clash = [b["name"] for b in bones if b["name"] in taken]
    if clash:
        raise ValueError(f"Bones already exist: {', '.join(clash)}. Use a new prefix or a new rig.")
    rig["bones"].extend(bones)
    ordered(rig)
    return [b["name"] for b in bones]


def _paint_weights(scene, rig: dict, p: dict) -> dict:
    """Painted skin: seed wt:<bone> from the current binding, then add, subtract, set, or smooth."""
    from .kernel.sculpt import neighbors, stroke
    from .transforms import to_local

    nodes = _objects_under(scene, _ids(scene, p))
    if not nodes:
        raise ValueError("Select objects or pass ids")
    bones = export_bones(rig)
    mode = str(p.get("mode") or "add")
    strength = float(p.get("strength", 0.5))
    value = float(p.get("value", 1.0))
    bone = find_bone(rig, str(p["bone"]))["name"] if p.get("bone") else ""
    for node in nodes:
        if scene.is_locked(node):
            raise ValueError(f"{node.get('name')} is locked")
        skin = node.get("skin") or {}
        if skin.get("rig") not in (None, rig["id"]):
            raise ValueError(f"{node.get('name')} is bound to another rig")
        mesh = scene._bake(node)
        if p.get("init") or not skin.get("paint"):
            seed = skin if skin.get("rig") == rig["id"] and not skin.get("paint") else {"rig": rig["id"], "auto": True}
            world = scene.world_mesh(node)
            joints, weights = bind_weights(world.verts, bones, seed, world.attrs)
            for name in [n for n in mesh.attrs if n.startswith("wt:")]:
                mesh.attrs.pop(name)
            for i, (js, ws) in enumerate(zip(joints, weights)):
                for j, w in zip(js, ws):
                    if w > 0:
                        mesh.ensure_attr("wt:" + bones[j]["name"])[i] += w
            node["skin"] = {"rig": rig["id"], "paint": True}
        if bone:
            target = "wt:" + bone
            if p.get("points"):
                pts, k = to_local(scene, node["id"], p["points"])
                stroke(mesh, pts, mode, float(p.get("radius", 20)) * k, strength, target=target, value=value)
            else:
                vals = mesh.ensure_attr(target)
                picked = {v for fi in p.get("faces") or [] if 0 <= int(fi) < len(mesh.faces) for v in mesh.faces[int(fi)]}
                verts = sorted(picked) if p.get("faces") else range(len(mesh.verts))
                adj = neighbors(mesh) if mode == "smooth" else None
                for i in list(verts):
                    v = vals[i]
                    if mode == "add":
                        v += strength
                    elif mode == "subtract":
                        v -= strength
                    elif mode == "set":
                        v += (value - v) * strength
                    elif mode == "smooth" and adj[i]:
                        v += (sum(vals[j] for j in adj[i]) / len(adj[i]) - v) * strength
                    vals[i] = max(0.0, min(1.0, v))
        if p.get("normalize"):
            groups = [vals for name, vals in mesh.attrs.items() if name.startswith("wt:")]
            for i in range(len(mesh.verts)):
                total = sum(g[i] for g in groups)
                if total > 0:
                    for g in groups:
                        g[i] /= total
        scene.write_mesh(node, mesh)
    scene.touch_geom()
    what = f"{mode} {bone}" if bone else "seeded"
    return {"summary": f"Weights {what} on {len(nodes)} object(s)", "ids": [n["id"] for n in nodes]}


# ---- IK --------------------------------------------------------------------


def _m3mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _m3T(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _m3v(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _rot_of(m4):
    """Rotation part of a pose matrix, scale stripped."""
    cols = [[m4[i][j] for i in range(3)] for j in range(3)]
    cols = [vnorm_list(c) for c in cols]
    return [[cols[j][i] for j in range(3)] for i in range(3)]


def vnorm_list(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


def _rodrigues(axis, angle):
    x, y, z = vnorm_list(axis)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]


def _rot_between(u, v):
    u, v = vnorm_list(u), vnorm_list(v)
    c = max(-1.0, min(1.0, sum(a * b for a, b in zip(u, v))))
    axis = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    if math.sqrt(sum(a * a for a in axis)) < 1e-9:
        if c > 0:
            return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        axis = [0.0, -u[2], u[1]] if abs(u[0]) < 0.9 else [-u[1], u[0], 0.0]
    return _rodrigues(axis, math.acos(c))


def _euler3(m3) -> list[float]:
    return [round(v, 4) for v in decompose([m3[0] + [0.0], m3[1] + [0.0], m3[2] + [0.0], [0.0, 0.0, 0.0, 1.0]])["rot"]]


def solve_ik(bones: list[dict], clip: dict | None, frame: float, effector: str, target, chain: int = 2,
             pole=None, keep: bool = True) -> dict[str, list[float]]:
    """Local rotation keys that put `effector`'s head on `target` (world cm).

    Two bones: exact, bending toward `pole` (default: where the middle joint already
    points). Longer chains: cyclic coordinate descent. keep=True holds the effector's
    world rotation, so a planted foot stays flat.
    """
    index = {b["name"]: i for i, b in enumerate(bones)}
    parent_of = {b["name"]: b.get("parent") for b in bones}
    if effector not in index:
        raise ValueError(f"No bone {effector!r}")
    mats = skin_matrices(bones, clip, frame)

    def head(name):
        m, h = mats[index[name]], bones[index[name]]["head"]
        return [sum(m[i][k] * h[k] for k in range(3)) + m[i][3] for i in range(3)]

    names, cur = [], parent_of[effector]
    while cur and len(names) < max(1, int(chain)):
        names.append(cur)
        cur = parent_of[cur]
    if not names:
        raise ValueError(f"{effector} has no parent bones to bend")
    names.reverse()
    rots = {n: _rot_of(mats[index[n]]) for n in names + [effector]}
    pos = {n: head(n) for n in names + [effector]}
    original = [row[:] for row in rots[effector]]
    t = [float(c) for c in target]
    if len(names) == 2:
        up, lo = names
        a_, b_, c_ = pos[up], pos[lo], pos[effector]
        la, lb = math.dist(a_, b_), math.dist(b_, c_)
        at = [t[i] - a_[i] for i in range(3)]
        d = math.sqrt(sum(x * x for x in at))
        if d > 1e-6:
            u = [x / d for x in at]
            d = max(abs(la - lb) + 1e-3, min(la + lb - 1e-3, d))
            w = [float(c) for c in pole] if pole else b_
            w = [w[i] - a_[i] for i in range(3)]
            along = sum(w[i] * u[i] for i in range(3))
            w = [w[i] - u[i] * along for i in range(3)]
            if math.sqrt(sum(x * x for x in w)) < 1e-6:
                w = [0.0, -u[2], u[1]] if abs(u[0]) < 0.9 else [-u[1], u[0], 0.0]
            v = vnorm_list(w)
            x = (la * la - lb * lb + d * d) / (2 * d)
            h = math.sqrt(max(0.0, la * la - x * x))
            b2 = [a_[i] + u[i] * x + v[i] * h for i in range(3)]
            c2 = [a_[i] + u[i] * d for i in range(3)]
            r1 = _rot_between([b_[i] - a_[i] for i in range(3)], [b2[i] - a_[i] for i in range(3)])
            rots[up] = _m3mul(r1, rots[up])
            c1 = [a_[i] + x_ for i, x_ in enumerate(_m3v(r1, [c_[k] - a_[k] for k in range(3)]))]
            r2 = _rot_between([c1[i] - b2[i] for i in range(3)], [c2[i] - b2[i] for i in range(3)])
            rots[lo] = _m3mul(r2, _m3mul(r1, rots[lo]))
            rots[effector] = _m3mul(r2, _m3mul(r1, rots[effector]))
    else:
        for _ in range(40):
            for joint in reversed(names):
                e, j = pos[effector], pos[joint]
                r = _rot_between([e[i] - j[i] for i in range(3)], [t[i] - j[i] for i in range(3)])
                for n in names[names.index(joint):] + [effector]:
                    rots[n] = _m3mul(r, rots[n])
                    if n != joint:
                        pos[n] = [j[i] + x_ for i, x_ in enumerate(_m3v(r, [pos[n][k] - j[k] for k in range(3)]))]
            if math.dist(pos[effector], t) < 0.1:
                break
    if keep:
        rots[effector] = original
    out = {}
    for n in names + ([effector] if keep else []):
        parent = parent_of[n]
        pr = rots.get(parent) or (_rot_of(mats[index[parent]]) if parent in index else [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        out[n] = _euler3(_m3mul(_m3T(pr), rots[n]))
    return out


def op_rig(scene, p: dict) -> dict:
    action = str(p.get("action") or "template")
    rigs = scene.data.setdefault("rigs", [])
    created = None
    if action == "new" or (action == "template" and not rigs):
        created = {"id": _nid("r"), "name": str(p.get("new_name") or "Rig"), "bones": [], "actions": []}
        rigs.append(created)
        if action == "new":
            scene.touch_geom()
            return {"summary": f"New rig {created['name']}", "ids": [created["id"]], "id": created["id"]}
    rig = created or find_rig(scene, str(p.get("rig") or ""))
    if action == "template":
        kind = str(p.get("template") or "biped").lower()
        targets = _objects_under(scene, _ids(scene, p)) or [n for n in scene.data["nodes"] if n.get("type") == "object"]
        box = _bounds(scene, targets)
        if kind == "biped":
            lo, hi = box or ([-40.0, -15.0, 0.0], [40.0, 15.0, 180.0])
            made = _add_bones(rig, _biped(lo, hi))
        elif kind == "chain":
            lo, hi = box or ([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
            start = _vec(p["start"], "start") if p.get("start") else [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]
            end = _vec(p["end"], "end") if p.get("end") else [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2]]
            prefix = _bone_name(p.get("prefix") or "chain")
            parent = str(p.get("parent") or "") or None
            if parent:
                find_bone(rig, parent)
            made = _add_bones(rig, _chain(start, end, int(p.get("count") or 4), prefix, parent))
        elif kind == "single":
            # One bone at the base, every target bound to it: enough skeleton to spin, bob, or carry shape keys.
            lo, hi = box or ([0.0, 0.0, 0.0], [0.0, 0.0, 100.0])
            name = _bone_name(p.get("prefix") or "root")
            base = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]
            made = _add_bones(rig, [{"name": name, "parent": None, "head": base, "tail": [base[0], base[1], hi[2]]}])
            for node in targets:
                node["skin"] = {"rig": rig["id"], "bone": name}
        else:
            raise ValueError("template is biped, chain, or single. Add bones one at a time with action='add_bone'.")
        scene.touch_geom()
        return {"summary": f"{kind.title()} rig: {len(made)} bones", "ids": [rig["id"]], "id": rig["id"], "bones": made}
    if action == "add_bone":
        name = _bone_name(p.get("bone") or p.get("new_name"))
        parent = str(p.get("parent") or "") or None
        if parent:
            find_bone(rig, parent)
        head = _vec(p.get("head"), "head")
        tail = _vec(p["tail"], "tail") if p.get("tail") else [head[0], head[1], head[2] + 20.0]
        _add_bones(rig, [{"name": name, "parent": parent, "head": head, "tail": tail}])
        scene.touch_geom()
        return {"summary": f"Bone {name}", "ids": [rig["id"]], "id": rig["id"], "bone": name}
    if action == "edit_bone":
        bone = find_bone(rig, str(p.get("bone") or ""))
        if p.get("head") is not None:
            bone["head"] = _vec(p["head"], "head")
        if p.get("tail") is not None:
            bone["tail"] = _vec(p["tail"], "tail")
        if "parent" in p:
            before = bone.get("parent")
            bone["parent"] = str(p.get("parent") or "") or None
            if bone["parent"]:
                find_bone(rig, bone["parent"])
            try:
                ordered(rig)
            except ValueError:
                bone["parent"] = before
                raise ValueError("A bone cannot parent to itself or its children") from None
        if p.get("new_name"):
            new = _bone_name(p["new_name"])
            if new != bone["name"]:
                if any(b["name"] == new for b in rig["bones"]):
                    raise ValueError(f"Bone {new!r} already exists")
                _rename_bone(scene, rig, bone["name"], new)
        scene.touch_geom()
        return {"summary": f"Edit bone {bone['name']}", "ids": [rig["id"]], "bone": bone["name"]}
    if action == "delete_bone":
        bone = find_bone(rig, str(p.get("bone") or ""))
        name, parent = bone["name"], bone.get("parent")
        rig["bones"] = [b for b in rig["bones"] if b is not bone]
        for b in rig["bones"]:
            if b.get("parent") == name:
                b["parent"] = parent
        for clip in rig.get("actions") or []:
            (clip.get("tracks") or {}).pop(name, None)
        for node in bound_objects(scene, rig):
            skin = node["skin"]
            if skin.get("bone") == name:
                if parent:
                    skin["bone"] = parent
                else:
                    node.pop("skin")
            elif name in (skin.get("bones") or []):
                skin["bones"] = [b for b in skin["bones"] if b != name]
        scene.touch_geom()
        return {"summary": f"Delete bone {name}", "ids": [rig["id"]]}
    if action == "bind":
        nodes = _objects_under(scene, _ids(scene, p))
        if not nodes:
            raise ValueError("Select objects or pass ids to bind")
        if any(scene.is_locked(n) for n in nodes):
            raise ValueError("Unlock objects before binding")
        if p.get("auto"):
            names = [str(b) for b in (p.get("bones") or [])]
            for b in names:
                find_bone(rig, b)
            skin = {"rig": rig["id"], "auto": True}
            if names:
                skin["bones"] = names
        else:
            skin = {"rig": rig["id"], "bone": find_bone(rig, str(p.get("bone") or ""))["name"]}
        for node in nodes:
            node["skin"] = dict(skin)
        scene.touch_geom()
        how = "auto weights" if skin.get("auto") else skin["bone"]
        return {"summary": f"Bind {len(nodes)} to {how}", "ids": [n["id"] for n in nodes]}
    if action == "unbind":
        nodes = _objects_under(scene, _ids(scene, p))
        for node in nodes:
            node.pop("skin", None)
        scene.touch_geom()
        return {"summary": f"Unbind {len(nodes)}", "ids": [n["id"] for n in nodes]}
    if action == "weights":
        return _paint_weights(scene, rig, p)
    if action == "delete":
        for node in bound_objects(scene, rig):
            node.pop("skin")
        scene.data["rigs"] = [r for r in rigs if r is not rig]
        scene.touch_geom()
        return {"summary": f"Delete rig {rig['name']}", "ids": [rig["id"]]}
    raise ValueError("rig action is template, new, add_bone, edit_bone, delete_bone, bind, unbind, or delete")


# ---- clips -----------------------------------------------------------------


def _upsert(keys: list, frame: float, value: list, interp: str) -> None:
    for key in keys:
        if abs(key[0] - frame) < 1e-6:
            key[1], key[2:] = value, [interp]
            return
    keys.append([frame, value, interp])
    keys.sort(key=lambda k: k[0])


def _keys(frames: float, fn, count: int = 4, interp: str = "smooth") -> list:
    return [[round(frames * i / count, 3), [round(v, 4) for v in fn(i / count)], interp] for i in range(count + 1)]


def _axis_vec(axis: str, amount: float) -> list[float]:
    out = [0.0, 0.0, 0.0]
    out[_AXIS.get(str(axis or "z").lower()[:1], 2)] = amount
    return out


def _need(rig: dict, *names: str) -> None:
    missing = [n for n in names if not any(b["name"] == n for b in rig["bones"])]
    if missing:
        raise ValueError(f"This needs a biped rig (missing {', '.join(missing)}). forge_rig(action='template', template='biped').")


def _chain_from(rig: dict, start: str) -> list[str]:
    out = [start]
    while True:
        kids = children(rig, out[-1])
        if not kids:
            return out
        out.append(kids[0])


def generate(rig: dict, kind: str, p: dict) -> tuple[dict, int, bool]:
    """Procedural keys: (tracks, frames, loop). Amplitudes scale with `amount`."""
    kind = kind.lower()
    a = float(p.get("amount") or 0) or None
    roots = [b["name"] for b in ordered(rig) if b.get("parent") is None]
    bone = str(p.get("bone") or (roots[0] if roots else ""))
    tw = math.tau
    if kind == "walk":
        _need(rig, "pelvis", "thigh_l", "thigh_r", "calf_l", "calf_r")
        f, s = int(p.get("frames") or 32), a or 1.0
        down = float(p.get("arms_down", 70))
        h = rig_height(rig)
        tracks = {"pelvis": {"loc": _keys(f, lambda t: [0, 0, 0.012 * h * s * (1 - math.cos(2 * tw * t)) / 2], 8)}}
        for side, phase in (("l", 0.0), ("r", 0.5)):
            tracks[f"thigh_{side}"] = {"rot": _keys(f, lambda t, ph=phase: [-28 * s * math.cos(tw * (t + ph)), 0, 0], 8)}
            tracks[f"calf_{side}"] = {"rot": _keys(f, lambda t, ph=phase: [5 * s + 45 * s * max(0.0, -math.sin(tw * (t + ph))), 0, 0], 8)}
            if any(b["name"] == f"upperarm_{side}" for b in rig["bones"]):
                sign = 1 if side == "l" else -1
                tracks[f"upperarm_{side}"] = {"rot": _keys(f, lambda t, ph=phase, sg=sign: euler_of([20 * s * math.cos(tw * (t + ph)), 0, 0], [0, sg * down, 0]), 8)}
        if any(b["name"] == "spine_01" for b in rig["bones"]):
            tracks["spine_01"] = {"rot": _keys(f, lambda t: [0, 0, 4 * s * math.cos(tw * t)], 8)}
        return tracks, f, True
    if kind == "idle":
        _need(rig, "pelvis", "spine_01")
        f, s = int(p.get("frames") or 60), a or 1.0
        down = float(p.get("arms_down", 70))
        h = rig_height(rig)
        tracks = {"spine_01": {"rot": _keys(f, lambda t: [-1.5 * s * math.sin(tw * t), 0, 0])},
                  "pelvis": {"loc": _keys(f, lambda t: [0, 0, -0.004 * h * s * (1 - math.cos(tw * t)) / 2])}}
        if any(b["name"] == "head" for b in rig["bones"]):
            tracks["head"] = {"rot": _keys(f, lambda t: [1.5 * s * math.sin(tw * t), 0, 0])}
        for side, sign in (("l", 1), ("r", -1)):
            if any(b["name"] == f"upperarm_{side}" for b in rig["bones"]):
                tracks[f"upperarm_{side}"] = {"rot": _keys(f, lambda t, sg=sign: [0, sg * (down + 2 * s * math.sin(tw * t)), 0])}
        return tracks, f, True
    if kind == "wave":
        _need(rig, "upperarm_r", "lowerarm_r")
        f, s = int(p.get("frames") or 40), a or 1.0
        tracks = {"upperarm_r": {"rot": _keys(f, lambda t: [0, 60, 0], 1)},
                  "lowerarm_r": {"rot": _keys(f, lambda t: [0, 40 + 25 * s * math.sin(2 * tw * t), 0], 8)}}
        if any(b["name"] == "upperarm_l" for b in rig["bones"]):
            tracks["upperarm_l"] = {"rot": _keys(f, lambda t: [0, float(p.get("arms_down", 70)), 0], 1)}
        return tracks, f, True
    if kind == "sway":
        names = [str(b) for b in (p.get("bones") or [])] or _chain_from(rig, bone)
        for n in names:
            find_bone(rig, n)
        f, amp = int(p.get("frames") or 48), a or 15.0
        phase = float(p.get("phase", 0.6))
        axis = str(p.get("axis") or "x")
        return {n: {"rot": _keys(f, lambda t, i=i: _axis_vec(axis, amp * math.sin(tw * t - i * phase)), 8)}
                for i, n in enumerate(names)}, f, True
    find_bone(rig, bone)
    axis = str(p.get("axis") or "")
    if kind == "spin":
        f, turns = int(p.get("frames") or 60), (a or 1.0)
        steps = max(4, int(round(abs(turns) * 4)))
        return {bone: {"rot": _keys(f, lambda t: _axis_vec(axis or "z", 360 * turns * t), steps, "linear")}}, f, True
    if kind == "bob":
        f, amp = int(p.get("frames") or 60), a or 20.0
        return {bone: {"loc": _keys(f, lambda t: _axis_vec(axis or "z", amp * (1 - math.cos(tw * t)) / 2))}}, f, True
    if kind == "swing":
        f, amp = int(p.get("frames") or 48), a or 30.0
        return {bone: {"rot": _keys(f, lambda t: _axis_vec(axis or "x", amp * math.cos(tw * t)))}}, f, True
    if kind == "open":
        f, amp = int(p.get("frames") or 30), a or 90.0
        return {bone: {"rot": _keys(f, lambda t: _axis_vec(axis or "z", amp * t), 1)}}, f, False
    raise ValueError(f"kind is one of {', '.join(KINDS)}")


def op_anim(scene, p: dict) -> dict:
    action = str(p.get("action") or "generate")
    rig = find_rig(scene, str(p.get("rig") or ""))
    clips = rig.setdefault("actions", [])
    if action in ("new", "generate") and not (action == "generate" and p.get("clip")):
        name = str(p.get("new_name") or p.get("clip_name") or (str(p.get("kind") or "Clip").title() if action == "generate" else "Clip"))
        taken = {c["name"] for c in clips}
        base, n = name, 2
        while name in taken:
            name, n = f"{base}_{n}", n + 1
        clip = {"id": _nid("a"), "name": name, "fps": int(p.get("fps") or 30), "frames": int(p.get("frames") or 60),
                "loop": True if p.get("loop") is None else bool(p["loop"]), "tracks": {}}
        clips.append(clip)
        if action == "new":
            scene.touch_geom()
            return {"summary": f"New clip {name}", "ids": [rig["id"]], "id": clip["id"], "clip": clip["id"]}
    else:
        clip = find_clip(rig, str(p.get("clip") or ""))
    if action == "generate":
        tracks, frames, loop = generate(rig, str(p.get("kind") or ""), p)
        for bone, channels in tracks.items():  # per channel, so bob (loc) then spin (rot) share one bone
            clip.setdefault("tracks", {}).setdefault(bone, {}).update(channels)
        clip["frames"] = max(1, frames)
        clip["loop"] = loop if p.get("loop") is None else bool(p["loop"])
        if p.get("fps"):
            clip["fps"] = int(p["fps"])
        scene.touch_geom()
        return {"summary": f"{str(p.get('kind')).title()} on {clip['name']}", "ids": [rig["id"]], "id": clip["id"],
                "clip": clip["id"], "bones": sorted(tracks), "frames": clip["frames"]}
    if action == "key":
        rows = list(p.get("keys") or []) or [{k: p.get(k) for k in ("bone", "frame", "rot", "loc", "scale", "interp", "shape", "value")}]
        count = 0
        for row in rows:
            frame = float(row.get("frame") or 0)
            if not math.isfinite(frame) or frame < 0:
                raise ValueError("frame must be 0 or more")
            interp = str(row.get("interp") or "smooth")
            if interp not in INTERPS:
                raise ValueError(f"interp is one of {', '.join(INTERPS)}")
            clip["frames"] = max(int(clip.get("frames") or 1), int(math.ceil(frame)))
            if row.get("shape"):  # shape-key weight, 0..1, ships as a morph target curve
                value = float(row.get("value") if row.get("value") is not None else 1.0)
                if not math.isfinite(value):
                    raise ValueError("shape value must be a number")
                _upsert(clip.setdefault("morphs", {}).setdefault(_bone_name(row["shape"]), []), frame, [value], interp)
                count += 1
                continue
            bone = find_bone(rig, str(row.get("bone") or ""))["name"]
            track = clip.setdefault("tracks", {}).setdefault(bone, {})
            for channel in CHANNELS:
                if row.get(channel) is not None:
                    _upsert(track.setdefault(channel, []), frame, _vec(row[channel], channel), interp)
                    count += 1
        if not count:
            raise ValueError("key needs rot, loc, scale, or shape + value")
        scene.touch_geom()
        return {"summary": f"Key {count} on {clip['name']}", "ids": [rig["id"]], "clip": clip["id"]}
    if action == "ik":
        effector = find_bone(rig, str(p.get("bone") or ""))["name"]
        target = _vec(p.get("target"), "target")
        span = p.get("span") or [p.get("frame") or 0]
        first, last = int(span[0]), int(span[-1])
        if first < 0 or last < first:
            raise ValueError("span is [first, last] with first <= last")
        bones = export_bones(rig)
        pole = _vec(p["pole"], "pole") if p.get("pole") else None
        keep = True if p.get("keep_rotation") is None else bool(p["keep_rotation"])
        for f in range(first, last + 1):
            for bone, rot in solve_ik(bones, clip, f, effector, target, int(p.get("chain") or 2), pole, keep).items():
                _upsert(clip.setdefault("tracks", {}).setdefault(bone, {}).setdefault("rot", []), float(f), rot, str(p.get("interp") or "smooth"))
        clip["frames"] = max(int(clip.get("frames") or 1), last)
        scene.touch_geom()
        return {"summary": f"IK {effector} to {[round(c, 1) for c in target]} on {last - first + 1} frame(s)", "ids": [rig["id"]], "clip": clip["id"]}
    if action == "unkey" and p.get("shape"):
        morphs = clip.get("morphs") or {}
        name = str(p["shape"])
        if p.get("frame") is None:
            morphs.pop(name, None)
        elif name in morphs:
            morphs[name] = [k for k in morphs[name] if abs(k[0] - float(p["frame"])) >= 1e-6]
        scene.touch_geom()
        return {"summary": f"Remove {name} keys", "ids": [rig["id"]], "clip": clip["id"]}
    if action == "unkey":
        track = (clip.get("tracks") or {}).get(str(p.get("bone") or ""))
        if track is None:
            return {"summary": "Nothing to remove", "ids": [rig["id"]]}
        channels = [str(p["channel"])] if p.get("channel") else list(CHANNELS)
        for channel in channels:
            if p.get("frame") is None:
                track.pop(channel, None)
            elif channel in track:
                track[channel] = [k for k in track[channel] if abs(k[0] - float(p["frame"])) >= 1e-6]
        if not any(track.values()):
            clip["tracks"].pop(str(p.get("bone")), None)
        scene.touch_geom()
        return {"summary": "Remove keys", "ids": [rig["id"]], "clip": clip["id"]}
    if action == "edit":
        if p.get("new_name"):
            clip["name"] = str(p["new_name"])
        for key in ("fps", "frames"):
            if p.get(key):
                clip[key] = max(1, int(p[key]))
        if p.get("loop") is not None:
            clip["loop"] = bool(p["loop"])
        scene.touch_geom()
        return {"summary": f"Edit clip {clip['name']}", "ids": [rig["id"]], "clip": clip["id"]}
    if action == "delete":
        rig["actions"] = [c for c in clips if c is not clip]
        scene.touch_geom()
        return {"summary": f"Delete clip {clip['name']}", "ids": [rig["id"]]}
    raise ValueError("anim action is generate, new, key, unkey, edit, or delete")


# ---- read side -------------------------------------------------------------


def skinned_part(scene, rig: dict, nodes: list[dict]) -> dict:
    """One combined world-space mesh of the bound nodes with per-vertex joints and weights."""
    bones = export_bones(rig)
    mesh, joints, weights = Mesh(), [], []
    for node in nodes:
        part = scene.world_mesh(node)
        j, w = bind_weights(part.verts, bones, node["skin"], part.attrs)
        mesh.append(part)
        joints += j
        weights += w
    mesh.smooth = any(scene.base_mesh(n).smooth for n in nodes)
    return {"mesh": mesh, "joints": joints, "weights": weights, "bones": bones, "clips": rig.get("actions") or []}


def morph_weights(clip: dict | None, names: list[str], frame: float) -> list[float]:
    """Shape-key weights at a frame; keys a clip never mentions stay 0."""
    morphs = (clip or {}).get("morphs") or {}
    out = []
    for name in names:
        v = _sample(morphs.get(name), frame, "loc")
        out.append(float(v[0]) if v else 0.0)
    return out


def apply_morphs(mesh: Mesh, clip: dict | None, frame: float) -> None:
    names = [n[3:] for n in mesh.attrs if n.startswith("sk:")]
    for name, w in zip(names, morph_weights(clip, names, frame)):
        if w:
            for v, d in zip(mesh.verts, mesh.attrs["sk:" + name]):
                v[0] += d[0] * w
                v[1] += d[1] * w
                v[2] += d[2] * w


def posed_meshes(scene, nodes: list[dict], rig: dict, clip: dict, frame: float) -> Mesh:
    """World meshes of nodes combined; the ones bound to `rig` are deformed to `frame`: shape keys, then bones."""
    bones = export_bones(rig)
    mats = skin_matrices(bones, clip, frame)
    out = Mesh()
    for node in nodes:
        mesh = scene.world_mesh(node)
        skin = node.get("skin") or {}
        if skin.get("rig") == rig["id"]:
            joints, weights = bind_weights(mesh.verts, bones, skin, mesh.attrs)
            apply_morphs(mesh, clip, frame)
            mesh = deform(mesh, joints, weights, mats)
        out.append(mesh)
    return out


def _b64(kind: str, values) -> str:
    return base64.b64encode(array.array(kind, values).tobytes()).decode("ascii")


def op_preview(scene, p: dict) -> dict:
    """Everything the dock needs to play a clip: rest geometry, weights, per-frame skin matrices."""
    rig = find_rig(scene, str(p.get("rig") or ""))
    bones = export_bones(rig)
    clip = None
    if p.get("clip") or len(rig.get("actions") or []) == 1:
        clip = find_clip(rig, str(p.get("clip") or ""))
    frames = int(clip["frames"]) if clip else 0
    mats = array.array("f")
    for f in range(frames + 1):
        for m in skin_matrices(bones, clip, f):
            mats.extend(m[r][c] for c in range(4) for r in range(4))  # column-major for THREE.Matrix4
    objects = []
    hidden = {layer["id"] for layer in scene.data.get("layers", []) if layer.get("visible") is False}
    meshes = []
    for node in bound_objects(scene, rig):
        if node.get("visible") is False or node.get("layer") in hidden:
            continue
        meshes.append((node, scene.world_mesh(node)))
    names = sorted({n[3:] for _, mesh in meshes for n in mesh.attrs if n.startswith("sk:")})
    uv_mats = scene.uv_materials()
    for node, mesh in meshes:
        joints, weights = bind_weights(mesh.verts, bones, node["skin"], mesh.attrs)
        tris = mesh.triangles()
        zero = [0.0, 0.0, 0.0]
        if node.get("material") in uv_mats:  # per-corner UVs so textures and blend layers show while it moves
            corner = [u for a, b, c, fi in tris for u in mesh.tri_uv(fi, (a, b, c))]
            blend = mesh.attrs.get("col:blend")
            objects.append({"ub": _b64("f", corner)})
            if blend:
                objects[-1]["cb"] = _b64("f", (c for rgba in blend for c in rgba[:3]))
        else:
            objects.append({})
        objects[-1].update({
            "id": node["id"], "material": node.get("material") or "", "smooth": bool(scene.base_mesh(node).smooth),
            "pb": _b64("f", (c for v in mesh.verts for c in v)),
            "ib": _b64("I", (i for t in tris for i in t[:3])),
            "jb": _b64("H", (j for row in joints for j in row)),
            "wb": _b64("f", (w for row in weights for w in row)),
            # Shape-key deltas, key by key, each len(verts) * 3, zeros where this object lacks the key.
            "mb": _b64("f", (c for name in names for d in (mesh.attrs.get("sk:" + name) or [zero] * len(mesh.verts)) for c in d)),
        })
    weights_by_frame = [w for f in range(frames + 1) for w in morph_weights(clip, names, f)]
    return {"summary": "rig preview", "ids": [], "rig": {"id": rig["id"], "name": rig["name"], "bones": bones},
            "clip": {k: clip[k] for k in ("id", "name", "fps", "frames", "loop")} if clip else None,
            "matrices": base64.b64encode(mats.tobytes()).decode("ascii"), "objects": objects,
            "morphs": names, "morph_weights": _b64("f", weights_by_frame)}


def issues(scene, rig: dict) -> list[dict]:
    out = []

    def add(kind, message, severity="warn"):
        out.append({"id": kind, "severity": severity, "message": f"{rig['name']}: {message}", "object": rig["id"],
                    "elements": {}, "fix": None})

    if not rig["bones"]:
        add("rig_empty", "no bones", "error")
        return out
    try:
        bones = export_bones(rig)
    except ValueError as exc:
        add("rig_cycle", str(exc), "error")
        return out
    names = {b["name"] for b in bones}
    if len(bones) > len(rig["bones"]):
        add("rig_roots", "several root bones; Send adds one root bone above them", "info")
    bound = bound_objects(scene, rig)
    if not bound:
        add("rig_unbound", "no objects bound; forge_rig(action='bind') before Send")
    for node in bound:
        skin = node["skin"]
        if skin.get("bone") and skin["bone"] not in names:
            add("rig_bone_missing", f"{node.get('name')} is bound to missing bone {skin['bone']}", "error")
        if any(m.get("type") == "array" for m in node.get("modifiers") or []):
            add("rig_array", f"{node.get('name')} has an array modifier; each copy deforms as one mesh")
    for clip in rig.get("actions") or []:
        for bone, track in (clip.get("tracks") or {}).items():
            if bone not in names:
                add("clip_bone_missing", f"{clip['name']} keys missing bone {bone}", "error")
            late = [k[0] for keys in track.values() for k in keys if k[0] > clip["frames"]]
            if late:
                add("clip_late_keys", f"{clip['name']} has keys after its last frame ({clip['frames']})")
        if clip.get("loop"):
            first = skin_matrices(bones, clip, 0)
            last = skin_matrices(bones, clip, clip["frames"])
            drift = max(abs(a[i][j] - b[i][j]) for a, b in zip(first, last) for i in range(3) for j in range(4))
            if drift > 0.5:
                add("clip_loop_pop", f"{clip['name']} loops but its last frame differs from frame 0; key the end to match")
    return out


def merge_rigs(src_rigs: list, node_map: dict, offset: list) -> list[dict]:
    """Copy rigs from another scene with fresh ids, moved by offset. Bindings remap through node_map."""
    out = []
    for rig in src_rigs or []:
        copy_rig = copy.deepcopy(rig)
        copy_rig["id"] = _nid("r")
        for bone in copy_rig["bones"]:
            bone["head"] = [bone["head"][i] + offset[i] for i in range(3)]
            bone["tail"] = [bone["tail"][i] + offset[i] for i in range(3)]
        for clip in copy_rig.get("actions") or []:
            clip["id"] = _nid("a")
        for node in node_map.values():
            if (node.get("skin") or {}).get("rig") == rig["id"]:
                node["skin"]["rig"] = copy_rig["id"]
        out.append(copy_rig)
    return out
