"""Forge kernel self-check. No host, no UEFN."""

from __future__ import annotations

import json
import os
import struct
import tempfile
import time
from pathlib import Path

from backend.gltf import write_glb
from backend.kernel.csg import boolean
from backend.kernel.mesh import box, sphere
from backend.kernel.ops import bevel_edges, extrude_faces
from backend.render import render_png
from backend.scene import dispatch
from backend.textures import make_seamless, normal_from_height, to_pot
from PIL import Image


def test_kernel_and_scene() -> None:
    root = Path(tempfile.mkdtemp(prefix="forge-"))
    os.environ["FORGE_SCENES_DIR"] = str(root)
    os.environ["FORGE_CAPTURES_DIR"] = str(root / "caps")

    m = box(100, 100, 100)
    assert m.tri_count() == 12
    before = m.tri_count()
    extrude_faces(m, [0], 10)
    assert m.tri_count() > before
    m = box()
    assert bevel_edges(m, m.edge_keys()[:4], 6) >= 1

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

    dispatch("", "scene_new", {"name": "Crowd"}, actor="user")
    dispatch("Crowd", "add", {"kind": "box", "count": 20000}, actor="user")
    from backend.scene import load as load_scene

    crowd = load_scene("Crowd")
    assert len(crowd.data["prototypes"]) == 1
    t_crowd = time.perf_counter()
    packed = crowd.payload()
    assert time.perf_counter() - t_crowd < 0.05, "instance payload over 50 ms"
    assert len(packed["prototypes"]) == 1
    assert len(packed["instances"]) == 20000
    assert packed["prototypes"][0]["tris"] == 12

    t1 = time.perf_counter()
    pngs = render_png(mesh, root / "caps" / "crate.png", ["three"])
    assert time.perf_counter() - t1 < 0.3
    assert Path(pngs[0]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

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


if __name__ == "__main__":
    test_kernel_and_scene()
    print("forge self-check ok")
