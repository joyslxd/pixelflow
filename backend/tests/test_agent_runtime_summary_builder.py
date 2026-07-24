from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from deerflow.agents.middlewares.summarization_middleware import (
    DeerFlowSummarizationMiddleware,
)
from pixelflow.agent_runtime.context.compaction import (
    DeerFlowSummaryEngine,
    SummaryBuilder,
    SummaryBuildRequest,
    SummaryBuildValidationError,
    SummaryGenerationError,
    SummaryGenerationInput,
    SummarySemanticSnapshot,
    SummarySourceMessage,
)
from pixelflow.agent_runtime.contracts import ContextSummary

NOW = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)


def _semantic_snapshot(**updates: Any) -> SummarySemanticSnapshot:
    payload: dict[str, Any] = {
        "user_goals": ["制作 30 秒新品视频"],
        "confirmed_decisions": ["使用 9:16 画幅"],
        "negative_constraints": ["不要真人出镜"],
        "workflow_states": {"video-1": "plan_review"},
        "unresolved_questions": ["是否需要旁白"],
        "artifact_evidence_refs": ["artifact:plan-1"],
    }
    payload.update(updates)
    return SummarySemanticSnapshot.model_validate(payload)


def _context_summary(
    *,
    summary_id: str = "summary-1",
    version: int = 1,
    previous_summary_id: str | None = None,
    covered_message_ids: list[str] | None = None,
    covered_sequence_end: int = 2,
) -> ContextSummary:
    semantic = _semantic_snapshot()
    payload: dict[str, Any] = {
        "summary_id": summary_id,
        "conversation_id": "conversation-1",
        "version": version,
        "previous_summary_id": previous_summary_id,
        "content_hash": "sha256:" + "0" * 64,
        "covered_message_ids": covered_message_ids or ["message-1", "message-2"],
        "covered_sequence_start": 1,
        "covered_sequence_end": covered_sequence_end,
        "compression_model": "summary-model",
        "created_at": NOW,
    }
    payload.update(semantic.model_dump(mode="python"))
    return ContextSummary.model_validate(payload)


def _message(
    sequence: int,
    *,
    conversation_id: str = "conversation-1",
    message_id: str | None = None,
    role: str = "user",
    content: Any | None = None,
) -> SummarySourceMessage:
    return SummarySourceMessage(
        conversation_id=conversation_id,
        message_id=message_id or f"message-{sequence}",
        sequence=sequence,
        role=role,
        content=content if content is not None else {"text": f"消息 {sequence}"},
    )


class _FakeSummaryEngine:
    def __init__(
        self,
        *,
        semantic: SummarySemanticSnapshot | None = None,
        token_count: int = 321,
    ) -> None:
        self.model_name = "summary-model"
        self.semantic = semantic or _semantic_snapshot()
        self.token_count = token_count
        self.count_inputs: list[SummaryGenerationInput] = []
        self.summary_inputs: list[SummaryGenerationInput] = []

    def count_tokens(self, source: SummaryGenerationInput) -> int:
        self.count_inputs.append(source.model_copy(deep=True))
        return self.token_count

    async def summarize(self, source: SummaryGenerationInput) -> SummarySemanticSnapshot:
        self.summary_inputs.append(source.model_copy(deep=True))
        return self.semantic.model_copy(deep=True)


def _builder(
    engine: Any,
    *,
    summary_id: str = "summary-next",
) -> SummaryBuilder:
    return SummaryBuilder(
        engine=engine,
        summary_id_factory=lambda: summary_id,
        clock=lambda: NOW,
    )


def test_summary_build_request_rejects_business_context() -> None:
    with pytest.raises(ValidationError, match="business_context"):
        SummaryBuildRequest.model_validate(
            {
                "conversation_id": "conversation-1",
                "previous_summary": None,
                "new_messages": [_message(1).model_dump(mode="python")],
                "business_context": {
                    "creation_contract": {"duration": 30},
                    "pending_action": "generate_video",
                },
            }
        )


@pytest.mark.asyncio
async def test_summary_builder_builds_first_version_from_contiguous_messages() -> None:
    engine = _FakeSummaryEngine(token_count=456)
    builder = _builder(engine, summary_id="summary-1")
    request = SummaryBuildRequest(
        conversation_id="conversation-1",
        new_messages=(
            _message(1),
            _message(2, role="assistant"),
        ),
    )

    result = await builder.build(request)

    assert result.source_token_count == 456
    assert result.summary.summary_id == "summary-1"
    assert result.summary.conversation_id == "conversation-1"
    assert result.summary.version == 1
    assert result.summary.previous_summary_id is None
    assert result.summary.covered_message_ids == ["message-1", "message-2"]
    assert result.summary.covered_sequence_start == 1
    assert result.summary.covered_sequence_end == 2
    assert result.summary.compression_model == "summary-model"
    assert result.summary.created_at == NOW
    assert result.summary.content_hash.startswith("sha256:")
    assert len(result.summary.content_hash) == 71
    assert engine.count_inputs == engine.summary_inputs
    assert engine.summary_inputs[0].previous_summary is None


@pytest.mark.asyncio
async def test_summary_builder_passes_only_previous_semantics_and_new_messages() -> None:
    previous = _context_summary()
    next_semantic = _semantic_snapshot(
        confirmed_decisions=["使用 9:16 画幅", "旁白使用温暖女声"],
        unresolved_questions=[],
    )
    engine = _FakeSummaryEngine(semantic=next_semantic)
    builder = _builder(engine, summary_id="summary-2")

    result = await builder.build(
        SummaryBuildRequest(
            conversation_id="conversation-1",
            previous_summary=previous,
            new_messages=(
                _message(3),
                _message(4, role="assistant"),
            ),
        )
    )

    generation_input = engine.summary_inputs[0]
    assert generation_input.previous_summary == _semantic_snapshot()
    assert [message.message_id for message in generation_input.new_messages] == [
        "message-3",
        "message-4",
    ]
    assert all(message.message_id not in {"message-1", "message-2"} for message in generation_input.new_messages)
    assert not hasattr(generation_input, "business_context")
    assert result.summary.version == 2
    assert result.summary.previous_summary_id == "summary-1"
    assert result.summary.covered_message_ids == [
        "message-1",
        "message-2",
        "message-3",
        "message-4",
    ]
    assert result.summary.covered_sequence_end == 4
    assert result.summary.confirmed_decisions == [
        "使用 9:16 画幅",
        "旁白使用温暖女声",
    ]
    assert result.summary.unresolved_questions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "messages", "error"),
    [
        (None, (), "至少包含一条"),
        (None, (_message(2),), "sequence 1"),
        (None, (_message(1), _message(3)), "连续"),
        (None, (_message(2), _message(1)), "严格递增"),
        (_context_summary(), (_message(4),), "sequence 3"),
        (
            _context_summary(),
            (_message(3, message_id="message-2"),),
            "message_id",
        ),
        (
            _context_summary(),
            (_message(3, conversation_id="conversation-2"),),
            "conversation_id",
        ),
    ],
)
async def test_summary_builder_rejects_non_incremental_message_ranges(
    previous: ContextSummary | None,
    messages: tuple[SummarySourceMessage, ...],
    error: str,
) -> None:
    builder = _builder(_FakeSummaryEngine())

    with pytest.raises(SummaryBuildValidationError, match=error):
        await builder.build(
            SummaryBuildRequest(
                conversation_id="conversation-1",
                previous_summary=previous,
                new_messages=messages,
            )
        )


@pytest.mark.asyncio
async def test_summary_builder_rejects_previous_summary_from_another_conversation() -> None:
    previous = _context_summary().model_copy(update={"conversation_id": "conversation-2"})
    builder = _builder(_FakeSummaryEngine())

    with pytest.raises(SummaryBuildValidationError, match="上一版摘要"):
        await builder.build(
            SummaryBuildRequest(
                conversation_id="conversation-1",
                previous_summary=previous,
                new_messages=(_message(3),),
            )
        )


class _BlockingSummaryEngine(_FakeSummaryEngine):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def summarize(self, source: SummaryGenerationInput) -> SummarySemanticSnapshot:
        self.started.set()
        await self.release.wait()
        self.summary_inputs.append(source.model_copy(deep=True))
        return self.semantic.model_copy(deep=True)


@pytest.mark.asyncio
async def test_summary_builder_freezes_source_before_await() -> None:
    previous = _context_summary()
    message = _message(3, content={"text": "确认旁白", "nested": {"value": 1}})
    request = SummaryBuildRequest(
        conversation_id="conversation-1",
        previous_summary=previous,
        new_messages=(message,),
    )
    engine = _BlockingSummaryEngine()
    task = asyncio.create_task(_builder(engine, summary_id="summary-2").build(request))
    await engine.started.wait()

    previous.user_goals.append("调用方后改目标")
    assert isinstance(message.content, dict)
    message.content["nested"]["value"] = 99
    engine.release.set()
    result = await task

    generation_input = engine.summary_inputs[0]
    assert generation_input.previous_summary is not None
    assert generation_input.previous_summary.user_goals == ("制作 30 秒新品视频",)
    assert generation_input.new_messages[0].content == {
        "text": "确认旁白",
        "nested": {"value": 1},
    }
    assert "调用方后改目标" not in result.summary.user_goals


class _MutatingSummaryEngine(_FakeSummaryEngine):
    def count_tokens(self, source: SummaryGenerationInput) -> int:
        assert isinstance(source.new_messages[0].content, dict)
        source.new_messages[0].content["nested"]["value"] = 99
        assert source.previous_summary is not None
        source.previous_summary.workflow_states["video-1"] = "被计量器修改"
        return self.token_count


@pytest.mark.asyncio
async def test_summary_builder_copies_input_for_each_engine_boundary() -> None:
    previous = _context_summary()
    message = _message(3, content={"nested": {"value": 1}})
    engine = _MutatingSummaryEngine()

    await _builder(engine, summary_id="summary-2").build(
        SummaryBuildRequest(
            conversation_id="conversation-1",
            previous_summary=previous,
            new_messages=(message,),
        )
    )

    generation_input = engine.summary_inputs[0]
    assert generation_input.new_messages[0].content == {"nested": {"value": 1}}
    assert generation_input.previous_summary is not None
    assert generation_input.previous_summary.workflow_states == {"video-1": "plan_review"}
    assert message.content == {"nested": {"value": 1}}
    assert previous.workflow_states == {"video-1": "plan_review"}


@pytest.mark.asyncio
async def test_summary_builder_rejects_invalid_engine_output() -> None:
    engine = _FakeSummaryEngine(
        semantic=_semantic_snapshot(
            artifact_evidence_refs=["artifact:plan-1", "artifact:plan-1"],
        )
    )

    with pytest.raises(ValidationError, match="不能重复"):
        await _builder(engine).build(
            SummaryBuildRequest(
                conversation_id="conversation-1",
                new_messages=(_message(1),),
            )
        )


@pytest.mark.asyncio
async def test_summary_content_hash_ignores_generated_identity_and_time() -> None:
    request = SummaryBuildRequest(
        conversation_id="conversation-1",
        new_messages=(_message(1),),
    )
    first = await SummaryBuilder(
        engine=_FakeSummaryEngine(),
        summary_id_factory=lambda: "summary-a",
        clock=lambda: NOW,
    ).build(request)
    second = await SummaryBuilder(
        engine=_FakeSummaryEngine(),
        summary_id_factory=lambda: "summary-b",
        clock=lambda: NOW.replace(hour=16),
    ).build(request)

    assert first.summary.content_hash == second.summary.content_hash


class _FakeDeerFlowMiddleware:
    def __init__(self, response: str) -> None:
        self.response = response
        self.counted_messages: list[Any] | None = None
        self.summarized_messages: list[Any] | None = None

    def token_counter(self, messages: list[Any]) -> int:
        self.counted_messages = messages
        return 789

    async def _acreate_summary(self, messages: list[Any]) -> str:
        self.summarized_messages = messages
        return self.response


@pytest.mark.asyncio
async def test_deerflow_summary_engine_reuses_token_counter_and_async_summary() -> None:
    semantic = _semantic_snapshot(unresolved_questions=[])
    middleware = _FakeDeerFlowMiddleware(semantic.model_dump_json())
    engine = DeerFlowSummaryEngine(
        model_name="deerflow-summary-model",
        token_counter=middleware.token_counter,
        summary_runner=middleware._acreate_summary,
    )
    source = SummaryGenerationInput(
        conversation_id="conversation-1",
        previous_summary=_semantic_snapshot(),
        new_messages=(
            _message(3),
            _message(4, role="assistant"),
        ),
    )

    assert engine.count_tokens(source) == 789
    assert await engine.summarize(source) == semantic
    assert middleware.counted_messages is not None
    assert middleware.summarized_messages is not None
    assert [message.type for message in middleware.counted_messages] == [
        "human",
        "human",
        "ai",
    ]
    assert [message.content for message in middleware.counted_messages] == [message.content for message in middleware.summarized_messages]
    assert "creation_contract" not in middleware.summarized_messages[0].content


@pytest.mark.asyncio
async def test_deerflow_factory_uses_dedicated_structured_prompt() -> None:
    semantic = _semantic_snapshot(unresolved_questions=[])
    model = MagicMock()

    async def _respond(prompt: str, **_: Any) -> Any:
        if '"user_goals"' not in prompt:
            return MagicMock(text="## SESSION INTENT\n制作视频\n\n## SUMMARY\n保持当前方案")
        return MagicMock(text=semantic.model_dump_json())

    model.ainvoke = AsyncMock(side_effect=_respond)
    middleware = DeerFlowSummarizationMiddleware(
        model=model,
        trigger=None,
        token_counter=lambda _: 789,
    )
    engine = DeerFlowSummaryEngine.from_middleware(
        middleware,
        model_name="deerflow-summary-model",
    )
    source = SummaryGenerationInput(
        conversation_id="conversation-1",
        previous_summary=_semantic_snapshot(),
        new_messages=(_message(3),),
    )

    assert await engine.summarize(source) == semantic
    prompt = model.ainvoke.await_args.args[0]
    assert '"user_goals"' in prompt
    assert '"workflow_states"' in prompt
    assert "只返回一个 JSON 对象" in prompt


@pytest.mark.asyncio
async def test_deerflow_summary_engine_rejects_non_json_output() -> None:
    middleware = _FakeDeerFlowMiddleware("Error generating summary: model unavailable")
    engine = DeerFlowSummaryEngine(
        model_name="deerflow-summary-model",
        token_counter=middleware.token_counter,
        summary_runner=middleware._acreate_summary,
    )

    with pytest.raises(SummaryGenerationError, match="结构化"):
        await engine.summarize(
            SummaryGenerationInput(
                conversation_id="conversation-1",
                new_messages=(_message(1),),
            )
        )
