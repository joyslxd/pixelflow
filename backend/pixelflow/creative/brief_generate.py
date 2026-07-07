"""brief_generate — 纯 Claude shot-planning (PRD §9.4).

Turns product info + video params + a creative direction into an authoritative
:class:`Brief` via the harness's config-driven chat model with structured
output. Three creative modes per the PRD:

* ``original``  — pure original创意 (no reference)
* ``reference`` — single reference video as structural inspiration
* ``attribution`` — multi-reference attribution / remix

The model is selected through ``deerflow.models.create_chat_model`` so model
choice, credentials and tracing stay config-driven (no hardcoded SDK).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from langchain_core.messages import BaseMessage

from deerflow.models import create_chat_model

from .models import Brief

logger = logging.getLogger(__name__)

CreativeMode = Literal["original", "reference", "attribution"]

_SYSTEM_PROMPT = """你是资深电商短视频导演与分镜策划。根据给定的商品信息、视频参数和创意方向，
产出一个结构化的分镜 Brief（对齐 PRD §9.4）。要求：

1. shots 必须遵守硬约束：第一镜是 hook（≤3s）、最后一镜是 cta；各镜时长之和≈目标总时长（±2s）。
2. 每个镜头给出 scene_type（hook/pain_point/solution/demo/social_proof/cta）与
   asset_strategy（use_real_asset/generate_asset/use_reference_structure/mixed）。
3. visual_description 用中文写给用户看；generation_prompt 是给生成模型的英文/结构化提示词，
   绝不能要求“画面生成文字/字幕”（字幕由剪辑阶段叠加）。
4. narration_text ≤50 字，onscreen_text ≤20 字。
5. global_visual 描述跨镜一致的主体、环境、光线、风格与禁止元素。
只输出符合 schema 的结构化数据，不要额外解释。"""


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _build_human_prompt(
    *,
    product_info: dict[str, Any],
    video_params: dict[str, Any],
    creative_direction: str,
    reference_analysis: dict[str, Any] | None,
    creative_mode: CreativeMode,
) -> str:
    parts = [
        f"【创意模式】{creative_mode}",
        f"【商品信息】\n{json.dumps(product_info, ensure_ascii=False, indent=2)}",
        f"【视频参数】\n{json.dumps(video_params, ensure_ascii=False, indent=2)}",
        f"【创意方向】\n{creative_direction or '（无特别要求，自由发挥）'}",
    ]
    if reference_analysis:
        parts.append(f"【参考视频分析结果】\n{json.dumps(reference_analysis, ensure_ascii=False, indent=2)}")
    return "\n\n".join(parts)


def _message_text(message: BaseMessage | Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Brief response JSON must be an object")
    return parsed


async def _brief_generate_via_json_text(model: Any, human: str) -> Brief:
    schema = Brief.model_json_schema()
    prompt = (
        f"{human}\n\n"
        "请严格只输出一个 JSON object，不要输出 Markdown、解释文字或代码块。\n"
        "JSON 必须符合以下 schema，字段名必须完全一致：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    response = await model.ainvoke([("system", _SYSTEM_PROMPT), ("human", prompt)])
    data = _extract_json_object(_message_text(response))
    return Brief.model_validate(data)


async def brief_generate(
    *,
    product_info: dict[str, Any],
    video_params: dict[str, Any],
    creative_direction: str = "",
    reference_analysis: dict[str, Any] | None = None,
    creative_mode: CreativeMode = "original",
) -> Brief:
    """Generate a structured :class:`Brief`. Raises on LLM/config failure.

    The caller (``creative_node``) is responsible for catching failures and
    degrading gracefully so the pipeline stays runnable offline.
    """
    model = create_chat_model(thinking_enabled=False)
    structured = model.with_structured_output(Brief)
    human = _build_human_prompt(
        product_info=product_info,
        video_params=video_params,
        creative_direction=creative_direction,
        reference_analysis=reference_analysis,
        creative_mode=creative_mode,
    )
    logger.info("[pixelflow] brief_generate mode=%s", creative_mode)
    try:
        brief = await structured.ainvoke([("system", _SYSTEM_PROMPT), ("human", human)])
    except Exception as exc:
        message = str(exc)
        if "json_schema" not in message and "response_format" not in message:
            raise
        logger.warning("[pixelflow] structured brief output unsupported, falling back to JSON text parsing: %s", message)
        brief = await _brief_generate_via_json_text(model, human)
    # Backfill the output params from video_params so downstream nodes are exact.
    brief.platform = video_params.get("platform", brief.platform)
    brief.duration_sec = int(video_params.get("duration_sec", brief.duration_sec))
    brief.ratio = video_params.get("ratio", brief.ratio)
    brief.size = video_params.get("size", brief.size)
    return brief
