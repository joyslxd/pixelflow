"""Tests for the EDIT draft-plan builder (pure logic)."""

from __future__ import annotations

from pixelflow.edit import build_draft_plan


def _clip(url, duration, **kw):
    base = {"source_url": url, "duration": duration, "transition_in": "", "onscreen_text": ""}
    base.update(kw)
    return base


def test_canvas_parsed_from_size():
    plan = build_draft_plan({"size": "1080x1920", "clips": []})
    assert (plan.width, plan.height) == (1080, 1920)
    assert plan.fps == 30
    assert plan.segments == []


def test_bad_size_falls_back_to_default():
    plan = build_draft_plan({"size": "garbage", "clips": []})
    assert (plan.width, plan.height) == (1080, 1920)


def test_clips_placed_end_to_end():
    timeline = {
        "size": "720x1280",
        "clips": [
            _clip("https://x/a.mp4", 3.0, onscreen_text="花字A"),
            _clip("https://x/b.mp4", 4.5, transition_in="叠化"),
        ],
    }
    plan = build_draft_plan(timeline)
    assert (plan.width, plan.height) == (720, 1280)
    s0, s1 = plan.segments
    assert s0.start == 0.0 and s0.duration == 3.0
    assert s0.caption == "花字A"
    assert s1.start == 3.0 and s1.duration == 4.5  # cursor advanced by s0 duration
    assert s1.transition_in == "叠化"


def test_custom_fps():
    plan = build_draft_plan({"clips": []}, fps=24)
    assert plan.fps == 24
