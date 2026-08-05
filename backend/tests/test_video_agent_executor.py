from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict

from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.executor.service import VideoAgentExecutor
from pixelflow.video_agent.tools import (
    InspectVideoWorkspaceTool,
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
)
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository

T0 = datetime(2026, 8, 5, tzinfo=UTC)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BillableTool:
    spec = VideoToolSpec(
        name="generate_scenes",
        description="测试计费生成",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scenes.variants",),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: VideoToolContext, arguments):
        self.calls += 1
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="分镜生成完成",
        )


async def make_executor(tool, *, confirmation_required: bool):
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={"artifact_refs": ["artifact:workspace-1"]},
            created_at=T0,
            updated_at=T0,
        ),
    )
    step = AgentPlanStep(
        step_id="step-1",
        plan_id="plan-1",
        sequence=1,
        tool_name=tool.spec.name,
        title="执行步骤",
        status=PlanStepStatus.PENDING,
        arguments={},
        confirmation_required=confirmation_required,
    )
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id=workspace.workspace_id,
            conversation_id=workspace.conversation_id,
            status=AgentPlanStatus.PLANNING,
            public_goal="执行测试计划",
            steps=(step,),
            created_at=T0,
            updated_at=T0,
        ),
        [step],
    )
    ticks = iter(T0 + timedelta(seconds=value) for value in range(1, 20))
    executor = VideoAgentExecutor(
        repository=repository,
        registry=VideoToolRegistry([tool]),
        clock=lambda: next(ticks),
    )
    return executor, repository, event_repository


@pytest.mark.asyncio
async def test_executor_runs_safe_plan_and_persists_completion() -> None:
    executor, repository, event_repository = await make_executor(
        InspectVideoWorkspaceTool(),
        confirmation_required=False,
    )

    completed = await executor.run_plan("user-1", "plan-1")

    assert completed.status is AgentPlanStatus.COMPLETED
    assert completed.steps[0].status is PlanStepStatus.COMPLETED
    assert [event.type.value for event in await event_repository.list_events("user-1", "conversation-1")] == [
        "agent.step.started",
        "agent.step.completed",
    ]
    assert (await repository.get_plan("user-1", "plan-1")) == completed


@pytest.mark.asyncio
async def test_executor_stops_before_billable_tool_until_confirmation() -> None:
    tool = BillableTool()
    executor, _, _ = await make_executor(tool, confirmation_required=True)

    waiting = await executor.run_plan("user-1", "plan-1")
    assert waiting.status is AgentPlanStatus.AWAITING_CONFIRMATION
    assert waiting.steps[0].status is PlanStepStatus.AWAITING_CONFIRMATION
    assert tool.calls == 0

    completed = await executor.confirm_step("user-1", "plan-1", "step-1")
    assert completed.status is AgentPlanStatus.COMPLETED
    assert completed.steps[0].status is PlanStepStatus.COMPLETED
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_executor_resumes_persisted_running_step_without_restarting_it() -> None:
    executor, repository, event_repository = await make_executor(
        InspectVideoWorkspaceTool(),
        confirmation_required=False,
    )
    await repository.update_plan_status(
        "user-1", "plan-1", AgentPlanStatus.RUNNING, now=T0
    )
    await repository.start_step_with_event(
        "user-1", "plan-1", "step-1", run_id="plan-1", now=T0
    )

    completed = await executor.resume_plan("user-1", "plan-1")

    assert completed.status is AgentPlanStatus.COMPLETED
    assert len(await event_repository.list_events("user-1", "conversation-1")) == 2
