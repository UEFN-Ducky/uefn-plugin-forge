"""Validate the built release artifact without installing or publishing it.

`py -3 -m backend.test_release <zip>` checks one built zip; with no argument it runs the tests.
"""
import ast
import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

# Run by the engine's own CPython 3.13 (see scripts/build_zip.py ENGINE_SOURCE): source in every build.
ENGINE_SOURCE = ("backend/engine/worker.py", "backend/engine/ops/")


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None, "Damaged archive"
        manifest = json.loads(archive.read("plugin.json"))
        assert manifest["id"] == "forge"
        assert re.search(manifest["contributes"]["agent.tools"]["intent_pattern"], "import mesh")
        compiled = any(n.endswith(".pyd") for n in names)
        required = {"ui/forge.bundle.js", "assets/blend_export_script.txt", "THIRD_PARTY_NOTICES.md", "skills/forge/SKILL.md", "RELEASE_NOTES.md",
                    "backend/engine/worker.py", "backend/engine/ops/__init__.py", "backend/engine/ops/bridge.py"}
        if not compiled:
            required.add("backend/__init__.py")
        assert required <= names, required - names
        assert not any(n.startswith(("node_modules/", "deploy/")) or "/test_" in n for n in names)
        if compiled:  # the Store build: no Python source in backend/ except the engine's (the build engine also ships ui/src/ minified)
            assert not any(n.startswith("backend/") and n.endswith(".py") and not n.startswith(ENGINE_SOURCE) for n in names)
        else:
            assert not any(n.startswith("ui/src/") for n in names)
        assert b"three.js authors" in archive.read("THIRD_PARTY_NOTICES.md")
        for name in names:
            if name.endswith(".py") or name == "assets/blend_export_script.txt":
                ast.parse(archive.read(name).decode("utf-8"), filename=name)
        nodes = manifest["contributes"]["automations"]["nodes"]
        node = next(n for n in nodes if n["id"] == "forge.import_mesh")
        assert {"path", "name", "destination", "scale", "preview"} <= {f["id"] for f in node["config_fields"]}
        print(json.dumps({"ok": True, "version": manifest["version"], "files": len(names), "bytes": Path(path).stat().st_size, "external_converter": "assets/blend_export_script.txt"}))


def _build_zip_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        import build_zip
    finally:
        sys.path.pop(0)
    return build_zip


class ReleaseTests(unittest.TestCase):
    def test_engine_files_ship_as_source(self):
        build_zip = _build_zip_module()
        self.assertEqual(build_zip.ENGINE_SOURCE, ENGINE_SOURCE)
        engine = [rel for rel, _ in build_zip.engine_source_files()]
        self.assertIn("backend/engine/worker.py", engine)
        self.assertIn("backend/engine/ops/bridge.py", engine)
        with tempfile.TemporaryDirectory(prefix="forge-release-test-") as tmp:
            tmp = Path(tmp)
            with patch.object(build_zip, "OUT_DIR", tmp):  # keeps the version ledger and deploy/ untouched
                plain = build_zip.build_zip(out=tmp / "plain.ducky-plugin.zip")
            verify(plain)
            # Stand-in for the compiled build: backend source replaced by a .pyd, then the engine files added back.
            compiled = tmp / "compiled.ducky-plugin.zip"
            with zipfile.ZipFile(plain) as src, zipfile.ZipFile(compiled, "w", zipfile.ZIP_DEFLATED) as out:
                for name in src.namelist():
                    if not (name.startswith("backend/") and name.endswith(".py")):
                        out.writestr(name, src.read(name))
                out.writestr("uefn_plugin_forge.v1.cp313-win_amd64.pyd", b"MZ")
            build_zip.add_engine_source(compiled)
            verify(compiled)
            for archive_path in (plain, compiled):
                with zipfile.ZipFile(archive_path) as archive:
                    for rel in engine:
                        text = archive.read(rel).decode("utf-8")
                        ast.parse(text, filename=rel)
                        self.assertEqual(text.encode("utf-8"), (build_zip.ROOT / rel).read_bytes(), rel)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        verify(sys.argv[1])
    else:
        unittest.main()
