"""Tests for the GENERATE-phase PromptEngine (pure logic)."""

from __future__ import annotations

from pixelflow.generate import build_seedance_prompt

_GV = {
    "subject_type": "年轻女性",
    "character_style": "休闲简约",
    "environment": "现代厨房",
    "lighting": "自然柔光",
    "overall_style": "清新生活方式",
    "forbidden_elements": "夸张特效",
}
_SHOT = {
    "generation_prompt": "女性手持保温杯倒水",
    "visual_description": "特写展示杯子",
    "camera_movement": "推",
    "shot_type": "特写",
}


def test_includes_all_sections():
    out = build_seedance_prompt(_SHOT, _GV, 5.0)
    assert "女性手持保温杯倒水" in out  # core
    assert "0-5s：推，特写。" in out  # time-coded camera
    assert "风格：清新生活方式，自然柔光，现代厨房。" in out
    assert "一致性：保持年轻女性、休闲简约与光线统一。" in out
    assert "负向：夸张特效；无字幕、无水印、无画面生成文字。" in out


def test_duration_formatted_without_trailing_zero():
    out = build_seedance_prompt(_SHOT, _GV, 3.0)
    assert "0-3s：" in out
    assert "0-3.0s" not in out


def test_negative_line_always_present_minimum():
    out = build_seedance_prompt({"generation_prompt": "简单镜头"}, {}, 4.0)
    assert "负向：无字幕、无水印、无画面生成文字。" in out
    # no global_visual -> no style/continuity lines
    assert "风格：" not in out
    assert "一致性：" not in out


def test_falls_back_to_visual_description():
    out = build_seedance_prompt({"visual_description": "桌上的杯子", "camera_movement": "平移"}, {}, 4.0)
    assert "桌上的杯子" in out


def test_core_punctuation_not_doubled():
    out = build_seedance_prompt({"generation_prompt": "已带句号。"}, {}, 4.0)
    assert "已带句号。" in out
    assert "已带句号。。" not in out


def test_char_cap_enforced():
    out = build_seedance_prompt({"generation_prompt": "长" * 5000}, {}, 4.0, max_chars=100)
    assert len(out) == 100
