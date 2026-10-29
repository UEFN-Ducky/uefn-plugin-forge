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
# hair -> existing groom ops: hair(style, length cm, density) on the scalp faces, then hair_cards(cards). The groom
# droops by gravity, not along the scalp, so short cuts are a dense short coat (crew / buzz) and only "long" droops;
# the scalp under the hair is tinted to the hair colour so gaps between cards read as hair.
HAIR = {"short": ({"style": "short", "length": 2.5, "density": 90, "clump": 0.1}, 220),
        "buzz": ({"style": "short", "length": 0.9, "density": 100, "clump": 0.05}, 160),
        "long": ({"style": "long", "length": 28.0}, 220), "none": (None, 0)}
HAIR_COLORS = ("black", "brown", "auburn", "blonde", "red", "white", "gray")
_DEFAULTS = {"sex": "neutral", "age": "adult", "build": "average", "shape": None, "height_cm": None, "tone": 0.3,
             "eye_color": "brown", "hair": "short", "hair_color": "brown", "name": "Head"}


def params(given):
    given = given or {}
    bad = [k for k in given if k not in _DEFAULTS]
    if bad:
        raise ValueError("Unknown param %r for head. Params: %s" % (bad[0], ", ".join(_DEFAULTS)))
    p = {**_DEFAULTS, **given}
    for key, options in (("sex", SEXES), ("age", AGES), ("build", BUILDS), ("eye_color", EYES), ("hair", tuple(HAIR)),
                         ("hair_color", HAIR_COLORS)):
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


_LIGHT, _DEEP = (0.68, 0.45, 0.36), (0.13, 0.065, 0.04)  # linear albedo at tone 0 and 1 (same as the engine's)


def scalp(u, v):
    """0..1 under the hair: hairline 0.80 at the forehead, 0.66 over the ears, 0.42 at the nape (engine scalp_faces)."""
    phi = abs(u - 0.5) * 2 * math.pi
    knots = [(0, 0.80), (0.6, 0.78), (1.6, 0.66), (math.pi, 0.42)]
    line = next(a[1] + (b[1] - a[1]) * (phi - a[0]) / (b[0] - a[0]) for a, b in zip(knots, knots[1:]) if phi <= b[0])
    return max(0.0, min(1.0, (v - line + 0.01) / 0.03))


def colour(u, v, tone, male, hair=None):
    """Linear skin albedo at head UV: redder nose, cheeks and lips' surround; blue-grey beard shadow on a male jaw;
    the scalp tinted toward the hair colour (hair: rgb) so gaps between cards read as hair, not skin."""
    c = [a + (b - a) * tone for a, b in zip(_LIGHT, _DEEP)]
    red = 0.9 * math.exp(-0.5 * ((u - 0.5) / 0.03) ** 2) * (0.29 <= v <= 0.5) \
        + 0.7 * (_g(u, v, 0.41, 0.4, 0.04, 0.05) + _g(u, v, 0.59, 0.4, 0.04, 0.05)) \
        + 0.3 * (_g(u, v, 0.5, 0.62, 0.05, 0.03) + _g(u, v, 0.5, 0.08, 0.04, 0.04))
    c = [c[0] * (1 + 0.04 * red), c[1] * (1 - 0.12 * red), c[2] * (1 - 0.14 * red)]
    if male:
        beard = _g(u, v, 0.5, 0.12, 0.12, 0.1) * (1 - 0.8 * _g(u, v, 0.5, 0.21, 0.07, 0.035))  # jaw + upper lip, not the lips
        c = [a * (1 - 0.35 * beard) + g * 0.35 * beard for a, g in zip(c, (0.2, 0.21, 0.24))]
    if hair:
        s = 0.85 * scalp(u, v)
        c = [a * (1 - s) + h * s for a, h in zip(c, hair)]
    return c


def _png(scene, file, fn, rgb, size=256):
    """8-bit grey or RGB PNG of fn(u, v) in 0..1 (stdlib only: the engine Python has no PIL)."""
    def px(u, v):
        val = fn(u, v)
        vals = val if rgb else [val]
        return bytes(round(255 * max(0.0, min(1.0, x))) for x in vals)
    rows = b"".join(b"\0" + b"".join(px((i + 0.5) / size, 1 - (j + 0.5) / size) for i in range(size)) for j in range(size))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))  # noqa: E731
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2 if rgb else 0, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    folder = scene.folder / "textures"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file).write_bytes(png)
    return file


def _unfight(scene, node, cell=0.1, rounds=12):
    """qa's z-fight rule (parallel faces whose centres share a 0.1 cm cell) on the hair cards: push one vertex of
    each offender 0.08 cm along its normal. The groom seeds cards from the node id, so a pair shows up now and then."""
    mesh = scene.local_mesh(node)
    moved = False
    for r in range(rounds):
        seen, bad = {}, []
        for fi in range(len(mesh.faces)):
            c, n = mesh.face_center(fi), mesh.face_normal(fi)
            key = tuple(round(v / cell) for v in c)
            if any(abs(sum(a * b for a, b in zip(n, o))) > 0.99 for o in seen.get(key, ())):
                bad.append((fi, n))
            seen.setdefault(key, []).append(n)
        if not bad:
            break
        for fi, n in bad:
            face = mesh.faces[fi]
            v = mesh.verts[face[r % len(face)]]
            mesh.verts[face[r % len(face)]] = [v[i] + n[i] * cell * 0.8 for i in range(3)]
        moved = True
    if moved:
        scene.write_mesh(node, mesh)


def _srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


@op("head", "head(sex=male|female|neutral, age=child|adult|elder, build=slim|average|heavy, shape={slider:-1..1}, height_cm=24, "
            "tone=0.3, eye_color, hair=short|buzz|long|none, hair_color=brown, name=Head) realistic quad head + neck: eye/mouth loops, "
            "eyeballs with cornea, brow+lash cards, nostrils, teeth, tongue, mouth bag, ears, SSS skin, scalp hair cards; groups eyes lips brows jaw nose cheeks")
def head(scene, p):
    q = params({k: v for k, v in p.items() if k not in ("id", "wait")})
    result, changed = engine(scene, "head", [], q)
    skin = scene.materials().get("m" + hashlib.sha1(("%s_Skin" % q["name"]).encode()).hexdigest()[:6])
    if skin is not None and skin.get("spec"):
        layers = [layer for layer in skin["spec"]["layers"] if layer.get("type") != "image"]
        male = q["sex"] == "male" and q["age"] != "child"
        from ..engine.ops.groom import rgb
        tint = rgb(q["hair_color"]) if q["hair"] != "none" else None
        col = _png(scene, "T_%s_Skin_C.png" % q["name"], lambda u, v: [_srgb(c) for c in colour(u, v, q["tone"], male, tint)], True)
        rough = _png(scene, "T_%s_Skin_R.png" % q["name"], roughness, False)
        layers[1:1] = [{"type": "image", "image": col, "target": "color"}, {"type": "image", "image": rough, "target": "roughness"}]
        skin["spec"]["layers"] = layers
    summary, ids = result["summary"], list(changed)
    strands, cards = HAIR[q["hair"]]
    if strands and result.get("scalp"):  # the existing groom ops, on the scalp faces the engine picked
        from .groom import hair, hair_cards
        head_id = next(i for i in changed if (scene.node(i) or {}).get("name") == q["name"])
        hair(scene, {**strands, "id": head_id, "region": result["scalp"], "color": q["hair_color"]})
        made = hair_cards(scene, {"id": head_id, "cards": cards})
        _unfight(scene, scene.node(made["ids"][0]))
        ids += made["ids"]
        summary += ", %s hair %d cards" % (q["hair"], made["cards"])
    return {"summary": summary, "ids": ids}  # tris/quads/groups are in the summary; stats would push the result past 300 chars
