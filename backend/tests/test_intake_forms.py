from __future__ import annotations

from pixelflow.intake.forms import draft_creative_directions, get_form_schema, validate_form


def test_video_form_schema_matches_required_screenshot_fields():
    schema = get_form_schema("video")

    assert schema.form_id == "ad_short_video_intake"
    assert schema.title == "AD投放短视频需求收集"
    assert schema.output_type == "video"
    assert [field.id for field in schema.fields] == [
        "product_info",
        "product_category",
        "target_audience",
        "conversion_goal",
        "video_duration_sec",
        "video_ratio",
        "video_model_mode",
        "video_model",
        "image_model",
        "image_model_capabilities",
        "video_usage",
        "visual_style",
    ]
    assert schema.fields[0].placeholder == "苹果什么什么PRO"
    assert schema.fields[1].type == "text"
    assert schema.fields[1].placeholder == "例如：服饰鞋包、运动鞋、数码3C"
    assert schema.fields[1].options == []
    assert schema.fields[3].options == ["直接购买", "品牌曝光", "种草引流", "引流直播间"]
    fields = {field.id: field for field in schema.fields}
    assert fields["video_duration_sec"].default_value == 30
    assert fields["video_duration_sec"].options == ["30", "60", "90", "180", "自定义"]
    assert fields["video_ratio"].type == "select"
    assert fields["video_ratio"].default_value == "9:16"
    assert fields["video_model_mode"].default_value == "system_recommended"
    assert fields["video_model"].default_value == "seedance-2.0"
    assert fields["image_model"].default_value == "gpt-image-2"
    assert fields["image_model_capabilities"].default_value == {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    }
    assert fields["video_usage"].default_value == "宣传片"
    assert fields["visual_style"].required is False
    assert all(field.required for field in schema.fields if field.id != "visual_style")
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


def test_validate_video_form_builds_confirmed_creation_contract() -> None:
    result = validate_form(
        "video",
        {
            "product_info": "AuroraFit 智能健康戒指",
            "product_category": "数码3C",
            "target_audience": "25-35 岁健康管理人群",
            "conversion_goal": "引流直播间",
            "video_duration_sec": 180,
            "video_ratio": "16:9",
            "video_model_mode": "manual",
            "video_model": "seedance-2.0-mini",
            "video_model_capabilities": {
                "generation_types": ["文生视频", "首尾帧", "全能参考"],
                "upload_file_types": ["JPG", "PNG", "MP4"],
                "aspect_ratios": ["9:16", "16:9"],
                "sizes": ["480p", "720p"],
                "sound_options": ["on", "off"],
                "durations_sec": list(range(4, 16)),
            },
            "video_size": "720p",
            "video_sound": "off",
            "image_model": "gpt-image-2",
            "image_model_capabilities": {
                "aspect_ratios": ["1:1", "16:9", "9:16"],
                "sizes": ["1080p", "2K", "4K"],
            },
            "video_usage": "新品宣传",
            "visual_style": "电影写实风",
        },
    )

    assert result.is_complete is True
    assert result.values["video_duration_sec"] == 180
    assert result.values["video_ratio"] == "16:9"
    assert result.values["video_model"] == "seedance-2.0-mini"
    assert result.values["video_model_capabilities"]["sizes"] == ["480p", "720p"]
    assert result.values["video_size"] == "720p"
    assert result.values["video_sound"] == "off"
    assert result.values["image_model"] == "gpt-image-2"
    assert result.values["image_model_capabilities"]["sizes"] == ["1080p", "2K", "4K"]


def test_validate_video_form_requires_realtime_video_model_capabilities() -> None:
    result = validate_form(
        "video",
        {
            "product_info": "AuroraFit 智能健康戒指",
            "product_category": "数码3C",
            "target_audience": "25-35 岁健康管理人群",
            "conversion_goal": "引流直播间",
            "video_duration_sec": 30,
            "video_ratio": "9:16",
            "video_model_mode": "manual",
            "video_model": "seedance-2.0-mini",
            "image_model": "gpt-image-2",
            "image_model_capabilities": {
                "aspect_ratios": ["1:1", "16:9", "9:16"],
                "sizes": ["1080p", "2K", "4K"],
            },
            "video_usage": "新品宣传",
        },
    )

    assert result.is_complete is False
    assert "video_model" in result.missing_fields
    assert "实时能力" in result.message


def test_validate_video_form_rejects_invalid_custom_duration() -> None:
    result = validate_form(
        "video",
        {
            "product_info": "AuroraFit 智能健康戒指",
            "product_category": "数码3C",
            "target_audience": "25-35 岁健康管理人群",
            "conversion_goal": "引流直播间",
            "video_duration_sec": 301,
        },
    )

    assert result.is_complete is False
    assert "video_duration_sec" in result.missing_fields
    assert "4-300" in result.message


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
