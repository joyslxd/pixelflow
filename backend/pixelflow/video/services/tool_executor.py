"""VideoAgent Tool 单次执行 Service（原生 Agent 循环的执行边界）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
)
from pixelflow.agent_tools.video import (
    VideoToolContext,
    VideoToolExecutionError,
    VideoToolRegistry,
)
from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.video.contracts import (
    AgentPlan,
    AgentPlanStatus,
    PlanStepStatus,
    VideoToolResult,
)

from .workspace_mutation import VideoWorkspaceMutationService

if TYPE_CHECKING:
    from pixelflow.video.workspace.repository import VideoWorkspaceRepository as VideoWorkspaceRepository


class VideoToolExecutor:
    """只执行单次 Tool Call；Plan 步进编排已硬删除，由原生 Agent 决定下一步。"""

    def __init__(
        self,
        *,
        repository: VideoWorkspaceRepository,
        registry: VideoToolRegistry,
        event_repository: AgentRuntimeRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._event_repository = event_repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._workspace_mutations = VideoWorkspaceMutationService(repository)

    async def execute_tool_call(
        self,
        *,
        context: VideoToolContext,
        tool_name: str,
        arguments: Mapping[str, object] | None = None,
    ) -> VideoToolResult:
        """执行单个 Tool Call，并在需要时写入 Workspace；不会自动继续下一步。"""

        if not isinstance(context, VideoToolContext):
            raise TypeError("context 必须是 VideoToolContext")
        name = (tool_name or "").strip()
        if not name:
            raise ValueError("tool_name 不能为空")

        result = await self._registry.execute(
            context,
            name,
            dict(arguments or {}),
        )
        if not isinstance(result, VideoToolResult):
            raise VideoToolExecutionError("工具结果无效，请稍后重试")
        if result.workspace_patch:
            await self._workspace_mutations.apply_tool_patch(
                user_id=context.user_id,
                workspace=context.workspace,
                patch=result.workspace_patch,
                now=self._clock(),
            )
        return result

    async def run_plan(
        self,
        user_id: str,
        plan_id: str,
        *,
        credential: TransientVideoAgentCredential | None = None,
    ) -> AgentPlan:
        del user_id, plan_id, credential
        raise RuntimeError(
            "VideoAgent Plan Runner（run_plan）已硬删除；请使用原生 Agent"
        )

    async def confirm_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        credential: TransientVideoAgentCredential | None = None,
    ) -> AgentPlan:
        del user_id, plan_id, step_id, credential
        raise RuntimeError(
            "VideoAgent Plan 步骤确认已硬删除；请使用原生 confirmation API"
        )

    async def cancel_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
    ) -> AgentPlan:
        """取消当前待确认计划（兼容旧确认卡取消入口）。"""

        plan = await self._required_plan(user_id, plan_id)
        if plan.status is AgentPlanStatus.CANCELLED:
            return plan
        waiting_steps = [
            step
            for step in plan.steps
            if step.status is PlanStepStatus.AWAITING_CONFIRMATION
        ]
        if (
            plan.status is not AgentPlanStatus.AWAITING_CONFIRMATION
            or len(waiting_steps) != 1
            or waiting_steps[0].step_id != step_id
        ):
            raise AgentRuntimeRecordConflictError("VideoAgent plan 当前确认步骤不匹配")
        return await self._repository.cancel_step_confirmation(
            user_id,
            plan_id,
            step_id,
            now=self._clock(),
        )

    async def resume_plan(
        self,
        user_id: str,
        plan_id: str,
        *,
        credential: TransientVideoAgentCredential | None = None,
    ) -> AgentPlan:
        return await self.run_plan(user_id, plan_id, credential=credential)

    async def maybe_resume_stale_running_plan(
        self,
        user_id: str,
        plan_id: str,
    ) -> AgentPlan | None:
        """旧 Plan Runner 已删除；原生 Agent 不按步骤超时重跑。"""

        del user_id, plan_id
        return None

    async def _required_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent plan 不存在或不属于当前用户"
            )
        return plan
