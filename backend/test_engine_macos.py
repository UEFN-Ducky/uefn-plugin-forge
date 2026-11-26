"""On a Mac the Forge engine pins the arm64 CPython + bpy wheel and lives in Application Support."""

import importlib
import platform
import sys
from pathlib import Path

import pytest

from .engine import runtime


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.delenv("FORGE_ENGINE_DIR", raising=False)
    monkeypatch.delenv("FORGE_ENGINE_PYTHON", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    yield importlib.reload(runtime)
    monkeypatch.undo()
    importlib.reload(runtime)


def test_mac_pins_arm64_python_and_bpy(mac):
    assert mac.IS_MAC and mac.IS_POSIX and not mac.IS_LINUX
    assert "aarch64-apple-darwin" in mac._FILES[mac._PY_ARCHIVE][0]
    assert mac._BPY_WHEEL.endswith("macosx_11_0_arm64.whl") and mac._BPY_WHEEL in mac._FILES
    assert all(len(sha) == 64 for _, sha, _ in mac._FILES.values())
    assert mac.root() == Path.home() / "Library" / "Application Support" / "UEFN-Ducky" / "forge-engine"
    assert mac.python() == mac.root() / "py" / "bin" / "python3"


def test_intel_mac_is_refused_before_any_download(mac, monkeypatch):
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    with pytest.raises(ValueError, match="Apple Silicon"):
        mac._provision()
