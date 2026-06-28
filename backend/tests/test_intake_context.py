from __future__ import annotations

from pixelflow.intake.context import normalize_intake_context


def test_generic_image_goal_is_enriched_with_product_subject() -> None:
    context = normalize_intake_context(
        intent="image",
        source_prompt="帮我生成书包的宣传图",
        extracted={
            "product_subject": "书包",
            "creation_goal": "宣传图",
            "industry_type": "服饰鞋包",
            "values": {
                "image_goal": "宣传图",
                "image_usage": "活动宣传",
            },
        },
    )

    assert context.product_subject == "书包"
    assert context.creation_goal == "书包宣传图"
    assert context.form_values["image_goal"] == "书包宣传图"
    assert context.source_prompt == "帮我生成书包的宣传图"


def test_requested_image_count_defaults_to_one_and_preserves_explicit_count() -> None:
    default_context = normalize_intake_context(
        intent="image",
        source_prompt="生成书包宣传图",
        extracted={"product_subject": "书包", "values": {}},
    )
    multiple_context = normalize_intake_context(
        intent="image",
        source_prompt="生成3张书包宣传图",
        extracted={"product_subject": "书包", "requested_output_count": 3, "values": {"image_count": 3}},
    )

    assert default_context.requested_output_count == 1
    assert multiple_context.requested_output_count == 3
    assert multiple_context.form_values["image_count"] == 3


def test_video_context_preserves_product_subject_in_product_info() -> None:
    context = normalize_intake_context(
        intent="video",
        source_prompt="帮我生成书包的宣传视频",
        extracted={
            "product_subject": "书包",
            "creation_goal": "宣传视频",
            "industry_type": "服饰鞋包",
            "values": {
                "product_info": "",
                "product_category": "服饰鞋包",
            },
        },
    )

    assert context.product_subject == "书包"
    assert context.creation_goal == "书包宣传视频"
    assert context.form_values["product_info"] == "书包"
