"""原生 Agent 事件 Outbox 发布助手。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pixelflow.agent_runtime.contracts import AgentEvent
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.video_agent.events import native as native_events


class NativeAgentEventPublisher:
    """按 conversation 递增 sequence 写入公开事件。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        owner = user_id.strip()
        conversation = conversation_id.strip()
        turn = turn_id.strip()
        if not owner or not conversation or not turn:
            raise ValueError("NativeAgentEventPublisher 需要 user/conversation/turn")
        self._repository = repository
        self._user_id = owner
        self._conversation_id = conversation
        self._turn_id = turn
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _next_sequence(self) -> int:
        events = await self._repository.list_events(
            self._user_id,
            self._conversation_id,
        )
        return 1 if not events else events[-1].sequence + 1

    def _ids(self, kind: str, *parts: str) -> tuple[str, str]:
        seed = ":".join((kind, self._conversation_id, self._turn_id, *parts))
        event_id = str(uuid5(NAMESPACE_URL, f"video-native-event:{seed}"))
        cursor = str(uuid5(NAMESPACE_URL, f"video-native-cursor:{seed}"))
        return event_id, cursor

    async def _emit(self, event: AgentEvent) -> AgentEvent:
        return await self._repository.create_event(self._user_id, event)

    async def tool_started(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        plan_id: str | None = None,
        step_id: str | None = None,
        title: str | None = None,
    ) -> AgentEvent:
        occurred_at = self._clock()
        event_id, cursor = self._ids("tool-started", tool_call_id)
        return await self._emit(
            native_events.build_tool_started_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                plan_id=plan_id,
                step_id=step_id,
                title=title,
            )
        )

    async def tool_completed(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        public_summary: str,
        artifact_refs: tuple[str, ...] = (),
        duration_ms: int | None = None,
    ) -> AgentEvent:
        occurred_at = self._clock()
        event_id, cursor = self._ids("tool-completed", tool_call_id)
        return await self._emit(
            native_events.build_tool_completed_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                public_summary=public_summary,
                artifact_refs=artifact_refs,
                duration_ms=duration_ms,
            )
        )

    async def tool_failed(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        public_summary: str,
    ) -> AgentEvent:
        occurred_at = self._clock()
        event_id, cursor = self._ids("tool-failed", tool_call_id)
        return await self._emit(
            native_events.build_tool_failed_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                public_summary=public_summary,
            )
        )

    async def response_delta(self, *, delta: str, chunk_index: int) -> AgentEvent:
        occurred_at = self._clock()
        event_id, cursor = self._ids("response-delta", str(chunk_index))
        return await self._emit(
            native_events.build_response_delta_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                delta=delta,
            )
        )

    async def response_completed(
        self,
        *,
        text: str,
        revision: str = "final",
    ) -> AgentEvent:
        occurred_at = self._clock()
        # revision 区分同 Turn 多次 completed（流式终态 / 空转修复），避免 event_id 冲突吞更新。
        safe_revision = (revision or "final").strip() or "final"
        event_id, cursor = self._ids("response-completed", safe_revision)
        return await self._emit(
            native_events.build_response_completed_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                text=text,
            )
        )

    async def reasoning_summary_delta(
        self,
        *,
        delta: str,
        chunk_index: int,
    ) -> AgentEvent:
        occurred_at = self._clock()
        event_id, cursor = self._ids("reasoning-delta", str(chunk_index))
        return await self._emit(
            native_events.build_reasoning_summary_delta_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                delta=delta,
            )
        )

    async def reasoning_summary_completed(
        self,
        *,
        summary: str,
        duration_ms: int | None = None,
    ) -> AgentEvent:
        occurred_at = self._clock()
        event_id, cursor = self._ids("reasoning-completed", "final")
        return await self._emit(
            native_events.build_reasoning_summary_completed_event(
                event_id=event_id,
                cursor=cursor,
                sequence=await self._next_sequence(),
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                occurred_at=occurred_at,
                summary=summary,
                duration_ms=duration_ms,
            )
        )
