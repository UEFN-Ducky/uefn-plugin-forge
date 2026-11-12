"""A slow send to UEFN answers still_running and goes on (Nov 11 2026: 7 forge_send_to_uefn
calls sat past five minutes with no word to the agent)."""

from __future__ import annotations

import contextvars
import threading

import pytest

from . import send_job

_who = contextvars.ContextVar("who", default="nobody")


def test_a_quick_send_returns_its_result_and_the_job_is_gone() -> None:
    jid = send_job.start(lambda progress: {"count": 2, "summary": "sent 2 meshes"})
    assert send_job.answer(jid, 5) == {"count": 2, "summary": "sent 2 meshes"}
    with pytest.raises(ValueError, match="No send job"):
        send_job.answer(jid, 0)


def test_a_slow_send_says_how_far_it_is_then_hands_over_the_result() -> None:
    go = threading.Event()
    seen = threading.Event()

    def run(progress: dict) -> dict:
        progress["of"] = 40
        progress["sent"] = 12
        seen.set()
        go.wait(10)
        progress["sent"] = 40
        return {"count": 40}

    jid = send_job.start(run)
    assert seen.wait(5)
    late = send_job.answer(jid, 0.05)
    assert (late["state"], late["job"], late["sent"], late["of"]) == ("still_running", jid, 12, 40)
    assert f"forge_send_to_uefn(job='{jid}')" in late["hint"] and "Don't send again" in late["hint"]
    assert send_job.answer(jid, 0.05)["state"] == "still_running"  # asking again does not restart it
    go.set()
    assert send_job.answer(jid, 5) == {"count": 40}


def test_the_sends_error_reaches_the_caller() -> None:
    def run(progress: dict) -> dict:
        raise RuntimeError("UEFN has no project open")

    with pytest.raises(RuntimeError, match="no project open"):
        send_job.answer(send_job.start(run), 5)


def test_the_send_runs_with_the_callers_context() -> None:
    """Editor calls made by the job keep the chat's identity (a ContextVar in Ducky)."""
    token = _who.set("chat-7")
    try:
        jid = send_job.start(lambda progress: {"who": _who.get()})
    finally:
        _who.reset(token)
    assert send_job.answer(jid, 5) == {"who": "chat-7"}


def test_a_finished_job_nobody_read_is_dropped_after_an_hour(monkeypatch) -> None:
    jid = send_job.start(lambda progress: {"count": 1})
    assert send_job._jobs[jid]["done"].wait(5)
    send_job._jobs[jid]["ended"] -= send_job.KEEP_S + 1
    other = send_job.start(lambda progress: {"count": 2})  # starting one prunes the stale one
    assert jid not in send_job._jobs
    assert send_job.answer(other, 5) == {"count": 2}
