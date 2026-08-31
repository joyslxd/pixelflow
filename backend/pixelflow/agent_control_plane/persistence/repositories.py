"""Agent Runtime 五类业务投影的统一 Repository Port 与双实现。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID
from weakref import WeakKeyDictionary

from pydantic import Field, JsonValue
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..contracts import (
    AgentEvent,
    AgentEventType,
    ContextSummary,
    ExternalJobStatus,
    TurnRecord,
    TurnStatus,
    WorkflowRecord,
)
from ..contracts.base import ContractModel
from .models import (
    PixelFlowAgentCompactionLockRow,
    PixelFlowAgentContextSummaryRow,
    PixelFlowAgentEventRow,
    PixelFlowAgentOperationRow,
    PixelFlowAgentTurnRow,
    PixelFlowAgentWorkflowRow,
)

_SQLITE_ENGINE_WRITE_LOCKS: WeakKeyDictionary[AsyncEngine, asyncio.Lock] = WeakKeyDictionary()
_OPERATION_QUOTA_EVENT_ID_PREFIX = "evt_job_quota_"


class AgentRuntimeRecordConflictError(RuntimeError):
    """记录主键或唯一业务键已经被占用。"""


class AgentRuntimeQuotaResumeStaleError(AgentRuntimeRecordConflictError):
    """额度恢复响应已落后于当前权威 Operation，不得登记任何副作用。"""

    reason_code = "video_quota_resume_stale"


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
    quota_pause_revision: int = Field(default=0, ge=0)
    next_poll_at: datetime | None = None
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OperationTerminalEventRecord(ContractModel):
    """原子结束 Operation 时写入 Outbox 的稳定事件内容。"""

    event_id: str = Field(min_length=1)
    cursor: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    occurred_at: datetime
    payload: dict[str, JsonValue]


class OperationQuotaEventRecord(ContractModel):
    """原子暂停或恢复 Operation 时写入的安全非终态事件。"""

    event_id: str = Field(min_length=1)
    cursor: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    occurred_at: datetime
    quota_pause_revision: int = Field(ge=1)
    quota_state: Literal["paused", "resumed"]
    payload: dict[str, JsonValue]


class OwnedOperationRecord(ContractModel):
    """仅供进程级恢复扫描使用的 Operation 所有者快照。"""

    user_id: str = Field(min_length=1)
    operation: OperationRecord


class OwnedOperationQuotaEvent(ContractModel):
    """恢复扫描使用的 Operation、quota event 与所有者快照。"""

    user_id: str = Field(min_length=1)
    operation: OperationRecord
    event: AgentEvent


class EventDeliveryClaim(ContractModel):
    """返回 Event Outbox 当前租约及投递尝试次数。"""

    event: AgentEvent
    delivery_attempts: int = Field(ge=1)
    lease_owner: str = Field(min_length=1)
    lease_expires_at: datetime


@dataclass
class _MemoryEventDeliveryState:
    """保存内存实现中不进入线合同的 Event 投递状态。"""

    status: str = "pending"
    delivery_attempts: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    published_at: datetime | None = None


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

    async def get_latest_event(self, user_id: str, conversation_id: str) -> AgentEvent | None: ...

    async def list_events(self, user_id: str, conversation_id: str) -> list[AgentEvent]: ...

    async def list_events_after_cursor(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None,
        limit: int = 100,
    ) -> list[AgentEvent] | None: ...

    async def claim_next_event(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None: ...

    async def complete_event_delivery(
        self,
        user_id: str,
        event_id: str,
        *,
        lease_owner: str,
        published_at: datetime,
    ) -> AgentEvent | None: ...

    async def create_operation(self, user_id: str, record: OperationRecord) -> OperationRecord: ...

    async def get_operation(self, user_id: str, job_id: str) -> OperationRecord | None: ...

    async def get_operation_by_idempotency_key(
        self,
        user_id: str,
        idempotency_key: str,
    ) -> OperationRecord | None: ...

    async def list_operations(self, user_id: str, conversation_id: str) -> list[OperationRecord]: ...

    async def list_due_operations(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationRecord]: ...

    async def list_pending_operation_completions(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationRecord]: ...

    async def list_pending_operation_quota_events(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationQuotaEvent]: ...

    async def claim_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None: ...

    async def complete_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None: ...

    async def release_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationRecord | None: ...

    async def claim_operation_lease(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None: ...

    async def heartbeat_operation_lease(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None: ...

    async def schedule_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None: ...

    async def pause_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationRecord | None: ...

    async def resume_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        now: datetime,
    ) -> OperationRecord | None: ...

    async def pause_operation_for_quota(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        lease_owner: str,
        expected_revision: int,
        now: datetime,
        event: OperationQuotaEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]: ...

    async def resume_operation_from_quota(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        workflow_id: str,
        expected_revision: int,
        now: datetime,
        delivery_lease_owner: str,
        delivery_lease_expires_at: datetime,
        event: OperationQuotaEventRecord,
    ) -> tuple[OperationRecord, EventDeliveryClaim]: ...

    async def finalize_operation_terminal(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        terminal_status: ExternalJobStatus,
        lease_owner: str,
        now: datetime,
        event: OperationTerminalEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]: ...

    async def finalize_operation_start_terminal(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        terminal_status: ExternalJobStatus,
        lease_owner: str,
        now: datetime,
        event: OperationTerminalEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]: ...

    async def claim_operation_completion_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None: ...

    async def claim_operation_quota_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        quota_pause_revision: int,
        quota_state: Literal["paused", "resumed"],
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None: ...


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


def _event_query_limit(limit: int) -> int:
    if isinstance(limit, bool) or limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    return limit


def _lease_window(
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
) -> tuple[str, datetime, datetime]:
    owner = _require_text("lease_owner", lease_owner, 128)
    normalized_now = _normalize_datetime("now", now)
    normalized_expiry = _normalize_datetime(
        "lease_expires_at",
        lease_expires_at,
    )
    if normalized_expiry <= normalized_now:
        raise ValueError("lease_expires_at must be later than now")
    return owner, normalized_now, normalized_expiry


def _operation_poll_schedule(
    now: datetime,
    next_poll_at: datetime,
) -> tuple[datetime, datetime]:
    normalized_now = _normalize_datetime("now", now)
    normalized_next_poll = _normalize_datetime(
        "next_poll_at",
        next_poll_at,
    )
    if normalized_next_poll <= normalized_now:
        raise ValueError("next_poll_at must be later than now")
    return normalized_now, normalized_next_poll


def _recovery_scan_window(
    now: datetime,
    limit: int,
) -> tuple[datetime, int]:
    normalized_now = _normalize_datetime("now", now)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    return normalized_now, limit


@asynccontextmanager
async def _repository_write_transaction(
    session: AsyncSession,
    sqlite_write_lock: asyncio.Lock | None,
) -> AsyncIterator[None]:
    if session.get_bind().dialect.name == "sqlite":
        if sqlite_write_lock is None:
            raise RuntimeError("SQLite Repository 写事务缺少 Engine 级进程内锁")
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
                "quota_pause_revision": normalized.quota_pause_revision,
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


def _normalize_operation_terminal_event(
    record: OperationTerminalEventRecord,
) -> OperationTerminalEventRecord:
    normalized = _clone(record)
    return _clone(
        normalized.model_copy(
            update={
                "event_id": _require_text(
                    "event_id",
                    normalized.event_id,
                    64,
                ),
                "cursor": _require_text(
                    "cursor",
                    normalized.cursor,
                    128,
                ),
                "run_id": _require_text(
                    "run_id",
                    normalized.run_id,
                    64,
                ),
                "occurred_at": _normalize_datetime(
                    "occurred_at",
                    normalized.occurred_at,
                ),
            }
        )
    )


def _normalize_operation_quota_event(
    record: OperationQuotaEventRecord,
) -> OperationQuotaEventRecord:
    normalized = _clone(record)
    return _clone(
        normalized.model_copy(
            update={
                "event_id": _require_text(
                    "event_id",
                    normalized.event_id,
                    64,
                ),
                "cursor": _require_text(
                    "cursor",
                    normalized.cursor,
                    128,
                ),
                "run_id": _require_text(
                    "run_id",
                    normalized.run_id,
                    64,
                ),
                "occurred_at": _normalize_datetime(
                    "occurred_at",
                    normalized.occurred_at,
                ),
            }
        )
    )


def _require_quota_revision(field: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _require_quota_state(value: str) -> Literal["paused", "resumed"]:
    if value not in {"paused", "resumed"}:
        raise ValueError("quota_state must be paused or resumed")
    return value


def _require_quota_event_contract(
    record: OperationQuotaEventRecord,
    *,
    job_id: str,
    quota_pause_revision: int,
    quota_state: Literal["paused", "resumed"],
) -> None:
    payload_revision = record.payload.get("quota_pause_revision")
    if (
        not record.event_id.startswith(_OPERATION_QUOTA_EVENT_ID_PREFIX)
        or record.quota_pause_revision != quota_pause_revision
        or record.quota_state != quota_state
        or record.payload.get("job_id") != job_id
        or isinstance(payload_revision, bool)
        or payload_revision != quota_pause_revision
        or record.payload.get("quota_state") != quota_state
    ):
        raise AgentRuntimeRecordConflictError("Operation quota 事件合同不一致")


def _require_operation_terminal_status(
    status: ExternalJobStatus,
) -> ExternalJobStatus:
    if status not in {
        ExternalJobStatus.SUCCEEDED,
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }:
        raise AgentRuntimeRecordConflictError("Operation 目标不是允许的终态")
    return status


def _build_operation_completion_event(
    *,
    conversation_id: str,
    sequence: int,
    record: OperationTerminalEventRecord,
) -> AgentEvent:
    return _normalize_event(
        AgentEvent(
            event_id=record.event_id,
            sequence=sequence,
            cursor=record.cursor,
            conversation_id=conversation_id,
            run_id=record.run_id,
            occurred_at=record.occurred_at,
            type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
            payload=record.payload,
        )
    )


def _operation_completion_event_matches(
    event: AgentEvent,
    *,
    conversation_id: str,
    record: OperationTerminalEventRecord,
) -> bool:
    return (
        event.event_id == record.event_id
        and event.cursor == record.cursor
        and event.conversation_id == conversation_id
        and event.run_id == record.run_id
        and event.type is AgentEventType.EXTERNAL_JOB_STATE_CHANGED
        and event.payload == record.payload
    )


def _build_operation_quota_event(
    *,
    conversation_id: str,
    sequence: int,
    record: OperationQuotaEventRecord,
) -> AgentEvent:
    return _normalize_event(
        AgentEvent(
            event_id=record.event_id,
            sequence=sequence,
            cursor=record.cursor,
            conversation_id=conversation_id,
            run_id=record.run_id,
            occurred_at=record.occurred_at,
            type=AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED,
            payload=record.payload,
        )
    )


def _operation_quota_event_matches(
    event: AgentEvent,
    *,
    conversation_id: str,
    record: OperationQuotaEventRecord,
) -> bool:
    return (
        event.event_id == record.event_id
        and event.cursor == record.cursor
        and event.conversation_id == conversation_id
        and event.run_id == record.run_id
        and event.type is AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
        and event.payload == record.payload
    )


def _is_operation_quota_event(event: AgentEvent) -> bool:
    """识别由 quota 原子事务写入的非终态内部事件。"""

    return (
        event.type is AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
        and event.event_id.startswith(_OPERATION_QUOTA_EVENT_ID_PREFIX)
    )


def _is_operation_internal_event(event: AgentEvent) -> bool:
    """识别必须先恢复 Workflow、不能被通用发布器抢占的内部事件。"""

    return (
        event.type is AgentEventType.EXTERNAL_JOB_STATE_CHANGED
        and event.event_id.startswith("evt_job_done_")
    ) or _is_operation_quota_event(event)


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
        self._event_delivery: dict[
            tuple[str, str],
            _MemoryEventDeliveryState,
        ] = {}
        self._event_write_lock = asyncio.Lock()
        self._operations: dict[tuple[str, str], OperationRecord] = {}
        self._operation_ids: set[str] = set()
        self._operation_idempotency_keys: set[str] = set()
        self._operation_stage_keys: set[tuple[str, str, int, int]] = set()
        self._operation_write_lock = asyncio.Lock()

    def _conversation_authority_owners(self, conversation_id: str) -> set[str]:
        """汇总会话现有权威记录的所有者，防止普通 Event 抢占归属。"""

        owners: set[str] = set()
        for records in (
            self._operations,
            self._workflows,
            self._turns,
            self._summaries,
            self._events,
        ):
            owners.update(
                record_owner
                for (record_owner, _), record in records.items()
                if record.conversation_id == conversation_id
            )
        return owners

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
        records = [record for (record_owner, _), record in self._workflows.items() if record_owner == owner and record.conversation_id == conversation]
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
                raise AgentRuntimeRecordConflictError("Turn 幂等键已经被其他所有者占用")
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
        owner_keys = [owner_key for owner_key, record in self._turns.items() if owner_key[0] == owner and record.conversation_id == conversation]
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
        if any(self._turns[owner_key].status is TurnStatus.PROCESSING for owner_key in owner_keys):
            return None
        if not owner_keys:
            return None
        owner_key = owner_keys[0]
        claimed = self._turns[owner_key].model_copy(update={"status": TurnStatus.PROCESSING})
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
        records = [record for (record_owner, _), record in self._summaries.items() if record_owner == owner and record.conversation_id == conversation]
        records.sort(key=lambda record: (record.version, record.summary_id))
        return [_clone(record) for record in records]

    async def create_event(self, user_id: str, record: AgentEvent) -> AgentEvent:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_event(record)
        cursor_key = (normalized.conversation_id, normalized.cursor)
        async with self._event_write_lock:
            authority_owners = self._conversation_authority_owners(
                normalized.conversation_id
            )
            if authority_owners - {owner}:
                raise AgentRuntimeRecordConflictError(
                    "AgentEvent conversation 已经属于其他所有者"
                )
            conversation_records = [(record_owner, existing) for (record_owner, _), existing in self._events.items() if existing.conversation_id == normalized.conversation_id]
            if any(record_owner != owner for record_owner, _ in conversation_records):
                raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
            expected_sequence = 1 if not conversation_records else max(existing.sequence for _, existing in conversation_records) + 1
            if normalized.sequence > expected_sequence:
                # 跳号禁止：只允许连续追加。
                raise AgentRuntimeRecordConflictError("AgentEvent sequence 必须连续递增")
            if normalized.sequence < expected_sequence:
                # 调用方在锁外读到的 next sequence 已过期（并发 append）；锁内改写为期望值。
                normalized = normalized.model_copy(update={"sequence": expected_sequence})
            sequence_key = (normalized.conversation_id, normalized.sequence)
            if normalized.event_id in self._event_ids or sequence_key in self._event_sequence_keys or cursor_key in self._event_cursor_keys:
                raise AgentRuntimeRecordConflictError("AgentEvent 记录已存在")
            owner_key = (owner, normalized.event_id)
            self._event_ids.add(normalized.event_id)
            self._event_sequence_keys.add(sequence_key)
            self._event_cursor_keys.add(cursor_key)
            self._events[owner_key] = _clone(normalized)
            self._event_delivery[owner_key] = _MemoryEventDeliveryState()
        return _clone(normalized)

    async def get_event(self, user_id: str, event_id: str) -> AgentEvent | None:
        owner = _require_text("user_id", user_id, 64)
        record = self._events.get((owner, _require_text("event_id", event_id, 64)))
        return None if record is None else _clone(record)

    async def get_latest_event(self, user_id: str, conversation_id: str) -> AgentEvent | None:
        """按会话序号回读最后一条公开事件，供刷新恢复当前 Harness Run。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [
            record
            for (record_owner, _), record in self._events.items()
            if record_owner == owner and record.conversation_id == conversation
        ]
        if not records:
            return None
        return _clone(max(records, key=lambda record: (record.sequence, record.event_id)))

    async def list_events(self, user_id: str, conversation_id: str) -> list[AgentEvent]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [record for (record_owner, _), record in self._events.items() if record_owner == owner and record.conversation_id == conversation]
        records.sort(key=lambda record: (record.sequence, record.event_id))
        return [_clone(record) for record in records]

    async def list_events_after_cursor(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None,
        limit: int = 100,
    ) -> list[AgentEvent] | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        page_size = _event_query_limit(limit)
        records = [record for (record_owner, _), record in self._events.items() if record_owner == owner and record.conversation_id == conversation]
        records.sort(key=lambda record: (record.sequence, record.event_id))
        if cursor is None:
            return [_clone(record) for record in records[:page_size]]
        normalized_cursor = _require_text("cursor", cursor, 128)
        anchor = next(
            (record for record in records if record.cursor == normalized_cursor),
            None,
        )
        if anchor is None:
            return None
        return [_clone(record) for record in records if record.sequence > anchor.sequence][:page_size]

    async def claim_next_event(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        async with self._event_write_lock:
            owner_keys = [owner_key for owner_key, record in self._events.items() if owner_key[0] == owner and record.conversation_id == conversation and self._event_delivery[owner_key].status != "published"]
            owner_keys.sort(
                key=lambda owner_key: (
                    self._events[owner_key].sequence,
                    self._events[owner_key].event_id,
                )
            )
            if not owner_keys:
                return None
            owner_key = owner_keys[0]
            if _is_operation_internal_event(self._events[owner_key]):
                return None
            state = self._event_delivery[owner_key]
            if state.status == "delivering":
                if state.lease_expires_at is None or state.lease_expires_at > normalized_now:
                    return None
            state.status = "delivering"
            state.delivery_attempts += 1
            state.lease_owner = worker
            state.lease_expires_at = normalized_expiry
            return EventDeliveryClaim(
                event=_clone(self._events[owner_key]),
                delivery_attempts=state.delivery_attempts,
                lease_owner=worker,
                lease_expires_at=normalized_expiry,
            )

    async def complete_event_delivery(
        self,
        user_id: str,
        event_id: str,
        *,
        lease_owner: str,
        published_at: datetime,
    ) -> AgentEvent | None:
        owner = _require_text("user_id", user_id, 64)
        owner_key = (
            owner,
            _require_text("event_id", event_id, 64),
        )
        worker = _require_text("lease_owner", lease_owner, 128)
        completed_at = _normalize_datetime(
            "published_at",
            published_at,
        )
        async with self._event_write_lock:
            event = self._events.get(owner_key)
            if event is None:
                return None
            state = self._event_delivery[owner_key]
            if state.status == "published":
                return _clone(event)
            if state.status != "delivering" or state.lease_owner != worker or state.lease_expires_at is None or completed_at >= state.lease_expires_at:
                raise AgentRuntimeRecordConflictError("AgentEvent 投递租约无效")
            state.status = "published"
            state.published_at = completed_at
            return _clone(event)

    async def create_operation(self, user_id: str, record: OperationRecord) -> OperationRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_operation(record)
        stage_key = (
            normalized.workflow_id,
            normalized.stage,
            normalized.stage_version,
            normalized.attempt,
        )
        async with self._operation_write_lock:
            if normalized.job_id in self._operation_ids or normalized.idempotency_key in self._operation_idempotency_keys or stage_key in self._operation_stage_keys:
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

    async def get_operation_by_idempotency_key(
        self,
        user_id: str,
        idempotency_key: str,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        key = _require_text("idempotency_key", idempotency_key, 255)
        for (record_owner, _), record in self._operations.items():
            if record_owner == owner and record.idempotency_key == key:
                return _clone(record)
        return None

    async def list_operations(self, user_id: str, conversation_id: str) -> list[OperationRecord]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        records = [record for (record_owner, _), record in self._operations.items() if record_owner == owner and record.conversation_id == conversation]
        records.sort(key=lambda record: (record.created_at, record.job_id))
        return [_clone(record) for record in records]

    async def list_due_operations(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationRecord]:
        """按稳定顺序返回无有效租约的到期轮询任务。"""

        normalized_now, normalized_limit = _recovery_scan_window(now, limit)
        candidates: list[OwnedOperationRecord] = []
        async with self._operation_write_lock:
            async with self._event_write_lock:
                for (owner, _), record in self._operations.items():
                    lease_pair_valid = (record.lease_owner is None) == (record.lease_expires_at is None)
                    lease_available = record.lease_expires_at is None or record.lease_expires_at <= normalized_now
                    has_pending_quota_event = any(
                        event_owner == owner
                        and event.payload.get("job_id") == record.job_id
                        and _is_operation_quota_event(event)
                        and delivery.status != "published"
                        for (event_owner, event_id), event in self._events.items()
                        if (delivery := self._event_delivery[(event_owner, event_id)])
                    )
                    if (
                        record.status is ExternalJobStatus.POLLING
                        and record.provider_job_id is not None
                        and record.next_poll_at is not None
                        and record.next_poll_at <= normalized_now
                        and lease_pair_valid
                        and lease_available
                        and not has_pending_quota_event
                    ):
                        candidates.append(
                            OwnedOperationRecord(
                                user_id=owner,
                                operation=_clone(record),
                            )
                        )
        candidates.sort(
            key=lambda candidate: (
                candidate.operation.next_poll_at,
                candidate.operation.created_at,
                candidate.operation.job_id,
            )
        )
        return [_clone(candidate) for candidate in candidates[:normalized_limit]]

    async def list_pending_operation_completions(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationRecord]:
        """返回尚未投递或投递租约已过期的 Operation 完成事件。"""

        normalized_now, normalized_limit = _recovery_scan_window(now, limit)
        candidates: list[tuple[int, OwnedOperationRecord]] = []
        async with self._operation_write_lock:
            async with self._event_write_lock:
                for event_key, event in self._events.items():
                    if event.type is not AgentEventType.EXTERNAL_JOB_STATE_CHANGED:
                        continue
                    state = self._event_delivery[event_key]
                    if state.status == "published":
                        continue
                    if state.status == "delivering" and state.lease_expires_at is not None and state.lease_expires_at > normalized_now:
                        continue
                    job_id = event.payload.get("job_id")
                    if not isinstance(job_id, str):
                        continue
                    owner = event_key[0]
                    operation = self._operations.get((owner, job_id))
                    if (
                        operation is None
                        or operation.conversation_id != event.conversation_id
                        or operation.status
                        not in {
                            ExternalJobStatus.SUCCEEDED,
                            ExternalJobStatus.FAILED,
                            ExternalJobStatus.TIMEOUT,
                            ExternalJobStatus.EXPIRED,
                        }
                        or event.payload.get("status") != operation.status.value
                    ):
                        continue
                    candidates.append(
                        (
                            event.sequence,
                            OwnedOperationRecord(
                                user_id=owner,
                                operation=_clone(operation),
                            ),
                        )
                    )
        candidates.sort(key=lambda candidate: candidate[0])
        return [_clone(candidate) for _, candidate in candidates[:normalized_limit]]

    async def list_pending_operation_quota_events(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationQuotaEvent]:
        """返回尚未投递或投递租约已过期的 quota 内部事件。"""

        normalized_now, normalized_limit = _recovery_scan_window(now, limit)
        candidates: list[tuple[int, OwnedOperationQuotaEvent]] = []
        async with self._operation_write_lock:
            async with self._event_write_lock:
                for event_key, event in self._events.items():
                    if not _is_operation_quota_event(event):
                        continue
                    state = self._event_delivery[event_key]
                    if state.status == "published":
                        continue
                    if state.status == "delivering" and state.lease_expires_at is not None and state.lease_expires_at > normalized_now:
                        continue
                    job_id = event.payload.get("job_id")
                    revision = event.payload.get("quota_pause_revision")
                    quota_state = event.payload.get("quota_state")
                    if (
                        not isinstance(job_id, str)
                        or isinstance(revision, bool)
                        or not isinstance(revision, int)
                        or quota_state not in {"paused", "resumed"}
                    ):
                        continue
                    owner = event_key[0]
                    operation = self._operations.get((owner, job_id))
                    if (
                        operation is None
                        or operation.conversation_id != event.conversation_id
                        or operation.status is not ExternalJobStatus.POLLING
                        or operation.provider_job_id is None
                        or operation.quota_pause_revision != revision
                    ):
                        continue
                    candidates.append(
                        (
                            event.sequence,
                            OwnedOperationQuotaEvent(
                                user_id=owner,
                                operation=_clone(operation),
                                event=_clone(event),
                            ),
                        )
                    )
        candidates.sort(key=lambda candidate: candidate[0])
        return [_clone(candidate) for _, candidate in candidates[:normalized_limit]]

    async def claim_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        """仅让一个请求领取尚未调用 Provider 的 created Operation。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if (
                record is None
                or record.conversation_id != conversation
                or record.status is not ExternalJobStatus.CREATED
                or record.provider_job_id is not None
                or (record.lease_owner is None) != (record.lease_expires_at is None)
                or (record.lease_expires_at is not None and record.lease_expires_at > normalized_now)
            ):
                return None
            claimed = _clone(
                record.model_copy(
                    update={
                        "lease_owner": worker,
                        "lease_expires_at": normalized_expiry,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = claimed
            return _clone(claimed)

    async def complete_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None:
        """绑定原 provider job ID，并把 start lease 转为轮询计划。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text("provider_job_id", provider_job_id, 128)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now, normalized_next_poll = _operation_poll_schedule(
            now,
            next_poll_at,
        )
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if (
                record is None
                or record.conversation_id != conversation
                or record.status is not ExternalJobStatus.CREATED
                or record.provider_job_id is not None
                or record.lease_owner != worker
                or record.lease_expires_at is None
                or record.lease_expires_at <= normalized_now
            ):
                return None
            started = _clone(
                record.model_copy(
                    update={
                        "provider_job_id": provider_id,
                        "status": ExternalJobStatus.POLLING,
                        "next_poll_at": normalized_next_poll,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = started
            return _clone(started)

    async def release_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationRecord | None:
        """在明确未创建 provider job 时释放 start lease，等待用户重试。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now = _normalize_datetime("now", now)
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if (
                record is None
                or record.conversation_id != conversation
                or record.status is not ExternalJobStatus.CREATED
                or record.provider_job_id is not None
                or record.lease_owner != worker
                or record.lease_expires_at is None
                or record.lease_expires_at <= normalized_now
            ):
                return None
            released = _clone(
                record.model_copy(
                    update={
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = released
            return _clone(released)

    async def claim_operation_lease(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if record is None or record.conversation_id != conversation or record.status is not ExternalJobStatus.POLLING or record.provider_job_id is None or record.next_poll_at is None or record.next_poll_at > normalized_now:
                return None
            if (record.lease_owner is None) != (record.lease_expires_at is None):
                return None
            if record.lease_owner == worker and record.lease_expires_at is not None and record.lease_expires_at > normalized_now:
                return _clone(record)
            if record.lease_expires_at is not None and record.lease_expires_at > normalized_now:
                return None
            claimed = _clone(
                record.model_copy(
                    update={
                        "lease_owner": worker,
                        "lease_expires_at": normalized_expiry,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = claimed
            return _clone(claimed)

    async def heartbeat_operation_lease(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if (
                record is None
                or record.conversation_id != conversation
                or record.status is not ExternalJobStatus.POLLING
                or record.lease_owner != worker
                or record.lease_expires_at is None
                or record.lease_expires_at <= normalized_now
                or normalized_expiry <= record.lease_expires_at
            ):
                return None
            extended = _clone(
                record.model_copy(
                    update={
                        "lease_expires_at": normalized_expiry,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = extended
            return _clone(extended)

    async def schedule_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now, normalized_next_poll = _operation_poll_schedule(
            now,
            next_poll_at,
        )
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if record is None or record.conversation_id != conversation or record.status is not ExternalJobStatus.POLLING or record.lease_owner != worker or record.lease_expires_at is None or record.lease_expires_at <= normalized_now:
                return None
            scheduled = _clone(
                record.model_copy(
                    update={
                        "next_poll_at": normalized_next_poll,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = scheduled
            return _clone(scheduled)

    async def pause_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationRecord | None:
        """额度不足时保留原 job，并停止自动轮询。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now = _normalize_datetime("now", now)
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if (
                record is None
                or record.conversation_id != conversation
                or record.status is not ExternalJobStatus.POLLING
                or record.provider_job_id is None
                or record.lease_owner != worker
                or record.lease_expires_at is None
                or record.lease_expires_at <= normalized_now
            ):
                return None
            paused = _clone(
                record.model_copy(
                    update={
                        "next_poll_at": None,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = paused
            return _clone(paused)

    async def resume_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        now: datetime,
    ) -> OperationRecord | None:
        """由用户动作重新安排额度暂停的原 provider job。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        normalized_now = _normalize_datetime("now", now)
        owner_key = (owner, operation_id)
        async with self._operation_write_lock:
            record = self._operations.get(owner_key)
            if (
                record is None
                or record.conversation_id != conversation
                or record.status is not ExternalJobStatus.POLLING
                or record.provider_job_id is None
                or record.next_poll_at is not None
                or record.lease_owner is not None
                or record.lease_expires_at is not None
            ):
                return None
            resumed = _clone(
                record.model_copy(
                    update={
                        "next_poll_at": normalized_now,
                        "updated_at": normalized_now,
                    }
                )
            )
            self._operations[owner_key] = resumed
            return _clone(resumed)

    async def pause_operation_for_quota(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        lease_owner: str,
        expected_revision: int,
        now: datetime,
        event: OperationQuotaEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]:
        """在同一内存临界区递增暂停代次并写入唯一 quota 事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text("provider_job_id", provider_job_id, 128)
        worker = _require_text("lease_owner", lease_owner, 128)
        revision = _require_quota_revision("expected_revision", expected_revision)
        paused_at = _normalize_datetime("now", now)
        event_record = _normalize_operation_quota_event(event)
        _require_quota_event_contract(
            event_record,
            job_id=operation_id,
            quota_pause_revision=revision + 1,
            quota_state="paused",
        )
        operation_key = (owner, operation_id)
        event_key = (owner, event_record.event_id)

        async with self._operation_write_lock:
            async with self._event_write_lock:
                current = self._operations.get(operation_key)
                if current is None or current.conversation_id != conversation:
                    raise AgentRuntimeRecordConflictError("Operation 不存在或不属于当前会话")
                if current.quota_pause_revision == revision + 1:
                    existing_event = self._events.get(event_key)
                    if existing_event is not None and _operation_quota_event_matches(
                        existing_event,
                        conversation_id=conversation,
                        record=event_record,
                    ):
                        return _clone(current), _clone(existing_event)
                    raise AgentRuntimeRecordConflictError("Operation quota 暂停重放事件不一致")
                if (
                    current.quota_pause_revision != revision
                    or current.status is not ExternalJobStatus.POLLING
                    or current.provider_job_id != provider_id
                    or current.lease_owner != worker
                    or current.lease_expires_at is None
                    or current.lease_expires_at <= paused_at
                ):
                    raise AgentRuntimeRecordConflictError("Operation quota pause CAS 冲突")

                conversation_events = [
                    (record_owner, stored_event)
                    for (record_owner, _), stored_event in self._events.items()
                    if stored_event.conversation_id == conversation
                ]
                if any(record_owner != owner for record_owner, _ in conversation_events):
                    raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                sequence = 1 if not conversation_events else max(
                    stored_event.sequence for _, stored_event in conversation_events
                ) + 1
                cursor_key = (conversation, event_record.cursor)
                sequence_key = (conversation, sequence)
                if event_record.event_id in self._event_ids or sequence_key in self._event_sequence_keys or cursor_key in self._event_cursor_keys:
                    raise AgentRuntimeRecordConflictError("Operation quota 事件身份已被占用")

                quota_event = _build_operation_quota_event(
                    conversation_id=conversation,
                    sequence=sequence,
                    record=event_record,
                )
                paused = _clone(
                    current.model_copy(
                        update={
                            "quota_pause_revision": revision + 1,
                            "next_poll_at": None,
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "updated_at": paused_at,
                        }
                    )
                )
                self._operations[operation_key] = paused
                self._event_ids.add(quota_event.event_id)
                self._event_sequence_keys.add(sequence_key)
                self._event_cursor_keys.add(cursor_key)
                self._events[event_key] = _clone(quota_event)
                self._event_delivery[event_key] = _MemoryEventDeliveryState()
                return _clone(paused), _clone(quota_event)

    async def resume_operation_from_quota(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        workflow_id: str,
        expected_revision: int,
        now: datetime,
        delivery_lease_owner: str,
        delivery_lease_expires_at: datetime,
        event: OperationQuotaEventRecord,
    ) -> tuple[OperationRecord, EventDeliveryClaim]:
        """原子恢复原 Provider job，并为 resume 事件预占投递租约。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        workflow = _require_text("workflow_id", workflow_id, 64)
        revision = _require_quota_revision("expected_revision", expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be at least 1")
        worker, resumed_at, normalized_expiry = _lease_window(
            delivery_lease_owner,
            now,
            delivery_lease_expires_at,
        )
        event_record = _normalize_operation_quota_event(event)
        _require_quota_event_contract(
            event_record,
            job_id=operation_id,
            quota_pause_revision=revision,
            quota_state="resumed",
        )
        operation_key = (owner, operation_id)
        event_key = (owner, event_record.event_id)

        async with self._operation_write_lock:
            async with self._event_write_lock:
                current = self._operations.get(operation_key)
                if current is None or current.conversation_id != conversation:
                    raise AgentRuntimeRecordConflictError("Operation 不存在或不属于当前会话")
                existing_event = self._events.get(event_key)
                if existing_event is not None:
                    state = self._event_delivery[event_key]
                    if (
                        current.status is ExternalJobStatus.POLLING
                        and current.provider_job_id is not None
                        and current.workflow_id == workflow
                        and current.quota_pause_revision == revision
                        and current.next_poll_at is not None
                        and current.lease_owner is None
                        and current.lease_expires_at is None
                        and _operation_quota_event_matches(
                            existing_event,
                            conversation_id=conversation,
                            record=event_record,
                        )
                        and state.status == "delivering"
                        and state.delivery_attempts == 1
                        and state.lease_owner == worker
                        and state.lease_expires_at == normalized_expiry
                        and state.lease_expires_at > resumed_at
                    ):
                        return _clone(current), EventDeliveryClaim(
                            event=_clone(existing_event),
                            delivery_attempts=1,
                            lease_owner=worker,
                            lease_expires_at=normalized_expiry,
                        )
                    raise AgentRuntimeRecordConflictError("Operation quota 恢复重放事件或租约不一致")
                if (
                    current.status is not ExternalJobStatus.POLLING
                    or current.provider_job_id is None
                    or current.next_poll_at is not None
                    or current.lease_owner is not None
                    or current.lease_expires_at is not None
                    or current.workflow_id != workflow
                    or current.quota_pause_revision != revision
                ):
                    raise AgentRuntimeRecordConflictError("Operation quota resume CAS 冲突")

                conversation_events = [
                    (record_owner, stored_event)
                    for (record_owner, _), stored_event in self._events.items()
                    if stored_event.conversation_id == conversation
                ]
                if any(record_owner != owner for record_owner, _ in conversation_events):
                    raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                sequence = 1 if not conversation_events else max(
                    stored_event.sequence for _, stored_event in conversation_events
                ) + 1
                cursor_key = (conversation, event_record.cursor)
                sequence_key = (conversation, sequence)
                if event_record.event_id in self._event_ids or sequence_key in self._event_sequence_keys or cursor_key in self._event_cursor_keys:
                    raise AgentRuntimeRecordConflictError("Operation quota 事件身份已被占用")

                quota_event = _build_operation_quota_event(
                    conversation_id=conversation,
                    sequence=sequence,
                    record=event_record,
                )
                resumed = _clone(
                    current.model_copy(
                        update={
                            "next_poll_at": resumed_at,
                            "updated_at": resumed_at,
                        }
                    )
                )
                self._operations[operation_key] = resumed
                self._event_ids.add(quota_event.event_id)
                self._event_sequence_keys.add(sequence_key)
                self._event_cursor_keys.add(cursor_key)
                self._events[event_key] = _clone(quota_event)
                self._event_delivery[event_key] = _MemoryEventDeliveryState(
                    status="delivering",
                    delivery_attempts=1,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )
                return _clone(resumed), EventDeliveryClaim(
                    event=_clone(quota_event),
                    delivery_attempts=1,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )

    async def finalize_operation_start_terminal(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        terminal_status: ExternalJobStatus,
        lease_owner: str,
        now: datetime,
        event: OperationTerminalEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]:
        """把同步Provider结果从start租约直接原子提交为终态和完成事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text("provider_job_id", provider_job_id, 128)
        worker = _require_text("lease_owner", lease_owner, 128)
        completed_at = _normalize_datetime("now", now)
        target = _require_operation_terminal_status(terminal_status)
        event_record = _normalize_operation_terminal_event(event)
        operation_key = (owner, operation_id)
        event_key = (owner, event_record.event_id)

        async with self._operation_write_lock:
            async with self._event_write_lock:
                current = self._operations.get(operation_key)
                if current is None or current.conversation_id != conversation:
                    raise AgentRuntimeRecordConflictError("Operation 不存在或不属于当前会话")
                if (
                    current.status is not ExternalJobStatus.CREATED
                    or current.provider_job_id is not None
                    or current.lease_owner != worker
                    or current.lease_expires_at is None
                    or current.lease_expires_at <= completed_at
                ):
                    raise AgentRuntimeRecordConflictError("Operation start租约无效")

                conversation_events = [
                    (record_owner, stored_event)
                    for (record_owner, _), stored_event in self._events.items()
                    if stored_event.conversation_id == conversation
                ]
                if any(record_owner != owner for record_owner, _ in conversation_events):
                    raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                sequence = 1 if not conversation_events else max(
                    stored_event.sequence for _, stored_event in conversation_events
                ) + 1
                cursor_key = (conversation, event_record.cursor)
                sequence_key = (conversation, sequence)
                if (
                    event_record.event_id in self._event_ids
                    or sequence_key in self._event_sequence_keys
                    or cursor_key in self._event_cursor_keys
                ):
                    raise AgentRuntimeRecordConflictError("Operation 完成事件身份已被占用")

                completion_event = _build_operation_completion_event(
                    conversation_id=conversation,
                    sequence=sequence,
                    record=event_record,
                )
                completed_operation = _clone(
                    current.model_copy(
                        update={
                            "provider_job_id": provider_id,
                            "status": target,
                            "next_poll_at": None,
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "updated_at": completed_at,
                        }
                    )
                )
                self._operations[operation_key] = completed_operation
                self._event_ids.add(completion_event.event_id)
                self._event_sequence_keys.add(sequence_key)
                self._event_cursor_keys.add(cursor_key)
                self._events[event_key] = _clone(completion_event)
                self._event_delivery[event_key] = _MemoryEventDeliveryState()
                return _clone(completed_operation), _clone(completion_event)

    async def finalize_operation_terminal(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        terminal_status: ExternalJobStatus,
        lease_owner: str,
        now: datetime,
        event: OperationTerminalEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]:
        """原子保存 Operation 终态和唯一完成事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text(
            "provider_job_id",
            provider_job_id,
            128,
        )
        worker = _require_text("lease_owner", lease_owner, 128)
        completed_at = _normalize_datetime("now", now)
        target = _require_operation_terminal_status(terminal_status)
        event_record = _normalize_operation_terminal_event(event)
        operation_key = (owner, operation_id)
        event_key = (owner, event_record.event_id)

        async with self._operation_write_lock:
            async with self._event_write_lock:
                current = self._operations.get(operation_key)
                if current is None or current.conversation_id != conversation:
                    raise AgentRuntimeRecordConflictError("Operation 不存在或不属于当前会话")
                if current.provider_job_id != provider_id:
                    raise AgentRuntimeRecordConflictError("Operation provider job ID 不一致")

                if current.status in {
                    ExternalJobStatus.SUCCEEDED,
                    ExternalJobStatus.FAILED,
                    ExternalJobStatus.TIMEOUT,
                    ExternalJobStatus.EXPIRED,
                }:
                    existing_event = self._events.get(event_key)
                    if (
                        current.status is target
                        and existing_event is not None
                        and _operation_completion_event_matches(
                            existing_event,
                            conversation_id=conversation,
                            record=event_record,
                        )
                    ):
                        return _clone(current), _clone(existing_event)
                    raise AgentRuntimeRecordConflictError("Operation 已保存不同终态或完成事件")

                if current.status is not ExternalJobStatus.POLLING or current.lease_owner != worker or current.lease_expires_at is None or current.lease_expires_at <= completed_at:
                    raise AgentRuntimeRecordConflictError("Operation 轮询租约无效")

                conversation_events = [(record_owner, stored_event) for (record_owner, _), stored_event in self._events.items() if stored_event.conversation_id == conversation]
                if any(record_owner != owner for record_owner, _ in conversation_events):
                    raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                sequence = 1 if not conversation_events else max(stored_event.sequence for _, stored_event in conversation_events) + 1
                cursor_key = (conversation, event_record.cursor)
                sequence_key = (conversation, sequence)
                if event_record.event_id in self._event_ids or sequence_key in self._event_sequence_keys or cursor_key in self._event_cursor_keys:
                    raise AgentRuntimeRecordConflictError("Operation 完成事件身份已被占用")

                completion_event = _build_operation_completion_event(
                    conversation_id=conversation,
                    sequence=sequence,
                    record=event_record,
                )
                completed_operation = _clone(
                    current.model_copy(
                        update={
                            "status": target,
                            "next_poll_at": None,
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "updated_at": completed_at,
                        }
                    )
                )

                self._operations[operation_key] = completed_operation
                self._event_ids.add(completion_event.event_id)
                self._event_sequence_keys.add(sequence_key)
                self._event_cursor_keys.add(cursor_key)
                self._events[event_key] = _clone(completion_event)
                self._event_delivery[event_key] = _MemoryEventDeliveryState()
                return _clone(completed_operation), _clone(completion_event)

    async def claim_operation_completion_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        """只领取指定 Operation 的终态事件，避免被其他 Outbox 事件阻塞。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        event_identity = _require_text("event_id", event_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        operation_key = (owner, operation_id)
        event_key = (owner, event_identity)

        async with self._operation_write_lock:
            async with self._event_write_lock:
                operation = self._operations.get(operation_key)
                event = self._events.get(event_key)
                if (
                    operation is None
                    or operation.conversation_id != conversation
                    or operation.status
                    not in {
                        ExternalJobStatus.SUCCEEDED,
                        ExternalJobStatus.FAILED,
                        ExternalJobStatus.TIMEOUT,
                        ExternalJobStatus.EXPIRED,
                    }
                    or event is None
                    or event.conversation_id != conversation
                    or event.type is not AgentEventType.EXTERNAL_JOB_STATE_CHANGED
                    or event.payload.get("job_id") != operation_id
                    or event.payload.get("status") != operation.status.value
                ):
                    return None
                state = self._event_delivery[event_key]
                if state.status == "published":
                    return None
                if state.status == "delivering" and state.lease_expires_at is not None and state.lease_expires_at > normalized_now:
                    return None
                state.status = "delivering"
                state.delivery_attempts += 1
                state.lease_owner = worker
                state.lease_expires_at = normalized_expiry
                return EventDeliveryClaim(
                    event=_clone(event),
                    delivery_attempts=state.delivery_attempts,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )

    async def claim_operation_quota_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        quota_pause_revision: int,
        quota_state: Literal["paused", "resumed"],
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        """按稳定事件 ID 领取指定 Operation 的 quota 恢复投递。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        event_identity = _require_text("event_id", event_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        revision = _require_quota_revision(
            "quota_pause_revision",
            quota_pause_revision,
        )
        if revision < 1:
            raise ValueError("quota_pause_revision must be at least 1")
        state_name = _require_quota_state(quota_state)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        operation_key = (owner, operation_id)
        event_key = (owner, event_identity)

        async with self._operation_write_lock:
            async with self._event_write_lock:
                operation = self._operations.get(operation_key)
                event = self._events.get(event_key)
                event_revision = None if event is None else event.payload.get(
                    "quota_pause_revision"
                )
                if (
                    operation is None
                    or operation.conversation_id != conversation
                    or operation.status is not ExternalJobStatus.POLLING
                    or operation.provider_job_id is None
                    or operation.quota_pause_revision != revision
                    or event is None
                    or event.conversation_id != conversation
                    or not _is_operation_quota_event(event)
                    or event.payload.get("job_id") != operation_id
                    or isinstance(event_revision, bool)
                    or event_revision != revision
                    or event.payload.get("quota_state") != state_name
                ):
                    return None
                delivery = self._event_delivery[event_key]
                if delivery.status == "published":
                    return None
                if delivery.status == "delivering" and delivery.lease_expires_at is not None and delivery.lease_expires_at > normalized_now:
                    return None
                delivery.status = "delivering"
                delivery.delivery_attempts += 1
                delivery.lease_owner = worker
                delivery.lease_expires_at = normalized_expiry
                return EventDeliveryClaim(
                    event=_clone(event),
                    delivery_attempts=delivery.delivery_attempts,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )


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
            "quota_pause_revision": row.quota_pause_revision,
            "next_poll_at": None if row.next_poll_at is None else _database_utc(row.next_poll_at),
            "lease_owner": row.lease_owner,
            "lease_expires_at": (None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)),
            "created_at": _database_utc(row.created_at),
            "updated_at": _database_utc(row.updated_at),
        }
    )


class SQLAgentRuntimeRepository:
    """使用 SQL owner 条件执行所有读取的异步 Repository。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        bind = session_factory.kw.get("bind")
        self._sqlite_write_lock = _SQLITE_ENGINE_WRITE_LOCKS.setdefault(bind, asyncio.Lock()) if isinstance(bind, AsyncEngine) and bind.dialect.name == "sqlite" else None

    async def _insert(self, row: object) -> None:
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise AgentRuntimeRecordConflictError("记录主键或唯一业务键已经被占用") from None

    async def _ensure_compaction_coordination_row(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
        owner_conflict_is_absent: bool = False,
    ) -> bool:
        """先落一条永久协调行，让所有 Turn 写入与领取锁定同一对象。"""

        statement = select(PixelFlowAgentCompactionLockRow).where(PixelFlowAgentCompactionLockRow.conversation_id == conversation_id)
        existing_turn_owners_statement = select(PixelFlowAgentTurnRow.user_id).where(PixelFlowAgentTurnRow.conversation_id == conversation_id).with_for_update()
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    existing = (await session.scalars(statement.with_for_update())).one_or_none()
                    if existing is not None:
                        if existing.user_id != user_id:
                            if owner_conflict_is_absent:
                                return False
                            raise AgentRuntimeRecordConflictError("conversation 压缩协调行已经属于其他所有者")
                        return True
                    existing_turn_owners = set((await session.scalars(existing_turn_owners_statement)).all())
                    if existing_turn_owners - {user_id}:
                        if owner_conflict_is_absent:
                            return False
                        raise AgentRuntimeRecordConflictError("conversation 既有 Turn 已经属于其他所有者")
                    session.add(
                        PixelFlowAgentCompactionLockRow(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            state="idle",
                            lease_owner=None,
                            lease_token=None,
                            lease_expires_at=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
        except IntegrityError:
            async with self._session_factory() as session:
                existing = (await session.scalars(statement)).one_or_none()
            if existing is None:
                raise AgentRuntimeRecordConflictError("conversation 压缩协调行创建失败") from None
            if existing.user_id != user_id:
                if owner_conflict_is_absent:
                    return False
                raise AgentRuntimeRecordConflictError("conversation 压缩协调行已经属于其他所有者") from None
        return True

    async def _lock_event_sequence_coordination(
        self,
        session: AsyncSession,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
        operation_id: str | None = None,
    ) -> PixelFlowAgentOperationRow | None:
        """在目标事务内验证归属并锁定或创建 Event 序号协调行。"""

        coordination_statement = (
            select(PixelFlowAgentCompactionLockRow)
            .where(PixelFlowAgentCompactionLockRow.conversation_id == conversation_id)
            .with_for_update()
        )
        coordination = (
            await session.scalars(coordination_statement)
        ).one_or_none()
        if coordination is not None and coordination.user_id != user_id:
            raise AgentRuntimeRecordConflictError(
                "conversation Event 序号协调行已经属于其他所有者"
            )

        operation: PixelFlowAgentOperationRow | None = None
        if operation_id is not None:
            operation_statement = (
                select(PixelFlowAgentOperationRow)
                .where(
                    PixelFlowAgentOperationRow.user_id == user_id,
                    PixelFlowAgentOperationRow.conversation_id == conversation_id,
                    PixelFlowAgentOperationRow.job_id == operation_id,
                )
                .with_for_update()
            )
            operation = (
                await session.scalars(operation_statement)
            ).one_or_none()
            if operation is None:
                raise AgentRuntimeRecordConflictError(
                    "Operation 不存在或不属于当前会话"
                )

        if coordination is None:
            if operation is None:
                authority_statements = (
                    select(PixelFlowAgentOperationRow.user_id)
                    .where(
                        PixelFlowAgentOperationRow.conversation_id
                        == conversation_id
                    )
                    .with_for_update(),
                    select(PixelFlowAgentTurnRow.user_id)
                    .where(PixelFlowAgentTurnRow.conversation_id == conversation_id)
                    .with_for_update(),
                    select(PixelFlowAgentEventRow.user_id)
                    .where(PixelFlowAgentEventRow.conversation_id == conversation_id)
                    .with_for_update(),
                    select(PixelFlowAgentWorkflowRow.user_id)
                    .where(
                        PixelFlowAgentWorkflowRow.conversation_id == conversation_id
                    )
                    .with_for_update(),
                    select(PixelFlowAgentContextSummaryRow.user_id)
                    .where(
                        PixelFlowAgentContextSummaryRow.conversation_id
                        == conversation_id
                    )
                    .with_for_update(),
                )
                authority_owners: set[str] = set()
                for authority_statement in authority_statements:
                    authority_owners.update(
                        (await session.scalars(authority_statement)).all()
                    )
                if authority_owners - {user_id}:
                    raise AgentRuntimeRecordConflictError(
                        "AgentEvent conversation 已经属于其他所有者"
                    )

            coordination = (
                await session.scalars(coordination_statement)
            ).one_or_none()
            if coordination is not None and coordination.user_id != user_id:
                raise AgentRuntimeRecordConflictError(
                    "conversation Event 序号协调行已经属于其他所有者"
                )
            if coordination is None:
                coordination = PixelFlowAgentCompactionLockRow(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    state="idle",
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(coordination)
                await session.flush()
        return operation

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
                pending_external_job_json=(None if normalized.pending_external_job is None else normalized.pending_external_job.model_dump(mode="json")),
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
                PixelFlowAgentWorkflowRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentWorkflowRow.updated_at.desc(), PixelFlowAgentWorkflowRow.workflow_id.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_workflow_from_row(row) for row in rows]

    async def create_turn(self, user_id: str, record: TurnRecord) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        await self._ensure_compaction_coordination_row(
            owner,
            normalized.conversation_id,
            now=normalized.created_at,
        )
        coordination_statement = select(PixelFlowAgentCompactionLockRow).where(PixelFlowAgentCompactionLockRow.conversation_id == normalized.conversation_id).with_for_update()
        stored_status = normalized.status
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    coordination = (await session.scalars(coordination_statement)).one()
                    if coordination.user_id != owner:
                        raise AgentRuntimeRecordConflictError("conversation 压缩协调行已经属于其他所有者")
                    if coordination.state in {"active", "retry_required"}:
                        if normalized.status not in {
                            TurnStatus.ACCEPTED,
                            TurnStatus.QUEUED,
                        }:
                            raise AgentRuntimeRecordConflictError("压缩未结束时只能保存待执行 Turn")
                        stored_status = TurnStatus.QUEUED
                    session.add(
                        PixelFlowAgentTurnRow(
                            turn_id=normalized.turn_id,
                            conversation_id=normalized.conversation_id,
                            user_id=owner,
                            client_input_id=str(normalized.client_input_id),
                            status=stored_status.value,
                            target_workflow_id=normalized.target_workflow_id,
                            decision_json=(None if normalized.decision is None else normalized.decision.model_dump(mode="json")),
                            expected_context_version=(normalized.expected_context_version),
                            created_at=normalized.created_at,
                            updated_at=normalized.created_at,
                        )
                    )
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("记录主键或唯一业务键已经被占用") from None
        return _clone(normalized.model_copy(update={"status": stored_status}))

    async def enqueue_turn(
        self,
        user_id: str,
        record: TurnRecord,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        await self._ensure_compaction_coordination_row(
            owner,
            normalized.conversation_id,
            now=normalized.created_at,
        )
        coordination_statement = select(PixelFlowAgentCompactionLockRow).where(PixelFlowAgentCompactionLockRow.conversation_id == normalized.conversation_id).with_for_update()
        statement = (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id == normalized.conversation_id,
                PixelFlowAgentTurnRow.client_input_id == str(normalized.client_input_id),
            )
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    coordination = (await session.scalars(coordination_statement)).one()
                    if coordination.user_id != owner:
                        raise AgentRuntimeRecordConflictError("conversation 压缩协调行已经属于其他所有者")
                    existing = (await session.scalars(statement)).one_or_none()
                    if existing is not None:
                        if coordination.state in {"active", "retry_required"} and existing.status == TurnStatus.ACCEPTED.value:
                            existing.status = TurnStatus.QUEUED.value
                            existing.updated_at = datetime.now(UTC)
                            await session.flush()
                        return _turn_from_row(existing)
                    stored_status = normalized.status
                    if coordination.state in {"active", "retry_required"}:
                        if normalized.status not in {
                            TurnStatus.ACCEPTED,
                            TurnStatus.QUEUED,
                        }:
                            raise AgentRuntimeRecordConflictError("压缩未结束时只能保存待执行 Turn")
                        stored_status = TurnStatus.QUEUED
                    session.add(
                        PixelFlowAgentTurnRow(
                            turn_id=normalized.turn_id,
                            conversation_id=normalized.conversation_id,
                            user_id=owner,
                            client_input_id=str(normalized.client_input_id),
                            status=stored_status.value,
                            target_workflow_id=normalized.target_workflow_id,
                            decision_json=(None if normalized.decision is None else normalized.decision.model_dump(mode="json")),
                            expected_context_version=(normalized.expected_context_version),
                            created_at=normalized.created_at,
                            updated_at=normalized.created_at,
                        )
                    )
        except IntegrityError:
            async with self._session_factory() as session:
                existing = (await session.scalars(statement)).one_or_none()
            if existing is not None:
                return _turn_from_row(existing)
            raise AgentRuntimeRecordConflictError("记录主键或唯一业务键已经被占用") from None
        return _clone(normalized.model_copy(update={"status": stored_status}))

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
                PixelFlowAgentTurnRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
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
        coordination_exists_statement = select(PixelFlowAgentCompactionLockRow.conversation_id).where(PixelFlowAgentCompactionLockRow.conversation_id == conversation)
        turn_exists_statement = (
            select(PixelFlowAgentTurnRow.inbox_sequence)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id == conversation,
            )
            .limit(1)
        )
        async with self._session_factory() as session:
            coordination_exists = (await session.scalars(coordination_exists_statement)).first()
            if coordination_exists is None:
                turn_exists = (await session.scalars(turn_exists_statement)).first()
                if turn_exists is None:
                    return None
        if not await self._ensure_compaction_coordination_row(
            owner,
            conversation,
            now=datetime.now(UTC),
            owner_conflict_is_absent=True,
        ):
            return None
        coordination_statement = select(PixelFlowAgentCompactionLockRow).where(PixelFlowAgentCompactionLockRow.conversation_id == conversation).with_for_update()
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
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                coordination = (await session.scalars(coordination_statement)).one()
                if coordination.user_id != owner:
                    raise AgentRuntimeRecordConflictError("conversation 压缩协调行已经属于其他所有者")
                if coordination.state in {"active", "retry_required"}:
                    return None
                rows = (await session.scalars(statement)).all()
                if any(row.status == TurnStatus.PROCESSING.value for row in rows):
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
                PixelFlowAgentContextSummaryRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentContextSummaryRow.version.asc(), PixelFlowAgentContextSummaryRow.summary_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_summary_from_row(row) for row in rows]

    async def create_event(self, user_id: str, record: AgentEvent) -> AgentEvent:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_event(record)
        last_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.conversation_id == normalized.conversation_id,
            )
            .order_by(PixelFlowAgentEventRow.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    await self._lock_event_sequence_coordination(
                        session,
                        owner,
                        normalized.conversation_id,
                        now=normalized.occurred_at,
                    )
                    last_row = (await session.scalars(last_statement)).first()
                    if last_row is not None and last_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                    expected_sequence = 1 if last_row is None else last_row.sequence + 1
                    if normalized.sequence > expected_sequence:
                        # 跳号禁止：只允许连续追加。
                        raise AgentRuntimeRecordConflictError("AgentEvent sequence 必须连续递增")
                    if normalized.sequence < expected_sequence:
                        # 锁外预读的 sequence 过期（工具进度 / 确认 / Operation 回写并发）；锁内自愈。
                        normalized = normalized.model_copy(
                            update={"sequence": expected_sequence}
                        )
                    session.add(
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
                    await session.flush()
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("记录主键或唯一业务键已经被占用") from None
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

    async def get_latest_event(self, user_id: str, conversation_id: str) -> AgentEvent | None:
        """按持久化 sequence 回读会话最新事件，避免刷新时扫描完整 Outbox。"""

        owner = _require_text("user_id", user_id, 64)
        statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentEventRow.sequence.desc(), PixelFlowAgentEventRow.event_id.desc())
            .limit(1)
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
                PixelFlowAgentEventRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentEventRow.sequence.asc(), PixelFlowAgentEventRow.event_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_event_from_row(row) for row in rows]

    async def list_events_after_cursor(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None,
        limit: int = 100,
    ) -> list[AgentEvent] | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        page_size = _event_query_limit(limit)
        after_sequence: int | None = None
        async with self._session_factory() as session:
            if cursor is not None:
                anchor_statement = select(PixelFlowAgentEventRow).where(
                    PixelFlowAgentEventRow.user_id == owner,
                    PixelFlowAgentEventRow.conversation_id == conversation,
                    PixelFlowAgentEventRow.cursor == _require_text("cursor", cursor, 128),
                )
                anchor = (await session.scalars(anchor_statement)).one_or_none()
                if anchor is None:
                    return None
                after_sequence = anchor.sequence
            statement = (
                select(PixelFlowAgentEventRow)
                .where(
                    PixelFlowAgentEventRow.user_id == owner,
                    PixelFlowAgentEventRow.conversation_id == conversation,
                )
                .order_by(
                    PixelFlowAgentEventRow.sequence.asc(),
                    PixelFlowAgentEventRow.event_id.asc(),
                )
                .limit(page_size)
            )
            if after_sequence is not None:
                statement = statement.where(PixelFlowAgentEventRow.sequence > after_sequence)
            rows = (await session.scalars(statement)).all()
        return [_event_from_row(row) for row in rows]

    async def claim_next_event(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.conversation_id == conversation,
                PixelFlowAgentEventRow.delivery_status.in_(("pending", "delivering")),
            )
            .order_by(
                PixelFlowAgentEventRow.sequence.asc(),
                PixelFlowAgentEventRow.event_id.asc(),
            )
            .limit(1)
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).first()
                if row is None:
                    return None
                if _is_operation_internal_event(_event_from_row(row)):
                    return None
                if row.delivery_status == "delivering":
                    if row.lease_expires_at is None:
                        return None
                    if _database_utc(row.lease_expires_at) > normalized_now:
                        return None
                row.delivery_status = "delivering"
                row.delivery_attempts += 1
                row.lease_owner = worker
                row.lease_expires_at = normalized_expiry
                await session.flush()
                return EventDeliveryClaim(
                    event=_event_from_row(row),
                    delivery_attempts=row.delivery_attempts,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )

    async def complete_event_delivery(
        self,
        user_id: str,
        event_id: str,
        *,
        lease_owner: str,
        published_at: datetime,
    ) -> AgentEvent | None:
        owner = _require_text("user_id", user_id, 64)
        event_identity = _require_text("event_id", event_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        completed_at = _normalize_datetime(
            "published_at",
            published_at,
        )
        statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.event_id == event_identity,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                if row is None:
                    return None
                if row.delivery_status == "published":
                    return _event_from_row(row)
                if row.delivery_status != "delivering" or row.lease_owner != worker or row.lease_expires_at is None or completed_at >= _database_utc(row.lease_expires_at):
                    raise AgentRuntimeRecordConflictError("AgentEvent 投递租约无效")
                row.delivery_status = "published"
                row.published_at = completed_at
                await session.flush()
                return _event_from_row(row)

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
                quota_pause_revision=normalized.quota_pause_revision,
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

    async def get_operation_by_idempotency_key(
        self,
        user_id: str,
        idempotency_key: str,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        statement = select(PixelFlowAgentOperationRow).where(
            PixelFlowAgentOperationRow.user_id == owner,
            PixelFlowAgentOperationRow.idempotency_key == _require_text("idempotency_key", idempotency_key, 255),
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
                PixelFlowAgentOperationRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentOperationRow.created_at.asc(), PixelFlowAgentOperationRow.job_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_operation_from_row(row) for row in rows]

    async def list_due_operations(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationRecord]:
        """按稳定顺序返回无有效租约的到期轮询任务。"""

        normalized_now, normalized_limit = _recovery_scan_window(now, limit)
        pending_quota_event = (
            select(PixelFlowAgentEventRow.outbox_id)
            .where(
                PixelFlowAgentEventRow.user_id == PixelFlowAgentOperationRow.user_id,
                PixelFlowAgentEventRow.conversation_id == PixelFlowAgentOperationRow.conversation_id,
                PixelFlowAgentEventRow.event_type == AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED.value,
                PixelFlowAgentEventRow.event_id.startswith(
                    _OPERATION_QUOTA_EVENT_ID_PREFIX,
                    autoescape=True,
                ),
                PixelFlowAgentEventRow.payload_json["job_id"].as_string() == PixelFlowAgentOperationRow.job_id,
                PixelFlowAgentEventRow.delivery_status != "published",
            )
            .correlate(PixelFlowAgentOperationRow)
            .exists()
        )
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.status == ExternalJobStatus.POLLING.value,
                PixelFlowAgentOperationRow.provider_job_id.is_not(None),
                PixelFlowAgentOperationRow.next_poll_at.is_not(None),
                PixelFlowAgentOperationRow.next_poll_at <= normalized_now,
                or_(
                    and_(
                        PixelFlowAgentOperationRow.lease_owner.is_(None),
                        PixelFlowAgentOperationRow.lease_expires_at.is_(None),
                    ),
                    and_(
                        PixelFlowAgentOperationRow.lease_owner.is_not(None),
                        PixelFlowAgentOperationRow.lease_expires_at.is_not(None),
                        PixelFlowAgentOperationRow.lease_expires_at <= normalized_now,
                    ),
                ),
                ~pending_quota_event,
            )
            .order_by(
                PixelFlowAgentOperationRow.next_poll_at.asc(),
                PixelFlowAgentOperationRow.created_at.asc(),
                PixelFlowAgentOperationRow.job_id.asc(),
            )
            .limit(normalized_limit)
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [
            OwnedOperationRecord(
                user_id=row.user_id,
                operation=_operation_from_row(row),
            )
            for row in rows
        ]

    async def list_pending_operation_completions(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationRecord]:
        """返回尚未投递或投递租约已过期的 Operation 完成事件。"""

        normalized_now, normalized_limit = _recovery_scan_window(now, limit)
        terminal_statuses = (
            ExternalJobStatus.SUCCEEDED.value,
            ExternalJobStatus.FAILED.value,
            ExternalJobStatus.TIMEOUT.value,
            ExternalJobStatus.EXPIRED.value,
        )
        operation_statement = (
            select(PixelFlowAgentOperationRow)
            .join(
                PixelFlowAgentEventRow,
                and_(
                    PixelFlowAgentOperationRow.user_id == PixelFlowAgentEventRow.user_id,
                    PixelFlowAgentOperationRow.conversation_id == PixelFlowAgentEventRow.conversation_id,
                    PixelFlowAgentOperationRow.job_id == PixelFlowAgentEventRow.payload_json["job_id"].as_string(),
                ),
            )
            .where(
                PixelFlowAgentEventRow.event_type == AgentEventType.EXTERNAL_JOB_STATE_CHANGED.value,
                PixelFlowAgentOperationRow.status.in_(terminal_statuses),
                PixelFlowAgentEventRow.payload_json["status"].as_string() == PixelFlowAgentOperationRow.status,
                or_(
                    PixelFlowAgentEventRow.delivery_status == "pending",
                    and_(
                        PixelFlowAgentEventRow.delivery_status == "delivering",
                        PixelFlowAgentEventRow.lease_expires_at.is_not(None),
                        PixelFlowAgentEventRow.lease_expires_at <= normalized_now,
                    ),
                ),
            )
            .order_by(PixelFlowAgentEventRow.outbox_id.asc())
            .limit(normalized_limit)
        )
        async with self._session_factory() as session:
            operation_rows = (await session.scalars(operation_statement)).all()
        return [
            OwnedOperationRecord(
                user_id=operation.user_id,
                operation=_operation_from_row(operation),
            )
            for operation in operation_rows
        ]

    async def list_pending_operation_quota_events(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[OwnedOperationQuotaEvent]:
        """用有界 SQL 联结返回可领取的 quota 内部事件。"""

        normalized_now, normalized_limit = _recovery_scan_window(now, limit)
        statement = (
            select(PixelFlowAgentOperationRow, PixelFlowAgentEventRow)
            .join(
                PixelFlowAgentEventRow,
                and_(
                    PixelFlowAgentOperationRow.user_id == PixelFlowAgentEventRow.user_id,
                    PixelFlowAgentOperationRow.conversation_id == PixelFlowAgentEventRow.conversation_id,
                    PixelFlowAgentOperationRow.job_id == PixelFlowAgentEventRow.payload_json["job_id"].as_string(),
                ),
            )
            .where(
                PixelFlowAgentOperationRow.status == ExternalJobStatus.POLLING.value,
                PixelFlowAgentOperationRow.provider_job_id.is_not(None),
                PixelFlowAgentEventRow.event_type == AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED.value,
                PixelFlowAgentEventRow.event_id.startswith(
                    _OPERATION_QUOTA_EVENT_ID_PREFIX,
                    autoescape=True,
                ),
                PixelFlowAgentEventRow.payload_json["quota_pause_revision"].as_integer() == PixelFlowAgentOperationRow.quota_pause_revision,
                PixelFlowAgentEventRow.payload_json["quota_state"].as_string().in_(("paused", "resumed")),
                or_(
                    PixelFlowAgentEventRow.delivery_status == "pending",
                    and_(
                        PixelFlowAgentEventRow.delivery_status == "delivering",
                        PixelFlowAgentEventRow.lease_expires_at.is_not(None),
                        PixelFlowAgentEventRow.lease_expires_at <= normalized_now,
                    ),
                ),
            )
            .order_by(PixelFlowAgentEventRow.outbox_id.asc())
            .limit(normalized_limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            OwnedOperationQuotaEvent(
                user_id=operation.user_id,
                operation=_operation_from_row(operation),
                event=_event_from_row(event),
            )
            for operation, event in rows
        ]

    async def claim_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        """仅让一个请求领取尚未调用 Provider 的 created Operation。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                current_expiry = None if row is None or row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if row is None or row.status != ExternalJobStatus.CREATED.value or row.provider_job_id is not None or (row.lease_owner is None) != (current_expiry is None) or (current_expiry is not None and current_expiry > normalized_now):
                    return None
                row.lease_owner = worker
                row.lease_expires_at = normalized_expiry
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def complete_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None:
        """绑定原 provider job ID，并把 start lease 转为轮询计划。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text("provider_job_id", provider_job_id, 128)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now, normalized_next_poll = _operation_poll_schedule(
            now,
            next_poll_at,
        )
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                current_expiry = None if row is None or row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if row is None or row.status != ExternalJobStatus.CREATED.value or row.provider_job_id is not None or row.lease_owner != worker or current_expiry is None or current_expiry <= normalized_now:
                    return None
                row.provider_job_id = provider_id
                row.status = ExternalJobStatus.POLLING.value
                row.next_poll_at = normalized_next_poll
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def release_operation_start(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationRecord | None:
        """在明确未创建 provider job 时释放 start lease，等待用户重试。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now = _normalize_datetime("now", now)
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                current_expiry = None if row is None or row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if row is None or row.status != ExternalJobStatus.CREATED.value or row.provider_job_id is not None or row.lease_owner != worker or current_expiry is None or current_expiry <= normalized_now:
                    return None
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def claim_operation_lease(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                if row is None or row.status != ExternalJobStatus.POLLING.value or row.provider_job_id is None or row.next_poll_at is None or _database_utc(row.next_poll_at) > normalized_now:
                    return None
                current_expiry = None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if (row.lease_owner is None) != (current_expiry is None):
                    return None
                if row.lease_owner == worker and current_expiry is not None and current_expiry > normalized_now:
                    return _operation_from_row(row)
                if current_expiry is not None and current_expiry > normalized_now:
                    return None
                row.lease_owner = worker
                row.lease_expires_at = normalized_expiry
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def finalize_operation_start_terminal(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        terminal_status: ExternalJobStatus,
        lease_owner: str,
        now: datetime,
        event: OperationTerminalEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]:
        """在一个SQL事务中把同步Provider start结果提交为终态和完成事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text("provider_job_id", provider_job_id, 128)
        worker = _require_text("lease_owner", lease_owner, 128)
        completed_at = _normalize_datetime("now", now)
        target = _require_operation_terminal_status(terminal_status)
        event_record = _normalize_operation_terminal_event(event)
        existing_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.event_id == event_record.event_id,
            )
            .with_for_update()
        )
        last_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(PixelFlowAgentEventRow.conversation_id == conversation)
            .order_by(PixelFlowAgentEventRow.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    row = await self._lock_event_sequence_coordination(
                        session,
                        owner,
                        conversation,
                        now=completed_at,
                        operation_id=operation_id,
                    )
                    if row is None:
                        raise AgentRuntimeRecordConflictError(
                            "Operation 不存在或不属于当前会话"
                        )
                    current_expiry = (
                        None
                        if row.lease_expires_at is None
                        else _database_utc(row.lease_expires_at)
                    )
                    if (
                        row.status != ExternalJobStatus.CREATED.value
                        or row.provider_job_id is not None
                        or row.lease_owner != worker
                        or current_expiry is None
                        or current_expiry <= completed_at
                    ):
                        raise AgentRuntimeRecordConflictError("Operation start租约无效")
                    if (await session.scalars(existing_event_statement)).one_or_none() is not None:
                        raise AgentRuntimeRecordConflictError("Operation 完成事件身份已被占用")
                    last_event_row = (await session.scalars(last_event_statement)).first()
                    if last_event_row is not None and last_event_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError(
                            "AgentEvent conversation 已被其他所有者占用"
                        )
                    sequence = 1 if last_event_row is None else last_event_row.sequence + 1
                    completion_event = _build_operation_completion_event(
                        conversation_id=conversation,
                        sequence=sequence,
                        record=event_record,
                    )
                    row.provider_job_id = provider_id
                    row.status = target.value
                    row.next_poll_at = None
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.updated_at = completed_at
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=completion_event.schema_version,
                            event_id=completion_event.event_id,
                            sequence=completion_event.sequence,
                            cursor=completion_event.cursor,
                            conversation_id=completion_event.conversation_id,
                            user_id=owner,
                            run_id=completion_event.run_id,
                            occurred_at=completion_event.occurred_at,
                            event_type=completion_event.type.value,
                            payload_json=completion_event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )
                    await session.flush()
                    completed_operation = _operation_from_row(row)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError(
                "Operation 终态或完成事件发生并发冲突"
            ) from None
        return completed_operation, completion_event

    async def finalize_operation_terminal(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        terminal_status: ExternalJobStatus,
        lease_owner: str,
        now: datetime,
        event: OperationTerminalEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]:
        """在一个 SQL 事务中保存 Operation 终态与完成事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text(
            "provider_job_id",
            provider_job_id,
            128,
        )
        worker = _require_text("lease_owner", lease_owner, 128)
        completed_at = _normalize_datetime("now", now)
        target = _require_operation_terminal_status(terminal_status)
        event_record = _normalize_operation_terminal_event(event)
        existing_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.event_id == event_record.event_id,
            )
            .with_for_update()
        )
        last_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.conversation_id == conversation,
            )
            .order_by(PixelFlowAgentEventRow.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    row = await self._lock_event_sequence_coordination(
                        session,
                        owner,
                        conversation,
                        now=completed_at,
                        operation_id=operation_id,
                    )
                    if row is None:
                        raise AgentRuntimeRecordConflictError(
                            "Operation 不存在或不属于当前会话"
                        )
                    if row.provider_job_id != provider_id:
                        raise AgentRuntimeRecordConflictError("Operation provider job ID 不一致")

                    existing_event_row = (await session.scalars(existing_event_statement)).one_or_none()
                    if row.status in {
                        ExternalJobStatus.SUCCEEDED.value,
                        ExternalJobStatus.FAILED.value,
                        ExternalJobStatus.TIMEOUT.value,
                        ExternalJobStatus.EXPIRED.value,
                    }:
                        existing_event = None if existing_event_row is None else _event_from_row(existing_event_row)
                        if (
                            row.status == target.value
                            and existing_event is not None
                            and _operation_completion_event_matches(
                                existing_event,
                                conversation_id=conversation,
                                record=event_record,
                            )
                        ):
                            return _operation_from_row(row), existing_event
                        raise AgentRuntimeRecordConflictError("Operation 已保存不同终态或完成事件")

                    current_expiry = None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                    if row.status != ExternalJobStatus.POLLING.value or row.lease_owner != worker or current_expiry is None or current_expiry <= completed_at:
                        raise AgentRuntimeRecordConflictError("Operation 轮询租约无效")
                    if existing_event_row is not None:
                        raise AgentRuntimeRecordConflictError("Operation 完成事件身份已被占用")

                    last_event_row = (await session.scalars(last_event_statement)).first()
                    if last_event_row is not None and last_event_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                    sequence = 1 if last_event_row is None else last_event_row.sequence + 1
                    completion_event = _build_operation_completion_event(
                        conversation_id=conversation,
                        sequence=sequence,
                        record=event_record,
                    )

                    row.status = target.value
                    row.next_poll_at = None
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.updated_at = completed_at
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=completion_event.schema_version,
                            event_id=completion_event.event_id,
                            sequence=completion_event.sequence,
                            cursor=completion_event.cursor,
                            conversation_id=completion_event.conversation_id,
                            user_id=owner,
                            run_id=completion_event.run_id,
                            occurred_at=completion_event.occurred_at,
                            event_type=completion_event.type.value,
                            payload_json=completion_event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )
                    await session.flush()
                    completed_operation = _operation_from_row(row)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("Operation 终态或完成事件发生并发冲突") from None
        return completed_operation, completion_event

    async def claim_operation_completion_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        """按稳定事件 ID 领取指定 Operation 的 Workflow 恢复投递。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        event_identity = _require_text("event_id", event_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        operation_statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.conversation_id == conversation,
                PixelFlowAgentEventRow.event_id == event_identity,
                PixelFlowAgentEventRow.event_type == AgentEventType.EXTERNAL_JOB_STATE_CHANGED.value,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                operation = (await session.scalars(operation_statement)).one_or_none()
                row = (await session.scalars(event_statement)).one_or_none()
                if (
                    operation is None
                    or operation.status
                    not in {
                        ExternalJobStatus.SUCCEEDED.value,
                        ExternalJobStatus.FAILED.value,
                        ExternalJobStatus.TIMEOUT.value,
                        ExternalJobStatus.EXPIRED.value,
                    }
                    or row is None
                    or row.payload_json.get("job_id") != operation_id
                    or row.payload_json.get("status") != operation.status
                ):
                    return None
                if row.delivery_status == "published":
                    return None
                if row.delivery_status == "delivering":
                    current_expiry = None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                    if current_expiry is not None and current_expiry > normalized_now:
                        return None
                row.delivery_status = "delivering"
                row.delivery_attempts += 1
                row.lease_owner = worker
                row.lease_expires_at = normalized_expiry
                await session.flush()
                return EventDeliveryClaim(
                    event=_event_from_row(row),
                    delivery_attempts=row.delivery_attempts,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )

    async def claim_operation_quota_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        quota_pause_revision: int,
        quota_state: Literal["paused", "resumed"],
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        """按稳定事件 ID 领取指定 Operation 的 quota 恢复投递。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        event_identity = _require_text("event_id", event_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        revision = _require_quota_revision(
            "quota_pause_revision",
            quota_pause_revision,
        )
        if revision < 1:
            raise ValueError("quota_pause_revision must be at least 1")
        state_name = _require_quota_state(quota_state)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        operation_statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.conversation_id == conversation,
                PixelFlowAgentEventRow.event_id == event_identity,
                PixelFlowAgentEventRow.event_type == AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED.value,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                operation = (await session.scalars(operation_statement)).one_or_none()
                row = (await session.scalars(event_statement)).one_or_none()
                event_revision = None if row is None else row.payload_json.get(
                    "quota_pause_revision"
                )
                if (
                    operation is None
                    or operation.status != ExternalJobStatus.POLLING.value
                    or operation.provider_job_id is None
                    or operation.quota_pause_revision != revision
                    or row is None
                    or not row.event_id.startswith(_OPERATION_QUOTA_EVENT_ID_PREFIX)
                    or row.payload_json.get("job_id") != operation_id
                    or isinstance(event_revision, bool)
                    or event_revision != revision
                    or row.payload_json.get("quota_state") != state_name
                ):
                    return None
                if row.delivery_status == "published":
                    return None
                if row.delivery_status == "delivering":
                    current_expiry = None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                    if current_expiry is not None and current_expiry > normalized_now:
                        return None
                row.delivery_status = "delivering"
                row.delivery_attempts += 1
                row.lease_owner = worker
                row.lease_expires_at = normalized_expiry
                await session.flush()
                return EventDeliveryClaim(
                    event=_event_from_row(row),
                    delivery_attempts=row.delivery_attempts,
                    lease_owner=worker,
                    lease_expires_at=normalized_expiry,
                )

    async def heartbeat_operation_lease(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner,
            now,
            lease_expires_at,
        )
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                current_expiry = None if row is None or row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if row is None or row.status != ExternalJobStatus.POLLING.value or row.lease_owner != worker or current_expiry is None or current_expiry <= normalized_now or normalized_expiry <= current_expiry:
                    return None
                row.lease_expires_at = normalized_expiry
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def pause_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationRecord | None:
        """额度不足时保留原 job，并停止自动轮询。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now = _normalize_datetime("now", now)
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                current_expiry = None if row is None or row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if row is None or row.status != ExternalJobStatus.POLLING.value or row.provider_job_id is None or row.lease_owner != worker or current_expiry is None or current_expiry <= normalized_now:
                    return None
                row.next_poll_at = None
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def resume_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        now: datetime,
    ) -> OperationRecord | None:
        """由用户动作重新安排额度暂停的原 provider job。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        normalized_now = _normalize_datetime("now", now)
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                if row is None or row.status != ExternalJobStatus.POLLING.value or row.provider_job_id is None or row.next_poll_at is not None or row.lease_owner is not None or row.lease_expires_at is not None:
                    return None
                row.next_poll_at = normalized_now
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)

    async def pause_operation_for_quota(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        provider_job_id: str,
        lease_owner: str,
        expected_revision: int,
        now: datetime,
        event: OperationQuotaEventRecord,
    ) -> tuple[OperationRecord, AgentEvent]:
        """在一个 SQL 事务中递增暂停代次并写入唯一 quota 事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        provider_id = _require_text("provider_job_id", provider_job_id, 128)
        worker = _require_text("lease_owner", lease_owner, 128)
        revision = _require_quota_revision("expected_revision", expected_revision)
        paused_at = _normalize_datetime("now", now)
        event_record = _normalize_operation_quota_event(event)
        _require_quota_event_contract(
            event_record,
            job_id=operation_id,
            quota_pause_revision=revision + 1,
            quota_state="paused",
        )
        existing_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.event_id == event_record.event_id,
            )
            .with_for_update()
        )
        last_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(PixelFlowAgentEventRow.conversation_id == conversation)
            .order_by(PixelFlowAgentEventRow.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    row = await self._lock_event_sequence_coordination(
                        session,
                        owner,
                        conversation,
                        now=paused_at,
                        operation_id=operation_id,
                    )
                    if row is None:
                        raise AgentRuntimeRecordConflictError(
                            "Operation 不存在或不属于当前会话"
                        )
                    existing_event_row = (
                        await session.scalars(existing_event_statement)
                    ).one_or_none()
                    if row.quota_pause_revision == revision + 1:
                        existing_event = None if existing_event_row is None else _event_from_row(existing_event_row)
                        if existing_event is not None and _operation_quota_event_matches(
                            existing_event,
                            conversation_id=conversation,
                            record=event_record,
                        ):
                            return _operation_from_row(row), existing_event
                        raise AgentRuntimeRecordConflictError("Operation quota 暂停重放事件不一致")
                    current_expiry = None if row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                    if (
                        row.quota_pause_revision != revision
                        or row.status != ExternalJobStatus.POLLING.value
                        or row.provider_job_id != provider_id
                        or row.lease_owner != worker
                        or current_expiry is None
                        or current_expiry <= paused_at
                    ):
                        raise AgentRuntimeRecordConflictError("Operation quota pause CAS 冲突")
                    if existing_event_row is not None:
                        raise AgentRuntimeRecordConflictError("Operation quota 事件身份已被占用")

                    last_event_row = (await session.scalars(last_event_statement)).first()
                    if last_event_row is not None and last_event_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                    sequence = 1 if last_event_row is None else last_event_row.sequence + 1
                    quota_event = _build_operation_quota_event(
                        conversation_id=conversation,
                        sequence=sequence,
                        record=event_record,
                    )
                    row.quota_pause_revision = revision + 1
                    row.next_poll_at = None
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.updated_at = paused_at
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=quota_event.schema_version,
                            event_id=quota_event.event_id,
                            sequence=quota_event.sequence,
                            cursor=quota_event.cursor,
                            conversation_id=quota_event.conversation_id,
                            user_id=owner,
                            run_id=quota_event.run_id,
                            occurred_at=quota_event.occurred_at,
                            event_type=quota_event.type.value,
                            payload_json=quota_event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )
                    await session.flush()
                    paused = _operation_from_row(row)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("Operation quota 暂停或事件发生并发冲突") from None
        return paused, quota_event

    async def resume_operation_from_quota(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        workflow_id: str,
        expected_revision: int,
        now: datetime,
        delivery_lease_owner: str,
        delivery_lease_expires_at: datetime,
        event: OperationQuotaEventRecord,
    ) -> tuple[OperationRecord, EventDeliveryClaim]:
        """在一个 SQL 事务中恢复原 job 并预占 resume 事件租约。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        workflow = _require_text("workflow_id", workflow_id, 64)
        revision = _require_quota_revision("expected_revision", expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be at least 1")
        worker, resumed_at, normalized_expiry = _lease_window(
            delivery_lease_owner,
            now,
            delivery_lease_expires_at,
        )
        event_record = _normalize_operation_quota_event(event)
        _require_quota_event_contract(
            event_record,
            job_id=operation_id,
            quota_pause_revision=revision,
            quota_state="resumed",
        )
        existing_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.event_id == event_record.event_id,
            )
            .with_for_update()
        )
        last_event_statement = (
            select(PixelFlowAgentEventRow)
            .where(PixelFlowAgentEventRow.conversation_id == conversation)
            .order_by(PixelFlowAgentEventRow.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    row = await self._lock_event_sequence_coordination(
                        session,
                        owner,
                        conversation,
                        now=resumed_at,
                        operation_id=operation_id,
                    )
                    if row is None:
                        raise AgentRuntimeRecordConflictError(
                            "Operation 不存在或不属于当前会话"
                        )
                    existing_event_row = (
                        await session.scalars(existing_event_statement)
                    ).one_or_none()
                    if existing_event_row is not None:
                        existing_event = _event_from_row(existing_event_row)
                        current_expiry = None if existing_event_row.lease_expires_at is None else _database_utc(existing_event_row.lease_expires_at)
                        if (
                            row.status == ExternalJobStatus.POLLING.value
                            and row.provider_job_id is not None
                            and row.workflow_id == workflow
                            and row.quota_pause_revision == revision
                            and row.next_poll_at is not None
                            and row.lease_owner is None
                            and row.lease_expires_at is None
                            and _operation_quota_event_matches(
                                existing_event,
                                conversation_id=conversation,
                                record=event_record,
                            )
                            and existing_event_row.delivery_status == "delivering"
                            and existing_event_row.delivery_attempts == 1
                            and existing_event_row.lease_owner == worker
                            and current_expiry == normalized_expiry
                            and current_expiry > resumed_at
                        ):
                            return _operation_from_row(row), EventDeliveryClaim(
                                event=existing_event,
                                delivery_attempts=1,
                                lease_owner=worker,
                                lease_expires_at=normalized_expiry,
                            )
                        raise AgentRuntimeRecordConflictError("Operation quota 恢复重放事件或租约不一致")
                    if (
                        row.status != ExternalJobStatus.POLLING.value
                        or row.provider_job_id is None
                        or row.next_poll_at is not None
                        or row.lease_owner is not None
                        or row.lease_expires_at is not None
                        or row.workflow_id != workflow
                        or row.quota_pause_revision != revision
                    ):
                        raise AgentRuntimeRecordConflictError("Operation quota resume CAS 冲突")

                    last_event_row = (await session.scalars(last_event_statement)).first()
                    if last_event_row is not None and last_event_row.user_id != owner:
                        raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                    sequence = 1 if last_event_row is None else last_event_row.sequence + 1
                    quota_event = _build_operation_quota_event(
                        conversation_id=conversation,
                        sequence=sequence,
                        record=event_record,
                    )
                    row.next_poll_at = resumed_at
                    row.updated_at = resumed_at
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=quota_event.schema_version,
                            event_id=quota_event.event_id,
                            sequence=quota_event.sequence,
                            cursor=quota_event.cursor,
                            conversation_id=quota_event.conversation_id,
                            user_id=owner,
                            run_id=quota_event.run_id,
                            occurred_at=quota_event.occurred_at,
                            event_type=quota_event.type.value,
                            payload_json=quota_event.payload,
                            delivery_status="delivering",
                            delivery_attempts=1,
                            lease_owner=worker,
                            lease_expires_at=normalized_expiry,
                        )
                    )
                    await session.flush()
                    resumed = _operation_from_row(row)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("Operation quota 恢复或事件发生并发冲突") from None
        return resumed, EventDeliveryClaim(
            event=quota_event,
            delivery_attempts=1,
            lease_owner=worker,
            lease_expires_at=normalized_expiry,
        )

    async def schedule_operation_poll(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        operation_id = _require_text("job_id", job_id, 64)
        worker = _require_text("lease_owner", lease_owner, 128)
        normalized_now, normalized_next_poll = _operation_poll_schedule(
            now,
            next_poll_at,
        )
        statement = (
            select(PixelFlowAgentOperationRow)
            .where(
                PixelFlowAgentOperationRow.user_id == owner,
                PixelFlowAgentOperationRow.conversation_id == conversation,
                PixelFlowAgentOperationRow.job_id == operation_id,
            )
            .with_for_update()
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                row = (await session.scalars(statement)).one_or_none()
                current_expiry = None if row is None or row.lease_expires_at is None else _database_utc(row.lease_expires_at)
                if row is None or row.status != ExternalJobStatus.POLLING.value or row.lease_owner != worker or current_expiry is None or current_expiry <= normalized_now:
                    return None
                row.next_poll_at = normalized_next_poll
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = normalized_now
                await session.flush()
                return _operation_from_row(row)
