"""将 Sidecar 安全事件投影到 PixelFlow 权威 Outbox 与消息 Repository。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol

from pixelflow.agent_control_plane.contracts import AgentEvent, AgentEventType
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRecordConflictError
from pixelflow.agent_control_plane.public_contracts import (
    AgentSnapshotV1,
    PublicAgentEventV1,
    PublicMessageV1,
    VideoWorkspaceProjectionV1,
)
from pixelflow.agent_tools.repository import RunBinding, SQLAgentToolRepository
from pixelflow.tasks import PixelFlowConversationMessageRecord, PixelFlowTaskStore

from .contracts import HarnessRunEvent
from .port import AgentHarnessPort
from .sidecar import GatewayHarnessSidecarError

_SNAPSHOT_EVENT_LIMIT = 256
_PUBLIC_RUN_STATUSES = {
    "accepted",
    "running",
    "suspended_operation",
    "suspended_confirmation",
    "suspended_authorization",
    "completed",
    "failed",
    "cancelled",
}


def _bounded_snapshot_events(events: list[AgentEvent]) -> list[AgentEvent]:
    """保留 Snapshot 尾部事件，完整历史仍由 Outbox 与 SSE 游标提供。"""

    return events[-_SNAPSHOT_EVENT_LIMIT:]


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
        video_repository: object | None = None,
    ) -> None:
        self._bindings = binding_repository
        self._events = event_repository
        self._task_store = task_store
        self._video_repository = video_repository
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
        """从同一 Outbox 与消息表生成可刷新恢复的有界 Harness Snapshot。"""

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
            PublicMessageV1(
                message_id=message.message_id,
                role=message.role,
                content=message.content,
            )
            for message in messages
            if message.payload.get("harness_run_id") == run_id
        ]
        status = "accepted"
        if events:
            state = events[-1].payload.get("status")
            if isinstance(state, str) and state in _PUBLIC_RUN_STATUSES:
                status = state
        workspace = await self._workspace_projection(binding)
        last_event = events[-1] if events else None
        return AgentSnapshotV1(
            run_id=run_id,
            conversation_id=conversation_id,
            status=status,
            last_sequence=0 if last_event is None else last_event.sequence,
            last_cursor="" if last_event is None else last_event.cursor,
            context_version=0,
            # 历史事件仍完整保留在 Gateway Outbox；首屏只回放尾部，避免重复 Tool
            # 进度把 Snapshot 撑大。last_sequence/last_cursor 仍指向完整 Outbox 尾部，
            # 后续 SSE 从该位置继续，不会重复拉取历史。
            events=[self._to_public_event(event) for event in _bounded_snapshot_events(events)],
            messages=response_messages,
            workspace=workspace,
        )

    async def _workspace_projection(self, binding: RunBinding) -> VideoWorkspaceProjectionV1 | None:
        """从权威 Workspace Repository 生成只读摘要；异常或归属不一致时不泄漏业务内容。"""

        repository = self._video_repository
        if repository is None or not hasattr(repository, "get_workspace"):
            return None
        workspace = await repository.get_workspace(binding.user_id, binding.workspace_id)
        if workspace is None or workspace.conversation_id != binding.conversation_id:
            return None
        from pixelflow.video.workspace import build_plan_digest, build_workspace_digest

        summary = build_workspace_digest(workspace)
        if hasattr(repository, "list_conversation_plans"):
            plans = await repository.list_conversation_plans(
                binding.user_id,
                binding.conversation_id,
            )
            summary["active_plan"] = build_plan_digest(plans[-1] if plans else None)

        return VideoWorkspaceProjectionV1(
            workspace_id=workspace.workspace_id,
            revision=workspace.revision,
            summary=summary,
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
                if event.type is AgentEventType.RUN_STATE_CHANGED and event.payload.get("status") in {
                    "completed",
                    "failed",
                    "cancelled",
                    "suspended_operation",
                    "suspended_confirmation",
                    "suspended_authorization",
                }:
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

        return PublicAgentEventV1(
            event_id=event.event_id,
            sequence=event.sequence,
            run_id=event.run_id,
            type=event.type,
            occurred_at=event.occurred_at.isoformat().replace("+00:00", "Z"),
            payload=dict(event.payload),
            conversation_id=event.conversation_id,
            cursor=event.cursor,
        )

    @staticmethod
    def _public_event(source_event: HarnessRunEvent) -> tuple[AgentEventType, dict[str, Any]]:
        """把有限 Sidecar 事件映射到既有公开枚举，拒绝 reasoning、参数和未知类型。"""

        if source_event.type in {"run.accepted", "run.started", "run.completed", "run.failed", "run.cancelled", "run.suspended"}:
            status_by_type = {
                "run.accepted": "accepted",
                "run.started": "running",
                "run.completed": "completed",
                "run.failed": "failed",
                "run.cancelled": "cancelled",
                "run.suspended": source_event.payload.get("status"),
            }
            payload: dict[str, Any] = {"status": status_by_type[source_event.type]}
            if source_event.type == "run.suspended" and payload["status"] not in {
                "suspended_operation",
                "suspended_confirmation",
                "suspended_authorization",
            }:
                raise HarnessEventProjectionError("Sidecar 挂起状态不符合公开合同")
            if payload["status"] in {"suspended_confirmation", "suspended_authorization"}:
                interrupt_id = source_event.payload.get("interrupt_id")
                if (
                    not isinstance(interrupt_id, str)
                    or not interrupt_id.strip()
                    or len(interrupt_id) > 128
                ):
                    raise HarnessEventProjectionError("Sidecar 人工中断身份不符合公开合同")
                payload["interrupt_id"] = interrupt_id
            if source_event.type == "run.failed":
                code = source_event.payload.get("code")
                if isinstance(code, str) and code in {
                    "engine_execution_failed",
                    "engine_finish_reason_unexpected",
                    "harness_run_recovery_required",
                    "deadline_exceeded",
                    "max_model_steps",
                    "max_business_tools",
                    "max_output_tokens",
                }:
                    payload["code"] = code
            return AgentEventType.RUN_STATE_CHANGED, payload
        if source_event.type == "tool.completed":
            tool_name = source_event.payload.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 128:
                raise HarnessEventProjectionError("Sidecar Tool 事件不符合公开合同")
            return AgentEventType.AGENT_TOOL_COMPLETED, {"tool_name": tool_name}
        if source_event.type == "public_summary.delta":
            delta = source_event.payload.get("delta")
            if not isinstance(delta, str) or not delta.strip() or len(delta) > 512:
                raise HarnessEventProjectionError("Sidecar 公开摘要增量不符合合同")
            return AgentEventType.AGENT_THINKING_DELTA, {"delta": delta.strip()}
        if source_event.type == "public_summary.completed":
            summary = source_event.payload.get("summary")
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 1_536:
                raise HarnessEventProjectionError("Sidecar 公开摘要终态不符合合同")
            return AgentEventType.AGENT_THINKING_COMPLETED, {"summary": summary.strip()}
        if source_event.type == "response.delta":
            delta = source_event.payload.get("delta")
            if not isinstance(delta, str) or not delta or len(delta) > 512:
                raise HarnessEventProjectionError("Sidecar 回复增量不符合公开合同")
            return AgentEventType.AGENT_RESPONSE_DELTA, {"delta": delta}
        if source_event.type == "response.completed":
            response = source_event.payload.get("response")
            if not isinstance(response, str) or not response.strip() or len(response) > 8_000:
                raise HarnessEventProjectionError("Sidecar 回复事件不符合公开合同")
            return AgentEventType.AGENT_RESPONSE_COMPLETED, {"response": response.strip()}
        raise HarnessEventProjectionError("Sidecar 事件类型不在公开白名单中")


__all__ = ["HarnessEventProjectionError", "HarnessRunProjector"]
