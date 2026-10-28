"""Host side of the warm bpy worker: start, call, timeout, cancel, restart after a crash."""
import itertools
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from . import runtime

# Plugin root/backend/engine, also in the compiled build (its modules report <root>/uefn_plugin_forge/...).
_WORKER = Path(__file__).resolve().parents[2] / "backend" / "engine" / "worker.py"
_START_S = 120


class EngineError(ValueError):
    pass


def scratch():
    path = runtime.root() / "scratch"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_cancelled():
    from .. import jobs
    if not jobs.cancellable():
        return False
    try:
        jobs.checkpoint()
        return False
    except ValueError:
        return True


class Worker:
    """One warm bpy process. Calls are serialized; bpy is single-threaded anyway."""

    def __init__(self):
        self.proc = None
        self.ops = []
        self.broken = {}
        self._lines = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _start(self):
        status = runtime.engine_status()
        if status["state"] != "ready":
            if status["state"] == "missing":
                status = runtime.install()
            raise EngineError("Blender engine is %s (%s%%); try again when it is ready." % (status["state"], status.get("progress", 0))
                              if status["state"] != "error" else "Blender engine install failed: " + status.get("error", ""))
        folder = scratch()
        self.log = folder / "worker.log"
        env = {**os.environ, "FORGE_ENGINE_SCRATCH": str(folder), "PYTHONNOUSERSITE": "1"}
        env.pop("PYTHONPATH", None)
        with open(self.log, "wb") as log:  # one log per worker life, so the tail is this crash
            self.proc = subprocess.Popen([str(runtime.python()), "-u", str(_WORKER)], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=log, env=env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._lines = queue.Queue()
        threading.Thread(target=self._read, args=(self.proc, self._lines), daemon=True, name="forge-engine-read").start()
        hello = self._next(time.monotonic() + _START_S, lambda: False, "start")
        self.ops, self.broken = hello.get("ops", []), hello.get("broken", {})

    @staticmethod
    def _read(proc, lines):
        with proc.stdout:
            for line in proc.stdout:
                lines.put(line)
        lines.put(None)

    def _tail(self):
        try:
            return self.log.read_text(encoding="utf-8", errors="replace").strip()[-600:]
        except OSError:
            return ""

    def _next(self, deadline, cancel, op):
        while True:
            if cancel():
                self.stop()
                raise EngineError("Cancelled %s; the engine restarts on the next call, the scene is unchanged" % op)
            try:
                line = self._lines.get(timeout=0.05)
            except queue.Empty:
                if time.monotonic() > deadline:
                    self.stop()
                    raise EngineError("Engine op %s timed out; the engine restarts on the next call" % op) from None
                continue
            if line is None:
                self.stop()
                raise EngineError("Blender engine stopped during %s; it restarts on the next call. Log: %s" % (op, self._tail()))
            return json.loads(line)

    def call(self, op, params=None, timeout=300.0, cancel=None):
        cancel = cancel or _job_cancelled
        with self._lock:
            if not self.alive():
                self._start()
            rid = next(self._ids)
            try:
                self.proc.stdin.write((json.dumps({"id": rid, "op": op, "params": params or {}}) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except OSError:
                self.stop()
                raise EngineError("Blender engine was not running; it restarts on the next call") from None
            deadline = time.monotonic() + timeout
            while True:
                msg = self._next(deadline, cancel, op)
                if msg.get("id") == rid:
                    break
        if not msg.get("ok"):
            raise EngineError(msg.get("error") or op + " failed")
        return msg.get("result")

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdin.close()


_worker = Worker()


def call(op, params=None, timeout=300.0, cancel=None):
    """Run one engine op in the warm worker; starts it (or the engine install) when needed."""
    return _worker.call(op, params, timeout, cancel)


def warm():
    """Start the worker in the background when the engine is ready; a no-op otherwise."""
    if runtime.engine_status()["state"] == "ready" and not _worker.alive():
        threading.Thread(target=lambda: _safe_ping(), daemon=True, name="forge-engine-warm").start()


def _safe_ping():
    try:
        call("ping", timeout=_START_S)
    except EngineError:
        pass


def stop():
    _worker.stop()
