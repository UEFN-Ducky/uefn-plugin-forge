"""A send to UEFN as a job, so a slow one answers instead of holding the agent.

Nov 11 2026: 7 forge_send_to_uefn calls sat past five minutes with no word to the agent,
which then could not tell a slow import from a hung one. A send that is not done within
``WAIT_S`` answers ``still_running`` with how far it is; the send goes on, and a call with
its ``job`` returns the result (the same shape forge_shot_render uses).
"""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from typing import Any, Callable

WAIT_S = 280.0  # under the 300 s at which Ducky tells an agent an editor call is late
KEEP_S = 3600.0  # a finished job nobody read is dropped after this

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _prune(now: float) -> None:
    for jid in [j for j, rec in _jobs.items() if rec["done"].is_set() and now - rec["ended"] > KEEP_S]:
        _jobs.pop(jid, None)


def start(run: Callable[[dict[str, int]], dict]) -> str:
    """Run ``run(progress)`` on its own thread (with the caller's context, so editor calls
    keep the chat's identity) and return the job id. ``run`` fills progress["sent"/"of"]."""
    rec: dict[str, Any] = {"id": uuid.uuid4().hex[:8], "done": threading.Event(), "ended": 0.0,
                           "progress": {"sent": 0, "of": 0}, "result": None, "error": None}
    ctx = contextvars.copy_context()

    def work() -> None:
        try:
            rec["result"] = ctx.run(run, rec["progress"])
        except BaseException as exc:  # noqa: BLE001 - handed to whoever reads the job
            rec["error"] = exc
        finally:
            rec["ended"] = time.monotonic()
            rec["done"].set()

    with _lock:
        _prune(time.monotonic())
        _jobs[rec["id"]] = rec
    threading.Thread(target=work, name="forge-send", daemon=True).start()
    return rec["id"]


def answer(job: str, wait_seconds: float = WAIT_S) -> dict:
    """The job's result once it is done; ``still_running`` with its progress when it is not
    within ``wait_seconds``. A finished job is handed out once. Re-raises the send's error."""
    with _lock:
        rec = _jobs.get(str(job or "").strip())
    if rec is None:
        raise ValueError(f"No send job {job!r}: its result was already read, or Ducky restarted. "
                         "Look in the island's Forge folder before sending again.")
    if not rec["done"].wait(max(0.0, min(1800.0, float(wait_seconds or 0)))):
        progress = rec["progress"]
        return {"ok": True, "state": "still_running", "job": rec["id"],
                "sent": int(progress.get("sent") or 0), "of": int(progress.get("of") or 0),
                "hint": f"UEFN is still importing. Call forge_send_to_uefn(job='{rec['id']}') for the result. "
                        "Don't send again: that starts the same imports a second time."}
    with _lock:
        _jobs.pop(rec["id"], None)
    if rec["error"] is not None:
        raise rec["error"]
    return rec["result"]
