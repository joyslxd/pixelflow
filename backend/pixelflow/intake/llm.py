"""采集阶段 LLM Skill。

这里对应设计文档里的 IntentRecognitionSkill 和 CreativeDirectionSkill。
它使用项目 profile 中配置的 ``deepseek-v4-pro``，并在模型调用失败、返回非 JSON
或字段不足时降级到本地确定性逻辑，保证前端交互不被一次模型异常中断。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pixelflow.intake.context import normalize_intake_context
from pixelflow.intake.forms import CreationIntent, CreativeDirection, draft_creative_directions, get_form_schema

INTAKE_LLM_MODEL_NAME = "deepseek-v4-pro"
IntakeIntent = Literal["video", "image", "ppt", "video_analysis", "unknown"]


@dataclass(frozen=True)
class IntentRecognitionResult:
    intent: IntakeIntent
    confidence: float = 0
    reason: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    intake_context: dict[str, Any] = field(default_factory=dict)
    llm_used: bool = False
    model_name: str = INTAKE_LLM_MODEL_NAME
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "values": self.values,
            "intake_context": self.intake_context,
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
    """用 LLM 识别图片、视频、PPT、视频分析意图，并抽取可填表字段。"""
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
        filtered_values = _augment_intent_values(intent, _filter_form_values(intent, payload.get("values")), text)
        image_operation = _image_operation_from_payload(intent, payload, filtered_values, text)
        if image_operation:
            filtered_values["image_operation"] = image_operation
        context = normalize_intake_context(
            intent=intent,
            source_prompt=prompt,
            extracted={
                **payload,
                "image_operation": image_operation,
                "requested_output_count": payload.get("requested_output_count") or filtered_values.get("image_count"),
                "values": filtered_values,
            },
        )
        values = dict(context.form_values)
        context_dict = context.to_dict()
        if image_operation:
            values["image_operation"] = image_operation
            context_dict["image_operation"] = image_operation
        _copy_image_param_context(values, context_dict)
        _copy_video_param_context(values, context_dict)
        return IntentRecognitionResult(
            intent=intent,
            confidence=_confidence(payload.get("confidence")),
            reason=str(payload.get("reason") or ""),
            values=values,
            intake_context=context_dict,
            llm_used=True,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001 - LLM boundary must degrade gracefully
        fallback = _fallback_intent(text)
        fallback_values = _augment_intent_values(fallback, {}, text)
        image_operation = _image_operation_from_payload(fallback, {}, fallback_values, text)
        if image_operation:
            fallback_values["image_operation"] = image_operation
        context = normalize_intake_context(
            intent=fallback,
            source_prompt=prompt,
            extracted={"values": fallback_values, "image_operation": image_operation},
        )
        values = dict(context.form_values)
        context_dict = context.to_dict()
        if image_operation:
            values["image_operation"] = image_operation
            context_dict["image_operation"] = image_operation
        _copy_image_param_context(values, context_dict)
        _copy_video_param_context(values, context_dict)
        return IntentRecognitionResult(
            intent=fallback,
            confidence=0.2 if fallback != "unknown" else 0,
            reason="LLM 调用失败，已使用本地兜底规则。",
            values=values,
            intake_context=context_dict,
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
    from pixelflow.tracing import record_trace_event_background

    try:
        model = model_factory(model_name, attach_tracing=False)
    except TypeError:
        model = model_factory(model_name)
    started_at = time.monotonic()
    try:
        response = model.invoke(prompt)
    except Exception as exc:
        record_trace_event_background(
            "llm_call",
            {
                "model": model_name,
                "prompt": prompt,
                "error": str(exc),
                "duration_ms": round((time.monotonic() - started_at) * 1000),
            },
        )
        raise
    content = getattr(response, "content", response)
    record_trace_event_background(
        "llm_call",
        {
            "model": model_name,
            "prompt": prompt,
            "response": str(content or ""),
            "duration_ms": round((time.monotonic() - started_at) * 1000),
        },
    )
    return _parse_json_payload(content)


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
- ppt_generation: 用户要制作 PPT/演示文稿/汇报幻灯片。
- video_analysis: 用户要分析、拆解、对比一个或多个已有视频。
- unknown: 需求不足，无法判断。

如果是 video_generation，可抽取 values 字段：
product_info, product_category, target_audience, conversion_goal,
video_duration_sec, video_ratio, video_model_mode, video_model, image_model,
video_usage, visual_style。
字段规则：
- video_duration_sec 是视频总时长的自然数秒，范围 4-300；没有明确时长时写 30。
- video_ratio 是最终视频画幅，只能优先识别 9:16、16:9、1:1；没有明确要求时写 9:16。
- video_model 是生成分镜视频使用的 Seedance 模型；image_model 是生成角色、场景、道具图片使用的图片模型，两者不能混淆。
- 用户明确说出 Seedance 模型时 video_model_mode=manual，否则 video_model_mode=system_recommended 且 video_model=seedance-2.0。
- 用户没有明确图片模型时 image_model=gpt-image-2。
- video_usage 是品牌宣传、产品介绍、活动预热、新品宣传等用途；visual_style 是电影写实、科技感等视觉风格。

如果是 image_generation，可抽取 values 字段：
image_goal, image_type, image_usage, image_style, image_size, image_quality, image_count。
其中 image_size 表示画面比例/尺寸，如 1:1、9:16、16:9；image_quality 表示清晰度，如 720p、1080p、2K、4K。
image_count 表示用户明确要求生成的图片张数；没有明确数量时不要猜测。
同时必须抽取顶层 image_operation：
- text_to_image: 纯文生图，没有参考图和编辑诉求。
- image_edit: 用户要修改、编辑、换背景、修图、改已有图片。
- reference_image: 用户上传或引用图片作为参考生成新图。
- multi_image_fusion: 用户要把多张图融合成一张图。
判断 image_operation 时要特别注意：
- 只要用户提到“上传的图片/原图/这张图/图中/图片中”等已有图片，并要求“变成/改成/换成/替换/调整/去掉/增加”等局部或整体改动，就必须判为 image_edit。
- 例如“帮我把上传的图片中的路飞衣服变成黄色”属于 image_edit，不属于 text_to_image。
- 只有没有已有图片引用、也没有编辑诉求时，才判为 text_to_image。

如果是 ppt_generation，可抽取 values 字段：
ppt_topic, ppt_style。附件由前端上传，不要编造 attachments。

无论哪种生成任务，都要尽量抽取顶层字段：
- product_subject: 用户真正要创作的产品、人物、活动或内容主体，例如“书包”。
- creation_goal: 完整创作目标，例如“书包宣传图”或“书包宣传视频”；不要只写“宣传图”。
- industry_type: 行业类型，例如“服饰鞋包”“数码3C”；无法判断时写 general。
- requested_output_count: 用户明确要求的产物数量；没有明确数量时写 1。

只返回 JSON，不要解释，不要 Markdown：
{{"intent":"video_generation|image_generation|ppt_generation|video_analysis|unknown","confidence":0.0,"reason":"一句话原因","product_subject":"","creation_goal":"","industry_type":"general","requested_output_count":1,"image_operation":"text_to_image|image_edit|reference_image|multi_image_fusion","values":{{}}}}

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
    if normalized in {"ppt", "ppt_generation", "generate_ppt", "smart_ppt", "presentation", "演示文稿", "生成ppt", "制作ppt", "ppt制作"}:
        return "ppt"
    if normalized in {"video_analysis", "analyze_video", "video_decompose", "视频分析", "视频拆解"}:
        return "video_analysis"
    return "unknown"


def _filter_form_values(intent: IntakeIntent, values: Any) -> dict[str, Any]:
    if intent not in {"video", "image", "ppt"} or not isinstance(values, dict):
        return {}
    schema = get_form_schema(intent)
    allowed = {field.id for field in schema.fields}
    if intent == "image":
        allowed.add("image_quality")
    filtered = {key: value for key, value in values.items() if key in allowed and _has(value)}
    if intent == "image":
        image_count = _normalize_image_count(values.get("image_count"))
        if image_count:
            filtered["image_count"] = image_count
    return filtered


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
    video_generation_words = ("生成", "制作", "做", "编辑", "修改", "延伸", "延长", "合并")
    image_hints = (
        "图片",
        "图像",
        "生图",
        "文生图",
        "图生图",
        "改图",
        "修图",
        "海报",
        "封面",
        "主图",
        "配图",
        "素材图",
        "参考图",
        "融合成一张图",
        "多图融合",
        "换背景",
        "篮球图",
        "篮球图片",
        "image",
    )
    ppt_hints = (
        "ppt",
        "演示文稿",
        "幻灯片",
        "汇报材料",
        "汇报ppt",
        "路演ppt",
        "做一份汇报",
        "制作汇报",
        "智能ppt",
        "powerpoint",
        "presentation",
    )
    video_hints = (
        "生成视频",
        "宣传视频",
        "短视频",
        "带货视频",
        "广告视频",
        "文生视频",
        "图生视频",
        "图片生成视频",
        "首帧",
        "首尾帧",
        "尾帧",
        "参考生成视频",
        "全能参考",
        "编辑视频",
        "视频编辑",
        "延伸视频",
        "延长视频",
        "分镜",
        "video",
    )
    if any(hint in lowered for hint in analysis_hints) or ("视频" in lowered and any(word in lowered for word in video_analysis_words)):
        return "video_analysis"
    if any(hint in lowered for hint in ppt_hints):
        return "ppt"
    if any(hint in lowered for hint in video_hints):
        return "video"
    if "视频" in lowered and any(word in lowered for word in video_generation_words):
        return "video"
    if any(hint in lowered for hint in image_hints):
        return "image"
    return "unknown"


def _augment_intent_values(intent: IntakeIntent, values: dict[str, Any], text: str) -> dict[str, Any]:
    if intent == "ppt" and not values.get("ppt_topic"):
        topic = _extract_ppt_topic(text)
        return {**values, "ppt_topic": topic} if topic else values
    if intent == "video":
        return _augment_video_intent_values(values, text)
    if intent != "image":
        return values
    enriched = dict(values)
    if not enriched.get("image_size"):
        image_size = _extract_image_ratio(text)
        if image_size:
            enriched["image_size"] = image_size
    if not enriched.get("image_quality"):
        image_quality = _extract_image_quality(text)
        if image_quality:
            enriched["image_quality"] = image_quality
    if _normalize_image_count(enriched.get("image_count")):
        return enriched
    image_count = _extract_image_count(text)
    if not image_count:
        return enriched
    return {**enriched, "image_count": image_count}


def _augment_video_intent_values(values: dict[str, Any], text: str) -> dict[str, Any]:
    enriched = dict(values)
    explicit_video_model = _extract_video_model(text)
    explicit_image_model = _extract_requested_image_model(text)

    if not _has(enriched.get("video_duration_sec")):
        enriched["video_duration_sec"] = _extract_video_duration(text) or 30
    if not _has(enriched.get("video_ratio")):
        enriched["video_ratio"] = _extract_image_ratio(text) or "9:16"
    if not _has(enriched.get("video_model")):
        enriched["video_model"] = explicit_video_model or "seedance-2.0"
    if not _has(enriched.get("video_model_mode")):
        enriched["video_model_mode"] = "manual" if explicit_video_model else "system_recommended"
    if not _has(enriched.get("image_model")):
        enriched["image_model"] = explicit_image_model or "gpt-image-2"
    if not _has(enriched.get("video_usage")):
        enriched["video_usage"] = _extract_video_usage(text) or "宣传片"
    if not _has(enriched.get("visual_style")):
        visual_style = _extract_visual_style(text)
        if visual_style:
            enriched["visual_style"] = visual_style
    return enriched


def _image_operation_from_payload(intent: IntakeIntent, payload: dict[str, Any], values: dict[str, Any], text: str) -> str:
    if intent != "image":
        return ""
    operation = _normalize_image_operation(payload.get("image_operation") or payload.get("operation") or values.get("image_operation") or values.get("operation"))
    inferred = _infer_image_operation(text)
    if inferred in {"image_edit", "multi_image_fusion"} and operation in {"", "text_to_image", "reference_image"}:
        return inferred
    return operation or inferred


def _normalize_image_operation(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "text_to_image": "text_to_image",
        "text2image": "text_to_image",
        "txt2img": "text_to_image",
        "文生图": "text_to_image",
        "image_edit": "image_edit",
        "edit": "image_edit",
        "imageedit": "image_edit",
        "图像编辑": "image_edit",
        "图片编辑": "image_edit",
        "改图": "image_edit",
        "修图": "image_edit",
        "reference_image": "reference_image",
        "multi_reference_image_generation": "reference_image",
        "reference": "reference_image",
        "参考图": "reference_image",
        "参考生成": "reference_image",
        "multi_image_fusion": "multi_image_fusion",
        "fusion": "multi_image_fusion",
        "融合": "multi_image_fusion",
        "多图融合": "multi_image_fusion",
    }
    return aliases.get(normalized, "")


def _infer_image_operation(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ["融合", "合成一张", "多图合成", "multi_image_fusion", "fusion"]):
        return "multi_image_fusion"
    if _references_existing_image(lowered) and _has_image_edit_action(lowered):
        return "image_edit"
    if any(keyword in lowered for keyword in ["编辑", "修改", "改成", "改为", "变成", "变为", "改图", "修图", "换背景", "替换背景", "去水印", "抠图", "image_edit", "edit"]):
        return "image_edit"
    if any(keyword in lowered for keyword in ["参考", "基于这张", "按照这张", "类似这张", "图生图", "reference"]):
        return "reference_image"
    return "text_to_image"


def _references_existing_image(text: str) -> bool:
    return any(
        keyword in text
        for keyword in [
            "上传的图片",
            "上传图片",
            "上传的图",
            "上传图",
            "这张图片",
            "这张图",
            "这幅图",
            "这张照片",
            "原图",
            "当前图片",
            "当前图",
            "图片中",
            "图中",
            "照片中",
            "素材图",
            "参考图",
        ]
    )


def _has_image_edit_action(text: str) -> bool:
    return any(
        keyword in text
        for keyword in [
            "变成",
            "变为",
            "变黄",
            "变红",
            "变蓝",
            "变白",
            "变黑",
            "改成",
            "改为",
            "换成",
            "换为",
            "替换",
            "修改",
            "调整",
            "调成",
            "去掉",
            "删除",
            "移除",
            "增加",
            "添加",
            "换背景",
            "改背景",
            "换色",
            "改色",
            "上色",
            "修复",
            "修图",
            "抠图",
        ]
    )


def _extract_ppt_topic(text: str) -> str:
    normalized = re.sub(r"\s+", "", text.strip())
    if not normalized:
        return ""
    patterns = [
        r"(?:帮我|给我|请|需要)?(?:做|制作|生成|输出|写)?(?:一份|一个|套)?(.{2,40}?)(?:PPT|ppt|演示文稿|幻灯片)",
        r"(?:帮我|给我|请|需要)?(?:做|制作|生成|输出|写)?(?:一份|一个|套)?(.{2,40}?)(?:汇报材料|汇报)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        topic = match.group(1)
        topic = re.sub(r"^(关于|一个|一份|这个|那个)", "", topic)
        topic = re.sub(r"(的|得)$", "", topic)
        if topic:
            if "PPT" not in topic.upper() and "汇报" not in topic:
                return f"{topic}PPT"
            return topic
    return normalized[:40]


def _extract_video_duration(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,3})\s*(?:秒|s(?:ec(?:ond)?s?)?)(?![A-Za-z])", text, flags=re.IGNORECASE)
    if not match:
        return None
    duration = int(match.group(1))
    return duration if 4 <= duration <= 300 else None


def _extract_video_model(text: str) -> str:
    match = re.search(r"\bseedance[\s_-]*(\d+(?:\.\d+)?(?:[\s_-]*(?:pro|lite|mini))?)\b", text, flags=re.IGNORECASE)
    if not match:
        return ""
    suffix = re.sub(r"[\s_]+", "-", match.group(1).lower())
    return f"seedance-{suffix}"


def _extract_requested_image_model(text: str) -> str:
    patterns = (
        r"\bgpt[\s_-]*image[\s_-]*(\d+(?:\.\d+)?)\b",
        r"\bseeddream[\s_-]*(\d+(?:\.\d+)?)\b",
        r"\bnano[\s_-]*banana[\s_-]*(pro|\d+(?:\.\d+)?)\b",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        suffix = match.group(1).lower()
        prefixes = ("gpt-image", "seeddream", "nanobanana")
        return f"{prefixes[index]}-{suffix}"
    return ""


def _extract_video_usage(text: str) -> str:
    usage_hints = (
        "新品宣传",
        "品牌宣传",
        "产品介绍",
        "活动预热",
        "广告投放",
        "直播引流",
        "带货",
        "种草",
        "企业宣传",
    )
    return next((hint for hint in usage_hints if hint in text), "")


def _extract_visual_style(text: str) -> str:
    style_hints = (
        "电影写实风",
        "电影写实",
        "电影感写实",
        "电影光影",
        "真实摄影",
        "高级质感",
        "简洁干净",
        "小红书风",
        "科技感",
        "插画风",
        "未来感",
    )
    matched = next((hint for hint in style_hints if hint in text), "")
    if matched:
        return matched
    match = re.search(r"(?:视觉风格|画面风格|风格)[为是：:]?\s*([^，。,.；;]{2,16})", text)
    return match.group(1).strip() if match else ""


def _extract_image_count(text: str) -> int | None:
    normalized = text.lower()
    patterns = [
        r"(?:生成|做|出|制作|来|要|给我)?\s*(\d{1,2})\s*(?:张|幅|个)\s*[^，。,.；;]{0,12}(?:图片|图|海报|封面|主图|素材图)",
        r"(?:生成|做|出|制作|来|要|给我)\s*(\d{1,2})\s*(?:张|幅|个)(?:$|[，。,.；;\s])",
        r"(?:图片|图|海报|封面|主图|素材图)\s*(\d{1,2})\s*(?:张|幅|个)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return _normalize_image_count(match.group(1))
    cn_match = re.search(r"(?:生成|做|出|制作|来|要|给我)?\s*([一二两三四五六七八九十]{1,3})\s*(?:张|幅|个)\s*[^，。,.；;]{0,12}(?:图片|图|海报|封面|主图|素材图)", normalized)
    if cn_match:
        return _normalize_image_count(cn_match.group(1))
    cn_short_match = re.search(r"(?:生成|做|出|制作|来|要|给我)\s*([一二两三四五六七八九十]{1,3})\s*(?:张|幅|个)(?:$|[，。,.；;\s])", normalized)
    if cn_short_match:
        return _normalize_image_count(cn_short_match.group(1))
    return None


def _extract_image_ratio(text: str) -> str:
    match = re.search(r"\b(1\s*:\s*1|9\s*:\s*16|16\s*:\s*9)\b", text, flags=re.IGNORECASE)
    if match:
        left, right = match.group(1).split(":")
        return f"{int(left.strip())}:{int(right.strip())}"
    lowered = text.lower()
    if any(keyword in lowered for keyword in ["竖版", "竖图", "竖屏", "9:16"]):
        return "9:16"
    if any(keyword in lowered for keyword in ["横版", "横图", "横屏", "横幅", "16:9"]):
        return "16:9"
    if any(keyword in lowered for keyword in ["正方形", "方图", "1:1"]):
        return "1:1"
    return ""


def _extract_image_quality(text: str) -> str:
    normalized = text.strip()
    match = re.search(r"(?<![A-Za-z0-9])(720p|1080p|2k|4k)(?![A-Za-z0-9])", normalized, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper().replace("P", "p") if match.group(1).lower().endswith("p") else match.group(1).upper()
    if "高清" in normalized:
        return "1080p"
    return ""


def _copy_image_param_context(values: dict[str, Any], context_dict: dict[str, Any]) -> None:
    for key in ("image_size", "image_quality", "image_model"):
        value = values.get(key)
        if _has(value):
            context_dict[key] = value


def _copy_video_param_context(values: dict[str, Any], context_dict: dict[str, Any]) -> None:
    for key in (
        "video_duration_sec",
        "video_ratio",
        "video_model_mode",
        "video_model",
        "image_model",
        "video_usage",
        "visual_style",
    ):
        value = values.get(key)
        if _has(value):
            context_dict[key] = value


def _normalize_image_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            number = int(text)
        else:
            number = _chinese_number(text)
    else:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
    if number <= 0:
        return None
    return max(1, min(10, number))


def _chinese_number(text: str) -> int:
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[-1], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + (digits.get(right, 0) if right else 0)
    return digits.get(text, 0)


def _has(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return bool(value)
    return True
