"""Gateway 侧的 OperationBatch 完成恢复：只从权威投影重建新的 Harness Run。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pixelflow.agent_control_plane.run_bridge import AgentRunBridge
from pixelflow.operations.jobs.batch_repository import OperationBatchOutboxRecord, SQLOperationBatchRepository
from pixelflow.operations.jobs.batch_resume import OperationBatchResumeDispatcher, OperationBatchResumePort
from pixelflow.video.workspace import build_workspace_digest

from .context_builder import PixelFlowContextBuilder
from .contracts import HarnessRunRequest
from .limits import LimitProfileResolver

logger = logging.getLogger(__name__)


class GatewayOperationBatchResumePort(OperationBatchResumePort):
    """类似 Application Service：用批次完成事件重建受限 Run，而非续跑旧 Session。"""

    def __init__(self, *, task_store: object, video_repository: object, bridge: AgentRunBridge) -> None:
        self._task_store = task_store
        self._video_repository = video_repository
        self._bridge = bridge

    async def create_operation_resume(self, event: OperationBatchOutboxRecord) -> str:
        """冻结 operation_resume_v1；completion_event_id 是唯一 trigger 与重试身份。"""

        conversation = await self._task_store.get_conversation(event.conversation_id, user_id=event.user_id)
        workspace = await self._video_repository.get_workspace(
            event.user_id,
            event.workspace_id,
        )
        if conversation is None or workspace is None or workspace.conversation_id != event.conversation_id:
            raise LookupError("OperationBatch 恢复缺少权威会话或工作区")
        projection = (
            PixelFlowContextBuilder()
            .build(
                {
                    "workspace_projection": build_workspace_digest(workspace),
                    "conversation_projection": {"title": conversation.title[:256], "revision": conversation.revision},
                    "preference_projection": {},
                    "brand_profile_projection": {},
                    "long_term_memory_projection": [],
                },
            )
            .projection
        )
        limits = LimitProfileResolver().resolve("operation_resume")
        request = HarnessRunRequest(
            user_id=event.user_id,
            conversation_id=event.conversation_id,
            workspace_id=workspace.workspace_id,
            workspace_revision=workspace.revision,
            trigger_id=event.completion_event_id,
            trigger_type="operation_resume",
            user_input="批次外部任务已全部结束，请基于当前工作区继续处理结果。",
            system_instruction="这是一次 M06 批次完成恢复。只能依据当前权威工作区决定后续动作；不得假设旧 Harness Session 仍可用。",
            context_digest=_digest({"completion_event_id": event.completion_event_id, "workspace_revision": workspace.revision, "context": projection}),
            model_profile_digest=_digest({"profile": "deepseek-v4-pro"}),
            context_budget_digest=_digest({"effective_context_k": 896, "output_reserve_k": 32, "safety_reserve_k": 32}),
            run_limits_digest=limits.digest,
            limit_profile=limits.profile,
            max_model_steps=limits.max_model_steps,
            max_business_tools=limits.max_business_tools,
            max_billable_batch_starts=limits.max_billable_batch_starts,
            deadline_seconds=limits.deadline_seconds,
            max_output_tokens=192,
            **projection,
        )
        return (await self._bridge.start(request)).run_id


class OperationBatchResumeWorker:
    """Gateway 生命周期托管的批次完成 Outbox Worker。"""

    def __init__(self, *, repository: SQLOperationBatchRepository, resume_port: OperationBatchResumePort, worker_id: str, clock: Callable[[], datetime] | None = None) -> None:
        self._dispatcher = OperationBatchResumeDispatcher(repository=repository, resume_port=resume_port, worker_id=worker_id)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="pixelflow-operation-batch-resume")

    async def aclose(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while not self._closed:
            try:
                delivered = await self._dispatcher.deliver_next(now=self._clock())
                if delivered is None:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("operation_batch_resume_delivery_failed")
                await asyncio.sleep(1)


def _digest(value: dict[str, object]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["GatewayOperationBatchResumePort", "OperationBatchResumeWorker"]
