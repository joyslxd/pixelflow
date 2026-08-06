"""规则优先、LLM兜底的服务端跨业务对话路由。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence

from pixelflow.intake.llm import IntentRecognitionResult, recognize_intent_with_llm

from .context import ContextBudgetPolicyProvider, estimate_context_tokens
from .contracts.routing import (
    RouteDecision,
    RouteDecisionSource,
    RouteIntent,
    RouteMaterial,
    RouteRequest,
)

IntentClassifier = Callable[
    [str, list[dict[str, object]]],
    Awaitable[IntentRecognitionResult],
]

_VIDEO_ANALYSIS = re.compile(r"视频分析|拆解.{0,8}视频|分析.{0,8}(?:这个|该|参考)?视频", re.IGNORECASE)
_VIDEO = re.compile(r"图生视频|文生视频|生成.{0,24}(?:视频|短片|影片)|制作.{0,24}(?:视频|短片|影片)", re.IGNORECASE)
_PPT = re.compile(r"PPT|演示文稿|幻灯片", re.IGNORECASE)
_IMAGE = re.compile(r"商品主图|海报|图片编辑|图像编辑|生成.{0,8}(?:图片|图像)", re.IGNORECASE)


class ConversationRouteService:
    """在任何业务副作用前形成公开、可持久化的路由决定。"""

    def __init__(
        self,
        *,
        llm_classifier: IntentClassifier = recognize_intent_with_llm,
        llm_timeout_seconds: float = 5.0,
        budget_policy_provider: ContextBudgetPolicyProvider | None = None,
    ) -> None:
        if llm_timeout_seconds <= 0:
            raise ValueError("路由模型超时必须大于零")
        self._llm_classifier = llm_classifier
        self._llm_timeout_seconds = llm_timeout_seconds
        self._budget_policy_provider = (
            budget_policy_provider or ContextBudgetPolicyProvider()
        )

    async def route(
        self,
        *,
        content: str,
        materials: Sequence[Mapping[str, object]] = (),
    ) -> RouteDecision:
        request = RouteRequest(
            content=content,
            materials=tuple(_safe_material(item) for item in materials),
        )
        policy = self._budget_policy_provider.policy_for(
            "conversation_route",
        )
        usable_input_tokens = (
            policy.effective_context_cap_tokens
            - policy.output_reserve_tokens
            - policy.safety_reserve_tokens
        )
        estimated_input_tokens = estimate_context_tokens(
            request.model_dump(mode="python"),
        )
        if estimated_input_tokens > usable_input_tokens:
            return _unknown("request_too_large")
        rule_decision = _deterministic_decision(request.content)
        if rule_decision is not None:
            return rule_decision
        try:
            result = await asyncio.wait_for(
                self._llm_classifier(
                    request.content,
                    [material.model_dump(exclude_none=True) for material in request.materials],
                ),
                timeout=self._llm_timeout_seconds,
            )
        except Exception:  # noqa: BLE001
            return _unknown("classifier_unavailable")
        intent = _route_intent(result.intent)
        if intent is RouteIntent.UNKNOWN or result.confidence < 0.5:
            return _unknown("ambiguous_request")
        return RouteDecision(
            intent=intent,
            confidence=result.confidence,
            decision_source=RouteDecisionSource.LLM,
            reason_code="llm_classified",
            requires_clarification=False,
        )


def _deterministic_decision(content: str) -> RouteDecision | None:
    if _VIDEO_ANALYSIS.search(content):
        return _explicit(RouteIntent.VIDEO_ANALYSIS, "explicit_video_analysis")
    matches = [
        (RouteIntent.VIDEO, "explicit_video_request", bool(_VIDEO.search(content))),
        (RouteIntent.PPT, "explicit_ppt_request", bool(_PPT.search(content))),
        (RouteIntent.IMAGE, "explicit_image_request", bool(_IMAGE.search(content))),
    ]
    selected = [(intent, reason) for intent, reason, matched in matches if matched]
    if len(selected) != 1:
        return None
    intent, reason = selected[0]
    return _explicit(intent, reason)


def _explicit(intent: RouteIntent, reason_code: str) -> RouteDecision:
    return RouteDecision(
        intent=intent,
        confidence=1,
        decision_source=RouteDecisionSource.RULE,
        reason_code=reason_code,
        requires_clarification=False,
    )


def _unknown(reason_code: str) -> RouteDecision:
    return RouteDecision(
        intent=RouteIntent.UNKNOWN,
        confidence=0,
        decision_source=RouteDecisionSource.FALLBACK,
        reason_code=reason_code,
        requires_clarification=True,
    )


def _route_intent(value: str) -> RouteIntent:
    try:
        return RouteIntent(value)
    except ValueError:
        return RouteIntent.UNKNOWN


def _safe_material(material: Mapping[str, object]) -> RouteMaterial:
    def text(*keys: str) -> str | None:
        for key in keys:
            value = material.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    return RouteMaterial(
        artifact_ref=text("artifact_ref", "asset_ref"),
        media_type=text("media_type", "type"),
        filename=text("filename", "name"),
        mime_type=text("mime_type", "mimeType"),
    )
