"""PixelFlow v2 采集表单和创意方向纯逻辑。

这个模块对应设计文档里的 FormSchemaSkill、FormCompletenessSkill 和
CreativeDirectionSkill 的本地确定性实现。它不访问数据库、不调用 LLM、不调
content-app，方便先把前后端契约稳定下来；后续接入 LLM 时可以替换
``draft_creative_directions`` 的内部实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CreationIntent = Literal["video", "image"]

VIDEO_CATEGORIES = ["美妆护肤", "食品饮料", "数码3C", "服饰鞋包", "家居日用", "保健养生", "其他品类"]
VIDEO_GOALS = ["直接购买", "品牌曝光", "种草引流", "引流直播间"]
IMAGE_TYPES = ["商品广告图", "人物/场景图", "海报/封面图", "插画/概念图", "背景/素材图", "其他"]
IMAGE_USAGES = ["广告投放", "社媒发布", "内容封面", "详情页配图", "活动宣传", "内部展示", "其他用途"]
IMAGE_STYLES = ["真实摄影", "高级质感", "简洁干净", "小红书风", "科技感", "插画风", "自由发挥"]
IMAGE_SIZES = ["1:1", "16:9", "9:16", "自动适配"]


@dataclass(frozen=True)
class FormField:
    id: str
    label: str
    type: Literal["text", "radio_group"]
    required: bool = True
    placeholder: str = ""
    options: list[str] = field(default_factory=list)
    default_value: str = ""
    source: Literal["system", "user", "llm"] = "system"
    confidence: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "placeholder": self.placeholder,
            "options": self.options,
            "default_value": self.default_value,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class FormSchema:
    form_id: str
    title: str
    output_type: CreationIntent
    fields: list[FormField]

    def to_dict(self) -> dict[str, Any]:
        return {
            "form_id": self.form_id,
            "title": self.title,
            "output_type": self.output_type,
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True)
class FormValidationResult:
    intent: CreationIntent
    schema: FormSchema
    values: dict[str, Any]
    missing_fields: list[str]
    intake_rounds: int
    is_complete: bool
    terminated: bool
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "schema": self.schema.to_dict(),
            "values": self.values,
            "missing_fields": self.missing_fields,
            "intake_rounds": self.intake_rounds,
            "is_complete": self.is_complete,
            "terminated": self.terminated,
            "message": self.message,
        }


@dataclass(frozen=True)
class CreativeDirection:
    direction_id: str
    title: str
    description: str
    recommended: bool = False
    tags: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction_id": self.direction_id,
            "title": self.title,
            "description": self.description,
            "recommended": self.recommended,
            "tags": self.tags,
            "data": self.data,
        }


def get_form_schema(intent: CreationIntent) -> FormSchema:
    if intent == "video":
        return FormSchema(
            form_id="ad_short_video_intake",
            title="AD投放短视频需求收集",
            output_type="video",
            fields=[
                FormField(id="product_info", label="请提供你要投放的产品信息", type="text", placeholder="苹果什么什么PRO"),
                FormField(id="product_category", label="产品品类", type="radio_group", options=VIDEO_CATEGORIES),
                FormField(id="target_audience", label="目标人群", type="text", placeholder="25-35"),
                FormField(id="conversion_goal", label="转化目标", type="radio_group", options=VIDEO_GOALS),
            ],
        )
    return FormSchema(
        form_id="image_generation_intake",
        title="图片生成需求收集",
        output_type="image",
        fields=[
            FormField(id="image_goal", label="你想生成什么图片？", type="text", placeholder="例如：科技感海报、办公室场景图、小红书封面、人物插画"),
            FormField(id="image_type", label="图片类型", type="radio_group", options=IMAGE_TYPES),
            FormField(id="image_usage", label="图片用途", type="radio_group", options=IMAGE_USAGES),
            FormField(id="image_style", label="图片风格", type="radio_group", options=IMAGE_STYLES),
            FormField(id="image_size", label="图片尺寸", type="radio_group", options=IMAGE_SIZES),
        ],
    )


def validate_form(intent: CreationIntent, values: dict[str, Any] | None, intake_rounds: int = 0) -> FormValidationResult:
    schema = get_form_schema(intent)
    normalized = _normalize_values(schema, values or {})
    missing = [field.id for field in schema.fields if field.required and not _has(normalized.get(field.id))]
    is_complete = not missing
    terminated = bool(missing and intake_rounds >= 3)
    if is_complete:
        message = "表单信息已完整，可以生成创意方向。"
    elif terminated:
        message = f"最多确认 3 次后仍缺少关键数据：{', '.join(missing)}。"
    else:
        message = f"还需要补充：{', '.join(missing)}。"
    return FormValidationResult(
        intent=intent,
        schema=schema,
        values=normalized,
        missing_fields=missing,
        intake_rounds=intake_rounds,
        is_complete=is_complete,
        terminated=terminated,
        message=message,
    )


def draft_creative_directions(
    intent: CreationIntent,
    values: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
) -> list[CreativeDirection]:
    subject = _subject_for(intent, values)
    audience = str(values.get("target_audience") or values.get("image_usage") or "目标用户")
    goal = str(values.get("conversion_goal") or values.get("image_goal") or "完成转化")
    anchors = product_creative_profile.get("visual_anchor_keywords") if product_creative_profile else None
    anchor_text = "、".join([str(item) for item in anchors[:3]]) if isinstance(anchors, list) and anchors else "产品质感、真实使用、转化动作"
    if intent == "video":
        return [
            CreativeDirection(
                direction_id="direction_1",
                title="痛点开场 + 产品解决",
                description=f"围绕「{subject}」先抛出 {audience} 的真实痛点，再用产品能力完成解决，结尾导向「{goal}」。",
                recommended=True,
                tags=["强转化", "前三秒钩子", "痛点解决"],
                data={"structure": "pain_solution", "visual_anchor": anchor_text},
            ),
            CreativeDirection(
                direction_id="direction_2",
                title="场景种草 + 生活方式",
                description=f"把「{subject}」放进高频生活场景，用 {anchor_text} 建立记忆点，降低广告感并自然带出「{goal}」。",
                tags=["种草", "场景化", "生活方式"],
                data={"structure": "lifestyle_seeding", "visual_anchor": anchor_text},
            ),
            CreativeDirection(
                direction_id="direction_3",
                title="对比反差 + 结果证明",
                description=f"通过使用前后或竞品心智反差突出「{subject}」，让用户快速理解为什么现在应该执行「{goal}」。",
                tags=["对比", "结果证明", "卖点强化"],
                data={"structure": "contrast_result", "visual_anchor": anchor_text},
            ),
        ]
    return [
        CreativeDirection(
            direction_id="direction_1",
            title="核心卖点海报",
            description=f"围绕「{subject}」制作主视觉，把 {anchor_text} 作为画面锚点，适合快速传达「{goal}」。",
            recommended=True,
            tags=["主视觉", "卖点明确", "可投放"],
            data={"structure": "key_visual", "visual_anchor": anchor_text},
        ),
        CreativeDirection(
            direction_id="direction_2",
            title="真实场景氛围图",
            description=f"把「{subject}」放在真实使用场景中，用环境和人物关系增强可信度，适合「{goal}」。",
            tags=["真实场景", "氛围", "信任感"],
            data={"structure": "scene_photo", "visual_anchor": anchor_text},
        ),
        CreativeDirection(
            direction_id="direction_3",
            title="内容平台封面",
            description=f"用更强标题感和构图焦点呈现「{subject}」，提升点击欲望，并服务「{goal}」。",
            tags=["封面", "点击率", "社媒"],
            data={"structure": "social_cover", "visual_anchor": anchor_text},
        ),
    ]


def _normalize_values(schema: FormSchema, values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for form_field in schema.fields:
        raw = values.get(form_field.id, form_field.default_value)
        normalized[form_field.id] = raw.strip() if isinstance(raw, str) else raw
    if schema.output_type == "image" and _has(values.get("image_count")):
        normalized["image_count"] = _normalize_image_count(values.get("image_count"))
    return normalized


def _normalize_image_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 1
    return max(1, min(10, number))


def _has(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _subject_for(intent: CreationIntent, values: dict[str, Any]) -> str:
    if intent == "video":
        return str(values.get("product_info") or "产品")
    return str(values.get("image_goal") or "图片创作目标")
