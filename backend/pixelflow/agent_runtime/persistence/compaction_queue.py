"""conversation 压缩租约与 Turn 队列的 Memory/SQL 双实现。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select

from ..contracts import TurnRecord, TurnStatus
from ..contracts.base import ContractModel
from .models import (
    PixelFlowAgentCompactionLockRow,
    PixelFlowAgentTurnRow,
)
from .repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
    _clone,
    _database_utc,
    _normalize_datetime,
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

    async def finish_compaction(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
    ) -> TurnRecord | None: ...


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
) -> tuple[str, UUID, datetime]:
    owner = _require_text("lease_owner", lease_owner, 128)
    if not isinstance(lease_token, UUID):
        raise ValueError("lease_token must be a UUID")
    return owner, lease_token, _normalize_datetime("now", now)


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
            queued = normalized.model_copy(update={"status": (TurnStatus.QUEUED if lease_state is not None else TurnStatus.ACCEPTED)})
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

    async def finish_compaction(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, token, normalized_now = _finish_parameters(
            lease_owner,
            lease_token,
            now,
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
                expired = state[1].model_copy(update={"lease_expires_at": normalized_now})
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

    async def finish_compaction(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease_owner: str,
        lease_token: UUID,
        now: datetime,
        claim_next: bool,
    ) -> TurnRecord | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            64,
        )
        worker, token, normalized_now = _finish_parameters(
            lease_owner,
            lease_token,
            now,
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
                    coordination.lease_expires_at = normalized_now
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


__all__ = [
    "CompactionLeaseConflictError",
    "CompactionQueueRepository",
    "ConversationCompactionLease",
    "MemoryCompactionQueueRepository",
    "SQLCompactionQueueRepository",
]
