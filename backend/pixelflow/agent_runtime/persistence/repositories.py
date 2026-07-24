"""Agent Runtime 五类业务投影的统一 Repository Port 与双实现。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID
from weakref import WeakKeyDictionary

from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..contracts import (
    AgentEvent,
    ContextSummary,
    ExternalJobStatus,
    TurnRecord,
    TurnStatus,
    WorkflowRecord,
)
from ..contracts.base import ContractModel
from .models import (
    PixelFlowAgentContextSummaryRow,
    PixelFlowAgentEventRow,
    PixelFlowAgentOperationRow,
    PixelFlowAgentTurnRow,
    PixelFlowAgentWorkflowRow,
)

_SQLITE_ENGINE_WRITE_LOCKS: WeakKeyDictionary[AsyncEngine, asyncio.Lock] = (
    WeakKeyDictionary()
)


class AgentRuntimeRecordConflictError(RuntimeError):
    """记录主键或唯一业务键已经被占用。"""


class OperationRecord(ContractModel):
    """外部任务 Operation 的可查询持久化投影。"""

    job_id: str = Field(min_length=1)
    provider_job_id: str | None = Field(default=None, min_length=1)
    workflow_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    stage_version: int = Field(ge=1)
    status: ExternalJobStatus
    attempt: int = Field(ge=1)
    request_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    next_poll_at: datetime | None = None
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class AgentRuntimeRepository(Protocol):
    """约束内存与 SQL 实现具有相同的所有者隔离语义。"""

    async def create_workflow(self, user_id: str, record: WorkflowRecord) -> WorkflowRecord: ...

    async def get_workflow(self, user_id: str, workflow_id: str) -> WorkflowRecord | None: ...

    async def list_workflows(self, user_id: str, conversation_id: str) -> list[WorkflowRecord]: ...

    async def create_turn(self, user_id: str, record: TurnRecord) -> TurnRecord: ...

    async def enqueue_turn(self, user_id: str, record: TurnRecord) -> TurnRecord: ...

    async def get_turn(self, user_id: str, turn_id: str) -> TurnRecord | None: ...

    async def get_turn_by_client_input_id(
        self,
        user_id: str,
        conversation_id: str,
        client_input_id: UUID,
    ) -> TurnRecord | None: ...

    async def list_turns(self, user_id: str, conversation_id: str) -> list[TurnRecord]: ...

    async def claim_next_turn(
        self,
        user_id: str,
        conversation_id: str,
    ) -> TurnRecord | None: ...

    async def create_summary(self, user_id: str, record: ContextSummary) -> ContextSummary: ...

    async def get_summary(self, user_id: str, summary_id: str) -> ContextSummary | None: ...

    async def list_summaries(self, user_id: str, conversation_id: str) -> list[ContextSummary]: ...

    async def create_event(self, user_id: str, record: AgentEvent) -> AgentEvent: ...

    async def get_event(self, user_id: str, event_id: str) -> AgentEvent | None: ...

    async def list_events(self, user_id: str, conversation_id: str) -> list[AgentEvent]: ...

    async def create_operation(self, user_id: str, record: OperationRecord) -> OperationRecord: ...

    async def get_operation(self, user_id: str, job_id: str) -> OperationRecord | None: ...

    async def list_operations(self, user_id: str, conversation_id: str) -> list[OperationRecord]: ...


def _require_text(field: str, value: str, max_length: int | None = None) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if max_length is not None and len(normalized) > max_length:
        raise ValueError(f"{field} must not exceed {max_length} characters")
    return normalized


def _optional_text(field: str, value: str | None, max_length: int) -> str | None:
    return None if value is None else _require_text(field, value, max_length)


def _clone[ModelT: ContractModel](record: ModelT) -> ModelT:
    return type(record).model_validate(record.model_dump(mode="python"))


def _normalize_datetime(field: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information")
    return value.astimezone(UTC)


def _optional_datetime(field: str, value: datetime | None) -> datetime | None:
    return None if value is None else _normalize_datetime(field, value)


def _database_utc(value: datetime) -> datetime:
    """数据库方言可能返回无时区的 UTC 墙上时间，读取时恢复 UTC 标记。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@asynccontextmanager
async def _turn_write_transaction(
    session: AsyncSession,
    sqlite_write_lock: asyncio.Lock | None,
) -> AsyncIterator[None]:
    if session.get_bind().dialect.name == "sqlite":
        if sqlite_write_lock is None:
            raise RuntimeError("SQLite Turn 写事务缺少 Engine 级进程内锁")
        # 同一 Engine 先串行写事务，再获取数据库写锁覆盖跨 Engine/进程并发。
        async with sqlite_write_lock:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield
            except BaseException:
                await session.rollback()
                raise
            else:
                await session.commit()
        return

    async with session.begin():
        yield


def _normalize_workflow(record: WorkflowRecord) -> WorkflowRecord:
    normalized = _clone(record)
    pending_job = normalized.pending_external_job
    if pending_job is not None:
        pending_job = pending_job.model_copy(
            update={
                "next_poll_at": _optional_datetime(
                    "pending_external_job.next_poll_at",
                    pending_job.next_poll_at,
                ),
                "lease_expires_at": _optional_datetime(
                    "pending_external_job.lease_expires_at",
                    pending_job.lease_expires_at,
                ),
            }
        )
    return _clone(
        normalized.model_copy(
            update={
                "workflow_id": _require_text("workflow_id", normalized.workflow_id, 64),
                "conversation_id": _require_text("conversation_id", normalized.conversation_id, 64),
                "current_stage": _require_text("current_stage", normalized.current_stage, 64),
                "pending_external_job": pending_job,
                "created_at": _normalize_datetime("created_at", normalized.created_at),
                "updated_at": _normalize_datetime("updated_at", normalized.updated_at),
            }
        )
    )


def _normalize_turn(record: TurnRecord) -> TurnRecord:
    normalized = _clone(record)
    return _clone(
        normalized.model_copy(
            update={
                "turn_id": _require_text("turn_id", normalized.turn_id, 64),
                "conversation_id": _require_text("conversation_id", normalized.conversation_id, 64),
                "target_workflow_id": _optional_text(
                    "target_workflow_id",
                    normalized.target_workflow_id,
                    64,
                ),
                "created_at": _normalize_datetime("created_at", normalized.created_at),
            }
        )
    )


def _normalize_summary(record: ContextSummary) -> ContextSummary:
    normalized = _clone(record)
    return _clone(
        normalized.model_copy(
            update={
                "summary_id": _require_text("summary_id", normalized.summary_id, 64),
                "conversation_id": _require_text("conversation_id", normalized.conversation_id, 64),
                "previous_summary_id": _optional_text(
                    "previous_summary_id",
                    normalized.previous_summary_id,
                    64,
                ),
                "content_hash": _require_text("content_hash", normalized.content_hash, 128),
                "compression_model": _require_text(
                    "compression_model",
                    normalized.compression_model,
                    128,
                ),
                "created_at": _normalize_datetime("created_at", normalized.created_at),
            }
        )
    )


def _normalize_event(record: AgentEvent) -> AgentEvent:
    normalized = _clone(record)
    return _clone(
        normalized.model_copy(
            update={
                "event_id": _require_text("event_id", normalized.event_id, 64),
                "cursor": _require_text("cursor", normalized.cursor, 128),
                "conversation_id": _require_text("conversation_id", normalized.conversation_id, 64),
                "run_id": _require_text("run_id", normalized.run_id, 64),
                "occurred_at": _normalize_datetime("occurred_at", normalized.occurred_at),
            }
        )
    )


def _normalize_operation(record: OperationRecord) -> OperationRecord:
    normalized = _clone(record)
    return _clone(
        normalized.model_copy(
            update={
                "job_id": _require_text("job_id", normalized.job_id, 64),
                "provider_job_id": _optional_text(
                    "provider_job_id",
                    normalized.provider_job_id,
                    128,
                ),
                "workflow_id": _require_text("workflow_id", normalized.workflow_id, 64),
                "conversation_id": _require_text("conversation_id", normalized.conversation_id, 64),
                "stage": _require_text("stage", normalized.stage, 64),
                "request_hash": _require_text("request_hash", normalized.request_hash, 128),
                "idempotency_key": _require_text("idempotency_key", normalized.idempotency_key, 255),
                "next_poll_at": _optional_datetime("next_poll_at", normalized.next_poll_at),
                "lease_owner": _optional_text("lease_owner", normalized.lease_owner, 128),
                "lease_expires_at": _optional_datetime(
                    "lease_expires_at",
                    normalized.lease_expires_at,
                ),
                "created_at": _normalize_datetime("created_at", normalized.created_at),
                "updated_at": _normalize_datetime("updated_at", normalized.updated_at),
            }
        )
    )


class MemoryAgentRuntimeRepository:
    """以隔离副本模拟 SQL 唯一约束和查询排序。"""

    def __init__(self) -> None:
        self._workflows: dict[tuple[str, str], WorkflowRecord] = {}
        self._workflow_ids: set[str] = set()
        self._turns: dict[tuple[str, str], TurnRecord] = {}
        self._turn_ids: set[str] = set()
        self._turn_owner_sequences: dict[tuple[str, str], int] = {}
        self._turn_client_keys: dict[
            tuple[str, str],
            tuple[str, str],
        ] = {}
        self._next_turn_sequence = 1
        self._summaries: dict[tuple[str, str], ContextSummary] = {}
        self._summary_ids: set[str] = set()
        self._summary_version_keys: set[tuple[str, int]] = set()
        self._events: dict[tuple[str, str], AgentEvent] = {}
        self._event_ids: set[str] = set()
        self._event_sequence_keys: set[tuple[str, int]] = set()
        self._event_cursor_keys: set[tuple[str, str]] = set()
        self._operations: dict[tuple[str, str], OperationRecord] = {}
        self._operation_ids: set[str] = set()
        self._operation_idempotency_keys: set[str] = set()
        self._operation_stage_keys: set[tuple[str, str, int, int]] = set()

    async def create_workflow(self, user_id: str, record: WorkflowRecord) -> WorkflowRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_workflow(record)
        if normalized.workflow_id in self._workflow_ids:
            raise AgentRuntimeRecordConflictError("Workflow 记录已存在")
        self._workflow_ids.add(normalized.workflow_id)
        self._workflows[(owner, normalized.workflow_id)] = _clone(normalized)
        return _clone(normalized)

    async def get_workflow(self, user_id: str, workflow_id: str) -> WorkflowRecord | None:
        owner = _require_text("user_id", user_id, 64)
        record = self._workflows.get((owner, _require_text("workflow_id", workflow_id, 64)))
        return None if record is None else _clone(record)

    async def list_workflows(self, user_id: str, conversation_id: str) -> list[WorkflowRecord]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [
            record
            for (record_owner, _), record in self._workflows.items()
            if record_owner == owner and record.conversation_id == conversation
        ]
        records.sort(key=lambda record: (record.updated_at, record.workflow_id), reverse=True)
        return [_clone(record) for record in records]

    async def create_turn(self, user_id: str, record: TurnRecord) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        client_key = (normalized.conversation_id, str(normalized.client_input_id))
        if normalized.turn_id in self._turn_ids or client_key in self._turn_client_keys:
            raise AgentRuntimeRecordConflictError("Turn 记录已存在")
        owner_key = (owner, normalized.turn_id)
        self._turn_ids.add(normalized.turn_id)
        self._turn_client_keys[client_key] = owner_key
        self._turns[owner_key] = _clone(normalized)
        self._turn_owner_sequences[owner_key] = self._next_turn_sequence
        self._next_turn_sequence += 1
        return _clone(normalized)

    async def enqueue_turn(
        self,
        user_id: str,
        record: TurnRecord,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        client_key = (
            normalized.conversation_id,
            str(normalized.client_input_id),
        )
        existing_owner_key = self._turn_client_keys.get(client_key)
        if existing_owner_key is not None:
            if existing_owner_key[0] != owner:
                raise AgentRuntimeRecordConflictError(
                    "Turn 幂等键已经被其他所有者占用"
                )
            return _clone(self._turns[existing_owner_key])
        return await self.create_turn(owner, normalized)

    async def get_turn(self, user_id: str, turn_id: str) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        record = self._turns.get((owner, _require_text("turn_id", turn_id, 64)))
        return None if record is None else _clone(record)

    async def get_turn_by_client_input_id(
        self,
        user_id: str,
        conversation_id: str,
        client_input_id: UUID,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        for (record_owner, _), record in self._turns.items():
            if record_owner == owner and record.conversation_id == conversation and record.client_input_id == client_input_id:
                return _clone(record)
        return None

    async def list_turns(self, user_id: str, conversation_id: str) -> list[TurnRecord]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        owner_keys = [
            owner_key
            for owner_key, record in self._turns.items()
            if owner_key[0] == owner and record.conversation_id == conversation
        ]
        owner_keys.sort(key=self._turn_owner_sequences.__getitem__)
        return [_clone(self._turns[owner_key]) for owner_key in owner_keys]

    async def claim_next_turn(
        self,
        user_id: str,
        conversation_id: str,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        owner_keys = [
            owner_key
            for owner_key, record in self._turns.items()
            if owner_key[0] == owner
            and record.conversation_id == conversation
            and record.status
            in {
                TurnStatus.ACCEPTED,
                TurnStatus.QUEUED,
                TurnStatus.PROCESSING,
            }
        ]
        owner_keys.sort(key=self._turn_owner_sequences.__getitem__)
        if any(
            self._turns[owner_key].status is TurnStatus.PROCESSING
            for owner_key in owner_keys
        ):
            return None
        if not owner_keys:
            return None
        owner_key = owner_keys[0]
        claimed = self._turns[owner_key].model_copy(
            update={"status": TurnStatus.PROCESSING}
        )
        self._turns[owner_key] = _clone(claimed)
        return _clone(claimed)

    async def create_summary(self, user_id: str, record: ContextSummary) -> ContextSummary:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_summary(record)
        version_key = (normalized.conversation_id, normalized.version)
        if normalized.summary_id in self._summary_ids or version_key in self._summary_version_keys:
            raise AgentRuntimeRecordConflictError("ContextSummary 记录已存在")
        self._summary_ids.add(normalized.summary_id)
        self._summary_version_keys.add(version_key)
        self._summaries[(owner, normalized.summary_id)] = _clone(normalized)
        return _clone(normalized)

    async def get_summary(self, user_id: str, summary_id: str) -> ContextSummary | None:
        owner = _require_text("user_id", user_id, 64)
        record = self._summaries.get((owner, _require_text("summary_id", summary_id, 64)))
        return None if record is None else _clone(record)

    async def list_summaries(self, user_id: str, conversation_id: str) -> list[ContextSummary]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [
            record
            for (record_owner, _), record in self._summaries.items()
            if record_owner == owner and record.conversation_id == conversation
        ]
        records.sort(key=lambda record: (record.version, record.summary_id))
        return [_clone(record) for record in records]

    async def create_event(self, user_id: str, record: AgentEvent) -> AgentEvent:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_event(record)
        sequence_key = (normalized.conversation_id, normalized.sequence)
        cursor_key = (normalized.conversation_id, normalized.cursor)
        if (
            normalized.event_id in self._event_ids
            or sequence_key in self._event_sequence_keys
            or cursor_key in self._event_cursor_keys
        ):
            raise AgentRuntimeRecordConflictError("AgentEvent 记录已存在")
        self._event_ids.add(normalized.event_id)
        self._event_sequence_keys.add(sequence_key)
        self._event_cursor_keys.add(cursor_key)
        self._events[(owner, normalized.event_id)] = _clone(normalized)
        return _clone(normalized)

    async def get_event(self, user_id: str, event_id: str) -> AgentEvent | None:
        owner = _require_text("user_id", user_id, 64)
        record = self._events.get((owner, _require_text("event_id", event_id, 64)))
        return None if record is None else _clone(record)

    async def list_events(self, user_id: str, conversation_id: str) -> list[AgentEvent]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [
            record
            for (record_owner, _), record in self._events.items()
            if record_owner == owner and record.conversation_id == conversation
        ]
        records.sort(key=lambda record: (record.sequence, record.event_id))
        return [_clone(record) for record in records]

    async def create_operation(self, user_id: str, record: OperationRecord) -> OperationRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_operation(record)
        stage_key = (
            normalized.workflow_id,
            normalized.stage,
            normalized.stage_version,
            normalized.attempt,
        )
        if (
            normalized.job_id in self._operation_ids
            or normalized.idempotency_key in self._operation_idempotency_keys
            or stage_key in self._operation_stage_keys
        ):
            raise AgentRuntimeRecordConflictError("Operation 记录已存在")
        self._operation_ids.add(normalized.job_id)
        self._operation_idempotency_keys.add(normalized.idempotency_key)
        self._operation_stage_keys.add(stage_key)
        self._operations[(owner, normalized.job_id)] = _clone(normalized)
        return _clone(normalized)

    async def get_operation(self, user_id: str, job_id: str) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        record = self._operations.get((owner, _require_text("job_id", job_id, 64)))
        return None if record is None else _clone(record)

    async def list_operations(self, user_id: str, conversation_id: str) -> list[OperationRecord]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [
            record
            for (record_owner, _), record in self._operations.items()
            if record_owner == owner and record.conversation_id == conversation
        ]
        records.sort(key=lambda record: (record.created_at, record.job_id))
        return [_clone(record) for record in records]


def _workflow_from_row(row: PixelFlowAgentWorkflowRow) -> WorkflowRecord:
    return WorkflowRecord.model_validate(
        {
            "workflow_id": row.workflow_id,
            "conversation_id": row.conversation_id,
            "kind": row.kind,
            "status": row.status,
            "current_stage": row.current_stage,
            "stage_version": row.stage_version,
            "creation_contract_snapshot": row.creation_contract_snapshot_json,
            "pending_external_job": row.pending_external_job_json,
            "latest_artifact_refs": row.latest_artifact_refs_json,
            "context_version": row.context_version,
            "created_at": _database_utc(row.created_at),
            "updated_at": _database_utc(row.updated_at),
        }
    )


def _turn_from_row(row: PixelFlowAgentTurnRow) -> TurnRecord:
    return TurnRecord.model_validate(
        {
            "turn_id": row.turn_id,
            "conversation_id": row.conversation_id,
            "client_input_id": row.client_input_id,
            "status": row.status,
            "target_workflow_id": row.target_workflow_id,
            "decision": row.decision_json,
            "expected_context_version": row.expected_context_version,
            "created_at": _database_utc(row.created_at),
        }
    )


def _summary_from_row(row: PixelFlowAgentContextSummaryRow) -> ContextSummary:
    return ContextSummary.model_validate(
        {
            "summary_id": row.summary_id,
            "conversation_id": row.conversation_id,
            "version": row.version,
            "previous_summary_id": row.previous_summary_id,
            "content_hash": row.content_hash,
            "user_goals": row.user_goals_json,
            "confirmed_decisions": row.confirmed_decisions_json,
            "negative_constraints": row.negative_constraints_json,
            "workflow_states": row.workflow_states_json,
            "unresolved_questions": row.unresolved_questions_json,
            "artifact_evidence_refs": row.artifact_evidence_refs_json,
            "covered_message_ids": row.covered_message_ids_json,
            "covered_sequence_start": row.covered_sequence_start,
            "covered_sequence_end": row.covered_sequence_end,
            "compression_model": row.compression_model,
            "created_at": _database_utc(row.created_at),
        }
    )


def _event_from_row(row: PixelFlowAgentEventRow) -> AgentEvent:
    return AgentEvent.model_validate(
        {
            "schema_version": row.schema_version,
            "event_id": row.event_id,
            "sequence": row.sequence,
            "cursor": row.cursor,
            "conversation_id": row.conversation_id,
            "run_id": row.run_id,
            "occurred_at": _database_utc(row.occurred_at),
            "type": row.event_type,
            "payload": row.payload_json,
        }
    )


def _operation_from_row(row: PixelFlowAgentOperationRow) -> OperationRecord:
    return OperationRecord.model_validate(
        {
            "job_id": row.job_id,
            "provider_job_id": row.provider_job_id,
            "workflow_id": row.workflow_id,
            "conversation_id": row.conversation_id,
            "stage": row.stage,
            "stage_version": row.stage_version,
            "status": row.status,
            "attempt": row.attempt,
            "request_hash": row.request_hash,
            "idempotency_key": row.idempotency_key,
            "next_poll_at": None if row.next_poll_at is None else _database_utc(row.next_poll_at),
            "lease_owner": row.lease_owner,
            "lease_expires_at": (
                None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)
            ),
            "created_at": _database_utc(row.created_at),
            "updated_at": _database_utc(row.updated_at),
        }
    )


class SQLAgentRuntimeRepository:
    """使用 SQL owner 条件执行所有读取的异步 Repository。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        bind = session_factory.kw.get("bind")
        self._sqlite_turn_write_lock = (
            _SQLITE_ENGINE_WRITE_LOCKS.setdefault(bind, asyncio.Lock())
            if isinstance(bind, AsyncEngine) and bind.dialect.name == "sqlite"
            else None
        )

    async def _insert(self, row: object) -> None:
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise AgentRuntimeRecordConflictError("记录主键或唯一业务键已经被占用") from None

    async def create_workflow(self, user_id: str, record: WorkflowRecord) -> WorkflowRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_workflow(record)
        await self._insert(
            PixelFlowAgentWorkflowRow(
                workflow_id=normalized.workflow_id,
                conversation_id=normalized.conversation_id,
                user_id=owner,
                kind=normalized.kind.value,
                status=normalized.status.value,
                current_stage=normalized.current_stage,
                stage_version=normalized.stage_version,
                creation_contract_snapshot_json=normalized.creation_contract_snapshot,
                pending_external_job_json=(
                    None
                    if normalized.pending_external_job is None
                    else normalized.pending_external_job.model_dump(mode="json")
                ),
                latest_artifact_refs_json=normalized.latest_artifact_refs,
                context_version=normalized.context_version,
                created_at=normalized.created_at,
                updated_at=normalized.updated_at,
            )
        )
        return _clone(normalized)

    async def get_workflow(self, user_id: str, workflow_id: str) -> WorkflowRecord | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentWorkflowRow).where(
            PixelFlowAgentWorkflowRow.user_id == owner,
            PixelFlowAgentWorkflowRow.workflow_id == _require_text("workflow_id", workflow_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _workflow_from_row(row)

    async def list_workflows(self, user_id: str, conversation_id: str) -> list[WorkflowRecord]:
        owner = _require_text("user_id", user_id, 64)
        statement = (
            select(PixelFlowAgentWorkflowRow)
            .where(
                PixelFlowAgentWorkflowRow.user_id == owner,
                PixelFlowAgentWorkflowRow.conversation_id
                == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentWorkflowRow.updated_at.desc(), PixelFlowAgentWorkflowRow.workflow_id.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_workflow_from_row(row) for row in rows]

    async def create_turn(self, user_id: str, record: TurnRecord) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        await self._insert(
            PixelFlowAgentTurnRow(
                turn_id=normalized.turn_id,
                conversation_id=normalized.conversation_id,
                user_id=owner,
                client_input_id=str(normalized.client_input_id),
                status=normalized.status.value,
                target_workflow_id=normalized.target_workflow_id,
                decision_json=None if normalized.decision is None else normalized.decision.model_dump(mode="json"),
                expected_context_version=normalized.expected_context_version,
                created_at=normalized.created_at,
                updated_at=normalized.created_at,
            )
        )
        return _clone(normalized)

    async def enqueue_turn(
        self,
        user_id: str,
        record: TurnRecord,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        statement = (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id
                == normalized.conversation_id,
                PixelFlowAgentTurnRow.client_input_id
                == str(normalized.client_input_id),
            )
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _turn_write_transaction(
                    session,
                    self._sqlite_turn_write_lock,
                ):
                    existing = (
                        await session.scalars(statement)
                    ).one_or_none()
                    if existing is not None:
                        return _turn_from_row(existing)
                    session.add(
                        PixelFlowAgentTurnRow(
                            turn_id=normalized.turn_id,
                            conversation_id=normalized.conversation_id,
                            user_id=owner,
                            client_input_id=str(
                                normalized.client_input_id
                            ),
                            status=normalized.status.value,
                            target_workflow_id=normalized.target_workflow_id,
                            decision_json=(
                                None
                                if normalized.decision is None
                                else normalized.decision.model_dump(
                                    mode="json"
                                )
                            ),
                            expected_context_version=(
                                normalized.expected_context_version
                            ),
                            created_at=normalized.created_at,
                            updated_at=normalized.created_at,
                        )
                    )
        except IntegrityError:
            async with self._session_factory() as session:
                existing = (
                    await session.scalars(statement)
                ).one_or_none()
            if existing is not None:
                return _turn_from_row(existing)
            raise AgentRuntimeRecordConflictError(
                "记录主键或唯一业务键已经被占用"
            ) from None
        return _clone(normalized)

    async def get_turn(self, user_id: str, turn_id: str) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentTurnRow).where(
            PixelFlowAgentTurnRow.user_id == owner,
            PixelFlowAgentTurnRow.turn_id == _require_text("turn_id", turn_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _turn_from_row(row)

    async def get_turn_by_client_input_id(
        self,
        user_id: str,
        conversation_id: str,
        client_input_id: UUID,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentTurnRow).where(
            PixelFlowAgentTurnRow.user_id == owner,
            PixelFlowAgentTurnRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            PixelFlowAgentTurnRow.client_input_id == str(client_input_id),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _turn_from_row(row)

    async def list_turns(self, user_id: str, conversation_id: str) -> list[TurnRecord]:
        owner = _require_text("user_id", user_id, 64)
        statement = (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id
                == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_turn_from_row(row) for row in rows]

    async def claim_next_turn(
        self,
        user_id: str,
        conversation_id: str,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        active_statuses = (
            TurnStatus.ACCEPTED.value,
            TurnStatus.QUEUED.value,
            TurnStatus.PROCESSING.value,
        )
        statement = (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id == conversation,
                PixelFlowAgentTurnRow.status.in_(active_statuses),
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _turn_write_transaction(
                session,
                self._sqlite_turn_write_lock,
            ):
                rows = (await session.scalars(statement)).all()
                if any(
                    row.status == TurnStatus.PROCESSING.value
                    for row in rows
                ):
                    return None
                if not rows:
                    return None
                row = rows[0]
                row.status = TurnStatus.PROCESSING.value
                await session.flush()
                return _turn_from_row(row)

    async def create_summary(self, user_id: str, record: ContextSummary) -> ContextSummary:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_summary(record)
        await self._insert(
            PixelFlowAgentContextSummaryRow(
                summary_id=normalized.summary_id,
                conversation_id=normalized.conversation_id,
                user_id=owner,
                version=normalized.version,
                previous_summary_id=normalized.previous_summary_id,
                content_hash=normalized.content_hash,
                user_goals_json=normalized.user_goals,
                confirmed_decisions_json=normalized.confirmed_decisions,
                negative_constraints_json=normalized.negative_constraints,
                workflow_states_json=normalized.workflow_states,
                unresolved_questions_json=normalized.unresolved_questions,
                artifact_evidence_refs_json=normalized.artifact_evidence_refs,
                covered_message_ids_json=normalized.covered_message_ids,
                covered_sequence_start=normalized.covered_sequence_start,
                covered_sequence_end=normalized.covered_sequence_end,
                compression_model=normalized.compression_model,
                created_at=normalized.created_at,
            )
        )
        return _clone(normalized)

    async def get_summary(self, user_id: str, summary_id: str) -> ContextSummary | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentContextSummaryRow).where(
            PixelFlowAgentContextSummaryRow.user_id == owner,
            PixelFlowAgentContextSummaryRow.summary_id == _require_text("summary_id", summary_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _summary_from_row(row)

    async def list_summaries(self, user_id: str, conversation_id: str) -> list[ContextSummary]:
        owner = _require_text("user_id", user_id, 64)
        statement = (
            select(PixelFlowAgentContextSummaryRow)
            .where(
                PixelFlowAgentContextSummaryRow.user_id == owner,
                PixelFlowAgentContextSummaryRow.conversation_id
                == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentContextSummaryRow.version.asc(), PixelFlowAgentContextSummaryRow.summary_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_summary_from_row(row) for row in rows]

    async def create_event(self, user_id: str, record: AgentEvent) -> AgentEvent:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_event(record)
        await self._insert(
            PixelFlowAgentEventRow(
                schema_version=normalized.schema_version,
                event_id=normalized.event_id,
                sequence=normalized.sequence,
                cursor=normalized.cursor,
                conversation_id=normalized.conversation_id,
                user_id=owner,
                run_id=normalized.run_id,
                occurred_at=normalized.occurred_at,
                event_type=normalized.type.value,
                payload_json=normalized.payload,
                delivery_status="pending",
                delivery_attempts=0,
            )
        )
        return _clone(normalized)

    async def get_event(self, user_id: str, event_id: str) -> AgentEvent | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentEventRow).where(
            PixelFlowAgentEventRow.user_id == owner,
            PixelFlowAgentEventRow.event_id == _require_text("event_id", event_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _event_from_row(row)

    async def list_events(self, user_id: str, conversation_id: str) -> list[AgentEvent]:
        owner = _require_text("user_id", user_id, 64)
        statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.conversation_id
                == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentEventRow.sequence.asc(), PixelFlowAgentEventRow.event_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_event_from_row(row) for row in rows]

    async def create_operation(self, user_id: str, record: OperationRecord) -> OperationRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_operation(record)
        await self._insert(
            PixelFlowAgentOperationRow(
                job_id=normalized.job_id,
                provider_job_id=normalized.provider_job_id,
                workflow_id=normalized.workflow_id,
                conversation_id=normalized.conversation_id,
                user_id=owner,
                stage=normalized.stage,
                stage_version=normalized.stage_version,
                status=normalized.status.value,
                attempt=normalized.attempt,
                request_hash=normalized.request_hash,
                idempotency_key=normalized.idempotency_key,
                next_poll_at=normalized.next_poll_at,
                lease_owner=normalized.lease_owner,
                lease_expires_at=normalized.lease_expires_at,
                created_at=normalized.created_at,
                updated_at=normalized.updated_at,
            )
        )
        return _clone(normalized)

    async def get_operation(self, user_id: str, job_id: str) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentOperationRow).where(
            PixelFlowAgentOperationRow.user_id == owner,
            PixelFlowAgentOperationRow.job_id == _require_text("job_id", job_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _operation_from_row(row)

    async def list_operations(self, user_id: str, conversation_id: str) -> list[OperationRecord]:
        owner = _require_text("user_id", user_id, 64)
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id
                == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentOperationRow.created_at.asc(), PixelFlowAgentOperationRow.job_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_operation_from_row(row) for row in rows]
