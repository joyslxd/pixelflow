"""Tests for the EDIT phase Timeline IR builder (pure logic, segment-based)."""

from __future__ import annotations

from pixelflow.edit import build_timeline


def _asset(i, ok=True, url="https://x/clip.mp4", duration=5.0):
    return {"segment_index": i, "shot_indices": [i], "duration": duration, "ok": ok, "url": url if ok else None}


_BRIEF = {"ratio": "9:16", "size": "1080x1920", "platform": "douyin"}


def test_clips_built_in_segment_order():
    timeline, notes = build_timeline(_BRIEF, [_asset(0, duration=8.0), _asset(1, duration=7.0)])
    assert [c.shot_index for c in timeline.clips] == [0, 1]
    assert notes == []
    c0 = timeline.clips[0]
    assert c0.source_url == "https://x/clip.mp4"
    assert c0.shot_id == "seg_000"
    assert c0.duration == 8.0
    assert timeline.ratio == "9:16"
    assert timeline.platform == "douyin"
    assert timeline.total_duration == 15.0


def test_failed_segment_is_skipped_with_note():
    timeline, notes = build_timeline(_BRIEF, [_asset(0), _asset(1, ok=False)])
    assert [c.shot_index for c in timeline.clips] == [0]
    assert len(notes) == 1
    assert "片段 1" in notes[0]
    assert timeline.total_duration == 5.0


def test_no_segments_yields_empty_timeline():
    timeline, notes = build_timeline({}, [])
    assert timeline.clips == []
    assert timeline.total_duration == 0.0
    assert notes == []
    assert timeline.ratio == "9:16"
    assert timeline.size == "1080x1920"


def test_all_failed_yields_empty_timeline_with_notes():
    timeline, notes = build_timeline(_BRIEF, [_asset(0, ok=False), _asset(1, ok=False)])
    assert timeline.clips == []
    assert len(notes) == 2
