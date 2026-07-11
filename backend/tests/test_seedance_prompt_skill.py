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
    assert "仅适用于 Seedance 2.0" not in skill_text
    assert "4-15" in skill_text
    assert "秒级时间码" in skill_text
    assert "@asset_id" in skill_text
    assert "最多 9 张" in skill_text
    assert "content-app 实时配置" in skill_text
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

