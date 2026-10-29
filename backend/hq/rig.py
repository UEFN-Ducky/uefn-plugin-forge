"""HQ rig: skeleton templates, auto weights, IK poses, a clip library and ARKit face shapes.

Same bone names as the UE5 Manny everywhere, so clips and retargeting work on every template.
Rotations are Forge degrees; the engine only does what needs bpy (bone-heat weights).
"""
import copy
import difflib
import json
import math
import re
from pathlib import Path

from .. import rig as rg
from ..transforms import decompose
from . import engine, op
from .qa import loop_seam

_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "rigs"
TEMPLATES = ("ue5_manny", "quadruped", "bird", "tail_chain")
CLIP_KINDS = ("walk", "run", "jump", "attack", "hit", "death", "idle")
STYLES = {"heavy": (1.2, 1.25), "light": (0.9, 0.85), "cartoony": (1.0, 1.6)}  # (time scale, amplitude)
_TAU = math.tau


def _pick(name, choices, what):
    if name in choices:
        return name
    close = difflib.get_close_matches(str(name), choices, n=1)
    hint = " Did you mean %r?" % close[0] if close else ""
    raise ValueError("No %s %r.%s Options: %s" % (what, name, hint, ", ".join(choices)))


def _targets(scene, p):
    ids = [i for i in (p.get("ids") or ([p["id"]] if p.get("id") else [])) if i]
    return rg._objects_under(scene, ids or rg._ids(scene, p))


# ---- r-ueskel: templates -----------------------------------------------------


def _spec(template):
    data = json.loads((_ASSETS / (template + ".json")).read_text(encoding="utf-8"))
    rows = []
    for name, parent, head, tail in data["bones"]:
        rows.append((name, parent, head, tail, 1))
        if name.endswith("_l"):
            rows.append((name[:-2] + "_r", parent[:-2] + "_r" if parent and parent.endswith("_l") else parent, head, tail, -1))
    return data["frame"], rows


def fit(template, lo, hi):
    """Bones of a template placed on the bounds lo..hi."""
    frame, rows = _spec(template)
    size = [hi[i] - lo[i] for i in range(3)]
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    h = size[2] or 180.0
    arm = (size[0] / 2) / (0.48 * h) if frame == "height" and size[0] > 0.7 * h else 1.0  # T-pose: reach the fingertips

    def at(f, sign, k=1.0):
        if frame == "height":
            return [round(cx + sign * f[0] * h * k, 3), round(cy + f[1] * h, 3), round(lo[2] + f[2] * h, 3)]
        x = f[0] if sign > 0 else 1 - f[0]
        return [round(lo[0] + x * size[0], 3), round(lo[1] + f[1] * size[1], 3), round(lo[2] + f[2] * size[2], 3)]

    def stretch(name):  # only arm and finger bones follow a wide T-pose
        return arm if name.split("_")[0] in ("clavicle", "upperarm", "lowerarm", "hand", "thumb", "index", "middle", "ring", "pinky") else 1.0

    return [{"name": n, "parent": par, "head": at(hd, s, stretch(n)), "tail": at(tl, s, stretch(n))} for n, par, hd, tl, s in rows]


def _tail_chain(lo, hi, count):
    axis = max(range(3), key=lambda i: hi[i] - lo[i])
    start = [(lo[i] + hi[i]) / 2 for i in range(3)]
    end = list(start)
    start[axis], end[axis] = lo[axis], hi[axis]
    return rg._chain(start, end, count, "tail", None)


def _retarget(bones, landmarks):
    """Move bone heads onto landmark points, dragging the parent's tail along when it met that head."""
    by = {b["name"]: b for b in bones}
    for name, point in landmarks.items():
        if name not in by:
            raise ValueError("No bone %r to place. Bones: %s" % (name, ", ".join(sorted(by)[:12])))
        bone = by[name]
        old, new = bone["head"], rg._vec(point, name)
        parent = by.get(bone.get("parent"))
        if parent and math.dist(parent["tail"], old) < 1e-3:
            parent["tail"] = list(new)
        bone["head"] = list(new)


@op("rig_template", "rig_template(template='ue5_manny'|'quadruped'|'bird'|'tail_chain', ids, landmarks={bone:[x,y,z]}, bind=true, count=8)")
def rig_template(scene, p):
    kind = _pick(str(p.get("template") or "ue5_manny"), TEMPLATES, "rig template")
    nodes = _targets(scene, p) or [n for n in scene.data["nodes"] if n.get("type") == "object"]
    box = rg._bounds(scene, nodes)
    lo, hi = box or ([-40.0, -15.0, 0.0], [40.0, 15.0, 180.0])
    bones = _tail_chain(lo, hi, int(p.get("count") or 8)) if kind == "tail_chain" else fit(kind, lo, hi)
    _retarget(bones, dict(p.get("landmarks") or {}))
    rigs = scene.data.setdefault("rigs", [])
    rig = rg.find_rig(scene, str(p["rig"])) if p.get("rig") else {"id": rg._nid("r"), "name": kind, "bones": [], "actions": []}
    rg._add_bones(rig, bones)
    if rig not in rigs:
        rigs.append(rig)
    if p.get("bind", True) and kind != "tail_chain":
        for node in nodes:
            node["skin"] = {"rig": rig["id"], "auto": True}
    scene.touch_geom()
    return {"summary": "%s: %d bones%s" % (kind, len(bones), ", bound %d" % len(nodes) if nodes and p.get("bind", True) else ""),
            "ids": [rig["id"]], "id": rig["id"], "bones": len(bones)}


# ---- r-weights ----------------------------------------------------------------


@op("auto_weights", "auto_weights(ids, target='uefn'(4 influences)|'ue5'(8), smooth=1, clean=0.01) bone-heat skin weights", long=False)
def auto_weights(scene, p):
    nodes = _targets(scene, p)
    if not nodes:
        raise ValueError("auto_weights needs ids of the meshes to skin")
    rigs = scene.data.get("rigs") or []
    for node in nodes:
        if not (node.get("skin") or {}).get("rig"):
            if len(rigs) != 1:
                raise ValueError("%s has no rig. Run rig_template first (or pass skin on the object)." % node.get("name"))
            node["skin"] = {"rig": rigs[0]["id"], "auto": True}
    target = str(p.get("target") or "uefn").lower()
    limit = int(p.get("limit") or (8 if target == "ue5" else 4))
    result, changed = engine(scene, "auto_weights", [n["id"] for n in nodes],
                             {"limit": limit, "smooth": int(p.get("smooth", 1)), "clean": float(p.get("clean", 0.01))})
    return {"ids": changed, "summary": "Weights on %d mesh(es), max %d influences (heat %d, fallback %d verts)" % (
        len(nodes), limit, result.get("heat", 0), result.get("fallback", 0)), "tris": result.get("tris", 0)}


# ---- r-ctrl: pose --------------------------------------------------------------


def _world_head(bones, clip, frame, name):
    index = {b["name"]: i for i, b in enumerate(bones)}
    m = rg.skin_matrices(bones, clip, frame)[index[name]]
    h = bones[index[name]]["head"]
    return [sum(m[i][k] * h[k] for k in range(3)) + m[i][3] for i in range(3)]


def _clip_of(rig, p, default):
    if p.get("clip"):
        return rg.find_clip(rig, str(p["clip"]))
    for c in rig.setdefault("actions", []):
        if c["name"] == default:
            return c
    clip = {"id": rg._nid("a"), "name": default, "fps": 30, "frames": 1, "loop": False, "tracks": {}}
    rig["actions"].append(clip)
    return clip


@op("pose", "pose(rig, clip, frame, controls={hand_l:{ik:[x,y,z],pole,chain}, foot_r:{pin:true}, spine:{curve:.2}}) -> FK keys")
def pose(scene, p):
    rig = rg.find_rig(scene, str(p.get("rig") or ""))
    clip = _clip_of(rig, p, "Pose")
    frame = float(p.get("frame") or 0)
    bones = rg.export_bones(rig)
    controls = dict(p.get("controls") or {})
    if not controls:
        raise ValueError("pose needs controls, e.g. {'hand_l': {'ik': [30, -40, 120]}, 'foot_r': {'pin': True}, 'spine': {'curve': 0.2}}")
    pins = {n: _world_head(bones, None, 0, n) for n, c in controls.items() if c.get("pin")}  # rest position, before any key moves
    keyed = []
    spine = controls.pop("spine", None)
    if spine is not None:
        chain = sorted(b["name"] for b in rig["bones"] if b["name"].startswith("spine_"))
        if not chain:
            raise ValueError("No spine_NN bones for the spine control. Bones: %s" % ", ".join(b["name"] for b in rig["bones"][:12]))
        each = float(spine.get("curve", 0)) * 90.0 / len(chain)  # curve 1 = 90 degrees forward over the whole spine
        for n in chain:
            rg._upsert(clip.setdefault("tracks", {}).setdefault(n, {}).setdefault("rot", []), frame, [each, 0.0, 0.0], "smooth")
            keyed.append(n)
    for name, spec in controls.items():
        rg.find_bone(rig, name)
        target = pins[name] if spec.get("pin") else spec.get("ik")
        if target is None:
            raise ValueError("%s needs ik:[x,y,z] or pin:true" % name)
        pole = rg._vec(spec["pole"], "pole") if spec.get("pole") else None
        keep = name.startswith(("foot", "ball"))  # planted feet stay flat, hands follow the reach
        for n, rot in rg.solve_ik(bones, clip, frame, name, rg._vec(target, "ik"), int(spec.get("chain") or 2), pole, keep).items():
            rg._upsert(clip.setdefault("tracks", {}).setdefault(n, {}).setdefault("rot", []), frame, rot, "smooth")
            keyed.append(n)
    clip["frames"] = max(int(clip.get("frames") or 1), int(math.ceil(frame)))
    scene.touch_geom()
    return {"summary": "Pose on %s at frame %g: %d bones keyed" % (clip["name"], frame, len(set(keyed))), "ids": [rig["id"]], "clip": clip["id"]}


# ---- r-clips -------------------------------------------------------------------


def _horizontal(rig, name):
    """+-1 sign of x when the bone lies sideways (a T-pose arm), else 0."""
    b = next((b for b in rig["bones"] if b["name"] == name), None)
    if b is None:
        return 0
    dx, dz = b["tail"][0] - b["head"][0], b["tail"][2] - b["head"][2]
    return (1 if dx > 0 else -1) if abs(dx) > abs(dz) else 0


def _pts(frames, pts, interp="smooth"):
    return [[round(frames * t, 3), [round(c, 4) for c in v], interp] for t, v in pts]


class _Clip:
    def __init__(self, rig, f, s, bone_map):
        self.rig, self.f, self.s = rig, f, s
        self.names = {b["name"] for b in rig["bones"]}
        self.map = bone_map
        self.h = rg.rig_height(rig)
        self.tracks = {}
        roots = [b["name"] for b in rg.ordered(rig) if b.get("parent") is None]
        self.root = bone_map.get("root") or (roots[0] if roots else "")
        self.spine = sorted(n for n in self.names if n.startswith("spine_"))

    def bone(self, role):
        name = self.map.get(role, role)
        return name if name in self.names else None

    def put(self, role, channel, keys):
        name = self.bone(role)
        if name:
            self.tracks.setdefault(name, {})[channel] = keys

    def limb(self, upper, lower, side, swing, flex, down=True):
        """Swing a two-bone limb about X; flex bends the lower bone (about Z for sideways arms, X for legs)."""
        u, lo = self.bone("%s_%s" % (upper, side)), self.bone("%s_%s" % (lower, side))
        sg = _horizontal(self.rig, u) if u else 0
        if u:
            self.tracks.setdefault(u, {})["rot"] = [[k[0], rg.euler_of([sw, 0, 0], [0, sg * 70.0 * down, 0]) if sg else [sw, 0, 0], k[2]]
                                                  for k, sw in ((k, k[1][0]) for k in swing)]
        if lo:
            self.tracks.setdefault(lo, {})["rot"] = [[k[0], [0, 0, -sg * k[1][0]] if sg else [k[1][0], 0, 0], k[2]] for k in flex]


def _run(c, travel):
    f, s, h = c.f, c.s, c.h
    n = 8
    c.put("pelvis", "loc", rg._keys(f, lambda t: [0, 0, 0.03 * h * s * (1 - math.cos(2 * _TAU * t)) / 2], n))
    c.put("pelvis", "rot", rg._keys(f, lambda t: [8 * s, 0, 6 * s * math.cos(_TAU * t)], n))
    for i, name in enumerate(c.spine[:2]):
        c.put(name, "rot", rg._keys(f, lambda t: [4 * s, 0, -4 * s * math.cos(_TAU * t)], n))
    for side, ph in (("l", 0.0), ("r", 0.5)):
        c.limb("thigh", "calf", side,
               rg._keys(f, lambda t, ph=ph: [-55 * s * math.cos(_TAU * (t + ph)), 0, 0], n),
               rg._keys(f, lambda t, ph=ph: [10 * s + 90 * s * max(0.0, -math.sin(_TAU * (t + ph))), 0, 0], n))
        c.limb("upperarm", "lowerarm", side,
               rg._keys(f, lambda t, ph=ph: [45 * s * math.cos(_TAU * (t + ph)), 0, 0], n),
               rg._keys(f, lambda t: [60 * s, 0, 0], n))
    if travel and c.root:
        c.tracks.setdefault(c.root, {})["loc"] = rg._keys(f, lambda t: [0, -1.4 * h * s * t, 0], 1, "linear")
    return f, True


def _walk(c, travel):
    """Biped: legs half a cycle apart, arms against them. Four legs (a tail_* chain, front legs on
    upperarm/lowerarm): the four-beat walk, back-left, front-left, back-right, front-right."""
    f, s, h = c.f, c.s, c.h
    n = 8
    four = any(name.startswith("tail_") for name in c.names) and c.bone("upperarm_l") is not None
    c.put("pelvis", "loc", rg._keys(f, lambda t: [0, 0, 0.012 * h * s * (1 - math.cos(2 * _TAU * t)) / 2], n))
    c.put("pelvis", "rot", rg._keys(f, lambda t: [0, 0, 3 * s * math.cos(_TAU * t)], n))
    c.put("head", "rot", rg._keys(f, lambda t: [3 * s * math.sin(2 * _TAU * t), 0, 0], n))
    if four:
        phase = {("thigh", "l"): 0.0, ("upperarm", "l"): 0.25, ("thigh", "r"): 0.5, ("upperarm", "r"): 0.75}
        for (upper, side), ph in phase.items():
            lower = "calf" if upper == "thigh" else "lowerarm"
            c.limb(upper, lower, side,
                   rg._keys(f, lambda t, ph=ph: [-24 * s * math.cos(_TAU * (t + ph)), 0, 0], n),
                   rg._keys(f, lambda t, ph=ph: [6 * s + 38 * s * max(0.0, -math.sin(_TAU * (t + ph))), 0, 0], n))
        for i, name in enumerate(sorted(n_ for n_ in c.names if n_.startswith("tail_"))):
            c.put(name, "rot", rg._keys(f, lambda t, i=i: [0, 0, (6 + 4 * i) * s * math.sin(_TAU * t - 0.6 * i)], n))
    else:
        for side, ph in (("l", 0.0), ("r", 0.5)):
            c.limb("thigh", "calf", side,
                   rg._keys(f, lambda t, ph=ph: [-28 * s * math.cos(_TAU * (t + ph)), 0, 0], n),
                   rg._keys(f, lambda t, ph=ph: [5 * s + 45 * s * max(0.0, -math.sin(_TAU * (t + ph))), 0, 0], n))
            c.limb("upperarm", "lowerarm", side,
                   rg._keys(f, lambda t, ph=ph: [18 * s * math.cos(_TAU * (t + ph)), 0, 0], n),
                   rg._keys(f, lambda t: [15 * s, 0, 0], n))
    if travel and c.root:
        c.tracks.setdefault(c.root, {})["loc"] = rg._keys(f, lambda t: [0, -0.6 * h * s * t, 0], 1, "linear")
    return f, True


def _idle(c, travel):
    f, s, h = c.f, c.s, c.h
    c.put("pelvis", "loc", rg._keys(f, lambda t: [0, 0, -0.004 * h * s * (1 - math.cos(_TAU * t)) / 2]))
    for name in c.spine[-1:]:
        c.put(name, "rot", rg._keys(f, lambda t: [-1.5 * s * math.sin(_TAU * t), 0, 0]))
    c.put("head", "rot", rg._keys(f, lambda t: [1.5 * s * math.sin(_TAU * t - 0.6), 0, 0]))
    for side in ("l", "r"):
        c.limb("upperarm", "lowerarm", side, rg._keys(f, lambda t: [2 * s * math.sin(_TAU * t), 0, 0]), rg._keys(f, lambda t: [8 * s, 0, 0]))
    return f, True


def _jump(c, travel):
    f, s, h = c.f, c.s, c.h
    z = lambda frac: [0, 0, frac * h * s]
    c.put("pelvis", "loc", _pts(f, [(0, z(0)), (.2, z(-.08)), (.33, z(.02)), (.5, z(.22)), (.75, z(0)), (.85, z(-.06)), (1, z(0))]))
    c.put("pelvis", "rot", _pts(f, [(0, [0, 0, 0]), (.2, [14 * s, 0, 0]), (.5, [-4 * s, 0, 0]), (.85, [10 * s, 0, 0]), (1, [0, 0, 0])]))
    for side in ("l", "r"):
        c.limb("thigh", "calf", side,
               _pts(f, [(0, [0, 0, 0]), (.2, [-55 * s, 0, 0]), (.33, [8 * s, 0, 0]), (.5, [-40 * s, 0, 0]), (.75, [-10 * s, 0, 0]), (.85, [-50 * s, 0, 0]), (1, [0, 0, 0])]),
               _pts(f, [(0, [0, 0, 0]), (.2, [85 * s, 0, 0]), (.33, [5 * s, 0, 0]), (.5, [65 * s, 0, 0]), (.75, [15 * s, 0, 0]), (.85, [80 * s, 0, 0]), (1, [0, 0, 0])]))
        c.limb("upperarm", "lowerarm", side,
               _pts(f, [(0, [0, 0, 0]), (.2, [30 * s, 0, 0]), (.4, [-70 * s, 0, 0]), (.85, [20 * s, 0, 0]), (1, [0, 0, 0])]),
               _pts(f, [(0, [0, 0, 0]), (.5, [30 * s, 0, 0]), (1, [0, 0, 0])]))
    if travel and c.root:
        c.tracks.setdefault(c.root, {})["loc"] = _pts(f, [(0, [0, 0, 0]), (.33, [0, 0, 0]), (.75, [0, -0.6 * h, 0]), (1, [0, -0.6 * h, 0])], "linear")
    return f, False


def _attack(c, travel):
    f, s, h = c.f, c.s, c.h
    for name in c.spine[-2:]:
        c.put(name, "rot", _pts(f, [(0, [0, 0, 0]), (.3, [-6 * s, 0, 25 * s]), (.5, [10 * s, 0, -30 * s]), (1, [0, 0, 0])]))
    c.put("pelvis", "rot", _pts(f, [(0, [0, 0, 0]), (.3, [0, 0, -10 * s]), (.5, [4 * s, 0, 14 * s]), (1, [0, 0, 0])]))
    c.put("head", "rot", _pts(f, [(0, [0, 0, 0]), (.3, [-8 * s, 0, 0]), (.5, [10 * s, 0, 0]), (1, [0, 0, 0])]))
    c.limb("upperarm", "lowerarm", "r",
           _pts(f, [(0, [0, 0, 0]), (.3, [70 * s, 0, 0]), (.5, [-95 * s, 0, 0]), (.7, [-80 * s, 0, 0]), (1, [0, 0, 0])]),
           _pts(f, [(0, [0, 0, 0]), (.3, [90 * s, 0, 0]), (.5, [10 * s, 0, 0]), (1, [0, 0, 0])]))
    c.put("thigh_l", "rot", _pts(f, [(0, [0, 0, 0]), (.3, [10 * s, 0, 0]), (.5, [-30 * s, 0, 0]), (1, [0, 0, 0])]))
    c.put("calf_l", "rot", _pts(f, [(0, [0, 0, 0]), (.5, [25 * s, 0, 0]), (1, [0, 0, 0])]))
    return f, False


def _hit(c, travel):
    f, s, h = c.f, c.s, c.h
    for name in c.spine:
        c.put(name, "rot", _pts(f, [(0, [0, 0, 0]), (.2, [-5 * s, 0, 3 * s]), (1, [0, 0, 0])]))
    c.put("head", "rot", _pts(f, [(0, [0, 0, 0]), (.2, [-14 * s, 0, 8 * s]), (1, [0, 0, 0])]))
    c.put("pelvis", "loc", _pts(f, [(0, [0, 0, 0]), (.2, [0, 0.03 * h * s, -0.015 * h * s]), (1, [0, 0, 0])]))
    return f, False


def _death(c, travel):
    f, s, h = c.f, c.s, c.h
    root = c.root
    if root:
        c.tracks.setdefault(root, {})["rot"] = _pts(f, [(0, [0, 0, 0]), (.2, [6, 0, 0]), (.75, [-90, 0, 0]), (1, [-90, 0, 0])])
    for side in ("l", "r"):
        c.put("thigh_" + side, "rot", _pts(f, [(0, [0, 0, 0]), (.5, [-25 * s, 0, 0]), (1, [-15 * s, 0, 0])]))
        c.put("calf_" + side, "rot", _pts(f, [(0, [0, 0, 0]), (.5, [40 * s, 0, 0]), (1, [20 * s, 0, 0])]))
    c.put("head", "rot", _pts(f, [(0, [0, 0, 0]), (.4, [-20 * s, 0, 10 * s]), (1, [-10 * s, 0, 14 * s])]))
    return f, False


_BUILD = {"walk": (_walk, 32), "run": (_run, 20), "idle": (_idle, 60), "jump": (_jump, 30), "attack": (_attack, 24), "hit": (_hit, 14), "death": (_death, 40)}


def make_clip(rig, kind, p):
    """A new clip dict for `kind` on any rig that follows the Manny bone names (override with map={role: bone})."""
    kind = _pick(str(kind or ""), CLIP_KINDS, "clip kind")
    style = p.get("style")
    time_k, amp = STYLES[_pick(str(style), tuple(STYLES), "style")] if style else (1.0, 1.0)
    speed = max(0.1, float(p.get("speed") or 1.0))
    build, base = _BUILD[kind]
    frames = max(2, int(round(base * time_k / speed)))
    c = _Clip(rig, frames, amp, dict(p.get("map") or {}))
    travel = bool(p.get("root_motion"))
    _, loop = build(c, travel)
    if not c.tracks:
        raise ValueError("No bones matched for %s. Pass map={'pelvis': '<bone>', 'thigh_l': ...} or run rig_template first." % kind)
    name = str(p.get("name") or "%s%s" % (kind.title(), "_" + style if style else ""))
    return {"id": rg._nid("a"), "name": name, "fps": 30, "frames": frames, "loop": loop, "tracks": c.tracks,
            "root_motion": travel and kind in ("walk", "run", "jump")}


@op("clip", "clip(kind='walk'|'run'|'jump'|'attack'|'hit'|'death'|'idle', speed=1, style='heavy'|'light'|'cartoony', root_motion=false, rig, map)")
def clip(scene, p):
    rig = rg.find_rig(scene, str(p.get("rig") or ""))
    made = make_clip(rig, p.get("kind"), p)
    taken = {c["name"] for c in rig.setdefault("actions", [])}
    base, n = made["name"], 2
    while made["name"] in taken:
        made["name"], n = "%s_%d" % (base, n), n + 1
    rig["actions"].append(made)
    scene.touch_geom()
    out = {"summary": "%s: %d frames%s%s" % (made["name"], made["frames"], ", loops" if made["loop"] else "", ", root motion" if made["root_motion"] else ""),
           "ids": [rig["id"]], "clip": made["id"], "frames": made["frames"]}
    if made["loop"] and p.get("loop_check", True):
        deg, cm = loop_seam(made)
        out["seam_deg"] = deg
        out["summary"] += ", seam %g deg %g cm" % (deg, cm)
    return out


def _add_clip(rig, clip):
    """Replace a clip of the same name, else append."""
    rig["actions"] = [c for c in rig.setdefault("actions", []) if c["name"] != clip["name"]] + [clip]
    return clip


@op("stress", "stress(rig?, map?) Stress clip for weight checks: arms up f10, elbows 140 f20, knees 150 f30, spine twist f40, "
              "rest f1/f50; then forge_render(clip='Stress') or look frames")
def stress(scene, p):
    rig = rg.find_rig(scene, str(p.get("rig") or ""))
    c = _Clip(rig, 50, 1.0, dict(p.get("map") or {}))
    zero = [0.0, 0.0, 0.0]

    def at(*pts):
        return [[float(f), list(v), "smooth"] for f, v in pts]

    for side in ("l", "r"):
        arm = c.bone("upperarm_" + side)
        sg = _horizontal(rig, arm) if arm else 0
        c.put("upperarm_" + side, "rot", at((1, zero), (10, [0, -sg * 80, 0] if sg else [-150, 0, 0]), (20, zero), (50, zero)))
        c.put("lowerarm_" + side, "rot", at((1, zero), (10, zero), (20, [0, 0, -sg * 140] if sg else [-140, 0, 0]), (30, zero), (50, zero)))
        c.put("thigh_" + side, "rot", at((1, zero), (20, zero), (30, [-90, 0, 0]), (40, zero), (50, zero)))
        c.put("calf_" + side, "rot", at((1, zero), (20, zero), (30, [150, 0, 0]), (40, zero), (50, zero)))
    for name in c.spine[-1:]:
        c.put(name, "rot", at((1, zero), (30, zero), (40, [0, 0, 45]), (50, zero)))
    if not c.tracks:
        raise ValueError("No arm, leg or spine bones matched. Pass map={'upperarm_l': '<bone>', ...} or use rig_template")
    made = _add_clip(rig, {"id": rg._nid("a"), "name": "Stress", "fps": 30, "frames": 50, "loop": False, "tracks": c.tracks})
    scene.touch_geom()
    return {"summary": "Stress: 50 frames, %d bones keyed; render frames 10 20 30 40" % len(c.tracks), "ids": [rig["id"]], "clip": made["id"]}


# ---- retarget and bake ---------------------------------------------------------------


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).split(":")[-1].lower())


# Mixamo / CMU / Blender / Unity humanoid names -> UE5 Manny names (keys normalised by _norm)
ALIAS = {"hips": "pelvis", "pelvis": "pelvis", "spine": "spine_01", "spine1": "spine_02", "spine2": "spine_03",
         "chest": "spine_03", "upperchest": "spine_04", "neck": "neck_01", "neck1": "neck_02", "head": "head"}
for _s, _side in (("l", "left"), ("r", "right")):
    for _src, _dst in (("shoulder", "clavicle"), ("collar", "clavicle"), ("arm", "upperarm"), ("upperarm", "upperarm"),
                       ("forearm", "lowerarm"), ("lowerarm", "lowerarm"), ("hand", "hand"), ("upleg", "thigh"),
                       ("upperleg", "thigh"), ("thigh", "thigh"), ("leg", "calf"), ("lowerleg", "calf"), ("shin", "calf"),
                       ("foot", "foot"), ("toebase", "ball"), ("toe", "ball"), ("toes", "ball")):
        ALIAS[_side + _src] = "%s_%s" % (_dst, _s)


def map_bones(src_names, dst_names, given=None):
    """{src bone: dst bone}: given map first, then the same name, then the alias table."""
    dst = {_norm(n): n for n in dst_names}
    out = {}
    for s in src_names:
        if given and s in given:
            out[s] = given[s]
        elif _norm(s) in dst:
            out[s] = dst[_norm(s)]
        elif ALIAS.get(_norm(s)) in dst_names:
            out[s] = ALIAS[_norm(s)]
    return out


_C = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]  # BVH Y-up, +Z front -> Forge Z-up, -Y front


def _mat(axis, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return {"X": [[1, 0, 0], [0, c, -s], [0, s, c]], "Y": [[c, 0, s], [0, 1, 0], [-s, 0, c]], "Z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _euler3(m):
    return [round(v, 3) for v in decompose([m[0] + [0.0], m[1] + [0.0], m[2] + [0.0], [0.0, 0.0, 0.0, 1.0]])["rot"]]


def parse_bvh(text):
    """(joints [{name, parent, offset, channels}], frame time, frames [[values]]). End Sites have name None."""
    tok = text.split()
    joints, stack, i = [], [], 0
    while i < len(tok) and tok[i] != "MOTION":
        t = tok[i]
        if t in ("ROOT", "JOINT", "End"):
            joints.append({"name": tok[i + 1] if t != "End" else None, "parent": stack[-1] if stack else None,
                           "offset": [0.0, 0.0, 0.0], "channels": []})
            i += 2
        elif t == "{":
            stack.append(len(joints) - 1)
            i += 1
        elif t == "}":
            stack.pop()
            i += 1
        elif t == "OFFSET":
            joints[stack[-1]]["offset"] = [float(x) for x in tok[i + 1:i + 4]]
            i += 4
        elif t == "CHANNELS":
            n = int(tok[i + 1])
            joints[stack[-1]]["channels"] = tok[i + 2:i + 2 + n]
            i += 2 + n
        else:
            i += 1
    if i >= len(tok):
        raise ValueError("Not a BVH file: no MOTION section")
    count, ft = int(tok[i + 2]), float(tok[i + 5])
    width = sum(len(j["channels"]) for j in joints)
    vals = [float(x) for x in tok[i + 6:i + 6 + count * width]]
    return joints, ft, [vals[f * width:(f + 1) * width] for f in range(count)]


def _leg(points):
    return sum(math.dist(points[k], points[k + 1]) for k in range(len(points) - 1))


def bvh_clip(text, rig, given=None, fps=30, name="Mocap"):
    """A Forge clip from BVH text. Rest poses must match (T-pose to T-pose); unmapped joints fold into their children."""
    joints, ft, frames = parse_bvh(text)
    names = [j["name"] for j in joints if j["name"]]
    bone_map = map_bones(names, [b["name"] for b in rig["bones"]], given)
    if not bone_map:
        raise ValueError("No BVH joints matched the rig. Pass map={'<bvh joint>': '<bone>'}")
    by = {j["name"]: k for k, j in enumerate(joints) if j["name"]}
    # leg ratio: target thigh->calf->foot over the source's, scales the hips travel
    src_leg = [n for n in names if bone_map.get(n) in ("thigh_l", "calf_l", "foot_l")]
    src_len = sum(math.dist([0, 0, 0], joints[by[n]]["offset"]) for n in src_leg[1:]) if len(src_leg) == 3 else 0
    bones = {b["name"]: b for b in rig["bones"]}
    dst_len = _leg([bones[n]["head"] for n in ("thigh_l", "calf_l", "foot_l")]) if all(n in bones for n in ("thigh_l", "calf_l", "foot_l")) else 0
    k = dst_len / src_len if src_len and dst_len else 1.0
    step = max(1, int(round(1.0 / (ft * fps)))) if ft > 0 else 1
    Ct = [list(r) for r in zip(*_C)]
    tracks = {}
    first = None
    for out_f, values in enumerate(frames[::step]):
        at, rots, pos = 0, {}, None
        for j, joint in enumerate(joints):
            r = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            for ch in joint["channels"]:
                v = values[at]
                at += 1
                if ch.endswith("rotation"):
                    r = _mm(r, _mat(ch[0].upper(), v))
                elif ch.endswith("position") and joint["parent"] is None:
                    pos = (pos or [0.0, 0.0, 0.0])
                    pos["XYZ".index(ch[0].upper())] = v
            rots[j] = r
        for j, joint in enumerate(joints):
            dst = bone_map.get(joint["name"])
            if not dst:
                continue
            r, up = rots[j], joint["parent"]
            while up is not None and not bone_map.get(joints[up]["name"]):  # fold unmapped parents in
                r = _mm(rots[up], r)
                up = joints[up]["parent"]
            rf = _mm(_mm(_C, r), Ct)
            tracks.setdefault(dst, {}).setdefault("rot", []).append([float(out_f), _euler3(rf), "linear"])
            if joint["parent"] is None and pos is not None:
                p = [sum(_C[a][b] * pos[b] for b in range(3)) for a in range(3)]
                first = first or p
                tracks[dst].setdefault("loc", []).append([float(out_f), [round((p[a] - first[a]) * k, 3) for a in range(3)], "linear"])
    count = max(1, len(frames[::step]) - 1)
    return {"id": rg._nid("a"), "name": name, "fps": fps, "frames": count, "loop": False, "tracks": tracks}, len(bone_map)


@op("retarget", "retarget(src=<clip name on another rig>|<file.bvh>, rig?, map?={src bone: bone}, name?) clip onto this rig: "
                "auto names (Manny, Mixamo, CMU), root travel scaled by leg ratio, BVH resampled to 30 fps")
def retarget(scene, p):
    rig = rg.find_rig(scene, str(p.get("rig") or ""))
    src = str(p.get("src") or "")
    given = dict(p.get("map") or {})
    if src.lower().endswith(".bvh"):
        path = Path(src).expanduser()
        if not path.is_file():
            raise ValueError("No BVH at %s" % path)
        made, mapped = bvh_clip(path.read_text(encoding="utf-8", errors="replace"), rig, given, name=str(p.get("name") or path.stem))
    else:
        owner = next((r for r in scene.data.get("rigs") or [] if r is not rig and any(c["name"] == src or c["id"] == src for c in r.get("actions") or [])), None)
        if owner is None:
            raise ValueError("retarget needs src=<clip name on another rig> or a .bvh path")
        clip = rg.find_clip(owner, src)
        bone_map = map_bones(list(clip.get("tracks") or {}), [b["name"] for b in rig["bones"]], given)
        if not bone_map:
            raise ValueError("No bones of %s matched this rig. Pass map={'<their bone>': '<bone>'}" % src)
        k = rg.rig_height(rig) / (rg.rig_height(owner) or 1.0)
        tracks = {}
        for s, d in bone_map.items():
            track = copy.deepcopy(clip["tracks"][s])
            track.pop("scale", None)
            for key in track.get("loc") or []:
                key[1] = [round(v * k, 3) for v in key[1]]
            tracks[d] = track
        made = dict(copy.deepcopy({x: clip[x] for x in clip if x != "tracks"}), id=rg._nid("a"), tracks=tracks,
                    name=str(p.get("name") or clip["name"]))
        mapped = len(bone_map)
    _add_clip(rig, made)
    scene.touch_geom()
    return {"summary": "%s on %s: %d bones mapped, %d frames" % (made["name"], rig.get("name"), mapped, made["frames"]),
            "ids": [rig["id"]], "clip": made["id"], "frames": made["frames"]}


def _close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def reduce_keys(keys, tol):
    """Drop keys that the straight line between their kept neighbours reproduces within tol."""
    if len(keys) <= 2:
        return keys
    kept = [keys[0]]
    for i in range(1, len(keys) - 1):
        a, b, c = kept[-1], keys[i], keys[i + 1]
        t = (b[0] - a[0]) / ((c[0] - a[0]) or 1.0)
        if not _close(b[1], [x + (y - x) * t for x, y in zip(a[1], c[1])], tol):
            kept.append(b)
    return kept + [keys[-1]]


@op("bake_clip", "bake_clip(clip, rig?, step=1, tol=0.1, to=game) resample to linear keys, drop bone scale keys, "
                 "close loop seams, reduce keys within tol (deg/cm)")
def bake_clip(scene, p):
    rig = rg.find_rig(scene, str(p.get("rig") or ""))
    clip = rg.find_clip(rig, str(p.get("clip") or ""))
    step, tol = max(1, int(p.get("step") or 1)), float(p.get("tol", 0.1))
    end = int(math.ceil(float(clip.get("frames") or 1)))
    frames = list(range(0, end + 1, step))
    if frames[-1] != end:
        frames.append(end)
    before = after = 0
    for bone, track in (clip.get("tracks") or {}).items():
        before += sum(len(track.get(ch) or []) for ch in rg.CHANNELS)
        track.pop("scale", None)  # engines drop or mangle bone scale; game rigs never key it
        for ch in ("rot", "loc"):
            if not track.get(ch):
                continue
            keys = []
            for f in frames:
                loc, q, _ = rg.local_pose({"tracks": {bone: track}}, bone, f)
                keys.append([float(f), _euler3(rg._qmat(q)) if ch == "rot" else [round(v, 3) for v in loc], "linear"])
            if clip.get("loop") and not (ch == "loc" and clip.get("root_motion") and bone in ("root", "forge_root")):
                keys[-1][1] = list(keys[0][1])
            track[ch] = reduce_keys(keys, tol)
            after += len(track[ch])
    scene.touch_geom()
    deg, cm = loop_seam(clip) if clip.get("loop") else (0, 0)
    return {"summary": "%s baked: %d -> %d keys%s" % (clip["name"], before, after, ", seam %g deg" % deg if clip.get("loop") else ""),
            "ids": [rig["id"]], "keys": after}


# ---- r-face --------------------------------------------------------------------

# region: (centre u, centre v, radius u, radius v) in head coordinates: u -1..1 across, v 0 chin..1 crown.
_REGION = {"eye": (.38, .62, .22, .1), "brow": (.38, .74, .25, .07), "browin": (.12, .74, .15, .07), "browout": (.62, .74, .2, .07),
           "cheek": (.55, .47, .3, .12), "nose": (.15, .52, .14, .09), "corner": (.32, .33, .15, .07), "upper": (0, .37, .45, .05),
           "lower": (0, .30, .45, .05), "mouth": (0, .33, .5, .1), "tongue": (0, .31, .15, .05)}
# name -> [(region, side, (out, forward, up))]; side l/r mirrors the region, "" both sides, "+"/"-" absolute x.
_SIDED = {"eyeBlink": [("eye", (0, 0, -.5))], "eyeLookDown": [("eye", (0, 0, -.15))], "eyeLookIn": [("eye", (-.15, 0, 0))],
          "eyeLookOut": [("eye", (.15, 0, 0))], "eyeLookUp": [("eye", (0, 0, .15))], "eyeSquint": [("eye", (0, 0, -.2))],
          "eyeWide": [("eye", (0, 0, .3))], "mouthSmile": [("corner", (.5, 0, .5))], "mouthFrown": [("corner", (0, 0, -.5))],
          "mouthDimple": [("corner", (-.2, 0, 0))], "mouthStretch": [("corner", (.6, 0, -.1))],
          "mouthPress": [("upper", (0, 0, -.1)), ("lower", (0, 0, .1))], "mouthLowerDown": [("lower", (0, 0, -.5))],
          "mouthUpperUp": [("upper", (0, 0, .5))], "browDown": [("brow", (0, 0, -.4))], "browOuterUp": [("browout", (0, 0, .5))],
          "cheekSquint": [("cheek", (0, 0, .3))], "noseSneer": [("nose", (0, 0, .3))]}
_CENTRE = {"jawOpen": [("jaw", "", (0, 0, -.9))], "jawForward": [("jaw", "", (0, .4, 0))], "jawLeft": [("jaw", "+", (.3, 0, 0))],
           "jawRight": [("jaw", "-", (-.3, 0, 0))], "mouthClose": [("upper", "", (0, 0, -.2)), ("lower", "", (0, 0, .2))],
           "mouthFunnel": [("mouth", "", (-.4, .5, 0))], "mouthPucker": [("mouth", "", (-.5, .4, 0))],
           "mouthLeft": [("mouth", "+", (.4, 0, 0))], "mouthRight": [("mouth", "-", (-.4, 0, 0))],
           "mouthRollLower": [("lower", "", (0, -.2, .1))], "mouthRollUpper": [("upper", "", (0, -.2, -.1))],
           "mouthShrugLower": [("lower", "", (0, .2, .3))], "mouthShrugUpper": [("upper", "", (0, .2, .3))],
           "browInnerUp": [("browin", "", (0, 0, .5))], "cheekPuff": [("cheek", "", (.4, .2, 0))], "tongueOut": [("tongue", "", (0, .6, 0))]}
SHAPES = dict(_CENTRE)
for _n, _parts in _SIDED.items():
    for _s, _tag in (("l", "Left"), ("r", "Right")):
        SHAPES[_n + _tag] = [(reg, _s, vec) for reg, vec in _parts]
BASIC12 = ("jawOpen", "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight", "eyeBlinkLeft", "eyeBlinkRight",
           "browInnerUp", "browDownLeft", "browDownRight", "mouthPucker", "cheekPuff")


def _weight(region, side, u, v):
    if region == "jaw":
        return max(0.0, min(1.0, (0.36 - v) / 0.2))
    cu, cv, ru, rv = _REGION[region]
    cs = {"l": (cu,), "r": (-cu,)}.get(side, (cu, -cu) if cu else (0.0,))
    return max(math.exp(-(((u - c) / ru) ** 2) - ((v - cv) / rv) ** 2) for c in cs)


def face_deltas(verts, names, head_frac=None):
    """{shape: [[dx,dy,dz] per vertex]} for the given ARKit names, from the verts' own head bounds (Forge cm, -Y front)."""
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    width, height = hi[0] - lo[0], hi[2] - lo[2]
    if head_frac or height > 1.6 * width:  # a whole character: the head is its top slice
        lo[2] = hi[2] - height * float(head_frac or 1 / 7)
        height = hi[2] - lo[2]
        width = min(width, height * 0.8)
    cx, k = (lo[0] + hi[0]) / 2, 0.12 * width
    local = []
    for x, y, z in verts:
        front = (hi[1] - y) / ((hi[1] - lo[1]) or 1.0) > 0.4
        v = (z - lo[2]) / (height or 1.0)
        local.append((2 * (x - cx) / (width or 1.0), v, 1 if x >= cx else -1) if front and 0 <= v <= 1 else None)
    out = {}
    for name in names:
        rows = []
        for spot in local:
            d = [0.0, 0.0, 0.0]
            if spot:
                u, v, sx = spot
                for region, side, (ox, fy, uz) in SHAPES[name]:
                    w = _weight(region, side, u, v)
                    if w < 0.02:
                        continue
                    sign = {"l": 1, "r": -1, "+": 1, "-": 1}.get(side, sx)  # "+"/"-" already carry the sign in the vector
                    d[0] += ox * sign * w * k
                    d[1] -= fy * w * k
                    d[2] += uz * w * k
            rows.append([round(c, 4) for c in d])
        out[name] = rows
    return out


# Oculus / Ready Player Me visemes as ARKit mixes (sil is the rest pose). "~X" = XLeft + XRight.
VISEMES = {"PP": {"mouthClose": 1, "~mouthPress": .5}, "FF": {"mouthRollLower": .6, "~mouthUpperUp": .3},
           "TH": {"jawOpen": .2, "tongueOut": .4}, "DD": {"jawOpen": .3, "~mouthStretch": .2},
           "kk": {"jawOpen": .35, "~mouthStretch": .3}, "CH": {"mouthFunnel": .6, "jawOpen": .2},
           "SS": {"~mouthStretch": .5, "jawOpen": .1}, "nn": {"jawOpen": .25, "mouthClose": .2},
           "RR": {"mouthFunnel": .4, "jawOpen": .2}, "aa": {"jawOpen": .7}, "E": {"jawOpen": .35, "~mouthStretch": .5},
           "ih": {"jawOpen": .25, "~mouthSmile": .3}, "oh": {"jawOpen": .5, "mouthFunnel": .5},
           "ou": {"mouthPucker": .8, "jawOpen": .15}}


def viseme_deltas(verts, head_frac=None):
    parts = {}
    for mix in VISEMES.values():
        for n in mix:
            for name in ((n[1:] + "Left", n[1:] + "Right") if n.startswith("~") else (n,)):
                parts[name] = None
    base = face_deltas(verts, list(parts), head_frac)
    out = {}
    for vis, mix in VISEMES.items():
        rows = [[0.0, 0.0, 0.0] for _ in verts]
        for n, w in mix.items():
            for name in ((n[1:] + "Left", n[1:] + "Right") if n.startswith("~") else (n,)):
                for row, d in zip(rows, base[name]):
                    for i in range(3):
                        row[i] += w * d[i]
        out["viseme_" + vis] = [[round(c, 4) for c in r] for r in rows]
    return out


def split_lr(verts, rows, soft=0.1):
    """(left, right) deltas of one shape: a smoothstep across the centre, `soft` of the width wide. Left is +X."""
    xs = [v[0] for v in verts]
    cx, half = (min(xs) + max(xs)) / 2, max(1e-6, soft * (max(xs) - min(xs)))
    left, right = [], []
    for v, d in zip(verts, rows):
        t = max(0.0, min(1.0, (v[0] - cx + half) / (2 * half)))
        w = t * t * (3 - 2 * t)
        left.append([round(c * w, 4) for c in d])
        right.append([round(c * (1 - w), 4) for c in d])
    return left, right


@op("face_shapes", "face_shapes(ids, set='arkit52'|'basic12'|'none', visemes=false, split=[shape names], report=false, head_frac) "
                   "ARKit-named shape keys, 14 viseme_ keys, _L/_R splits, and a list of keys that move nothing")
def face_shapes(scene, p):
    nodes = _targets(scene, p)
    if not nodes:
        raise ValueError("face_shapes needs the head mesh id")
    which = _pick(str(p.get("set") or "arkit52"), ("arkit52", "basic12", "none"), "shape set")
    names = {"arkit52": list(SHAPES), "basic12": list(BASIC12), "none": []}[which]
    split = [str(s) for s in p.get("split") or []]
    flat = []
    for node in nodes:
        if scene.is_locked(node):
            raise ValueError("%s is locked" % node.get("name"))
        mesh = scene._bake(node)
        if names:
            for key in [k for k in mesh.attrs if k.startswith("sk:") and k[3:] in SHAPES]:
                mesh.attrs.pop(key)
        for name, rows in face_deltas(mesh.verts, names, p.get("head_frac")).items():
            mesh.attrs["sk:" + name] = rows
        if p.get("visemes"):
            for name, rows in viseme_deltas(mesh.verts, p.get("head_frac")).items():
                mesh.attrs["sk:" + name] = rows
        for name in split:
            rows = mesh.attrs.get("sk:" + name)
            if rows is None:
                raise ValueError("No shape key %r on %s to split" % (name, node.get("name")))
            mesh.attrs["sk:%s_L" % name], mesh.attrs["sk:%s_R" % name] = split_lr(mesh.verts, rows)
        flat += [k[3:] for k, rows in mesh.attrs.items() if k.startswith("sk:") and max((abs(c) for r in rows for c in r), default=0) < 1e-4]
        scene.write_mesh(node, mesh)
    scene.touch_geom()
    count = len(names) + (len(VISEMES) if p.get("visemes") else 0) + 2 * len(split)
    out = {"summary": "%d shape keys (%s%s%s) on %d mesh(es)" % (count, which, " +visemes" if p.get("visemes") else "",
                                                                  " +%d split" % len(split) if split else "", len(nodes)),
           "ids": [n["id"] for n in nodes], "shapes": count}
    if p.get("report"):
        out["flat"] = len(flat)
        out["summary"] += "; %d move nothing%s" % (len(flat), " (%s)" % ", ".join(flat[:4]) if flat else "")
    return out
