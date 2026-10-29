"""HQ face kit: cartoon_face. The bpy work is engine/ops/face_kit.py.

Sizes are fractions of the head width, so the same call suits a 20 cm head and a 2 m one.
"""
from . import engine, op

NAME = "face_kit"

STYLES = ("cute", "heroic", "realistic")
SPECIES = ("cat", "dog", "human", "bird")
_DEFAULTS = {"style": "cute", "species": "cat", "eye_size": 0.22, "look": [0, 0]}


def params(given):
    given = given or {}
    bad = [k for k in given if k not in _DEFAULTS]
    if bad:
        raise ValueError("Unknown param %r for cartoon_face. Params: head, %s" % (bad[0], ", ".join(_DEFAULTS)))
    p = {**_DEFAULTS, **given}
    if p["style"] not in STYLES:
        raise ValueError("style must be one of: %s" % ", ".join(STYLES))
    if p["species"] not in SPECIES:
        raise ValueError("species must be one of: %s" % ", ".join(SPECIES))
    p["eye_size"] = float(p["eye_size"])
    if not 0.05 <= p["eye_size"] <= 0.28:
        raise ValueError("eye_size must be 0.05-0.28 (fraction of head width; cute default 0.22)")
    look = p["look"]
    if not (isinstance(look, (list, tuple)) and len(look) == 2):
        raise ValueError("look must be [x, y] in -1..1, e.g. [0.3, 0]")
    p["look"] = [max(-1.0, min(1.0, float(v))) for v in look]
    return p


@op("cartoon_face", "cartoon_face(head, style=cute|heroic|realistic, species=cat|dog|human|bird, eye_size=0.22, look=[0,0]) "
                    "eyes (white, iris, pupil, glints), brows, cream muzzle + cheeks, nose, mouth crease, ears; "
                    "shape keys blink smile brow_up. Head must face -Y")
def cartoon_face(scene, p):
    ident = str(p.get("head") or p.get("id") or "")
    node = scene.node(ident) if ident else None
    if node is None or node.get("type") != "object":
        objs = ["%s(%s)" % (n["id"], n.get("name", "")) for n in scene.data["nodes"] if n.get("type") == "object"][:8]
        raise ValueError("No head object %r. Pass head=<object id>. Objects: %s" % (ident, ", ".join(objs) or "none"))
    q = params({k: v for k, v in p.items() if k not in ("head", "id", "wait")})
    result, changed = engine(scene, "cartoon_face", [ident], {**q, "head": ident})
    return {**result, "ids": changed or [ident]}
