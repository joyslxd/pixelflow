"""Tests for the pure-logic intake skills (PRD §8.4 + §8.7)."""

from __future__ import annotations

from pixelflow.intake import demand_integrity_check, normalize_video_params

# A demand that passes every blocking check.
_COMPLETE_PI = {"product_name": "保温杯", "main_image_url": "https://x/img.jpg", "price": 99.0}
_COMPLETE_VP = {"platform": "douyin", "video_duration_sec": 30}
_COMPLETE_CD = {"creative_style": "tutorial", "core_message": "保温12小时"}


def _statuses(result, item):
    return [c.status for c in result.check_results if c.item == item]


# -- demand_integrity_check (§8.7) --


def test_complete_demand_is_complete():
    result = demand_integrity_check(_COMPLETE_PI, _COMPLETE_VP, _COMPLETE_CD)
    assert result.is_complete
    assert all(c.status != "fail" for c in result.check_results)


def test_missing_required_fields_block_and_produce_questions():
    result = demand_integrity_check({}, {}, {})
    assert not result.is_complete
    assert "fail" in _statuses(result, "商品名称")
    assert "fail" in _statuses(result, "商品图片")
    assert "fail" in _statuses(result, "平台")
    # questions() caps at 3 follow-ups and only surfaces blocking actions.
    qs = result.questions()
    assert len(qs) == 3
    assert all(q for q in qs)


def test_core_message_satisfied_by_business_goal():
    vp = {**_COMPLETE_VP, "business_goal": "促进下单"}
    result = demand_integrity_check(_COMPLETE_PI, vp, {"creative_style": "story"})
    assert "pass" in _statuses(result, "核心诉求")


def test_missing_price_warns_not_blocks():
    pi = {"product_name": "杯子", "main_image_url": "https://x/i.jpg"}  # no price
    result = demand_integrity_check(pi, _COMPLETE_VP, _COMPLETE_CD)
    assert "warn" in _statuses(result, "价格缺失")
    # warn must not flip is_complete by itself
    assert result.is_complete


def test_uncleaned_image_warns():
    result = demand_integrity_check(_COMPLETE_PI, _COMPLETE_VP, _COMPLETE_CD)
    assert "warn" in _statuses(result, "图片清洗")


def test_pending_reference_video_warns():
    refs = [{"asset_id": "a1", "status": "downloading"}]
    result = demand_integrity_check(_COMPLETE_PI, _COMPLETE_VP, _COMPLETE_CD, reference_videos=refs)
    assert "warn" in _statuses(result, "参考视频")


# -- normalize_video_params (§8.4) --


def test_duration_snapped_to_nearest_bucket():
    out, notes = normalize_video_params({"video_duration_sec": 20})
    assert out["video_duration_sec"] == 15
    assert any("归一" in n for n in notes)


def test_duration_bucket_kept_when_valid():
    out, notes = normalize_video_params({"video_duration_sec": 60})
    assert out["video_duration_sec"] == 60
    assert notes == []


def test_resolution_forced_to_1080p():
    out, notes = normalize_video_params({"video_resolution": "4k"})
    assert out["video_resolution"] == "1080p"
    assert any("1080p" in n for n in notes)


def test_unsupported_platform_flagged():
    _, notes = normalize_video_params({"platform": "myspace"})
    assert any("不支持平台" in n for n in notes)


def test_defaults_filled():
    out, _ = normalize_video_params(None)
    assert out["video_resolution"] == "1080p"
    assert out["segment_strategy"] == "auto"
