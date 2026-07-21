from pathlib import Path

import pytest

from pixelflow.generate.seedance_prompt import build_seedance_shot_prompt, load_seedance_guidance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT_ROOT / "skills" / "public" / "borgrise-creative-assistant-v2" / "skills" / "seedance-prompt"


def test_vendored_seedance_skill_targets_the_whole_model_family():
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "name: seedance-prompt" in skill_text
    assert "Seedance 系列" in skill_text
    assert "任意" in skill_text or "所有" in skill_text
    assert "Seedance 2.0" not in skill_text
    assert "仅适用于 Seedance 2.0" not in skill_text
    assert "Seedance 2.0 核心能力" not in skill_text
    assert "### 平台参数" not in skill_text
    assert "2K" not in skill_text
    assert "分辨率480p-720p" not in skill_text
    assert "4-15" in skill_text
    assert "秒级时间码" in skill_text
    assert "@asset_id" in skill_text
    assert "最多 9 张" in skill_text
    assert "content-app 实时配置" in skill_text
    assert "一个或多个中文段落" in skill_text
    assert "每个段落必须以" in skill_text
    assert "段落数量" in skill_text
    assert (SKILL_DIR / "THIRD_PARTY_NOTICE.md").exists()


def test_load_seedance_guidance_extracts_runtime_family_rules():
    guidance = load_seedance_guidance()

    for marker in [
        "适用范围与模型边界",
        "PixelFlow 分镜执行合同",
        "参考素材与一致性",
        "声音、对白与字幕",
        "镜头语言与真实感",
        "质量检查",
    ]:
        assert marker in guidance
    assert len(guidance) < (SKILL_DIR / "SKILL.md").stat().st_size


@pytest.mark.parametrize("video_model", ["seedance-1.5-pro", "seedance-2.0-mini"])
def test_build_seedance_shot_prompt_contains_current_model_and_final_contract(video_model: str):
    prompt = build_seedance_shot_prompt(
        scene_index=2,
        start_second=10,
        end_second=20,
        plan_markdown="## 创作目标\n展示通勤背包防泼水能力。",
        storyline="雨天通勤者从容进入办公室",
        narration="雨再大，也不怕重要文件被淋湿。",
        visual_style="电影写实，冷暖光对比",
        available_asset_ids=["character-commuter", "scene-office", "prop-backpack"],
        video_ratio="9:16",
        video_model=video_model,
    )

    assert f"当前视频模型：{video_model}" in prompt
    assert "Seedance 系列 Skill 规则" in prompt
    assert "10-20秒" in prompt
    assert "展示通勤背包防泼水能力" in prompt
    assert "@character-commuter" in prompt
    assert "@scene-office" in prompt
    assert "@prop-backpack" in prompt
    assert "最多 9" in prompt
    assert "只允许使用上述 @asset_id" in prompt
    assert "不要使用未声明素材" in prompt
    assert "镜头描述由一个或多个中文段落组成" in prompt
    assert "每个段落必须以当前分镜内部的整数秒范围开头" in prompt
    assert "段落数量由内容变化决定" in prompt


@pytest.mark.parametrize(
    ("start_second", "end_second"),
    [
        (10.5, 15.5),
        (10, 14.5),
    ],
)
def test_build_seedance_shot_prompt_rejects_non_integer_second_ranges(
    start_second: float,
    end_second: float,
):
    with pytest.raises(ValueError):
        build_seedance_shot_prompt(
            scene_index=1,
            start_second=start_second,
            end_second=end_second,
            plan_markdown="## 创作目标\n展示产品卖点。",
            storyline="产品在真实场景中完成演示",
            narration="清晰展示核心能力。",
            visual_style="电影写实",
            available_asset_ids=["prop-product"],
            video_ratio="9:16",
            video_model="seedance-1.5-pro",
            include_guidance=False,
            include_plan=False,
        )

