"""Serialized plugin jobs with cooperative cancellation and bounded retention."""
import concurrent.futures
import secrets
import threading
import time

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="forge-model")
_lock = threading.Lock()
_jobs = {}
_context = threading.local()


def checkpoint(stage=None):
    row = getattr(_context, "row", None)
    if row is not None:
        if stage:
            row["stage"] = stage
        if row["cancel"].is_set():
            raise ValueError("Import cancelled; no scene was changed")


def cancellable():
    return getattr(_context, "row", None) is not None


def submit(fn, params):
    with _lock:
        now = time.monotonic()
        for key, row in list(_jobs.items()):
            if row["future"].done() and now - row["started"] > 1800:
                del _jobs[key]
        if len(_jobs) >= 32:
            return {"ok": False, "error": "Forge is busy; wait for the current operation to finish"}
        key = secrets.token_hex(12)
        row = {"started": now, "cancel": threading.Event(), "stage": "Queued"}
        def work():
            _context.row = row
            try:
                checkpoint("Reading file")
                return fn(**params)
            finally:
                _context.row = None
        row["future"] = _pool.submit(work)
        _jobs[key] = row
    try:
        result = row["future"].result(timeout=0.008)
        with _lock:
            _jobs.pop(key, None)
        return {"ok": True, "done": True, "result": result}
    except concurrent.futures.TimeoutError:
        return {"ok": True, "done": False, "job": key}
    except Exception as exc:
        with _lock:
            _jobs.pop(key, None)
        return {"ok": False, "error": str(exc)}


def cancel(job):
    with _lock:
        row = _jobs.get(job)
        if not row:
            return {"ok": False, "error": "Job already finished or expired"}
        row["cancel"].set()
    return {"ok": True, "requested": True}


def poll(job):
    with _lock:
        row = _jobs.get(job)
        if row is None:
            return {"ok": False, "error": "Forge job expired; refresh the scene"}
        future = row["future"]
        if not future.done():
            return {"ok": True, "done": False, "stage": row["stage"],
                    "elapsed": round(time.monotonic() - row["started"], 1)}
        _jobs.pop(job, None)
    try:
        return {"ok": True, "done": True, "result": future.result()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
