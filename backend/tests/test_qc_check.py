"""Tests for the QC phase verdict (pure logic, segment-based coverage)."""

from __future__ import annotations

from pixelflow.qc import qc_check


def _brief(duration_sec=30, tolerance="+2s"):
    return {"duration_sec": duration_sec, "hard_constraints": {"total_duration_tolerance": tolerance}}


def _assets(n):
    return [{"segment_index": i, "ok": True, "url": "https://x/c.mp4"} for i in range(n)]


def _timeline(n_clips, total_duration=30.0):
    return {"clips": [{"shot_index": i} for i in range(n_clips)], "total_duration": total_duration}


def _status(result, item):
    return next(c.status for c in result.check_results if c.item == item)


def test_full_coverage_on_target_passes():
    result = qc_check(_brief(), _assets(2), _timeline(2, 30.0))
    assert result.passed
    assert _status(result, "片段完整性") == "pass"
    assert _status(result, "时长达标") == "pass"
    assert 0 < result.score <= 1.0  # 含 P0 占位 warn(产品一致性等),非满分


def test_missing_clip_fails_and_scores_partial():
    result = qc_check(_brief(), _assets(3), _timeline(2, 20.0))
    assert not result.passed  # blocking -> routes back to GENERATE
    assert _status(result, "片段完整性") == "fail"
    assert result.score < 1.0


def test_duration_drift_warns_not_fails():
    result = qc_check(_brief(duration_sec=30, tolerance="+2s"), _assets(2), _timeline(2, 40.0))
    assert result.passed  # warn must not flip passed
    assert _status(result, "时长达标") == "warn"


def test_duration_within_tolerance_passes():
    result = qc_check(_brief(duration_sec=30, tolerance="+2s"), _assets(2), _timeline(2, 31.5))
    assert _status(result, "时长达标") == "pass"


def test_empty_brief_passes_vacuously():
    result = qc_check({}, [], {"clips": [], "total_duration": 0.0})
    assert result.passed
    assert _status(result, "片段完整性") == "pass"
    assert all(c.item != "时长达标" for c in result.check_results)


def test_all_clips_failed_fails():
    result = qc_check(_brief(), _assets(3), _timeline(0, 0.0))
    assert not result.passed
    assert _status(result, "片段完整性") == "fail"


def test_local_video_ratio_mismatch_warns(monkeypatch):
    monkeypatch.setattr("pixelflow.qc.check._probe_video", lambda path: {"width": 1920, "height": 1080, "duration": 30.0})
    monkeypatch.setattr("pixelflow.qc.check._has_black_frames", lambda path: False)
    monkeypatch.setattr("pixelflow.qc.check._has_freeze_frames", lambda path: False)

    result = qc_check({**_brief(), "ratio": "9:16", "size": "1080x1920"}, _assets(1), _timeline(1), "/tmp/out.mp4")

    assert result.passed
    assert _status(result, "手机端画幅适配") == "warn"


def test_local_video_freeze_fails(monkeypatch):
    monkeypatch.setattr("pixelflow.qc.check._probe_video", lambda path: {"width": 1080, "height": 1920, "duration": 30.0})
    monkeypatch.setattr("pixelflow.qc.check._has_black_frames", lambda path: False)
    monkeypatch.setattr("pixelflow.qc.check._has_freeze_frames", lambda path: True)

    result = qc_check({**_brief(), "ratio": "9:16", "size": "1080x1920"}, _assets(1), _timeline(1), "/tmp/out.mp4")

    assert not result.passed
    assert _status(result, "卡顿/冻结检测") == "fail"
