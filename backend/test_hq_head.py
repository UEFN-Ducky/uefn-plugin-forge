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
    key = repr(sorted(kw.items()))
    if key not in _CACHE:
        if not _CACHE:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        p = hq_head.params(kw)
        _CACHE[key] = engine_head.build_head({**p, "unit": 0.01})
    return _CACHE[key]


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
    assert abs(lo) < 1e-4 and abs(hi - 0.24) < 0.002, "head spans z %.3f..%.3f m, want 0..0.24" % (lo, hi)
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


def test_loops_valence_4():
    parts, info = build()
    bm = _bm(parts["head"])
    for name in ("eye_L", "eye_R", "mouth"):
        rings = info["rings"][name]
        assert len(rings) >= 4 and len(rings[0]) >= 14, "%s: %d rings of %d" % (name, len(rings), len(rings[0]))
        bad = [i for r in rings for i in r if len(bm.verts[i].link_edges) != 4]
        assert not bad, "%s loop vertices with valence != 4: %d" % (name, len(bad))
    bm.free()


def test_manifold():
    parts, _ = build()
    bm = _bm(parts["head"])
    open_edges = [e for e in bm.edges if len(e.link_faces) != 2]
    assert not open_edges, "head shell has %d non-manifold edges" % len(open_edges)
    bm.free()
    for name in ("eye_L", "eye_R", "ears", "tongue"):
        bm = _bm(parts[name])
        shells = [e for e in bm.edges if len(e.link_faces) not in (1, 2)]
        assert not shells, name
        bm.free()


def test_parts_and_materials():
    parts, _ = build()
    assert {"head", "eye_L", "eye_R", "eyewet", "teeth_U", "teeth_L", "tongue", "ears"} <= set(parts)
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


def test_params_and_registration():
    for bad in ({"sex": "x"}, {"age": "teen"}, {"shape": {"snout": 1}}, {"shape": {"chin": 3}}, {"tone": 2}, {"height_cm": 2}, {"hat": 1}):
        try:
            hq_head.params(bad)
            assert False, bad
        except ValueError as e:
            assert len(str(e)) > 15, e
    assert "head" in hq_pkg.HQ_OPS and callable(engine_head.op_head)
    assert len(hq_pkg.run_hq(None, "help", {"op": "head"})["summary"]) <= 400
    assert 0.25 < hq_head.roughness(0.5, 0.72) < 0.4 < hq_head.roughness(0.41, 0.38), "T-zone must be glossier than cheeks"


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
            hq_out = t["forge_hq"](name="HeadTest", op="head", params={"sex": "female", "name": "Her"})
            assert hq_out.get("ok") and len(json.dumps(hq_out)) <= 300, hq_out
            scene = load("HeadTest")
            names = {n.get("name") for n in scene.data["nodes"] if n.get("type") == "object"}
            assert {"Head", "Head_eye_L", "Head_eye_R", "Head_teeth_U", "Head_tongue", "Head_ears", "Her"} <= names, names
            skin = next(m for m in scene.data["materials"] if m["name"] == "Head_Skin")
            image = next(layer for layer in skin["spec"]["layers"] if layer["type"] == "image")
            assert image["target"] == "roughness" and (scene.folder / "textures" / image["image"]).is_file()
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
            ids = t["forge_hq"](name="HeadDemo", op="head", params=head, verbose=True) if False else \
                t["forge_hq"](name="HeadDemo", op="head", params=head)["ids"]
            print(ids)
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
