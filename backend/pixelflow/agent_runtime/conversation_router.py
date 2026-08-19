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
_VIDEO = re.compile(
    r"图生视频|文生视频|"
    r"(?:创意|带货|商品|电商|产品|品牌|营销)(?:短)?视频|"
    r"(?:生成|制作|做|拍|出|创作|拍摄|创建).{0,36}"
    r"(?:视频|短片|影片|广告片|宣传片|品牌片|TVC|tvc|"
    r"(?:\d+(?:\.\d+)?\s*(?:s|秒|S|分钟|min)\s*)?广告)|"
    r"(?:视频|短片|影片).{0,12}(?:广告|成片)|"
    r"(?:广告片|宣传片|品牌片|(?:\d+(?:\.\d+)?\s*(?:s|秒|S|分钟|min)\s*)广告)|"
    r"(?:一分钟|60\s*s|60\s*秒)\s*广告",
    re.IGNORECASE,
)
_PPT = re.compile(r"PPT|演示文稿|幻灯片", re.IGNORECASE)
_IMAGE = re.compile(
    r"商品主图|海报|图片编辑|图像编辑|生成.{0,8}(?:图片|图像)|"
    r"(?:做|制作|出|创建).{0,8}(?:海报|主图|图片|图像)",
    re.IGNORECASE,
)
_CLARIFY_VIDEO = re.compile(
    r"^(?:请)?(?:帮我)?(?:我要)?"
    r"(?:创建|做|生成|制作)?"
    r"(?:一个|一条|一支)?"
    r"(?:带货)?"
    r"(?:视频|广告视频|视频广告|带货视频)"
    r"(?:吧)?$",
    re.IGNORECASE,
)
_CLARIFY_IMAGE = re.compile(
    r"^(?:请)?(?:帮我)?(?:我要)?(?:创建|做|生成|制作)?(?:一张|一个)?(?:图片|图像|海报)(?:吧)?$",
    re.IGNORECASE,
)
_CLARIFY_PPT = re.compile(
    r"^(?:请)?(?:帮我)?(?:我要)?(?:创建|做|生成|制作)?(?:一份|一个)?(?:PPT|演示文稿|幻灯片)(?:吧)?$",
    re.IGNORECASE,
)


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
        rule_decision = _deterministic_decision(request.content)
        if rule_decision is not None:
            return rule_decision
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
    clarify = content.strip()
    if len(clarify) <= 36:
        if _CLARIFY_VIDEO.fullmatch(clarify):
            return _explicit(RouteIntent.VIDEO, "explicit_video_request")
        if _CLARIFY_PPT.fullmatch(clarify):
            return _explicit(RouteIntent.PPT, "explicit_ppt_request")
        if _CLARIFY_IMAGE.fullmatch(clarify):
            return _explicit(RouteIntent.IMAGE, "explicit_image_request")
    # 用户直接粘贴可拍成稿：按视频意图进入，避免先 unknown 再澄清丢上下文。
    from pixelflow.video_agent.entrypoint import looks_like_complete_shooting_script

    if looks_like_complete_shooting_script(content):
        return _explicit(RouteIntent.VIDEO, "complete_script_payload")
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
