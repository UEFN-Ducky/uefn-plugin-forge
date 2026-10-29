"""Rebuild makehuman_head.npz from a MakeHuman checkout (CC0 assets, see THIRD_PARTY_NOTICES.md).

    python extract.py <makehuman>/makehuman/data

Keeps the head and neck of base.obj (cut at the clavicle, y > CUT dm) plus the eye, teeth, tongue and eyelash
helpers, in Forge axes (x, -z, y: Z-up, face toward -Y), still in MakeHuman decimetres. Targets are stored as
sparse float16 deltas on those vertices: the macro targets per sex x age x weight (the three races averaged)
and one +/- pair per face slider.
"""
import sys
from pathlib import Path

import numpy as np

CUT = 5.75  # dm: the neck base, just above the clavicle
HELPERS = ("helper-l-eye", "helper-r-eye", "helper-upper-teeth", "helper-lower-teeth", "helper-tongue",
           "helper-l-eyelashes-1", "helper-l-eyelashes-2", "helper-r-eyelashes-1", "helper-r-eyelashes-2")
SEXES, AGES, WEIGHTS = ("female", "male"), ("child", "young", "old"), ("minweight", "averageweight", "maxweight")
RACES = ("african", "asian", "caucasian")
# slider -> (+1 targets, -1 targets); {s} is l and r
SLIDERS = {
    "face_width": (["head/head-scale-horiz-incr"], ["head/head-scale-horiz-decr"]),
    "skull_depth": (["head/head-back-scale-depth-incr"], ["head/head-back-scale-depth-decr"]),
    "jaw_width": (["chin/chin-bones-incr"], ["chin/chin-bones-decr"]),
    "chin": (["chin/chin-prominent-incr"], ["chin/chin-prominent-decr"]),
    "cheekbone": (["cheek/{s}-cheek-bones-incr"], ["cheek/{s}-cheek-bones-decr"]),
    "brow": (["eyebrows/eyebrows-trans-forward"], ["eyebrows/eyebrows-trans-backward"]),
    "nose_width": (["nose/nose-scale-horiz-incr"], ["nose/nose-scale-horiz-decr"]),
    "nose_length": (["nose/nose-scale-vert-incr"], ["nose/nose-scale-vert-decr"]),
    "nose_bridge": (["nose/nose-hump-incr"], ["nose/nose-hump-decr"]),
    "nose_tip": (["nose/nose-point-up"], ["nose/nose-point-down"]),
    "lip_full": (["mouth/mouth-upperlip-volume-incr", "mouth/mouth-lowerlip-volume-incr"],
                 ["mouth/mouth-upperlip-volume-decr", "mouth/mouth-lowerlip-volume-decr"]),
    "mouth_width": (["mouth/mouth-scale-horiz-incr"], ["mouth/mouth-scale-horiz-decr"]),
    "eye_size": (["eyes/{s}-eye-scale-incr"], ["eyes/{s}-eye-scale-decr"]),
    "eye_tilt": (["eyes/{s}-eye-corner2-up"], ["eyes/{s}-eye-corner2-down"]),
    "eye_spacing": (["eyes/{s}-eye-trans-out"], ["eyes/{s}-eye-trans-in"]),
    "ear_size": (["ears/{s}-ear-scale-incr"], ["ears/{s}-ear-scale-decr"]),
    "fat": (["head/head-fat-incr"], ["head/head-fat-decr"]),
}


def read_obj(path):
    verts, faces, groups, group = [], [], [], None
    for line in open(path, encoding="utf-8"):
        if line.startswith("v "):
            verts.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("g "):
            group = line.split()[1]
        elif line.startswith("f "):
            faces.append([int(t.split("/")[0]) - 1 for t in line.split()[1:]])
            groups.append(group)
    return np.array(verts), faces, groups


def read_target(path, n):
    d = np.zeros((n, 3))
    for line in open(path, encoding="utf-8"):
        if line[:1].isdigit():
            i, x, y, z = line.split()
            d[int(i)] = float(x), float(y), float(z)
    return d


def main(data):
    data = Path(data)
    V, F, G = read_obj(data / "3dobjs" / "base.obj")
    keep = {"skin": [f for f, g in zip(F, G) if g == "body" and min(V[i, 1] for i in f) > CUT]}
    for h in HELPERS:
        keep[h] = [f for f, g in zip(F, G) if g == h]
    used = np.unique(np.concatenate([np.ravel(fs) for fs in keep.values()]))
    remap = np.full(len(V), -1)
    remap[used] = np.arange(len(used))
    out = {}
    for name, fs in keep.items():
        out["f:" + name] = remap[np.array(fs)].astype(np.uint16)
    co = V[used]
    # neck cut: level the open ring on its lowest point so the cap is flat
    edges = {}
    for f in keep["skin"]:
        for a, b in zip(f, f[1:] + f[:1]):
            edges[tuple(sorted((a, b)))] = edges.get(tuple(sorted((a, b))), 0) + 1
    ring = remap[np.unique([v for e, c in edges.items() if c == 1 for v in e])]
    co[ring, 1] = co[ring, 1].min()
    out["co"] = np.column_stack([co[:, 0], -co[:, 2], co[:, 1]]).astype(np.float32)

    def delta(names):
        d = sum(read_target(data / "targets" / (t + ".target"), len(V)) for t in names)
        d = d[used]
        return np.column_stack([d[:, 0], -d[:, 2], d[:, 1]])

    def store(key, d):
        idx = np.nonzero(np.abs(d).max(axis=1) > 1e-5)[0]
        out["i:" + key] = idx.astype(np.uint16)
        out["d:" + key] = d[idx].astype(np.float16)

    macro = data / "targets" / "macrodetails"
    for s in SEXES:
        for a in AGES:
            store("race-%s-%s" % (s, a), delta(["macrodetails/%s-%s-%s" % (r, s, a) for r in RACES]) / 3)
            for w in WEIGHTS:
                name = "universal-%s-%s-averagemuscle-%s" % (s, a, w)
                if (macro / (name + ".target")).exists():
                    store("macro-%s-%s-%s" % (s, a, w), delta(["macrodetails/" + name]))
    for slider, (up, down) in SLIDERS.items():
        for sign, names in (("+", up), ("-", down)):
            names = [n.format(s=side) for n in names for side in (("l", "r") if "{s}" in n else ("",))]
            store("slider-%s%s" % (slider, sign), delta(names))
    target = Path(__file__).with_name("makehuman_head.npz")
    np.savez_compressed(target, **out)
    print(target, target.stat().st_size, "bytes", len(used), "verts", len(keep["skin"]), "skin quads")


if __name__ == "__main__":
    main(sys.argv[1])
