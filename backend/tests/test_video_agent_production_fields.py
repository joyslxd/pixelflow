"""生产字段探测：一律 LLM，禁止本地正则猜画幅/时长/CTA。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pixelflow.agent_runtime.service import _confirmation_cost_summary
from pixelflow.video_agent.production_fields import (
    CLARIFY_MARKER,
    ProductionFieldsAnalysis,
    _parse_analysis_payload,
    analyze_production_fields_with_llm,
    creative_confirm_cost_summary,
    format_creative_confirm_clarification,
    looks_like_production_field_reply,
    looks_like_scene_asset_continue,
    normalize_user_text,
)


def test_normalize_fullwidth_colon() -> None:
    assert normalize_user_text("9：16") == "9:16"


def test_parse_analysis_payload_keeps_aspect_and_cta_values() -> None:
    parsed = _parse_analysis_payload(
        '{"duration_sec": 180, "has_aspect_ratio": true, "aspect_ratio": "9:16", '
        '"has_ending_cta": true, "ending_cta": "none", "missing": []}'
    )
    assert parsed is not None
    assert parsed.aspect_ratio == "9:16"
    assert parsed.ending_cta == "none"
    assert parsed.missing == ()


def test_parse_analysis_payload_drops_duration_from_missing() -> None:
    parsed = _parse_analysis_payload(
        '{"duration_sec": 180, "has_aspect_ratio": false, '
        '"has_ending_cta": false, "missing": ["视频时长", "视频画幅", "结尾行动引导"]}'
    )
    assert parsed is not None
    assert parsed.duration_sec == 180
    assert parsed.missing == ("视频画幅", "结尾行动引导")


def test_format_clarification_uses_explicit_llm_fields_only() -> None:
    clarification = format_creative_confirm_clarification(
        missing=["视频画幅", "结尾行动引导"],
        duration_sec=180,
    )
    assert "已识别时长：180秒" in clarification
    assert CLARIFY_MARKER in clarification
    assert "视频时长" not in clarification


def test_production_field_reply_is_structural_gate_only() -> None:
    """补字段门闩只认 awaiting/missing，不认话术关键词。"""

    with_missing = {
        "script": {"content": "x" * 100, "missing_requirements": ["视频画幅"]},
    }
    assert looks_like_production_field_reply(
        "9：16",
        workspace_payload=with_missing,
    ) is True
    # 「没有参考图」是生图续步，即使工作区仍缺画幅也不能当成补字段。
    assert looks_like_production_field_reply(
        "没有参考图，直接生成",
        workspace_payload=with_missing,
    ) is False
    assert looks_like_scene_asset_continue("没有参考图，直接生成") is True
    # 确认脚本不得被 awaiting/missing 截胡。
    assert looks_like_production_field_reply(
        "确认脚本",
        workspace_payload={
            "awaiting_production_fields": True,
            "script": {
                "content": "x" * 100,
                "missing_requirements": ["结尾行动引导"],
            },
        },
    ) is False
    # 单镜重生成不得被 awaiting 截胡成补字段。
    assert looks_like_production_field_reply(
        "确认并生成分镜视频（scene-1）",
        workspace_payload={
            "awaiting_production_fields": True,
            "script": {
                "content": "x" * 100,
                "missing_requirements": ["视频画幅"],
            },
        },
    ) is False
    # 无 awaiting/missing 时，即使短句也不当成补字段。
    assert looks_like_production_field_reply(
        "9：16",
        workspace_payload={"script": {"content": "x" * 100, "missing_requirements": []}},
    ) is False
    assert looks_like_production_field_reply(
        "随便聊聊天气",
        workspace_payload={"latest_input": "短"},
    ) is False


def test_enrich_choice_replies_maps_third_option_to_none_cta() -> None:
    from pixelflow.video_agent.production_fields import enrich_analysis_with_choice_replies

    incomplete = ProductionFieldsAnalysis(
        duration_sec=180,
        missing=("视频画幅", "结尾行动引导"),
        has_aspect_ratio=False,
        has_ending_cta=False,
    )
    enriched = enrich_analysis_with_choice_replies("1. 9：16 2. 第三个", incomplete)
    assert enriched.aspect_ratio == "9:16"
    assert enriched.has_aspect_ratio is True
    assert enriched.ending_cta == "none"
    assert enriched.has_ending_cta is True
    assert enriched.missing == ()


def test_enrich_choice_replies_ignores_third_shot_edit() -> None:
    from pixelflow.video_agent.production_fields import enrich_analysis_with_choice_replies

    incomplete = ProductionFieldsAnalysis(
        duration_sec=None,
        missing=("结尾行动引导",),
        has_aspect_ratio=True,
        has_ending_cta=False,
        aspect_ratio="9:16",
    )
    enriched = enrich_analysis_with_choice_replies("把第三个分镜旁白改短", incomplete)
    assert enriched.has_ending_cta is False
    assert enriched.ending_cta is None
    assert "结尾行动引导" in enriched.missing


def test_production_field_reply_uses_workspace_not_regex() -> None:
    assert looks_like_production_field_reply("9：16") is True
    assert looks_like_production_field_reply(
        "9：16",
        workspace_payload={
            "script": {
                "content": "完整脚本",
                "missing_requirements": ["视频画幅", "结尾行动引导"],
            }
        },
    ) is True
    assert looks_like_production_field_reply(
        "随便聊聊天气",
        workspace_payload={"latest_input": "短"},
    ) is False


@pytest.mark.asyncio
async def test_analyze_production_fields_with_llm_fake_model() -> None:
    class _FieldsModel:
        def invoke(self, messages):  # noqa: ANN001, ARG002
            return SimpleNamespace(
                content=(
                    '{"duration_sec": 180, "has_aspect_ratio": true, '
                    '"has_ending_cta": true, "missing": []}'
                ),
            )

    analysis = await analyze_production_fields_with_llm(
        text="脚本…\n\n【本轮指令】9:16,结尾不需要引导",
        model=_FieldsModel(),
    )
    assert analysis.duration_sec == 180
    assert analysis.has_aspect_ratio is True
    assert analysis.has_ending_cta is True
    assert analysis.missing == ()


def test_build_excerpt_prefers_round_instruction() -> None:
    from pixelflow.video_agent.production_fields import build_production_fields_excerpt

    long_script = "分镜正文" * 200
    text = f"{long_script}\n\n【本轮指令】9:16,结尾不需要引导"
    excerpt = build_production_fields_excerpt(text)
    assert "【本轮指令】9:16,结尾不需要引导" in excerpt
    assert len(excerpt) < len(text)


def test_format_production_fields_update_notice() -> None:
    from pixelflow.video_agent.production_fields import format_production_fields_update_notice

    notice = format_production_fields_update_notice(
        ProductionFieldsAnalysis(
            duration_sec=180,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        ),
        script_version=1,
    )
    assert "已更新脚本版本 1" in notice
    assert "生产字段已齐" in notice
    assert "在右侧查看脚本" in notice
    assert "确认卡" not in notice
    assert "视频时长" not in notice


def test_confirmation_cost_summary_reads_workspace_script_fields() -> None:
    summary = _confirmation_cost_summary(
        "confirm_script_creative",
        0,
        workspace_payload={
            "latest_input": "做一条180秒智能空调种草片，主打舒适送风",
            "script": {
                "duration_sec": 180,
                "missing_requirements": ["视频画幅", "结尾行动引导"],
                "content": "脚本",
            },
            "script_pipeline": {
                "start": {
                    "content": "# 选题\n\n智能空调舒适体验\n时长：180秒\n画幅：待用户确认\n",
                }
            },
        },
    )
    assert "已识别时长：180秒" in summary
    assert "视频画幅" in summary
    assert "结尾行动引导" in summary
    assert "视频时长" not in summary.split(CLARIFY_MARKER)[-1]


def test_creative_confirm_cost_summary_with_explicit_missing() -> None:
    summary = creative_confirm_cost_summary(
        preview="选题摘要",
        missing=["视频画幅"],
        duration_sec=60,
    )
    assert "已识别时长：60秒" in summary
    assert "视频画幅" in summary
    assert "结尾行动引导" not in summary
