"""采集阶段标准上下文。

这个模块像 Java 里的领域 DTO + 归一化 Service：把 LLM 的自由输出和前端表单
短字段统一成后续创意方向、plan.md、图片/视频生成都能复用的稳定合同。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

IntakeIntent = Literal["video", "image", "video_analysis", "unknown"]

GENERIC_GOALS = {
    "宣传",
    "宣传图",
    "海报",
    "广告",
    "广告图",
    "展示",
    "推广",
    "产品图",
    "图片",
    "图",
    "视频",
    "宣传视频",
    "短视频",
}


@dataclass(frozen=True)
class IntakeContext:
    source_prompt: str
    intent: IntakeIntent
    product_subject: str = ""
    creation_goal: str = ""
    industry_type: str = "general"
    requested_output_count: int = 1
    form_values: dict[str, Any] = field(default_factory=dict)
    product_creative_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_prompt": self.source_prompt,
            "intent": self.intent,
            "product_subject": self.product_subject,
            "creation_goal": self.creation_goal,
            "industry_type": self.industry_type,
            "requested_output_count": self.requested_output_count,
            "form_values": self.form_values,
            "product_creative_profile": self.product_creative_profile,
        }


def normalize_intake_context(
    *,
    intent: IntakeIntent,
    source_prompt: str,
    extracted: dict[str, Any],
) -> IntakeContext:
    values = dict(extracted.get("values") or {})
    subject = _text(extracted.get("product_subject")) or _subject_from_values(intent, values) or _subject_from_prompt(intent, source_prompt)
    goal = _text(extracted.get("creation_goal")) or _goal_from_values(intent, values)

    if intent == "image":
        goal = _complete_goal(subject, goal, fallback_suffix="图片")
        if goal:
            values["image_goal"] = goal
    elif intent == "video":
        goal = _complete_goal(subject, goal, fallback_suffix="视频")
        if subject:
            values["product_info"] = _text(values.get("product_info")) or subject

    count = _normalize_count(extracted.get("requested_output_count") or values.get("image_count"))
    if intent == "image":
        values["image_count"] = count

    return IntakeContext(
        source_prompt=source_prompt.strip(),
        intent=intent,
        product_subject=subject,
        creation_goal=goal,
        industry_type=_text(extracted.get("industry_type")) or _industry_from_values(values) or "general",
        requested_output_count=count,
        form_values=values,
        product_creative_profile=dict(extracted.get("product_creative_profile") or {}),
    )


def _complete_goal(subject: str, goal: str, *, fallback_suffix: str) -> str:
    if not subject:
        return goal
    if not goal:
        return f"{subject}{fallback_suffix}"
    if _is_generic_goal(goal) or subject not in goal:
        return f"{subject}{goal}"
    return goal


def _subject_from_values(intent: IntakeIntent, values: dict[str, Any]) -> str:
    if intent == "video":
        return _text(values.get("product_info") or values.get("product_name"))
    image_goal = _text(values.get("image_goal"))
    if intent == "image" and image_goal and not _is_generic_goal(image_goal):
        return _strip_goal_suffix(image_goal)
    return ""


def _goal_from_values(intent: IntakeIntent, values: dict[str, Any]) -> str:
    if intent == "image":
        return _text(values.get("image_goal"))
    if intent == "video":
        product = _text(values.get("product_info") or values.get("product_name"))
        return f"{product}视频" if product else ""
    return ""


def _subject_from_prompt(intent: IntakeIntent, source_prompt: str) -> str:
    text = source_prompt.strip()
    if not text or intent not in {"image", "video"}:
        return ""
    target_words = "图片|图|海报|封面|主图|素材图|宣传图|视频|短视频|宣传视频|广告视频"
    match = re.search(rf"(?:生成|做|制作|出|来|给我|帮我)?\s*(?:\d+|[一二两三四五六七八九十]+)?\s*(?:张|个|幅|条|段)?\s*([\u4e00-\u9fa5A-Za-z0-9]+?)\s*(?:的)?(?:{target_words})", text)
    if match:
        candidate = match.group(1)
        candidate = re.sub(r"^(一个|一张|一些|这个|那个)", "", candidate)
        candidate = re.sub(r"(宣传|广告|商品|产品)$", "", candidate)
        return candidate.strip()
    return ""


def _strip_goal_suffix(value: str) -> str:
    stripped = value
    for suffix in ("宣传视频", "广告视频", "短视频", "宣传图", "商品图", "产品图", "海报", "封面", "主图", "图片", "视频", "图"):
        if stripped.endswith(suffix) and len(stripped) > len(suffix):
            return stripped[: -len(suffix)]
    return stripped


def _industry_from_values(values: dict[str, Any]) -> str:
    return _text(values.get("product_category") or values.get("industry_type") or values.get("industry"))


def _is_generic_goal(value: str) -> bool:
    normalized = value.strip()
    return not normalized or normalized in GENERIC_GOALS


def _normalize_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 1
    return max(1, min(10, number))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
