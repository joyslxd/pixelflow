"""Tests for the EDIT phase Timeline IR builder (pure logic)."""

from __future__ import annotations

from pixelflow.edit import build_timeline


def _shot(i, **kw):
    base = {
        "shot_id": f"shot_{i:03d}",
        "duration": 5.0,
        "transition_in": "fade",
        "transition_out": "cut",
        "narration_text": f"旁白{i}",
        "onscreen_text": f"花字{i}",
    }
    base.update(kw)
    return base


def _asset(i, ok=True, url="https://x/clip.mp4"):
    return {"shot_index": i, "ok": ok, "url": url if ok else None}


_BRIEF = {
    "ratio": "9:16",
    "size": "1080x1920",
    "platform": "douyin",
    "shots": [_shot(0), _shot(1)],
}


def test_clips_built_in_shot_order_with_metadata():
    timeline, notes = build_timeline(_BRIEF, [_asset(0), _asset(1)])
    assert [c.shot_index for c in timeline.clips] == [0, 1]
    assert notes == []
    c0 = timeline.clips[0]
    assert c0.source_url == "https://x/clip.mp4"
    assert c0.duration == 5.0
    assert c0.transition_in == "fade"
    assert c0.narration_text == "旁白0"
    assert c0.onscreen_text == "花字0"
    assert timeline.ratio == "9:16"
    assert timeline.platform == "douyin"
    assert timeline.total_duration == 10.0


def test_failed_shot_is_skipped_with_note():
    timeline, notes = build_timeline(_BRIEF, [_asset(0), _asset(1, ok=False)])
    assert [c.shot_index for c in timeline.clips] == [0]
    assert len(notes) == 1
    assert "分镜 1" in notes[0]
    assert timeline.total_duration == 5.0


def test_missing_asset_is_skipped():
    # only shot 0 has an asset; shot 1 has none
    timeline, notes = build_timeline(_BRIEF, [_asset(0)])
    assert [c.shot_index for c in timeline.clips] == [0]
    assert len(notes) == 1


def test_no_shots_yields_empty_timeline():
    timeline, notes = build_timeline({"shots": []}, [])
    assert timeline.clips == []
    assert timeline.total_duration == 0.0
    assert notes == []
    # defaults applied
    assert timeline.ratio == "9:16"
    assert timeline.size == "1080x1920"


def test_all_failed_yields_empty_timeline_with_notes():
    timeline, notes = build_timeline(_BRIEF, [_asset(0, ok=False), _asset(1, ok=False)])
    assert timeline.clips == []
    assert len(notes) == 2
