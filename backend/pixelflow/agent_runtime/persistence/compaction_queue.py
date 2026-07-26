"""conversation 压缩租约与 Turn 队列的 Memory/SQL 双实现。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import Field, JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..contracts import AgentEvent, AgentEventType, TurnRecord, TurnStatus
from ..contracts.base import ContractModel
from .models import (
    PixelFlowAgentCompactionLockRow,
    PixelFlowAgentEventRow,
    PixelFlowAgentTurnRow,
)
from .repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
    _clone,
    _database_utc,
    _MemoryEventDeliveryState,
    _normalize_datetime,
    _normalize_event,
    _normalize_turn,
    _repository_write_transaction,
    _require_text,
    _turn_from_row,
)


class CompactionLeaseConflictError(RuntimeError):
    """压缩租约已过期、被接管或不属于当前 worker。"""


class ConversationCompactionLease(ContractModel):
    """conversation 压缩的可恢复租约和 fencing token。"""

    conversation_id: str = Field(min_length=1)
    lease_owner: str = Field(min_length=1)
    lease_token: UUID
    lease_expires_at: datetime


@runtime_checkable
class CompactionQueueRepository(Protocol):
    """约束压缩锁、持续入队和原子领取使用同一持久化语义。"""

    async def acquire_compaction_lease(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> ConversationCompactionLease | None: ...

    async def get_compaction_lease(
        self,
        user_id: str,
        conversation_id: str,
    ) -> ConversationCompactionLease | None: ...

    async def claim_next_turn(
        self,
        user_id: str,
        conversation_id: str,
    ) -> TurnRecord | None: ...

    async def enqueue_turn_for_execution(
        self,
        user_id: str,
        record: TurnRecord,
        *,
        now: datetime,
    ) -> TurnRecord: ...

    async def complete_turn_and_claim_next(
        self,
        user_id: str,
        conversation_id: str,
        *,
        turn_id: str,
        now: datetime,
    ) -> tuple[TurnRecord, TurnRecord | None]: ...

    async def finish_compaction(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
        retry_not_before: datetime | None = None,
    ) -> TurnRecord | None: ...

    async def finish_compaction_with_event(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
        retry_not_before: datetime | None = None,
        run_id: str,
        event_type: AgentEventType,
        payload: dict[str, JsonValue],
    ) -> tuple[TurnRecord | None, AgentEvent]: ...


def _lease_parameters(
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


def _finish_parameters(
    lease_owner: str,
    lease_token: UUID,
    now: datetime,
    claim_next: bool,
    retry_not_before: datetime | None,
) -> tuple[str, UUID, datetime, datetime | None]:
    owner = _require_text("lease_owner", lease_owner, 128)
    if not isinstance(lease_token, UUID):
        raise ValueError("lease_token must be a UUID")
    normalized_now = _normalize_datetime("now", now)
    if claim_next:
        if retry_not_before is not None:
            raise ValueError("claim_next=true 时 retry_not_before 必须为空")
        return owner, lease_token, normalized_now, None
    if retry_not_before is None:
        raise ValueError("claim_next=false 时必须提供 retry_not_before")
    normalized_retry = _normalize_datetime(
        "retry_not_before",
        retry_not_before,
    )
    if normalized_retry <= normalized_now:
        raise ValueError("retry_not_before 必须晚于 now")
    return owner, lease_token, normalized_now, normalized_retry


def _terminal_event(
    *,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    event_type: AgentEventType,
    payload: dict[str, JsonValue],
) -> AgentEvent:
    """只允许在租约收尾事务中构造压缩终态事件。"""

    if event_type not in {
        AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
        AgentEventType.CONTEXT_COMPRESSION_FAILED,
    }:
        raise ValueError("压缩租约收尾只接受 completed 或 failed 事件")
    event_uuid = uuid4().hex
    return _normalize_event(
        AgentEvent(
            event_id=f"evt_{event_uuid}",
            sequence=sequence,
            cursor=f"cursor_{event_uuid}",
            conversation_id=conversation_id,
            run_id=_require_text("run_id", run_id, 64),
            occurred_at=occurred_at,
            type=event_type,
            payload=payload,
        )
    )


def _execution_turn(record: TurnRecord) -> TurnRecord:
    normalized = _normalize_turn(record)
    if normalized.status not in {
        TurnStatus.ACCEPTED,
        TurnStatus.QUEUED,
    }:
        raise ValueError("待执行 Turn 只能使用 accepted 或 queued 状态")
    return normalized


def _lease_from_row(
    row: PixelFlowAgentCompactionLockRow,
) -> ConversationCompactionLease:
    if row.state not in {"active", "retry_required"} or row.lease_owner is None or row.lease_token is None or row.lease_expires_at is None:
        raise CompactionLeaseConflictError("压缩协调行缺少有效租约字段")
    return ConversationCompactionLease(
        conversation_id=row.conversation_id,
        lease_owner=row.lease_owner,
        lease_token=UUID(row.lease_token),
        lease_expires_at=_database_utc(row.lease_expires_at),
    )


class MemoryCompactionQueueRepository(MemoryAgentRuntimeRepository):
    """在 M01 Memory Repository 上增加可验证的 conversation 压缩状态。"""

    def __init__(self) -> None:
        super().__init__()
        self._compaction_leases: dict[
            str,
            tuple[str, ConversationCompactionLease],
        ] = {}
        self._compaction_write_lock = asyncio.Lock()

    async def create_turn(
        self,
        user_id: str,
        record: TurnRecord,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        async with self._compaction_write_lock:
            lease_state = self._compaction_leases.get(normalized.conversation_id)
            if lease_state is not None and lease_state[0] != owner:
                raise AgentRuntimeRecordConflictError("conversation 压缩租约已经属于其他所有者")
            stored = normalized
            if lease_state is not None:
                if normalized.status not in {
                    TurnStatus.ACCEPTED,
                    TurnStatus.QUEUED,
                }:
                    raise AgentRuntimeRecordConflictError("压缩未结束时只能保存待执行 Turn")
                stored = normalized.model_copy(update={"status": TurnStatus.QUEUED})
            return await MemoryAgentRuntimeRepository.create_turn(
                self,
                owner,
                stored,
            )

    async def enqueue_turn(
        self,
        user_id: str,
        record: TurnRecord,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _normalize_turn(record)
        async with self._compaction_write_lock:
            lease_state = self._compaction_leases.get(normalized.conversation_id)
            if lease_state is not None and lease_state[0] != owner:
                raise AgentRuntimeRecordConflictError("conversation 压缩租约已经属于其他所有者")
            stored = normalized
            if lease_state is not None:
                if normalized.status not in {
                    TurnStatus.ACCEPTED,
                    TurnStatus.QUEUED,
                }:
                    raise AgentRuntimeRecordConflictError("压缩未结束时只能保存待执行 Turn")
                stored = normalized.model_copy(update={"status": TurnStatus.QUEUED})
            client_key = (
                stored.conversation_id,
                str(stored.client_input_id),
            )
            existing_owner_key = self._turn_client_keys.get(client_key)
            if existing_owner_key is not None:
                if existing_owner_key[0] != owner:
                    raise AgentRuntimeRecordConflictError("Turn 幂等键已经被其他所有者占用")
                return _clone(self._turns[existing_owner_key])
            return await MemoryAgentRuntimeRepository.create_turn(
                self,
                owner,
                stored,
            )

    async def acquire_compaction_lease(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> ConversationCompactionLease | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, normalized_now, normalized_expiry = _lease_parameters(
            lease_owner,
            now,
            lease_expires_at,
        )
        async with self._compaction_write_lock:
            existing_state = self._compaction_leases.get(conversation)
            if existing_state is not None:
                existing_owner, existing = existing_state
                if existing_owner != owner:
                    raise AgentRuntimeRecordConflictError("conversation 压缩租约已经属于其他所有者")
                if existing.lease_expires_at > normalized_now:
                    return None

            owner_keys = [owner_key for owner_key, turn in self._turns.items() if owner_key[0] == owner and turn.conversation_id == conversation]
            if any(self._turns[owner_key].status is TurnStatus.PROCESSING for owner_key in owner_keys):
                return None
            for owner_key in owner_keys:
                turn = self._turns[owner_key]
                if turn.status is TurnStatus.ACCEPTED:
                    self._turns[owner_key] = _clone(turn.model_copy(update={"status": TurnStatus.QUEUED}))

            lease = ConversationCompactionLease(
                conversation_id=conversation,
                lease_owner=worker,
                lease_token=uuid4(),
                lease_expires_at=normalized_expiry,
            )
            self._compaction_leases[conversation] = (
                owner,
                _clone(lease),
            )
            return _clone(lease)

    async def get_compaction_lease(
        self,
        user_id: str,
        conversation_id: str,
    ) -> ConversationCompactionLease | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        state = self._compaction_leases.get(conversation)
        if state is None or state[0] != owner:
            return None
        return _clone(state[1])

    async def enqueue_turn_for_execution(
        self,
        user_id: str,
        record: TurnRecord,
        *,
        now: datetime,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _execution_turn(record)
        _normalize_datetime("now", now)
        async with self._compaction_write_lock:
            lease_state = self._compaction_leases.get(normalized.conversation_id)
            if lease_state is not None and lease_state[0] != owner:
                raise AgentRuntimeRecordConflictError("conversation 压缩租约已经属于其他所有者")
            has_execution_owner = any(
                turn.conversation_id == normalized.conversation_id
                and turn.status
                in {
                    TurnStatus.ACCEPTED,
                    TurnStatus.PROCESSING,
                }
                for (turn_owner, _), turn in self._turns.items()
                if turn_owner == owner
            )
            queued = normalized.model_copy(
                update={
                    "status": (
                        TurnStatus.QUEUED
                        if lease_state is not None or has_execution_owner
                        else TurnStatus.ACCEPTED
                    ),
                },
            )
            client_key = (
                queued.conversation_id,
                str(queued.client_input_id),
            )
            existing_owner_key = self._turn_client_keys.get(client_key)
            if existing_owner_key is not None:
                if existing_owner_key[0] != owner:
                    raise AgentRuntimeRecordConflictError("Turn 幂等键已经被其他所有者占用")
                return _clone(self._turns[existing_owner_key])
            return await MemoryAgentRuntimeRepository.create_turn(
                self,
                owner,
                queued,
            )

    async def claim_next_turn(
        self,
        user_id: str,
        conversation_id: str,
    ) -> TurnRecord | None:
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        async with self._compaction_write_lock:
            if conversation in self._compaction_leases:
                return None
            return await super().claim_next_turn(
                user_id,
                conversation,
            )

    async def complete_turn_and_claim_next(
        self,
        user_id: str,
        conversation_id: str,
        *,
        turn_id: str,
        now: datetime,
    ) -> tuple[TurnRecord, TurnRecord | None]:
        """确认旧 v2 已持久化接力点，再按 Inbox 顺序领取下一 Turn。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        normalized_now = _normalize_datetime("now", now)
        normalized_turn_id = _require_text("turn_id", turn_id, 64)
        async with self._compaction_write_lock:
            owner_key = (owner, normalized_turn_id)
            current = self._turns.get(owner_key)
            if current is None or current.conversation_id != conversation:
                raise AgentRuntimeRecordConflictError("Turn 不存在或不属于当前会话")
            if current.status not in {
                TurnStatus.ACCEPTED,
                TurnStatus.PROCESSING,
                TurnStatus.COMPLETED,
            }:
                raise AgentRuntimeRecordConflictError("只有已接力 Turn 可以完成")
            completed = current.model_copy(
                update={"status": TurnStatus.COMPLETED},
            )
            self._turns[owner_key] = _clone(completed)
            if conversation in self._compaction_leases:
                return _clone(completed), None
            active = [
                turn
                for (record_owner, _), turn in self._turns.items()
                if record_owner == owner
                and turn.conversation_id == conversation
                and turn.turn_id != normalized_turn_id
                and turn.status is TurnStatus.PROCESSING
            ]
            if active:
                return _clone(completed), None
            candidates = [
                (key, turn)
                for key, turn in self._turns.items()
                if key[0] == owner
                and turn.conversation_id == conversation
                and turn.status
                in {TurnStatus.ACCEPTED, TurnStatus.QUEUED}
            ]
            candidates.sort(
                key=lambda item: (
                    item[1].created_at,
                    item[1].turn_id,
                ),
            )
            if not candidates:
                return _clone(completed), None
            next_key, next_turn = candidates[0]
            claimed = next_turn.model_copy(
                update={"status": TurnStatus.PROCESSING},
            )
            self._turns[next_key] = _clone(claimed)
            del normalized_now
            return _clone(completed), _clone(claimed)

    async def finish_compaction(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
        retry_not_before: datetime | None = None,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, token, normalized_now, normalized_retry = _finish_parameters(
            lease_owner,
            lease_token,
            now,
            claim_next,
            retry_not_before,
        )
        async with self._compaction_write_lock:
            state = self._compaction_leases.get(conversation)
            if state is None or state[0] != owner or state[1].lease_owner != worker or state[1].lease_token != token or state[1].lease_expires_at <= normalized_now:
                raise CompactionLeaseConflictError("压缩租约已失效，拒绝陈旧 worker 收尾")
            owner_keys = [
                owner_key
                for owner_key, turn in self._turns.items()
                if owner_key[0] == owner
                and turn.conversation_id == conversation
                and turn.status
                in {
                    TurnStatus.ACCEPTED,
                    TurnStatus.QUEUED,
                    TurnStatus.PROCESSING,
                }
            ]
            owner_keys.sort(key=self._turn_owner_sequences.__getitem__)
            if claim_next and any(self._turns[owner_key].status is TurnStatus.PROCESSING for owner_key in owner_keys):
                raise CompactionLeaseConflictError("压缩结束时已有 processing Turn，拒绝重复领取")
            if not claim_next:
                expired = state[1].model_copy(
                    update={"lease_expires_at": normalized_retry},
                )
                self._compaction_leases[conversation] = (
                    owner,
                    _clone(expired),
                )
                return None
            del self._compaction_leases[conversation]
            candidates = [owner_key for owner_key in owner_keys if self._turns[owner_key].status in {TurnStatus.ACCEPTED, TurnStatus.QUEUED}]
            if not candidates:
                return None
            owner_key = candidates[0]
            claimed = self._turns[owner_key].model_copy(update={"status": TurnStatus.PROCESSING})
            self._turns[owner_key] = _clone(claimed)
            return _clone(claimed)

    async def finish_compaction_with_event(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
        retry_not_before: datetime | None = None,
        run_id: str,
        event_type: AgentEventType,
        payload: dict[str, JsonValue],
    ) -> tuple[TurnRecord | None, AgentEvent]:
        """在同一内存临界区校验 fencing、写终态事件并迁移队列。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, token, normalized_now, normalized_retry = _finish_parameters(
            lease_owner,
            lease_token,
            now,
            claim_next,
            retry_not_before,
        )
        async with self._compaction_write_lock:
            state = self._compaction_leases.get(conversation)
            if state is None or state[0] != owner or state[1].lease_owner != worker or state[1].lease_token != token or state[1].lease_expires_at <= normalized_now:
                raise CompactionLeaseConflictError("压缩租约已失效，拒绝陈旧 worker 收尾")
            owner_keys = [
                owner_key
                for owner_key, turn in self._turns.items()
                if owner_key[0] == owner
                and turn.conversation_id == conversation
                and turn.status
                in {
                    TurnStatus.ACCEPTED,
                    TurnStatus.QUEUED,
                    TurnStatus.PROCESSING,
                }
            ]
            owner_keys.sort(key=self._turn_owner_sequences.__getitem__)
            if claim_next and any(self._turns[owner_key].status is TurnStatus.PROCESSING for owner_key in owner_keys):
                raise CompactionLeaseConflictError("压缩结束时已有 processing Turn，拒绝重复领取")

            async with self._event_write_lock:
                conversation_records = [(record_owner, existing) for (record_owner, _), existing in self._events.items() if existing.conversation_id == conversation]
                if any(record_owner != owner for record_owner, _ in conversation_records):
                    raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                event = _terminal_event(
                    sequence=(1 if not conversation_records else max(existing.sequence for _, existing in conversation_records) + 1),
                    conversation_id=conversation,
                    run_id=run_id,
                    occurred_at=normalized_now,
                    event_type=event_type,
                    payload=payload,
                )
                sequence_key = (event.conversation_id, event.sequence)
                cursor_key = (event.conversation_id, event.cursor)
                if event.event_id in self._event_ids or sequence_key in self._event_sequence_keys or cursor_key in self._event_cursor_keys:
                    raise AgentRuntimeRecordConflictError("AgentEvent 记录已存在")

                claimed: TurnRecord | None = None
                if claim_next:
                    del self._compaction_leases[conversation]
                    candidates = [
                        owner_key
                        for owner_key in owner_keys
                        if self._turns[owner_key].status
                        in {
                            TurnStatus.ACCEPTED,
                            TurnStatus.QUEUED,
                        }
                    ]
                    if candidates:
                        claimed_key = candidates[0]
                        claimed = self._turns[claimed_key].model_copy(update={"status": TurnStatus.PROCESSING})
                        self._turns[claimed_key] = _clone(claimed)
                else:
                    expired = state[1].model_copy(
                        update={"lease_expires_at": normalized_retry},
                    )
                    self._compaction_leases[conversation] = (
                        owner,
                        _clone(expired),
                    )

                owner_key = (owner, event.event_id)
                self._event_ids.add(event.event_id)
                self._event_sequence_keys.add(sequence_key)
                self._event_cursor_keys.add(cursor_key)
                self._events[owner_key] = _clone(event)
                self._event_delivery[owner_key] = _MemoryEventDeliveryState()
                return (
                    None if claimed is None else _clone(claimed),
                    _clone(event),
                )


class SQLCompactionQueueRepository(SQLAgentRuntimeRepository):
    """用永久协调行、短事务和 fencing token 串行化压缩与 Turn。"""

    @staticmethod
    def _coordination_statement(
        conversation_id: str,
    ):
        return select(PixelFlowAgentCompactionLockRow).where(PixelFlowAgentCompactionLockRow.conversation_id == conversation_id).with_for_update()

    @staticmethod
    def _active_turn_statement(
        user_id: str,
        conversation_id: str,
    ):
        return (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == user_id,
                PixelFlowAgentTurnRow.conversation_id == conversation_id,
                PixelFlowAgentTurnRow.status.in_(
                    (
                        TurnStatus.ACCEPTED.value,
                        TurnStatus.QUEUED.value,
                        TurnStatus.PROCESSING.value,
                    )
                ),
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
            .with_for_update()
        )

    async def acquire_compaction_lease(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> ConversationCompactionLease | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, normalized_now, normalized_expiry = _lease_parameters(
            lease_owner,
            now,
            lease_expires_at,
        )
        await self._ensure_compaction_coordination_row(
            owner,
            conversation,
            now=normalized_now,
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                coordination = (await session.scalars(self._coordination_statement(conversation))).one()
                if coordination.user_id != owner:
                    raise AgentRuntimeRecordConflictError("conversation 压缩协调行已经属于其他所有者")
                if coordination.state == "active":
                    existing_lease = _lease_from_row(coordination)
                    if existing_lease.lease_expires_at > normalized_now:
                        return None
                elif coordination.state not in {"idle", "retry_required"}:
                    raise CompactionLeaseConflictError("conversation 压缩协调状态不合法")

                turns = (await session.scalars(self._active_turn_statement(owner, conversation))).all()
                if any(turn.status == TurnStatus.PROCESSING.value for turn in turns):
                    return None
                for turn in turns:
                    if turn.status == TurnStatus.ACCEPTED.value:
                        turn.status = TurnStatus.QUEUED.value
                        turn.updated_at = normalized_now

                coordination.state = "active"
                coordination.lease_owner = worker
                coordination.lease_token = str(uuid4())
                coordination.lease_expires_at = normalized_expiry
                coordination.updated_at = normalized_now
                await session.flush()
                return _lease_from_row(coordination)

    async def get_compaction_lease(
        self,
        user_id: str,
        conversation_id: str,
    ) -> ConversationCompactionLease | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        statement = select(PixelFlowAgentCompactionLockRow).where(
            PixelFlowAgentCompactionLockRow.user_id == owner,
            PixelFlowAgentCompactionLockRow.conversation_id == conversation,
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        if row is None or row.state == "idle":
            return None
        return _lease_from_row(row)

    async def enqueue_turn_for_execution(
        self,
        user_id: str,
        record: TurnRecord,
        *,
        now: datetime,
    ) -> TurnRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized = _execution_turn(record)
        _normalize_datetime("now", now)
        return await super().enqueue_turn(owner, normalized)

    async def claim_next_turn(
        self,
        user_id: str,
        conversation_id: str,
    ) -> TurnRecord | None:
        return await super().claim_next_turn(
            user_id,
            conversation_id,
        )

    async def complete_turn_and_claim_next(
        self,
        user_id: str,
        conversation_id: str,
        *,
        turn_id: str,
        now: datetime,
    ) -> tuple[TurnRecord, TurnRecord | None]:
        """在协调行短事务中完成旧 v2 接力并领取最早排队输入。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        normalized_turn_id = _require_text("turn_id", turn_id, 64)
        normalized_now = _normalize_datetime("now", now)
        await self._ensure_compaction_coordination_row(
            owner,
            conversation,
            now=normalized_now,
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                coordination = (
                    await session.scalars(
                        self._coordination_statement(conversation),
                    )
                ).one()
                if coordination.user_id != owner:
                    raise AgentRuntimeRecordConflictError(
                        "conversation 压缩协调行已经属于其他所有者",
                    )
                target = (
                    await session.scalars(
                        select(PixelFlowAgentTurnRow)
                        .where(
                            PixelFlowAgentTurnRow.user_id == owner,
                            PixelFlowAgentTurnRow.conversation_id
                            == conversation,
                            PixelFlowAgentTurnRow.turn_id
                            == normalized_turn_id,
                        )
                        .with_for_update(),
                    )
                ).one_or_none()
                if target is None:
                    raise AgentRuntimeRecordConflictError(
                        "Turn 不存在或不属于当前会话",
                    )
                if target.status not in {
                    TurnStatus.ACCEPTED.value,
                    TurnStatus.PROCESSING.value,
                    TurnStatus.COMPLETED.value,
                }:
                    raise AgentRuntimeRecordConflictError(
                        "只有已接力 Turn 可以完成",
                    )
                target.status = TurnStatus.COMPLETED.value
                target.updated_at = normalized_now
                if coordination.state != "idle":
                    await session.flush()
                    return _turn_from_row(target), None

                turns = (
                    await session.scalars(
                        self._active_turn_statement(
                            owner,
                            conversation,
                        ),
                    )
                ).all()
                if any(
                    turn.status == TurnStatus.PROCESSING.value
                    for turn in turns
                ):
                    await session.flush()
                    return _turn_from_row(target), None
                next_turn = next(
                    (
                        turn
                        for turn in turns
                        if turn.status
                        in {
                            TurnStatus.ACCEPTED.value,
                            TurnStatus.QUEUED.value,
                        }
                    ),
                    None,
                )
                if next_turn is not None:
                    next_turn.status = TurnStatus.PROCESSING.value
                    next_turn.updated_at = normalized_now
                await session.flush()
                return (
                    _turn_from_row(target),
                    None
                    if next_turn is None
                    else _turn_from_row(next_turn),
                )

    async def finish_compaction(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
        retry_not_before: datetime | None = None,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, token, normalized_now, normalized_retry = _finish_parameters(
            lease_owner,
            lease_token,
            now,
            claim_next,
            retry_not_before,
        )
        await self._ensure_compaction_coordination_row(
            owner,
            conversation,
            now=normalized_now,
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                coordination = (await session.scalars(self._coordination_statement(conversation))).one()
                if coordination.user_id != owner or coordination.state != "active":
                    raise CompactionLeaseConflictError("压缩租约已失效，拒绝陈旧 worker 收尾")
                current_lease = _lease_from_row(coordination)
                if current_lease.lease_owner != worker or current_lease.lease_token != token or current_lease.lease_expires_at <= normalized_now:
                    raise CompactionLeaseConflictError("压缩租约已失效，拒绝陈旧 worker 收尾")
                turns = (await session.scalars(self._active_turn_statement(owner, conversation))).all()
                if claim_next and any(turn.status == TurnStatus.PROCESSING.value for turn in turns):
                    raise CompactionLeaseConflictError("压缩结束时已有 processing Turn，拒绝重复领取")
                if not claim_next:
                    coordination.state = "retry_required"
                    coordination.lease_expires_at = normalized_retry
                    coordination.updated_at = normalized_now
                    await session.flush()
                    return None

                coordination.state = "idle"
                coordination.lease_owner = None
                coordination.lease_token = None
                coordination.lease_expires_at = None
                coordination.updated_at = normalized_now
                candidates = [
                    turn
                    for turn in turns
                    if turn.status
                    in {
                        TurnStatus.ACCEPTED.value,
                        TurnStatus.QUEUED.value,
                    }
                ]
                if not candidates:
                    await session.flush()
                    return None
                claimed = candidates[0]
                claimed.status = TurnStatus.PROCESSING.value
                claimed.updated_at = normalized_now
                await session.flush()
                return _turn_from_row(claimed)

    async def finish_compaction_with_event(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
        retry_not_before: datetime | None = None,
        run_id: str,
        event_type: AgentEventType,
        payload: dict[str, JsonValue],
    ) -> tuple[TurnRecord | None, AgentEvent]:
        """在同一数据库事务中校验 fencing、写终态 Outbox 并迁移队列。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, token, normalized_now, normalized_retry = _finish_parameters(
            lease_owner,
            lease_token,
            now,
            claim_next,
            retry_not_before,
        )
        await self._ensure_compaction_coordination_row(
            owner,
            conversation,
            now=normalized_now,
        )
        last_event_statement = select(PixelFlowAgentEventRow).where(PixelFlowAgentEventRow.conversation_id == conversation).order_by(PixelFlowAgentEventRow.sequence.desc()).limit(1).with_for_update()
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    coordination = (await session.scalars(self._coordination_statement(conversation))).one()
                    if coordination.user_id != owner or coordination.state != "active":
                        raise CompactionLeaseConflictError("压缩租约已失效，拒绝陈旧 worker 收尾")
                    current_lease = _lease_from_row(coordination)
                    if current_lease.lease_owner != worker or current_lease.lease_token != token or current_lease.lease_expires_at <= normalized_now:
                        raise CompactionLeaseConflictError("压缩租约已失效，拒绝陈旧 worker 收尾")
                    turns = (await session.scalars(self._active_turn_statement(owner, conversation))).all()
                    if claim_next and any(turn.status == TurnStatus.PROCESSING.value for turn in turns):
                        raise CompactionLeaseConflictError("压缩结束时已有 processing Turn，拒绝重复领取")

                    last_event = (await session.scalars(last_event_statement)).first()
                    if last_event is not None and last_event.user_id != owner:
                        raise AgentRuntimeRecordConflictError("AgentEvent conversation 已被其他所有者占用")
                    event = _terminal_event(
                        sequence=(1 if last_event is None else last_event.sequence + 1),
                        conversation_id=conversation,
                        run_id=run_id,
                        occurred_at=normalized_now,
                        event_type=event_type,
                        payload=payload,
                    )
                    session.add(
                        PixelFlowAgentEventRow(
                            schema_version=event.schema_version,
                            event_id=event.event_id,
                            sequence=event.sequence,
                            cursor=event.cursor,
                            conversation_id=event.conversation_id,
                            user_id=owner,
                            run_id=event.run_id,
                            occurred_at=event.occurred_at,
                            event_type=event.type.value,
                            payload_json=event.payload,
                            delivery_status="pending",
                            delivery_attempts=0,
                        )
                    )

                    claimed: TurnRecord | None = None
                    if claim_next:
                        coordination.state = "idle"
                        coordination.lease_owner = None
                        coordination.lease_token = None
                        coordination.lease_expires_at = None
                        candidates = [
                            turn
                            for turn in turns
                            if turn.status
                            in {
                                TurnStatus.ACCEPTED.value,
                                TurnStatus.QUEUED.value,
                            }
                        ]
                        if candidates:
                            claimed_row = candidates[0]
                            claimed_row.status = TurnStatus.PROCESSING.value
                            claimed_row.updated_at = normalized_now
                            claimed = _turn_from_row(claimed_row)
                    else:
                        coordination.state = "retry_required"
                        coordination.lease_expires_at = normalized_retry
                    coordination.updated_at = normalized_now
                    await session.flush()
                    return claimed, _clone(event)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("压缩收尾事件唯一键冲突") from None


__all__ = [
    "CompactionLeaseConflictError",
    "CompactionQueueRepository",
    "ConversationCompactionLease",
    "MemoryCompactionQueueRepository",
    "SQLCompactionQueueRepository",
]
