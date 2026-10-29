"""HQ weapon: a gun or melee weapon in one call, built from part generators. The bpy work is engine/ops/weapon.py.

Game conventions: origin at the grip, muzzle / blade tip along +X, Z up, cm. Sockets (guns: Muzzle Grip
Magazine Sight; melee: Grip TrailBase TrailTip) are stored on the node as node["hq"]["weapon"]["sockets"].
"""
import difflib

from . import engine, op

# kind: default overall length, cm (real references in skills/forge/references/weapons.md)
LENGTHS = {"rifle": 92.0, "pistol": 20.2, "shotgun": 100.0, "smg": 68.0, "sniper": 115.0, "scifi_rifle": 95.0,
           "scifi_pistol": 26.0, "knife": 30.0, "sword": 100.0, "axe": 70.0, "hammer": 75.0}
GUNS = ("rifle", "pistol", "shotgun", "smg", "sniper", "scifi_rifle", "scifi_pistol")
STYLES = ("realistic", "stylized")
PARTS = {"stock": ("ar", "skeleton", "wood", "precision", "block", "none"),
         "sight": ("iron", "red_dot", "holo", "scope", "bead", "none"),
         "muzzle": ("flash", "brake", "suppressor", "emitter", "none"),
         "mag": ("std", "extended", "drum", "tube", "none"),
         "rail": (True, False)}
PISTOL_PARTS = {"sight": ("iron", "red_dot", "none"), "muzzle": ("suppressor", "emitter", "none")}
SOCKETS = {"gun": ("Grip", "Magazine", "Muzzle", "Sight"), "melee": ("Grip", "TrailBase", "TrailTip")}
_KEYS = ("kind", "style", "length_cm", "parts", "seed", "name", "at", "wait")


def _pick(value, choices, what):
    if value in choices:
        return value
    close = difflib.get_close_matches(str(value), [str(c) for c in choices], n=1)
    raise ValueError("No %s %r.%s Options: %s" % (what, value, " Did you mean %r?" % close[0] if close else "",
                                                   ", ".join(str(c) for c in choices)))


def params(given):
    bad = [k for k in given if k not in _KEYS]
    if bad:
        raise ValueError("Unknown param %r for weapon. Params: %s" % (bad[0], ", ".join(_KEYS[:-1])))
    kind = _pick(given.get("kind", "rifle"), tuple(LENGTHS), "weapon kind")
    q = {"kind": kind, "style": _pick(given.get("style", "realistic"), STYLES, "style"),
         "length": float(given.get("length_cm") or LENGTHS[kind]), "seed": int(given.get("seed", 0))}
    if not 0.5 * LENGTHS[kind] <= q["length"] <= 2 * LENGTHS[kind]:
        raise ValueError("length_cm %g is off for a %s: keep it %g-%g (default %g)"
                         % (q["length"], kind, LENGTHS[kind] / 2, LENGTHS[kind] * 2, LENGTHS[kind]))
    parts = given.get("parts") or {}
    if parts:
        if kind not in GUNS:
            raise ValueError("parts are for guns; a %s has none (Guns: %s)" % (kind, ", ".join(GUNS)))
        table = PISTOL_PARTS if kind.endswith("pistol") else PARTS
        for key, value in parts.items():
            _pick(value, table[_pick(key, tuple(table), "%s part" % kind)], key)
        q["parts"] = dict(parts)
    if given.get("name"):
        q["name"] = str(given["name"])
    if given.get("at") is not None:
        at = [float(v) for v in given["at"]]
        if len(at) != 3:
            raise ValueError("at needs [x, y, z] cm")
        q["at"] = at
    return q


@op("weapon", "weapon(kind=rifle|pistol|shotgun|smg|sniper|knife|sword|axe|hammer|scifi_rifle|scifi_pistol, "
              "style=realistic|stylized, length_cm?, parts?={stock,sight,muzzle,mag,rail}, seed=0, name?, at?) one SM_ "
              "mesh: grip origin, +X forward, sockets on the node; see skill forge/weapons")
def weapon(scene, p):
    q = params(p)
    result, changed = engine(scene, "weapon", [], q)
    sockets = result.pop("sockets")
    return {**result, "ids": changed,
            "node_data": {"kind": q["kind"], "style": q["style"], "length": result["length"], "sockets": sockets}}
