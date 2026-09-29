"""Serialized plugin jobs: return promptly to the panel while modeling runs."""
import concurrent.futures
import secrets
import threading
import time

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="forge-model")
_lock = threading.Lock()
_jobs = {}

def submit(fn, params):
    with _lock:
        now = time.monotonic()
        for key, (future, started, delivered) in list(_jobs.items()):
            if future.done() and delivered and now - delivered > 30:
                del _jobs[key]
        if len(_jobs) >= 32:
            return {"ok": False, "error": "Forge is busy; wait for the current operation to finish"}
        key = secrets.token_hex(12)
        future = _pool.submit(fn, **params)
        _jobs[key] = (future, now, 0)
    try:
        result = future.result(timeout=0.008)
        with _lock: _jobs.pop(key, None)
        return {"ok": True, "done": True, "result": result}
    except concurrent.futures.TimeoutError:
        return {"ok": True, "done": False, "job": key}
    except Exception as exc:
        with _lock: _jobs.pop(key, None)
        return {"ok": False, "error": str(exc)}

def poll(job):
    with _lock:
        row = _jobs.get(job)
        if row is None:
            return {"ok": False, "error": "Forge job expired; refresh the scene"}
        future, started, delivered = row
        if not future.done():
            return {"ok": True, "done": False, "elapsed": round(time.monotonic()-started, 1)}
        _jobs.pop(job, None)
    try:
        return {"ok": True, "done": True, "result": future.result()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
