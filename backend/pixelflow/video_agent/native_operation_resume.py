"""原生 Agent 的 Operation 终态内部 resume Turn。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pixelflow.video_agent.contracts import (
    AgentPlanStatus,
    PlanStepStatus,
    VideoToolResult,
)
from pixelflow.video_agent.native_invoke import (
    NativeVideoAgentInvokeRequest,
    NativeVideoAgentInvoker,
)
from pixelflow.video_agent.workspace import VideoAgentRepository

logger = logging.getLogger(__name__)


class NativeOperationResumeHandler:
    """Operation 成功后：收口 RUNNING 步骤并内部 invoke 原生 Agent（幂等）。"""

    def __init__(
        self,
        *,
        repository: VideoAgentRepository,
        native_invoker: NativeVideoAgentInvoker,
    ) -> None:
        self._repository = repository
        self._native_invoker = native_invoker
        self._seen_event_ids: set[str] = set()

    async def __call__(
        self,
        *,
        user_id: str,
        conversation_id: str,
        plan_id: str,
        job_id: str,
        status: str,
        completion_event_id: str,
    ) -> None:
        if completion_event_id in self._seen_event_ids:
            return
        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None or plan.conversation_id != conversation_id:
            return
        workspace = await self._repository.get_workspace(user_id, plan.workspace_id)
        if workspace is None:
            return

        # 幂等标记写入 workspace，跨进程重复完成事件只唤醒一次有效判断。
        claimed = workspace.payload.get("native_operation_resume_claimed")
        if isinstance(claimed, dict) and claimed.get("event_id") == completion_event_id:
            self._seen_event_ids.add(completion_event_id)
            return

        now = datetime.now(UTC)
        running = next(
            (step for step in plan.steps if step.status is PlanStepStatus.RUNNING),
            None,
        )
        if running is not None:
            await self._repository.complete_step_with_event(
                user_id,
                plan_id,
                running.step_id,
                VideoToolResult(
                    tool_name=running.tool_name,
                    public_summary=f"异步任务已完成（{status}）",
                    pending_operation_job_ids=(job_id,),
                ),
                run_id=completion_event_id,
                now=now,
            )

        try:
            current = await self._repository.get_workspace(user_id, plan.workspace_id)
            if current is not None:
                await self._repository.apply_workspace_patch(
                    user_id,
                    current.workspace_id,
                    {
                        "native_operation_resume_claimed": {
                            "event_id": completion_event_id,
                            "job_id": job_id,
                            "status": status,
                        }
                    },
                    expected_revision=current.revision,
                    now=now,
                )
        except Exception:  # noqa: BLE001
            logger.exception("claim native operation resume failed")

        turn_id = str(
            uuid5(
                NAMESPACE_URL,
                f"pixelflow-native-op-resume:{conversation_id}:{completion_event_id}",
            )
        )
        refreshed = await self._repository.get_workspace(user_id, plan.workspace_id)
        if refreshed is None:
            return
        await self._native_invoker.invoke(
            NativeVideoAgentInvokeRequest(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                plan_id=plan_id,
                content=(
                    f"[内部恢复] 异步任务 {job_id} 已{status}。"
                    "请读取最新工作区结果，决定下一步最小动作；不要重复启动同一任务。"
                ),
                workspace=refreshed,
                credential=None,
            )
        )
        current_plan = await self._repository.get_plan(user_id, plan_id)
        if current_plan is not None and current_plan.status is AgentPlanStatus.RUNNING:
            await self._repository.update_plan_status(
                user_id,
                plan_id,
                AgentPlanStatus.COMPLETED,
                now=datetime.now(UTC),
            )
        self._seen_event_ids.add(completion_event_id)


__all__ = ["NativeOperationResumeHandler"]
