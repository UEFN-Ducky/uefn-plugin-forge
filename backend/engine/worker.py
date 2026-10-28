"""Warm bpy worker. Runs inside the engine's Python: JSON lines in on stdin, JSON lines out.

Request {"id", "op", "params"} -> reply {"id", "ok", "result" | "error", "ms"}. Big arrays
travel as .npy files in FORGE_ENGINE_SCRATCH; the line only carries their paths.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path


def main():
    # bpy and its C code print to fd 1; keep that off the protocol channel.
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bpy
    import ops

    table, broken = ops.discover()
    table["ping"] = lambda p: {"blender": bpy.app.version_string}
    table["sleep"] = lambda p: time.sleep(float(p.get("seconds", 1))) or {}
    fresh = {"ping", "sleep"}  # no scene, so no factory reset
    proto.write(json.dumps({"ready": True, "ops": sorted(table), "broken": broken}) + "\n")
    for line in sys.stdin:
        if not line.strip():
            continue
        start = time.perf_counter()
        req = json.loads(line)
        name = req.get("op", "")
        reply = {"id": req.get("id")}
        try:
            fn = table.get(name)
            if fn is None:
                raise ValueError("No engine op %r. Ops: %s" % (name, ", ".join(sorted(table))))
            if name not in fresh:
                bpy.ops.wm.read_factory_settings(use_empty=True)
            reply.update(ok=True, result=fn(req.get("params") or {}))
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            reply.update(ok=False, error="%s: %s" % (type(exc).__name__, exc) if not isinstance(exc, ValueError) else str(exc))
        reply["ms"] = round((time.perf_counter() - start) * 1000, 1)
        proto.write(json.dumps(reply) + "\n")


if __name__ == "__main__":
    main()
