from __future__ import annotations

import pytest

from pixelflow.creative.scene_blueprint import (
    fallback_scene_blueprints,
    normalize_scene_blueprints,
    repair_scene_blueprints_schedule,
    scene_blueprint_durations,
)


def _blueprints() -> list[dict[str, object]]:
    return [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "title": "问题钩子",
            "structure_role": "opening",
            "start_sec": 0,
            "end_sec": 6,
            "duration_sec": 6,
            "storyline": "雨水突然落下，背包面临进水风险。",
            "shot_description": "0-6秒: 特写雨滴落在背包表面，镜头快速推近材质纹理，结尾停在水珠滑落的瞬间。",
            "narration": "突如其来的雨，最怕包里一起遭殃。",
            "transition": "顺着滑落水珠切到拉链位置。",
            "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]},
        },
        {
            "scene_id": "scene-2",
            "scene_index": 2,
            "title": "能力证明",
            "structure_role": "climax",
            "start_sec": 6,
            "end_sec": 18,
            "duration_sec": 12,
            "storyline": "通过泼水和开包检查证明防水能力。",
            "shot_description": "0-12秒: 中景展示连续泼水，镜头环绕背包后切入拉链特写，打开背包展示内部保持干燥。",
            "narration": "高密防泼水面料，把雨留在外面。",
            "transition": "由干燥内衬匹配剪辑到通勤收纳。",
            "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包", "水杯"]},
        },
        {
            "scene_id": "scene-3",
            "scene_index": 3,
            "title": "结果收束",
            "structure_role": "conclusion",
            "start_sec": 18,
            "end_sec": 26,
            "duration_sec": 8,
            "storyline": "背包在通勤终点保持整洁，完成购买引导。",
            "shot_description": "0-8秒: 全景跟随背包进入明亮办公区，镜头停在完整产品外观和干燥内胆，最后定格购买引导。",
            "narration": "全天候通勤，现在就选它。",
            "transition": "产品定格结束。",
            "asset_requirements": {"characters": [], "scenes": ["办公区"], "props": ["防水背包"]},
        },
    ]


def test_normalize_scene_blueprints_preserves_llm_duration_schedule() -> None:
    result = normalize_scene_blueprints(_blueprints(), total_duration_sec=26)

    assert scene_blueprint_durations(result) == [6, 12, 8]
    assert result[0]["structure_role"] == "opening"
    assert result[-1]["structure_role"] == "conclusion"
    assert result[1]["start_sec"] == 6
    assert result[1]["end_sec"] == 18


def test_normalize_scene_blueprints_accepts_seedance_story_role_aliases() -> None:
    blueprints = _blueprints()
    blueprints[0]["structure_role"] = "hook"
    blueprints[1]["structure_role"] = "proof"
    blueprints[2]["structure_role"] = "cta"

    result = normalize_scene_blueprints(blueprints, total_duration_sec=26)

    assert [item["structure_role"] for item in result] == ["opening", "climax", "conclusion"]


def test_normalize_scene_blueprints_accepts_llm_chinese_composite_story_roles() -> None:
    blueprints = _blueprints()
    blueprints[0]["structure_role"] = "开场钩子"
    blueprints[1]["structure_role"] = "展开证明"
    blueprints[2]["structure_role"] = "结尾CTA"

    result = normalize_scene_blueprints(blueprints, total_duration_sec=26)

    assert [item["structure_role"] for item in result] == ["opening", "development", "conclusion"]


def test_normalize_scene_blueprints_canonicalizes_duplicate_llm_scene_ids() -> None:
    blueprints = _blueprints()
    blueprints[0]["scene_id"] = "duplicated"
    blueprints[1]["scene_id"] = "duplicated"

    result = normalize_scene_blueprints(blueprints, total_duration_sec=26)

    assert [item["scene_id"] for item in result] == ["scene-1", "scene-2", "scene-3"]


def test_normalize_scene_blueprints_fills_missing_transition_without_discarding_llm_plan() -> None:
    blueprints = _blueprints()
    blueprints[0]["transition"] = ""
    blueprints[-1]["transition"] = ""

    result = normalize_scene_blueprints(blueprints, total_duration_sec=26)

    assert result[0]["title"] == "问题钩子"
    assert result[0]["transition"] == "沿当前动作或视线自然切入下一分镜。"
    assert result[-1]["transition"] == "产品定格结束。"


def test_repair_scene_blueprints_schedule_preserves_llm_content_and_exact_total() -> None:
    raw = []
    cursor = 0
    roles = ["hook", "proof", "proof", "proof", "cta"]
    durations = [6, 6, 6, 6, 2]
    for index, (role, duration) in enumerate(zip(roles, durations, strict=True), start=1):
        raw.append(
            {
                "scene_id": f"llm-scene-{index}",
                "scene_index": index,
                "title": f"LLM详细分镜{index}",
                "structure_role": role,
                "start_sec": cursor,
                "end_sec": cursor + duration,
                "duration_sec": duration,
                "storyline": f"保留第{index}个分镜的具体故事线。",
                "shot_description": f"0-{duration}秒: 保留第{index}个分镜的具体镜头描述。",
                "narration": "本分镜无旁白",
                "transition": "" if index == len(durations) else "动作匹配剪辑。",
                "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]},
            }
        )
        cursor += duration

    result = repair_scene_blueprints_schedule(raw, total_duration_sec=26)

    assert [item["title"] for item in result] == [f"LLM详细分镜{index}" for index in range(1, 6)]
    assert sum(scene_blueprint_durations(result)) == 26
    assert all(4 <= item["duration_sec"] <= 15 for item in result)
    assert result[-1]["end_sec"] == 26
    assert f"0-{result[-1]['duration_sec']}秒" in result[-1]["shot_description"]


def test_normalize_scene_blueprints_rejects_non_second_seedance_description() -> None:
    blueprints = _blueprints()
    blueprints[0]["shot_description"] = "0-6000ms: 推近背包"

    with pytest.raises(ValueError, match="毫秒"):
        normalize_scene_blueprints(blueprints, total_duration_sec=26)


def test_fallback_scene_blueprints_allocate_by_story_role_instead_of_ten_second_chunks() -> None:
    result = fallback_scene_blueprints(
        total_duration_sec=26,
        product_name="防水通勤背包",
        direction_description="雨天通勤实测",
        visual_style="电影写实",
        conversion_goal="直接购买",
    )

    durations = scene_blueprint_durations(result)
    assert sum(durations) == 26
    assert all(4 <= duration <= 15 for duration in durations)
    assert len(set(durations)) > 1
    assert result[0]["structure_role"] == "opening"
    assert result[-1]["structure_role"] == "conclusion"
    assert all("秒" in str(item["shot_description"]) for item in result)


@pytest.mark.parametrize("total_duration_sec", [4, 300])
def test_fallback_scene_blueprints_keep_every_scene_within_hard_duration_boundaries(
    total_duration_sec: int,
) -> None:
    result = fallback_scene_blueprints(
        total_duration_sec=total_duration_sec,
        product_name="防水通勤背包",
        direction_description="雨天通勤实测",
        visual_style="电影写实",
        conversion_goal="直接购买",
    )

    durations = scene_blueprint_durations(result)
    assert sum(durations) == total_duration_sec
    assert all(4 <= duration <= 15 for duration in durations)
    assert result[0]["start_sec"] == 0
    assert result[-1]["end_sec"] == total_duration_sec
