"""Tests for segment planning + fused prompt (pure logic, offline)."""

from __future__ import annotations

from pixelflow.generate import build_segment_prompt, plan_segments


def _shot(dur, **kw):
    base = {"duration": dur}
    base.update(kw)
    return base


# -- plan_segments --


def test_short_video_is_single_segment():
    shots = [_shot(2.5), _shot(3), _shot(2)]
    segs = plan_segments(shots, 15)
    assert len(segs) == 1
    assert segs[0]["shot_indices"] == [0, 1, 2]
    assert segs[0]["duration"] == 7.5
    assert segs[0]["index"] == 0


def test_splits_when_exceeding_max():
    shots = [_shot(10), _shot(8), _shot(6)]
    segs = plan_segments(shots, 15)
    assert [s["shot_indices"] for s in segs] == [[0], [1, 2]]
    assert [s["duration"] for s in segs] == [10, 14]
    assert [s["index"] for s in segs] == [0, 1]


def test_single_overlong_shot_gets_own_segment():
    # a shot longer than max still forms its own segment (caller clamps duration)
    segs = plan_segments([_shot(20), _shot(3)], 15)
    assert [s["shot_indices"] for s in segs] == [[0], [1]]


def test_exact_fit_stays_in_one_segment():
    segs = plan_segments([_shot(7), _shot(8)], 15)  # 7+8=15, not > 15
    assert len(segs) == 1
    assert segs[0]["duration"] == 15


def test_empty_shots():
    assert plan_segments([], 15) == []


# -- build_segment_prompt --


def test_prompt_fuses_all_shots_with_cumulative_timecodes():
    shots = [
        _shot(2.5, generation_prompt="开场特写", camera_movement="推", shot_type="特写"),
        _shot(3, generation_prompt="产品展示", camera_movement="平移"),
    ]
    gv = {"overall_style": "情绪种草", "forbidden_elements": "无竞品"}
    prompt = build_segment_prompt(shots, gv)

    assert "开场特写" in prompt
    assert "产品展示" in prompt
    assert "0-2.5s" in prompt
    assert "2.5-5.5s" in prompt  # cumulative
    assert "整体风格：情绪种草" in prompt
    assert "无竞品" in prompt
    assert "无画面生成文字" in prompt  # negative line always present


def test_prompt_falls_back_to_visual_description():
    prompt = build_segment_prompt([_shot(3, visual_description="后备描述")], {})
    assert "后备描述" in prompt


def test_prompt_capped_at_max_chars():
    long_shot = _shot(3, generation_prompt="字" * 5000)
    prompt = build_segment_prompt([long_shot], {}, max_chars=200)
    assert len(prompt) == 200
