"""M04.5 压缩生命周期 Outbox 事件测试。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.context import (
    CompactionProgressError,
    CompactionSegment,
    CompactionStageRequest,
    CompactionStageResult,
    ContextCompactionCoordinator,
    ContextCompactionRequest,
    ContextCompactionResult,
    ConversationCompactionRuntime,
    ModelContextProfile,
    RepositoryCompactionEventOutbox,
    TokenMeter,
)
from pixelflow.agent_runtime.contracts import (
    AgentEvent,
    AgentEventType,
    ContextBudgetReport,
)
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_TABLES,
    CompactionLeaseConflictError,
    MemoryAgentRuntimeRepository,
    MemoryCompactionQueueRepository,
    SQLCompactionQueueRepository,
)

RepositoryKind = Literal["memory", "sql"]
OWNER = "user-a"
CONVERSATION = "conversation-a"
RUN_ID = "run-a"
NOW = datetime(2026, 7, 25, 6, 0, tzinfo=UTC)


@asynccontextmanager
async def _event_repository(
    kind: RepositoryKind,
) -> AsyncIterator[MemoryCompactionQueueRepository | SQLCompactionQueueRepository]:
    if kind == "memory":
        yield MemoryCompactionQueueRepository()
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    try:
        yield SQLCompactionQueueRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def _existing_event() -> AgentEvent:
    return AgentEvent(
        event_id="event-existing",
        sequence=1,
        cursor="cursor-existing",
        conversation_id=CONVERSATION,
        run_id="run-existing",
        occurred_at=NOW - timedelta(seconds=1),
        type=AgentEventType.RUN_STATE_CHANGED,
        payload={"status": "running"},
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_outbox_appends_contiguous_compaction_events(
    kind: RepositoryKind,
) -> None:
    async with _event_repository(kind) as repository:
        await repository.create_event(OWNER, _existing_event())
        outbox = RepositoryCompactionEventOutbox(repository=repository)

        started = await outbox.append(
            OWNER,
            conversation_id=CONVERSATION,
            run_id=RUN_ID,
            event_type=AgentEventType.CONTEXT_COMPRESSION_STARTED,
            payload={"status": "running"},
            occurred_at=NOW,
        )
        progressed = await outbox.append(
            OWNER,
            conversation_id=CONVERSATION,
            run_id=RUN_ID,
            event_type=AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
            payload={"status": "running", "action": "incremental_summary"},
            occurred_at=NOW + timedelta(milliseconds=1),
        )

        assert (started.sequence, progressed.sequence) == (2, 3)
        assert started.cursor != progressed.cursor
        assert started.event_id != progressed.event_id
        assert [event.type for event in await repository.list_events(OWNER, CONVERSATION)] == [
            AgentEventType.RUN_STATE_CHANGED,
            AgentEventType.CONTEXT_COMPRESSION_STARTED,
            AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
        ]


class _RacingEventRepository:
    def __init__(self) -> None:
        self.backing = MemoryAgentRuntimeRepository()
        self.injected = False

    async def list_events(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[AgentEvent]:
        return await self.backing.list_events(user_id, conversation_id)

    async def create_event(
        self,
        user_id: str,
        record: AgentEvent,
    ) -> AgentEvent:
        if not self.injected:
            self.injected = True
            await self.backing.create_event(
                user_id,
                AgentEvent(
                    event_id="event-racer",
                    sequence=record.sequence,
                    cursor="cursor-racer",
                    conversation_id=record.conversation_id,
                    run_id="run-racer",
                    occurred_at=record.occurred_at,
                    type=AgentEventType.RUN_STATE_CHANGED,
                    payload={"status": "running"},
                ),
            )
        return await self.backing.create_event(user_id, record)


@pytest.mark.asyncio
async def test_repository_outbox_retries_a_concurrent_sequence_conflict() -> None:
    repository = _RacingEventRepository()
    outbox = RepositoryCompactionEventOutbox(repository=repository)

    appended = await outbox.append(
        OWNER,
        conversation_id=CONVERSATION,
        run_id=RUN_ID,
        event_type=AgentEventType.CONTEXT_COMPRESSION_STARTED,
        payload={"status": "running"},
        occurred_at=NOW,
    )

    assert appended.sequence == 2
    assert [event.event_id for event in await repository.list_events(OWNER, CONVERSATION)] == [
        "event-racer",
        appended.event_id,
    ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_atomically_finishes_lease_and_appends_terminal_event(
    kind: RepositoryKind,
) -> None:
    async with _event_repository(kind) as repository:
        lease = await repository.acquire_compaction_lease(
            OWNER,
            CONVERSATION,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        assert lease is not None
        await RepositoryCompactionEventOutbox(repository=repository).append(
            OWNER,
            conversation_id=CONVERSATION,
            run_id=RUN_ID,
            event_type=AgentEventType.CONTEXT_COMPRESSION_STARTED,
            payload={"status": "running"},
            occurred_at=NOW,
        )

        next_turn, terminal = await repository.finish_compaction_with_event(
            OWNER,
            CONVERSATION,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            now=NOW + timedelta(seconds=1),
            claim_next=True,
            run_id=RUN_ID,
            event_type=AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
            payload={"status": "completed"},
        )

        assert next_turn is None
        assert terminal.sequence == 2
        assert await repository.get_compaction_lease(OWNER, CONVERSATION) is None
        assert [event.type for event in await repository.list_events(OWNER, CONVERSATION)] == [
            AgentEventType.CONTEXT_COMPRESSION_STARTED,
            AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
        ]


def _budget(
    *,
    estimated_input_tokens: int,
    compaction_level: int,
) -> ContextBudgetReport:
    return ContextBudgetReport(
        estimated_input_tokens=estimated_input_tokens,
        effective_context_tokens=120,
        usable_input_tokens=100,
        max_output_tokens=10,
        safety_reserve_tokens=10,
        utilization=estimated_input_tokens / 100,
        compaction_level=compaction_level,
    )


def _request() -> ContextCompactionRequest:
    return ContextCompactionRequest(
        conversation_id=CONVERSATION,
        budget_report=_budget(
            estimated_input_tokens=75,
            compaction_level=2,
        ),
        incremental_segments=(CompactionSegment(segment_id="message-1", estimated_tokens=10),),
    )


class _LifecycleStageExecutor:
    def __init__(self, event_repository: MemoryCompactionQueueRepository) -> None:
        self._event_repository = event_repository
        self.requests: list[CompactionStageRequest] = []

    async def execute(
        self,
        request: CompactionStageRequest,
    ) -> CompactionStageResult:
        persisted = await self._event_repository.list_events(OWNER, CONVERSATION)
        assert persisted[0].type is AgentEventType.CONTEXT_COMPRESSION_STARTED
        self.requests.append(request)
        next_tokens = 65 if request.action == "externalize_payloads" else 40
        return CompactionStageResult(estimated_input_tokens=next_tokens)


def _coordinator(
    executor: _LifecycleStageExecutor,
) -> ContextCompactionCoordinator:
    return ContextCompactionCoordinator(
        executor=executor,
        summary_model_name="summary-model",
        model_profiles={
            "summary-model": ModelContextProfile(
                model_name="summary-model",
                max_context_tokens=150_000,
                max_output_tokens=10_000,
                tokenizer_strategy="provider_usage",
                verified_at=NOW,
                source="M04.5 测试档案",
            )
        },
        token_meter=TokenMeter(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_runtime_persists_started_progress_and_completed_in_order() -> None:
    event_repository = MemoryCompactionQueueRepository()
    executor = _LifecycleStageExecutor(event_repository)
    runtime = ConversationCompactionRuntime(
        coordinator=_coordinator(executor),
        repository=event_repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(repository=event_repository),
        clock=lambda: NOW,
    )

    result = await runtime.compact(OWNER, _request(), run_id=RUN_ID)
    events = await event_repository.list_events(OWNER, CONVERSATION)

    assert result.status == "completed"
    assert [event.type for event in events] == [
        AgentEventType.CONTEXT_COMPRESSION_STARTED,
        AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
        AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
        AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert {event.run_id for event in events} == {RUN_ID}
    assert [event.payload.get("action") for event in events[1:3]] == [
        "externalize_payloads",
        "incremental_summary",
    ]
    serialized_payloads = json.dumps(
        [event.payload for event in events],
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        "summary_text",
        "summary_content",
        "token",
        "prompt",
        "authorization",
        "api_key",
        "用户原文",
    ):
        assert forbidden not in serialized_payloads


def test_runtime_requires_event_sink() -> None:
    with pytest.raises(TypeError, match="event_sink"):
        ConversationCompactionRuntime(
            coordinator=object(),
            repository=MemoryCompactionQueueRepository(),
            lease_owner="worker-a",
            lease_ttl=timedelta(minutes=5),
            clock=lambda: NOW,
        )


def test_runtime_rejects_event_sink_bound_to_different_repository() -> None:
    with pytest.raises(ValueError, match="同一个 Repository"):
        ConversationCompactionRuntime(
            coordinator=object(),
            repository=MemoryCompactionQueueRepository(),
            lease_owner="worker-a",
            lease_ttl=timedelta(minutes=5),
            event_sink=RepositoryCompactionEventOutbox(repository=MemoryCompactionQueueRepository()),
            clock=lambda: NOW,
        )


class _FailingProgressEventSink:
    def __init__(self, delegate: RepositoryCompactionEventOutbox) -> None:
        self._delegate = delegate

    def is_bound_to(self, repository: object) -> bool:
        return self._delegate.is_bound_to(repository)

    async def append(self, *args: Any, **kwargs: Any) -> AgentEvent:
        if kwargs.get("event_type") is AgentEventType.CONTEXT_COMPRESSION_PROGRESSED:
            raise RuntimeError("模拟 Outbox 写入失败")
        return await self._delegate.append(*args, **kwargs)


@pytest.mark.asyncio
async def test_progress_event_failure_stops_compaction_and_preserves_recovery_marker() -> None:
    queue_repository = MemoryCompactionQueueRepository()
    executor = _LifecycleStageExecutor(queue_repository)
    runtime = ConversationCompactionRuntime(
        coordinator=_coordinator(executor),
        repository=queue_repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        event_sink=_FailingProgressEventSink(RepositoryCompactionEventOutbox(repository=queue_repository)),
        clock=lambda: NOW,
    )

    with pytest.raises(CompactionProgressError, match="进度事件"):
        await runtime.compact(OWNER, _request(), run_id=RUN_ID)

    events = await queue_repository.list_events(OWNER, CONVERSATION)
    assert [event.type for event in events] == [
        AgentEventType.CONTEXT_COMPRESSION_STARTED,
        AgentEventType.CONTEXT_COMPRESSION_FAILED,
    ]
    recovery = await queue_repository.get_compaction_lease(OWNER, CONVERSATION)
    assert recovery is not None
    assert recovery.lease_expires_at <= NOW


class _OutcomeCoordinator:
    def __init__(
        self,
        outcome: ContextCompactionResult | Exception,
    ) -> None:
        self.outcome = outcome
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def coordinate(
        self,
        request: ContextCompactionRequest,
        *,
        on_progress: Any = None,
    ) -> ContextCompactionResult:
        self.started.set()
        await self.resume.wait()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _paused_result() -> ContextCompactionResult:
    budget = _budget(
        estimated_input_tokens=95,
        compaction_level=4,
    )
    return ContextCompactionResult(
        status="paused",
        initial_budget_report=budget,
        final_budget_report=budget,
        target_input_tokens=44,
        attempts=(),
        model_invocation_allowed=False,
        pause_reason="hard_gate_compaction_failed",
    )


@pytest.mark.parametrize(
    ("outcome", "raises"),
    [
        (_paused_result(), False),
        (RuntimeError("包含用户原文的内部异常"), True),
    ],
)
@pytest.mark.asyncio
async def test_runtime_persists_safe_recoverable_failed_event(
    outcome: ContextCompactionResult | Exception,
    raises: bool,
) -> None:
    coordinator = _OutcomeCoordinator(outcome)
    queue_repository = MemoryCompactionQueueRepository()
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=queue_repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(repository=queue_repository),
        clock=lambda: NOW,
    )

    task = asyncio.create_task(runtime.compact(OWNER, _request(), run_id=RUN_ID))
    await coordinator.started.wait()
    coordinator.resume.set()
    if raises:
        with pytest.raises(RuntimeError, match="内部异常"):
            await task
    else:
        result = await task
        assert result.status == "paused"

    events = await queue_repository.list_events(OWNER, CONVERSATION)
    assert [event.type for event in events] == [
        AgentEventType.CONTEXT_COMPRESSION_STARTED,
        AgentEventType.CONTEXT_COMPRESSION_FAILED,
    ]
    assert events[-1].payload["status"] == "retry_required"
    assert events[-1].payload["reason_code"] in {
        "hard_gate_compaction_failed",
        "compaction_execution_failed",
    }
    assert "用户原文" not in json.dumps(events[-1].payload, ensure_ascii=False)
    recovery = await queue_repository.get_compaction_lease(OWNER, CONVERSATION)
    assert recovery is not None
    assert recovery.lease_expires_at <= NOW


@pytest.mark.asyncio
async def test_already_running_does_not_duplicate_started_event() -> None:
    coordinator = _OutcomeCoordinator(_paused_result())
    queue_repository = MemoryCompactionQueueRepository()
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=queue_repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(repository=queue_repository),
        clock=lambda: NOW,
    )

    first = asyncio.create_task(runtime.compact(OWNER, _request(), run_id=RUN_ID))
    await coordinator.started.wait()
    parallel = await runtime.compact(OWNER, _request(), run_id="run-b")
    persisted_during_run = await queue_repository.list_events(OWNER, CONVERSATION)
    coordinator.resume.set()
    await first

    assert parallel.status == "already_running"
    assert [event.type for event in persisted_during_run] == [
        AgentEventType.CONTEXT_COMPRESSION_STARTED,
    ]


@pytest.mark.asyncio
async def test_stale_worker_cannot_persist_completed_or_failed_terminal_event() -> None:
    queue_repository = MemoryCompactionQueueRepository()
    completed = _paused_result().model_copy(
        update={
            "status": "target_reached",
            "final_budget_report": _budget(
                estimated_input_tokens=40,
                compaction_level=4,
            ),
            "model_invocation_allowed": True,
            "pause_reason": None,
        }
    )
    coordinator = _OutcomeCoordinator(completed)
    current_time = [NOW]
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=queue_repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=1),
        event_sink=RepositoryCompactionEventOutbox(repository=queue_repository),
        clock=lambda: current_time[0],
    )

    task = asyncio.create_task(runtime.compact(OWNER, _request(), run_id=RUN_ID))
    await coordinator.started.wait()
    current_time[0] = NOW + timedelta(minutes=2)
    replacement = await queue_repository.acquire_compaction_lease(
        OWNER,
        CONVERSATION,
        lease_owner="worker-b",
        now=current_time[0],
        lease_expires_at=current_time[0] + timedelta(minutes=5),
    )
    assert replacement is not None
    coordinator.resume.set()

    with pytest.raises(CompactionLeaseConflictError, match="陈旧 worker"):
        await task

    events = await queue_repository.list_events(OWNER, CONVERSATION)
    assert [event.type for event in events] == [
        AgentEventType.CONTEXT_COMPRESSION_STARTED,
    ]
    active = await queue_repository.get_compaction_lease(OWNER, CONVERSATION)
    assert active is not None
    assert active.lease_token == replacement.lease_token
