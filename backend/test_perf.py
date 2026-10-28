"""Agent-path speed: a 50-step batch and a 20k-tri sculpt through the forge_* tools, with undo/redo exact.

Run: py -3 -m backend.test_perf        (PROFILE=1 prints the top cProfile rows)
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from . import tools
from .scene import forget, load
from .test_surface import _Api

# Measured on the dev PC before s-speed. A batch is already one commit (10 ms), so the 2x gate is on single
# calls: there 73% of the time is history.push / push_transform file I/O (index read + write + snapshot sweep).
BASELINE_BATCH_S = 0.010  # 50-step batch, reported only
BASELINE_CALLS_S = 0.391  # 50 single tool calls on a 20k-tri scene


def crate_steps(n=50):
    steps = [{"tool": "forge_add", "kind": "box", "object_name": "Crate"}]
    for k in range(1, n):
        steps.append([
            {"tool": "forge_add", "kind": "box", "pos": [k * 600, 0, 0], "params": {"w": 60, "d": 60, "h": 60}},
            {"tool": "forge_edit", "id": "$%d" % (k - 1), "rot": [0, 0, k]},
            {"tool": "forge_material_set", "material": "Wood", "base_color": [0.4, 0.3, k / 50]},
            {"tool": "forge_material_assign", "material": "Wood", "id": "$0"},
            {"tool": "forge_uv_project", "mode": "box", "id": "$%d" % (k - 1)},
        ][k % 5])
    return steps


def timed(fn, *a, **k):
    t = time.perf_counter()
    out = fn(*a, **k)
    return out, time.perf_counter() - t


class Perf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="forge-perf-")
        cls.patches = [patch.dict(os.environ, {"FORGE_SCENES_DIR": str(Path(cls.tmp.name) / "scenes")}),
                       patch.object(tools, "_attach_captures", lambda r: r)]
        for p in cls.patches:
            p.start()
        cls.api = _Api()
        tools.register_tools(cls.api)
        cls.t = cls.api.fns

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def scene(self, name):
        self.addCleanup(forget, name)
        self.t["forge_scene_new"](name=name)
        return name

    def state(self, name):
        data = load(name).data
        return json.dumps({k: data.get(k) for k in ("nodes", "materials", "prototypes")}, sort_keys=True, default=str)

    def heavy(self, name):
        """A scene with real geometry in it: a sculpted 20k-tri sphere, so saves and history cost what they do."""
        s = self.scene(name)
        self.t["forge_add"](name=s, kind="sphere", params={"radius": 100, "seg": 128, "rings": 80})
        self.t["forge_sculpt"](name=s, brush="draw", points=[[0, -100, 100]], radius=30, strength=3)
        return s

    def test_batch_50_and_undo_exact(self):
        s = self.heavy("PerfBatch")
        before = self.state(s)
        best = min(self._batch(s, before) for _ in range(3))
        print(f"\n50-step batch: {best * 1000:.0f} ms (baseline {BASELINE_BATCH_S * 1000:.0f} ms)")
        self.assertLessEqual(best, BASELINE_BATCH_S * 3, "batch regressed")

    def test_calls_50(self):
        s = self.heavy("PerfCalls")
        runs = []
        for _ in range(3):  # best of 3, as for the batch: other processes on the PC only ever add time
            t = time.perf_counter()
            for k in range(50):
                self.t["forge_edit"](name=s, rot=[0, 0, k])
                self.t["forge_material_set"](name=s, material="Wood", roughness=k / 50)
            runs.append((time.perf_counter() - t) / 2)
        took = min(runs)
        print(f"\n50 single calls: {took * 1000:.0f} ms (baseline {BASELINE_CALLS_S * 1000:.0f} ms)")
        if BASELINE_CALLS_S:
            self.assertLessEqual(took, BASELINE_CALLS_S / 2)

    def _batch(self, s, before):
        _, took = timed(self.t["forge_batch"], name=s, steps=crate_steps())
        after = self.state(s)
        self.t["forge_undo"](name=s)
        self.assertEqual(self.state(s), before, "undo restores the scene exactly")
        self.t["forge_redo"](name=s)
        self.assertEqual(self.state(s), after, "redo brings the batch back exactly")
        self.t["forge_undo"](name=s)
        return took

    def test_sculpt_20k(self):
        s = self.scene("PerfSculpt")
        self.t["forge_add"](name=s, kind="sphere", params={"radius": 100, "seg": 128, "rings": 80})
        tris = load(s).local_mesh(load(s).last_object()).tri_count()
        self.assertGreaterEqual(tris, 20000)
        _, took = timed(self.t["forge_sculpt"], name=s, brush="draw", points=[[0, -100, 100]], radius=30, strength=3)
        print(f"\nsculpt on {tris} tris: {took * 1000:.0f} ms")


if __name__ == "__main__":
    if os.environ.get("PROFILE"):
        import cProfile
        import pstats

        cProfile.run("unittest.main(exit=False)", sort="cumulative", filename=str(Path(tempfile.gettempdir()) / "forge_perf.prof"))
        pstats.Stats(str(Path(tempfile.gettempdir()) / "forge_perf.prof")).sort_stats(os.environ["PROFILE"] if os.environ["PROFILE"] != "1" else "tottime").print_stats(25)
    else:
        unittest.main()
