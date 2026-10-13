"""Validate the built release artifact without installing or publishing it."""
import ast
import json
import re
import sys
import zipfile
from pathlib import Path


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None, "Damaged archive"
        manifest = json.loads(archive.read("plugin.json"))
        assert manifest["id"] == "forge"
        assert re.search(manifest["contributes"]["agent.tools"]["intent_pattern"], "import mesh")
        required = {"backend/__init__.py", "ui/forge.bundle.js", "assets/blend_export_script.txt", "THIRD_PARTY_NOTICES.md", "skills/forge/SKILL.md", "RELEASE_NOTES.md"}
        assert required <= names, required - names
        assert "backend/blend_export_script.py" not in names
        assert not any(n.startswith(("node_modules/", "ui/src/", "deploy/")) or "/test_" in n for n in names)
        assert b"three.js authors" in archive.read("THIRD_PARTY_NOTICES.md")
        for name in names:
            if name.endswith(".py") or name == "assets/blend_export_script.txt":
                ast.parse(archive.read(name).decode("utf-8"), filename=name)
        nodes = manifest["contributes"]["automations"]["nodes"]
        node = next(n for n in nodes if n["id"] == "forge.import_mesh")
        assert {"path", "name", "destination", "scale", "preview"} <= {f["id"] for f in node["config_fields"]}
        print(json.dumps({"ok": True, "version": manifest["version"], "files": len(names), "bytes": Path(path).stat().st_size, "external_converter": "assets/blend_export_script.txt"}))


if __name__ == "__main__":
    verify(sys.argv[1])
