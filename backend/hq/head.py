"""HQ head: realistic human head. The bpy work is engine/ops/head.py; see skill forge/realistic_head.

height_cm is chin to crown (adult 24, child 19, elder 23.5). Sliders are -1..1 offsets of neutral anatomy.
"""
import hashlib
import math
import struct
import zlib

from . import engine, op

NAME = "head"

SEXES = ("male", "female", "neutral")
AGES = ("child", "adult", "elder")
BUILDS = ("slim", "average", "heavy")
EYES = ("brown", "hazel", "green", "blue", "grey", "amber")
SLIDERS = ("face_width", "skull_depth", "jaw_width", "chin", "cheekbone", "brow", "nose_width", "nose_length",
           "nose_bridge", "lip_full", "mouth_width", "eye_size", "eye_tilt", "eye_spacing")
HEIGHT = {"child": 19.0, "adult": 24.0, "elder": 23.5}
_DEFAULTS = {"sex": "neutral", "age": "adult", "build": "average", "shape": None, "height_cm": None, "tone": 0.3,
             "eye_color": "brown", "name": "Head"}


def params(given):
    given = given or {}
    bad = [k for k in given if k not in _DEFAULTS]
    if bad:
        raise ValueError("Unknown param %r for head. Params: %s" % (bad[0], ", ".join(_DEFAULTS)))
    p = {**_DEFAULTS, **given}
    for key, options in (("sex", SEXES), ("age", AGES), ("build", BUILDS), ("eye_color", EYES)):
        if p[key] not in options:
            raise ValueError("%s must be one of: %s" % (key, ", ".join(options)))
    shape = dict(p["shape"] or {})
    for k, v in shape.items():
        if k not in SLIDERS:
            raise ValueError("Unknown shape slider %r. Sliders (-1..1): %s" % (k, ", ".join(SLIDERS)))
        if not -1.0 <= float(v) <= 1.0:
            raise ValueError("shape.%s must be -1..1 (0 = neutral)" % k)
    p["shape"] = {k: float(v) for k, v in shape.items()}
    p["height_cm"] = float(p["height_cm"] or HEIGHT[p["age"]])
    if not 10 <= p["height_cm"] <= 60:
        raise ValueError("height_cm is chin to crown: 10-60 (adult 24)")
    p["tone"] = float(p["tone"])
    if not 0 <= p["tone"] <= 1:
        raise ValueError("tone must be 0-1 (0 light .. 1 deep skin)")
    p["name"] = str(p["name"] or "Head")
    return p


def _g(u, v, cu, cv, su, sv):
    return math.exp(-0.5 * (((u - cu) / su) ** 2 + ((v - cv) / sv) ** 2))


def roughness(u, v):
    """Skin roughness at head UV (u round the head, 0.5 = face centre; v = height): oily T-zone 0.32, cheeks 0.58."""
    r = 0.5
    r -= 0.18 * _g(u, v, 0.5, 0.72, 0.06, 0.1)                                     # forehead
    r -= 0.18 * math.exp(-0.5 * ((u - 0.5) / 0.022) ** 2) * (0.3 <= v <= 0.6)      # nose
    r -= 0.1 * _g(u, v, 0.5, 0.07, 0.04, 0.05)                                     # chin
    r += 0.08 * (_g(u, v, 0.41, 0.38, 0.04, 0.06) + _g(u, v, 0.59, 0.38, 0.04, 0.06))  # cheeks
    return max(0.0, min(1.0, r))


def _tzone(scene, name, size=256):
    """8-bit grey PNG of roughness() (stdlib only: the engine Python has no PIL)."""
    rows = b"".join(b"\0" + bytes(round(255 * roughness((i + 0.5) / size, 1 - (j + 0.5) / size)) for i in range(size))
                    for j in range(size))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))  # noqa: E731
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    folder = scene.folder / "textures"
    folder.mkdir(parents=True, exist_ok=True)
    file = "T_%s_Skin_R.png" % name
    (folder / file).write_bytes(png)
    return file


@op("head", "head(sex=male|female|neutral, age=child|adult|elder, build=slim|average|heavy, shape={slider:-1..1}, height_cm=24, "
            "tone=0.3, eye_color=brown|hazel|green|blue|grey|amber, name=Head) realistic quad head: eye/mouth/nasolabial loops, "
            "eyeballs (sclera, iris, cornea), tear lines, teeth, gums, tongue, mouth bag, ears, SSS skin; groups eyes lips brows jaw nose cheeks")
def head(scene, p):
    q = params({k: v for k, v in p.items() if k not in ("id", "wait")})
    result, changed = engine(scene, "head", [], q)
    skin = scene.materials().get("m" + hashlib.sha1(("%s_Skin" % q["name"]).encode()).hexdigest()[:6])
    if skin is not None and skin.get("spec"):
        layers = [layer for layer in skin["spec"]["layers"] if layer.get("type") != "image"]
        layers.insert(1, {"type": "image", "image": _tzone(scene, q["name"]), "target": "roughness"})
        skin["spec"]["layers"] = layers
    return {"summary": result["summary"], "ids": changed, "stats": {k: result[k] for k in ("tris", "quads", "groups") if k in result}}
