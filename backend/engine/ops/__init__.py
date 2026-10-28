"""Engine-side ops: every op_<name>(params) -> dict in engine/ops/*.py, found at worker start.

Only the bpy worker imports this package (as top-level `ops`); the plugin's Python never does.
A feature adds a file here; nothing else changes.
"""
import importlib
import os
import pkgutil
from pathlib import Path


def discover():
    """({op name: fn}, {module: error}). A broken feature file never takes the others down."""
    table, broken = {}, {}
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        try:
            module = importlib.import_module(__name__ + "." + info.name)
        except Exception as exc:
            broken[info.name] = "%s: %s" % (type(exc).__name__, exc)
            continue
        for attr in dir(module):
            if attr.startswith("op_") and callable(getattr(module, attr)):
                table[attr[3:]] = getattr(module, attr)
    return table, broken


def scratch():
    """Folder for .npy hand-offs and exports; set by the host client."""
    return Path(os.environ.get("FORGE_ENGINE_SCRATCH") or ".")
