"""Resolve which video scenes a revision request should touch.

This module is the backend source of truth for "用户到底要修哪些分镜".
The main path uses the project LLM to understand natural Chinese feedback and
returns structured scene ids. If the model cannot produce a valid result, the
caller gets an unknown scope instead of a guessed full-video regeneration.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

REVISION_SCOPE_LLM_MODEL_NAME = "deepseek-v4-pro"
ScopeAction = Literal["fix_specific", "fix_all", "unknown"]
ScopeConfidence = Literal["high", "medium", "low"]
ModelFactory = Callable[..., Any]


@dataclass(frozen=True)
class RevisionScopeResult:
    target_scene_ids: list[str] = field(default_factory=list)
    excluded_scene_ids: list[str] = field(default_factory=list)
    action: ScopeAction = "unknown"
    confidence: ScopeConfidence = "low"
    llm_used: bool = False
    raw_response: str = ""
    error: str | None = None


async def resolve_revision_scope(
    *,
    feedback: str | None,
    scenes: list[Any],
    model_name: str = REVISION_SCOPE_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> RevisionScopeResult:
    """Resolve target/excluded scene ids from user feedback.

    The LLM path handles natural language variants. On model failure, this
    returns an unknown scope and never interprets vague feedback as full-video
    regeneration.
    """
    text = (feedback or "").strip()
    scene_refs = _scene_refs(scenes)
    if not text or not scene_refs:
        return RevisionScopeResult()
    try:
        raw_content, payload = await asyncio.to_thread(
            _invoke_scope_model,
            _scope_prompt(text, scene_refs),
            model_name,
            model_factory or _default_model_factory,
        )
        if not isinstance(payload, dict):
            raise ValueError("revision scope response must be a JSON object")
        return _validate_scope_result(payload, scene_refs, llm_used=True, raw_response=raw_content)
    except Exception as exc:  # noqa: BLE001 - LLM boundary must degrade gracefully
        return RevisionScopeResult(llm_used=False, error=str(exc))


def _scene_refs(scenes: list[Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, scene in enumerate(scenes, start=1):
        if hasattr(scene, "model_dump"):
            scene = scene.model_dump()
        elif not isinstance(scene, dict) and hasattr(scene, "dict"):
            scene = scene.dict()
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id or scene_id in seen:
            continue
        raw_index = scene.get("scene_index")
        try:
            scene_index = int(raw_index)
        except (TypeError, ValueError):
            scene_index = position
        seen.add(scene_id)
        refs.append(
            {
                "scene_id": scene_id,
                "scene_index": scene_index,
                "summary": _scene_summary(scene),
            }
        )
    return refs


def _scene_summary(scene: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in ("title", "storyline", "prompt", "narration"):
        value = scene.get(key)
        if value:
            pieces.append(str(value))
    shot_description = scene.get("shot_description")
    if isinstance(shot_description, dict):
        text = shot_description.get("text") or shot_description.get("description")
        if text:
            pieces.append(str(text))
    elif shot_description:
        pieces.append(str(shot_description))
    return " / ".join(piece.strip() for piece in pieces if piece and piece.strip())[:240]


def _default_model_factory(model_name: str, *, attach_tracing: bool = False) -> Any:
    from deerflow.models.factory import create_chat_model

    return create_chat_model(model_name, attach_tracing=attach_tracing)


def _invoke_scope_model(prompt: str, model_name: str, model_factory: ModelFactory) -> tuple[str, Any]:
    try:
        model = model_factory(model_name, attach_tracing=False)
    except TypeError:
        model = model_factory(model_name)
    response = model.invoke(prompt)
    raw_content = str(getattr(response, "content", response) or "")
    return raw_content, _parse_json_payload(raw_content)


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


def _validate_scope_result(
    raw: dict[str, Any],
    scene_refs: list[dict[str, Any]],
    *,
    llm_used: bool,
    raw_response: str = "",
) -> RevisionScopeResult:
    valid_scene_ids = {scene["scene_id"] for scene in scene_refs}
    target = _dedupe_scene_ids(raw.get("target_scene_ids"), valid_scene_ids)
    excluded = [scene_id for scene_id in _dedupe_scene_ids(raw.get("excluded_scene_ids"), valid_scene_ids) if scene_id not in target]
    action = _normalize_action(raw.get("action"))
    confidence = _normalize_confidence(raw.get("confidence"))

    if action == "fix_all":
        return RevisionScopeResult(
            target_scene_ids=[],
            excluded_scene_ids=excluded,
            action="fix_all",
            confidence=confidence,
            llm_used=llm_used,
            raw_response=raw_response,
        )
    return RevisionScopeResult(
        target_scene_ids=target,
        excluded_scene_ids=excluded,
        action="fix_specific" if target else "unknown",
        confidence=confidence if target or excluded else "low",
        llm_used=llm_used,
        raw_response=raw_response,
    )


def _dedupe_scene_ids(value: Any, valid_scene_ids: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for item in value:
        scene_id = str(item or "").strip()
        if scene_id not in valid_scene_ids or scene_id in seen:
            continue
        seen.add(scene_id)
        ids.append(scene_id)
    return ids


def _normalize_action(value: Any) -> ScopeAction:
    normalized = str(value or "").strip().lower()
    if normalized in {"fix_specific", "specific", "partial"}:
        return "fix_specific"
    if normalized in {"fix_all", "all"}:
        return "fix_all"
    return "unknown"


def _normalize_confidence(value: Any) -> ScopeConfidence:
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized  # type: ignore[return-value]
    return "low"


def _scope_prompt(feedback: str, scene_refs: list[dict[str, Any]]) -> str:
    scene_lines = "\n".join(
        f"- 第{scene['scene_index']}个分镜 (scene_id: {scene['scene_id']}): {scene['summary'] or '无摘要'}"
        for scene in scene_refs
    )
    return f"""你是 PixelFlow 的分镜修改范围识别模块。
根据用户反馈和当前视频的分镜列表，判断用户要修改哪些分镜、明确不要动哪些分镜。

分镜列表：
{scene_lines}

用户反馈：
{feedback}

输出要求：
1. target_scene_ids: 用户明确要修改/修复/重新生成的分镜 scene_id 列表。
2. excluded_scene_ids: 用户明确说没有问题、不要动、不要重新生成的分镜 scene_id 列表。
3. action:
   - fix_specific: 用户指定了具体要修复的分镜。
   - fix_all: 用户明确要求所有分镜或整条视频都重新生成。
   - unknown: 无法判断具体范围。
4. confidence: high / medium / low。

重要规则：
- 只返回真实存在于分镜列表中的 scene_id，不要编造。
- “第2个分镜和第3个分镜内容错误”“分镜3也不对”“第三段不对”“后两个分镜不对”都表示对应分镜需要修复。
- “第1个分镜没有问题”“第1段不要动”表示对应分镜不要修改。
- 如果用户只是泛泛评价画面质量、没有指定范围，也没有明确全量重做，action 返回 unknown。

只返回 JSON，不要解释，不要 Markdown：
{{"target_scene_ids":[],"excluded_scene_ids":[],"action":"fix_specific|fix_all|unknown","confidence":"high|medium|low"}}
"""
