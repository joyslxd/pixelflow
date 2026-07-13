"""垂类行业创作画像解析。

这个模块像 Java 里的 Strategy Service：已知行业读取本地 Skill 模板，
未知行业调用 LLM 生成同结构画像，LLM 不可用时返回通用电商兜底画像。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

INDUSTRY_PROFILE_LLM_MODEL_NAME = "deepseek-v4-pro"
ModelFactory = Callable[..., Any]

INDUSTRY_PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "public"
    / "borgrise-creative-assistant-v2"
    / "templates"
    / "industry_profile.md"
)

INDUSTRY_ALIASES = {
    "美妆": "beauty",
    "美妆护肤": "beauty",
    "护肤": "beauty",
    "食品": "food",
    "食品饮料": "food",
    "饮料": "food",
    "服饰": "clothing",
    "鞋包": "clothing",
    "服饰鞋包": "clothing",
    "书包": "clothing",
    "双肩包": "clothing",
    "箱包": "clothing",
    "女包": "clothing",
    "男包": "clothing",
    "数码3c": "digital",
    "3c数码": "digital",
    "数码3C": "digital",
    "3C数码": "digital",
    "数码": "digital",
    "手机": "digital",
    "耳机": "digital",
    "智能硬件": "digital",
    "家清": "home_cleaning",
    "家清日用": "home_cleaning",
    "家居日用": "home_cleaning",
    "清洁": "home_cleaning",
    "宠物": "pet",
    "宠物用品": "pet",
}


@dataclass(frozen=True)
class IndustryProfileResult:
    industry: str
    industry_name: str
    profile: dict[str, Any]
    source: Literal["template", "llm", "general_fallback"]
    model_name: str = INDUSTRY_PROFILE_LLM_MODEL_NAME
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "industry": self.industry,
            "industry_name": self.industry_name,
            "product_creative_profile": self.profile,
            "source": self.source,
            "model_name": self.model_name,
            "error": self.error,
        }


async def resolve_industry_profile(
    *,
    industry_type: str,
    source_prompt: str,
    form_values: dict[str, Any],
    materials: list[dict[str, Any]] | None = None,
    model_name: str = INDUSTRY_PROFILE_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> IndustryProfileResult:
    template = _template_profile(industry_type, form_values)
    if template:
        return template
    try:
        payload = await asyncio.to_thread(
            _invoke_json_model,
            _industry_profile_prompt(industry_type, source_prompt, form_values, materials or []),
            model_name,
            model_factory or _default_model_factory,
        )
        return _validated_llm_profile(payload, model_name=model_name)
    except Exception as exc:  # noqa: BLE001 - LLM boundary must keep intake usable
        return _general_profile(source_prompt, form_values, model_name=model_name, error=str(exc))


def _template_profile(industry_type: str, form_values: dict[str, Any]) -> IndustryProfileResult | None:
    industry = _industry_code(industry_type)
    if not industry and _is_generic_industry(industry_type):
        industry = _industry_code(_product_hint(form_values))
    if not industry:
        return None
    template = _load_template_profiles().get(industry)
    if not template:
        return None
    profile = dict(template["product_creative_profile"])
    if "core_message" not in profile:
        target = _target_text("", form_values)
        profile["core_message"] = f"{target} 创作需遵循{template['industry_name']}行业表达规则。" if target else f"遵循{template['industry_name']}行业表达规则。"
    return IndustryProfileResult(
        industry=industry,
        industry_name=str(template.get("industry_name") or industry),
        profile=profile,
        source="template",
    )


@lru_cache(maxsize=1)
def _load_template_profiles() -> dict[str, dict[str, Any]]:
    text = INDUSTRY_PROFILE_PATH.read_text(encoding="utf-8")
    profiles: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        industry = str(payload.get("industry") or "").strip()
        profile = payload.get("product_creative_profile")
        if industry and isinstance(profile, dict):
            profiles[industry] = payload
    return profiles


def _validated_llm_profile(payload: Any, *, model_name: str) -> IndustryProfileResult:
    if not isinstance(payload, dict):
        raise ValueError("industry profile response must be a JSON object")
    industry = str(payload.get("industry") or "general").strip() or "general"
    industry_name = str(payload.get("industry_name") or "通用电商").strip() or "通用电商"
    profile = payload.get("product_creative_profile")
    if not isinstance(profile, dict):
        raise ValueError("industry profile response missing product_creative_profile")
    normalized = _normalize_profile(profile, core_message=str(profile.get("core_message") or "").strip())
    return IndustryProfileResult(
        industry=industry,
        industry_name=industry_name,
        profile=normalized,
        source="llm",
        model_name=model_name,
    )


def _normalize_profile(profile: dict[str, Any], *, core_message: str) -> dict[str, Any]:
    normalized = dict(profile)
    normalized["core_message"] = core_message or "围绕产品主体、使用场景和转化目标组织创作。"
    normalized.setdefault("core_expression_rules", {"must_include": [], "must_avoid": [], "description": ""})
    normalized.setdefault("key_scenes", {})
    normalized.setdefault("product_display_rules", {})
    normalized.setdefault("safety_compliance", {})
    normalized.setdefault("audience_pain_points", [])
    normalized.setdefault("emotional_triggers", [])
    normalized.setdefault("visual_anchor_keywords", [])
    normalized.setdefault("prompt_injection", {"creative_direction_note": "", "plan_note": "", "video_generation_note": ""})
    return normalized


def _general_profile(source_prompt: str, form_values: dict[str, Any], *, model_name: str, error: str | None) -> IndustryProfileResult:
    target = _target_text(source_prompt, form_values) or "产品创作目标"
    profile = _normalize_profile(
        {
            "core_message": f"{target} 需要清晰呈现产品主体、核心卖点、真实使用场景和转化动作。",
            "core_expression_rules": {
                "must_include": ["产品主体", "核心卖点", "真实使用场景", "转化动作"],
                "must_avoid": ["与主体无关的通用宣传", "夸大承诺", "画面水印"],
                "description": "通用电商创作需要先保证用户明确主体不丢失，再根据用途组织视觉焦点和转化信息。",
            },
            "visual_anchor_keywords": ["产品质感", "真实使用", "清晰主体"],
            "prompt_injection": {
                "creative_direction_note": "创意方向必须明确产品主体和使用场景，不得退化为泛泛宣传。",
                "plan_note": "plan.md 必须写出产品主体、用途、视觉锚点和转化收口。",
                "video_generation_note": "Keep the product subject clear and recognizable in every key shot.",
            },
        },
        core_message=f"{target} 需要清晰呈现产品主体、核心卖点、真实使用场景和转化动作。",
    )
    return IndustryProfileResult(
        industry="general",
        industry_name="通用电商",
        profile=profile,
        source="general_fallback",
        model_name=model_name,
        error=error,
    )


def _industry_profile_prompt(industry_type: str, source_prompt: str, form_values: dict[str, Any], materials: list[dict[str, Any]]) -> str:
    return f"""你是 PixelFlow 的垂类行业 Skill。
当前产品没有命中预制行业规范，请基于用户需求生成同结构 product_creative_profile。

要求：
1. 只能补充创作画像，不得改写用户明确主体、数量和用途。
2. 必须返回 industry、industry_name、product_creative_profile。
3. product_creative_profile 必须包含 core_message、core_expression_rules、key_scenes、product_display_rules、safety_compliance、audience_pain_points、emotional_triggers、visual_anchor_keywords、prompt_injection。
4. 只返回 JSON，不要 Markdown。

行业候选：{industry_type or "general"}
原始需求：{source_prompt}
表单数据：{json.dumps(form_values, ensure_ascii=False)}
素材摘要：{json.dumps(materials, ensure_ascii=False)[:2000]}
"""


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
        decoder = json.JSONDecoder()
        payload, _end = decoder.raw_decode(text[min(start_candidates) :])
        return payload


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(model_name, attach_tracing=attach_tracing)


def _industry_code(industry_type: str) -> str:
    normalized = industry_type.strip()
    lowered = normalized.lower()
    if lowered in _load_template_profiles():
        return lowered
    for alias, code in INDUSTRY_ALIASES.items():
        if alias.lower() in lowered or lowered in alias.lower():
            return code
    return ""


def _is_generic_industry(industry_type: str) -> bool:
    normalized = industry_type.strip().lower()
    return normalized in {"", "general", "其他", "其他品类", "未知", "未分类"}


def _product_hint(form_values: dict[str, Any]) -> str:
    return " ".join(
        str(form_values.get(key) or "")
        for key in ("product_info", "product_category", "image_goal", "image_type", "ppt_topic", "ppt_style")
    )


def _target_text(source_prompt: str, form_values: dict[str, Any]) -> str:
    return str(
        form_values.get("image_goal")
        or form_values.get("product_info")
        or form_values.get("ppt_topic")
        or form_values.get("creation_goal")
        or source_prompt
        or ""
    ).strip()
