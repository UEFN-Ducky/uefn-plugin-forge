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


@op("creature", "creature(kind=big_cat|canine|horse, length=220, height?, build=lean|heavy, faces=7000, name?, face=true) smooth sculpted quad body; face adds eye objects, nose, lips, ears and head UVs")
def creature(scene, p):
    q = params({k: v for k, v in p.items() if k not in ("id", "wait")})
    result, changed = engine(scene, "creature", [], q)
    return {**result, "ids": changed}
