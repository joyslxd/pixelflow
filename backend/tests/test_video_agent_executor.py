from __future__ import annotations

import asyncio
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


class WorkspacePatchTool:
    spec = VideoToolSpec(
        name="import_script",
        description="测试工作区补丁",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script",),
    )

    async def execute(self, context: VideoToolContext, arguments):
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="脚本已导入",
            workspace_patch={
                "script": {
                    "source": "user_import",
                    "version": 1,
                    "content": "展示商品",
                }
            },
        )


class PendingOperationTool:
    spec = VideoToolSpec(
        name="analyze_reference_video",
        description="测试可恢复外部任务",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("reference_videos",),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: VideoToolContext, arguments):
        del arguments
        assert context.plan_id == "plan-1"
        assert context.step_id == "step-1"
        self.calls += 1
        if self.calls == 1:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="参考视频解析任务已启动",
                workspace_patch={
                    "reference_videos": [
                        {"job_id": "operation-reference-1", "status": "polling"}
                    ]
                },
                pending_operation_job_ids=("operation-reference-1",),
            )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="参考视频解析完成",
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
async def test_confirm_step_returns_before_followup_steps() -> None:
    """确认 HTTP 只完成确认步；后续步留给 resume，避免被长 LLM 拖死。"""

    confirm_tool = BillableTool()
    followup = InspectVideoWorkspaceTool()
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
    steps = [
        AgentPlanStep(
            step_id="step-confirm",
            plan_id="plan-1",
            sequence=1,
            tool_name=confirm_tool.spec.name,
            title="确认创意",
            status=PlanStepStatus.PENDING,
            arguments={},
            confirmation_required=True,
        ),
        AgentPlanStep(
            step_id="step-followup",
            plan_id="plan-1",
            sequence=2,
            tool_name=followup.spec.name,
            title="后续阶段",
            status=PlanStepStatus.PENDING,
            arguments={},
            confirmation_required=False,
        ),
    ]
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id=workspace.workspace_id,
            conversation_id=workspace.conversation_id,
            status=AgentPlanStatus.PLANNING,
            public_goal="确认后继续",
            steps=tuple(steps),
            created_at=T0,
            updated_at=T0,
        ),
        steps,
    )
    ticks = iter(T0 + timedelta(seconds=value) for value in range(1, 40))
    executor = VideoAgentExecutor(
        repository=repository,
        registry=VideoToolRegistry([confirm_tool, followup]),
        clock=lambda: next(ticks),
    )

    waiting = await executor.run_plan("user-1", "plan-1")
    assert waiting.status is AgentPlanStatus.AWAITING_CONFIRMATION

    after_confirm = await executor.confirm_step("user-1", "plan-1", "step-confirm")
    assert after_confirm.status is AgentPlanStatus.RUNNING
    assert after_confirm.steps[0].status is PlanStepStatus.COMPLETED
    assert after_confirm.steps[1].status is PlanStepStatus.PENDING
    assert confirm_tool.calls == 1

    completed = await executor.resume_plan("user-1", "plan-1")
    assert completed.status is AgentPlanStatus.COMPLETED
    assert completed.steps[1].status is PlanStepStatus.COMPLETED


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


@pytest.mark.asyncio
async def test_stale_running_resume_resets_started_at_for_duration() -> None:
    """陈旧 RUNNING 重跑后耗时不应包含僵尸等待。"""

    from pixelflow.video_agent.tools.script_skill_pipeline import (
        SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS,
    )

    executor, repository, _ = await make_executor(
        InspectVideoWorkspaceTool(),
        confirmation_required=False,
    )
    old_start = T0
    await repository.update_plan_status(
        "user-1", "plan-1", AgentPlanStatus.RUNNING, now=old_start
    )
    await repository.start_step_with_event(
        "user-1", "plan-1", "step-1", run_id="plan-1", now=old_start
    )

    resume_at = old_start + timedelta(
        seconds=SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS + 60
    )
    ticks = [resume_at + timedelta(seconds=i) for i in range(12)]
    clock_values = iter(ticks)
    executor._clock = lambda: next(clock_values, ticks[-1])

    completed = await executor.resume_plan("user-1", "plan-1")

    assert completed.status is AgentPlanStatus.COMPLETED
    step = completed.steps[0]
    assert step.started_at is not None and step.started_at >= resume_at
    assert step.started_at != old_start
    assert step.duration_ms is not None
    assert step.duration_ms < 60_000


@pytest.mark.asyncio
async def test_executor_persists_declared_workspace_patch_before_completion() -> None:
    executor, repository, _ = await make_executor(
        WorkspacePatchTool(),
        confirmation_required=False,
    )

    completed = await executor.run_plan("user-1", "plan-1")
    workspace = await repository.get_workspace("user-1", "workspace-1")

    assert completed.status is AgentPlanStatus.COMPLETED
    assert workspace is not None
    assert workspace.revision == 2
    assert workspace.payload["script"]["source"] == "user_import"


@pytest.mark.asyncio
async def test_executor_keeps_step_running_until_operation_result_is_replayed() -> None:
    tool = PendingOperationTool()
    executor, repository, event_repository = await make_executor(
        tool,
        confirmation_required=False,
    )

    running = await executor.run_plan("user-1", "plan-1")
    workspace = await repository.get_workspace("user-1", "workspace-1")

    assert running.status is AgentPlanStatus.RUNNING
    assert running.steps[0].status is PlanStepStatus.RUNNING
    assert workspace is not None
    assert workspace.payload["reference_videos"][0]["status"] == "polling"
    assert [
        event.type.value
        for event in await event_repository.list_events("user-1", "conversation-1")
    ] == ["agent.step.started"]

    completed = await executor.resume_plan("user-1", "plan-1")

    assert completed.status is AgentPlanStatus.COMPLETED
    assert completed.steps[0].status is PlanStepStatus.COMPLETED
    assert tool.calls == 2


class SlowThenSucceedTool:
    spec = VideoToolSpec(
        name="run_script_skill_stage",
        description="模拟可被确认成片取消的脚本阶段",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script_pipeline",),
    )

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def execute(self, context: VideoToolContext, arguments):  # noqa: ANN001, ARG002
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="阶段完成",
        )


@pytest.mark.asyncio
async def test_executor_returns_cancelled_plan_without_completing_superseded_step() -> None:
    tool = SlowThenSucceedTool()
    executor, repository, _ = await make_executor(tool, confirmation_required=False)
    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    # 把工具名改成脚本 Skill，便于 cancel_active_script_skill_plans 识别。
    steps = await repository.list_plan_steps("user-1", "plan-1")
    skill_step = steps[0].model_copy(
        update={
            "tool_name": "run_script_skill_stage",
            "title": "合规检查 /compliance",
            "arguments": {"stage": "compliance"},
        }
    )
    await repository.save_plan(
        "user-1",
        plan.model_copy(
            update={
                "status": AgentPlanStatus.PLANNING,
                "public_goal": "成稿自检与导出",
                "steps": (skill_step,),
            }
        ),
        [skill_step],
    )

    run_task = asyncio.create_task(executor.run_plan("user-1", "plan-1"))
    await asyncio.wait_for(tool.started.wait(), timeout=2)
    cancelled = await repository.cancel_active_script_skill_plans(
        "user-1",
        "conversation-1",
        now=T0 + timedelta(seconds=5),
    )
    tool.release.set()
    result = await asyncio.wait_for(run_task, timeout=2)

    assert len(cancelled) == 1
    assert result.status is AgentPlanStatus.CANCELLED
    assert result.steps[0].status is PlanStepStatus.SKIPPED
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_executor_retries_workspace_patch_after_revision_conflict() -> None:
    """长耗时 Tool 返回后若 revision 被并发 bump，应重读后仍写回补丁。"""

    executor, repository, _ = await make_executor(
        WorkspacePatchTool(),
        confirmation_required=False,
    )
    original_apply = repository.apply_workspace_patch
    calls = {"n": 0}

    async def flaky_apply(user_id, workspace_id, patch, *, expected_revision, now):
        calls["n"] += 1
        if calls["n"] == 1:
            # 模拟并发 Turn 已写入：先 bump revision，再让原 CAS 失败。
            await original_apply(
                user_id,
                workspace_id,
                {"product_info": {"name": "concurrent"}},
                expected_revision=expected_revision,
                now=now,
            )
            from pixelflow.agent_runtime.persistence.repositories import (
                AgentRuntimeRecordConflictError,
            )

            raise AgentRuntimeRecordConflictError("VideoAgent workspace revision 已变化")
        return await original_apply(
            user_id,
            workspace_id,
            patch,
            expected_revision=expected_revision,
            now=now,
        )

    repository.apply_workspace_patch = flaky_apply  # type: ignore[method-assign]

    completed = await executor.run_plan("user-1", "plan-1")
    workspace = await repository.get_workspace("user-1", "workspace-1")

    assert completed.status is AgentPlanStatus.COMPLETED
    assert workspace is not None
    assert workspace.payload["script"]["source"] == "user_import"
    assert workspace.payload["product_info"]["name"] == "concurrent"
    assert calls["n"] >= 2
