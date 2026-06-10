"""Tests for summarize_storyboards — pure reference-video analysis (offline)."""

from __future__ import annotations

from pixelflow.intake import summarize_storyboards
from pixelflow.intake.reference_summary import MAX_SHOTS_PER_VIDEO


def test_none_and_empty_return_none():
    assert summarize_storyboards(None) is None
    assert summarize_storyboards([]) is None


def test_refs_without_storyboard_return_none():
    refs = [{"url": "https://x/a.mp4", "status": "failed"}, {"url": "https://x/b.mp4", "storyboard": []}]
    assert summarize_storyboards(refs) is None


def test_normalizes_vendor_keys():
    refs = [
        {
            "url": "https://x/a.mp4",
            "storyboard": [
                {"duration_sec": "3.5", "visual_description": "开场特写", "camera_movement": "推"},
                {"time": 2, "desc": "产品展示", "shot_type": "近景"},
            ],
        }
    ]
    out = summarize_storyboards(refs)
    assert out["video_count"] == 1
    video = out["videos"][0]
    assert video["url"] == "https://x/a.mp4"
    assert video["shot_count"] == 2
    assert video["shots"][0] == {"index": 0, "duration": 3.5, "description": "开场特写", "camera": "推"}
    assert video["shots"][1] == {"index": 1, "duration": 2.0, "description": "产品展示", "camera": "近景"}
    assert video["total_duration"] == 5.5
    assert video["avg_shot_duration"] == 2.75


def test_caps_shots_but_reports_full_count():
    board = [{"description": f"s{i}", "duration": 1} for i in range(20)]
    out = summarize_storyboards([{"url": "u", "storyboard": board}])
    video = out["videos"][0]
    assert len(video["shots"]) == MAX_SHOTS_PER_VIDEO
    assert video["shot_count"] == 20


def test_non_dict_shot_becomes_description():
    out = summarize_storyboards([{"url": "u", "storyboard": ["纯文本分镜"]}])
    assert out["videos"][0]["shots"][0] == {"index": 0, "description": "纯文本分镜"}


def test_bad_duration_is_skipped_not_crashing():
    out = summarize_storyboards([{"url": "u", "storyboard": [{"duration": "abc", "description": "d"}]}])
    shot = out["videos"][0]["shots"][0]
    assert "duration" not in shot
    assert shot["description"] == "d"


def test_multiple_videos_counted():
    refs = [
        {"url": "a", "storyboard": [{"description": "x"}]},
        {"url": "b", "storyboard": [{"description": "y"}]},
    ]
    out = summarize_storyboards(refs)
    assert out["video_count"] == 2
