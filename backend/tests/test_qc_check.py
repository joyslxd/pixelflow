"""Tests for the QC phase verdict (pure logic)."""

from __future__ import annotations

from pixelflow.qc import qc_check


def _brief(n_shots, duration_sec=30, tolerance="+2s"):
    return {
        "shots": [{"shot_id": f"shot_{i:03d}"} for i in range(n_shots)],
        "duration_sec": duration_sec,
        "hard_constraints": {"total_duration_tolerance": tolerance},
    }


def _timeline(n_clips, total_duration=30.0):
    return {"clips": [{"shot_index": i} for i in range(n_clips)], "total_duration": total_duration}


def _status(result, item):
    return next(c.status for c in result.check_results if c.item == item)


def test_full_coverage_on_target_passes():
    result = qc_check(_brief(3), [], _timeline(3, 30.0))
    assert result.passed
    assert result.score == 1.0
    assert _status(result, "片段完整性") == "pass"
    assert _status(result, "时长达标") == "pass"


def test_missing_clip_fails_and_scores_partial():
    result = qc_check(_brief(3), [], _timeline(2, 20.0))
    assert not result.passed  # blocking -> routes back to GENERATE
    assert _status(result, "片段完整性") == "fail"
    assert result.score == round(2 / 3, 2)


def test_duration_drift_warns_not_fails():
    # full coverage but assembled duration far from target
    result = qc_check(_brief(3, duration_sec=30, tolerance="+2s"), [], _timeline(3, 40.0))
    assert result.passed  # warn must not flip passed
    assert _status(result, "时长达标") == "warn"


def test_duration_within_tolerance_passes():
    result = qc_check(_brief(3, duration_sec=30, tolerance="+2s"), [], _timeline(3, 31.5))
    assert _status(result, "时长达标") == "pass"


def test_empty_brief_passes_vacuously():
    result = qc_check({"shots": []}, [], {"clips": [], "total_duration": 0.0})
    assert result.passed
    assert result.score == 1.0
    # no duration_sec target -> no duration check emitted
    assert all(c.item != "时长达标" for c in result.check_results)


def test_all_clips_failed_fails():
    result = qc_check(_brief(3), [], _timeline(0, 0.0))
    assert not result.passed
    assert result.score == 0.0
