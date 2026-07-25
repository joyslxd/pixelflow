"""把压缩生命周期状态追加到 M01 Event Outbox。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import JsonValue

from ..contracts import AgentEvent, AgentEventType
from ..persistence import AgentRuntimeRecordConflictError


@runtime_checkable
class CompactionEventSink(Protocol):
    """隔离压缩编排和具体 Event Repository。"""

    def is_bound_to(self, repository: object) -> bool:
        """确认事件和队列使用同一个可原子收尾的 Repository。"""

        ...

    async def append(
        self,
        user_id: str,
        *,
        conversation_id: str,
        run_id: str,
        event_type: AgentEventType,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> AgentEvent: ...


class _EventOutboxRepository(Protocol):
    """描述追加压缩事件所需的最小 M01 Repository 能力。"""

    async def list_events(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[AgentEvent]: ...

    async def create_event(
        self,
        user_id: str,
        record: AgentEvent,
    ) -> AgentEvent: ...


class CompactionEventPersistenceError(RuntimeError):
    """压缩事件无法按连续 sequence 持久化。"""


class RepositoryCompactionEventOutbox:
    """复用 M01 Outbox，并在并发 sequence 冲突后重新读取尾部。"""

    def __init__(
        self,
        *,
        repository: _EventOutboxRepository,
        max_append_attempts: int = 8,
    ) -> None:
        if isinstance(max_append_attempts, bool) or max_append_attempts < 1:
            raise ValueError("max_append_attempts 必须是大于零的整数")
        self._repository = repository
        self._max_append_attempts = max_append_attempts

    def is_bound_to(self, repository: object) -> bool:
        """终态事件必须和压缩协调行共享同一个 Repository。"""

        return self._repository is repository

    async def append(
        self,
        user_id: str,
        *,
        conversation_id: str,
        run_id: str,
        event_type: AgentEventType,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> AgentEvent:
        """生成不透明 ID/cursor，并让 M01 Repository 校验最终连续性。"""

        for _ in range(self._max_append_attempts):
            existing = await self._repository.list_events(
                user_id,
                conversation_id,
            )
            event_uuid = uuid4().hex
            event = AgentEvent(
                event_id=f"evt_{event_uuid}",
                sequence=(existing[-1].sequence + 1 if existing else 1),
                cursor=f"cursor_{event_uuid}",
                conversation_id=conversation_id,
                run_id=run_id,
                occurred_at=occurred_at,
                type=event_type,
                payload=payload,
            )
            try:
                return await self._repository.create_event(user_id, event)
            except AgentRuntimeRecordConflictError:
                continue
        raise CompactionEventPersistenceError("压缩事件追加冲突次数超过安全上限")


__all__ = [
    "CompactionEventPersistenceError",
    "CompactionEventSink",
    "RepositoryCompactionEventOutbox",
]
