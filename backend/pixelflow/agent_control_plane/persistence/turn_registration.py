"""原子登记 R1 Turn、可见消息、上下文版本和首批 Outbox 事件。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)

from ..contracts import (
    AgentEventType,
    OrchestrationMode,
    TurnRecord,
    TurnStatus,
)
from .compaction_queue import (
    MemoryCompactionQueueRepository,
    SQLCompactionQueueRepository,
)
from .models import (
    PixelFlowAgentCompactionLockRow,
    PixelFlowAgentEventRow,
    PixelFlowAgentTurnRow,
)
from .repositories import (
    _database_utc,
    _repository_write_transaction,
    _turn_from_row,
)

_REGISTRATION_LOCKS = tuple(asyncio.Lock() for _ in range(64))
_MAX_REGISTRATION_ATTEMPTS = 8


class TurnRegistrationContextConflictError(RuntimeError):
    """原子登记时发现调用方上下文版本落后。"""

    def __init__(
        self,
        expected_context_version: int,
        current_context_version: int,
    ) -> None:
        self.expected_context_version = expected_context_version
        self.current_context_version = current_context_version
        super().__init__("Agent Runtime Turn 登记上下文版本冲突")


class TurnRegistrationUnavailableError(RuntimeError):
    """原子登记时发现对话不存在、越权或没有启用 Runtime。"""


@dataclass(frozen=True, slots=True)
class TurnRegistrationResult:
    """返回一次原子登记或同幂等键复用的权威结果。"""

    turn: TurnRecord
    message: PixelFlowConversationMessageRecord
    context_version: int
    orchestration_mode: OrchestrationMode
    created: bool


def _runtime_context_version(context: dict | None) -> int:
    runtime = (context or {}).get(AGENT_RUNTIME_CONTEXT_KEY)
    if not isinstance(runtime, dict) or runtime.get("mode") not in {
        "shadow",
        "assist",
        "primary",
    }:
        raise TurnRegistrationUnavailableError("对话没有启用 Agent Runtime")
    value = runtime.get("context_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TurnRegistrationUnavailableError(
            "对话缺少合法的 Agent Runtime 上下文版本",
        )
    return value


def _message_from_row(
    row: PixelFlowConversationMessageRow,
) -> PixelFlowConversationMessageRecord:
    created_at = _database_utc(row.created_at).isoformat()
    return PixelFlowConversationMessageRecord(
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        role=row.role,
        content=row.content or "",
        payload=deepcopy(row.payload_json or {}),
        created_at=created_at,
    )


def _registration_lock(
    user_id: str,
    conversation_id: str,
) -> asyncio.Lock:
    """让 Memory 双实现和同进程多 Service 共享同一有限锁条带。"""

    return _REGISTRATION_LOCKS[
        hash((user_id, conversation_id)) % len(_REGISTRATION_LOCKS)
    ]


@asynccontextmanager
async def turn_registration_context_read_scope(
    user_id: str,
    conversation_id: str,
) -> AsyncIterator[None]:
    """让 Context 一次性读取与 Memory Turn/响应登记复用同一锁。"""

    async with _registration_lock(user_id, conversation_id):
        yield


class MemoryTurnRegistrationStore:
    """在 Memory 双实现的共享临界区内完成等价原子登记。"""

    def __init__(
        self,
        *,
        repository: MemoryCompactionQueueRepository,
        task_store: MemoryPixelFlowTaskStore,
    ) -> None:
        self._repository = repository
        self._task_store = task_store

    async def register(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: PixelFlowConversationMessageRecord,
        turn: TurnRecord,
        expected_context_version: int,
        occurred_at: datetime,
    ) -> TurnRegistrationResult:
        """共享锁内先判幂等/CAS，再提交不会调用外部系统的内存写入。"""

        async with _registration_lock(user_id, conversation_id):
            conversation = await self._task_store.get_conversation(
                conversation_id,
                user_id=user_id,
            )
            if conversation is None:
                raise TurnRegistrationUnavailableError("Conversation not found")
            existing = await self._repository.get_turn_by_client_input_id(
                user_id,
                conversation_id,
                turn.client_input_id,
            )
            if existing is not None:
                messages = await self._task_store.list_conversation_messages(
                    conversation_id,
                    user_id=user_id,
                )
                stored_message = next(
                    (
                        item
                        for item in messages
                        if item.message_id == message.message_id
                    ),
                    None,
                )
                if stored_message is None:
                    raise TurnRegistrationUnavailableError(
                        "幂等 Turn 缺少对应权威消息",
                    )
                return TurnRegistrationResult(
                    turn=existing,
                    message=deepcopy(stored_message),
                    context_version=_runtime_context_version(
                        conversation.context,
                    ),
                    orchestration_mode=OrchestrationMode(
                        conversation.orchestration_mode,
                    ),
                    created=False,
                )

            current_version = _runtime_context_version(conversation.context)
            if current_version != expected_context_version:
                raise TurnRegistrationContextConflictError(
                    expected_context_version,
                    current_version,
                )

            stored_message = await self._task_store.append_conversation_message(
                deepcopy(message),
            )
            stored_turn = await self._repository.enqueue_turn_for_execution(
                user_id,
                turn,
                now=occurred_at,
            )
            next_version = current_version + 1
            runtime_patch: dict[str, object] = {"context_version": next_version}
            updated = (
                await self._task_store.update_conversation(
                    conversation_id,
                    user_id=user_id,
                    expected_revision=conversation.revision,
                    orchestration_mode=conversation.orchestration_mode,
                    orchestration_version=1,
                    _agent_runtime_patch=runtime_patch,
                )
            )
            if updated is None:
                raise TurnRegistrationUnavailableError("Conversation not found")

            queued_turns = [
                item
                for item in await self._repository.list_turns(
                    user_id,
                    conversation_id,
                )
                if item.status is TurnStatus.QUEUED
            ]
            queue_position = (
                next(
                    (
                        index
                        for index, item in enumerate(queued_turns, start=1)
                        if item.turn_id == stored_turn.turn_id
                    ),
                    None,
                )
                if stored_turn.status is TurnStatus.QUEUED
                else None
            )
            await self._append_memory_events(
                user_id=user_id,
                conversation_id=conversation_id,
                message=stored_message,
                turn=stored_turn,
                queue_position=queue_position,
                occurred_at=occurred_at,
            )
            return TurnRegistrationResult(
                turn=stored_turn,
                message=deepcopy(stored_message),
                context_version=next_version,
                orchestration_mode=OrchestrationMode(
                    updated.orchestration_mode,
                ),
                created=True,
            )

    async def _append_memory_events(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: PixelFlowConversationMessageRecord,
        turn: TurnRecord,
        queue_position: int | None,
        occurred_at: datetime,
    ) -> None:
        """复用 Memory Repository 的连续 sequence 校验追加首批事件。"""

        from ..contracts import AgentEvent

        events = await self._repository.list_events(
            user_id,
            conversation_id,
        )
        next_sequence = 1 if not events else events[-1].sequence + 1
        payloads: list[tuple[AgentEventType, dict]] = []
        payloads.extend((
            (
                AgentEventType.INPUT_STATE_CHANGED,
                {
                    "client_input_id": str(turn.client_input_id),
                    "turn_id": turn.turn_id,
                    "status": turn.status.value,
                    "queue_position": queue_position,
                },
            ),
            (
                AgentEventType.MESSAGE_UPSERTED,
                {"message": message.to_dict()},
            ),
        ))
        for offset, (event_type, payload) in enumerate(payloads):
            event_uuid = uuid4().hex
            await self._repository.create_event(
                user_id,
                AgentEvent(
                    event_id=f"evt_{event_uuid}",
                    sequence=next_sequence + offset,
                    cursor=f"cursor_{event_uuid}",
                    conversation_id=conversation_id,
                    run_id=turn.turn_id,
                    occurred_at=occurred_at,
                    type=event_type,
                    payload=payload,
                ),
            )


class SQLTurnRegistrationStore:
    """用同一数据库事务完成幂等、CAS 和四类首批写入。"""

    def __init__(
        self,
        *,
        repository: SQLCompactionQueueRepository,
        task_store: SQLPixelFlowTaskStore,
    ) -> None:
        if repository._session_factory is not task_store.session_factory:
            raise ValueError("Turn 原子登记必须复用同一个 SQL Session 工厂")
        self._repository = repository
        self._session_factory = task_store.session_factory

    async def register(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: PixelFlowConversationMessageRecord,
        turn: TurnRecord,
        expected_context_version: int,
        occurred_at: datetime,
    ) -> TurnRegistrationResult:
        """锁定 conversation 与压缩协调行，失败时整批回滚。"""

        for attempt in range(_MAX_REGISTRATION_ATTEMPTS):
            try:
                return await self._register_once(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message=message,
                    turn=turn,
                    expected_context_version=expected_context_version,
                    occurred_at=occurred_at,
                )
            except IntegrityError:
                if attempt + 1 == _MAX_REGISTRATION_ATTEMPTS:
                    raise
        raise AssertionError("Turn 原子登记重试循环不应自然结束")

    async def _register_once(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: PixelFlowConversationMessageRecord,
        turn: TurnRecord,
        expected_context_version: int,
        occurred_at: datetime,
    ) -> TurnRegistrationResult:
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._repository._sqlite_write_lock,
            ):
                conversation = (
                    await session.scalars(
                        select(PixelFlowConversationRow)
                        .where(
                            PixelFlowConversationRow.conversation_id
                            == conversation_id,
                            PixelFlowConversationRow.user_id == user_id,
                        )
                        .with_for_update(),
                    )
                ).one_or_none()
                if conversation is None:
                    raise TurnRegistrationUnavailableError(
                        "Conversation not found",
                    )

                existing_turn = (
                    await session.scalars(
                        select(PixelFlowAgentTurnRow)
                        .where(
                            PixelFlowAgentTurnRow.user_id == user_id,
                            PixelFlowAgentTurnRow.conversation_id
                            == conversation_id,
                            PixelFlowAgentTurnRow.client_input_id
                            == str(turn.client_input_id),
                        )
                        .with_for_update(),
                    )
                ).one_or_none()
                if existing_turn is not None:
                    stored_message = await session.get(
                        PixelFlowConversationMessageRow,
                        message.message_id,
                    )
                    if (
                        stored_message is None
                        or stored_message.conversation_id != conversation_id
                    ):
                        raise TurnRegistrationUnavailableError(
                            "幂等 Turn 缺少对应权威消息",
                        )
                    return TurnRegistrationResult(
                        turn=_turn_from_row(existing_turn),
                        message=_message_from_row(stored_message),
                        context_version=_runtime_context_version(
                            conversation.context_json,
                        ),
                        orchestration_mode=OrchestrationMode(
                            conversation.orchestration_mode or "frontend_v2",
                        ),
                        created=False,
                    )

                current_version = _runtime_context_version(
                    conversation.context_json,
                )
                if current_version != expected_context_version:
                    raise TurnRegistrationContextConflictError(
                        expected_context_version,
                        current_version,
                    )

                coordination = (
                    await session.scalars(
                        select(PixelFlowAgentCompactionLockRow)
                        .where(
                            PixelFlowAgentCompactionLockRow.conversation_id
                            == conversation_id,
                        )
                        .with_for_update(),
                    )
                ).one_or_none()
                if coordination is None:
                    coordination = PixelFlowAgentCompactionLockRow(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        state="idle",
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        created_at=occurred_at,
                        updated_at=occurred_at,
                    )
                    session.add(coordination)
                    await session.flush()
                elif coordination.user_id != user_id:
                    raise TurnRegistrationUnavailableError(
                        "conversation 压缩协调行属于其他所有者",
                    )

                execution_owner_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PixelFlowAgentTurnRow)
                        .where(
                            PixelFlowAgentTurnRow.user_id == user_id,
                            PixelFlowAgentTurnRow.conversation_id
                            == conversation_id,
                            PixelFlowAgentTurnRow.status.in_(
                                (
                                    TurnStatus.ACCEPTED.value,
                                    TurnStatus.PROCESSING.value,
                                ),
                            ),
                        ),
                    )
                ) or 0
                stored_status = (
                    TurnStatus.QUEUED
                    if coordination.state in {"active", "retry_required"}
                    or execution_owner_count > 0
                    else TurnStatus.ACCEPTED
                )
                queued_before = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PixelFlowAgentTurnRow)
                        .where(
                            PixelFlowAgentTurnRow.user_id == user_id,
                            PixelFlowAgentTurnRow.conversation_id
                            == conversation_id,
                            PixelFlowAgentTurnRow.status
                            == TurnStatus.QUEUED.value,
                        ),
                    )
                ) or 0
                stored_message = PixelFlowConversationMessageRow(
                    message_id=message.message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role=message.role,
                    content=message.content,
                    payload_json=deepcopy(message.payload),
                    created_at=occurred_at,
                )
                stored_turn = PixelFlowAgentTurnRow(
                    turn_id=turn.turn_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    client_input_id=str(turn.client_input_id),
                    status=stored_status.value,
                    target_workflow_id=turn.target_workflow_id,
                    expected_context_version=expected_context_version,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
                session.add_all((stored_message, stored_turn))

                runtime_context = deepcopy(
                    conversation.context_json or {},
                )
                runtime = deepcopy(
                    runtime_context[AGENT_RUNTIME_CONTEXT_KEY],
                )
                next_version = current_version + 1
                runtime["context_version"] = next_version
                runtime_context[AGENT_RUNTIME_CONTEXT_KEY] = runtime
                conversation.context_json = runtime_context
                conversation.revision += 1
                conversation.updated_at = occurred_at

                current_sequence = (
                    await session.scalar(
                        select(
                            func.max(PixelFlowAgentEventRow.sequence),
                        ).where(
                            PixelFlowAgentEventRow.conversation_id
                            == conversation_id,
                        ),
                    )
                ) or 0
                queue_position = (
                    int(queued_before) + 1
                    if stored_status is TurnStatus.QUEUED
                    else None
                )
                self._append_sql_event_rows(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message=message,
                    turn=turn.model_copy(
                        update={"status": stored_status},
                    ),
                    queue_position=queue_position,
                    occurred_at=occurred_at,
                    current_sequence=int(current_sequence),
                )
                await session.flush()
                return TurnRegistrationResult(
                    turn=_turn_from_row(stored_turn),
                    message=_message_from_row(stored_message),
                    context_version=next_version,
                    orchestration_mode=OrchestrationMode(
                        conversation.orchestration_mode or "frontend_v2",
                    ),
                    created=True,
                )

    @staticmethod
    def _append_sql_event_rows(
        *,
        session,
        user_id: str,
        conversation_id: str,
        message: PixelFlowConversationMessageRecord,
        turn: TurnRecord,
        queue_position: int | None,
        occurred_at: datetime,
        current_sequence: int,
    ) -> None:
        payloads: list[tuple[AgentEventType, dict]] = []
        payloads.extend((
            (
                AgentEventType.INPUT_STATE_CHANGED,
                {
                    "client_input_id": str(turn.client_input_id),
                    "turn_id": turn.turn_id,
                    "status": turn.status.value,
                    "queue_position": queue_position,
                },
            ),
            (
                AgentEventType.MESSAGE_UPSERTED,
                {"message": message.to_dict()},
            ),
        ))
        rows: list[PixelFlowAgentEventRow] = []
        for offset, (event_type, payload) in enumerate(payloads, start=1):
            event_uuid = uuid4().hex
            rows.append(
                PixelFlowAgentEventRow(
                    schema_version=1,
                    event_id=f"evt_{event_uuid}",
                    sequence=current_sequence + offset,
                    cursor=f"cursor_{event_uuid}",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    run_id=turn.turn_id,
                    occurred_at=occurred_at,
                    event_type=event_type.value,
                    payload_json=payload,
                    delivery_status="pending",
                    delivery_attempts=0,
                    lease_owner=None,
                    lease_expires_at=None,
                    published_at=None,
                )
            )
        session.add_all(rows)


def make_turn_registration_store(
    *,
    repository,
    task_store,
):
    """按已装配的双实现选择相同语义的原子登记 Store。"""

    if isinstance(
        repository,
        SQLCompactionQueueRepository,
    ) and isinstance(task_store, SQLPixelFlowTaskStore):
        return SQLTurnRegistrationStore(
            repository=repository,
            task_store=task_store,
        )
    if isinstance(
        repository,
        MemoryCompactionQueueRepository,
    ) and isinstance(task_store, MemoryPixelFlowTaskStore):
        return MemoryTurnRegistrationStore(
            repository=repository,
            task_store=task_store,
        )
    raise TypeError("Agent Runtime Repository 与 Task Store 双实现不匹配")


__all__ = [
    "MemoryTurnRegistrationStore",
    "SQLTurnRegistrationStore",
    "TurnRegistrationContextConflictError",
    "TurnRegistrationResult",
    "TurnRegistrationUnavailableError",
    "make_turn_registration_store",
    "turn_registration_context_read_scope",
]
