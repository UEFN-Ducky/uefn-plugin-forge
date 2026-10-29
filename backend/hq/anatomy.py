"""HQ anatomy ops: creature. The bpy work is engine/ops/anatomy.py; shapes live in assets/anatomy/<kind>.json.

Lengths are Forge units (cm).
"""
import json
from pathlib import Path

from . import engine, op

NAME = "anatomy"

_DIR = Path(__file__).resolve().parents[2] / "assets" / "anatomy"
BUILDS = ("lean", "heavy")
_DEFAULTS = {"kind": "big_cat", "length": 220.0, "height": None, "build": "lean", "faces": 7000, "name": None, "face": True}


def kinds():
    return sorted(f.stem for f in _DIR.glob("*.json"))


def spec_for(kind):
    path = _DIR / (str(kind) + ".json")
    if not path.is_file():
        raise ValueError("Unknown kind %r. Kinds: %s" % (kind, ", ".join(kinds())))
    return json.loads(path.read_text(encoding="utf-8"))


def params(given):
    given = given or {}
    bad = [k for k in given if k not in _DEFAULTS]
    if bad:
        raise ValueError("Unknown param %r for creature. Params: %s" % (bad[0], ", ".join(_DEFAULTS)))
    p = {**_DEFAULTS, **given}
    spec = spec_for(p["kind"])
    if float(p["length"]) <= 0:
        raise ValueError("length must be > 0 (cm, e.g. 220)")
    if p["build"] not in BUILDS:
        raise ValueError("build must be one of: %s" % ", ".join(BUILDS))
    if not 1000 <= int(p["faces"]) <= 40000:
        raise ValueError("faces must be 1000-40000 (UEFN budget: about 7000)")
    p["length"] = float(p["length"])
    p["height"] = float(p["height"] or p["length"] * spec["aspect"])
    return {**p, "spec": spec}


CARTOON_KINDS = ("lion", "cat", "dog", "bear")
CARTOON_POSES = ("sit", "stand")
_CARTOON = {"kind": "lion", "pose": "sit", "height": 60.0, "head_ratio": None, "name": None}


def cartoon_params(given):
    bad = [k for k in given if k not in _CARTOON]
    if bad:
        raise ValueError("Unknown param %r for cartoon_body. Params: %s" % (bad[0], ", ".join(_CARTOON)))
    p = {**_CARTOON, **given}
    if p["kind"] not in CARTOON_KINDS:
        raise ValueError("kind must be one of: %s" % ", ".join(CARTOON_KINDS))
    if p["pose"] not in CARTOON_POSES:
        raise ValueError("pose must be one of: %s" % ", ".join(CARTOON_POSES))
    p["height"] = float(p["height"])
    if p["height"] <= 0:
        raise ValueError("height must be > 0 (cm, e.g. 60)")
    if p["head_ratio"] is not None and not 0.15 <= float(p["head_ratio"]) <= 0.6:
        raise ValueError("head_ratio is the head's share of height: 0.15-0.6 (lion 0.36)")
    return p


@op("cartoon_body", "cartoon_body(kind=lion|cat|dog|bear, pose=sit|stand, height=60, head_ratio?, name?) chibi quad body: torso, front legs, 4 toed paws, haunches, S-tail; slots body belly paw ear_inner; summary has head@ and tail_tip@")
def cartoon_body(scene, p):
    q = cartoon_params({k: v for k, v in p.items() if k not in ("id", "wait")})
    result, changed = engine(scene, "cartoon_body", [], q)
    return {"summary": result["summary"], "ids": changed}


@op("creature", "creature(kind=big_cat|canine|horse, length=220, height?, build=lean|heavy, faces=7000, name?, face=true) smooth sculpted quad body; face adds eye objects, nose, lips, ears and head UVs")
def creature(scene, p):
    q = params({k: v for k, v in p.items() if k not in ("id", "wait")})
    result, changed = engine(scene, "creature", [], q)
    return {**result, "ids": changed}
