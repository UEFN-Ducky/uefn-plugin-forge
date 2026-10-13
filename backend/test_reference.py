"""Exercise an already imported Mega City reference, preserving authored scene state."""
import json
import sys
import tempfile
from pathlib import Path
from .scene import load, dispatch, forget, list_scenes
from .blend_import import _inspect_data, import_blend
from .uefn import export_glbs


def verify(name):
    scene = load(name)
    before = scene._snapshot()
    names = list_scenes()
    miami = load("Miami_City_Collection").data["rev"]
    dispatch(name, "undo")
    dispatch(name, "redo")
    forget(name)
    scene = load(name)
    assert scene.data["nodes"] == before["nodes"], "Undo/redo changed geometry"
    assert scene.data["materials"] == before["materials"], "Undo/redo changed materials"
    inspected = _inspect_data(scene.data, scene.folder)
    for actual, expected in zip(inspected["dimensions_cm"], (3072, 3072, 1536)):
        assert abs(actual - expected) < 1, (actual, expected)
    with tempfile.TemporaryDirectory(prefix="forge-reference-roundtrip-") as folder:
        exported = export_glbs(scene, "all", Path(folder))
        assert len(exported) == 1, "Hidden collision/LODs leaked into export"
        preview = import_blend(exported[0]["glb"], preview=True)
        for actual, expected in zip(preview["dimensions_cm"], inspected["dimensions_cm"]):
            assert abs(actual - expected) < 1, (actual, expected)
        assert preview["triangles"] == 320, preview["triangles"]
    assert load("Miami_City_Collection").data["rev"] == miami
    assert len(list_scenes()) == len(names), "Preview created a scene"
    print(json.dumps({"ok": True, "scene": name, "dimensions_cm": inspected["dimensions_cm"], "visible_triangles": inspected["triangles"], "undo_redo_reopen": True, "glb_roundtrip": True, "miami_revision": miami}))


if __name__ == "__main__":
    verify(sys.argv[1])
