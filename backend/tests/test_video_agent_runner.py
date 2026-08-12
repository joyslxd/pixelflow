"""Task 11独立VideoAgent Runner生命周期测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.credentials import (
    TransientVideoAgentCredential,
    VideoAgentCredentialUnavailableError,
)
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint, video_agent_plan_id
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.planner.model import VideoAgentPlanningContext
from pixelflow.video_agent.runner import VideoAgentRunner, VideoAgentRunScope
from pixelflow.video_agent.tools import (
    ConfirmScriptCreativeTool,
    InspectVideoWorkspaceTool,
    RunScriptSkillStageTool,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class _StubPathAPlanner:
    async def plan_turn(self, context: VideoAgentPlanningContext) -> AgentPlan:
        plan_id = video_agent_plan_id(context.conversation_id, context.turn_id)
        now = datetime(2026, 8, 6, tzinfo=UTC)
        stage_specs = (
            ("start", "选题与创作目标 /start"),
            ("confirm", "确认选题创意"),
            ("plan", "三幕结构与爽点 /plan"),
            ("characters", "角色/场景/道具设定 /characters"),
            ("outline", "分镜大纲 /outline"),
            ("episode", "生成剧本正文 /episode"),
            ("review", "五维自检 /review"),
            ("compliance", "合规检查 /compliance"),
            ("export", "导出脚本产物 /export"),
        )
        steps: list[AgentPlanStep] = []
        for index, (stage, title) in enumerate(stage_specs, start=1):
            if stage == "confirm":
                steps.append(
                    AgentPlanStep(
                        step_id=f"{plan_id}-step-{index}",
                        plan_id=plan_id,
                        sequence=index,
                        tool_name="confirm_script_creative",
                        title=title,
                        status=PlanStepStatus.PENDING,
                        arguments={},
                        confirmation_required=True,
                    )
                )
                continue
            steps.append(
                AgentPlanStep(
                    step_id=f"{plan_id}-step-{index}",
                    plan_id=plan_id,
                    sequence=index,
                    tool_name="run_script_skill_stage",
                    title=title,
                    status=PlanStepStatus.PENDING,
                    arguments={"stage": stage, "creative_direction": ""},
                    confirmation_required=False,
                )
            )
        return AgentPlan(
            plan_id=plan_id,
            workspace_id=context.workspace.workspace_id,
            conversation_id=context.conversation_id,
            status=AgentPlanStatus.PLANNING,
            public_goal="处理视频创作请求",
            steps=tuple(steps),
            created_at=now,
            updated_at=now,
        )


@pytest.mark.asyncio
async def test_runner_executes_persisted_plan_and_discards_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pixelflow.video_agent.thinking_stream import IntakeThinkingResult

    async def fake_generate(*, stage, user_story, prior, on_token=None):  # noqa: ANN001, ARG001
        return f"# {stage}\n\n基于用户输入生成：{user_story[:40]}\n时长：15秒\n画幅：9:16\n结尾请下单购买\n"

    async def fake_thinking(**_kwargs):  # noqa: ANN001, ARG001
        return IntakeThinkingResult(
            user_message="开始脚本创作。",
            entry_path="create",
            intent="create",
            needs_user_reply=False,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script_skill_pipeline._generate_stage_markdown",
        fake_generate,
    )
    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.stream_intake_thinking",
        fake_thinking,
    )

    async def fake_missing_fields(_content: str, _user_text: str = ""):  # noqa: ANN001
        return []

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script_skill_pipeline.missing_creative_production_fields_async",
        fake_missing_fields,
    )
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    now = datetime(2026, 8, 6, tzinfo=UTC)
    submission = await VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=_StubPathAPlanner(),
        clock=lambda: now,
    ).submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=("artifact:product-1",),
    )
    executor = VideoAgentExecutor(
        repository=video_repository,
        registry=VideoToolRegistry(
            [RunScriptSkillStageTool(), ConfirmScriptCreativeTool()]
        ),
        clock=lambda: now,
    )
    runner = VideoAgentRunner(
        repository=video_repository,
        executor=executor,
    )
    credential = TransientVideoAgentCredential("Bearer transient-test")
    scope = VideoAgentRunScope(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        plan_id=submission.plan.plan_id,
    )

    await runner.notify_turn(scope, credential)

    paused = await video_repository.get_plan("user-1", submission.plan.plan_id)
    assert paused is not None
    assert paused.status.value == "awaiting_confirmation"
    assert paused.steps[0].status.value == "completed"
    assert paused.steps[1].tool_name == "confirm_script_creative"
    assert paused.steps[1].status.value == "awaiting_confirmation"

    workspace = await video_repository.get_workspace("user-1", paused.workspace_id)
    assert workspace is not None
    await video_repository.apply_workspace_patch(
        "user-1",
        paused.workspace_id,
        {"latest_input": "生成商品视频，时长15秒，画幅9:16，结尾引导下单购买"},
        expected_revision=workspace.revision,
        now=now,
    )

    await executor.confirm_step(
        "user-1",
        submission.plan.plan_id,
        paused.steps[1].step_id,
    )
    # 确认 HTTP 只跑完确认步；后续 Skill 需 resume（与线上后台续跑一致）。
    await executor.resume_plan("user-1", submission.plan.plan_id)

    restored = await video_repository.get_plan("user-1", submission.plan.plan_id)
    assert restored is not None
    assert restored.status.value == "completed"
    assert [step.tool_name for step in restored.steps] == [
        "run_script_skill_stage",
        "confirm_script_creative",
        "run_script_skill_stage",
        "run_script_skill_stage",
        "run_script_skill_stage",
        "run_script_skill_stage",
        "run_script_skill_stage",
        "run_script_skill_stage",
        "run_script_skill_stage",
    ]
    assert all(step.status.value == "completed" for step in restored.steps)
    events = await runtime_repository.list_events("user-1", "conversation-1")
    assert any(event.type.value == "agent.step.progressed" for event in events)
    with pytest.raises(VideoAgentCredentialUnavailableError):
        credential.borrow_authorization()


@pytest.mark.asyncio
async def test_executor_marks_contract_invalid_tool_result_failed() -> None:
    class _EmptyInput(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)

    class _InvalidResultTool:
        spec = VideoToolSpec(
            name="invalid_result",
            description="返回未声明 Workspace 修改",
            input_model=_EmptyInput,
            cost_level=VideoToolCostLevel.NONE,
            confirmation_required=False,
            idempotency_mode=VideoToolIdempotencyMode.REQUEST,
            recovery_mode=VideoToolRecoveryMode.INLINE,
            workspace_mutations=("script",),
        )

        async def execute(self, context, arguments):  # noqa: ANN001, ARG002
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="不应标记完成",
                workspace_patch={"script_pipeline": {}},
            )

    now = datetime(2026, 8, 12, tzinfo=UTC)
    repository = MemoryVideoAgentRepository(
        event_repository=MemoryAgentRuntimeRepository(),
    )
    workspace = VideoWorkspace(
        workspace_id="workspace-invalid-result",
        conversation_id="conversation-invalid-result",
        created_at=now,
        updated_at=now,
    )
    plan = AgentPlan(
        plan_id="plan-invalid-result",
        workspace_id=workspace.workspace_id,
        conversation_id=workspace.conversation_id,
        status=AgentPlanStatus.PLANNING,
        public_goal="验证工具失败语义",
        steps=(
            AgentPlanStep(
                step_id="step-invalid-result",
                plan_id="plan-invalid-result",
                sequence=1,
                tool_name="invalid_result",
                title="执行无效工具",
                status=PlanStepStatus.PENDING,
                arguments={},
            ),
        ),
        created_at=now,
        updated_at=now,
    )
    await repository.create_workspace("user-1", workspace)
    await repository.save_plan("user-1", plan, list(plan.steps))

    result = await VideoAgentExecutor(
        repository=repository,
        registry=VideoToolRegistry([_InvalidResultTool()]),
        clock=lambda: now,
    ).run_plan("user-1", plan.plan_id)

    assert result.status is AgentPlanStatus.FAILED
    assert result.steps[0].status is PlanStepStatus.FAILED
    assert result.steps[0].public_summary == "工具结果无效，请稍后重试"


@pytest.mark.asyncio
async def test_runner_rejects_cross_conversation_plan_and_discards_credential() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    submission = await VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
    ).submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=(),
    )
    runner = VideoAgentRunner(
        repository=video_repository,
        executor=VideoAgentExecutor(
            repository=video_repository,
            registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
        ),
    )
    credential = TransientVideoAgentCredential("Bearer transient-test")

    with pytest.raises(AgentRuntimeRecordConflictError):
        await runner.notify_turn(
            VideoAgentRunScope(
                user_id="user-1",
                conversation_id="conversation-other",
                turn_id="turn-1",
                plan_id=submission.plan.plan_id,
            ),
            credential,
        )

    with pytest.raises(VideoAgentCredentialUnavailableError):
        credential.borrow_authorization()
