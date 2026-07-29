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
    revise_plan_markdown_with_llm,
)
from pixelflow.creative.revision_contract import (
    build_manual_plan_revision_feedback,
    extract_explicit_revision_patch,
    mentioned_revision_fields,
    merge_revision_contract,
    validate_revision_contract,
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


class SequenceFakeModel:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.prompts: list[object] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.contents) - 1)
        return FakeMessage(self.contents[index])


def _revision_blueprints(total_duration_sec: int) -> list[dict[str, object]]:
    blueprints: list[dict[str, object]] = []
    cursor = 0
    scene_count = total_duration_sec // 10
    for index in range(1, scene_count + 1):
        role = "opening" if index == 1 else "conclusion" if index == scene_count else "development"
        blueprints.append(
            {
                "scene_id": f"scene-{index}",
                "scene_index": index,
                "title": f"新版分镜{index}",
                "structure_role": role,
                "start_sec": cursor,
                "end_sec": cursor + 10,
                "duration_sec": 10,
                "storyline": f"新版故事线{index}，严格服务最终确认方案。",
                "shot_description": (
                    f"0-10秒: 地点：现代客厅；主体：体验者与智能空调；动作：体验者执行第{index}段使用动作并观察反馈；"
                    "景别：中景切产品特写；运镜：稳定跟拍后缓慢推近；光影：柔和自然光突出产品轮廓；"
                    "声音：保留空调运行声并配合旁白；收束：停在本段结果细节并沿视线衔接下一镜。"
                ),
                "narration": f"新版旁白{index}。",
                "transition": "动作匹配转场。" if index < scene_count else "定格收束。",
                "asset_requirements": {
                    "characters": ["体验者"],
                    "scenes": ["现代客厅"],
                    "props": ["智能空调"],
                },
            }
        )
        cursor += 10
    return blueprints


def _valid_generation_blueprints(total_duration_sec: int) -> list[dict[str, object]]:
    durations = [6, 12, 8] if total_duration_sec == 26 else [10] * (total_duration_sec // 10)
    blueprints: list[dict[str, object]] = []
    cursor = 0
    for index, duration in enumerate(durations, start=1):
        role = "opening" if index == 1 else "conclusion" if index == len(durations) else "development"
        blueprints.append(
            {
                "scene_id": f"scene-{index}",
                "scene_index": index,
                "title": f"健康陪伴分镜{index}",
                "structure_role": role,
                "start_sec": cursor,
                "end_sec": cursor + duration,
                "duration_sec": duration,
                "storyline": f"体验者在晨光公园推进第{index}段健康体验。",
                "shot_description": (
                    f"0-{duration}秒: 地点：晨光公园；主体：健康体验者与AuroraFit智能健康戒指；"
                    f"动作：体验者完成第{index}段健康记录并查看结果；景别：中景切戒指特写；"
                    "运镜：稳定跟拍后缓慢推近；光影：晨光侧逆光勾勒戒指轮廓；"
                    "声音：保留脚步声与轻柔音乐；收束：停在健康数据结果并衔接下一镜。"
                ),
                "narration": f"第{index}段健康陪伴清晰可见。",
                "transition": "动作匹配转场。" if index < len(durations) else "产品定格结束。",
                "asset_requirements": {
                    "characters": ["健康体验者"],
                    "scenes": ["晨光公园"],
                    "props": ["AuroraFit智能健康戒指"],
                },
            }
        )
        cursor += duration
    return blueprints


@pytest.mark.parametrize(
    ("feedback", "expected_duration"),
    [
        ("把总时长从30秒修改为180秒", 180),
        ("视频总时长不要改成60秒，保持30秒", 30),
        ("把视频总时长修改为1分钟30秒", 90),
    ],
)
def test_explicit_revision_patch_uses_final_duration_directive(feedback: str, expected_duration: int) -> None:
    assert extract_explicit_revision_patch("video", feedback)["video_duration_sec"] == expected_duration


@pytest.mark.parametrize(
    "feedback",
    [
        "把第2个分镜时长从10秒改成8秒",
        "镜头1保持5秒，镜头2调整为12秒",
        "请确认当前视频总时长是30秒吗",
    ],
)
def test_explicit_revision_patch_does_not_treat_scene_duration_as_total(feedback: str) -> None:
    assert "video_duration_sec" not in extract_explicit_revision_patch("video", feedback)


def test_explicit_revision_patch_does_not_treat_poster_assets_as_output_count() -> None:
    assert "image_count" not in extract_explicit_revision_patch("image", "背景墙里摆放3张海报，主体保持不变")


@pytest.mark.parametrize(
    ("intent", "feedback", "field_name", "expected"),
    [
        ("video", "视频模型从 seedance-1.5-pro 改成 seedance-2.0", "video_model", "seedance-2.0"),
        ("video", "图片模型从 seeddream-4.5 改成 gpt-image-2", "image_model", "gpt-image-2"),
        ("video", "视频模型从seedance-1.5-pro改成seedance-2.0", "video_model", "seedance-2.0"),
        ("video", "图片模型从seeddream-4.5改成gpt-image-2", "image_model", "gpt-image-2"),
    ],
)
def test_explicit_revision_patch_uses_new_model_value(
    intent: str,
    feedback: str,
    field_name: str,
    expected: str,
) -> None:
    assert extract_explicit_revision_patch(intent, feedback)[field_name] == expected


def test_revision_fields_do_not_open_visual_style_when_user_says_keep_it() -> None:
    fields = mentioned_revision_fields("video", "风格不要改，只把视频总时长改成60秒")

    assert "video_duration_sec" in fields
    assert "visual_style" not in fields


def test_explicit_revision_patch_uses_final_image_ratio() -> None:
    patch = extract_explicit_revision_patch("image", "图片尺寸从1:1改成16:9")

    assert patch["image_size"] == "16:9"


def test_merge_revision_contract_recognizes_natural_video_duration_phrase() -> None:
    merged = merge_revision_contract(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        "把片子改成180秒，其他内容保持不变",
        {"video_duration_sec": 180},
    )

    assert merged["video_duration_sec"] == 180


@pytest.mark.parametrize(
    ("feedback", "llm_duration", "expected"),
    [
        ("视频总时长延长30秒", 60, 60),
        ("把视频延长30秒", 60, 60),
        ("视频总时长缩短10秒", 20, 20),
        ("时长调成180秒", 180, 180),
        ("做成三分钟", 180, 180),
    ],
)
def test_merge_revision_contract_applies_relative_total_duration(
    feedback: str,
    llm_duration: int,
    expected: int,
) -> None:
    merged = merge_revision_contract(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        feedback,
        {"video_duration_sec": llm_duration},
    )

    assert merged["video_duration_sec"] == expected


def test_merge_image_revision_contract_applies_relative_chinese_output_count() -> None:
    merged = merge_revision_contract(
        "image",
        {
            "intent": "image",
            "image_goal": "智能音箱宣传图",
            "image_type": "商品广告图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "1:1",
            "image_count": 1,
        },
        "再多出两版，其他不变",
        {"image_count": 3},
    )

    assert merged["image_count"] == 3


def test_merge_revision_contract_rejects_model_change_without_capability_snapshot() -> None:
    with pytest.raises(ValueError, match="模型能力"):
        merge_revision_contract(
            "video",
            VIDEO_FORM,
            "视频模型改成 seedance-2.0-fast",
            {"video_model": "seedance-2.0-fast"},
        )


def test_validate_image_revision_contract_rejects_malformed_ratio() -> None:
    with pytest.raises(ValueError, match="图片尺寸"):
        validate_revision_contract(
            "image",
            {
                "intent": "image",
                "image_goal": "智能音箱宣传图",
                "image_type": "商品广告图",
                "image_usage": "社媒发布",
                "image_style": "真实摄影",
                "image_size": "16:99",
                "image_count": 1,
            },
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("image_goal", {"value": "智能音箱宣传图"}),
        ("image_type", ["商品广告图"]),
        ("image_usage", 123),
        ("image_style", {"name": "真实摄影"}),
        ("image_size", ["1:1"]),
    ],
)
def test_validate_image_revision_contract_rejects_non_string_fields(field_name: str, invalid_value: object) -> None:
    contract = {
        "image_goal": "智能音箱宣传图",
        "image_type": "商品广告图",
        "image_usage": "社媒发布",
        "image_style": "真实摄影",
        "image_size": "1:1",
        "image_count": 1,
    }
    contract[field_name] = invalid_value

    with pytest.raises(ValueError):
        validate_revision_contract("image", contract)


def test_revise_video_plan_updates_contract_and_exact_scene_total_from_feedback() -> None:
    original = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        {"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
    )
    revised_blueprints = _revision_blueprints(180)
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能空调 180 秒体验片\n\n## 一、选题方向\n在当前创意基础上扩展完整体验过程。\n\n## 三、视频规格\n- 时长：180 秒\n- 画幅：9:16\n\n## 五、镜头列表\n严格按新版蓝图执行。"),
                "creation_contract_patch": {"video_duration_sec": 180},
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": revised_blueprints,
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values={**VIDEO_FORM, "video_duration_sec": 30},
            selected_direction={"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="把视频总时长修改为180秒，其他创意保持不变",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.plan_version == 2
    assert revised.creation_contract["video_duration_sec"] == 180
    assert len(revised.scene_blueprints) == 18
    assert sum(revised.scene_durations_sec) == 180
    assert all(4 <= duration <= 15 for duration in revised.scene_durations_sec)
    assert "视频总时长：180 秒" in revised.plan_markdown
    assert revised.plan_history[-1]["creation_contract"]["video_duration_sec"] == 180
    assert sum(item["duration_sec"] for item in revised.plan_history[-1]["scene_blueprints"]) == 180


def test_revise_image_plan_updates_final_execution_contract() -> None:
    form_values = {
        "image_goal": "智能音箱宣传图",
        "image_type": "商品广告图",
        "image_usage": "社媒发布",
        "image_style": "真实摄影",
        "image_size": "1:1",
        "image_count": 1,
    }
    direction = {"direction_id": "direction_1", "title": "家居氛围", "description": "展示智能音箱融入现代家居。"}
    original = build_plan_markdown("image", form_values, direction)
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能音箱横版组图\n\n## 一、选题方向\n延续家居氛围并增加科技秩序感。\n\n## 三、图片规格\n- 尺寸：16:9\n- 数量：4 张\n\n## 五、主图方案\n生成四张不同家居时段的横版画面。"),
                "creation_contract_patch": {
                    "image_style": "极简科技感",
                    "image_size": "16:9",
                    "image_count": 4,
                },
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="image",
            form_values=form_values,
            selected_direction=direction,
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="改成16:9极简科技风，并生成4张",
            creation_contract=original.creation_contract,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.creation_contract["image_style"] == "极简科技感"
    assert revised.creation_contract["image_size"] == "16:9"
    assert revised.creation_contract["image_count"] == 4
    assert "图片尺寸：16:9" in revised.plan_markdown
    assert "生成数量：4 张" in revised.plan_markdown


def test_revise_video_plan_keeps_current_version_when_retried_blueprint_is_invalid() -> None:
    original = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        {"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
    )
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能空调 60 秒体验片\n\n## 一、选题方向\n延续舒适体验创意并扩展完整使用旅程。\n\n## 三、视频规格\n- 时长：60 秒\n- 画幅：9:16\n\n## 五、镜头列表\n由权威分镜蓝图承载完整调度。"),
                "creation_contract_patch": {"video_duration_sec": 60},
                # 模拟 LLM 忘记扩充分镜，仍返回旧版 30 秒调度。
                "scene_blueprints": _revision_blueprints(30),
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values={**VIDEO_FORM, "video_duration_sec": 30},
            selected_direction={"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="把总时长修改为60秒",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.plan_version == original.plan_version
    assert revised.plan_history == original.plan_history
    assert revised.creation_contract == original.creation_contract
    assert revised.scene_blueprints == original.scene_blueprints
    assert revised.error


def test_revise_video_plan_ignores_llm_patch_for_unmentioned_fields() -> None:
    original = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        {"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
    )
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能空调 60 秒体验片\n\n## 一、选题方向\n延续原创意。\n\n## 三、视频规格\n- 时长：60 秒\n\n## 五、镜头列表\n严格按蓝图执行。"),
                "creation_contract_patch": {
                    "video_duration_sec": 60,
                    "visual_style": "赛博朋克",
                    "video_model": "seedance-1.5-pro",
                },
                "scene_blueprints": _revision_blueprints(60),
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values={**VIDEO_FORM, "video_duration_sec": 30},
            selected_direction={"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="只把总时长修改为60秒，其他内容保持不变",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.creation_contract["video_duration_sec"] == 60
    assert revised.creation_contract["visual_style"] == VIDEO_FORM["visual_style"]
    assert revised.creation_contract["video_model"] == VIDEO_FORM["video_model"]


def test_revise_video_plan_retries_invalid_contract_then_uses_corrected_patch() -> None:
    original = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        {"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
    )
    invalid_payload = {
        "plan_markdown": "# 智能空调体验片\n\n## 一、选题方向\n延续原创意。\n\n## 三、视频规格\n调整时长。\n\n## 五、镜头列表\n按蓝图执行。",
        "creation_contract_patch": {"video_duration_sec": 500},
        "scene_blueprints": _revision_blueprints(30),
    }
    corrected_payload = {
        "plan_markdown": "# 智能空调 60 秒体验片\n\n## 一、选题方向\n延续原创意。\n\n## 三、视频规格\n- 时长：60 秒\n\n## 五、镜头列表\n按蓝图执行。",
        "creation_contract_patch": {"video_duration_sec": 60},
        "scene_blueprints": _revision_blueprints(60),
    }
    fake_model = SequenceFakeModel(
        [
            json.dumps(invalid_payload, ensure_ascii=False),
            json.dumps(corrected_payload, ensure_ascii=False),
        ]
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values={**VIDEO_FORM, "video_duration_sec": 30},
            selected_direction={"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="把总时长延长一些",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    # 两次 Plan 合同修订后，还会执行一次 Seedance 专用写作并按校验反馈重试一次。
    assert len(fake_model.prompts) == 4
    assert "500" in str(fake_model.prompts[1])
    assert "Seedance Plan 分镜写作 Skill" in fake_model.prompts[2]
    assert revised.creation_contract["video_duration_sec"] == 60
    assert sum(revised.scene_durations_sec) == 60


def test_revise_video_plan_does_not_publish_invalid_contract_after_retry() -> None:
    original = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        {"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
    )
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": "# 智能空调体验片\n\n## 一、选题方向\n延续原创意。\n\n## 三、视频规格\n调整时长。\n\n## 五、镜头列表\n按蓝图执行。",
                "creation_contract_patch": {"video_duration_sec": 500},
                "scene_blueprints": _revision_blueprints(30),
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values={**VIDEO_FORM, "video_duration_sec": 30},
            selected_direction={"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback="把总时长延长一些",
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert len(fake_model.prompts) == 2
    assert revised.plan_version == original.plan_version
    assert revised.plan_history == original.plan_history
    assert revised.creation_contract == original.creation_contract
    assert revised.scene_blueprints == original.scene_blueprints
    assert revised.error


def test_build_video_plan_with_llm_uses_uploaded_template_and_constrains_scene_image_specs() -> None:
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": "# AuroraFit 智能健康戒指新品宣传\n\n## 一、选题方向\n晨跑到睡眠的健康陪伴。\n\n## 三、视频规格\n- 时长：180 秒\n- 画幅：9:16\n\n## 五、镜头列表\n严格按照提供的精确镜头时间线执行。",
                "scene_image_ratio": "3:4",
                "scene_image_size": "8K",
                "scene_blueprints": _valid_generation_blueprints(180),
            },
            ensure_ascii=False,
        )
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
    assert "地点：雨中街道" in result.plan_markdown
    assert "声音：" in result.plan_markdown
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
                "scene_blueprints": _valid_generation_blueprints(26),
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


def test_plan_removes_exact_semantic_memory_text_without_internal_marker() -> None:
    memory_text = "品牌长期偏好：真实摄影，避免夸张特效。"
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": (f"# 防水背包宣传片\n\n## 一、选题方向\n雨天通勤实测。\n\n## 二、选题优势\n{memory_text}\n产品证明清晰。\n\n## 三、视频规格\n- 时长：26 秒\n\n## 五、镜头列表\n按蓝图执行。"),
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": _valid_generation_blueprints(26),
            },
            ensure_ascii=False,
        )
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            {**VIDEO_FORM, "video_duration_sec": 26},
            {"direction_id": "direction_1", "title": "雨天实测", "description": "证明防水能力。"},
            intake_context={
                "semantic_memory": {
                    "enabled": True,
                    "items": [{"content": memory_text, "metadata": {"category": "preference"}}],
                }
            },
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert memory_text in str(fake_model.prompts[0])
    assert memory_text not in result.plan_markdown
    assert "产品证明清晰。" in result.plan_markdown


def test_plan_removes_semantic_memory_even_when_llm_adds_markdown_formatting() -> None:
    memory_text = "品牌长期偏好：真实摄影，避免夸张特效。"
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 防水背包宣传片\n\n## 一、选题方向\n雨天通勤实测。\n\n## 二、选题优势\n品牌长期偏好：**真实摄影**，避免夸张特效。\n产品证明清晰。\n\n## 三、视频规格\n- 时长：26 秒\n\n## 五、镜头列表\n按蓝图执行。"),
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": _valid_generation_blueprints(26),
            },
            ensure_ascii=False,
        )
    )

    result = asyncio.run(
        build_plan_markdown_with_llm(
            "video",
            {**VIDEO_FORM, "video_duration_sec": 26},
            {"direction_id": "direction_1", "title": "雨天实测", "description": "证明防水能力。"},
            intake_context={
                "semantic_memory": {
                    "enabled": True,
                    "items": [{"content": memory_text, "metadata": {"category": "preference"}}],
                }
            },
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert memory_text in str(fake_model.prompts[0])
    assert memory_text not in result.plan_markdown.replace("**", "")
    assert "产品证明清晰。" in result.plan_markdown


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


def test_manual_video_plan_edit_reconciles_markdown_contract_and_blueprints_with_llm():
    original = build_plan_markdown(
        "video",
        {**VIDEO_FORM, "video_duration_sec": 30},
        {"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
    )
    edited_markdown = original.plan_markdown.replace("30 秒", "60 秒")
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能空调 60 秒体验片\n\n## 一、选题方向\n保留当前创意并扩展完整使用旅程。\n\n## 三、视频规格\n- 时长：60 秒\n- 画幅：9:16\n\n## 五、镜头列表\n严格按新版蓝图执行。"),
                "creation_contract_patch": {"video_duration_sec": 60},
                "scene_image_ratio": "9:16",
                "scene_image_size": "4K",
                "scene_blueprints": _revision_blueprints(60),
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="video",
            form_values={**VIDEO_FORM, "video_duration_sec": 30},
            selected_direction={"direction_id": "direction_1", "title": "舒适体验", "description": "展示智能空调带来的舒适变化。"},
            current_plan_markdown=original.plan_markdown,
            current_plan_version=original.plan_version,
            plan_history=original.plan_history,
            revision_feedback=build_manual_plan_revision_feedback(original.plan_markdown, edited_markdown),
            creation_contract=original.creation_contract,
            current_scene_blueprints=original.scene_blueprints,
            change_source="manual_edit",
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.plan_version == 2
    assert revised.creation_contract["video_duration_sec"] == 60
    assert sum(revised.scene_durations_sec) == 60
    assert len(revised.scene_blueprints) == 6
    assert "视频总时长：60 秒" in revised.plan_markdown
    assert revised.llm_used is True
    assert revised.plan_history[-1]["change_source"] == "manual_edit"


def test_manual_image_plan_edit_allows_llm_to_update_every_contract_field():
    form_values = {
        "image_goal": "智能音箱宣传图",
        "image_type": "商品广告图",
        "image_usage": "社媒发布",
        "image_style": "真实摄影",
        "image_size": "1:1",
        "image_count": 1,
    }
    direction = {"direction_id": "direction_1", "title": "家居氛围", "description": "展示智能音箱融入现代家居。"}
    original = build_plan_markdown("image", form_values, direction)
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能音箱横版组图\n\n## 一、选题方向\n升级为极简科技风横版组图。\n\n## 三、图片规格\n- 尺寸：16:9\n- 数量：3 张\n\n## 五、主图方案\n生成三张不同家居时段的画面。"),
                "creation_contract_patch": {
                    "image_style": "极简科技感",
                    "image_size": "16:9",
                    "image_count": 3,
                },
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="image",
            form_values=form_values,
            selected_direction=direction,
            current_plan_markdown=original.plan_markdown,
            current_plan_version=1,
            plan_history=original.plan_history,
            revision_feedback=build_manual_plan_revision_feedback(
                original.plan_markdown,
                original.plan_markdown.replace("真实摄影", "极简科技感").replace("1:1", "16:9").replace("1 张", "3 张"),
            ),
            creation_contract=original.creation_contract,
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.creation_contract["image_style"] == "极简科技感"
    assert revised.creation_contract["image_size"] == "16:9"
    assert revised.creation_contract["image_count"] == 3
    assert revised.plan_version == 2


def test_manual_image_plan_edit_ignores_llm_contract_fields_not_changed_by_user():
    form_values = {
        "image_goal": "智能音箱宣传图",
        "image_type": "商品广告图",
        "image_usage": "社媒发布",
        "image_style": "真实摄影",
        "image_size": "1:1",
        "image_count": 1,
    }
    direction = {"direction_id": "direction_1", "title": "家居氛围", "description": "展示智能音箱融入现代家居。"}
    original = build_plan_markdown("image", form_values, direction)
    edited_markdown = original.plan_markdown.replace("展示智能音箱融入现代家居。", "重点突出音箱旋钮的金属细节。")
    fake_model = FakeModel(
        json.dumps(
            {
                "plan_markdown": ("# 智能音箱宣传图\n\n## 一、选题方向\n重点突出音箱旋钮的金属细节。\n\n## 三、图片规格\n维持原规格。\n\n## 五、主图方案\n强化旋钮细节。"),
                "creation_contract_patch": {
                    "image_style": "插画风",
                    "image_size": "16:9",
                    "image_count": 7,
                },
            },
            ensure_ascii=False,
        )
    )

    revised = asyncio.run(
        revise_plan_markdown_with_llm(
            intent="image",
            form_values=form_values,
            selected_direction=direction,
            current_plan_markdown=original.plan_markdown,
            current_plan_version=1,
            plan_history=original.plan_history,
            revision_feedback=build_manual_plan_revision_feedback(original.plan_markdown, edited_markdown),
            creation_contract=original.creation_contract,
            change_source="manual_edit",
            model_factory=lambda *_args, **_kwargs: fake_model,
        )
    )

    assert revised.plan_version == 2
    assert revised.creation_contract["image_style"] == "真实摄影"
    assert revised.creation_contract["image_size"] == "1:1"
    assert revised.creation_contract["image_count"] == 1


def test_manual_image_plan_edit_keeps_readonly_count_when_model_invents_count():
    current_contract = {
        "intent": "image",
        "image_goal": "蓝色保温杯电商主图",
        "image_type": "商品广告图",
        "image_usage": "广告投放",
        "image_style": "简洁干净",
        "image_size": "1:1",
        "image_count": 1,
    }
    current_markdown = "# 保温杯主图\n\n## 六、视觉重点\n\n- 突出杯盖密封圈。\n\n## 制作执行合同\n\n- 生成数量：1 张"
    edited_markdown = current_markdown.replace("突出杯盖密封圈。", "强化杯盖密封圈与包内物品干燥状态的视觉证据。")

    merged = merge_revision_contract(
        "image",
        current_contract,
        build_manual_plan_revision_feedback(current_markdown, edited_markdown),
        {"image_count": 3},
    )

    assert merged["image_count"] == 1
