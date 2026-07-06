"""Scene-level semantic QC for generated video segments.

This module is the semantic judge for "does this generated segment actually
match the storyboard contract?". It intentionally uses structured LLM output
instead of trying to enumerate every possible off-topic product with keywords.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any, Literal, TypedDict

SCENE_SEMANTIC_LLM_MODEL_NAME = "deepseek-v4-pro"
ModelFactory = Callable[..., Any]


class SceneSemanticInput(TypedDict):
    scene_id: str
    scene_index: int | None
    scene_contract_text: str
    observed_text: str


class SceneSemanticResult(TypedDict, total=False):
    scene_id: str
    passed: bool
    category: Literal["plan_consistency", "storyboard_coverage", "product_consistency"]
    severity: Literal["major", "minor", "info"]
    message: str
    expected: str
    observed: str
    suggestion: str


async def evaluate_scene_semantic_contracts(
    *,
    global_contract_text: str,
    items: list[SceneSemanticInput],
    model_name: str = SCENE_SEMANTIC_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> list[SceneSemanticResult]:
    """Ask the project LLM to compare each scene contract with observed video text."""
    valid_items = [
        item
        for item in items
        if item.get("scene_id") and item.get("scene_contract_text") and item.get("observed_text")
    ]
    if not global_contract_text.strip() or not valid_items:
        return []
    try:
        payload = await asyncio.to_thread(
            _invoke_json_model,
            _scene_semantic_prompt(global_contract_text, valid_items),
            model_name,
            model_factory or _default_model_factory,
        )
    except Exception:
        return []
    return _validate_scene_semantic_payload(payload, {item["scene_id"] for item in valid_items})


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
        decoder = json.JSONDecoder()
        payload, _end = decoder.raw_decode(text[min(start_candidates) :])
        return payload


def _validate_scene_semantic_payload(payload: Any, valid_scene_ids: set[str]) -> list[SceneSemanticResult]:
    raw_items = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    results: list[SceneSemanticResult] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        scene_id = str(raw.get("scene_id") or "").strip()
        if scene_id not in valid_scene_ids or scene_id in seen:
            continue
        seen.add(scene_id)
        passed = bool(raw.get("passed"))
        category = _normalize_category(raw.get("category"))
        severity = _normalize_severity(raw.get("severity"))
        results.append(
            {
                "scene_id": scene_id,
                "passed": passed,
                "category": category,
                "severity": "info" if passed else severity,
                "message": str(raw.get("message") or ""),
                "expected": str(raw.get("expected") or ""),
                "observed": str(raw.get("observed") or ""),
                "suggestion": str(raw.get("suggestion") or ""),
            }
        )
    return results


def _normalize_category(value: Any) -> Literal["plan_consistency", "storyboard_coverage", "product_consistency"]:
    normalized = str(value or "").strip()
    if normalized in {"plan_consistency", "storyboard_coverage", "product_consistency"}:
        return normalized  # type: ignore[return-value]
    return "product_consistency"


def _normalize_severity(value: Any) -> Literal["major", "minor", "info"]:
    normalized = str(value or "").strip()
    if normalized in {"major", "minor", "info"}:
        return normalized  # type: ignore[return-value]
    return "major"


def _scene_semantic_prompt(global_contract_text: str, items: list[SceneSemanticInput]) -> str:
    scenes_json = json.dumps(items, ensure_ascii=False, indent=2)
    return f"""你是 PixelFlow 视频综合质检的逐分镜语义审查模块。
请根据“原始全局方案合同”和每个分镜的“分镜合同/实际视频拆解文本”，判断每个分镜是否真的生成对了。

原始全局方案合同：
{global_contract_text[:1800]}

待审查分镜 JSON：
{scenes_json}

判断标准：
1. 如果实际视频主体、商品、场景或动作明显偏离原始产品/分镜合同，passed=false。
2. 如果旁白/卖点仍在说原产品，但画面主体变成其他产品，passed=false，category=product_consistency，severity=major。
3. 如果只是镜头角度、光线、细节表达略有差异，但产品主体和核心卖点一致，passed=true。
4. 不要因为画面里出现手机作为使用场景/连接设备就误判；只有手机、口红、电饭煲等变成主角并替代原产品时才判失败。
5. 必须逐个返回所有输入 scene_id；只返回真实存在的 scene_id。

只返回 JSON，不要解释，不要 Markdown：
[
  {{
    "scene_id": "scene-1",
    "passed": true,
    "category": "product_consistency|plan_consistency|storyboard_coverage",
    "severity": "major|minor|info",
    "message": "如果失败，用一句话说明问题；通过则为空字符串",
    "expected": "该分镜应符合的产品/画面合同摘要",
    "observed": "实际视频拆解文本摘要",
    "suggestion": "如果失败，说明只重生成该分镜时应恢复什么；通过则为空字符串"
  }}
]
"""
