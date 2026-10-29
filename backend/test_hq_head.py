"""py -3 -m backend.test_hq_head  (re-runs itself under the Forge engine Python if bpy is missing)."""
import os
import subprocess
import sys

try:
    import bpy
    import bmesh
except ImportError:
    py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UEFN-Ducky", "forge-engine", "py", "python.exe")
    if not os.path.exists(py):
        sys.exit("bpy engine not installed (forge-engine/py/python.exe missing)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"import sys,runpy;sys.path.insert(0,{root!r});runpy.run_module('backend.test_hq_head',run_name='__main__')"
    sys.exit(subprocess.call([py, "-c", code]))

import json
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from backend import hq as hq_pkg
from backend import tools
from backend.engine import client
from backend.engine.ops import head as engine_head
from backend.hq import head as hq_head
from backend.scene import forget, load

_CACHE = {}


def build(**kw):
    """The code-built head unless base="makehuman"."""
    key = repr(sorted(kw.items()))
    if key not in _CACHE:
        if not _CACHE:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        p = hq_head.params({"base": "procedural", **kw})
        fn = engine_head.build_makehuman if p["base"] == "makehuman" else engine_head.build_head
        _CACHE[key] = fn({**p, "unit": 0.01})
    return _CACHE[key]


def mh(**kw):
    return build(base="makehuman", **kw)


def _world(ob):
    return np.array([tuple(ob.matrix_world @ v.co) for v in ob.data.vertices])


def _open_edges(ob):
    bm = _bm(ob)
    bad = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    return bad


REPO = Path(__file__).resolve().parents[1]


def test_mh_head_and_parts():
    parts, info = mh(sex="male")
    assert set(parts) == {"head", "eye_L", "eye_R", "details", "teeth_U", "teeth_L", "tongue"}, set(parts)
    s = engine_head.stats(parts)
    assert 6000 <= s["tris"] <= 16000 and s["quads"] >= 95 and s["groups"] == 6, s
    for name in ("head", "eye_L", "eye_R", "tongue"):
        assert not _open_edges(parts[name]), "%s must be closed" % name
    co = _world(parts["head"])
    chin = info["chin"]
    assert abs(co[:, 2].min()) < 1e-4, "the bust stands on z=0"
    assert abs(co[:, 2].max() - chin - 0.24) < 0.002, "chin to crown %.3f m, want 0.24" % (co[:, 2].max() - chin)
    assert chin >= 0.035, "neck reaches %.1f cm below the chin" % (chin * 100)
    eye = _world(parts["eye_L"]).mean(0)
    assert 0.44 <= (eye[2] - chin) / 0.24 <= 0.56 and eye[0] > 0.02, "eye line at %.2f H" % ((eye[2] - chin) / 0.24)
    tip = co[np.argmin(co[:, 1])]
    assert abs(tip[0]) < 0.004 and chin < tip[2] < eye[2], "nose tip at %s" % tip
    for name in ("head", "eye_L", "details", "teeth_U", "tongue"):  # qa: every part pivots on its base
        local = np.array([tuple(v.co) for v in parts[name].data.vertices])
        assert abs(local[:, 2].min()) < 1e-3 and np.abs(local[:, :2].min(0) + local[:, :2].max(0)).max() < 2e-3, name
    used = {parts["head"].data.materials[q.material_index].name.split("_", 1)[1] for q in parts["head"].data.polygons}
    assert used == {"Skin", "Lips"}, used
    lips = [q.center for q in parts["head"].data.polygons if q.material_index == 1]
    assert 20 <= len(lips) <= 300, "%d lip faces" % len(lips)
    det = {parts["details"].data.materials[q.material_index].name.split("_", 1)[1] for q in parts["details"].data.polygons}
    assert det == {"Hair"}, det


def test_mh_sliders_move_their_region():
    """Every slider moves the mesh; nose_width widens the nose and leaves the ears; ear_size the other way round."""
    base = engine_head.mh_verts({})
    x, y, z = base.T  # MakeHuman dm, Forge axes
    nose = (np.abs(x) < 0.25) & (y < -1.45) & (z > 6.7) & (z < 7.2)
    ears = (np.abs(x) > 0.72) & (z > 6.9) & (z < 7.6) & (y > -0.6)
    assert nose.sum() > 30 and ears.sum() > 60
    for name in engine_head.MH_SLIDERS:
        for v in (1.0, -1.0):
            d = np.linalg.norm(engine_head.mh_verts({"shape": {name: v}}) - base, axis=1)
            assert d.max() > 0.01, "%s=%g moves nothing" % (name, v)
    wide = engine_head.mh_verts({"shape": {"nose_width": 1.0}})
    assert np.ptp(wide[nose, 0]) - np.ptp(base[nose, 0]) > 0.005, "nose_width=1 should widen the nose"
    assert np.abs(wide[ears] - base[ears]).max() < 1e-3, "nose_width moved the ears"
    big = engine_head.mh_verts({"shape": {"ear_size": 1.0}})
    assert np.linalg.norm(big[ears] - base[ears], axis=1).max() > 0.02 and np.abs(big[nose] - base[nose]).max() < 1e-3
    male, female = engine_head.mh_verts({"sex": "male"}), engine_head.mh_verts({"sex": "female"})
    child = engine_head.mh_verts({"age": "child"})
    assert np.abs(male - female).max() > 0.05 and np.abs(child - base).max() > 0.5, "sex and age targets must apply"


def test_mh_asset_and_notice():
    files = list((REPO / "assets" / "heads").iterdir())
    assert sum(f.stat().st_size for f in files) < 5 * 1024 * 1024, "assets/heads must stay under 5 MB"
    notice = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "CC0" in notice and "makehumancommunity/makehuman" in notice and re.search(r"\b[0-9a-f]{40}\b", notice), "notice needs source, commit, CC0"


def test_mh_hair_covers_scalp():
    from mathutils.bvhtree import BVHTree
    for hair in ("short", "buzz"):
        parts, info = mh(sex="male", hair=hair)
        det = parts["details"]
        tree = BVHTree.FromPolygons([tuple(c) for c in _world(det)], [list(q.vertices) for q in det.data.polygons])
        head = parts["head"]
        mw = head.matrix_world
        hit = 0
        for f in info["scalp"]:
            q = head.data.polygons[f]
            loc = tree.ray_cast(mw @ q.center, q.normal, 0.02)[0]
            hit += loc is not None
        assert len(info["scalp"]) > 150 and hit / len(info["scalp"]) >= 0.9, "%s hair covers %d of %d scalp faces" % (hair, hit, len(info["scalp"]))
    parts, info = mh(sex="male", hair="none")
    assert len(parts["details"].data.polygons) < 600, "hair=none: brows and lashes only"


def test_mh_qa_rules_every_part():
    """qa's rules in cm for every variant: no two points of a part within 0.05 (they weld) and no parallel faces
    of any two parts whose centres share a 0.1 cell (z-fight)."""
    from mathutils.kdtree import KDTree
    for kw in ({"sex": "male"}, {"sex": "female", "hair": "long"}, {"age": "child", "hair": "buzz"}, {"age": "elder", "build": "heavy"},
               {"sex": "female", "build": "slim", "shape": {"eye_size": 1, "eye_spacing": -1, "mouth_width": 1, "lip_full": 1, "nose_width": -1}}):
        parts, _ = mh(**kw)
        seen, pairs = {}, 0
        for name, ob in parts.items():
            co = _world(ob) * 100
            tree = KDTree(len(co))
            for i, c in enumerate(co):
                tree.insert(c, i)
            tree.balance()
            gap = min(tree.find_n(c, 2)[1][2] for c in co)
            assert gap >= 0.05, "%s %s: points %.3f cm apart" % (kw, name, gap)
            mw = ob.matrix_world
            for q in ob.data.polygons:
                key = tuple(round(v / 0.1) for v in (mw @ q.center) * 100)
                pairs += any(abs(q.normal.dot(o)) > 0.99 for o in seen.get(key, ()))
                seen.setdefault(key, []).append(q.normal.copy())
        assert not pairs, "%s: %d z-fight face pairs" % (kw, pairs)
        assert not _open_edges(parts["head"]), kw


def _bm(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    return bm


def test_budget_and_quads():
    parts, _ = build(sex="male")
    s = engine_head.stats(parts)
    assert 6000 <= s["tris"] <= 14000, "tris %d outside 6k-14k" % s["tris"]
    assert s["quads"] >= 85, "head is %d%% quads (want 85+)" % s["quads"]
    assert s["groups"] == 6 and {g.name for g in parts["head"].vertex_groups} == {"eyes", "lips", "brows", "jaw", "nose", "cheeks"}


def test_proportions():
    parts, info = build()
    z = np.array([v.co.z for v in parts["head"].data.vertices])
    lo, hi = z.min(), z.max()
    assert abs(lo) < 1e-3 and abs(hi - 0.24) < 0.002, "head spans z %.3f..%.3f m, want 0..0.24" % (lo, hi)
    eye_z = parts["eye_L"].location.z
    assert abs((eye_z - lo) / (hi - lo) - 0.5) <= 0.03, "eye line at %.3f of head height" % ((eye_z - lo) / (hi - lo))
    co = np.array([tuple(v.co) for v in parts["head"].data.vertices])
    left = co[info["rings"]["eye_L"][-1]]
    width = np.ptp(left[:, 0])
    gap = 2 * np.abs(co[info["rings"]["eye_R"][-1]][:, 0]).min()
    assert 0.85 <= gap / width <= 1.25, "eyes %.2f eye widths apart (want about 1)" % (gap / width)
    assert co[np.argmin(co[:, 2]), 1] < -0.03, "lowest point should be the chin, not the under-jaw"
    nose_tip = co[np.argmin(co[:, 1])]
    assert abs(nose_tip[0]) < 0.004 and 0.28 < nose_tip[2] / 0.24 < 0.4, "nose tip at %s" % nose_tip
    # cranium (crown - nasion) vs face (nasion - chin): real adults 0.85-1.1, a doll's egg is ~1.35
    mid = co[(np.abs(co[:, 0]) < 0.006) & (co[:, 1] < -0.05) & (co[:, 2] > 0.45 * 0.24) & (co[:, 2] < 0.58 * 0.24)]
    nasion = mid[np.argmax(mid[:, 1])][2]
    ratio = (hi - nasion) / (nasion - lo)
    assert 0.85 <= ratio <= 1.1, "cranium / face height %.2f (want 0.85-1.1)" % ratio
    # nose projects >= 1.6 cm in front of the inner eye corners
    canthi = [co[r][np.argmin(np.abs(co[r][:, 0]))][1] for r in (info["rings"]["eye_L"][-1], info["rings"]["eye_R"][-1])]
    assert np.mean(canthi) - nose_tip[1] >= 0.016, "nose projects %.1f cm (want 1.6+)" % ((np.mean(canthi) - nose_tip[1]) * 100)
    # cheekbones at least 10 % wider than the jaw
    zyg = np.abs(co[(co[:, 2] > 0.40 * 0.24) & (co[:, 2] < 0.48 * 0.24) & (co[:, 1] < 0.05), 0]).max()
    jaw = np.abs(co[(co[:, 2] > 0.10 * 0.24) & (co[:, 2] < 0.20 * 0.24), 0]).max()
    assert zyg >= 1.1 * jaw, "bizygomatic %.1f cm vs bigonial %.1f cm (want +10%%)" % (zyg * 100, jaw * 100)


def test_loops_valence_4():
    parts, info = build()
    bm = _bm(parts["head"])
    for name in ("eye_L", "eye_R", "mouth"):
        rings = info["rings"][name]
        assert len(rings) >= 4 and len(rings[0]) >= 14, "%s: %d rings of %d" % (name, len(rings), len(rings[0]))
        bad = [i for r in rings for i in r if len(bm.verts[i].link_edges) != 4]
        assert not bad, "%s loop vertices with valence != 4: %d" % (name, len(bad))
    bm.free()


def test_manifold_and_nostrils():
    """The shell is closed except two nostril tunnels: exactly 2 boundary loops, inside the nose."""
    parts, info = build()
    bm = _bm(parts["head"])
    assert not [e for e in bm.edges if len(e.link_faces) > 2], "head shell has non-manifold edges"
    open_edges = [e for e in bm.edges if len(e.link_faces) == 1]
    verts = {v for e in open_edges for v in e.verts}
    loops = []
    while verts:  # flood the boundary into loops
        stack, loop = [verts.pop()], set()
        while stack:
            v = stack.pop()
            loop.add(v)
            for e in v.link_edges:
                w = e.other_vert(v)
                if len(e.link_faces) == 1 and w in verts:
                    verts.discard(w)
                    stack.append(w)
        loops.append(loop)
    assert len(loops) == 2, "want 2 boundary loops (nostrils), got %d" % len(loops)
    for loop in loops:
        c = np.mean([tuple(v.co) for v in loop], axis=0) / 0.24
        assert abs(c[0]) < 0.05 and 0.27 < c[2] < 0.36 and c[1] < -0.2, "nostril loop outside the nose at %s H" % c
    assert sorted(len(loop) for loop in loops) == [4, 4] and len(info["nostrils"]) == 2
    bm.free()
    bm = _bm(parts["neck"])
    assert all(len(e.link_faces) == 2 for e in bm.edges), "neck must be closed"
    bm.free()
    low = min((parts["neck"].matrix_world @ v.co).z for v in parts["neck"].data.vertices)
    assert low <= -0.10, "neck reaches %.1f cm below the chin (want 10+)" % (-low * 100)
    for name in ("eye_L", "eye_R", "ears", "tongue"):
        bm = _bm(parts[name])
        shells = [e for e in bm.edges if len(e.link_faces) not in (1, 2)]
        assert not shells, name
        bm.free()


def test_parts_and_materials():
    parts, _ = build()
    assert {"head", "eye_L", "eye_R", "details", "teeth_U", "teeth_L", "tongue", "ears", "neck"} <= set(parts)
    det = parts["details"]
    mw = det.matrix_world
    by = {}
    for q in det.data.polygons:
        by.setdefault(det.data.materials[q.material_index].name.split("_", 1)[1], []).append((mw @ q.center) / 0.24)
    assert len(by.get("Brow", ())) >= 100, "eyebrow cards missing: %d brow faces" % len(by.get("Brow", ()))
    assert len(by.get("Lash", ())) >= 60, "lash cards missing: %d lash faces" % len(by.get("Lash", ()))
    bz = np.array([c.z for c in by["Brow"]])
    assert 0.55 < bz.mean() < 0.65 and {c.x > 0 for c in by["Brow"]} == {True, False}, "brows sit above both eyes"
    assert all(abs(c.z - 0.5) < 0.06 for c in by["Lash"]), "lashes ring the eyes"
    eye = [m.name.split("_", 1)[1] for m in parts["eye_L"].data.materials]
    assert eye == ["Sclera", "Cornea", "Iris", "Pupil"], eye
    used = {parts["eye_L"].data.materials[q.material_index].name.split("_", 1)[1] for q in parts["eye_L"].data.polygons}
    assert used == {"Sclera", "Cornea", "Iris", "Pupil"}, used
    head_used = {parts["head"].data.materials[q.material_index].name.split("_", 1)[1] for q in parts["head"].data.polygons}
    assert head_used == {"Skin", "Lips", "MouthBag", "EyeSocket"}, head_used
    skin = json.loads(bpy.data.materials["Head_Skin"]["forge"])["spec"]["layers"]
    sss = next(layer for layer in skin if layer["type"] == "sss")
    assert sss["radius"] == [1.0, 0.35, 0.2] and sss["scale"] == 0.01
    bsdf = next(n for n in bpy.data.materials["Head_Skin"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert bsdf.inputs["Subsurface Weight"].default_value == 1.0
    assert 0 < bpy.data.materials["Head_Cornea"]["forge"].count("opacity")
    # pivots: eyeballs rotate about their own centre
    assert abs(parts["eye_L"].location.x - 0.13 * 0.24) < 0.002 and parts["eye_L"].location.x > 0 > parts["eye_R"].location.x
    # iris sits behind the cornea apex (parallax)
    co = np.array([tuple(parts["eye_L"].matrix_world @ v.co) for v in parts["eye_L"].data.vertices])
    fm = np.array([q.material_index for q in parts["eye_L"].data.polygons])
    iris = sorted({i for q in np.nonzero(fm == 2)[0] for i in parts["eye_L"].data.polygons[int(q)].vertices})
    assert co[:, 1].min() < co[iris, 1].min() - 0.002, "iris must sit 2 mm+ behind the cornea apex"
    assert co[:, 1].min() < parts["eye_L"].location.y - 0.012, "cornea dome should bulge in front of the eyeball"


def test_variants():
    for kw in ({"sex": "female"}, {"age": "child"}, {"age": "elder", "build": "heavy"},
               {"shape": {"nose_width": 1, "lip_full": -1, "jaw_width": 1, "eye_tilt": 1}}):
        parts, _ = build(**kw)
        s = engine_head.stats(parts)
        assert 6000 <= s["tris"] <= 14000 and s["quads"] >= 85, (kw, s)
    child, _ = build(age="child")
    z = max(v.co.z for v in child["head"].data.vertices)
    assert abs(z - 0.19) < 0.002, "child head %.3f m tall, default 19 cm" % z
    a, _ = build()
    w, _ = build(shape={"nose_width": 1})
    span = lambda parts: np.ptp([v.co.x for v in parts["head"].data.vertices if abs(v.co.x) < 0.03 and 0.07 < v.co.z < 0.08 and v.co.y < -0.08])  # noqa: E731
    assert span(w) >= span(a), "nose_width=1 should not narrow the nose"


# Average adult side profile through anthropometric landmarks, in head heights (y forward = -, z up), crown first.
PROFILE = [(0.0, 1.0), (-0.22, 0.94), (-0.34, 0.8), (-0.39, 0.66), (-0.41, 0.585), (-0.385, 0.55), (-0.43, 0.42),
           (-0.47, 0.325), (-0.4, 0.3), (-0.42, 0.24), (-0.4, 0.205), (-0.415, 0.18), (-0.385, 0.15), (-0.4, 0.07),
           (-0.33, 0.0), (-0.15, 0.06), (0.0, 0.17), (0.2, 0.27), (0.38, 0.42), (0.42, 0.6), (0.34, 0.85), (0.18, 0.97)]


def _inside(poly, py, pz):
    poly = np.asarray(poly)
    inside = np.zeros(py.shape, bool)
    for (y0, z0), (y1, z1) in zip(poly, np.roll(poly, -1, 0)):
        cross = ((z0 > pz) != (z1 > pz)) & (py < (y1 - y0) * (pz - z0) / (z1 - z0 + 1e-12) + y0)
        inside ^= cross
    return inside


def _side_mask(ob, H, grid):
    """Side silhouette of the mesh on a (y, z) grid in head heights: every triangle filled."""
    co = np.array([tuple(ob.matrix_world @ v.co) for v in ob.data.vertices]) / H
    mask = np.zeros((len(grid), len(grid)), bool)
    step = grid[1] - grid[0]
    for q in ob.data.polygons:
        vs = list(q.vertices)
        for i in range(1, len(vs) - 1):
            t = co[[vs[0], vs[i], vs[i + 1]]][:, 1:]
            lo, hi = np.searchsorted(grid, t.min(0) - step), np.searchsorted(grid, t.max(0) + step)
            gy, gz = np.meshgrid(grid[lo[0]:hi[0]], grid[lo[1]:hi[1]], indexing="ij")
            mask[lo[0]:hi[0], lo[1]:hi[1]] |= _inside(t, gy, gz)
    return mask


def test_side_profile_iou():
    parts, _ = build(sex="male")
    grid = np.linspace(-0.6, 0.6, 241)
    mask = _side_mask(parts["head"], 0.24, grid)
    gy, gz = np.meshgrid(grid, grid, indexing="ij")
    ref = _inside(PROFILE, gy, gz)
    iou = (mask & ref).sum() / (mask | ref).sum()
    assert iou >= 0.8, "side profile IoU %.2f against the landmark profile (want 0.8+)" % iou


def test_qa_rules_every_part():
    """qa's rules in cm: no two points of a part within 0.05 (they weld into non-manifold edges) and no parallel
    faces whose centres share a 0.1 cell (z-fight), for every variant."""
    for kw in ({"sex": "male"}, {"sex": "female"}, {"age": "child"}, {"age": "elder", "build": "heavy"},
               {"sex": "female", "shape": {"eye_size": 1, "eye_spacing": -1, "mouth_width": 1, "lip_full": 1}}):
        parts, _ = build(**kw)
        for name, ob in parts.items():
            mw = ob.matrix_world
            co = np.array([tuple(mw @ v.co) for v in ob.data.vertices]) * 100
            d = np.linalg.norm(co[:, None] - co[None], axis=2)
            np.fill_diagonal(d, 9)
            assert d.min() >= 0.05, "%s %s: points %.3f cm apart" % (kw, name, d.min())
            seen, pairs = {}, 0
            for q in ob.data.polygons:
                key = tuple(round(v / 0.1) for v in (mw @ q.center) * 100)
                pairs += any(abs(q.normal.dot(o)) > 0.99 for o in seen.get(key, ()))
                seen.setdefault(key, []).append(q.normal.copy())
            assert not pairs, "%s %s: %d z-fight face pairs" % (kw, name, pairs)


def test_params_and_registration():
    for bad in ({"base": "x"}, {"sex": "x"}, {"age": "teen"}, {"shape": {"snout": 1}}, {"shape": {"chin": 3}}, {"tone": 2}, {"height_cm": 2}, {"hat": 1}):
        try:
            hq_head.params(bad)
            assert False, bad
        except ValueError as e:
            assert len(str(e)) > 15, e
    assert "head" in hq_pkg.HQ_OPS and callable(engine_head.op_head)
    assert len(hq_pkg.run_hq(None, "help", {"op": "head"})["summary"]) <= 400
    assert 0.25 < hq_head.roughness(0.5, 0.72) < 0.4 < hq_head.roughness(0.41, 0.38), "T-zone must be glossier than cheeks"
    doc = (Path(__file__).resolve().parents[1] / "skills" / "forge" / "references" / "realistic_head.md").read_text(encoding="utf-8")
    head, sep, _ = doc[4:].partition("\n---\n")
    assert doc.startswith("---\n") and sep, "realistic_head.md needs a --- frontmatter block"
    meta = dict(line.strip().split(": ", 1) for line in head.splitlines() if ": " in line)
    assert {"description", "label", "load_condition"} <= set(meta) and all(meta[k].strip('"') for k in meta), meta
    named = set(re.findall(r'"op": "([a-z_]+)"', doc)) | {"head", "face_shapes", "lods", "hair_cards", "topology"}
    assert named <= set(hq_pkg.HQ_OPS), "realistic_head.md names ops Forge lacks: %s" % (named - set(hq_pkg.HQ_OPS))


class _Api:
    def __init__(self):
        self.fns = {}
        self.changeset = MagicMock()
        self.listener = None

    def tool(self, **_):
        return lambda fn: self.fns.setdefault(fn.__name__, fn)

    def __getattr__(self, _):
        return lambda *a, **k: (lambda fn: fn)


def test_through_forge_batch():
    """The AI path: forge_batch -> forge_hq head; result <= 300 chars, objects + skin roughness map in the scene."""
    with tempfile.TemporaryDirectory(prefix="forge-head-") as tmp, \
            patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(tmp) / "scenes")}), \
            patch.object(tools, "_attach_captures", lambda r: r):
        api = _Api()
        tools.register_tools(api)
        t = api.fns
        t["forge_scene_new"](name="HeadTest")
        try:
            out = t["forge_batch"](name="HeadTest", steps=[{"tool": "forge_hq", "op": "head", "params": {"sex": "male"}}])
            assert out.get("ok", True), out
            hq_out = t["forge_hq"](name="HeadTest", op="head", params={"sex": "female", "name": "Her", "hair": "none", "base": "procedural"})
            assert hq_out.get("ok") and len(json.dumps(hq_out)) <= 300, hq_out
            long = t["forge_hq"](name="HeadTest", op="head", params={"sex": "female", "name": "Long", "hair": "long", "hair_color": "auburn"})
            assert long.get("ok") and len(json.dumps(long)) <= 300, long
            scene = load("HeadTest")
            names = [n.get("name") for n in scene.data["nodes"] if n.get("type") == "object"]
            assert {"Head", "Head_eye_L", "Head_eye_R", "Head_teeth_U", "Head_tongue", "Head_details", "Her", "Her_ears", "Her_neck"} <= set(names), names
            assert not [n for n in names if n.endswith("_cards")], "makehuman hair is a cap (+ drape); hair=none has none: %s" % names
            hair = next(n for n in scene.data["nodes"] if n.get("name") == "Long_details")
            zs = [v[2] for v in scene.world_mesh(hair).verts]
            assert len(scene.local_mesh(hair).faces) >= 2000 and np.ptp(zs) > 20, "long hair should hang from the crown to the neck"
            skin = next(m for m in scene.data["materials"] if m["name"] == "Head_Skin")
            images = {layer["target"]: layer["image"] for layer in skin["spec"]["layers"] if layer["type"] == "image"}
            assert set(images) == {"color", "roughness"} and all((scene.folder / "textures" / f).is_file() for f in images.values())

            def grade(prefix, want):
                ids = [n["id"] for n in scene.data["nodes"] if n.get("type") == "object" and str(n.get("name")).startswith(prefix)]
                qa = t["forge_hq"](name="HeadTest", op="qa", params={"ids": ids, "profile": "uefn", "budget": "char"})
                print("qa %s:" % prefix, qa.get("summary"))
                assert qa.get("score", 0) >= want and qa.get("errors") == 0, qa
            grade("Head", 90)   # makehuman base, short hair cap
            grade("Long", 90)   # makehuman base, long hair cards
            grade("Her", 85)    # procedural base, no hair
            head = next(n["id"] for n in scene.data["nodes"] if n.get("name") == "Head")
            alone = t["forge_hq"](name="HeadTest", op="qa", params={"id": head, "profile": "uefn", "budget": "char"})
            print("qa head:", alone.get("summary"))
            assert alone.get("score", 0) >= 95 and alone.get("errors") == 0, alone
            uv = t["forge_hq"](name="HeadTest", op="uv", id=head, params={"method": "seams", "res": 2048, "texel": 0})
            print("uv:", uv.get("summary"))
            packed = float(re.search(r"(\d+(?:\.\d+)?)% packed", uv["summary"]).group(1))
            assert packed > 40, uv
        finally:
            client.stop()
            forget("HeadTest")


def render_demos(out, **head):
    """HEAD_RENDER=<folder>: head_front/three/side.png through forge_hq head + light (final quality)."""
    import shutil
    with tempfile.TemporaryDirectory(prefix="forge-head-") as tmp, \
            patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(tmp) / "scenes")}), \
            patch.object(tools, "_attach_captures", lambda r: r):
        api = _Api()
        tools.register_tools(api)
        t = api.fns
        t["forge_scene_new"](name="HeadDemo")
        try:
            print(t["forge_hq"](name="HeadDemo", op="head", params=head)["summary"])
            ids = [n["id"] for n in load("HeadDemo").data["nodes"] if n.get("type") == "object"]
            for view in ("front", "three", "side"):
                r = t["forge_hq"](name="HeadDemo", op="light", params={"ids": ids, "preset": "soft", "view": view,
                                                                       "quality": "final", "size": [900, 900]})
                print(view, r)
                if r.get("path"):
                    shutil.copy(r["path"], Path(out) / ("head_%s.png" % view))
        finally:
            client.stop()
            forget("HeadDemo")


if __name__ == "__main__":
    if os.environ.get("HEAD_RENDER"):
        render_demos(os.environ["HEAD_RENDER"], **json.loads(os.environ.get("HEAD_PARAMS") or "{}"))
        client.stop()
        sys.exit(0)
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    client.stop()
