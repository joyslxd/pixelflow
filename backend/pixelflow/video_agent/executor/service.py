"""VideoAgent 计划执行、确认闸门与断点恢复 Service。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.video_agent.contracts import AgentPlan, AgentPlanStatus, PlanStepStatus
from pixelflow.video_agent.tools import VideoToolContext, VideoToolRegistry
from pixelflow.video_agent.workspace import VideoAgentRepository


class VideoAgentExecutor:
    def __init__(
        self,
        *,
        repository: VideoAgentRepository,
        registry: VideoToolRegistry,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        plan = await self._required_plan(user_id, plan_id)
        if plan.status in {
            AgentPlanStatus.COMPLETED,
            AgentPlanStatus.FAILED,
            AgentPlanStatus.CANCELLED,
            AgentPlanStatus.AWAITING_CONFIRMATION,
        }:
            return plan
        if len(plan.steps) > 8:
            raise AgentRuntimeRecordConflictError("VideoAgent 单个计划不能超过八步")
        if plan.status is AgentPlanStatus.PLANNING:
            plan = await self._repository.update_plan_status(
                user_id,
                plan_id,
                AgentPlanStatus.RUNNING,
                now=self._clock(),
            )
        return await self._continue(user_id, plan)

    async def confirm_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
    ) -> AgentPlan:
        plan = await self._required_plan(user_id, plan_id)
        if plan.status is not AgentPlanStatus.AWAITING_CONFIRMATION:
            raise AgentRuntimeRecordConflictError("VideoAgent plan 当前不等待确认")
        await self._repository.confirm_step(
            user_id,
            plan_id,
            step_id,
            now=self._clock(),
        )
        await self._repository.start_step_with_event(
            user_id,
            plan_id,
            step_id,
            run_id=plan_id,
            now=self._clock(),
        )
        plan = await self._repository.update_plan_status(
            user_id,
            plan_id,
            AgentPlanStatus.RUNNING,
            now=self._clock(),
        )
        return await self._continue(user_id, plan)

    async def resume_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        return await self.run_plan(user_id, plan_id)

    async def _continue(self, user_id: str, plan: AgentPlan) -> AgentPlan:
        workspace = await self._repository.get_workspace(user_id, plan.workspace_id)
        if workspace is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent workspace 不存在或不属于当前用户"
            )
        for step in plan.steps:
            if step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}:
                continue
            if step.status is PlanStepStatus.AWAITING_CONFIRMATION:
                return await self._repository.update_plan_status(
                    user_id,
                    plan.plan_id,
                    AgentPlanStatus.AWAITING_CONFIRMATION,
                    now=self._clock(),
                )
            if step.status is PlanStepStatus.PENDING and step.confirmation_required:
                await self._repository.request_step_confirmation(
                    user_id,
                    plan.plan_id,
                    step.step_id,
                )
                return await self._repository.update_plan_status(
                    user_id,
                    plan.plan_id,
                    AgentPlanStatus.AWAITING_CONFIRMATION,
                    now=self._clock(),
                )
            if step.status is PlanStepStatus.PENDING:
                step, _ = await self._repository.start_step_with_event(
                    user_id,
                    plan.plan_id,
                    step.step_id,
                    run_id=plan.plan_id,
                    now=self._clock(),
                )
            elif step.status is PlanStepStatus.RUNNING:
                step, _ = await self._repository.start_step_with_event(
                    user_id,
                    plan.plan_id,
                    step.step_id,
                    run_id=plan.plan_id,
                    now=step.started_at or self._clock(),
                )
            result = await self._registry.execute(
                VideoToolContext(user_id=user_id, workspace=workspace),
                step.tool_name,
                step.arguments,
            )
            if result.workspace_patch:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent 工作区补丁尚未接入权威 Repository"
                )
            await self._repository.complete_step_with_event(
                user_id,
                plan.plan_id,
                step.step_id,
                result,
                run_id=plan.plan_id,
                now=self._clock(),
            )
            plan = await self._required_plan(user_id, plan.plan_id)
        return await self._repository.update_plan_status(
            user_id,
            plan.plan_id,
            AgentPlanStatus.COMPLETED,
            now=self._clock(),
        )

    async def _required_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent plan 不存在或不属于当前用户"
            )
        return plan
