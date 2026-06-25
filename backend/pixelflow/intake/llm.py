"""采集阶段 LLM Skill。

这里对应设计文档里的 IntentRecognitionSkill 和 CreativeDirectionSkill。
它使用项目 profile 中配置的 ``deepseek-v4-pro``，并在模型调用失败、返回非 JSON
或字段不足时降级到本地确定性逻辑，保证前端交互不被一次模型异常中断。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pixelflow.intake.forms import CreationIntent, CreativeDirection, draft_creative_directions, get_form_schema

INTAKE_LLM_MODEL_NAME = "deepseek-v4-pro"
IntakeIntent = Literal["video", "image", "video_analysis", "unknown"]


@dataclass(frozen=True)
class IntentRecognitionResult:
    intent: IntakeIntent
    confidence: float = 0
    reason: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    llm_used: bool = False
    model_name: str = INTAKE_LLM_MODEL_NAME
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "values": self.values,
            "llm_used": self.llm_used,
            "model_name": self.model_name,
            "error": self.error,
        }


ModelFactory = Callable[..., Any]


async def recognize_intent_with_llm(
    prompt: str,
    materials: list[dict[str, Any]] | None = None,
    *,
    model_name: str = INTAKE_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> IntentRecognitionResult:
    """用 LLM 识别图片生成、视频生成、视频分析意图，并抽取可填表字段。"""
    text = _combined_text(prompt, materials or [])
    try:
        payload = await asyncio.to_thread(
            _invoke_json_model,
            _intent_prompt(text),
            model_name,
            model_factory or _default_model_factory,
        )
        if not isinstance(payload, dict):
            raise ValueError("intent response must be a JSON object")
        intent = _normalize_intent(payload.get("intent"))
        values = _filter_form_values(intent, payload.get("values"))
        return IntentRecognitionResult(
            intent=intent,
            confidence=_confidence(payload.get("confidence")),
            reason=str(payload.get("reason") or ""),
            values=values,
            llm_used=True,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001 - LLM boundary must degrade gracefully
        fallback = _fallback_intent(text)
        return IntentRecognitionResult(
            intent=fallback,
            confidence=0.2 if fallback != "unknown" else 0,
            reason="LLM 调用失败，已使用本地兜底规则。",
            values={},
            llm_used=False,
            model_name=model_name,
            error=str(exc),
        )


async def draft_creative_directions_with_llm(
    intent: CreationIntent,
    values: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    *,
    model_name: str = INTAKE_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> list[CreativeDirection]:
    """用 LLM 生成 3 个创意方向，失败时降级到本地确定性方向。"""
    try:
        payload = await asyncio.to_thread(
            _invoke_json_model,
            _directions_prompt(intent, values, product_creative_profile or {}),
            model_name,
            model_factory or _default_model_factory,
        )
        raw_directions = payload.get("directions") if isinstance(payload, dict) else payload
        directions = _normalize_directions(raw_directions)
        if len(directions) != 3:
            raise ValueError("creative direction response must contain exactly 3 directions")
        return directions
    except Exception:
        return draft_creative_directions(intent, values, product_creative_profile or {})


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(model_name, attach_tracing=attach_tracing)


def _invoke_json_model(prompt: str, model_name: str, model_factory: ModelFactory) -> Any:
    try:
        model = model_factory(model_name, attach_tracing=False)
    except TypeError:
        model = model_factory(model_name)
    response = model.invoke(prompt)
    return _parse_json_payload(getattr(response, "content", response))


def _parse_json_payload(content: Any) -> Any:
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        decoder = json.JSONDecoder()
        payload, _end = decoder.raw_decode(text[start:])
        return payload


def _intent_prompt(text: str) -> str:
    return f"""你是 PixelFlow 采集 Agent 的意图识别 Skill。
请判断用户请求属于哪一类，并抽取可自动填入表单的字段。

可选 intent:
- video_generation: 用户要生成图片以外的视频/短视频/广告视频。
- image_generation: 用户要生成图片/海报/封面/主图/素材图。
- video_analysis: 用户要分析、拆解、对比一个或多个已有视频。
- unknown: 需求不足，无法判断。

如果是 video_generation，可抽取 values 字段：
product_info, product_category, target_audience, conversion_goal。

如果是 image_generation，可抽取 values 字段：
image_goal, image_type, image_usage, image_style, image_size。

只返回 JSON，不要解释，不要 Markdown：
{{"intent":"video_generation|image_generation|video_analysis|unknown","confidence":0.0,"reason":"一句话原因","values":{{}}}}

用户输入和素材：
{text}
"""


def _directions_prompt(intent: CreationIntent, values: dict[str, Any], product_creative_profile: dict[str, Any]) -> str:
    return f"""你是 PixelFlow 的创意方向生成 Skill。
请基于用户表单和行业创作画像，生成 3 个可直接进入 plan.md 的创意方向。

要求：
1. 必须返回 exactly 3 个方向。
2. 第 1 个推荐，recommended=true；另外两个 recommended=false。
3. 每个方向要有 title、description、tags、data。
4. description 要具体说明开头、画面重点和转化目标，不要空泛。
5. 只返回 JSON，不要 Markdown。

输出格式：
{{"directions":[
  {{"title":"方向名","description":"方向描述","recommended":true,"tags":["标签"],"data":{{"structure":"结构名"}}}},
  {{"title":"方向名","description":"方向描述","recommended":false,"tags":["标签"],"data":{{"structure":"结构名"}}}},
  {{"title":"方向名","description":"方向描述","recommended":false,"tags":["标签"],"data":{{"structure":"结构名"}}}}
]}}

产物类型：{intent}
表单数据：{json.dumps(values, ensure_ascii=False)}
行业创作画像：{json.dumps(product_creative_profile, ensure_ascii=False)}
"""


def _combined_text(prompt: str, materials: list[dict[str, Any]]) -> str:
    pieces = [prompt]
    for material in materials:
        pieces.extend(_collect_strings(material))
    return "\n".join(piece for piece in pieces if piece)


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_collect_strings(item))
        return collected
    if isinstance(value, dict):
        collected: list[str] = []
        for item in value.values():
            collected.extend(_collect_strings(item))
        return collected
    return []


def _normalize_intent(value: Any) -> IntakeIntent:
    normalized = str(value or "").strip().lower()
    if normalized in {"video", "video_generation", "generate_video", "视频生成", "生成视频"}:
        return "video"
    if normalized in {"image", "image_generation", "generate_image", "图片生成", "生成图片"}:
        return "image"
    if normalized in {"video_analysis", "analyze_video", "video_decompose", "视频分析", "视频拆解"}:
        return "video_analysis"
    return "unknown"


def _filter_form_values(intent: IntakeIntent, values: Any) -> dict[str, Any]:
    if intent not in {"video", "image"} or not isinstance(values, dict):
        return {}
    schema = get_form_schema(intent)
    allowed = {field.id for field in schema.fields}
    return {key: value for key, value in values.items() if key in allowed and _has(value)}


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _normalize_directions(value: Any) -> list[CreativeDirection]:
    if not isinstance(value, list):
        return []
    directions: list[CreativeDirection] = []
    for index, item in enumerate(value[:3], start=1):
        if not isinstance(item, dict):
            return []
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        if not title or not description:
            return []
        tags = [str(tag) for tag in item.get("tags", []) if tag] if isinstance(item.get("tags"), list) else []
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        directions.append(
            CreativeDirection(
                direction_id=f"direction_{index}",
                title=title,
                description=description,
                recommended=bool(item.get("recommended")) if index != 1 else True,
                tags=tags,
                data=data,
            )
        )
    return directions


def _fallback_intent(text: str) -> IntakeIntent:
    lowered = text.lower()
    analysis_hints = (
        "视频分析",
        "分析视频",
        "分析这个视频",
        "拆解视频",
        "拆解这个视频",
        "视频拆解",
        "分镜拆解",
        "视频解析",
        "解析视频",
        "analyze video",
    )
    video_analysis_words = ("分析", "拆解", "解析", "对比", "复盘", "看看", "看下", "研究")
    image_hints = ("图片", "海报", "封面", "主图", "配图", "素材图", "image")
    video_hints = ("视频", "短视频", "带货", "广告视频", "分镜", "video")
    if any(hint in lowered for hint in analysis_hints) or ("视频" in lowered and any(word in lowered for word in video_analysis_words)):
        return "video_analysis"
    if any(hint in lowered for hint in image_hints):
        return "image"
    if any(hint in lowered for hint in video_hints):
        return "video"
    return "unknown"


def _has(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return bool(value)
    return True
