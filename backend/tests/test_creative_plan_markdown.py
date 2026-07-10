from __future__ import annotations

import asyncio

from pixelflow.creative.plan_markdown import build_plan_markdown, build_plan_markdown_with_llm

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
