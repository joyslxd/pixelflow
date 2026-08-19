from __future__ import annotations

import pytest

from pixelflow.agent_runtime.config import ContextBudgetConfig
from pixelflow.agent_runtime.context import ContextBudgetPolicyProvider
from pixelflow.agent_runtime.contracts import RouteDecisionSource, RouteIntent
from pixelflow.agent_runtime.conversation_router import ConversationRouteService
from pixelflow.intake.llm import IntentRecognitionResult


class FakeClassifier:
    def __init__(self, result: IntentRecognitionResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    async def __call__(self, content: str, materials: list[dict[str, object]]):
        self.calls.append((content, materials))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_explicit_video_analysis_wins_without_calling_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="帮我拆解分析这个视频",
    )

    assert decision.intent is RouteIntent.VIDEO_ANALYSIS
    assert decision.decision_source is RouteDecisionSource.RULE
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_conflicting_explicit_intents_use_llm_once() -> None:
    classifier = FakeClassifier(
        IntentRecognitionResult(intent="video", confidence=0.92, llm_used=True)
    )
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="先做一张商品主图，再生成一个商品视频",
        materials=[
            {
                "artifact_ref": "artifact:image-1",
                "mime_type": "image/png",
                "url": "https://example.invalid/a.png?token=secret-value",
                "authorization": "Bearer secret-value",
            }
        ],
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.LLM
    assert len(classifier.calls) == 1
    assert classifier.calls[0][1] == [
        {"artifact_ref": "artifact:image-1", "mime_type": "image/png"}
    ]
    assert "secret-value" not in decision.model_dump_json()


@pytest.mark.asyncio
async def test_ambiguous_request_fails_closed_when_llm_is_unavailable() -> None:
    classifier = FakeClassifier(RuntimeError("Bearer secret-value"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="照这个做一版",
        materials=[{"artifact_ref": "artifact:ref-1", "media_type": "video"}],
    )

    assert decision.intent is RouteIntent.UNKNOWN
    assert decision.requires_clarification is True
    assert decision.reason_code == "classifier_unavailable"
    assert "secret-value" not in decision.model_dump_json()


@pytest.mark.asyncio
async def test_colloquial_make_video_ad_uses_rule_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="帮我做一个护肤品广告视频",
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert decision.requires_clarification is False
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_product_creative_video_noun_phrase_uses_rule_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="伊菲丹 3 分钟断句创意视频 卖防晒霜的",
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert decision.requires_clarification is False
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_generate_timed_ad_story_uses_video_rule_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    story = (
        "高级但不浮夸的餐厅里，一张泛黄的旧照片被放在桌面中央。" * 40
        + "\n帮我根据以上故事情节生成 60s 广告"
    )
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content=story,
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert decision.reason_code == "explicit_video_request"
    assert decision.requires_clarification is False
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_generate_one_minute_ad_uses_video_rule_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="帮我生成一分钟广告",
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_short_clarification_video_uses_rule_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="创建视频",
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_generate_commerce_video_clarification_uses_rule_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content="生成带货视频",
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_pasted_episode_script_routes_as_video_without_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    script = (
        "# 剧本正文 /episode\n**时长**：60秒\n"
        "### 镜头 01\n- **时间**：00:00-00:04\n- **景别**：特写\n"
        "- **运镜**：俯拍\n- **画面**：旧照片\n- **旁白**：十年后\n"
        "### 镜头 02\n- **时间**：00:04-00:10\n- **景别**：中景\n"
        "- **运镜**：固定\n- **画面**：圆桌聚会蓝妹啤酒\n- **旁白**：无\n"
        "### 镜头 03\n- **时间**：00:10-00:20\n- **景别**：特写\n"
        "- **运镜**：推镜\n- **画面**：开瓶泡沫\n- **旁白**：如约\n"
        "### 镜头 04\n- **时间**：00:20-00:30\n- **景别**：全景\n"
        "- **运镜**：缓推\n- **画面**：碰杯 CTA\n- **行动引导**：购买\n"
    )
    decision = await ConversationRouteService(llm_classifier=classifier).route(
        content=script,
    )

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert decision.reason_code == "complete_script_payload"
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_long_story_with_video_keyword_bypasses_budget_gate() -> None:
    """规则命中必须先于 token 预算，否则长剧本会被误判为未知意图。"""

    classifier = FakeClassifier(RuntimeError("不应调用"))
    router = ConversationRouteService(
        llm_classifier=classifier,
        budget_policy_provider=ContextBudgetPolicyProvider(
            ContextBudgetConfig(
                effective_context_k=3,
                output_reserve_k=1,
                safety_reserve_k=1,
            )
        ),
    )

    decision = await router.route(content=("十年前我们在小餐馆碰杯。" * 80) + "生成60s广告")

    assert decision.intent is RouteIntent.VIDEO
    assert decision.decision_source is RouteDecisionSource.RULE
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_router_uses_shared_context_budget_before_calling_llm() -> None:
    classifier = FakeClassifier(RuntimeError("不应调用"))
    router = ConversationRouteService(
        llm_classifier=classifier,
        budget_policy_provider=ContextBudgetPolicyProvider(
            ContextBudgetConfig(
                effective_context_k=3,
                output_reserve_k=1,
                safety_reserve_k=1,
            )
        ),
    )

    decision = await router.route(content="照" * 600)

    assert decision.intent is RouteIntent.UNKNOWN
    assert decision.reason_code == "request_too_large"
    assert decision.requires_clarification is True
    assert classifier.calls == []
