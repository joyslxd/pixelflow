"""验证统一 Agent Runtime 图装配、注册 ID 与生命周期边界。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import interrupt

from app.gateway.pixelflow_agent_runtime import (
    make_pixelflow_agent_graph_runtime,
)
from pixelflow import make_pixelflow_graph
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
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    AGENT_RUNTIME_GRAPH_ID,
    FakeWorkflowRegistry,
    WorkflowCommand,
    make_agent_runtime_graph,
    resume_graph_from_interrupt,
    supervisor_namespace,
)
from pixelflow.agent_runtime.identity import interrupt_id
from pixelflow.agent_runtime.persistence import (
    MemoryVideoRuntimeRepository,
    StoredAgentInterrupt,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    DecisionValidationRequest,
    DecisionValidator,
    DeterministicResolution,
    DeterministicResolutionStatus,
    DeterministicTargetResolver,
    SupervisorDecisionService,
    SupervisorTurnEvidence,
)
from pixelflow.agent_runtime.supervisor.routing import (
    SupervisorRoutingError,
    _clarification_resume_command,
)
from pixelflow.agent_workflows.video import (
    VideoLiveWorkflowHandler,
    VideoPlanningWorkflowService,
    encode_video_workflow_state,
)
from pixelflow.agent_workflows.video.live_handler import WorkflowDispatchResult
from pixelflow.intake.forms import draft_creative_directions, validate_form
from pixelflow.tasks.store import MemoryPixelFlowTaskStore

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 28, tzinfo=UTC)
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
        "aspect_ratios": ["9:16"],
        "sizes": ["1080p"],
        "sound_options": ["on"],
        "durations_sec": [5, 10, 15],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1"],
        "sizes": ["1080p"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


def _workflow(
    *,
    conversation_id: str,
    current_stage: str = "plan_review",
    stage_version: int = 1,
) -> WorkflowRecord:
    """构造图装配测试所需的最小 Workflow 投影。"""

    return WorkflowRecord(
        workflow_id="wf-image",
        conversation_id=conversation_id,
        kind=WorkflowKind.IMAGE,
        status=WorkflowStatus.AWAITING_USER,
        current_stage=current_stage,
        stage_version=stage_version,
        creation_contract_snapshot={},
        pending_external_job=None,
        latest_artifact_refs=[],
        context_version=stage_version,
        created_at=NOW,
        updated_at=NOW,
    )


def _decision(conversation_id: str) -> ActionDecision:
    """构造一条显式定位既有 Workflow 的继续命令。"""

    return ActionDecision(
        action=AgentAction.CONTINUE_WORKFLOW,
        intent=AgentIntent.IMAGE,
        target_workflow_id="wf-image",
        target_stage="plan_review",
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="explicit_target",
        idempotency_key=f"decision:turn-{conversation_id}",
    )


def _state(conversation_id: str, *, current_input: str = "继续") -> dict:
    """生成可直接交给 Supervisor 图的测试状态。"""

    workflow = _workflow(conversation_id=conversation_id)
    decision = _decision(conversation_id)
    candidate = ActionClassificationCandidate(
        workflow_id=workflow.workflow_id,
        intent=AgentIntent.IMAGE,
        status=workflow.status,
        current_stage=workflow.current_stage,
        stage_version=workflow.stage_version,
        context_version=workflow.context_version,
        allowed_actions=(AgentAction.CONTINUE_WORKFLOW,),
        targets=(
            ActionClassificationTarget(
                target_stage=workflow.current_stage,
            ),
        ),
    )
    classification_request = ActionClassificationRequest(
        turn_id=f"turn-{conversation_id}",
        content=current_input,
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.IMAGE,
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            reason_code="explicit_target",
            candidate_workflow_ids=(workflow.workflow_id,),
        ),
        candidates=(candidate,),
    )
    return {
        "conversation_id": conversation_id,
        "user_id": f"user-{conversation_id}",
        "turn_id": f"turn-{conversation_id}",
        "run_id": f"run-{conversation_id}",
        "current_input": current_input,
        "context_version": 1,
        "workflows": {workflow.workflow_id: workflow},
        "active_workflow_id": workflow.workflow_id,
        "decision": decision,
        "decision_validation_request": DecisionValidationRequest(
            decision=decision,
            classification_request=classification_request,
            current_candidates=(candidate,),
            expected_context_version=1,
            current_context_version=1,
        ),
    }


class _InterruptingHandler:
    """用真实 LangGraph interrupt 模拟 Workflow 人工确认。"""

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        response = interrupt(
            {
                "type": "plan_review",
                "workflow_id": command.workflow_id,
            }
        )
        assert response == {"approved": True}
        assert command.workflow is not None
        return command.workflow.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "current_stage": "generation",
                "stage_version": command.workflow.stage_version + 1,
                "context_version": command.workflow.context_version + 1,
            },
            deep=True,
        )


class _ImmediateHandler:
    """立即返回新投影，用于验证多会话 checkpoint 隔离。"""

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        assert command.workflow is not None
        return command.workflow.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "current_stage": "generation",
                "stage_version": command.workflow.stage_version + 1,
                "context_version": command.workflow.context_version + 1,
            },
            deep=True,
        )


class _ImmediateStartHandler:
    """记录全局追问恢复后仍使用原 Turn 派发的新 Workflow。"""

    def __init__(self) -> None:
        self.turn_ids: list[str] = []

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        self.turn_ids.append(command.turn_id)
        return WorkflowRecord(
            workflow_id=command.workflow_id,
            conversation_id=command.conversation_id,
            kind=WorkflowKind.VIDEO,
            status=WorkflowStatus.RUNNING,
            current_stage="intake",
            stage_version=1,
            creation_contract_snapshot={},
            pending_external_job=None,
            latest_artifact_refs=[],
            context_version=1,
            created_at=NOW,
            updated_at=NOW,
        )


class _LiveInterruptingHandler:
    """用完整 live 结果打开中断，并在原 Turn 恢复后推进规划。"""

    def __init__(self, turn_ids: list[str]) -> None:
        self._turn_ids = turn_ids

    async def dispatch(self, command: WorkflowCommand) -> WorkflowDispatchResult:
        self._turn_ids.append(command.turn_id)
        planning = VideoPlanningWorkflowService()
        state = planning.start(
            workflow_id=command.workflow_id,
            conversation_id=command.conversation_id,
            intent="video",
            intake_context={"source_prompt": command.current_input},
            now=NOW,
        )
        if command.decision.action is AgentAction.START_WORKFLOW:
            workflow = planning.to_workflow_record(state)
            opened = StoredAgentInterrupt(
                interrupt_id=interrupt_id(command.turn_id, "video_intake_required"),
                conversation_id=command.conversation_id,
                workflow_id=command.workflow_id,
                turn_id=command.turn_id,
                kind="video_intake_form",
                reason_code="video_intake_required",
                payload={"workflow_id": command.workflow_id, "stage": "intake"},
                opened_at=NOW,
                user_id=command.user_id,
                thread_id=command.namespace.thread_id,
                checkpoint_ns="root",
            )
            return WorkflowDispatchResult(
                state=encode_video_workflow_state(
                    user_id=command.user_id,
                    state=state,
                    workflow_version=1,
                    last_turn_id=command.turn_id,
                    last_action_key=command.decision.idempotency_key,
                ),
                workflow=workflow,
                interrupt=opened,
                turn_status=TurnStatus.WAITING_USER,
                update_active_workflow=True,
                active_workflow_id=command.workflow_id,
            )
        assert command.decision.action is AgentAction.CONTINUE_WORKFLOW
        state = planning.confirm_intake(
            state,
            validate_form("video", VIDEO_FORM),
            now=NOW,
        )
        state = planning.publish_directions(
            state,
            draft_creative_directions("video", VIDEO_FORM),
            now=NOW,
        )
        workflow = planning.to_workflow_record(state)
        return WorkflowDispatchResult(
            state=encode_video_workflow_state(
                user_id=command.user_id,
                state=state,
                workflow_version=2,
                last_turn_id=command.turn_id,
                last_action_key=command.decision.idempotency_key,
            ),
            workflow=workflow,
            turn_status=TurnStatus.COMPLETED,
        )


class _FixedStartContextAssembler:
    """为真实 DecisionService 提供无外部依赖的已验证上下文。"""

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


class _NoCredentialProvider:
    """首轮不会读取凭据；若越界读取则返回明确缺失。"""

    def get(self, turn_id: str):
        del turn_id
        return None


class _FixedClock:
    """让真实 Handler 的首轮状态和 interrupt 可稳定断言。"""

    def now(self) -> datetime:
        return NOW


def _registry(handler: object) -> FakeWorkflowRegistry:
    """把单个测试处理器注册到图片 Workflow。"""

    return FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})


def _live_start_state(conversation_id: str) -> dict:
    """构造由路由器预分配视频 Workflow ID 的首轮状态。"""

    turn_id = f"turn-{conversation_id}"
    decision = ActionDecision(
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
        target_workflow_id=None,
        target_stage=None,
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="explicit_start",
        idempotency_key=f"decision:{turn_id}",
    )
    classification = ActionClassificationRequest(
        turn_id=turn_id,
        content="生成一条智能戒指广告",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=None,
            target_stage=None,
            reason_code="explicit_start",
            candidate_workflow_ids=(),
        ),
        candidates=(),
    )
    return {
        "conversation_id": conversation_id,
        "user_id": f"user-{conversation_id}",
        "turn_id": turn_id,
        "run_id": f"run-{conversation_id}",
        "current_input": "生成一条智能戒指广告",
        "materials": [],
        "artifact_refs": [],
        "context_version": 1,
        "workflows": {},
        "decision": decision,
        "decision_validation_request": DecisionValidationRequest(
            decision=decision,
            classification_request=classification,
            current_candidates=(),
            allowed_global_actions=(AgentAction.START_WORKFLOW,),
            expected_context_version=1,
            current_context_version=1,
        ),
    }


async def _decision_service_live_start_state(conversation_id: str) -> dict:
    """经真实 DecisionService 生成首轮决策与 Validator 快照。"""

    turn = TurnRecord(
        turn_id=f"turn-{conversation_id}",
        conversation_id=conversation_id,
        client_input_id=UUID("20000000-0000-4000-8000-000000000007"),
        status=TurnStatus.PROCESSING,
        expected_context_version=0,
        created_at=NOW,
    )
    content = "生成一条智能戒指广告"
    decision_result = await SupervisorDecisionService(
        resolver=DeterministicTargetResolver(),
        classifier=None,
        validator=DecisionValidator(),
        context_assembler=_FixedStartContextAssembler(),
    ).decide(
        SupervisorTurnEvidence(
            user_id="user-live-real",
            conversation_id=conversation_id,
            turn=turn,
            content=content,
            visible_messages=(),
            workflows=(),
            active_workflow_id=None,
            explicit_action=ExplicitActionSignal(
                action=AgentAction.START_WORKFLOW,
                intent=AgentIntent.VIDEO,
            ),
            expected_context_version=0,
            authoritative_context_version=0,
        )
    )
    return {
        "conversation_id": conversation_id,
        "user_id": "user-live-real",
        "turn_id": turn.turn_id,
        "run_id": f"run-{conversation_id}",
        "current_input": content,
        "materials": [],
        "artifact_refs": [],
        "context_version": 0,
        "workflows": {},
        "decision": decision_result.decision,
        "decision_validation_request": decision_result.validation_request,
    }


def _live_resume_value(*, stored: StoredAgentInterrupt) -> dict:
    """模拟服务端基于已保存响应与权威证据构造的 Graph 恢复值。"""

    response_id = UUID("10000000-0000-4000-8000-000000000007")
    decision = ActionDecision(
        action=AgentAction.CONTINUE_WORKFLOW,
        intent=AgentIntent.VIDEO,
        target_workflow_id=stored.workflow_id,
        target_stage="intake",
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=None,
        patch={"form_values": VIDEO_FORM},
        reason_code="explicit_interrupt_response",
        idempotency_key=f"decision:{response_id}",
    )
    return {
        "client_response_id": str(response_id),
        "interrupt_id": stored.interrupt_id,
        "workflow_id": stored.workflow_id,
        "stage": "intake",
        "decision": decision.model_dump(mode="json"),
        "value": {
            "content": "确认需求并继续",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "explicit_action": {
                "action": "continue_workflow",
                "intent": "video",
                "workflow_id": stored.workflow_id,
                "stage": "intake",
                "artifact_ref": None,
                "patch": {"form_values": VIDEO_FORM},
            },
        },
    }


def _global_clarification_state(conversation_id: str) -> dict:
    """构造没有 active Workflow 的全局追问状态。"""

    turn_id = f"turn-{conversation_id}"
    content = "帮我做一个"
    decision = ActionDecision(
        action=AgentAction.CLARIFY,
        intent=AgentIntent.GENERAL,
        confidence=1,
        requires_confirmation=True,
        clarification_question="请明确要创建什么内容。",
        reason_code="ambiguous_target",
        idempotency_key=f"decision:{turn_id}",
    )
    classification = ActionClassificationRequest(
        turn_id=turn_id,
        content=content,
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.CLARIFY,
            intent=AgentIntent.GENERAL,
            reason_code="ambiguous_target",
            candidate_workflow_ids=(),
        ),
        candidates=(),
    )
    return {
        "conversation_id": conversation_id,
        "user_id": f"user-{conversation_id}",
        "turn_id": turn_id,
        "run_id": f"run-{conversation_id}",
        "current_input": content,
        "materials": [],
        "artifact_refs": [],
        "context_version": 0,
        "workflows": {},
        "active_workflow_id": None,
        "decision": decision,
        "decision_validation_request": DecisionValidationRequest(
            decision=decision,
            classification_request=classification,
            current_candidates=(),
            allowed_global_actions=(AgentAction.CLARIFY, AgentAction.START_WORKFLOW),
            expected_context_version=0,
            current_context_version=0,
        ),
    }


def _global_clarification_resume_value(state: dict) -> dict:
    """模拟 Executor 使用持久响应和权威证据构造的严格内部信封。"""

    turn_id = state["turn_id"]
    content = "创建一条商品介绍视频"
    decision = ActionDecision(
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
        confidence=1,
        reason_code="explicit_start",
        idempotency_key=f"decision:{turn_id}",
    )
    classification = ActionClassificationRequest(
        turn_id=turn_id,
        content=content,
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            reason_code="explicit_start",
            candidate_workflow_ids=(),
        ),
        candidates=(),
    )
    response_id = "40000000-0000-4000-8000-000000000001"
    return {
        "client_response_id": response_id,
        "interrupt_id": interrupt_id(turn_id, "ambiguous_target"),
        "resume_context_version": 1,
        "source_decision_idempotency_key": f"decision:{turn_id}",
        "decision": decision.model_dump(mode="json"),
        "decision_validation_request": DecisionValidationRequest(
            decision=decision,
            classification_request=classification,
            current_candidates=(),
            allowed_global_actions=(AgentAction.CLARIFY, AgentAction.START_WORKFLOW),
            expected_context_version=1,
            current_context_version=1,
        ).model_dump(mode="json"),
        "value": {
            "content": content,
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "explicit_action": None,
        },
        "answer_message": None,
    }


@pytest.mark.asyncio
async def test_global_clarification_resume_revalidates_and_keeps_original_turn() -> None:
    """严格恢复信封必须回到 route_action，且不能用响应 ID 冒充原 Turn。"""

    conversation_id = "conv-global-clarification"
    state = _global_clarification_state(conversation_id)
    handler = _ImmediateStartHandler()
    graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
        checkpointer=InMemorySaver(),
    )
    namespace = supervisor_namespace(conversation_id)

    await graph.ainvoke(state, namespace.as_runnable_config())
    interrupted = await graph.aget_state(namespace.as_runnable_config())
    assert interrupted.interrupts[0].value["type"] == "clarification"
    result = await resume_graph_from_interrupt(
        graph,
        namespace,
        interrupt_id=interrupted.interrupts[0].id,
        response=_global_clarification_resume_value(state),
    )

    assert result["turn_id"] == state["turn_id"]
    assert result["current_input"] == "创建一条商品介绍视频"
    assert result["last_interrupt_response_id"] == (
        "40000000-0000-4000-8000-000000000001"
    )
    assert result["decision"].idempotency_key == (
        "decision:40000000-0000-4000-8000-000000000001"
    )
    assert result["context_version"] == 1
    assert handler.turn_ids == [state["turn_id"]]


@pytest.mark.parametrize("invalid_version", [True, -1])
def test_global_clarification_resume_rejects_invalid_snapshot_identity(
    invalid_version: object,
) -> None:
    """内部恢复证据不得把 bool 或负数伪装成上下文版本。"""

    state = _global_clarification_state("conv-invalid-resume-version")
    response = _global_clarification_resume_value(state)
    response["resume_context_version"] = invalid_version

    with pytest.raises(SupervisorRoutingError) as captured:
        _clarification_resume_command(
            state,
            source_decision=state["decision"],
            response=response,
        )

    assert captured.value.reason_code == (
        "invalid_clarification_resume_context_version"
    )


def test_global_clarification_resume_rejects_context_version_rollback() -> None:
    """恢复快照不得回退已经进入 Graph checkpoint 的版本。"""

    state = _global_clarification_state("conv-resume-version-rollback")
    state["context_version"] = 2
    response = _global_clarification_resume_value(state)

    with pytest.raises(SupervisorRoutingError) as captured:
        _clarification_resume_command(
            state,
            source_decision=state["decision"],
            response=response,
        )

    assert captured.value.reason_code == "clarification_resume_context_rollback"


def test_global_clarification_resume_binds_validation_context_versions() -> None:
    """新 DecisionValidationRequest 的双版本必须绑定响应前快照。"""

    state = _global_clarification_state("conv-resume-version-conflict")
    response = _global_clarification_resume_value(state)
    validation = dict(response["decision_validation_request"])
    validation["expected_context_version"] = 2
    validation["current_context_version"] = 2
    response["decision_validation_request"] = validation

    with pytest.raises(SupervisorRoutingError) as captured:
        _clarification_resume_command(
            state,
            source_decision=state["decision"],
            response=response,
        )

    assert captured.value.reason_code == "clarification_resume_context_conflict"


@pytest.mark.asyncio
async def test_live_graph_resumes_original_memory_interrupt_after_rebuild() -> None:
    """live 结果先入 checkpoint，再由独立中断节点恢复原 Turn。"""

    checkpointer = InMemorySaver()
    namespace = supervisor_namespace("conv-live-memory")
    turn_ids: list[str] = []
    first_graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry(
            {WorkflowKind.VIDEO: _LiveInterruptingHandler(turn_ids)}
        ),
        checkpointer=checkpointer,
    )

    await first_graph.ainvoke(
        _live_start_state("conv-live-memory"),
        namespace.as_runnable_config(),
    )
    interrupted = await first_graph.aget_state(namespace.as_runnable_config())
    original_graph_interrupt_id = interrupted.interrupts[0].id
    stored = StoredAgentInterrupt.model_validate(
        interrupted.values["workflow_dispatch_result"]["interrupt"]
    )
    original_turn_id = interrupted.values["turn_id"]

    restarted_graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry(
            {WorkflowKind.VIDEO: _LiveInterruptingHandler(turn_ids)}
        ),
        checkpointer=checkpointer,
    )
    result = await resume_graph_from_interrupt(
        restarted_graph,
        namespace,
        interrupt_id=original_graph_interrupt_id,
        response=_live_resume_value(stored=stored),
    )

    workflow_id = stored.workflow_id
    assert workflow_id is not None
    assert result["workflows"][workflow_id].current_stage == "direction_review"
    assert result["turn_id"] == original_turn_id
    assert len(set(turn_ids)) == 1
    assert result["last_interrupt_response_id"] == (
        "10000000-0000-4000-8000-000000000007"
    )


@pytest.mark.asyncio
async def test_real_decision_router_dispatcher_and_video_handler_start_workflow() -> None:
    """真实首轮合同由 Router 预分配 ID，并进入真实视频 Handler。"""

    conversation_id = "conv-live-real-start"
    repository = MemoryVideoRuntimeRepository(
        task_store=MemoryPixelFlowTaskStore()
    )
    handler = VideoLiveWorkflowHandler(
        repository=repository,
        capabilities=object(),
        credential_provider=_NoCredentialProvider(),
        clock=_FixedClock(),
    )
    graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
        checkpointer=InMemorySaver(),
    )
    namespace = supervisor_namespace(conversation_id)

    await graph.ainvoke(
        await _decision_service_live_start_state(conversation_id),
        namespace.as_runnable_config(),
    )
    interrupted = await graph.aget_state(namespace.as_runnable_config())
    stored = StoredAgentInterrupt.model_validate(
        interrupted.values["workflow_dispatch_result"]["interrupt"]
    )
    routed_decision = ActionDecision.model_validate(
        interrupted.values["decision"]
    )

    assert routed_decision.target_workflow_id is None
    assert stored.workflow_id.startswith("wf_")
    assert interrupted.values["workflows"][stored.workflow_id].current_stage == (
        "intake"
    )
    assert stored.kind == "video_intake_form"


@pytest.mark.asyncio
async def test_live_graph_resumes_original_sqlite_interrupt_after_reopen(
    tmp_path: Path,
) -> None:
    """SQLite 重开后 live 中断身份与原 Turn 仍保持不变。"""

    database_path = tmp_path / "m13-live-composition.db"
    namespace = supervisor_namespace("conv-live-sqlite")
    turn_ids: list[str] = []
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        await saver.setup()
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry(
                {WorkflowKind.VIDEO: _LiveInterruptingHandler(turn_ids)}
            ),
            checkpointer=saver,
        )
        await graph.ainvoke(
            _live_start_state("conv-live-sqlite"),
            namespace.as_runnable_config(),
        )
        interrupted = await graph.aget_state(namespace.as_runnable_config())
        original_graph_interrupt_id = interrupted.interrupts[0].id
        stored = StoredAgentInterrupt.model_validate(
            interrupted.values["workflow_dispatch_result"]["interrupt"]
        )
        original_turn_id = interrupted.values["turn_id"]

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        await saver.setup()
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry(
                {WorkflowKind.VIDEO: _LiveInterruptingHandler(turn_ids)}
            ),
            checkpointer=saver,
        )
        result = await resume_graph_from_interrupt(
            graph,
            namespace,
            interrupt_id=original_graph_interrupt_id,
            response=_live_resume_value(stored=stored),
        )

    assert result["turn_id"] == original_turn_id
    assert len(set(turn_ids)) == 1


@pytest.mark.asyncio
async def test_composed_graph_resumes_original_memory_interrupt_after_rebuild() -> None:
    """图对象重建后仍通过共享 Memory checkpointer 恢复原中断。"""

    checkpointer = InMemorySaver()
    namespace = supervisor_namespace("conv-memory")
    first_graph = make_agent_runtime_graph(
        registry=_registry(_InterruptingHandler()),
        checkpointer=checkpointer,
    )

    await first_graph.ainvoke(_state("conv-memory"), namespace.as_runnable_config())
    interrupted = await first_graph.aget_state(namespace.as_runnable_config())
    original_interrupt_id = interrupted.interrupts[0].id

    restarted_graph = make_agent_runtime_graph(
        registry=_registry(_InterruptingHandler()),
        checkpointer=checkpointer,
    )
    result = await resume_graph_from_interrupt(
        restarted_graph,
        namespace,
        interrupt_id=original_interrupt_id,
        response={"approved": True},
    )

    assert result["workflows"]["wf-image"].current_stage == "generation"
    assert result["workflows"]["wf-image"].status == WorkflowStatus.RUNNING
    completed = await restarted_graph.aget_state(namespace.as_runnable_config())
    assert completed.interrupts == ()
    assert completed.next == ()


@pytest.mark.asyncio
async def test_composed_graph_resumes_original_sqlite_interrupt_after_reopen(
    tmp_path: Path,
) -> None:
    """关闭并重开 SQLite 后仍从同一 Supervisor 中断继续。"""

    database_path = tmp_path / "m02-composition.db"
    namespace = supervisor_namespace("conv-sqlite")

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as first_checkpointer:
        await first_checkpointer.setup()
        first_graph = make_agent_runtime_graph(
            registry=_registry(_InterruptingHandler()),
            checkpointer=first_checkpointer,
        )
        await first_graph.ainvoke(
            _state("conv-sqlite"),
            namespace.as_runnable_config(),
        )
        interrupted = await first_graph.aget_state(namespace.as_runnable_config())
        original_interrupt_id = interrupted.interrupts[0].id

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as restarted_checkpointer:
        await restarted_checkpointer.setup()
        restarted_graph = make_agent_runtime_graph(
            registry=_registry(_InterruptingHandler()),
            checkpointer=restarted_checkpointer,
        )
        result = await resume_graph_from_interrupt(
            restarted_graph,
            namespace,
            interrupt_id=original_interrupt_id,
            response={"approved": True},
        )

    assert result["workflows"]["wf-image"].current_stage == "generation"
    assert result["workflows"]["wf-image"].stage_version == 2


@pytest.mark.asyncio
async def test_composed_graph_keeps_same_workflow_id_isolated_by_conversation() -> None:
    """不同 conversation 的同名 Workflow 不共享 Supervisor 状态。"""

    checkpointer = InMemorySaver()
    graph = make_agent_runtime_graph(
        registry=_registry(_ImmediateHandler()),
        checkpointer=checkpointer,
    )
    first_namespace = supervisor_namespace("conv-first")
    second_namespace = supervisor_namespace("conv-second")

    await graph.ainvoke(
        _state("conv-first", current_input="第一条"),
        first_namespace.as_runnable_config(),
    )
    await graph.ainvoke(
        _state("conv-second", current_input="第二条"),
        second_namespace.as_runnable_config(),
    )

    first = await graph.aget_state(first_namespace.as_runnable_config())
    second = await graph.aget_state(second_namespace.as_runnable_config())
    assert first.values["conversation_id"] == "conv-first"
    assert first.values["current_input"] == "第一条"
    assert first.values["workflows"]["wf-image"].conversation_id == "conv-first"
    assert second.values["conversation_id"] == "conv-second"
    assert second.values["current_input"] == "第二条"
    assert second.values["workflows"]["wf-image"].conversation_id == "conv-second"


@pytest.mark.asyncio
async def test_gateway_graph_runtime_reuses_shared_checkpointer_and_cleans_state() -> None:
    """Gateway 只借用共享 checkpointer，并在退出时移除图运行时引用。"""

    app = FastAPI()
    checkpointer = InMemorySaver()

    async with make_pixelflow_agent_graph_runtime(
        app,
        checkpointer=checkpointer,
    ) as runtime:
        assert app.state.pixelflow_agent_graph_runtime is runtime
        assert runtime.graph_id == AGENT_RUNTIME_GRAPH_ID
        assert runtime.graph.checkpointer is checkpointer
        assert runtime.closed is False

    assert runtime.closed is True
    assert not hasattr(app.state, "pixelflow_agent_graph_runtime")
    assert [item async for item in checkpointer.alist({"configurable": {"thread_id": "still-open"}})] == []


def test_langgraph_registry_adds_new_graph_without_replacing_old_graph() -> None:
    """工具注册表新增独立图 ID，同时保留旧 PixelFlow 图入口。"""

    config = json.loads((BACKEND_ROOT / "langgraph.json").read_text(encoding="utf-8"))

    assert AGENT_RUNTIME_GRAPH_ID == "pixelflow_agent_runtime"
    assert config["graphs"]["pixelflow"] == "pixelflow:make_pixelflow_graph"
    assert config["graphs"]["lead_agent"] == "deerflow.agents:make_lead_agent"
    assert config["graphs"][AGENT_RUNTIME_GRAPH_ID] == ("pixelflow.agent_runtime.graph:make_agent_runtime_graph")
    assert make_pixelflow_graph().get_graph().nodes
