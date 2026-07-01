from __future__ import annotations

from pixelflow.intake.forms import draft_creative_directions, get_form_schema, validate_form


def test_video_form_schema_matches_required_screenshot_fields():
    schema = get_form_schema("video")

    assert schema.form_id == "ad_short_video_intake"
    assert schema.title == "AD投放短视频需求收集"
    assert schema.output_type == "video"
    assert [field.id for field in schema.fields] == ["product_info", "product_category", "target_audience", "conversion_goal"]
    assert schema.fields[0].placeholder == "苹果什么什么PRO"
    assert schema.fields[1].options == ["美妆护肤", "食品饮料", "数码3C", "服饰鞋包", "家居日用", "保健养生", "其他品类"]
    assert schema.fields[3].options == ["直接购买", "品牌曝光", "种草引流", "引流直播间"]
    assert all(field.required for field in schema.fields)
    assert all(field.source == "system" for field in schema.fields)
    assert all(field.confidence == 0 for field in schema.fields)


def test_image_form_schema_matches_required_screenshot_fields():
    schema = get_form_schema("image")

    assert schema.form_id == "image_generation_intake"
    assert schema.title == "图片生成需求收集"
    assert schema.output_type == "image"
    assert [field.id for field in schema.fields] == ["image_goal", "image_type", "image_usage", "image_style", "image_size"]
    assert schema.fields[0].placeholder == "例如：科技感海报、办公室场景图、小红书封面、人物插画"
    assert schema.fields[1].options == ["商品广告图", "人物/场景图", "海报/封面图", "插画/概念图", "背景/素材图", "其他"]
    assert schema.fields[2].options == ["广告投放", "社媒发布", "内容封面", "详情页配图", "活动宣传", "内部展示", "其他用途"]
    assert schema.fields[3].options == ["真实摄影", "高级质感", "简洁干净", "小红书风", "科技感", "插画风", "自由发挥"]
    assert schema.fields[4].options == ["1:1", "16:9", "9:16", "自动适配"]


def test_ppt_form_schema_requires_topic_style_and_office_attachments():
    schema = get_form_schema("ppt")

    assert schema.form_id == "ppt_generation_intake"
    assert schema.title == "PPT生成需求收集"
    assert schema.output_type == "ppt"
    assert [field.id for field in schema.fields] == ["ppt_topic", "ppt_style", "attachments"]
    assert schema.fields[0].placeholder == "例如：2026年度营销策略汇报"
    assert schema.fields[1].options == ["极简商务", "科技数据", "教育培训", "产品发布", "投融资路演", "自定义"]
    assert schema.fields[2].type == "file_list"
    assert schema.fields[2].accept == [".doc", ".docx", ".xls", ".xlsx", ".pdf"]
    assert schema.fields[2].multiple is True


def test_validate_form_returns_missing_fields_and_terminates_after_three_rounds():
    result = validate_form("video", {"product_info": "  "}, intake_rounds=3)

    assert result.is_complete is False
    assert result.intake_rounds == 3
    assert result.terminated is True
    assert result.missing_fields == ["product_info", "product_category", "target_audience", "conversion_goal"]
    assert "最多确认 3 次" in result.message


def test_validate_form_accepts_complete_image_values():
    result = validate_form(
        "image",
        {
            "image_goal": "科技感耳机海报",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "科技感",
            "image_size": "9:16 竖版海报",
        },
        intake_rounds=1,
    )

    assert result.is_complete is True
    assert result.terminated is False
    assert result.missing_fields == []
    assert result.values["image_style"] == "科技感"


def test_validate_ppt_form_accepts_only_word_excel_and_pdf_attachments():
    result = validate_form(
        "ppt",
        {
            "ppt_topic": "2026年度营销策略汇报",
            "ppt_style": "极简商务",
            "attachments": [
                {"name": "report.docx", "url": "https://x/report.docx?token=abc"},
                {"name": "data.xlsx", "url": "https://x/data.xlsx"},
                {"name": "process.pdf", "url": "https://x/process.pdf#page=1"},
            ],
        },
    )

    assert result.is_complete is True
    assert result.missing_fields == []
    assert [item["url"] for item in result.values["attachments"]] == [
        "https://x/report.docx?token=abc",
        "https://x/data.xlsx",
        "https://x/process.pdf#page=1",
    ]


def test_validate_ppt_form_rejects_unsupported_attachment_extensions():
    result = validate_form(
        "ppt",
        {
            "ppt_topic": "新品上市汇报",
            "ppt_style": "产品发布",
            "attachments": [{"name": "cover.png", "url": "https://x/cover.png"}],
        },
    )

    assert result.is_complete is False
    assert result.missing_fields == ["attachments"]
    assert "仅支持 Word、Excel、PDF" in result.message


def test_validate_image_form_preserves_hidden_image_count_metadata():
    result = validate_form(
        "image",
        {
            "image_goal": "篮球主题宣传图",
            "image_type": "海报/封面图",
            "image_usage": "社媒发布",
            "image_style": "真实摄影",
            "image_size": "自动适配",
            "image_count": 4,
        },
    )

    assert result.is_complete is True
    assert result.values["image_count"] == 4


def test_draft_creative_directions_returns_three_with_recommended_first():
    directions = draft_creative_directions(
        "video",
        {
            "product_info": "苹果什么什么PRO",
            "product_category": "数码3C",
            "target_audience": "25-35",
            "conversion_goal": "引流直播间",
        },
        product_creative_profile={"visual_anchor_keywords": ["通勤", "质感"]},
    )

    assert len(directions) == 3
    assert directions[0].recommended is True
    assert {direction.direction_id for direction in directions} == {"direction_1", "direction_2", "direction_3"}
    assert all(direction.title for direction in directions)
    assert all("苹果什么什么PRO" in direction.description for direction in directions)
