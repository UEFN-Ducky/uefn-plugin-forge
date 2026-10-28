"""HQ rig: skeleton templates, auto weights, IK poses, a clip library and ARKit face shapes.

Same bone names as the UE5 Manny everywhere, so clips and retargeting work on every template.
Rotations are Forge degrees; the engine only does what needs bpy (bone-heat weights).
"""
import copy
import difflib
import json
import math
from pathlib import Path

from .. import rig as rg
from . import engine, op

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
    return {"summary": "%s: %d frames%s%s" % (made["name"], made["frames"], ", loops" if made["loop"] else "", ", root motion" if made["root_motion"] else ""),
            "ids": [rig["id"]], "clip": made["id"], "frames": made["frames"]}


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


@op("face_shapes", "face_shapes(ids, set='arkit52'|'basic12', head_frac) ARKit-named shape keys from landmark regions of a head")
def face_shapes(scene, p):
    nodes = _targets(scene, p)
    if not nodes:
        raise ValueError("face_shapes needs the head mesh id")
    which = _pick(str(p.get("set") or "arkit52"), ("arkit52", "basic12"), "shape set")
    names = list(SHAPES) if which == "arkit52" else list(BASIC12)
    for node in nodes:
        if scene.is_locked(node):
            raise ValueError("%s is locked" % node.get("name"))
        mesh = scene._bake(node)
        for key in [k for k in mesh.attrs if k.startswith("sk:") and k[3:] in SHAPES]:
            mesh.attrs.pop(key)
        for name, rows in face_deltas(mesh.verts, names, p.get("head_frac")).items():
            mesh.attrs["sk:" + name] = rows
        scene.write_mesh(node, mesh)
    scene.touch_geom()
    return {"summary": "%d %s shape keys on %d mesh(es)" % (len(names), which, len(nodes)), "ids": [n["id"] for n in nodes], "shapes": len(names)}
