"""将 Sidecar 安全事件投影到 PixelFlow 权威 Outbox 与消息 Repository。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol

from pixelflow.agent_control_plane.contracts import AgentEvent, AgentEventType
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRecordConflictError
from pixelflow.agent_control_plane.public_contracts import AgentSnapshotV1, PublicAgentEventV1
from pixelflow.agent_tools.repository import RunBinding, SQLAgentToolRepository
from pixelflow.tasks import PixelFlowConversationMessageRecord, PixelFlowTaskStore

from .contracts import HarnessRunEvent
from .port import AgentHarnessPort
from .sidecar import GatewayHarnessSidecarError


class _EventRepository(Protocol):
    """描述 Harness 投影复用既有 Agent Event Outbox 所需的最小能力。"""

    async def get_event(self, user_id: str, event_id: str) -> AgentEvent | None: ...

    async def list_events(self, user_id: str, conversation_id: str) -> list[AgentEvent]: ...

    async def create_event(self, user_id: str, record: AgentEvent) -> AgentEvent: ...


class HarnessEventProjectionError(RuntimeError):
    """表示 Sidecar 事件无法安全投影，调用方只能返回固定错误。"""


class HarnessRunProjector:
    """类似 Application Service：消费 Sidecar 事件并更新 Gateway 的权威公开投影。"""

    def __init__(
        self,
        *,
        binding_repository: SQLAgentToolRepository,
        event_repository: _EventRepository,
        task_store: PixelFlowTaskStore,
    ) -> None:
        self._bindings = binding_repository
        self._events = event_repository
        self._task_store = task_store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self, *, harness: AgentHarnessPort, binding: RunBinding) -> None:
        """为同一 Run 至多启动一个投影任务；重复 Turn 请求只回读既有任务。"""

        async with self._lock:
            task = self._tasks.get(binding.run_id)
            if task is not None and not task.done():
                return
            self._tasks[binding.run_id] = asyncio.create_task(
                self._project_until_terminal(harness=harness, binding=binding),
                name=f"pixelflow-harness-projector:{binding.run_id}",
            )

    async def aclose(self) -> None:
        """关闭时只取消本进程投影协程，Sidecar 与已写 Outbox 不伪造终态。"""

        async with self._lock:
            tasks = tuple(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def snapshot(self, *, user_id: str, conversation_id: str, run_id: str) -> AgentSnapshotV1:
        """从同一 Outbox 与消息表生成可刷新恢复的最小 Harness Snapshot。"""

        binding = await self._require_binding(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        events = [
            event
            for event in await self._events.list_events(user_id, conversation_id)
            if event.run_id == binding.run_id
        ]
        messages = await self._task_store.list_conversation_messages(
            conversation_id,
            user_id=user_id,
        )
        response_messages = [
            {
                "message_id": message.message_id,
                "role": message.role,
                "content": message.content,
            }
            for message in messages
            if message.payload.get("harness_run_id") == run_id
        ]
        status = "accepted"
        if events:
            state = events[-1].payload.get("status")
            if isinstance(state, str) and state in {"accepted", "running", "completed", "failed"}:
                status = state
        return AgentSnapshotV1(
            run_id=run_id,
            status=status,
            last_sequence=0 if not events else events[-1].sequence,
            events=[self._to_public_event(event) for event in events],
            messages=response_messages,
        )

    async def events_after(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        after_sequence: int,
    ) -> list[AgentEvent]:
        """按 Gateway Outbox sequence 回放一个已绑定 Run 的公开事件。"""

        binding = await self._require_binding(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        return [
            event
            for event in await self._events.list_events(user_id, conversation_id)
            if event.run_id == binding.run_id and event.sequence > after_sequence
        ]

    async def _project_until_terminal(
        self,
        *,
        harness: AgentHarnessPort,
        binding: RunBinding,
    ) -> None:
        """消费一次 Sidecar 事件流；任何不安全输入都不写入公开 Outbox。"""

        try:
            async for source_event in harness.stream_sidecar_events(
                user_id=binding.user_id,
                conversation_id=binding.conversation_id,
                run_id=binding.run_id,
                after_sequence=0,
            ):
                event = await self._append_source_event(binding, source_event)
                if source_event.type == "response.completed":
                    await self._append_response_message(binding, source_event)
                if event.type is AgentEventType.RUN_STATE_CHANGED and event.payload.get("status") in {"completed", "failed"}:
                    return
        except GatewayHarnessSidecarError as error:
            raise HarnessEventProjectionError("Sidecar 事件投影失败") from error
        finally:
            async with self._lock:
                self._tasks.pop(binding.run_id, None)

    async def _append_source_event(
        self,
        binding: RunBinding,
        source_event: HarnessRunEvent,
    ) -> AgentEvent:
        """按 Sidecar event_id 幂等写入已有 Outbox，避免重连重复发布。"""

        existing = await self._events.get_event(binding.user_id, source_event.event_id)
        if existing is not None:
            return existing
        event_type, payload = self._public_event(source_event)
        occurred_at = self._parse_occurred_at(source_event.occurred_at)
        for _ in range(8):
            existing = await self._events.get_event(binding.user_id, source_event.event_id)
            if existing is not None:
                return existing
            prior = await self._events.list_events(binding.user_id, binding.conversation_id)
            record = AgentEvent(
                event_id=source_event.event_id,
                sequence=1 if not prior else prior[-1].sequence + 1,
                cursor=source_event.event_id,
                conversation_id=binding.conversation_id,
                run_id=binding.run_id,
                occurred_at=occurred_at,
                type=event_type,
                payload=payload,
            )
            try:
                return await self._events.create_event(binding.user_id, record)
            except AgentRuntimeRecordConflictError:
                continue
        raise HarnessEventProjectionError("Gateway Event Outbox 写入冲突超过安全上限")

    async def _append_response_message(
        self,
        binding: RunBinding,
        source_event: HarnessRunEvent,
    ) -> None:
        """把已过滤最终回复写为权威助手消息，重复重放使用稳定主键。"""

        response = source_event.payload.get("response")
        if not isinstance(response, str) or not response.strip() or len(response) > 8_000:
            raise HarnessEventProjectionError("Sidecar 最终回复不符合公开消息合同")
        await self._task_store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id=f"harness_response_{binding.run_id[5:]}",
                conversation_id=binding.conversation_id,
                user_id=binding.user_id,
                role="assistant",
                content=response.strip(),
                payload={"harness_run_id": binding.run_id},
            ),
        )

    async def _require_binding(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
    ) -> RunBinding:
        binding = await self._bindings.get_run_binding(run_id)
        if binding is None or binding.user_id != user_id or binding.conversation_id != conversation_id:
            raise LookupError("Harness Run 不存在或不属于当前用户/会话")
        return binding

    @staticmethod
    def _parse_occurred_at(value: str) -> datetime:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise HarnessEventProjectionError("Sidecar 事件时间格式非法") from error

    @staticmethod
    def _to_public_event(event: AgentEvent) -> PublicAgentEventV1:
        """把内部 Outbox 事件映射为冻结的 Harness 浏览器合同。"""

        mapping = {
            AgentEventType.RUN_STATE_CHANGED: "run.state_changed",
            AgentEventType.AGENT_TOOL_COMPLETED: "tool.completed",
            AgentEventType.AGENT_RESPONSE_COMPLETED: "response.completed",
        }
        event_type = mapping.get(event.type)
        if event_type is None:
            raise HarnessEventProjectionError("内部事件不在 Harness 公开白名单")
        return PublicAgentEventV1(
            event_id=event.event_id,
            sequence=event.sequence,
            run_id=event.run_id,
            type=event_type,
            occurred_at=event.occurred_at.isoformat().replace("+00:00", "Z"),
            payload=dict(event.payload),
        )

    @staticmethod
    def _public_event(source_event: HarnessRunEvent) -> tuple[AgentEventType, dict[str, Any]]:
        """把有限 Sidecar 事件映射到既有公开枚举，拒绝 reasoning、参数和未知类型。"""

        if source_event.type in {"run.accepted", "run.started", "run.completed", "run.failed"}:
            status_by_type = {
                "run.accepted": "accepted",
                "run.started": "running",
                "run.completed": "completed",
                "run.failed": "failed",
            }
            payload: dict[str, Any] = {"status": status_by_type[source_event.type]}
            if source_event.type == "run.failed":
                code = source_event.payload.get("code")
                if isinstance(code, str) and code in {
                    "engine_execution_failed",
                    "engine_finish_reason_unexpected",
                    "harness_run_recovery_required",
                }:
                    payload["code"] = code
            return AgentEventType.RUN_STATE_CHANGED, payload
        if source_event.type == "tool.completed":
            tool_name = source_event.payload.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 128:
                raise HarnessEventProjectionError("Sidecar Tool 事件不符合公开合同")
            return AgentEventType.AGENT_TOOL_COMPLETED, {"tool_name": tool_name}
        if source_event.type == "response.completed":
            response = source_event.payload.get("response")
            if not isinstance(response, str) or not response.strip() or len(response) > 8_000:
                raise HarnessEventProjectionError("Sidecar 回复事件不符合公开合同")
            return AgentEventType.AGENT_RESPONSE_COMPLETED, {"response": response.strip()}
        raise HarnessEventProjectionError("Sidecar 事件类型不在公开白名单中")


__all__ = ["HarnessEventProjectionError", "HarnessRunProjector"]
