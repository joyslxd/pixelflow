"""Regression test for poll_task status handling (offline, make_request mocked).

The Borgrise API returns lowercase task statuses ("pending"/"processing"/
"completed"/"failed"); poll_task must match them case-insensitively or it polls
until timeout even after the task is done.
"""

from __future__ import annotations

from pixelflow.skills.borgrise import run_generation


def _patch(monkeypatch, responses):
    calls = iter(responses)
    monkeypatch.setattr(run_generation, "make_request", lambda *a, **k: next(calls))
    monkeypatch.setattr(run_generation.time, "sleep", lambda *_: None)


def test_poll_returns_on_lowercase_completed(monkeypatch):
    _patch(
        monkeypatch,
        [
            {"data": {"status": "pending"}},
            {"data": {"status": "processing"}},
            {"data": {"status": "completed", "result": {"video_url": "https://x/v.mp4"}}},
        ],
    )
    result = run_generation.poll_task("t1")
    assert not result.get("error")
    assert run_generation.extract_video_url(result) == "https://x/v.mp4"


def test_poll_returns_error_on_lowercase_failed(monkeypatch):
    _patch(monkeypatch, [{"data": {"status": "failed", "error": "boom"}}])
    result = run_generation.poll_task("t1")
    assert result.get("error")
    assert result.get("message") == "boom"
