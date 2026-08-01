"""验证 Supervisor 决策在图内安全分流且不绕过 Validator。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    WorkflowCommand,
    make_agent_runtime_graph,
    resume_graph_from_interrupt,
    supervisor_namespace,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    DecisionValidationError,
    DecisionValidationRequest,
    DeterministicResolution,
    DeterministicResolutionStatus,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _workflow(
    *,
    workflow_id: str = "wf-video",
    conversation_id: str = "conv-route",
) -> WorkflowRecord:
    """构造路由前不得被非业务动作改写的 Workflow。"""

    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=WorkflowKind.VIDEO,
        status=WorkflowStatus.AWAITING_USER,
        current_stage="plan_review",
        stage_version=2,
        creation_contract_snapshot={"duration_seconds": 30},
        pending_external_job=None,
        latest_artifact_refs=["artifact:plan:v2"],
        context_version=5,
        created_at=NOW,
        updated_at=NOW,
    )


def _candidate() -> ActionClassificationCandidate:
    """返回与 Workflow 权威投影一致的分类候选。"""

    return ActionClassificationCandidate(
        workflow_id="wf-video",
        intent=AgentIntent.VIDEO,
        status=WorkflowStatus.AWAITING_USER,
        current_stage="plan_review",
        stage_version=2,
        context_version=5,
        allowed_actions=(
            AgentAction.ANSWER_ONLY,
            AgentAction.CONTINUE_WORKFLOW,
            AgentAction.MODIFY_WORKFLOW,
        ),
        targets=(
            ActionClassificationTarget(
                target_stage="plan_review",
                target_artifact_ref="artifact:plan:v2",
            ),
        ),
    )


def _decision(
    action: AgentAction,
    *,
    confidence: float = 0.93,
) -> ActionDecision:
    """按动作生成与同一 Turn 幂等键绑定的决策。"""

    is_workflow_action = action in {
        AgentAction.CONTINUE_WORKFLOW,
        AgentAction.MODIFY_WORKFLOW,
    }
    return ActionDecision(
        action=action,
        intent=(AgentIntent.VIDEO if is_workflow_action or action == AgentAction.START_WORKFLOW else AgentIntent.GENERAL),
        target_workflow_id=("wf-video" if is_workflow_action else None),
        target_stage=("plan_review" if is_workflow_action else None),
        target_artifact_ref=("artifact:plan:v2" if is_workflow_action else None),
        confidence=confidence,
        requires_confirmation=False,
        clarification_question=("你希望继续处理哪个任务？" if action == AgentAction.CLARIFY else None),
        patch=({"style": "科技感"} if action == AgentAction.MODIFY_WORKFLOW else {}),
        reason_code=f"route_{action.value}",
        idempotency_key="decision:turn-route",
    )


def _validation_request(
    decision: ActionDecision,
    *,
    current_context_version: int = 5,
) -> DecisionValidationRequest:
    """生成图节点必须重新校验的冻结分类快照和当前状态。"""

    if decision.target_workflow_id is not None:
        candidate = _candidate()
        resolution = DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=decision.action,
            intent=decision.intent,
            target_workflow_id=decision.target_workflow_id,
            target_stage=decision.target_stage,
            target_artifact_ref=decision.target_artifact_ref,
            reason_code=f"route_{decision.action.value}",
            candidate_workflow_ids=(decision.target_workflow_id,),
        )
        candidates = (candidate,)
    else:
        resolution = DeterministicResolution(
            status=DeterministicResolutionStatus.PARTIAL,
            action=decision.action,
            intent=decision.intent,
            reason_code=f"route_{decision.action.value}",
        )
        candidates = ()
    classification_request = ActionClassificationRequest(
        turn_id="turn-route",
        content="请处理这条输入",
        deterministic_resolution=resolution,
        candidates=candidates,
    )
    return DecisionValidationRequest(
        decision=decision,
        classification_request=classification_request,
        current_candidates=candidates,
        allowed_global_actions=(
            AgentAction.ANSWER_ONLY,
            AgentAction.CLARIFY,
            AgentAction.START_WORKFLOW,
        ),
        expected_context_version=5,
        current_context_version=current_context_version,
    )


def _state(
    decision: ActionDecision,
    *,
    validation_request: DecisionValidationRequest | None = None,
    answer_message: AIMessage | None = None,
) -> dict:
    """构造同一会话的图输入，并显式携带 Validator 请求。"""

    workflow = _workflow()
    state = {
        "conversation_id": "conv-route",
        "user_id": "user-route",
        "turn_id": "turn-route",
        "run_id": "run-route",
        "current_input": "请处理这条输入",
        "context_version": 5,
        "messages": [],
        "workflows": {workflow.workflow_id: workflow},
        "active_workflow_id": workflow.workflow_id,
        "decision": decision,
        "decision_validation_request": (validation_request or _validation_request(decision)),
    }
    if answer_message is not None:
        state["answer_message"] = answer_message
    return state


class _RecordingHandler:
    """记录业务命令，便于证明非业务分支没有进入 Workflow。"""

    def __init__(self) -> None:
        self.commands: list[WorkflowCommand] = []

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        self.commands.append(command)
        if command.workflow is None:
            return WorkflowRecord(
                workflow_id=command.workflow_id,
                conversation_id=command.conversation_id,
                kind=command.kind,
                status=WorkflowStatus.DRAFT,
                current_stage="intake",
                stage_version=1,
                creation_contract_snapshot={},
                pending_external_job=None,
                latest_artifact_refs=[],
                context_version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        return command.workflow.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "current_stage": "generation",
                "stage_version": command.workflow.stage_version + 1,
                "context_version": command.workflow.context_version + 1,
            },
            deep=True,
        )


def _graph(
    handler: _RecordingHandler,
    *,
    checkpointer: InMemorySaver | None = None,
):
    """装配仅注册视频 Workflow 的真实 Supervisor 图。"""

    return make_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
        checkpointer=checkpointer,
    )


@pytest.mark.asyncio
async def test_answer_only_saves_answer_without_changing_workflow_state() -> None:
    """answer_only 只追加回答消息，不推进 Workflow 或上下文版本。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.ANSWER_ONLY)
    before = _workflow().model_dump(mode="python")
    result = await _graph(handler).ainvoke(
        _state(
            decision,
            answer_message=AIMessage(
                id="assistant:decision:turn-route",
                content="因为当前模型支持这一步所需的画幅和时长。",
            ),
        )
    )

    assert handler.commands == []
    assert result["workflows"]["wf-video"].model_dump(mode="python") == before
    assert result["active_workflow_id"] == "wf-video"
    assert result["context_version"] == 5
    assert result["messages"] == [
        AIMessage(
            id="assistant:decision:turn-route",
            content="因为当前模型支持这一步所需的画幅和时长。",
        )
    ]


@pytest.mark.asyncio
async def test_answer_only_rejects_message_id_from_another_turn() -> None:
    """回答消息不能借用旧消息 ID 覆盖既有会话内容。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.ANSWER_ONLY)

    with pytest.raises(ValueError) as exc_info:
        await _graph(handler).ainvoke(
            _state(
                decision,
                answer_message=AIMessage(
                    id="assistant:decision:other-turn",
                    content="这条回答不能覆盖旧消息。",
                ),
            )
        )

    assert getattr(exc_info.value, "reason_code", None) == ("answer_message_id_conflict")
    assert handler.commands == []


@pytest.mark.asyncio
async def test_clarify_opens_resumable_interrupt_without_dispatching_workflow() -> None:
    """clarify 打开追问，旧任意恢复形状必须失败关闭且不修改 Workflow。"""

    handler = _RecordingHandler()
    checkpointer = InMemorySaver()
    graph = _graph(handler, checkpointer=checkpointer)
    namespace = supervisor_namespace("conv-route")
    before = _workflow().model_dump(mode="python")

    await graph.ainvoke(
        _state(_decision(AgentAction.CLARIFY)),
        namespace.as_runnable_config(),
    )
    interrupted = await graph.aget_state(namespace.as_runnable_config())

    assert handler.commands == []
    assert len(interrupted.interrupts) == 1
    assert interrupted.interrupts[0].value == {
        "type": "clarification",
        "question": "你希望继续处理哪个任务？",
        "reason_code": "route_clarify",
        "idempotency_key": "decision:turn-route",
    }
    assert interrupted.values["workflows"]["wf-video"].model_dump(mode="python") == before

    with pytest.raises(ValueError) as exc_info:
        await resume_graph_from_interrupt(
            graph,
            namespace,
            interrupt_id=interrupted.interrupts[0].id,
            response={"answer": "继续视频任务"},
        )

    assert handler.commands == []
    assert getattr(exc_info.value, "reason_code", None) == (
        "invalid_clarification_resume_envelope"
    )


@pytest.mark.asyncio
async def test_validator_downgrade_to_clarify_cannot_enter_workflow() -> None:
    """低置信度业务动作必须先降级追问，不能按原动作派发。"""

    handler = _RecordingHandler()
    checkpointer = InMemorySaver()
    graph = _graph(handler, checkpointer=checkpointer)
    namespace = supervisor_namespace("conv-route")
    decision = _decision(
        AgentAction.MODIFY_WORKFLOW,
        confidence=0.4,
    )

    await graph.ainvoke(
        _state(decision),
        namespace.as_runnable_config(),
    )
    interrupted = await graph.aget_state(namespace.as_runnable_config())

    assert handler.commands == []
    assert interrupted.values["decision"].action == AgentAction.CLARIFY
    assert interrupted.interrupts[0].value["reason_code"] == ("low_confidence_requires_clarification")


@pytest.mark.asyncio
async def test_invalid_decision_never_reaches_workflow_dispatcher() -> None:
    """版本冲突在图路由节点 fail-closed，业务处理器调用次数保持为零。"""

    handler = _RecordingHandler()
    checkpointer = InMemorySaver()
    graph = _graph(handler, checkpointer=checkpointer)
    namespace = supervisor_namespace("conv-route")
    decision = _decision(AgentAction.MODIFY_WORKFLOW)
    request = _validation_request(
        decision,
        current_context_version=6,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        await graph.ainvoke(
            _state(decision, validation_request=request),
            namespace.as_runnable_config(),
        )

    assert exc_info.value.reason_code == "context_version_conflict"
    assert handler.commands == []
    snapshot = await graph.aget_state(namespace.as_runnable_config())
    assert snapshot.values["workflows"]["wf-video"] == _workflow()


@pytest.mark.asyncio
async def test_state_decision_must_match_validation_request_snapshot() -> None:
    """拒绝用一条安全校验请求为另一条业务决策背书。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.MODIFY_WORKFLOW)
    unrelated = _decision(AgentAction.CONTINUE_WORKFLOW)

    with pytest.raises(DecisionValidationError) as exc_info:
        await _graph(handler).ainvoke(
            _state(
                decision,
                validation_request=_validation_request(unrelated),
            )
        )

    assert exc_info.value.reason_code == "decision_snapshot_conflict"
    assert handler.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_key", "invalid_value", "expected_reason_code"),
    [
        (
            "decision",
            {"action": "机密用户原文-非法动作"},
            "invalid_decision",
        ),
        (
            "decision_validation_request",
            {"机密字段": "机密用户原文-非法请求"},
            "invalid_validation_request",
        ),
    ],
)
async def test_malformed_route_state_only_exposes_safe_reason_code(
    state_key: str,
    invalid_value: object,
    expected_reason_code: str,
) -> None:
    """Pydantic 解析失败不得把恶意输入值带入公开异常。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.MODIFY_WORKFLOW)
    state = _state(decision)
    state[state_key] = invalid_value

    with pytest.raises(ValueError) as exc_info:
        await _graph(handler).ainvoke(state)

    assert getattr(exc_info.value, "reason_code", None) == (expected_reason_code)
    assert "机密用户原文" not in str(exc_info.value)
    assert handler.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_change", "expected_reason_code"),
    [
        (
            {"turn_id": "turn-other"},
            "turn_id_conflict",
        ),
        (
            {"context_version": 6},
            "context_version_state_conflict",
        ),
        (
            {"current_input": "完全不同且未分类的输入"},
            "current_input_conflict",
        ),
    ],
)
async def test_validation_request_must_belong_to_current_graph_state(
    state_change: dict[str, object],
    expected_reason_code: str,
) -> None:
    """分类 Turn 与版本必须绑定当前图状态，不能由调用方自证权威。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.MODIFY_WORKFLOW)
    state = _state(decision)
    state.update(state_change)

    with pytest.raises(DecisionValidationError) as exc_info:
        await _graph(handler).ainvoke(state)

    assert exc_info.value.reason_code == expected_reason_code
    assert handler.commands == []


@pytest.mark.asyncio
async def test_validated_candidate_must_match_graph_workflow_projection() -> None:
    """图内 Workflow 已前进时拒绝使用旧候选快照派发命令。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.MODIFY_WORKFLOW)
    state = _state(decision)
    state["workflows"] = {
        "wf-video": _workflow().model_copy(
            update={
                "current_stage": "generation",
                "stage_version": 3,
                "context_version": 6,
            },
            deep=True,
        )
    }

    with pytest.raises(DecisionValidationError) as exc_info:
        await _graph(handler).ainvoke(state)

    assert exc_info.value.reason_code == "workflow_projection_conflict"
    assert handler.commands == []


@pytest.mark.asyncio
async def test_valid_business_command_is_dispatched_after_validation() -> None:
    """合法业务命令经 Validator 后只派发到唯一目标 Workflow。"""

    handler = _RecordingHandler()
    decision = _decision(AgentAction.MODIFY_WORKFLOW)

    result = await _graph(handler).ainvoke(_state(decision))

    assert len(handler.commands) == 1
    assert handler.commands[0].decision == decision
    assert handler.commands[0].workflow_id == "wf-video"
    assert result["workflows"]["wf-video"].current_stage == "generation"
    assert result["workflows"]["wf-video"].context_version == 6


@pytest.mark.asyncio
async def test_start_workflow_gets_stable_preallocated_id_after_validation() -> None:
    """新建动作通过校验后才派生稳定 ID，重复路由不会得到随机目标。"""

    first_handler = _RecordingHandler()
    second_handler = _RecordingHandler()
    decision = _decision(AgentAction.START_WORKFLOW)

    first_result = await _graph(first_handler).ainvoke(_state(decision))
    second_result = await _graph(second_handler).ainvoke(_state(decision))

    first_command = first_handler.commands[0]
    second_command = second_handler.commands[0]
    assert first_command.workflow is None
    assert first_command.workflow_id == second_command.workflow_id
    assert first_command.workflow_id.startswith("wf_")
    assert first_command.decision == decision
    assert first_command.decision.target_workflow_id is None
    assert first_command.workflow_id in first_result["workflows"]
    assert first_result["workflows"] == second_result["workflows"]
