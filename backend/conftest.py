"""Under pytest without bpy, each bpy test script runs as one item in the Forge engine Python.

Those scripts relaunch themselves there and sys.exit() at import, which pytest cannot collect.
"""
import importlib.util
import os
import subprocess

import pytest

HAS_BPY = importlib.util.find_spec("bpy") is not None
ENGINE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UEFN-Ducky", "forge-engine", "py", "python.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAUNCH = '"forge-engine", "py", "python.exe"'


def pytest_pycollect_makemodule(module_path, parent):
    if HAS_BPY or RELAUNCH not in module_path.read_text(encoding="utf-8"):
        return None
    return EngineScript.from_parent(parent, path=module_path)


class EngineScript(pytest.File):
    def collect(self):
        yield EngineRun.from_parent(self, name="engine")


class EngineRun(pytest.Item):
    def runtest(self):
        assert os.path.exists(ENGINE), "bpy engine not installed (forge-engine/py/python.exe missing)"
        mod = "backend." + self.path.stem
        code = f"import sys,runpy;sys.path.insert(0,{ROOT!r});runpy.run_module({mod!r},run_name='__main__')"
        r = subprocess.run([ENGINE, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, (r.stdout[-3000:] + r.stderr[-3000:])

    def reportinfo(self):
        return self.path, None, self.path.stem + " (forge engine)"
