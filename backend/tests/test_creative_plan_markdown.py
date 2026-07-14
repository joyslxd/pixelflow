from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from pixelflow.creative.plan_markdown import (
    PlanMarkdownResult,
    build_plan_markdown,
    build_plan_markdown_with_llm,
    restore_plan_version,
)

VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 180,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


def test_build_video_plan_markdown_fills_uploaded_template_sections():
    result = build_plan_markdown(
        "video",
        VIDEO_FORM,
        {
            "direction_id": "direction_1",
            "title": "痛点开场 + 产品解决",
            "description": "通勤路上先抛出续航痛点，再用产品能力完成解决。",
            "data": {"visual_anchor": "通勤、质感"},
        },
    )

    assert result.template_path.as_posix().endswith("backend/skills/public/borgrise-creative-assistant-v2/templates/plan_video.md")
    assert "## 一、选题方向" in result.plan_markdown
    assert "## 制作执行合同" in result.plan_markdown
    assert "AuroraFit 智能健康戒指" in result.plan_markdown
    assert "痛点开场 + 产品解决" in result.plan_markdown
    assert "引流直播间" in result.plan_markdown
    assert result.creation_contract["video_duration_sec"] == 180
    assert result.creation_contract["video_model"] == "seedance-2.0"
    assert result.creation_contract["image_model"] == "gpt-image-2"
    assert sum(result.scene_durations_sec) == 180
    assert all(4 <= duration <= 15 for duration in result.scene_durations_sec)
    assert result.plan_version == 1
    assert result.consistency_issues == []


def test_build_image_plan_markdown_marks_video_only_sections_not_applicable():
    result = build_plan_markdown(
        "image",
        {
            "image_goal": "科技感耳机海报",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "科技感",
            "image_size": "9:16 竖版海报",
        },
        {
            "direction_id": "direction_2",
            "title": "真实场景氛围图",
            "description": "在真实办公室场景里突出耳机的科技质感。",
            "data": {"visual_anchor": "办公室、蓝牙、金属质感"},
        },
    )

    assert "科技感耳机海报" in result.plan_markdown
    assert "真实场景氛围图" in result.plan_markdown
    assert "## 三、图片规格" in result.plan_markdown
    assert "## 制作执行合同" in result.plan_markdown
    assert result.output_type == "image"
    assert result.template_path.name == "plan_image.md"
    assert result.review_timeout_sec is None


def test_build_image_plan_markdown_uses_intake_context_complete_goal():
    result = build_plan_markdown(
        "image",
        {
            "image_goal": "宣传图",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "自动适配",
        },
        {
            "direction_id": "direction_1",
            "title": "通学收纳主视觉",
            "description": "突出书包容量、护脊和耐磨卖点。",
        },
        {
            "core_message": "儿童通学场景里的轻量护脊书包",
            "visual_anchor_keywords": ["通学路", "收纳分区", "护脊背负"],
        },
        intake_context={
            "source_prompt": "帮我生成书包的宣传图",
            "product_subject": "书包",
            "creation_goal": "书包宣传图",
            "industry_type": "服饰鞋包",
            "requested_output_count": 1,
        },
    )

    assert "# 书包宣传图｜通学收纳主视觉" in result.plan_markdown
    assert "原始需求：帮我生成书包的宣传图" in result.plan_markdown
    assert "产品主体：书包" in result.plan_markdown
    assert "行业类型：服饰鞋包" in result.plan_markdown
    assert "宣传图 = 面向" not in result.plan_markdown


def test_build_video_plan_markdown_infers_requested_duration_from_context():
    result = build_plan_markdown(
        "video",
        {
            "product_info": "智能洗地机 X9",
            "product_category": "家居日用",
            "target_audience": "30-45 岁养宠家庭",
            "conversion_goal": "直接购买",
            "video_duration_sec": 90,
        },
        {
            "direction_id": "direction_1",
            "title": "宠物家庭深度种草",
            "description": "用复杂场景证明清洁力",
        },
        {"core_message": "帮我生成90秒左右的视频，要完整讲清宠物毛发和湿垃圾清洁场景"},
    )

    assert "- 时长：90 秒" in result.plan_markdown
    assert "00:00-01:30" in result.plan_markdown
    assert sum(result.scene_durations_sec) == 90


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[object] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return FakeMessage(self.content)


def test_build_video_plan_with_llm_uses_uploaded_template_and_constrains_scene_image_specs() -> None:
    fake_model = FakeModel(
        """
        {
          "plan_markdown": "# AuroraFit 智能健康戒指新品宣传\\n\\n## 一、选题方向\\n晨跑到睡眠的健康陪伴。\\n\\n## 三、视频规格\\n- 时长：180 秒\\n- 画幅：9:16\\n\\n## 五、镜头列表\\n严格按照提供的精确镜头时间线执行。",
          "scene_image_ratio": "3:4",
          "scene_image_size": "8K"
        }
        """
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            VIDEO_FORM,
            {
                "direction_id": "direction_1",
                "title": "全天健康陪伴",
                "description": "从晨跑、办公到睡眠展示戒指价值。",
            },
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.llm_used is True
    assert result.model_name == "deepseek-v4-pro"
    assert result.template_path.name == "plan_video.md"
    assert result.creation_contract["scene_image_ratio"] == "9:16"
    assert result.creation_contract["scene_image_size"] == "4K"
    assert sum(result.scene_durations_sec) == 180
    assert "视频模型：seedance-2.0" in result.plan_markdown
    assert "图片模型：gpt-image-2" in result.plan_markdown
    assert "苹果PRO" not in result.plan_markdown
    assert fake_model.prompts


def test_build_video_plan_with_llm_uses_seedance_skill_and_llm_scene_schedule() -> None:
    fake_model = FakeModel(
        """
        {
          "plan_markdown": "# 防水通勤背包雨天实测\\n\\n## 一、选题方向\\n用雨天实测完成卖点证明。\\n\\n## 三、视频规格\\n- 时长：26 秒\\n- 画幅：9:16\\n\\n## 五、镜头列表\\n旧计划把每个镜头固定成10秒。\\n\\n## 背景音乐\\n雨声与节奏鼓点。",
          "scene_image_ratio": "9:16",
          "scene_image_size": "4K",
          "scene_blueprints": [
            {
              "scene_id": "scene-1", "scene_index": 1, "title": "雨水钩子", "structure_role": "opening",
              "start_sec": 0, "end_sec": 6, "duration_sec": 6,
              "storyline": "雨水突袭形成冲突。", "shot_description": "0-6秒: 特写雨滴砸向背包，镜头快速推近材质。",
              "narration": "下雨最怕包里一起遭殃。", "transition": "顺着水滴切到拉链。",
              "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]}
            },
            {
              "scene_id": "scene-2", "scene_index": 2, "title": "防水证明", "structure_role": "climax",
              "start_sec": 6, "end_sec": 18, "duration_sec": 12,
              "storyline": "泼水和开包检查证明防水。", "shot_description": "0-12秒: 中景连续泼水后切入拉链特写，打开背包展示干燥内胆。",
              "narration": "高密防泼水面料，把雨留在外面。", "transition": "由内胆匹配剪辑到办公区。",
              "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包", "水杯"]}
            },
            {
              "scene_id": "scene-3", "scene_index": 3, "title": "通勤收束", "structure_role": "conclusion",
              "start_sec": 18, "end_sec": 26, "duration_sec": 8,
              "storyline": "抵达办公区并完成购买引导。", "shot_description": "0-8秒: 跟拍背包进入办公区，定格完整外观和干燥内胆。",
              "narration": "全天候通勤，现在就选它。", "transition": "产品定格结束。",
              "asset_requirements": {"characters": [], "scenes": ["办公区"], "props": ["防水背包"]}
            }
          ]
        }
        """
    )
    form = {
        **VIDEO_FORM,
        "product_info": "防水通勤背包",
        "video_duration_sec": 26,
    }

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            form,
            {
                "direction_id": "direction_1",
                "title": "雨天防水实测",
                "description": "用雨水冲突、能力证明和通勤结果完成总分总叙事。",
            },
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    prompt = str(fake_model.prompts[0])
    assert "一个短分镜只安排一个主要叙事目标" in prompt
    assert "自主决定分镜数量和每个分镜时长" in prompt
    assert "总分总" in prompt
    assert "精确镜头时间线" not in prompt
    assert result.scene_durations_sec == [6, 12, 8]
    assert [item["title"] for item in result.scene_blueprints] == ["雨水钩子", "防水证明", "通勤收束"]
    assert "### 权威分镜创作蓝图" in result.plan_markdown
    assert "0-12秒: 中景连续泼水" in result.plan_markdown
    assert "旧计划把每个镜头固定成10秒" not in result.plan_markdown
    assert result.plan_markdown.count("### 权威分镜创作蓝图") == 1
    assert "## 背景音乐\n雨声与节奏鼓点" in result.plan_markdown


def test_build_video_plan_repairs_invalid_llm_duration_without_replacing_story_content() -> None:
    raw_blueprints = []
    cursor = 0
    for index, (role, duration) in enumerate(
        zip(["hook", "proof", "proof", "proof", "cta"], [6, 6, 6, 6, 2], strict=True),
        start=1,
    ):
        raw_blueprints.append(
            {
                "scene_id": f"scene-{index}",
                "scene_index": index,
                "title": f"暴雨实测分镜{index}",
                "structure_role": role,
                "start_sec": cursor,
                "end_sec": cursor + duration,
                "duration_sec": duration,
                "storyline": f"第{index}段具体产品证明。",
                "shot_description": f"0-{duration}秒: 展示第{index}段具体产品动作。",
                "narration": "本分镜无旁白",
                "transition": "动作匹配剪辑。" if index < 5 else "",
                "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]},
            }
        )
        cursor += duration
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": "# 防水背包宣传片\n\n## 一、选题方向\n暴雨实测。\n\n## 三、视频规格\n26秒。\n\n## 五、镜头列表\n由蓝图生成。",
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": raw_blueprints,
            },
            ensure_ascii=False,
        )
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            {**VIDEO_FORM, "product_info": "防水通勤背包", "video_duration_sec": 26},
            {"direction_id": "direction_1", "title": "暴雨实测", "description": "用五段实测完成证明。"},
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert result.llm_used is True
    assert [item["title"] for item in result.scene_blueprints] == [f"暴雨实测分镜{index}" for index in range(1, 6)]
    assert sum(result.scene_durations_sec) == 26
    assert all(4 <= duration <= 15 for duration in result.scene_durations_sec)
    assert "需求冲突钩子" not in result.plan_markdown


def test_plan_memory_is_internal_context_and_never_rendered_to_user() -> None:
    memory_text = "用户偏好：电影写实风，不展示价格。"
    fallback = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 26},
        {
            "direction_id": "direction_1",
            "title": "雨天通勤实测",
            "description": "用真实雨天证明产品能力。",
        },
        intake_context={
            "semantic_memory": {
                "enabled": True,
                "items": [{"content": memory_text, "metadata": {"category": "preference"}}],
            }
        },
    )

    assert "长期记忆约束" not in fallback.plan_markdown
    assert memory_text not in fallback.plan_markdown

    leaked_plan = (
        "# 防水背包宣传片\n\n## 一、选题方向\n雨天通勤实测。\n\n## 二、选题优势\n"
        f"- **长期记忆约束**：{memory_text}\n"
        "  stage=prepare_scene_packages; message=scenes=3 assets=4; ok=True\n"
        "  用户创作上下文：采集 Agent 完成意图识别；Skill 经验仅供内部决策。\n"
        "- 产品证明清晰。\n\n## 三、视频规格\n- 时长：26 秒\n\n## 五、镜头列表\n按蓝图执行。"
    )
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": leaked_plan,
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
            },
            ensure_ascii=False,
        )
    )
    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            {**VIDEO_FORM, "video_duration_sec": 26},
            {
                "direction_id": "direction_1",
                "title": "雨天通勤实测",
                "description": "用真实雨天证明产品能力。",
            },
            intake_context={
                "semantic_memory": {
                    "enabled": True,
                    "items": [{"content": memory_text, "metadata": {"category": "preference"}}],
                }
            },
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    prompt = str(fake_model.prompts[0])
    assert memory_text in prompt
    assert "长期记忆约束" not in result.plan_markdown
    assert memory_text not in result.plan_markdown
    assert "stage=prepare_scene_packages" not in result.plan_markdown
    assert "用户创作上下文" not in result.plan_markdown
    assert "Skill 经验" not in result.plan_markdown
    assert "- 产品证明清晰。" in result.plan_markdown


def test_plan_blueprint_internal_memory_markers_never_reach_user_or_scene_contract() -> None:
    blueprints = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "title": "雨水钩子",
            "structure_role": "opening",
            "start_sec": 0,
            "end_sec": 6,
            "duration_sec": 6,
            "storyline": "长期记忆约束：用户偏好电影写实风。",
            "shot_description": "0-6秒: 特写雨滴砸向背包。",
            "narration": "下雨最怕包里一起遭殃。",
            "transition": "顺着水滴切到拉链。",
            "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]},
        },
        {
            "scene_id": "scene-2",
            "scene_index": 2,
            "title": "防水证明",
            "structure_role": "climax",
            "start_sec": 6,
            "end_sec": 18,
            "duration_sec": 12,
            "storyline": "泼水和开包检查证明防水。",
            "shot_description": "0-12秒: 中景连续泼水后展示干燥内胆。",
            "narration": "PowerMem 记忆：不要展示价格。",
            "transition": "由内胆匹配剪辑到办公区。",
            "asset_requirements": {"characters": [], "scenes": ["雨中街道"], "props": ["防水背包"]},
        },
        {
            "scene_id": "scene-3",
            "scene_index": 3,
            "title": "通勤收束",
            "structure_role": "conclusion",
            "start_sec": 18,
            "end_sec": 26,
            "duration_sec": 8,
            "storyline": "抵达办公区并完成购买引导。",
            "shot_description": "0-8秒: 跟拍背包进入办公区。",
            "narration": "全天候通勤，现在就选它。",
            "transition": "产品定格结束。",
            "asset_requirements": {"characters": [], "scenes": ["办公区"], "props": ["防水背包"]},
        },
    ]
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": "# 防水背包宣传片\n\n## 一、选题方向\n雨天实测。\n\n## 三、视频规格\n26秒。\n\n## 五、镜头列表\n按蓝图执行。",
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": blueprints,
            },
            ensure_ascii=False,
        )
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            {**VIDEO_FORM, "product_info": "防水通勤背包", "video_duration_sec": 26},
            {"direction_id": "direction_1", "title": "雨天实测", "description": "证明防水能力。"},
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    serialized_blueprints = json.dumps(result.scene_blueprints, ensure_ascii=False)
    assert "长期记忆约束" not in result.plan_markdown
    assert "PowerMem" not in result.plan_markdown
    assert "长期记忆约束" not in serialized_blueprints
    assert "PowerMem" not in serialized_blueprints


def test_restore_plan_version_activates_history_without_appending():
    history = [
        {
            "version": 1,
            "plan_markdown": "# plan.md v1",
            "creation_contract": {"video_model": "seedance-1.5-pro", "video_duration_sec": 20},
            "scene_durations_sec": [10, 10],
        },
        {
            "version": 2,
            "plan_markdown": "# plan.md v2",
            "creation_contract": {"video_model": "seedance-2.0", "video_duration_sec": 20},
            "scene_durations_sec": [5, 15],
        },
    ]
    original_history = copy.deepcopy(history)

    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=history,
        restore_version=1,
        creation_contract=history[1]["creation_contract"],
        scene_durations_sec=history[1]["scene_durations_sec"],
    )

    assert result.plan_version == 1
    assert result.plan_markdown == "# plan.md v1"
    assert result.plan_history == original_history
    assert [item["version"] for item in result.plan_history] == [1, 2]
    assert history == original_history
    assert result.restored_from_version == 1
    assert result.creation_contract == history[0]["creation_contract"]
    assert result.scene_durations_sec == [10, 10]


def test_restore_legacy_history_keeps_current_authoritative_contract():
    current_contract = {"video_model": "seedance-2.0", "video_duration_sec": 20}

    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[
            {"version": 1, "plan_markdown": "# plan.md v1"},
            {"version": 2, "plan_markdown": "# plan.md v2"},
        ],
        restore_version=1,
        creation_contract=current_contract,
        scene_durations_sec=[10, 10],
    )

    assert result.plan_version == 1
    assert result.creation_contract == current_contract
    assert result.scene_durations_sec == [10, 10]


def test_restore_legacy_history_rejects_invalid_request_blueprint_fallback() -> None:
    current_contract = {"video_model": "seedance-2.0", "video_duration_sec": 30}

    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[
            {
                "version": 1,
                "plan_markdown": "# plan.md v1",
                "creation_contract": current_contract,
                "scene_durations_sec": [10, 10, 10],
            },
            {"version": 2, "plan_markdown": "# plan.md v2"},
        ],
        restore_version=1,
        creation_contract=current_contract,
        scene_durations_sec=[10, 10, 10],
        scene_blueprints=[
            {
                "scene_id": "scene-1",
                "scene_index": 1,
                "duration_sec": 999,
            }
        ],
    )

    assert result.scene_blueprints == []
    assert result.scene_durations_sec == [10, 10, 10]


def test_restore_legacy_history_rejects_invalid_request_duration_fallback() -> None:
    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[{"version": 1, "plan_markdown": "# plan.md v1"}],
        restore_version=1,
        creation_contract={"video_model": "seedance-2.0", "video_duration_sec": 30},
        scene_durations_sec=[3, 27],
    )

    assert result.scene_durations_sec == []


def test_restore_plan_sanitizes_internal_memory_text_from_historical_markdown() -> None:
    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[
            {
                "version": 1,
                "plan_markdown": "# plan.md v1\n\n## 长期记忆约束\nPowerMem 记忆：内部偏好原文。\n\n## 三、视频规格\n20秒。",
                "creation_contract": {"video_model": "seedance-2.0", "video_duration_sec": 20},
                "scene_durations_sec": [10, 10],
            }
        ],
        restore_version=1,
        creation_contract={"video_model": "seedance-2.0", "video_duration_sec": 20},
        scene_durations_sec=[10, 10],
    )

    assert "长期记忆约束" not in result.plan_markdown
    assert "PowerMem" not in result.plan_markdown
    assert "内部偏好原文" not in result.plan_markdown


def test_restore_image_plan_preserves_explicit_empty_snapshots():
    current_contract = {"intent": "image", "image_model_capabilities": {"sizes": ["2K"]}}

    result = restore_plan_version(
        intent="image",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[
            {
                "version": 1,
                "plan_markdown": "# plan.md v1",
                "creation_contract": {},
                "scene_durations_sec": [],
            },
            {
                "version": 2,
                "plan_markdown": "# plan.md v2",
                "creation_contract": current_contract,
                "scene_durations_sec": [10],
            },
        ],
        restore_version=1,
        creation_contract=current_contract,
        scene_durations_sec=[10],
    )

    assert result.creation_contract == {}
    assert result.scene_durations_sec == []


@pytest.mark.parametrize(
    "malformed_durations",
    (
        [None, 10],
        [object(), 10],
        ["invalid", 10],
        [10.0, 10],
        [True, 10, 9],
        [3, 10, 7],
        [16, 4],
        [10, 9],
    ),
    ids=(
        "none",
        "object",
        "invalid-string",
        "float",
        "bool",
        "below-minimum",
        "above-maximum",
        "wrong-total",
    ),
)
def test_restore_malformed_scene_durations_falls_back_to_current_authoritative_value(
    malformed_durations: list[object],
):
    current_contract = {"video_model": "seedance-2.0", "video_duration_sec": 20}

    result = restore_plan_version(
        intent="video",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=[
            {
                "version": 1,
                "plan_markdown": "# plan.md v1",
                "creation_contract": {"video_model": "seedance-1.5-pro", "video_duration_sec": 20},
                "scene_durations_sec": malformed_durations,
            },
            {
                "version": 2,
                "plan_markdown": "# plan.md v2",
                "creation_contract": current_contract,
                "scene_durations_sec": [6, 14],
            },
        ],
        restore_version=1,
        creation_contract=current_contract,
        scene_durations_sec=[6, 14],
    )

    assert result.scene_durations_sec == [6, 14]


def test_initial_plan_history_snapshot_deep_copies_nested_contract():
    contract = {"image_model_capabilities": {"aspect_ratios": ["1:1"]}}
    result = PlanMarkdownResult(
        output_type="image",
        plan_markdown="# plan.md v1",
        template_path=Path("plan_image.md"),
        creation_contract=contract,
    )

    contract["image_model_capabilities"]["aspect_ratios"].append("9:16")
    result.creation_contract["image_model_capabilities"]["aspect_ratios"].append("16:9")

    assert result.plan_history[0]["creation_contract"] == {"image_model_capabilities": {"aspect_ratios": ["1:1"]}}


def test_next_version_deep_copies_nested_contract_and_caller_history():
    caller_history = [
        {
            "version": 1,
            "plan_markdown": "# plan.md v1",
            "creation_contract": {"image_model_capabilities": {"sizes": ["2K"]}},
            "scene_durations_sec": [],
        }
    ]
    original_history = copy.deepcopy(caller_history)
    next_contract = {"image_model_capabilities": {"sizes": ["4K"]}}
    current = PlanMarkdownResult(
        output_type="image",
        plan_markdown="# plan.md v1",
        template_path=Path("plan_image.md"),
        plan_history=caller_history,
        creation_contract=next_contract,
    )

    revised = current.next_version(
        plan_markdown="# plan.md v2",
        plan_history=caller_history,
        creation_contract=next_contract,
    )
    next_contract["image_model_capabilities"]["sizes"].append("8K")
    revised.creation_contract["image_model_capabilities"]["sizes"].append("1080p")

    assert revised.plan_history[-1]["creation_contract"] == {"image_model_capabilities": {"sizes": ["4K"]}}
    revised.plan_history[0]["creation_contract"]["image_model_capabilities"]["sizes"].append("4K")
    assert caller_history == original_history


def test_restore_deep_copies_nested_contract_and_caller_history():
    history = [
        {
            "version": 1,
            "plan_markdown": "# plan.md v1",
            "creation_contract": {"image_model_capabilities": {"aspect_ratios": ["1:1"]}},
            "scene_durations_sec": [],
        },
        {
            "version": 2,
            "plan_markdown": "# plan.md v2",
            "creation_contract": {"image_model_capabilities": {"aspect_ratios": ["9:16"]}},
            "scene_durations_sec": [],
        },
    ]
    original_history = copy.deepcopy(history)

    result = restore_plan_version(
        intent="image",
        current_plan_markdown="# plan.md v2",
        current_plan_version=2,
        plan_history=history,
        restore_version=1,
        creation_contract=history[1]["creation_contract"],
    )
    result.creation_contract["image_model_capabilities"]["aspect_ratios"].append("16:9")

    assert result.plan_history[0]["creation_contract"] == original_history[0]["creation_contract"]
    result.plan_history[0]["creation_contract"]["image_model_capabilities"]["aspect_ratios"].append("4:3")
    assert history == original_history


def test_next_version_uses_history_max_after_restore():
    creation_contract = {"video_model": "seedance-2.0", "video_duration_sec": 20}
    restored = PlanMarkdownResult(
        output_type="video",
        plan_markdown="# plan.md v1",
        template_path=Path("plan_video.md"),
        plan_version=1,
        plan_history=[
            {"version": 1, "plan_markdown": "# plan.md v1"},
            {"version": 2, "plan_markdown": "# plan.md v2"},
        ],
        creation_contract=creation_contract,
        scene_durations_sec=[10, 10],
    )

    revised = restored.next_version(
        plan_markdown="# plan.md v3",
        current_version=restored.plan_version,
    )

    assert revised.plan_version == 3
    assert [item["version"] for item in revised.plan_history] == [1, 2, 3]
    assert revised.plan_history[-1]["creation_contract"] == creation_contract
    assert revised.plan_history[-1]["scene_durations_sec"] == [10, 10]
