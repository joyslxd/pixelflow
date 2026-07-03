from __future__ import annotations

from pixelflow.creative.plan_markdown import build_plan_markdown


def test_build_video_plan_markdown_fills_uploaded_template_sections():
    result = build_plan_markdown(
        "video",
        {
            "product_info": "苹果什么什么PRO",
            "product_category": "数码3C",
            "target_audience": "25-35",
            "conversion_goal": "引流直播间",
        },
        {
            "direction_id": "direction_1",
            "title": "痛点开场 + 产品解决",
            "description": "通勤路上先抛出续航痛点，再用产品能力完成解决。",
            "data": {"visual_anchor": "通勤、质感"},
        },
    )

    assert result.template_path.as_posix().endswith("backend/skills/public/borgrise-creative-assistant-v2/templates/plan.md")
    assert "## 一、选题方向" in result.plan_markdown
    assert "## 十、开发输出要求" in result.plan_markdown
    assert "苹果什么什么PRO" in result.plan_markdown
    assert "痛点开场 + 产品解决" in result.plan_markdown
    assert "引流直播间" in result.plan_markdown
    assert "【" not in result.plan_markdown
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
    assert "图片生成" in result.plan_markdown
    assert "视频生成不适用" in result.plan_markdown
    assert result.output_type == "image"
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
