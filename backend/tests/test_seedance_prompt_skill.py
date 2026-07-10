from pathlib import Path

from pixelflow.generate.seedance_prompt import build_seedance_shot_prompt, load_seedance_guidance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT_ROOT / "skills" / "public" / "borgrise-creative-assistant-v2" / "skills" / "seedance-prompt"


def test_vendored_seedance_skill_keeps_timestamps_and_reference_rules():
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert "Seedance 2.0" in skill_text
    assert "4-15秒" in skill_text
    assert "时间戳" in skill_text
    assert "@引用系统" in skill_text
    assert "≤9张" in skill_text
    assert (SKILL_DIR / "THIRD_PARTY_NOTICE.md").exists()


def test_load_seedance_guidance_extracts_only_runtime_sections():
    guidance = load_seedance_guidance()

    assert "平台参数" in guidance
    assert "@引用系统" in guidance
    assert "时长策略" in guidance
    assert "声音控制" in guidance
    assert "镜头" in guidance
    assert len(guidance) < (SKILL_DIR / "SKILL.md").stat().st_size


def test_build_seedance_shot_prompt_contains_final_contract_and_plan_context():
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
    )

    assert "分镜 2" in prompt
    assert "10-20秒" in prompt
    assert "展示通勤背包防泼水能力" in prompt
    assert "电影写实，冷暖光对比" in prompt
    assert "9:16" in prompt
    assert "@character-commuter" in prompt
    assert "@scene-office" in prompt
    assert "@prop-backpack" in prompt
    assert "最多 9" in prompt
    assert "不要使用未声明" in prompt

