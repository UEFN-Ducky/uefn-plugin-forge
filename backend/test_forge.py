"""Forge kernel self-check. No host, no UEFN."""

from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import time
from pathlib import Path

from backend.gltf import write_glb
from backend.kernel.csg import boolean
from backend.kernel.mesh import Mesh, box, sphere
from backend.kernel.ops import bevel_edges, extrude_faces
from backend.render import render_png
from backend.scene import dispatch
from backend.textures import make_seamless, normal_from_height, to_pot
from PIL import Image


def test_dock_parts_keep_every_triangle() -> None:
    from backend.scene import Scene, _GEOM_PART, _blank
    scene = Scene(_blank("Parts"), Path("."))
    blob = "abcd" * (_GEOM_PART // 2 + 3)
    packed = {"id": "bowl", "pb": blob, "ib": "AAAA", "tris": 99}
    docked = scene._dock_proto(packed)
    assert docked["tris"] == 99 and "pb" not in docked and docked["ib"] == "AAAA"
    assert packed["pb"] == blob
    size = docked["part_size"]
    acc = "".join(scene._geom_hold[("bowl", "pb")][i * size:(i + 1) * size] for i in range((len(blob) + size - 1) // size))
    assert acc == blob and len(acc) > _GEOM_PART


def test_look_styles() -> None:
    root = Path(tempfile.mkdtemp(prefix="forge-look-"))
    os.environ["FORGE_SCENES_DIR"] = str(root)
    os.environ["FORGE_CAPTURES_DIR"] = str(root / "caps")
    dispatch("", "scene_new", {"name": "Looks"}, actor="user")
    dispatch("Looks", "add", {"kind": "box", "name": "Box", "params": {"w": 80, "d": 80, "h": 80}}, actor="agent")
    wire = dispatch("Looks", "look", {"look": "three", "style": "wire"})
    assert "fill " in wire["seen"] and wire["seen"].endswith("style wire"), wire["seen"]
    with Image.open(wire["paths"][0]) as im:
        assert sum(1 for p in im.convert("RGB").getdata() if p == (240, 240, 240)) > 50  # edge pixels
    sil = dispatch("Looks", "look", {"look": {"view": "front", "style": "silhouette", "px": 128}})
    with Image.open(sil["paths"][0]) as im:
        assert max(im.size) == 128, im.size
        assert len(im.convert("RGB").getcolors()) == 2
    for style in ("islands", "checker", "xray"):
        assert Path(dispatch("Looks", "look", {"style": style})["paths"][0]).is_file()
    for bad in ({"style": "neon"}, {"px": 64}):
        try:
            dispatch("Looks", "look", bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_kernel_and_scene() -> None:
    root = Path(tempfile.mkdtemp(prefix="forge-"))
    os.environ["FORGE_SCENES_DIR"] = str(root)
    os.environ["FORGE_CAPTURES_DIR"] = str(root / "caps")

    m = box(100, 100, 100)
    assert m.tri_count() == 12
    before = m.tri_count()
    extrude_faces(m, [0], 10)
    assert m.tri_count() > before
    def open_edges(mesh):
        counts = {}
        for face in mesh.faces:
            n = len(face)
            for i, a in enumerate(face):
                b = face[(i + 1) % n]
                key = (a, b) if a < b else (b, a)
                counts[key] = counts.get(key, 0) + 1
        return sum(1 for v in counts.values() if v != 2)

    m = box()
    assert bevel_edges(m, m.edge_keys()[:4], 6) >= 1
    whole = box()
    bevel_edges(whole, whole.edge_keys(), 8)
    assert open_edges(whole) == 0
    from backend.kernel.mesh import build_primitive
    assert open_edges(build_primitive("rounded_box", {"radius": 8})) == 0
    tile = build_primitive("box")
    xs = [v[0] for v in tile.verts]
    zs = [v[2] for v in tile.verts]
    assert max(xs) - min(xs) == 512 and max(zs) - min(zs) == 512

    a = box(100, 100, 100)
    b = box(100, 100, 100)
    b.translate((50, 0, 0))
    cut = boolean(a, b, "difference")
    _lo, hi = cut.bounds()
    assert hi[0] <= 1.0, hi

    t0 = time.perf_counter()
    dense = sphere(radius=50, seg=24, rings=12)
    extrude_faces(dense, [0], 2)
    assert (time.perf_counter() - t0) < 0.05, "extrude over 50 ms"

    dispatch("", "scene_new", {"name": "Crate"}, actor="user")
    dispatch("Crate", "add", {"kind": "box", "name": "Crate", "params": {"w": 80, "d": 80, "h": 80}}, actor="agent")
    got = dispatch("Crate", "scene_get", {})
    assert got["objects"][0]["tris"] == 12
    oid = got["objects"][0]["id"]
    dispatch("Crate", "select", {"object": oid, "query": "top"}, actor="agent")
    dispatch("Crate", "mesh_op", {"op": "extrude", "distance": 8}, actor="agent")
    extruded = dispatch("Crate", "scene_get", {})["objects"][0]["tris"]
    assert extruded > 12
    dispatch("Crate", "mesh_op", {"op": "bevel", "width": 3}, actor="user")
    after = dispatch("Crate", "undo", {}, actor="user")
    assert after["summary"] == "Undo"
    assert dispatch("Crate", "scene_get", {})["objects"][0]["tris"] == extruded
    from backend.files import open_forge, save_forge
    saved = save_forge("Crate")
    assert Path(saved["path"]).is_file()
    assert open_forge(saved["path"])["name"] == "Crate"
    dispatch("Crate", "check", {})
    fixed = dispatch("Crate", "fix", {"issues": "all", "id": oid}, actor="agent")
    assert fixed["fixed"]

    info = dispatch("Crate", "scene_get", {})
    from backend.scene import load

    scene = load("Crate")
    node = scene.node(oid)
    mesh = scene.world_mesh(node)
    glb = write_glb(mesh, root / "crate.glb", scene.materials())
    blob = glb.read_bytes()
    assert blob[:4] == b"glTF"
    length = struct.unpack_from("<I", blob, 8)[0]
    assert length == len(blob)
    js_len = struct.unpack_from("<I", blob, 12)[0]
    meta = json.loads(blob[20:20 + js_len].decode().strip())
    assert meta["meshes"][0]["primitives"]

    # Send turns the dock's right-handed Z-up space a quarter turn into glTF, never a mirror:
    # Forge +Y lands on glTF -Z and the winding still agrees with the normal. UEFN's own import
    # (glTF x, y, z -> UE x, z, y) then shows the asset exactly as the dock does.
    flat = Mesh()
    for p in ([0, 0, 0], [100, 0, 0], [0, 100, 0]):
        flat.add_vert(p)
    flat.add_face([0, 1, 2], "default")
    meta, binary = _glb_json(write_glb(flat, root / "flat.glb"))
    prim = meta["meshes"][0]["primitives"][0]
    pts = _floats(meta, binary, prim["attributes"]["POSITION"])
    tri = [pts[k * 3:k * 3 + 3] for k in range(3)]
    e1, e2 = [tri[1][k] - tri[0][k] for k in range(3)], [tri[2][k] - tri[0][k] for k in range(3)]
    assert [0.0, 0.0, -1.0] in tri and _floats(meta, binary, prim["attributes"]["NORMAL"])[:3] == [0.0, 1.0, 0.0], tri
    assert e1[2] * e2[0] - e1[0] * e2[2] > 0, "the winding must face the normal (+Y, up)"

    dispatch("", "scene_new", {"name": "Twin"}, actor="user")
    dispatch("Twin", "add", {"kind": "box", "name": "A"}, actor="user")
    dispatch("Twin", "add", {"kind": "box", "name": "B", "pos": [150, 0, 0]}, actor="user")
    twin = dispatch("Twin", "scene_payload", {})
    assert len(twin["prototypes"]) == 1, twin["prototypes"]
    assert len(twin["instances"]) == 2
    shared = twin["instances"][0][1]
    assert twin["instances"][1][1] == shared
    dispatch("Twin", "select", {"object": twin["instances"][0][0], "query": "top"}, actor="user")
    dispatch("Twin", "mesh_op", {"op": "extrude", "distance": 5}, actor="user")
    forked = dispatch("Twin", "scene_payload", {})
    assert len(forked["prototypes"]) == 2
    by_id = {row[0]: row[1] for row in forked["instances"]}
    assert by_id[twin["instances"][1][0]] == shared
    assert by_id[twin["instances"][0][0]] != shared

    dispatch("", "scene_new", {"name": "Nest"}, actor="user")
    box_a = dispatch("Nest", "add", {"kind": "box", "name": "A"}, actor="user")["id"]
    box_b = dispatch("Nest", "add", {"kind": "box", "name": "B", "pos": [80, 0, 0]}, actor="user")["id"]
    gid = dispatch("Nest", "group", {"ids": [box_a, box_b], "name": "Body"}, actor="user")["id"]
    nested = dispatch("Nest", "scene_payload", {})
    parents = {row[0]: row[2] for row in nested["instances"]}
    assert parents[box_a] == gid and parents[box_b] == gid
    lid = dispatch("Nest", "layer", {"action": "add", "name": "Glass"}, actor="user")["id"]
    dispatch("Nest", "layer", {"action": "assign", "id": lid, "ids": [box_a]}, actor="user")
    dispatch("Nest", "layer", {"action": "set", "id": lid, "visible": False}, actor="user")
    hidden = dispatch("Nest", "scene_payload", {})
    assert any(layer["id"] == lid and layer["visible"] is False for layer in hidden["layers"])
    assert any(node["id"] == box_a and node["layer"] == lid for node in hidden["nodes"])
    dispatch("Nest", "ungroup", {"id": gid}, actor="user")
    flat = {row[0]: row[2] for row in dispatch("Nest", "scene_payload", {})["instances"]}
    assert flat[box_a] in ("", None)
    dispatch("Nest", "texture", {
        "png_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
        "name": "Swatch", "tileable": False, "derive_maps": True,
    }, actor="user")
    tex = dispatch("Nest", "scene_get", {})
    swatch = next(m for m in tex["materials"] if m["name"] == "Swatch")
    assert swatch["textures"]["base_color"] and swatch["textures"]["normal"] and swatch["textures"]["orm"]
    dispatch("Nest", "join", {"ids": [box_a, box_b]}, actor="user")
    assert len([n for n in dispatch("Nest", "scene_payload", {})["nodes"] if n.get("type") == "object"]) == 1

    dispatch("", "scene_new", {"name": "Crowd"}, actor="user")
    dispatch("Crowd", "add", {"kind": "box", "count": 20000}, actor="user")
    from backend.scene import load as load_scene

    crowd = load_scene("Crowd")
    assert len(crowd.data["prototypes"]) == 1
    t_crowd = time.perf_counter()
    packed = crowd.payload()
    elapsed = time.perf_counter() - t_crowd
    assert elapsed < 0.05, f"instance payload over 50 ms: {elapsed * 1000:.2f} ms"
    print(f"20,000 boxes cold payload: {elapsed * 1000:.2f} ms (budget 50 ms)")
    t_cached = time.perf_counter()
    cached = crowd.payload()
    assert cached["instances"] is packed["instances"]
    print(f"20,000 boxes cached payload: {(time.perf_counter() - t_cached) * 1000:.3f} ms")
    assert len(packed["prototypes"]) == 1
    assert len(packed["instances"]) == 20000
    assert packed["prototypes"][0]["tris"] == 12

    t1 = time.perf_counter()
    pngs = render_png(mesh, root / "caps" / "crate.png", ["three"])
    assert time.perf_counter() - t1 < 0.3
    assert Path(pngs[0]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # A lit, textured front face: red on the left of its texture, blue on the right.
    from backend.render import render_mesh
    quad = Mesh()
    for p in ([-50, 0, 0], [50, 0, 0], [50, 0, 100], [-50, 0, 100]):
        quad.add_vert(p)
    quad.add_face([0, 1, 2, 3], "m")
    quad.uvs[0] = [[0, 0], [1, 0], [1, 1], [0, 1]]
    split = Image.new("RGB", (2, 1))
    split.putpixel((0, 0), (255, 0, 0))
    split.putpixel((1, 0), (0, 0, 255))
    split.save(root / "split.png")
    shot = render_mesh(quad, "front", materials={"m": {"base_color": [1, 1, 1], "textures": {"base_color": "split.png"}}}, textures=root)
    left, right = shot.getpixel((125, 120)), shot.getpixel((195, 120))
    assert left[0] > 100 > left[2] and right[2] > 100 > right[0], (left, right)

    img = Image.new("RGB", (32, 32), (180, 40, 40))
    tile = make_seamless(img)
    assert tile.size == (32, 32)
    nrm = normal_from_height(to_pot(img, 32))
    assert nrm.size[0] == 32

    # broken mesh: duplicate verts + a degenerate face
    bad = box(10, 10, 10)
    i = bad.add_vert(bad.verts[0])
    j = bad.add_vert(bad.verts[1])
    k = bad.add_vert([bad.verts[0][0], bad.verts[0][1], bad.verts[0][2]])
    bad.add_face([i, j, k], "default")
    from backend.check import check_mesh, fix_mesh

    issues = check_mesh(bad, "x", has_collision=True, pivot_is_base=True)
    assert any(i["id"] == "degenerate" for i in issues)
    fix_mesh(bad, {"degenerate", "duplicates"})
    after = check_mesh(bad, "x", has_collision=True, pivot_is_base=True)
    assert not any(i["id"] == "degenerate" for i in after)


def _glb_json(path: Path) -> tuple[dict, bytes]:
    blob = path.read_bytes()
    js_len = struct.unpack_from("<I", blob, 12)[0]
    meta = json.loads(blob[20:20 + js_len].decode().strip())
    bin_len = struct.unpack_from("<I", blob, 20 + js_len)[0]
    return meta, blob[28 + js_len:28 + js_len + bin_len]


def _floats(meta: dict, binary: bytes, acc_index: int) -> list[float]:
    acc = meta["accessors"][acc_index]
    view = meta["bufferViews"][acc["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc["type"]]
    return list(struct.unpack_from(f"<{acc['count'] * width}f", binary, view["byteOffset"]))


def test_rig_anim_batch() -> None:
    import math
    import random

    from backend.kernel.mesh import _euler
    from backend.rig import _qmat, _quat, export_bones, skin_matrices
    from backend.uefn import export_skinned

    root = Path(tempfile.mkdtemp(prefix="forge-rig-"))
    os.environ["FORGE_SCENES_DIR"] = str(root)
    os.environ["FORGE_CAPTURES_DIR"] = str(root / "caps")

    # Pose math rests on the quaternion matching the object Euler order exactly.
    for _ in range(50):
        rot = [random.uniform(-180, 180) for _ in range(3)]
        a, b = _euler(rot), _qmat(_quat(rot))
        assert max(abs(a[i][j] - b[i][j]) for i in range(3) for j in range(3)) < 1e-9, rot

    # One batch builds a blocky figure with $refs; one undo removes all of it.
    dispatch("", "scene_new", {"name": "Guy"}, actor="agent")
    built = dispatch("Guy", "batch", {"steps": [
        {"tool": "forge_add", "kind": "box", "object_name": "Body", "params": {"w": 40, "d": 20, "h": 80}, "pos": [0, 0, 90]},
        {"tool": "forge_add", "kind": "box", "object_name": "ArmL", "params": {"w": 70, "d": 10, "h": 10}, "pos": [55, 0, 139]},
        {"tool": "forge_add", "kind": "box", "object_name": "ArmR", "params": {"w": 70, "d": 10, "h": 10}, "pos": [-55, 0, 139]},
        {"tool": "forge_add", "kind": "box", "object_name": "Legs", "params": {"w": 40, "d": 15, "h": 90}},
        {"tool": "forge_group", "ids": ["$0", "$1", "$2", "$3"], "object_name": "Figure"},
    ]}, actor="agent")
    assert len(built["steps"]) == 5 and built["ids"]
    ids = [s["id"] for s in built["steps"][:4]]
    assert dispatch("Guy", "scene_get", {})["objects"][1]["name"] == "ArmL"
    dispatch("Guy", "undo", {}, actor="agent")
    assert dispatch("Guy", "scene_get", {})["objects"] == []
    dispatch("Guy", "redo", {}, actor="agent")
    assert len(dispatch("Guy", "scene_get", {})["objects"]) == 4
    rev = dispatch("Guy", "scene_get", {"brief": True})["rev"]
    try:
        dispatch("Guy", "batch", {"steps": [{"tool": "forge_add", "kind": "box"}, {"tool": "forge_delete", "ids": ["$7"]}]}, actor="agent")
        raise AssertionError("bad $ref must fail")
    except ValueError as exc:
        assert "step 1" in str(exc)
    assert len(dispatch("Guy", "scene_get", {})["objects"]) == 4, "failed batch must roll back"
    assert dispatch("Guy", "scene_get", {"brief": True})["rev"] == rev

    # Biped fits the figure (170 cm, T-pose span 180 cm); rigid arms, smooth body and legs.
    made = dispatch("Guy", "rig", {"action": "template", "template": "biped", "ids": ids}, actor="agent")
    assert len(made["bones"]) == 23 and "upperarm_r" in made["bones"]
    rig = dispatch("Guy", "scene_get", {})["rigs"][0]
    head = next(b for b in rig["bones"] if b["name"] == "head")
    hand = next(b for b in rig["bones"] if b["name"] == "hand_l")
    assert abs(head["tail"][2] - 170) < 1e-6 and abs(hand["tail"][0] - 90) < 1e-3, (head, hand)
    dispatch("Guy", "rig", {"action": "bind", "ids": [ids[1]], "bone": "upperarm_l"}, actor="agent")
    dispatch("Guy", "rig", {"action": "bind", "ids": [ids[2]], "bone": "upperarm_r"}, actor="agent")
    dispatch("Guy", "rig", {"action": "bind", "ids": [ids[0], ids[3]], "auto": True}, actor="agent")
    walk = dispatch("Guy", "anim", {"action": "generate", "kind": "walk"}, actor="agent")
    assert walk["frames"] == 32 and "thigh_l" in walk["bones"]
    dispatch("Guy", "anim", {"action": "new", "new_name": "Raise", "frames": 20, "loop": False}, actor="agent")
    dispatch("Guy", "anim", {"action": "key", "clip": "Raise", "keys": [
        {"bone": "upperarm_l", "frame": 0, "rot": [0, 0, 0]},
        {"bone": "upperarm_l", "frame": 20, "rot": [0, -90, 0]},
    ]}, actor="agent")

    # Rigid binding: the arm's far end swings straight up around the shoulder at frame 20.
    from backend.scene import load
    scene = load("Guy")
    rig = scene.data["rigs"][0]
    bones = export_bones(rig)
    raise_clip = next(c for c in rig["actions"] if c["name"] == "Raise")
    shoulder = next(b for b in bones if b["name"] == "upperarm_l")["head"]
    m = skin_matrices(bones, raise_clip, 20)[[b["name"] for b in bones].index("upperarm_l")]
    tip = [shoulder[0] + 50, shoulder[1], shoulder[2]]
    moved = [sum(m[i][j] * tip[j] for j in range(3)) + m[i][3] for i in range(3)]
    assert max(abs(moved[i] - [shoulder[0], shoulder[1], shoulder[2] + 50][i]) for i in range(3)) < 1e-6, moved
    assert all(abs(m[i][j] - (i == j)) < 1e-9 for m in skin_matrices(bones, None, 0) for i in range(4) for j in range(4))

    preview = dispatch("Guy", "rig_preview", {"clip": "Walk"})
    assert len(base64.b64decode(preview["matrices"])) == (32 + 1) * len(bones) * 16 * 4
    assert {o["id"] for o in preview["objects"]} == set(ids)
    pngs = dispatch("Guy", "render", {"clip": "Walk", "frames": [0, 8]})["paths"]
    assert len(pngs) == 2 and all(Path(p).is_file() for p in pngs)
    kinds = {i["id"] for i in dispatch("Guy", "check", {})["issues"]}
    assert not kinds & {"rig_bone_missing", "clip_bone_missing", "clip_loop_pop"}, kinds

    # Skinned GLB: one skeleton root, weights sum to 1, each clip baked to every frame.
    out = export_skinned(scene, root / "export")
    assert len(out) == 1 and out[0]["clips"] == ["Walk", "Raise"]
    meta, binary = _glb_json(Path(out[0]["glb"]))
    skin = meta["skins"][0]
    assert len(skin["joints"]) == len(bones) == meta["accessors"][skin["inverseBindMatrices"]]["count"]
    assert meta["scenes"][0]["nodes"] == [0, skin["skeleton"]] and meta["nodes"][0]["skin"] == 0
    for prim in meta["meshes"][0]["primitives"]:
        weights = _floats(meta, binary, prim["attributes"]["WEIGHTS_0"])
        assert all(abs(sum(weights[i:i + 4]) - 1) < 1e-5 for i in range(0, len(weights), 4))
    anims = {a["name"]: a for a in meta["animations"]}
    assert set(anims) == {"Walk", "Raise"}
    times = meta["accessors"][anims["Walk"]["samplers"][0]["input"]]
    assert times["count"] == 33 and abs(times["max"][0] - 32 / 30) < 1e-6
    for anim in anims.values():
        for channel in anim["channels"]:
            assert 1 <= channel["target"]["node"] <= len(bones)
    rot = next(c for c in anims["Raise"]["channels"] if meta["nodes"][c["target"]["node"]]["name"] == "upperarm_l")
    last = _floats(meta, binary, anims["Raise"]["samplers"][rot["sampler"]]["output"])[-4:]
    # Forge -90 about Y is -90 about glTF -Z, so +90 about Z: (x, y, z, w) = (0, 0, sin 45, cos 45).
    assert max(abs(a - b) for a, b in zip(last, [0, 0, math.sqrt(0.5), math.sqrt(0.5)])) < 1e-5, last

    # A concave cap triangulates to its true area (a fan would overlap and miss).
    from backend.kernel.mesh import build_primitive, vcross, vlen, vsub
    outline = [[0, 0], [300, 0], [300, 60], [60, 60], [60, 300], [0, 300]]
    ell = build_primitive("extrude", {"points": outline, "depth": 250})
    top = [t for t in ell.triangles() if len(ell.faces[t[3]]) == 6 and ell.verts[t[0]][2] > 1]
    area = sum(vlen(vcross(vsub(ell.verts[b], ell.verts[a]), vsub(ell.verts[c], ell.verts[a]))) / 2 for a, b, c, _ in top)
    assert abs(area - 32400) < 1e-6, area

    # Concave boolean through the scene, then merge the rigged figure into another scene.
    dispatch("", "scene_new", {"name": "Wall"}, actor="agent")
    wall = dispatch("Wall", "add", {"kind": "extrude", "params": {"points": outline, "depth": 250}}, actor="agent")["id"]
    holes = [dispatch("Wall", "add", {"kind": "box", "params": {"w": 30, "d": 200, "h": 60}, "pos": [x, 30, 100]}, actor="agent")["id"] for x in (150, 230)]
    cut = dispatch("Wall", "boolean", {"a": wall, "b": holes, "op": "difference"}, actor="agent")
    assert len(dispatch("Wall", "scene_get", {})["objects"]) == 1 and cut["id"]
    merged = dispatch("Wall", "merge_scene", {"source": "Guy", "offset": [500, 0, 0]}, actor="agent")
    after = dispatch("Wall", "scene_get", {})
    assert len(after["objects"]) == 5 and len(after["rigs"]) == 1 and merged["ids"]
    moved_rig = after["rigs"][0]
    assert moved_rig["id"] != rig["id"] and abs(next(b for b in moved_rig["bones"] if b["name"] == "head")["head"][0] - (head["head"][0] + 500)) < 1e-6
    assert all(o["skin"]["rig"] == moved_rig["id"] for o in after["objects"] if o.get("skin"))


def test_sculpt_paint_shapes() -> None:
    import array
    import math

    from backend.rig import bind_weights, export_bones, skin_matrices
    from backend.scene import load
    from backend.uefn import export_skinned

    root = Path(tempfile.mkdtemp(prefix="forge-paint-"))
    os.environ["FORGE_SCENES_DIR"] = str(root)
    os.environ["FORGE_CAPTURES_DIR"] = str(root / "caps")

    def mesh_of(name, oid):
        scene = load(name)
        return scene.local_mesh(scene.node(oid))

    # Smooth subdivide rounds a box; a mirrored draw on +X bulges both sides by the same amount.
    dispatch("", "scene_new", {"name": "Clay"}, actor="agent")
    clay = dispatch("Clay", "add", {"kind": "box", "params": {"w": 60, "d": 60, "h": 60}}, actor="agent")["id"]
    dispatch("Clay", "mesh_op", {"op": "subdivide", "smooth": True, "levels": 2}, actor="agent")
    lo, hi = mesh_of("Clay", clay).bounds()
    assert len(mesh_of("Clay", clay).verts) == 98
    dispatch("Clay", "sculpt", {"id": clay, "brush": "draw", "points": [[hi[0], 0, (lo[2] + hi[2]) / 2]],
                                "radius": 15, "strength": 4, "mirror": "x"}, actor="agent")
    lo2, hi2 = mesh_of("Clay", clay).bounds()
    assert hi2[0] > hi[0] + 1 and abs(hi2[0] + lo2[0]) < 1e-6, (lo2, hi2)

    # Sculpting into a shape key leaves the base mesh alone.
    dispatch("Clay", "shape", {"id": clay, "action": "add", "key": "Puff"}, actor="agent")
    rest = mesh_of("Clay", clay).verts
    dispatch("Clay", "sculpt", {"id": clay, "brush": "draw", "points": [[0, lo[1], 30]], "radius": 20, "strength": 5, "shape": "Puff"}, actor="agent")
    drawn = mesh_of("Clay", clay).attrs["sk:Puff"]
    dispatch("Clay", "sculpt", {"id": clay, "brush": "inflate_all", "strength": 2, "shape": "Puff"}, actor="agent")
    shaped = mesh_of("Clay", clay)
    assert shaped.verts == rest and max(abs(c) for d in drawn for c in d) > 1 and shaped.attrs["sk:Puff"] != drawn

    # The dock's stroke commit: exact values, exactly one geom step (the dock keeps its mesh on
    # that), and refused whole when the mesh changed under it or the payload is bad.
    def b64(kind, values):
        return base64.b64encode(array.array(kind, values).tobytes()).decode()

    stroke = {"id": clay, "target": "pos", "count": 98, "idx": b64("I", [0]), "values": b64("f", [1.5, 2.5, 3.5])}
    geom = dispatch("Clay", "scene_get", {"brief": True})["geom"]
    assert dispatch("Clay", "values", stroke, actor="user")["geom"] == geom + 1
    assert mesh_of("Clay", clay).verts[0] == [1.5, 2.5, 3.5]
    for bad in ({"count": 97}, {"idx": b64("I", [98])}, {"values": b64("f", [math.nan] * 3)}, {"values": b64("f", [0])}):
        try:
            dispatch("Clay", "values", {**stroke, "values": b64("f", [0, 0, 0]), **bad}, actor="user")
            raise AssertionError(f"values must refuse {bad}")
        except ValueError:
            pass
    assert mesh_of("Clay", clay).verts[0] == [1.5, 2.5, 3.5], "a refused stroke changes nothing"
    tris = mesh_of("Clay", clay).tri_count()
    dispatch("Clay", "mesh_op", {"op": "decimate", "ratio": 0.5}, actor="agent")
    assert 0 < mesh_of("Clay", clay).tri_count() < tris

    # Smart unwrap: one chart per box side, all inside 0..1.
    dispatch("", "scene_new", {"name": "Paint"}, actor="agent")
    rock = dispatch("Paint", "add", {"kind": "box", "params": {"w": 100, "d": 80, "h": 60}}, actor="agent")["id"]
    unwrap = dispatch("Paint", "uv", {"mode": "smart", "id": rock}, actor="agent")
    uvs = [c for face in mesh_of("Paint", rock).uvs for uv in face for c in uv]
    assert unwrap["charts"] == 6 and 0 <= min(uvs) and max(uvs) <= 1, unwrap

    # Blend paint: an "up" mask covers the top and not the bottom, and ships as COLOR_0.
    moss = dispatch("Paint", "material_set", {"name": "Moss", "base_color": [0.2, 0.5, 0.1]}, actor="agent")["material"]["id"]
    stone = dispatch("Paint", "material_set", {"name": "Stone", "base_color": [0.5, 0.5, 0.5]}, actor="agent")["material"]["id"]
    # What forge_material_set(material="Stone") and material="<id>" send: both update Stone in place.
    dispatch("Paint", "material_set", {"id": "Stone", "name": "Stone", "layers": ["Moss"]}, actor="agent")
    dispatch("Paint", "material_set", {"id": stone, "name": stone, "roughness": 0.9}, actor="agent")
    stones = [m for m in dispatch("Paint", "scene_get", {})["materials"] if m["id"] == stone or m["name"] == "Stone"]
    assert len(stones) == 1 and stones[0]["name"] == "Stone" and stones[0]["layers"] == [moss] and stones[0]["roughness"] == 0.9, stones
    extra = dispatch("Paint", "add", {"kind": "box", "material": "Stone", "pos": [300, 0, 0]}, actor="agent")["id"]
    dispatch("Paint", "material_assign", {"id": rock, "material": "Stone"}, actor="agent")
    nodes = {n["id"]: n for n in load("Paint").data["nodes"]}
    assert nodes[extra]["material"] == stone and nodes[rock]["material"] == stone
    dispatch("Paint", "paint", {"id": rock, "mode": "blend", "channel": "r", "mask": "up", "threshold": 0.3, "softness": 0.2}, actor="agent")
    painted = mesh_of("Paint", rock)
    red = [(v[2], painted.attrs["col:blend"][i][0]) for i, v in enumerate(painted.verts)]
    assert all(r == 1 for z, r in red if z > 59) and all(r == 0 for z, r in red if z < 1), red
    meta, _ = _glb_json(write_glb(painted, root / "rock.glb", load("Paint").materials()))
    assert all("COLOR_0" in p["attributes"] for p in meta["meshes"][0]["primitives"])

    # Texture paint: every stroke saves new files, undo gets the old pixels back, strays are swept.
    wood = dispatch("Paint", "material_set", {"name": "Wood", "base_color": [0.5, 0.3, 0.2]}, actor="agent")["material"]["id"]
    folder = load("Paint").folder / "textures"

    def base_color():
        return next(m for m in dispatch("Paint", "scene_get", {})["materials"] if m["id"] == wood)["textures"]["base_color"]

    dispatch("Paint", "paint", {"id": rock, "mode": "texture", "material": wood, "fill": "all", "color": [0.5, 0.3, 0.2], "size": 64}, actor="agent")
    first = base_color()
    (folder / "stray.png").write_bytes(b"x")
    dispatch("Paint", "paint", {"id": rock, "mode": "texture", "material": wood, "points": [[0, -40, 30]], "radius": 10, "color": "#ff0000"}, actor="agent")
    assert base_color() != first and (folder / first).is_file() and not (folder / "stray.png").exists()
    dispatch("Paint", "undo", {}, actor="user")
    assert base_color() == first and (folder / first).is_file()

    # A .blend import stores meshes with no UVs at all: a textured object on one still opens (the
    # stadium crash), and a mesh no textured object uses ships without UVs.
    paint = load("Paint")
    for pid, mat in (("pblend", wood), ("pplain", paint.data["materials"][0]["id"])):
        raw = box(40, 40, 40).to_dict()
        raw["uvs"] = []
        paint.data["prototypes"].append({"id": pid, "kind": "mesh", "params": {}, "key": "", "mesh": raw})
        paint.data["nodes"].append({"id": "o" + pid, "type": "object", "name": pid, "parent": None, "layer": "default",
                                    "visible": True, "locked": False, "prototype": pid, "modifiers": [], "material": mat,
                                    "transform": {"pos": [0, 300, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]}})
    paint.touch_geom()
    paint.save()
    protos = {p["id"]: p for p in dispatch("Paint", "scene_payload", {})["prototypes"]}
    has_uv = {pid: "ub" in p or "ub" in p.get("parts", {}) for pid, p in protos.items()}
    assert has_uv["pblend"] and not has_uv["pplain"], has_uv

    # Weights: seeding keeps each vertex's total at 1; painting one bone fully takes the skin over.
    dispatch("", "scene_new", {"name": "Arm"}, actor="agent")
    arm = dispatch("Arm", "add", {"kind": "lathe", "params": {"profile": [[0, 0], [10, 0], [10, 100], [0, 100]], "seg": 12}}, actor="agent")["id"]
    dispatch("Arm", "rig", {"action": "template", "template": "chain", "count": 2, "prefix": "seg"}, actor="agent")
    dispatch("Arm", "rig", {"action": "bind", "ids": [arm], "auto": True}, actor="agent")
    dispatch("Arm", "rig", {"action": "weights", "ids": [arm], "init": True}, actor="agent")
    seeded = mesh_of("Arm", arm)
    groups = [n for n in seeded.attrs if n.startswith("wt:")]
    assert len(groups) == 2 and all(abs(sum(seeded.attrs[g][i] for g in groups) - 1) < 1e-6 for i in range(len(seeded.verts)))
    dispatch("Arm", "rig", {"action": "weights", "ids": [arm], "bone": "seg_01", "mode": "set", "value": 0, "strength": 1}, actor="user")
    dispatch("Arm", "rig", {"action": "weights", "ids": [arm], "bone": "seg_02", "mode": "set", "value": 1, "strength": 1}, actor="user")
    scene = load("Arm")
    node = scene.node(arm)
    world = scene.world_mesh(node)
    bones = export_bones(scene.data["rigs"][0])
    j2 = [b["name"] for b in bones].index("seg_02")
    joints, weights = bind_weights(world.verts, bones, node["skin"], world.attrs)
    assert all(abs(sum(w for j, w in zip(js, ws) if j == j2) - 1) < 1e-6 for js, ws in zip(joints, weights))

    # IK: the hand lands on a reachable target at the keyed frame.
    dispatch("", "scene_new", {"name": "Reach"}, actor="agent")
    dispatch("Reach", "rig", {"action": "template", "template": "biped"}, actor="agent")
    dispatch("Reach", "anim", {"action": "new", "new_name": "Grab", "frames": 10}, actor="agent")
    bones = export_bones(load("Reach").data["rigs"][0])
    names = [b["name"] for b in bones]
    shoulder, hand = bones[names.index("upperarm_l")]["head"], bones[names.index("hand_l")]["head"]
    reach = math.dist(shoulder, hand)
    target = [shoulder[0] + 0.3 * reach, shoulder[1] - 0.5 * reach, shoulder[2] - 0.2 * reach]
    dispatch("Reach", "anim", {"action": "ik", "clip": "Grab", "bone": "hand_l", "target": target, "frame": 5}, actor="agent")
    grab = next(c for c in load("Reach").data["rigs"][0]["actions"] if c["name"] == "Grab")
    m = skin_matrices(bones, grab, 5)[names.index("hand_l")]
    got = [sum(m[i][j] * hand[j] for j in range(3)) + m[i][3] for i in range(3)]
    assert math.dist(got, target) < 0.5, (got, target)

    # Shape keys ship as glTF morph targets with a weights channel that reaches the keyed value.
    dispatch("", "scene_new", {"name": "Face"}, actor="agent")
    head = dispatch("Face", "add", {"kind": "box", "params": {"w": 40, "d": 40, "h": 40}}, actor="agent")["id"]
    dispatch("Face", "mesh_op", {"op": "subdivide", "smooth": True, "levels": 1}, actor="agent")
    dispatch("Face", "shape", {"id": head, "action": "add", "key": "Smile"}, actor="agent")
    dispatch("Face", "sculpt", {"id": head, "brush": "draw", "points": [[0, -20, 20]], "radius": 15, "strength": 4, "shape": "Smile"}, actor="agent")
    dispatch("Face", "rig", {"action": "template", "template": "single", "ids": [head]}, actor="agent")
    dispatch("Face", "anim", {"action": "new", "new_name": "Grin", "frames": 10}, actor="agent")
    dispatch("Face", "anim", {"action": "key", "clip": "Grin", "keys": [{"shape": "Smile", "frame": 0, "value": 0}, {"shape": "Smile", "frame": 10, "value": 1}]}, actor="agent")
    out = export_skinned(load("Face"), root / "face")
    meta, binary = _glb_json(Path(out[0]["glb"]))
    face = meta["meshes"][0]
    assert face["extras"]["targetNames"] == ["Smile"] and all(p.get("targets") for p in face["primitives"])
    grin = meta["animations"][0]
    morph = next(c for c in grin["channels"] if c["target"]["path"] == "weights")
    assert _floats(meta, binary, grin["samplers"][morph["sampler"]]["output"])[-1] == 1.0


def test_send() -> None:
    """Send against a fake listener that answers like UEFN: order, materials, the blend master, placement."""
    from backend.uefn import send_to_uefn

    os.environ["FORGE_SCENES_DIR"] = tempfile.mkdtemp(prefix="forge-send-")
    calls = []

    def listener(command, params, timeout=None):
        calls.append((command, params))
        base = params.get("destination_path", "")
        if command == "import_asset":
            stem = Path(params["source_file"]).stem
            return {"imported": [f"{base}/StaticMeshes/{stem}.{stem}"]}
        if command == "create_material_instance":
            return {"material_instance_path": f"{params['folder']}/{params['asset_name']}.{params['asset_name']}"}
        if command == "spawn_actor":
            return {"actor": {"label": params["label"]}}
        return {
            "get_project_info": {"content_root": "/Island/"},
            "forge_build_master": {"material_path": "/Island/Forge/Materials/M_Forge_Master.M_Forge_Master"},
            "forge_build_blend_master": {"material_path": "/Island/Forge/Materials/M_Forge_Blend.M_Forge_Blend"},
            "forge_import_skeletal": {"skeletal_mesh": f"{base}/SK_Guy", "skeleton": f"{base}/SK_Guy_Skeleton",
                                      "physics_asset": f"{base}/SK_Guy_PhysicsAsset", "animations": [f"{base}/Walk"]},
            "get_viewport_camera": {"location": {"x": 100, "y": 200, "z": 300}, "rotation": {"yaw": 90}},
            "animated_mesh_capabilities": {"device_assets": [{"name": "BP_AnimatedMeshDevice", "path": "/Game/Creative/Devices/AnimatedMesh/BP_AnimatedMeshDevice"}]},
            "validate_uefn_asset": {"ok": True, "valid": True, "errors": []},
        }.get(command, {"ok": True})

    # Untextured materials, a blend material, a rigged character, and a static prop in one send.
    dispatch("", "scene_new", {"name": "Guy"})
    dispatch("Guy", "batch", {"steps": [
        {"tool": "forge_material_set", "material": "Skin", "base_color": [0.9, 0.7, 0.5]},
        {"tool": "forge_material_set", "material": "Moss", "base_color": [0.2, 0.5, 0.1]},
        {"tool": "forge_material_set", "material": "Stone", "layers": ["$1"]},
        {"tool": "forge_add", "kind": "box", "object_name": "Body", "params": {"w": 40, "d": 20, "h": 170}, "material": "$0"},
        {"tool": "forge_add", "kind": "box", "object_name": "Rock", "params": {"w": 60, "d": 60, "h": 60}, "pos": [200, 0, 0], "material": "$2"},
        {"tool": "forge_rig", "action": "template", "template": "biped", "ids": ["$3"]},
        {"tool": "forge_rig", "action": "bind", "ids": ["$3"], "auto": True},
        {"tool": "forge_anim", "action": "generate", "kind": "walk"},
    ]})
    out = send_to_uefn("Guy", listener, place=True)
    seq = [c for c, _ in calls]
    assert seq.index("forge_import_skeletal") < seq.index("import_asset"), "skeletal first"
    skin = next(p for c, p in calls if c == "forge_set_mesh_materials" and "SK_Guy" in p["mesh_path"])
    assert "MI_SK_Guy_Skin" in skin["materials"][0], skin
    assert seq.count("forge_build_blend_master") == 1 and any(p.get("param_name") == "Layer1Color" for _, p in calls)
    spawn = [p for c, p in calls if c == "spawn_actor"]
    assert spawn[0]["asset_path"].endswith("BP_AnimatedMeshDevice_C") and len(spawn) == 2, spawn
    assert abs(spawn[0]["location"][1] - 1000) < 1e-6, spawn[0]  # yaw 90: 8 m along +Y from y = 200
    assert next(p for c, p in calls if c == "configure_animated_mesh")["anim_path"].endswith("/Walk")
    assert [a["mesh"].rsplit("/", 1)[1] for a in out["assets"]] == ["SK_Guy", "SM_Rock"], out["assets"]


if __name__ == "__main__":
    test_dock_parts_keep_every_triangle()
    test_look_styles()
    test_kernel_and_scene()
    test_rig_anim_batch()
    test_sculpt_paint_shapes()
    test_send()
    from .test_live import run as test_live
    test_live()
    import unittest
    from .test_regressions import SceneRegressions
    from .test_import import ImportTests
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in (SceneRegressions, ImportTests))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("forge self-check ok")
