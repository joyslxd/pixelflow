from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentEventType,
    ContextSummary,
    ExternalJobRef,
    ExternalJobStatus,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    OperationRecord,
    SQLAgentRuntimeRepository,
)

RepositoryKind = Literal["memory", "sql"]
OWNER_A = "user-a"
OWNER_B = "user-b"
CONVERSATION_ID = "conv-shared"
NOW = datetime(2026, 7, 24, 1, 2, 3, 456789, tzinfo=UTC)
LOCAL_TIMEZONE = timezone(timedelta(hours=8))


@asynccontextmanager
async def _repository(kind: RepositoryKind) -> AsyncIterator[AgentRuntimeRepository]:
    if kind == "memory":
        yield MemoryAgentRuntimeRepository()
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=AGENT_RUNTIME_TABLES))
    try:
        yield SQLAgentRuntimeRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def _external_job(workflow_id: str) -> ExternalJobRef:
    return ExternalJobRef(
        job_id=f"job-for-{workflow_id}",
        provider_job_id="provider-job-1",
        workflow_id=workflow_id,
        stage="image_generate",
        status=ExternalJobStatus.POLLING,
        attempt=1,
        idempotency_key=f"idem-{workflow_id}",
        next_poll_at=NOW + timedelta(minutes=1),
        lease_owner=None,
        lease_expires_at=None,
    )


def _workflow(
    workflow_id: str,
    *,
    conversation_id: str = CONVERSATION_ID,
    updated_at: datetime = NOW,
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=WorkflowKind.IMAGE,
        status=WorkflowStatus.RUNNING,
        current_stage="image_generate",
        stage_version=2,
        creation_contract_snapshot={"nested": {"ratio": "1:1"}},
        pending_external_job=_external_job(workflow_id),
        latest_artifact_refs=["artifact-1"],
        context_version=3,
        created_at=NOW,
        updated_at=updated_at,
    )


def _turn(
    turn_id: str,
    client_input_id: str,
    *,
    conversation_id: str = CONVERSATION_ID,
) -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        conversation_id=conversation_id,
        client_input_id=UUID(client_input_id),
        status=TurnStatus.ACCEPTED,
        target_workflow_id="wf-1",
        decision=ActionDecision(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent="image",
            target_workflow_id="wf-1",
            target_stage="image_generate",
            target_artifact_ref=None,
            confidence=0.99,
            requires_confirmation=False,
            clarification_question=None,
            patch={"source": "test"},
            reason_code="explicit_target",
            idempotency_key=f"decision-{turn_id}",
        ),
        expected_context_version=3,
        created_at=NOW,
    )


def _summary(
    summary_id: str,
    version: int,
    *,
    conversation_id: str = CONVERSATION_ID,
) -> ContextSummary:
    return ContextSummary(
        summary_id=summary_id,
        conversation_id=conversation_id,
        version=version,
        previous_summary_id=None if version == 1 else f"summary-{version - 1}",
        content_hash=f"hash-{version}",
        user_goals=["生成商品图"],
        confirmed_decisions=["使用 1:1"],
        negative_constraints=["不要水印"],
        workflow_states={"wf-1": "running"},
        unresolved_questions=["是否需要透明背景"],
        artifact_evidence_refs=["artifact-1"],
        covered_message_ids=["message-1", "message-2"],
        covered_sequence_start=1,
        covered_sequence_end=2,
        compression_model="fake-summary-model",
        created_at=NOW + timedelta(seconds=version),
    )


def _event(
    event_id: str,
    sequence: int,
    *,
    conversation_id: str = CONVERSATION_ID,
    cursor: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor or f"cursor-{sequence}",
        conversation_id=conversation_id,
        run_id="run-1",
        occurred_at=NOW + timedelta(seconds=sequence),
        type=AgentEventType.WORKFLOW_PROGRESSED,
        payload={"nested": {"sequence": sequence}},
    )


def _operation(
    job_id: str,
    *,
    conversation_id: str = CONVERSATION_ID,
    idempotency_key: str | None = None,
    created_at: datetime = NOW,
) -> OperationRecord:
    return OperationRecord(
        job_id=job_id,
        provider_job_id="provider-job-1",
        workflow_id=f"wf-for-{job_id}",
        conversation_id=conversation_id,
        stage="image_generate",
        stage_version=2,
        status=ExternalJobStatus.POLLING,
        attempt=1,
        request_hash=f"hash-{job_id}",
        idempotency_key=idempotency_key or f"idem-{job_id}",
        next_poll_at=NOW + timedelta(minutes=1),
        lease_owner="worker-1",
        lease_expires_at=NOW + timedelta(minutes=2),
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_round_trips_all_records_with_stable_order(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        older_workflow = _workflow("wf-1", updated_at=NOW)
        newer_workflow = _workflow("wf-2", updated_at=NOW + timedelta(seconds=1))
        first_turn = _turn("turn-1", "00000000-0000-0000-0000-000000000001")
        second_turn = _turn("turn-2", "00000000-0000-0000-0000-000000000002")
        first_summary = _summary("summary-1", 1)
        second_summary = _summary("summary-2", 2)
        first_event = _event("event-1", 1)
        second_event = _event("event-2", 2)
        first_operation = _operation("job-1", created_at=NOW)
        second_operation = _operation("job-2", created_at=NOW + timedelta(seconds=1))

        assert await repository.create_workflow(OWNER_A, older_workflow) == older_workflow
        assert await repository.create_workflow(OWNER_A, newer_workflow) == newer_workflow
        assert await repository.create_turn(OWNER_A, first_turn) == first_turn
        assert await repository.create_turn(OWNER_A, second_turn) == second_turn
        assert await repository.create_summary(OWNER_A, first_summary) == first_summary
        assert await repository.create_summary(OWNER_A, second_summary) == second_summary
        assert await repository.create_event(OWNER_A, first_event) == first_event
        assert await repository.create_event(OWNER_A, second_event) == second_event
        assert await repository.create_operation(OWNER_A, first_operation) == first_operation
        assert await repository.create_operation(OWNER_A, second_operation) == second_operation

        assert await repository.get_workflow(OWNER_A, "wf-1") == older_workflow
        assert [item.workflow_id for item in await repository.list_workflows(OWNER_A, CONVERSATION_ID)] == [
            "wf-2",
            "wf-1",
        ]
        assert await repository.get_turn(OWNER_A, "turn-1") == first_turn
        assert (
            await repository.get_turn_by_client_input_id(
                OWNER_A,
                CONVERSATION_ID,
                UUID("00000000-0000-0000-0000-000000000002"),
            )
            == second_turn
        )
        assert [item.turn_id for item in await repository.list_turns(OWNER_A, CONVERSATION_ID)] == ["turn-1", "turn-2"]
        assert await repository.get_summary(OWNER_A, "summary-1") == first_summary
        assert [item.version for item in await repository.list_summaries(OWNER_A, CONVERSATION_ID)] == [1, 2]
        assert await repository.get_event(OWNER_A, "event-1") == first_event
        assert [item.sequence for item in await repository.list_events(OWNER_A, CONVERSATION_ID)] == [1, 2]
        assert await repository.get_operation(OWNER_A, "job-1") == first_operation
        assert [item.job_id for item in await repository.list_operations(OWNER_A, CONVERSATION_ID)] == ["job-1", "job-2"]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_hides_every_record_from_other_users(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        await repository.create_workflow(OWNER_A, _workflow("wf-1"))
        await repository.create_turn(OWNER_A, _turn("turn-1", "00000000-0000-0000-0000-000000000001"))
        await repository.create_summary(OWNER_A, _summary("summary-1", 1))
        await repository.create_event(OWNER_A, _event("event-1", 1))
        await repository.create_operation(OWNER_A, _operation("job-1"))

        assert await repository.get_workflow(OWNER_B, "wf-1") is None
        assert await repository.list_workflows(OWNER_B, CONVERSATION_ID) == []
        assert await repository.get_turn(OWNER_B, "turn-1") is None
        assert (
            await repository.get_turn_by_client_input_id(
                OWNER_B,
                CONVERSATION_ID,
                UUID("00000000-0000-0000-0000-000000000001"),
            )
            is None
        )
        assert await repository.list_turns(OWNER_B, CONVERSATION_ID) == []
        assert await repository.get_summary(OWNER_B, "summary-1") is None
        assert await repository.list_summaries(OWNER_B, CONVERSATION_ID) == []
        assert await repository.get_event(OWNER_B, "event-1") is None
        assert await repository.list_events(OWNER_B, CONVERSATION_ID) == []
        assert await repository.get_operation(OWNER_B, "job-1") is None
        assert await repository.list_operations(OWNER_B, CONVERSATION_ID) == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_rejects_cross_owner_id_collisions(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        records: list[tuple[Any, Any]] = [
            (repository.create_workflow, _workflow("wf-1")),
            (repository.create_turn, _turn("turn-1", "00000000-0000-0000-0000-000000000001")),
            (repository.create_summary, _summary("summary-1", 1)),
            (repository.create_event, _event("event-1", 1)),
            (repository.create_operation, _operation("job-1")),
        ]
        for create_record, record in records:
            await create_record(OWNER_A, record)
            with pytest.raises(AgentRuntimeRecordConflictError):
                await create_record(OWNER_B, record)


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_normalizes_unique_constraint_conflicts(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        await repository.create_turn(OWNER_A, _turn("turn-1", "00000000-0000-0000-0000-000000000001"))
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_turn(
                OWNER_A,
                _turn("turn-2", "00000000-0000-0000-0000-000000000001"),
            )

        await repository.create_summary(OWNER_A, _summary("summary-1", 1))
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_summary(OWNER_A, _summary("summary-other", 1))

        await repository.create_event(OWNER_A, _event("event-1", 1))
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_event(OWNER_A, _event("event-other", 1, cursor="cursor-other"))
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_event(OWNER_A, _event("event-cursor-conflict", 2, cursor="cursor-1"))

        first_operation = _operation("job-1", idempotency_key="same-idempotency-key")
        await repository.create_operation(OWNER_A, first_operation)
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_operation(
                OWNER_A,
                _operation("job-2", idempotency_key="same-idempotency-key"),
            )
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_operation(
                OWNER_A,
                _operation("job-stage-conflict").model_copy(
                    update={
                        "workflow_id": first_operation.workflow_id,
                        "idempotency_key": "different-idempotency-key",
                    }
                ),
            )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_returns_deeply_isolated_records(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        workflow = _workflow("wf-1")
        turn = _turn("turn-1", "00000000-0000-0000-0000-000000000001")
        summary = _summary("summary-1", 1)
        event = _event("event-1", 1)
        created_workflow = await repository.create_workflow(OWNER_A, workflow)
        created_turn = await repository.create_turn(OWNER_A, turn)
        created_summary = await repository.create_summary(OWNER_A, summary)
        created_event = await repository.create_event(OWNER_A, event)

        workflow.creation_contract_snapshot["nested"]["ratio"] = "changed-before-read"
        workflow.latest_artifact_refs.append("changed-before-read")
        assert workflow.pending_external_job is not None
        workflow.pending_external_job.stage = "changed-before-read"
        assert turn.decision is not None
        turn.decision.patch["source"] = "changed-before-read"
        summary.user_goals.append("changed-before-read")
        summary.workflow_states["wf-1"] = "changed-before-read"
        event.payload["nested"]["sequence"] = 99

        created_workflow.creation_contract_snapshot["nested"]["ratio"] = "changed-created-result"
        created_workflow.latest_artifact_refs.append("changed-created-result")
        assert created_workflow.pending_external_job is not None
        created_workflow.pending_external_job.stage = "changed-created-result"
        assert created_turn.decision is not None
        created_turn.decision.patch["source"] = "changed-created-result"
        created_summary.user_goals.append("changed-created-result")
        created_summary.workflow_states["wf-1"] = "changed-created-result"
        created_event.payload["nested"]["sequence"] = 100

        first_workflow_read = await repository.get_workflow(OWNER_A, "wf-1")
        first_turn_read = await repository.get_turn(OWNER_A, "turn-1")
        first_summary_read = await repository.get_summary(OWNER_A, "summary-1")
        first_event_read = await repository.get_event(OWNER_A, "event-1")
        assert first_workflow_read is not None
        assert first_workflow_read.creation_contract_snapshot == {"nested": {"ratio": "1:1"}}
        assert first_workflow_read.latest_artifact_refs == ["artifact-1"]
        assert first_workflow_read.pending_external_job is not None
        assert first_workflow_read.pending_external_job.stage == "image_generate"
        assert first_turn_read is not None and first_turn_read.decision is not None
        assert first_turn_read.decision.patch == {"source": "test"}
        assert first_summary_read is not None
        assert first_summary_read.user_goals == ["生成商品图"]
        assert first_summary_read.workflow_states == {"wf-1": "running"}
        assert first_event_read is not None
        assert first_event_read.payload == {"nested": {"sequence": 1}}

        first_workflow_read.creation_contract_snapshot["nested"]["ratio"] = "changed-read-result"
        first_workflow_read.latest_artifact_refs.append("changed-read-result")
        first_turn_read.decision.patch["source"] = "changed-read-result"
        first_summary_read.user_goals.append("changed-read-result")
        first_event_read.payload["nested"]["sequence"] = 101
        second_workflow_read = await repository.get_workflow(OWNER_A, "wf-1")
        second_turn_read = await repository.get_turn(OWNER_A, "turn-1")
        second_summary_read = await repository.get_summary(OWNER_A, "summary-1")
        second_event_read = await repository.get_event(OWNER_A, "event-1")
        assert second_workflow_read is not None
        assert second_workflow_read.creation_contract_snapshot == {"nested": {"ratio": "1:1"}}
        assert second_workflow_read.latest_artifact_refs == ["artifact-1"]
        assert second_turn_read is not None and second_turn_read.decision is not None
        assert second_turn_read.decision.patch == {"source": "test"}
        assert second_summary_read is not None and second_summary_read.user_goals == ["生成商品图"]
        assert second_event_read is not None and second_event_read.payload == {"nested": {"sequence": 1}}


@pytest.mark.parametrize("owner", ["", "   "])
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_rejects_empty_owner_on_every_query(kind: RepositoryKind, owner: str) -> None:
    async with _repository(kind) as repository:
        query_calls = [
            lambda: repository.get_workflow(owner, "wf-1"),
            lambda: repository.list_workflows(owner, CONVERSATION_ID),
            lambda: repository.get_turn(owner, "turn-1"),
            lambda: repository.get_turn_by_client_input_id(
                owner,
                CONVERSATION_ID,
                UUID("00000000-0000-0000-0000-000000000001"),
            ),
            lambda: repository.list_turns(owner, CONVERSATION_ID),
            lambda: repository.get_summary(owner, "summary-1"),
            lambda: repository.list_summaries(owner, CONVERSATION_ID),
            lambda: repository.get_event(owner, "event-1"),
            lambda: repository.list_events(owner, CONVERSATION_ID),
            lambda: repository.get_operation(owner, "job-1"),
            lambda: repository.list_operations(owner, CONVERSATION_ID),
        ]
        for query_call in query_calls:
            with pytest.raises(ValueError, match="user_id"):
                await query_call()


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_rejects_empty_owner_on_every_create(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        create_calls = [
            lambda: repository.create_workflow("   ", _workflow("wf-1")),
            lambda: repository.create_turn(
                "   ",
                _turn("turn-1", "00000000-0000-0000-0000-000000000001"),
            ),
            lambda: repository.create_summary("   ", _summary("summary-1", 1)),
            lambda: repository.create_event("   ", _event("event-1", 1)),
            lambda: repository.create_operation("   ", _operation("job-1")),
        ]
        for create_call in create_calls:
            with pytest.raises(ValueError, match="user_id"):
                await create_call()


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_normalizes_all_aware_datetimes_to_utc(kind: RepositoryKind) -> None:
    local_time = NOW.astimezone(LOCAL_TIMEZONE)
    workflow = _workflow("wf-1").model_copy(
        update={
            "created_at": local_time,
            "updated_at": local_time,
            "pending_external_job": _external_job("wf-1").model_copy(
                update={
                    "next_poll_at": local_time,
                    "lease_expires_at": local_time,
                }
            ),
        }
    )
    turn = _turn("turn-1", "00000000-0000-0000-0000-000000000001").model_copy(update={"created_at": local_time})
    summary = _summary("summary-1", 1).model_copy(update={"created_at": local_time})
    event = _event("event-1", 1).model_copy(update={"occurred_at": local_time})
    operation = _operation("job-1").model_copy(
        update={
            "next_poll_at": local_time,
            "lease_expires_at": local_time,
            "created_at": local_time,
            "updated_at": local_time,
        }
    )

    async with _repository(kind) as repository:
        created_workflow = await repository.create_workflow(OWNER_A, workflow)
        created_turn = await repository.create_turn(OWNER_A, turn)
        created_summary = await repository.create_summary(OWNER_A, summary)
        created_event = await repository.create_event(OWNER_A, event)
        created_operation = await repository.create_operation(OWNER_A, operation)

        assert created_workflow.created_at == NOW
        assert created_workflow.updated_at == NOW
        assert created_workflow.pending_external_job is not None
        assert created_workflow.pending_external_job.next_poll_at == NOW
        assert created_workflow.pending_external_job.lease_expires_at == NOW
        assert created_turn.created_at == NOW
        assert created_summary.created_at == NOW
        assert created_event.occurred_at == NOW
        assert created_operation.next_poll_at == NOW
        assert created_operation.lease_expires_at == NOW
        assert created_operation.created_at == NOW
        assert created_operation.updated_at == NOW

        assert await repository.get_workflow(OWNER_A, "wf-1") == created_workflow
        assert await repository.get_turn(OWNER_A, "turn-1") == created_turn
        assert await repository.get_summary(OWNER_A, "summary-1") == created_summary
        assert await repository.get_event(OWNER_A, "event-1") == created_event
        assert await repository.get_operation(OWNER_A, "job-1") == created_operation


def _records_with_naive_datetimes() -> list[tuple[str, Any]]:
    naive = NOW.replace(tzinfo=None)
    return [
        ("created_at", _workflow("wf-created").model_copy(update={"created_at": naive})),
        ("updated_at", _workflow("wf-updated").model_copy(update={"updated_at": naive})),
        (
            "next_poll_at",
            _workflow("wf-next-poll").model_copy(update={"pending_external_job": _external_job("wf-next-poll").model_copy(update={"next_poll_at": naive})}),
        ),
        (
            "lease_expires_at",
            _workflow("wf-lease").model_copy(update={"pending_external_job": _external_job("wf-lease").model_copy(update={"lease_expires_at": naive})}),
        ),
        (
            "created_at",
            _turn("turn-naive", "00000000-0000-0000-0000-000000000001").model_copy(update={"created_at": naive}),
        ),
        ("created_at", _summary("summary-naive", 1).model_copy(update={"created_at": naive})),
        ("occurred_at", _event("event-naive", 1).model_copy(update={"occurred_at": naive})),
        ("next_poll_at", _operation("job-next-poll").model_copy(update={"next_poll_at": naive})),
        ("lease_expires_at", _operation("job-lease").model_copy(update={"lease_expires_at": naive})),
        ("created_at", _operation("job-created").model_copy(update={"created_at": naive})),
        ("updated_at", _operation("job-updated").model_copy(update={"updated_at": naive})),
    ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_rejects_naive_datetimes(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        for field, record in _records_with_naive_datetimes():
            if isinstance(record, WorkflowRecord):
                create_call = repository.create_workflow(OWNER_A, record)
            elif isinstance(record, TurnRecord):
                create_call = repository.create_turn(OWNER_A, record)
            elif isinstance(record, ContextSummary):
                create_call = repository.create_summary(OWNER_A, record)
            elif isinstance(record, AgentEvent):
                create_call = repository.create_event(OWNER_A, record)
            else:
                create_call = repository.create_operation(OWNER_A, record)
            with pytest.raises(ValueError, match=field):
                await create_call


def _record_with_overlong_field(record_type: str, field: str, max_length: int) -> Any:
    value = "x" * (max_length + 1)
    if record_type == "workflow":
        return _workflow("wf-length").model_copy(update={field: value})
    if record_type == "turn":
        return _turn("turn-length", "00000000-0000-0000-0000-000000000001").model_copy(update={field: value})
    if record_type == "summary":
        version = 2 if field == "previous_summary_id" else 1
        return _summary("summary-length", version).model_copy(update={field: value})
    if record_type == "event":
        return _event("event-length", 1).model_copy(update={field: value})
    return _operation("job-length").model_copy(update={field: value})


@pytest.mark.parametrize(
    ("record_type", "field", "max_length"),
    [
        ("workflow", "workflow_id", 64),
        ("workflow", "conversation_id", 64),
        ("workflow", "current_stage", 64),
        ("turn", "turn_id", 64),
        ("turn", "conversation_id", 64),
        ("turn", "target_workflow_id", 64),
        ("summary", "summary_id", 64),
        ("summary", "conversation_id", 64),
        ("summary", "previous_summary_id", 64),
        ("summary", "content_hash", 128),
        ("summary", "compression_model", 128),
        ("event", "event_id", 64),
        ("event", "cursor", 128),
        ("event", "conversation_id", 64),
        ("event", "run_id", 64),
        ("operation", "job_id", 64),
        ("operation", "provider_job_id", 128),
        ("operation", "workflow_id", 64),
        ("operation", "conversation_id", 64),
        ("operation", "stage", 64),
        ("operation", "request_hash", 128),
        ("operation", "idempotency_key", 255),
        ("operation", "lease_owner", 128),
    ],
)
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_rejects_strings_longer_than_database_columns(
    kind: RepositoryKind,
    record_type: str,
    field: str,
    max_length: int,
) -> None:
    record = _record_with_overlong_field(record_type, field, max_length)
    async with _repository(kind) as repository:
        create_method = getattr(repository, f"create_{record_type}")
        with pytest.raises(ValueError, match=field):
            await create_method(OWNER_A, record)


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_rejects_owner_longer_than_database_column(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        with pytest.raises(ValueError, match="user_id"):
            await repository.create_workflow("x" * 65, _workflow("wf-1"))
        with pytest.raises(ValueError, match="user_id"):
            await repository.get_workflow("x" * 65, "wf-1")


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_contract_uses_id_tie_breakers_for_equal_timestamps(kind: RepositoryKind) -> None:
    async with _repository(kind) as repository:
        await repository.create_workflow(OWNER_A, _workflow("wf-1", updated_at=NOW))
        await repository.create_workflow(OWNER_A, _workflow("wf-2", updated_at=NOW))
        await repository.create_operation(OWNER_A, _operation("job-2", created_at=NOW))
        await repository.create_operation(OWNER_A, _operation("job-1", created_at=NOW))

        workflows = await repository.list_workflows(OWNER_A, CONVERSATION_ID)
        operations = await repository.list_operations(OWNER_A, CONVERSATION_ID)
        assert [item.workflow_id for item in workflows] == ["wf-2", "wf-1"]
        assert [item.job_id for item in operations] == ["job-1", "job-2"]
