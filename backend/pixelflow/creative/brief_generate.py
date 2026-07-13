"""Brief 生成器：用 LLM 产出结构化分镜方案（PRD §9.4）。

输入商品信息、视频参数、创意方向和可选参考视频分析，输出权威 ``Brief`` DTO。
模型由 harness 的 ``create_chat_model`` 创建，凭证、模型选择、追踪都走配置，
不在业务代码里硬编码 SDK。

当前支持三种创意模式：

* ``original``：无参考视频，完全原创。
* ``reference``：单个参考视频，主要复刻结构和节奏。
* ``attribution``：多个参考视频，做归因融合和混剪式创意。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

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


async def brief_generate(
    *,
    product_info: dict[str, Any],
    video_params: dict[str, Any],
    creative_direction: str = "",
    reference_analysis: dict[str, Any] | None = None,
    creative_mode: CreativeMode = "original",
) -> Brief:
    """生成结构化 ``Brief``。

    如果模型或配置不可用会抛出异常；上层 ``creative_node`` 负责捕获并降级，
    保证离线或配置缺失时整条流水线仍能给出可解释状态。
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
    # 用 video_params 回填输出参数，确保下游节点拿到的是用户最终确认的精确值。
    brief.platform = video_params.get("platform", brief.platform)
    brief.duration_sec = int(video_params.get("duration_sec", brief.duration_sec))
    brief.ratio = video_params.get("ratio", brief.ratio)
    brief.size = video_params.get("size", brief.size)
    return brief


async def _brief_generate_via_json_text(model: Any, human: str) -> Brief:
    response = await model.ainvoke(
        [
            ("system", f"{_SYSTEM_PROMPT}\n只返回 JSON 对象，不要使用 Markdown 代码块。"),
            ("human", human),
        ]
    )
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    raw = str(content).strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return Brief.model_validate(json.loads(raw))
