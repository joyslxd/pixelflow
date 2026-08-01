from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    ContextBudgetReport,
    ContextEnvelope,
    ExplicitActionSignal,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    WorkflowCommand,
    WorkflowCommandDispatcher,
    workflow_namespace,
)
from pixelflow.agent_runtime.persistence import MemoryVideoRuntimeRepository
from pixelflow.agent_runtime.supervisor import (
    DecisionValidator,
    DeterministicTargetResolver,
    SupervisorDecisionService,
    SupervisorTurnEvidence,
)
from pixelflow.agent_workflows.video import (
    VideoLiveStateConflictError,
    VideoLiveWorkflowHandler,
    VideoPlanningWorkflowService,
    VideoScenePackageWorkflowService,
    WorkflowDispatchResult,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)
from pixelflow.agent_workflows.video.live_capabilities import TransientTurnCredential
from pixelflow.creative.plan_markdown import (
    PlanMarkdownResult,
    build_plan_markdown,
    restore_plan_version,
)
from pixelflow.intake.forms import draft_creative_directions, validate_form
from pixelflow.tasks.store import MemoryPixelFlowTaskStore

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 30,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_model_capabilities": {
        "generation_types": ["text_to_video", "image_to_video"],
        "upload_file_types": ["image"],
        "aspect_ratios": ["9:16", "16:9"],
        "sizes": ["1080p"],
        "sound_options": ["on", "off"],
        "durations_sec": [4, 5, 6, 8, 10, 12, 15],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


class _FakeClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _FakeCapabilities:
    def __init__(self) -> None:
        self.generate_scene_assets_calls = 0
        self.last_initial_plan_materials: list[dict[str, Any]] | None = None

    async def validate_intake(
        self,
        form_values: dict[str, Any],
        *,
        intake_rounds: int,
    ):
        return validate_form("video", form_values, intake_rounds=intake_rounds)

    async def generate_directions(
        self,
        form_values: dict[str, Any],
        intake_context: dict[str, Any],
    ):
        del intake_context
        return draft_creative_directions("video", form_values)

    async def generate_initial_plan(
        self,
        *,
        form_values: dict[str, Any],
        selected_direction: dict[str, Any],
        intake_context: dict[str, Any],
        materials: list[dict[str, Any]],
    ) -> PlanMarkdownResult:
        del intake_context
        self.last_initial_plan_materials = materials
        return build_plan_markdown("video", form_values, selected_direction)

    async def revise_plan(
        self,
        state,
        *,
        revision_feedback: str,
    ) -> PlanMarkdownResult:
        current = state.active_plan
        assert current is not None
        return PlanMarkdownResult(
            output_type="video",
            plan_markdown=current.plan_markdown,
            template_path=Path("video-live-test.md"),
            plan_version=current.plan_version,
            plan_history=current.plan_history,
            creation_contract=current.creation_contract,
            scene_durations_sec=current.scene_durations_sec,
            scene_blueprints=current.scene_blueprints,
            asset_manifest=current.asset_manifest,
        ).next_version(
            plan_markdown=f"{current.plan_markdown}\n\n## 用户修订\n{revision_feedback}",
            plan_history=current.plan_history,
            current_version=current.plan_version,
            change_source="user_feedback",
        )

    async def restore_plan(
        self,
        state,
        *,
        plan_version: int,
    ) -> PlanMarkdownResult:
        current = state.active_plan
        assert current is not None
        return restore_plan_version(
            intent="video",
            current_plan_markdown=current.plan_markdown,
            current_plan_version=current.plan_version,
            plan_history=current.plan_history,
            restore_version=plan_version,
            creation_contract=current.creation_contract,
            scene_durations_sec=current.scene_durations_sec,
            scene_blueprints=current.scene_blueprints,
            asset_manifest=current.asset_manifest,
        )

    async def generate_scene_assets(
        self,
        state,
        *,
        credential: TransientTurnCredential,
    ) -> dict[str, Any]:
        del credential
        self.generate_scene_assets_calls += 1
        assets = state.scene_package.global_assets
        for item in assets["characters"]:
            item["three_view_images"] = [
                f"https://assets.example.com/{item['asset_id']}.png"
            ]
        for collection in ("scenes", "props"):
            for item in assets[collection]:
                item["images"] = [
                    f"https://assets.example.com/{item['asset_id']}.png"
                ]
        return {"ok": True, "global_assets": assets}


class _SeededMemoryVideoRuntimeRepository(MemoryVideoRuntimeRepository):
    """为 Handler 合同测试写入真实 codec 生成的 Memory 状态。"""

    async def seed_state(self, state, *, action_key: str = "seed:state"):
        workflow = _workflow_record(state)
        envelope = encode_video_workflow_state(
            user_id="user-1",
            state=state,
            workflow_version=1,
            last_turn_id="turn-seed",
            last_action_key=action_key,
        )
        self._video_states[("user-1", workflow.workflow_id)] = envelope
        self._workflows[("user-1", workflow.workflow_id)] = workflow
        return envelope, workflow

    async def seed_result(self, result: WorkflowDispatchResult) -> None:
        """模拟原子提交后的权威信封，供同一原 Turn 的下一轮恢复使用。"""

        self._video_states[("user-1", result.workflow.workflow_id)] = result.state
        self._workflows[("user-1", result.workflow.workflow_id)] = result.workflow


class _FakeCredentialProvider:
    def __init__(
        self,
        credential: TransientTurnCredential | None = None,
    ) -> None:
        self._credential = credential
        self.turn_ids: list[str] = []

    def get(self, turn_id: str) -> TransientTurnCredential | None:
        self.turn_ids.append(turn_id)
        return self._credential


class _FixedSupervisorContextAssembler:
    """为动作可达性测试提供无模型、无外部 I/O 的已验证上下文。"""

    async def assemble(self, request: object) -> ContextEnvelope:
        return ContextEnvelope(
            current_input=str(getattr(request, "current_input")),
            validated_context_version=int(
                getattr(request, "expected_context_version")
            ),
            budget_report=ContextBudgetReport(
                estimated_input_tokens=1,
                effective_context_tokens=100,
                usable_input_tokens=80,
                max_output_tokens=10,
                safety_reserve_tokens=10,
                utilization=1 / 80,
            ),
        )


async def _dispatch_through_real_supervisor(
    *,
    handler: VideoLiveWorkflowHandler,
    workflow,
    action: AgentAction,
    patch: dict[str, Any],
    sequence: int,
) -> WorkflowDispatchResult:
    """经真实 DecisionService、Validator 与 Dispatcher 调用真实 Handler。"""

    turn = TurnRecord(
        turn_id=f"turn-delivery-{sequence}",
        conversation_id=workflow.conversation_id,
        client_input_id=UUID(int=sequence),
        status=TurnStatus.PROCESSING,
        target_workflow_id=workflow.workflow_id,
        expected_context_version=workflow.context_version,
        created_at=workflow.updated_at,
    )
    artifact_ref = workflow.latest_artifact_refs[-1]
    decision = await SupervisorDecisionService(
        resolver=DeterministicTargetResolver(),
        classifier=None,
        validator=DecisionValidator(),
        context_assembler=_FixedSupervisorContextAssembler(),
    ).decide(
        SupervisorTurnEvidence(
            user_id="user-1",
            conversation_id=workflow.conversation_id,
            turn=turn,
            content="执行视频交付动作",
            visible_messages=(),
            workflows=(workflow,),
            active_workflow_id=workflow.workflow_id,
            explicit_action=ExplicitActionSignal(
                action=action,
                intent=AgentIntent.VIDEO,
                workflow_id=workflow.workflow_id,
                stage=workflow.current_stage,
                artifact_ref=artifact_ref,
                patch=patch,
            ),
            expected_context_version=workflow.context_version,
            authoritative_context_version=workflow.context_version,
        )
    )
    dispatched = await WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.VIDEO: handler})
    ).dispatch_result(
        {
            "conversation_id": workflow.conversation_id,
            "user_id": "user-1",
            "turn_id": turn.turn_id,
            "current_input": "执行视频交付动作",
            "materials": [],
            "artifact_refs": [],
            "workflows": {workflow.workflow_id: workflow},
        },
        decision.decision,
    )
    assert isinstance(dispatched, WorkflowDispatchResult)
    return dispatched


@pytest.fixture
def state_repository() -> _SeededMemoryVideoRuntimeRepository:
    return _SeededMemoryVideoRuntimeRepository(task_store=MemoryPixelFlowTaskStore())


@pytest.fixture
def video_handler(
    state_repository: _SeededMemoryVideoRuntimeRepository,
) -> VideoLiveWorkflowHandler:
    return VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(),
    )


@pytest.fixture
def command_factory():
    def build(
        *,
        action: AgentAction,
        patch: dict[str, Any] | None = None,
        workflow=None,
        conversation_id: str | None = None,
        workflow_id: str | None = None,
        target_stage: str | None = None,
        target_artifact_ref: str | None = None,
        idempotency_key: str = "decision:turn-video-1",
    ) -> WorkflowCommand:
        workflow_id = workflow_id or (
            "wf-video-1" if workflow is None else workflow.workflow_id
        )
        conversation_id = conversation_id or (
            "conv-1" if workflow is None else workflow.conversation_id
        )
        decision = ActionDecision(
            action=action,
            intent=AgentIntent.VIDEO,
            target_workflow_id=(
                None
                if action is AgentAction.START_WORKFLOW
                else workflow_id
            ),
            target_stage=(
                target_stage
                if target_stage is not None
                else None if workflow is None else workflow.current_stage
            ),
            target_artifact_ref=target_artifact_ref,
            confidence=1,
            requires_confirmation=False,
            clarification_question=None,
            patch=patch or {},
            reason_code="explicit_target",
            idempotency_key=idempotency_key,
        )
        return WorkflowCommand(
            conversation_id=conversation_id,
            workflow_id=workflow_id,
            kind=WorkflowKind.VIDEO,
            decision=decision,
            workflow=workflow,
            namespace=workflow_namespace(conversation_id, workflow_id),
            user_id="user-1",
            turn_id="turn-video-1",
            current_input="生成一条智能戒指广告",
            materials=[],
            reply_to_message_id=None,
            artifact_refs=[],
        )

    return build


@pytest.mark.asyncio
async def test_start_workflow_opens_intake_interrupt(
    video_handler: VideoLiveWorkflowHandler,
    command_factory,
) -> None:
    """首轮决策不带目标，Handler 信任 Router 预分配的 Workflow ID。"""

    materials = [
        {
            "type": "image",
            "url": "https://assets.example.com/ring-front.png",
        },
        {
            "type": "image",
            "url": "https://assets.example.com/ring-side.png",
        },
    ]
    command = replace(
        command_factory(action=AgentAction.START_WORKFLOW),
        current_input="用这两张参考图生成智能戒指广告",
        materials=materials,
    )
    result = await video_handler.dispatch(
        command
    )

    assert isinstance(result, WorkflowDispatchResult)
    assert result.workflow.current_stage == "intake"
    assert result.state.state_kind.value == "planning"
    assert result.interrupt is not None
    assert result.interrupt.kind == "video_intake_form"
    assert result.interrupt.payload == {
        "workflow_id": command.workflow_id,
        "stage": "intake",
        "ui_kind": "video_intake_form",
        "form_values": {},
        "core_message": "用这两张参考图生成智能戒指广告",
        "materials": materials,
        "intake_rounds": 0,
    }
    assert result.turn_status is TurnStatus.WAITING_USER


@pytest.mark.asyncio
async def test_start_workflow_rejects_decision_with_preexisting_target(
    video_handler: VideoLiveWorkflowHandler,
    command_factory,
) -> None:
    """首轮 target 只能由 Router 预分配，Decision 不得伪装成既有目标。"""

    command = command_factory(action=AgentAction.START_WORKFLOW)
    invalid = replace(
        command,
        decision=command.decision.model_copy(
            update={"target_workflow_id": command.workflow_id}
        ),
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_start_workflow_target_must_be_new",
    ):
        await video_handler.dispatch(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "seed_stage", "patch", "expected_stage", "interrupt_kind"),
    [
        (
            AgentAction.CONTINUE_WORKFLOW,
            "intake",
            {"form_values": VIDEO_FORM},
            "direction_review",
            "video_direction_review",
        ),
        (
            AgentAction.MODIFY_WORKFLOW,
            "plan_review",
            {"revision_feedback": "节奏更快"},
            "plan_review",
            "video_plan_review",
        ),
        (
            AgentAction.REGENERATE_STAGE,
            "direction_review",
            {},
            "direction_review",
            "video_direction_review",
        ),
        (
            AgentAction.SWITCH_WORKFLOW,
            "plan_review",
            {},
            "plan_review",
            None,
        ),
        (
            AgentAction.CANCEL_WORKFLOW,
            "plan_review",
            {},
            "plan_review",
            None,
        ),
    ],
)
async def test_video_handler_planning_action_table(
    action: AgentAction,
    seed_stage: str,
    patch: dict[str, Any],
    expected_stage: str,
    interrupt_kind: str | None,
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    state = _planning_state(seed_stage)
    _, workflow = await state_repository.seed_state(state)

    result = await video_handler.dispatch(
        command_factory(action=action, patch=patch, workflow=workflow)
    )

    assert result.workflow.current_stage == expected_stage
    assert (None if result.interrupt is None else result.interrupt.kind) == interrupt_kind
    if result.interrupt is not None:
        assert result.interrupt.payload["ui_kind"] == interrupt_kind
    if action is AgentAction.SWITCH_WORKFLOW:
        assert result.update_active_workflow is True
        assert result.active_workflow_id == workflow.workflow_id
    if action is AgentAction.CANCEL_WORKFLOW:
        cancelled = decode_video_workflow_state(result.state)
        assert result.workflow.status is WorkflowStatus.CANCELLED
        assert cancelled.status is WorkflowStatus.CANCELLED
        assert cancelled.current_stage is state.current_stage
        assert cancelled.stage_version == state.stage_version + 1
        assert cancelled.context_version == state.context_version + 1
        assert result.update_active_workflow is True
        assert result.active_workflow_id is None


@pytest.mark.asyncio
async def test_video_handler_intake_cancel_requires_explicit_form_cancelled_patch(
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    state = _planning_state("intake")
    _, workflow = await state_repository.seed_state(state)

    result = await video_handler.dispatch(
        command_factory(
            action=AgentAction.CANCEL_WORKFLOW,
            patch={"form_cancelled": True},
            workflow=workflow,
        )
    )
    cancelled = decode_video_workflow_state(result.state)

    assert cancelled.current_stage.value == "form_cancelled"
    assert cancelled.status is WorkflowStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [{}, {"form_cancelled": False}, {"form_cancelled": True, "extra": True}],
)
async def test_video_handler_rejects_ambiguous_intake_cancel_patch(
    patch: dict[str, Any],
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    state = _planning_state("intake")
    _, workflow = await state_repository.seed_state(state)

    with pytest.raises(VideoLiveStateConflictError, match="video_action_patch_invalid"):
        await video_handler.dispatch(
            command_factory(
                action=AgentAction.CANCEL_WORKFLOW,
                patch=patch,
                workflow=workflow,
            )
        )


@pytest.mark.asyncio
async def test_video_handler_plan_review_regenerates_three_new_directions(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    state = _planning_state("plan_review")
    _, workflow = await state_repository.seed_state(state)
    capabilities = _FakeCapabilities()
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=capabilities,
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.REGENERATE_STAGE,
            patch={},
            workflow=workflow,
        )
    )
    restarted = decode_video_workflow_state(result.state)

    assert restarted.current_stage.value == "direction_review"
    assert restarted.active_plan is None
    assert restarted.selected_direction == {}
    assert len(restarted.creative_directions) == 3
    assert result.interrupt is not None
    assert result.interrupt.kind == "video_direction_review"
    assert result.interrupt.payload["ui_kind"] == "video_direction_review"


@pytest.mark.asyncio
async def test_video_handler_retries_failed_merge_with_existing_operation_port(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """retry_failed 只让 M11 为原合并请求领取新 attempt。"""

    from test_agent_video_workflow_postproduction import (
        _claim_started,
        _complete_generation,
    )

    from pixelflow.agent_workflows.video import VideoPostProductionWorkflowService

    generation, operation_port, _ = await _complete_generation()
    service = VideoPostProductionWorkflowService(operation_port)
    state = await service.start_merge(generation)
    state = await _claim_started(state, operation_port)
    state = await service.record_merge_failure(
        state,
        error="供应商暂时不可用",
        attempts=3,
        retryable=True,
    )
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.RETRY_FAILED,
            workflow=workflow,
        )
    )

    assert result.workflow.current_stage == "merge_video"
    assert result.workflow.status is WorkflowStatus.RUNNING
    assert result.workflow.pending_external_job is not None


@pytest.mark.asyncio
async def test_video_handler_selects_direction_and_opens_plan_review(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """方向确认只生成一个初始 Plan，并等待显式人工审核。"""

    original_material = {
        "url": "https://assets.example.com/ring.png",
        "type": "image",
    }
    state = _planning_state("direction_review", materials=[original_material])
    _, workflow = await state_repository.seed_state(state)
    capabilities = _FakeCapabilities()
    video_handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=capabilities,
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(),
    )

    result = await video_handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            patch={"direction_id": "direction_1"},
            workflow=workflow,
        )
    )

    assert result.workflow.current_stage == "plan_review"
    assert result.interrupt is not None
    assert result.interrupt.kind == "video_plan_review"
    assert result.messages[0].payload["artifact"]["type"] == "plan"
    assert capabilities.last_initial_plan_materials == [original_material]


@pytest.mark.asyncio
async def test_video_handler_restores_plan_history_without_appending_version(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """Plan 回退复用 M11 历史，不伪造一个新的修订版本。"""

    capabilities = _FakeCapabilities()
    planning = VideoPlanningWorkflowService()
    state = _planning_state("plan_review")
    revised = await capabilities.revise_plan(
        state,
        revision_feedback="节奏更快",
    )
    state = planning.publish_revision(
        state,
        revised,
        now=state.updated_at + timedelta(seconds=1),
    )
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=capabilities,
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={"plan_version": 1},
            workflow=workflow,
        )
    )

    restored = decode_video_workflow_state(result.state)
    assert restored.active_plan.plan_version == 1
    assert restored.active_plan.restored_from_version == 1
    assert len(restored.active_plan.plan_history) == 2


@pytest.mark.asyncio
async def test_video_handler_plan_continue_without_credential_opens_auth_interrupt(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """只有到付费图片 Skill 边界才读取一次临时凭据。"""

    state = _planning_state("plan_review")
    _, workflow = await state_repository.seed_state(state)
    capabilities = _FakeCapabilities()
    credential_provider = _FakeCredentialProvider()
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=capabilities,
        credential_provider=credential_provider,
        clock=_FakeClock(),
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=workflow,
            target_artifact_ref=workflow.latest_artifact_refs[0],
        )
    )

    assert result.workflow.current_stage == "generate_scene_assets"
    assert result.interrupt is not None
    assert result.interrupt.kind == "authorization_required"
    assert result.interrupt.payload == {
        "workflow_id": workflow.workflow_id,
        "stage": "generate_scene_assets",
        "artifact_ref": workflow.latest_artifact_refs[0],
        "ui_kind": "authorization_required",
        "authorization_action": {
            "action": "continue_workflow",
            "intent": "video",
            "workflow_id": workflow.workflow_id,
            "stage": workflow.current_stage,
            "artifact_ref": workflow.latest_artifact_refs[0],
            "patch": {},
        },
    }
    assert "token" not in str(result.interrupt.payload).lower()
    assert "authorization" not in str(
        result.interrupt.payload["authorization_action"]
    ).lower()
    assert credential_provider.turn_ids == ["turn-video-1"]
    assert capabilities.generate_scene_assets_calls == 0


@pytest.mark.asyncio
async def test_video_handler_scene_assets_use_transient_credential_and_open_review(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """场景资产生成后只保存安全图片结果，不保存临时 Authorization。"""

    planning = VideoPlanningWorkflowService()
    approved = planning.approve_plan(_planning_state("plan_review"), now=NOW)
    state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=[],
        now=NOW,
    )
    _, workflow = await state_repository.seed_state(state)
    capabilities = _FakeCapabilities()
    credential_provider = _FakeCredentialProvider(
        TransientTurnCredential("Bearer live-secret")
    )
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=capabilities,
        credential_provider=credential_provider,
        clock=_FakeClock(),
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=workflow,
        )
    )

    assert result.workflow.current_stage == "scene_package_review"
    assert result.interrupt is not None
    assert result.interrupt.kind == "video_scene_package_review"
    assert result.interrupt.payload["ui_kind"] == "video_scene_package_review"
    published = decode_video_workflow_state(result.state)
    artifact = result.messages[0].model_dump(mode="json")["payload"]["artifact"]
    assert set(artifact) == {
        "type",
        "title",
        "description",
        "actionLabel",
        "videoScenePackages",
    }
    assert artifact["type"] == "video_scene_packages"
    assert artifact["videoScenePackages"] == {
        "ok": True,
        "message": "视频分镜与场景素材已准备完成。",
        "requires_confirmation": True,
        "review_timeout_sec": None,
        "target_duration_ms": published.scene_package.target_duration_ms,
        "global_assets": published.scene_package.global_assets,
        "scene_packages": published.scene_package.scene_packages,
        "creation_contract": published.scene_package.creation_contract,
    }
    assert "live-secret" not in result.model_dump_json()
    assert credential_provider.turn_ids == ["turn-video-1"]


@pytest.mark.asyncio
async def test_video_handler_confirms_scene_package_and_claims_scene_operations(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """场景包确认只通过 M11/M06 领取分镜 Operation，不直接调用 Provider。"""

    from test_agent_video_workflow_generation import (
        _AtomicFakeOperationPort,
        _reviewed_scene_package_state,
    )

    state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=workflow,
        )
    )

    assert result.workflow.current_stage == "generate_scene_videos"
    assert result.workflow.pending_external_job is not None
    assert result.interrupt is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch_factory",
    [
        lambda state: {
            "scene_id": state.scene_package.scene_packages[0]["scene_id"],
            "scene_patch": {"narration": "新的审核旁白"},
        },
        lambda state: {
            "asset_action": "replace",
            "asset_group": "characters",
            "asset_id": state.scene_package.global_assets["characters"][0]["asset_id"],
            "asset_patch": {
                "source": "image_asset",
                "display_image_url": "https://assets.example.com/replaced-character.png",
                "generation_reference_url": "https://assets.example.com/replaced-character-source.png",
            },
        },
        lambda state: {
            "asset_action": "delete",
            "asset_group": "characters",
            "asset_id": state.scene_package.global_assets["characters"][0]["asset_id"],
        },
        lambda state: {
            "asset_action": "add",
            "asset_group": "props",
            "asset_id": "prop-manual-cup",
            "asset_patch": {
                "source": "local_upload",
                "display_image_url": "https://assets.example.com/cup.png",
                "generation_reference_url": "https://assets.example.com/cup-source.png",
                "asset_name": "运动水杯",
            },
        },
    ],
)
async def test_video_handler_applies_bounded_scene_package_review_modification(
    patch_factory,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    from test_agent_video_workflow_generation import _reviewed_scene_package_state

    state = _reviewed_scene_package_state()
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
    )
    patch = patch_factory(state)

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch=patch,
            workflow=workflow,
        )
    )
    updated = decode_video_workflow_state(result.state)

    assert updated.scene_package.to_dict() != state.scene_package.to_dict()
    assert updated.scene_package.creation_contract == state.scene_package.creation_contract
    assert updated.scene_package.target_duration_ms == state.scene_package.target_duration_ms
    assert updated.stage_version == state.stage_version + 1
    assert updated.context_version == state.context_version + 1
    assert result.interrupt is not None
    assert result.interrupt.kind == "video_scene_package_review"
    assert result.interrupt.payload["ui_kind"] == "video_scene_package_review"


@pytest.mark.asyncio
async def test_video_handler_modifies_then_regenerates_only_dirty_scene(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """单镜修改先等待复核，重生成动作再领取 dirty 分镜 Operation。"""

    from test_agent_video_workflow_postproduction import _complete_generation

    state, operation_port, _ = await _complete_generation()
    _, workflow = await state_repository.seed_state(state)
    clock = _FakeClock(state.updated_at + timedelta(seconds=1))
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=clock,
        operation_port=operation_port,
    )
    scene_id = state.scene_packages[0]["scene_id"]

    modified = await handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={
                "scene_id": scene_id,
                "scene_patch": {"narration": "新的单镜旁白"},
            },
            workflow=workflow,
        )
    )
    modified_state = decode_video_workflow_state(modified.state)
    await state_repository.seed_state(
        modified_state,
        action_key=modified.state.last_action_key,
    )

    regenerated = await handler.dispatch(
        command_factory(
            action=AgentAction.REGENERATE_STAGE,
            workflow=modified.workflow,
            idempotency_key="decision:turn-video-2",
        )
    )
    regenerated_state = decode_video_workflow_state(regenerated.state)

    assert modified.workflow.current_stage == "scene_video_review"
    assert modified.interrupt is not None
    assert modified.interrupt.kind == "video_scene_video_review"
    assert modified.interrupt.payload["ui_kind"] == "video_result_review"
    artifact = modified.messages[0].model_dump(mode="json")["payload"]["artifact"]
    assert set(artifact) == {
        "type",
        "title",
        "description",
        "actionLabel",
        "videoScenePackages",
        "generatedSceneVideos",
        "videoScenePackageEditedSceneIds",
    }
    assert artifact["videoScenePackages"]["ok"] is True
    assert artifact["videoScenePackages"]["scene_packages"] == (
        modified_state.scene_packages
    )
    assert artifact["generatedSceneVideos"] == {
        "ok": True,
        "endpoint": "/api/video/text-to-video",
        "scene_videos": modified_state.scene_videos,
        "failed_scenes": modified_state.failed_scenes,
        "message": "场景视频生成完成。",
        "quota_insufficient": False,
    }
    assert regenerated.workflow.current_stage == "generate_scene_videos"
    assert [
        item["scene_id"] for item in regenerated_state.generation_requests
    ] == [scene_id]


@pytest.mark.asyncio
async def test_video_handler_confirms_successful_scenes_and_starts_merge(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """全部成功且没有 dirty/failed 时，人工确认才领取合并 Operation。"""

    from test_agent_video_workflow_postproduction import _complete_generation

    state, operation_port, _ = await _complete_generation()
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=workflow,
        )
    )

    assert result.workflow.current_stage == "merge_video"
    assert result.workflow.status is WorkflowStatus.RUNNING
    assert result.workflow.pending_external_job is not None


@pytest.mark.asyncio
async def test_video_handler_requests_quality_review_from_video_review(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """成片修改意见先启动 QAAgent QC，不直接猜测受影响镜头。"""

    from test_agent_video_workflow_delivery import _video_review_state

    state, operation_port, _, _ = await _video_review_state()
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={"user_feedback": "请检查第二镜商品露出"},
            workflow=workflow,
        )
    )

    assert result.workflow.current_stage == "quality_review"
    assert result.workflow.status is WorkflowStatus.RUNNING
    assert result.workflow.pending_external_job is not None


@pytest.mark.asyncio
async def test_video_handler_applies_qc_scoped_scene_revision(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """QC 后只按显式 scene_patches 重生成受影响分镜。"""

    from test_agent_video_workflow_delivery import _video_review_state
    from test_agent_video_workflow_postproduction import _claim_started

    from pixelflow.agent_workflows.video import VideoQualityReviewWorkflowResult

    state, operation_port, generation_service, postproduction_service = (
        await _video_review_state()
    )
    state = await postproduction_service.start_quality_review(
        state,
        user_feedback="请定位第二镜问题",
    )
    state = await _claim_started(state, operation_port)
    scene_id = state.generation_state.scene_packages[1]["scene_id"]
    state = await postproduction_service.record_quality_success(
        state,
        result=VideoQualityReviewWorkflowResult(
            ok=True,
            passed=False,
            summary_markdown="第二镜需调整",
            quality_report_markdown="商品露出不足",
            affected_scene_ids=[scene_id],
            revision_prompt="增强第二镜商品露出",
            task_id="qc-provider-live",
        ),
        provider_job_id="qc-provider-live",
    )
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        generation_service=generation_service,
        postproduction_service=postproduction_service,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={
                "scene_patches": {
                    scene_id: {"narration": "强化商品功能旁白"}
                }
            },
            workflow=workflow,
        )
    )
    generation = decode_video_workflow_state(result.state)

    assert result.workflow.current_stage == "generate_scene_videos"
    assert [item["scene_id"] for item in generation.generation_requests] == [
        scene_id
    ]


@pytest.mark.asyncio
async def test_video_handler_final_confirmation_initializes_delivery_state(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """最终确认必须由用户显式继续，并初始化可下载交付状态。"""

    from test_agent_video_workflow_delivery import _video_review_state

    state, operation_port, _, _ = await _video_review_state()
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=workflow,
        )
    )
    delivery = decode_video_workflow_state(result.state)

    assert result.state.state_kind.value == "delivery"
    assert result.workflow.current_stage == "completed"
    assert result.workflow.status is WorkflowStatus.COMPLETED
    assert delivery.postproduction_state.finalized_by_user is True
    artifact = result.messages[0].model_dump(mode="json")["payload"]["artifact"]
    merged = delivery.postproduction_state.merged_video
    assert merged is not None
    assert artifact["type"] == "video_result"
    assert artifact["title"] == "视频成片"
    assert artifact["description"]
    assert artifact["actionLabel"] == "下载视频"
    assert artifact["videoAccepted"] is True
    assert artifact["videoScenePackages"]["ok"] is True
    assert artifact["generatedSceneVideos"]["ok"] is True
    assert artifact["mergedVideo"] == {
        "ok": True,
        "endpoint": merged["endpoint"],
        "merged_video_url": merged["video_url"],
        "task_id": merged["task_id"],
        "scene_videos": merged["scene_videos"],
        "error": None,
        "message": "视频合并完成。",
        "quota_insufficient": False,
        "raw": merged["raw"],
    }


@pytest.mark.asyncio
async def test_video_handler_records_only_final_merged_video_download(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """导出交付只接受当前合并成品 URL，并写入 Delivery 权威证据。"""

    from test_agent_video_workflow_delivery import _video_review_state

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, postproduction_service = (
        await _video_review_state()
    )
    finished = await postproduction_service.finish(
        review,
        operation_port=operation_port,
        now=review.updated_at + timedelta(seconds=1),
    )
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    state = await delivery_service.initialize(
        finished,
        now=finished.updated_at,
    )
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )
    video_url = state.postproduction_state.merged_video["video_url"]

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            patch={"delivery_download_url": video_url},
            workflow=workflow,
        )
    )
    updated = decode_video_workflow_state(result.state)
    artifact = result.messages[0].model_dump(mode="json")["payload"]["artifact"]

    assert updated.final_video_delivery["deliveryDownloadedUrl"] == video_url
    assert updated.delivery_artifact_ref in result.workflow.latest_artifact_refs
    assert artifact["title"] == "视频成片"
    assert artifact["actionLabel"] == "下载视频"
    assert artifact["mergedVideo"]["merged_video_url"] == video_url


@pytest.mark.asyncio
async def test_video_handler_starts_jianying_from_delivery_service(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """剪映动作复用 Delivery Service，只领取稳定 Operation。"""

    from test_agent_video_workflow_delivery import _video_review_state

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, postproduction_service = (
        await _video_review_state()
    )
    finished = await postproduction_service.finish(
        review,
        operation_port=operation_port,
        now=review.updated_at + timedelta(seconds=1),
    )
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    state = await delivery_service.initialize(
        finished,
        now=finished.updated_at,
    )
    _, workflow = await state_repository.seed_state(state)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(state.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )

    result = await handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={
                "jianying_action": "start",
                "project_name": "智能戒指新品广告",
            },
            workflow=workflow,
        )
    )
    updated = decode_video_workflow_state(result.state)

    assert updated.pending_jianying_request["project_name"] == (
        "智能戒指新品广告"
    )
    assert result.workflow.pending_external_job is not None


@pytest.mark.asyncio
async def test_real_chain_starts_jianying_before_video_acceptance(
    state_repository: _SeededMemoryVideoRuntimeRepository,
) -> None:
    """未结束成片时可先生成剪映草稿，且不得隐式接受视频。"""

    from test_agent_video_workflow_delivery import _video_review_state

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, _ = await _video_review_state()
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    _, workflow = await state_repository.seed_state(review)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(review.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )

    result = await _dispatch_through_real_supervisor(
        handler=handler,
        workflow=workflow,
        action=AgentAction.MODIFY_WORKFLOW,
        patch={
            "jianying_action": "start",
            "project_name": "智能戒指新品广告",
        },
        sequence=201,
    )
    delivery = decode_video_workflow_state(result.state)

    assert result.state.state_kind.value == "delivery"
    assert result.workflow.status is WorkflowStatus.AWAITING_USER
    assert result.workflow.current_stage == "video_review"
    assert result.workflow.pending_external_job is not None
    assert delivery.postproduction_state.finalized_by_user is False
    assert result.messages[0].payload["artifact"]["videoAccepted"] is False


@pytest.mark.asyncio
async def test_real_chain_final_confirmation_does_not_require_jianying(
    state_repository: _SeededMemoryVideoRuntimeRepository,
) -> None:
    """已初始化的未接受 Delivery 无草稿记录也能独立完成最终确认。"""

    from test_agent_video_workflow_delivery import _video_review_state

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, postproduction_service = (
        await _video_review_state()
    )
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    delivery = await delivery_service.initialize(review)
    _, workflow = await state_repository.seed_state(delivery)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(delivery.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        postproduction_service=postproduction_service,
        delivery_service=delivery_service,
    )

    result = await _dispatch_through_real_supervisor(
        handler=handler,
        workflow=workflow,
        action=AgentAction.CONTINUE_WORKFLOW,
        patch={},
        sequence=202,
    )
    completed = decode_video_workflow_state(result.state)

    assert completed.postproduction_state.finalized_by_user is True
    assert completed.jianying_draft_records == {}
    assert result.workflow.status is WorkflowStatus.COMPLETED
    assert result.messages[0].payload["artifact"]["videoAccepted"] is True


@pytest.mark.asyncio
async def test_real_chain_records_completed_final_video_download(
    state_repository: _SeededMemoryVideoRuntimeRepository,
) -> None:
    """完成态视频下载经 Supervisor 白名单抵达真实交付 Handler。"""

    from test_agent_video_workflow_delivery import _video_review_state

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, postproduction_service = (
        await _video_review_state()
    )
    finished = await postproduction_service.finish(
        review,
        operation_port=operation_port,
        now=review.updated_at + timedelta(seconds=1),
    )
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    delivery = await delivery_service.initialize(finished)
    _, workflow = await state_repository.seed_state(delivery)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(delivery.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )
    video_url = finished.merged_video["video_url"]

    result = await _dispatch_through_real_supervisor(
        handler=handler,
        workflow=workflow,
        action=AgentAction.CONTINUE_WORKFLOW,
        patch={"delivery_download_url": video_url},
        sequence=203,
    )
    downloaded = decode_video_workflow_state(result.state)

    assert downloaded.final_video_delivery["deliveryDownloadedUrl"] == video_url


@pytest.mark.asyncio
async def test_real_chain_retries_failed_draft_after_video_completion(
    state_repository: _SeededMemoryVideoRuntimeRepository,
) -> None:
    """完成态草稿失败可经 RETRY_FAILED 创建同版本的新 attempt。"""

    from test_agent_video_workflow_delivery import (
        _FakeJianyingDraftSkill,
        _video_review_state,
    )

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService
    from pixelflow.jianying_draft.models import (
        JianyingDraftResult,
        JianyingDraftStatus,
    )

    review, operation_port, _, postproduction_service = (
        await _video_review_state()
    )
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    delivery = await delivery_service.initialize(review)
    failed = await delivery_service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill(
            [
                JianyingDraftResult(
                    status=JianyingDraftStatus.FAILED,
                    message="第三方剪映草稿任务处理失败：素材校验失败",
                )
            ]
        ),
    )
    finished = await postproduction_service.finish(
        review,
        operation_port=operation_port,
        now=failed.updated_at + timedelta(seconds=1),
    )
    completed = await delivery_service.synchronize_postproduction(
        failed,
        finished,
        now=finished.updated_at,
    )
    _, workflow = await state_repository.seed_state(completed)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(completed.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )

    result = await _dispatch_through_real_supervisor(
        handler=handler,
        workflow=workflow,
        action=AgentAction.RETRY_FAILED,
        patch={"jianying_action": "start"},
        sequence=204,
    )
    retried = decode_video_workflow_state(result.state)

    assert retried.pending_operation is not None
    assert retried.operation_attempts[retried.current_storyboard_version_id] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    ["failed", "timeout"],
)
async def test_real_chain_retries_failed_draft_before_video_acceptance(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    terminal_status: str,
) -> None:
    """未确认视频的失败或超时草稿可显式重试，且不得提前接受视频。"""

    from test_agent_video_workflow_delivery import (
        _FakeJianyingDraftSkill,
        _video_review_state,
    )

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService
    from pixelflow.jianying_draft.models import (
        JianyingDraftResult,
        JianyingDraftStatus,
    )

    review, operation_port, _, _ = await _video_review_state()
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    delivery = await delivery_service.initialize(review)
    terminal = await delivery_service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill(
            [
                JianyingDraftResult(
                    status=JianyingDraftStatus(terminal_status),
                    message="剪映草稿未生成，请重试。",
                )
            ]
        ),
    )
    _, workflow = await state_repository.seed_state(terminal)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(terminal.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )

    result = await _dispatch_through_real_supervisor(
        handler=handler,
        workflow=workflow,
        action=AgentAction.RETRY_FAILED,
        patch={"jianying_action": "start"},
        sequence=206 if terminal_status == "failed" else 207,
    )
    retried = decode_video_workflow_state(result.state)
    version_id = retried.current_storyboard_version_id

    assert result.workflow.status is WorkflowStatus.AWAITING_USER
    assert result.workflow.current_stage == "video_review"
    assert retried.pending_operation is not None
    assert retried.operation_attempts[version_id] == 2
    assert retried.postproduction_state.finalized_by_user is False
    assert result.messages[0].payload["artifact"]["videoAccepted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft_state",
    ["missing", "running", "succeeded"],
)
async def test_real_chain_rejects_unaccepted_draft_retry_without_failed_terminal(
    state_repository: _SeededMemoryVideoRuntimeRepository,
    draft_state: str,
) -> None:
    """未确认视频只有当前版本草稿为失败或超时时才能显式重试。"""

    from test_agent_video_workflow_delivery import (
        _FakeJianyingDraftSkill,
        _succeeded_result,
        _video_review_state,
    )

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, _ = await _video_review_state()
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    delivery = await delivery_service.initialize(review)
    if draft_state == "running":
        delivery = await delivery_service.start_jianying_draft(delivery)
    elif draft_state == "succeeded":
        delivery = await delivery_service.generate_jianying_with_skill(
            delivery,
            skill=_FakeJianyingDraftSkill([_succeeded_result()]),
        )
    _, workflow = await state_repository.seed_state(delivery)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(delivery.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_jianying_retry_requires_failed_or_timeout",
    ):
        await _dispatch_through_real_supervisor(
            handler=handler,
            workflow=workflow,
            action=AgentAction.RETRY_FAILED,
            patch={"jianying_action": "start"},
            sequence={"missing": 208, "running": 209, "succeeded": 210}[
                draft_state
            ],
        )


@pytest.mark.asyncio
async def test_real_chain_records_completed_jianying_download(
    state_repository: _SeededMemoryVideoRuntimeRepository,
) -> None:
    """完成态剪映下载经 CONTINUE 到达真实交付 Handler 并留下证据。"""

    from test_agent_video_workflow_delivery import (
        _FakeJianyingDraftSkill,
        _succeeded_result,
        _video_review_state,
    )

    from pixelflow.agent_workflows.video import VideoDeliveryWorkflowService

    review, operation_port, _, postproduction_service = (
        await _video_review_state()
    )
    delivery_service = VideoDeliveryWorkflowService(operation_port)
    delivery = await delivery_service.initialize(review)
    succeeded = await delivery_service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    finished = await postproduction_service.finish(
        review,
        operation_port=operation_port,
        now=succeeded.updated_at + timedelta(seconds=1),
    )
    completed = await delivery_service.synchronize_postproduction(
        succeeded,
        finished,
        now=finished.updated_at,
    )
    _, workflow = await state_repository.seed_state(completed)
    handler = VideoLiveWorkflowHandler(
        repository=state_repository,
        capabilities=_FakeCapabilities(),
        credential_provider=_FakeCredentialProvider(),
        clock=_FakeClock(completed.updated_at + timedelta(seconds=1)),
        operation_port=operation_port,
        delivery_service=delivery_service,
    )
    version_id = completed.current_storyboard_version_id
    download_url = completed.jianying_draft_records[version_id]["download_url"]

    result = await _dispatch_through_real_supervisor(
        handler=handler,
        workflow=workflow,
        action=AgentAction.CONTINUE_WORKFLOW,
        patch={
            "download_url": download_url,
            "jianying_action": "download",
            "storyboard_version_id": version_id,
        },
        sequence=205,
    )
    downloaded = decode_video_workflow_state(result.state)

    assert downloaded.jianying_draft_records[version_id]["draftDownloadedAt"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "seed_stage", "patch"),
    [
        (AgentAction.START_WORKFLOW, "intake", {}),
        (AgentAction.CONTINUE_WORKFLOW, "direction_review", {"form_values": VIDEO_FORM}),
        (AgentAction.MODIFY_WORKFLOW, "direction_review", {"revision_feedback": "节奏更快"}),
        (AgentAction.REGENERATE_STAGE, "intake", {}),
        (AgentAction.RETRY_FAILED, "plan_review", {}),
        (AgentAction.CANCEL_WORKFLOW, "form_cancelled", {}),
    ],
)
async def test_video_handler_rejects_action_in_illegal_state(
    action: AgentAction,
    seed_stage: str,
    patch: dict[str, Any],
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    state = _planning_state(seed_stage)
    _, workflow = await state_repository.seed_state(state)

    with pytest.raises(VideoLiveStateConflictError):
        await video_handler.dispatch(
            command_factory(action=action, patch=patch, workflow=workflow)
        )


@pytest.mark.asyncio
async def test_video_handler_rejects_cross_conversation_state(
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    _, workflow = await state_repository.seed_state(_planning_state("plan_review"))

    with pytest.raises(VideoLiveStateConflictError, match="conversation"):
        await video_handler.dispatch(
            command_factory(
                action=AgentAction.MODIFY_WORKFLOW,
                patch={"revision_feedback": "节奏更快"},
                workflow=workflow,
                conversation_id="conv-2",
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_stage", "target_artifact_ref", "patch", "reason"),
    [
        ("direction_review", None, {"revision_feedback": "节奏更快"}, "stage"),
        ("plan_review", "artifact:stale", {"revision_feedback": "节奏更快"}, "artifact"),
        ("plan_review", None, {"unexpected": True}, "patch"),
    ],
)
async def test_video_handler_rejects_stale_target_or_illegal_patch(
    target_stage: str,
    target_artifact_ref: str | None,
    patch: dict[str, Any],
    reason: str,
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    _, workflow = await state_repository.seed_state(_planning_state("plan_review"))

    with pytest.raises(VideoLiveStateConflictError, match=reason):
        await video_handler.dispatch(
            command_factory(
                action=AgentAction.MODIFY_WORKFLOW,
                patch=patch,
                workflow=workflow,
                target_stage=target_stage,
                target_artifact_ref=target_artifact_ref,
            )
        )


@pytest.mark.asyncio
async def test_video_handler_replays_same_action_key_with_stable_result(
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """同一前置快照与 action key 重放时保持信封和消息 ID 稳定。"""

    _, workflow = await state_repository.seed_state(_planning_state("plan_review"))
    command = command_factory(
        action=AgentAction.MODIFY_WORKFLOW,
        patch={"revision_feedback": "节奏更快"},
        workflow=workflow,
        idempotency_key="decision:stable-replay",
    )

    first = await video_handler.dispatch(command)
    duplicate = await video_handler.dispatch(command)

    assert duplicate.state == first.state
    assert duplicate.workflow == first.workflow
    assert duplicate.messages == first.messages
    assert duplicate.interrupt == first.interrupt


@pytest.mark.asyncio
async def test_video_handler_same_turn_opens_new_plan_review_occurrence(
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """同一原 Turn 完成一轮修订后，再次同原因审核必须使用新 ID。"""

    _, workflow = await state_repository.seed_state(_planning_state("plan_review"))
    first = await video_handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={"revision_feedback": "第一轮加快节奏"},
            workflow=workflow,
            idempotency_key="decision:review-response-1",
        )
    )
    await state_repository.seed_result(first)
    second = await video_handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={"revision_feedback": "第二轮强化卖点"},
            workflow=first.workflow,
            idempotency_key="decision:review-response-2",
        )
    )

    assert first.interrupt is not None
    assert second.interrupt is not None
    assert second.interrupt.reason_code == first.interrupt.reason_code
    assert second.interrupt.turn_id == first.interrupt.turn_id
    assert second.interrupt.interrupt_id != first.interrupt.interrupt_id


@pytest.mark.asyncio
async def test_video_handler_same_turn_opens_new_authorization_occurrence(
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """授权仍缺失且领域阶段版本未前进时，下一次提示也必须使用新 ID。"""

    _, workflow = await state_repository.seed_state(_planning_state("plan_review"))
    first = await video_handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=workflow,
            idempotency_key="decision:authorization-response-1",
        )
    )
    await state_repository.seed_result(first)
    second = await video_handler.dispatch(
        command_factory(
            action=AgentAction.CONTINUE_WORKFLOW,
            workflow=first.workflow,
            idempotency_key="decision:authorization-response-2",
        )
    )

    assert first.interrupt is not None
    assert second.interrupt is not None
    assert first.interrupt.reason_code == "authorization_required"
    assert second.interrupt.reason_code == first.interrupt.reason_code
    assert second.workflow.stage_version == first.workflow.stage_version
    assert second.state.workflow_version == first.state.workflow_version + 1
    assert second.interrupt.interrupt_id != first.interrupt.interrupt_id


@pytest.mark.asyncio
async def test_video_handler_same_action_identity_with_different_payload_has_new_digest(
    video_handler: VideoLiveWorkflowHandler,
    state_repository: _SeededMemoryVideoRuntimeRepository,
    command_factory,
) -> None:
    """同 action key 的不同业务摘要不可伪装成相同幂等结果。"""

    _, workflow = await state_repository.seed_state(_planning_state("plan_review"))
    first = await video_handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={"revision_feedback": "节奏更快"},
            workflow=workflow,
            idempotency_key="decision:conflicting-replay",
        )
    )
    conflicting = await video_handler.dispatch(
        command_factory(
            action=AgentAction.MODIFY_WORKFLOW,
            patch={"revision_feedback": "节奏更舒缓"},
            workflow=workflow,
            idempotency_key="decision:conflicting-replay",
        )
    )

    assert conflicting.state.last_action_key == first.state.last_action_key
    assert conflicting.state.payload_sha256 != first.state.payload_sha256


def _planning_state(
    stage: str,
    *,
    materials: list[dict[str, Any]] | None = None,
):
    from test_agent_video_workflow_generation import _with_concrete_asset_names

    service = VideoPlanningWorkflowService()
    state = service.start(
        workflow_id="wf-video-1",
        conversation_id="conv-1",
        intent="video",
        intake_context={
            "source_prompt": "生成一条智能戒指广告",
            "materials": [] if materials is None else materials,
        },
        now=NOW - timedelta(seconds=10),
    )
    if stage == "intake":
        return state
    if stage == "form_cancelled":
        return service.cancel_intake(state, now=NOW - timedelta(seconds=9))
    state = service.confirm_intake(
        state,
        validate_form("video", VIDEO_FORM),
        now=NOW - timedelta(seconds=9),
    )
    directions = draft_creative_directions("video", VIDEO_FORM)
    state = service.publish_directions(
        state,
        directions,
        now=NOW - timedelta(seconds=8),
    )
    if stage == "direction_review":
        return state
    state = service.select_direction(
        state,
        "direction_1",
        now=NOW - timedelta(seconds=7),
    )
    state = service.publish_initial_plan(
        state,
        _with_concrete_asset_names(
            build_plan_markdown("video", VIDEO_FORM, state.selected_direction)
        ),
        now=NOW - timedelta(seconds=6),
    )
    if stage == "plan_review":
        return state
    raise AssertionError(f"不支持的规划测试阶段：{stage}")


def _workflow_record(state):
    return project_video_workflow_state(state)
