"""Tests for reference-video wiring in intake_node / creative_node (offline, mocked skill)."""

from __future__ import annotations

import asyncio

from pixelflow.creative.models import Brief, GlobalVisual
from pixelflow.nodes import creative_node, intake_node
from pixelflow.skills import StoryboardResult

# Demand-complete state so intake_node never hits the interrupt gate.
_COMPLETE_STATE = {
    "task_id": "t1",
    "product_info": {"product_name": "保温杯", "main_image_url": "https://x/m.jpg"},
    "video_params": {"platform": "douyin", "video_duration_sec": 30},
    "creative_direction": {"core_message": "长效保温", "creative_style": "情绪种草"},
}


class _FakeDecompose:
    """Records decompose calls; returns canned success or failure."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[str] = []

    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult:
        self.calls.append(video_url)
        if self.ok:
            return StoryboardResult(ok=True, shots=[{"description": "开场", "duration": 3}])
        return StoryboardResult(ok=False, error="boom")


# -- intake_node --


def test_intake_parses_pending_reference(monkeypatch):
    fake = _FakeDecompose()
    monkeypatch.setattr("pixelflow.nodes.get_video_decompose_skill", lambda: fake)

    state = {**_COMPLETE_STATE, "reference_videos": [{"url": "https://x/ref.mp4", "status": "pending"}]}
    out = asyncio.run(intake_node(state))

    assert fake.calls == ["https://x/ref.mp4"]
    ref = out["reference_videos"][0]
    assert ref["status"] == "done"
    assert ref["storyboard"] == [{"description": "开场", "duration": 3}]
    assert out["demand_complete"] is True


def test_intake_marks_failed_reference_non_blocking(monkeypatch):
    fake = _FakeDecompose(ok=False)
    monkeypatch.setattr("pixelflow.nodes.get_video_decompose_skill", lambda: fake)

    state = {**_COMPLETE_STATE, "reference_videos": [{"url": "https://x/ref.mp4"}]}
    out = asyncio.run(intake_node(state))

    ref = out["reference_videos"][0]
    assert ref["status"] == "failed"
    assert ref["error"] == "boom"
    # A failed reference is a warn, never a blocker.
    assert out["demand_complete"] is True


def test_intake_skips_done_and_failed_references(monkeypatch):
    fake = _FakeDecompose()
    monkeypatch.setattr("pixelflow.nodes.get_video_decompose_skill", lambda: fake)

    refs = [
        {"url": "https://x/done.mp4", "status": "done", "storyboard": [{"description": "已解析"}]},
        {"url": "https://x/failed.mp4", "status": "failed", "error": "old"},
        {"status": "pending"},  # no url
    ]
    state = {**_COMPLETE_STATE, "reference_videos": refs}
    out = asyncio.run(intake_node(state))

    assert fake.calls == []
    assert [r.get("status") for r in out["reference_videos"]] == ["done", "failed", "pending"]


def test_intake_no_references_never_builds_skill(monkeypatch):
    def _boom():
        raise AssertionError("skill should not be constructed")

    monkeypatch.setattr("pixelflow.nodes.get_video_decompose_skill", _boom)
    out = asyncio.run(intake_node(dict(_COMPLETE_STATE)))
    assert out["reference_videos"] == []


# -- creative_node --

_EMPTY_BRIEF = Brief(
    brief_id="b1",
    global_visual=GlobalVisual(subject_type="", environment="", lighting="", character_style="", overall_style="", forbidden_elements=""),
    shots=[],
    platform="douyin",
    duration_sec=30,
)


def _run_creative(monkeypatch, reference_videos):
    captured: dict = {}

    async def fake_brief_generate(**kwargs):
        captured.update(kwargs)
        return _EMPTY_BRIEF

    monkeypatch.setattr("pixelflow.nodes.brief_generate", fake_brief_generate)
    state = {**_COMPLETE_STATE, "reference_videos": reference_videos}
    asyncio.run(creative_node(state))
    return captured


def test_creative_without_references_is_original_mode(monkeypatch):
    captured = _run_creative(monkeypatch, [])
    assert captured["reference_analysis"] is None
    assert captured["creative_mode"] == "original"


def test_creative_single_reference_is_reference_mode(monkeypatch):
    refs = [{"url": "a", "status": "done", "storyboard": [{"description": "x", "duration": 2}]}]
    captured = _run_creative(monkeypatch, refs)
    assert captured["creative_mode"] == "reference"
    assert captured["reference_analysis"]["video_count"] == 1
    assert captured["reference_analysis"]["videos"][0]["shots"][0]["description"] == "x"


def test_creative_multiple_references_is_attribution_mode(monkeypatch):
    refs = [
        {"url": "a", "status": "done", "storyboard": [{"description": "x"}]},
        {"url": "b", "status": "done", "storyboard": [{"description": "y"}]},
    ]
    captured = _run_creative(monkeypatch, refs)
    assert captured["creative_mode"] == "attribution"
    assert captured["reference_analysis"]["video_count"] == 2


def test_creative_failed_reference_falls_back_to_original(monkeypatch):
    refs = [{"url": "a", "status": "failed", "error": "boom"}]
    captured = _run_creative(monkeypatch, refs)
    assert captured["reference_analysis"] is None
    assert captured["creative_mode"] == "original"
