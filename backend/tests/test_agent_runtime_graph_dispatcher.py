from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    WorkflowCommand,
    WorkflowCommandDispatcher,
)
from pixelflow.agent_workflows.video import (
    VideoPlanningWorkflowService,
    encode_video_workflow_state,
)
from pixelflow.agent_workflows.video.live_handler import WorkflowDispatchResult

NOW = datetime(2026, 7, 27, tzinfo=UTC)
NON_WORKFLOW_ACTIONS = {
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
}
EXISTING_WORKFLOW_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
    AgentAction.SWITCH_WORKFLOW,
    AgentAction.CANCEL_WORKFLOW,
}


def _workflow(
    workflow_id: str,
    *,
    conversation_id: str = "conv-1",
    kind: WorkflowKind = WorkflowKind.IMAGE,
    current_stage: str = "intake",
    stage_version: int = 1,
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=kind,
        status=WorkflowStatus.RUNNING,
        current_stage=current_stage,
        stage_version=stage_version,
        creation_contract_snapshot={},
        pending_external_job=None,
        latest_artifact_refs=[],
        context_version=stage_version,
        created_at=NOW,
        updated_at=NOW,
    )


def _decision(
    *,
    action: AgentAction = AgentAction.CONTINUE_WORKFLOW,
    intent: AgentIntent = AgentIntent.IMAGE,
    target_workflow_id: str | None = "wf-image",
    clarification_question: str | None = None,
) -> ActionDecision:
    return ActionDecision(
        action=action,
        intent=intent,
        target_workflow_id=target_workflow_id,
        target_stage=None,
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=clarification_question,
        patch={},
        reason_code="explicit_target",
        idempotency_key="turn-1:dispatch",
    )


class _RecordingHandler:
    """记录收到的命令，并返回预先配置的工作流投影。"""

    def __init__(self, result: WorkflowRecord) -> None:
        self.result = result
        self.commands: list[WorkflowCommand] = []

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        self.commands.append(command)
        return self.result


class _LiveRecordingHandler:
    """返回完整 live 结果，验证 dispatcher 不会丢失业务状态。"""

    def __init__(self, result: WorkflowDispatchResult) -> None:
        self.result = result
        self.commands: list[WorkflowCommand] = []

    async def dispatch(self, command: WorkflowCommand) -> WorkflowDispatchResult:
        self.commands.append(command)
        return self.result


class _MutatingHandler(_RecordingHandler):
    """主动修改命令副本，用于验证原始输入不会被反向污染。"""

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        self.commands.append(command)
        assert command.workflow is not None
        command.workflow.creation_contract_snapshot["style"]["colors"].append(
            "blue"
        )
        command.workflow.latest_artifact_refs.append("artifact://handler")
        command.decision.patch["style"]["colors"].append("blue")
        return self.result


class _RaisingHandler:
    """记录调用后抛出固定异常，冻结 dispatcher 的异常边界。"""

    def __init__(self, error: RuntimeError) -> None:
        self.error = error
        self.commands: list[WorkflowCommand] = []

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        self.commands.append(command)
        assert command.workflow is not None
        command.workflow.latest_artifact_refs.append("artifact://failed-handler")
        command.decision.patch["attempts"].append(2)
        raise self.error


@pytest.mark.asyncio
async def test_dispatcher_targets_only_requested_workflow_and_uses_isolated_namespace() -> None:
    first = _workflow("wf-image-a")
    second = _workflow("wf-image-b")
    updated_second = _workflow(
        "wf-image-b",
        current_stage="planning",
        stage_version=2,
    )
    handler = _RecordingHandler(updated_second)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )
    state = {
        "conversation_id": "conv-1",
        "workflows": {
            first.workflow_id: first,
            second.workflow_id: second,
        },
        "active_workflow_id": first.workflow_id,
    }

    result = await dispatcher.dispatch(
        state,
        _decision(target_workflow_id=second.workflow_id),
    )

    assert result == updated_second
    assert len(handler.commands) == 1
    command = handler.commands[0]
    assert command.workflow_id == second.workflow_id
    assert command.workflow == second
    assert command.workflow is not second
    assert command.namespace.thread_id == (
        "pf:conversation:conv-1:workflow:wf-image-b:v1"
    )
    assert command.namespace.checkpoint_ns == ""
    assert state["workflows"][first.workflow_id] is first
    assert state["workflows"][second.workflow_id] is second


@pytest.mark.asyncio
async def test_dispatch_result_wraps_legacy_handler_without_changing_dispatch_contract() -> None:
    """旧 Handler 继续只返回 Workflow，新增入口提供无中断兼容外壳。"""

    workflow = _workflow("wf-image")
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: _RecordingHandler(workflow)})
    )
    state = {
        "conversation_id": "conv-1",
        "workflows": {workflow.workflow_id: workflow},
    }

    wrapped = await dispatcher.dispatch_result(state, _decision())
    direct = await dispatcher.dispatch(state, _decision())

    assert wrapped.workflow == workflow
    assert wrapped.state is None
    assert wrapped.interrupt is None
    assert wrapped.turn_status is TurnStatus.COMPLETED
    assert direct == workflow


@pytest.mark.asyncio
async def test_dispatch_result_preserves_live_handler_state_and_interrupt_contract() -> None:
    """live Handler 结果经过 dispatcher 后仍保留 codec 状态。"""

    planning = VideoPlanningWorkflowService()
    state = planning.start(
        workflow_id="wf-new-video",
        conversation_id="conv-1",
        intent="video",
        intake_context={"source_prompt": "生成视频"},
        now=NOW,
    )
    workflow = planning.to_workflow_record(state)
    live_result = WorkflowDispatchResult(
        state=encode_video_workflow_state(
            user_id="user-conv-1",
            state=state,
            workflow_version=1,
            last_turn_id="turn-conv-1",
            last_action_key="turn-1:dispatch",
        ),
        workflow=workflow,
        turn_status=TurnStatus.COMPLETED,
        update_active_workflow=True,
        active_workflow_id=workflow.workflow_id,
    )
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry(
            {WorkflowKind.VIDEO: _LiveRecordingHandler(live_result)}
        )
    )

    result = await dispatcher.dispatch_result(
        {
            "conversation_id": "conv-1",
            "user_id": "user-conv-1",
            "turn_id": "turn-conv-1",
            "current_input": "生成视频",
            "workflows": {},
        },
        _decision(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id="wf-new-video",
        ),
    )

    assert result == live_result
    assert result.state.payload_sha256 == live_result.state.payload_sha256


@pytest.mark.asyncio
async def test_dispatcher_isolates_nested_input_and_handler_result_references() -> None:
    existing = _workflow("wf-image").model_copy(
        update={
            "creation_contract_snapshot": {
                "style": {"colors": ["red"]},
            },
            "latest_artifact_refs": ["artifact://original"],
        }
    )
    handler_result = _workflow(
        "wf-image",
        current_stage="planning",
        stage_version=2,
    ).model_copy(
        update={
            "creation_contract_snapshot": {
                "style": {"colors": ["green"]},
            },
            "latest_artifact_refs": ["artifact://result"],
        }
    )
    handler = _MutatingHandler(handler_result)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )
    decision = _decision().model_copy(
        update={"patch": {"style": {"colors": ["red"]}}}
    )

    result = await dispatcher.dispatch(
        {
            "conversation_id": "conv-1",
            "workflows": {existing.workflow_id: existing},
        },
        decision,
    )

    assert existing.creation_contract_snapshot == {
        "style": {"colors": ["red"]},
    }
    assert existing.latest_artifact_refs == ["artifact://original"]
    assert decision.patch == {"style": {"colors": ["red"]}}

    handler_result.creation_contract_snapshot["style"]["colors"].append(
        "purple"
    )
    handler_result.latest_artifact_refs.append("artifact://later")

    assert result.creation_contract_snapshot == {
        "style": {"colors": ["green"]},
    }
    assert result.latest_artifact_refs == ["artifact://result"]


@pytest.mark.asyncio
async def test_dispatcher_routes_multiple_workflow_kinds_to_distinct_handlers() -> None:
    image = _workflow("wf-image")
    video = _workflow("wf-video", kind=WorkflowKind.VIDEO)
    image_handler = _RecordingHandler(image)
    video_handler = _RecordingHandler(video)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry(
            {
                WorkflowKind.IMAGE: image_handler,
                WorkflowKind.VIDEO: video_handler,
            }
        )
    )
    state = {
        "conversation_id": "conv-1",
        "user_id": "user-conv-1",
        "turn_id": "turn-conv-1",
        "current_input": "继续视频流程",
        "workflows": {
            image.workflow_id: image,
            video.workflow_id: video,
        },
    }

    await dispatcher.dispatch(
        state,
        _decision(
            intent=AgentIntent.VIDEO,
            target_workflow_id=video.workflow_id,
        ),
    )

    assert image_handler.commands == []
    assert [item.workflow_id for item in video_handler.commands] == ["wf-video"]
    assert video_handler.commands[0].source_interrupt_id is None


@pytest.mark.asyncio
async def test_dispatcher_passes_exact_source_interrupt_id_to_video_handler() -> None:
    """Dispatcher 必须把已校验的来源中断 ID 原样交给视频 Handler。"""

    video = _workflow("wf-video", kind=WorkflowKind.VIDEO)
    handler = _RecordingHandler(video)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.VIDEO: handler})
    )

    await dispatcher.dispatch(
        {
            "conversation_id": "conv-1",
            "user_id": "user-conv-1",
            "turn_id": "turn-conv-1",
            "current_input": "恢复视频流程",
            "source_interrupt_id": "interrupt-exact-source-1",
            "workflows": {video.workflow_id: video},
        },
        _decision(
            intent=AgentIntent.VIDEO,
            target_workflow_id=video.workflow_id,
        ),
    )

    assert handler.commands[0].source_interrupt_id == (
        "interrupt-exact-source-1"
    )


@pytest.mark.asyncio
async def test_dispatcher_rejects_workflow_from_another_conversation() -> None:
    foreign = _workflow("wf-image", conversation_id="conv-2")
    handler = _RecordingHandler(foreign)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )

    with pytest.raises(ValueError, match="conversation_id"):
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "workflows": {foreign.workflow_id: foreign},
            },
            _decision(),
        )

    assert handler.commands == []


@pytest.mark.asyncio
async def test_dispatcher_rejects_unknown_target_without_guessing_active_workflow() -> None:
    active = _workflow("wf-active")
    handler = _RecordingHandler(active)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )

    with pytest.raises(KeyError, match="wf-missing"):
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "workflows": {active.workflow_id: active},
                "active_workflow_id": active.workflow_id,
            },
            _decision(target_workflow_id="wf-missing"),
        )

    assert handler.commands == []


@pytest.mark.asyncio
async def test_dispatcher_requires_explicit_target_for_business_action() -> None:
    handler = _RecordingHandler(_workflow("wf-image"))
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )

    with pytest.raises(ValueError, match="target_workflow_id"):
        await dispatcher.dispatch(
            {"conversation_id": "conv-1", "workflows": {}},
            _decision(target_workflow_id=None),
        )

    assert handler.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    sorted(NON_WORKFLOW_ACTIONS),
)
async def test_dispatcher_rejects_non_workflow_actions(action: AgentAction) -> None:
    handler = _RecordingHandler(_workflow("wf-image"))
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )
    decision = _decision(
        action=action,
        target_workflow_id=None,
        clarification_question=(
            "请说明要继续哪个任务？"
            if action == AgentAction.CLARIFY
            else None
        ),
    )

    with pytest.raises(ValueError, match="业务命令"):
        await dispatcher.dispatch(
            {"conversation_id": "conv-1", "workflows": {}},
            decision,
        )

    assert handler.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    sorted(EXISTING_WORKFLOW_ACTIONS),
)
async def test_dispatcher_accepts_each_existing_workflow_action(
    action: AgentAction,
) -> None:
    existing = _workflow("wf-image")
    handler = _RecordingHandler(existing)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )

    await dispatcher.dispatch(
        {
            "conversation_id": "conv-1",
            "workflows": {existing.workflow_id: existing},
        },
        _decision(action=action),
    )

    assert [item.decision.action for item in handler.commands] == [action]


def test_action_categories_cover_the_complete_frozen_contract() -> None:
    assert (
        NON_WORKFLOW_ACTIONS
        | EXISTING_WORKFLOW_ACTIONS
        | {AgentAction.START_WORKFLOW}
    ) == set(AgentAction)


@pytest.mark.asyncio
async def test_dispatcher_rejects_unknown_future_action_fail_closed() -> None:
    existing = _workflow("wf-image")
    handler = _RecordingHandler(existing)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )
    future_decision = ActionDecision.model_construct(
        action="future_workflow_action",
        intent=AgentIntent.IMAGE,
        target_workflow_id=existing.workflow_id,
        target_stage=None,
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="future_contract",
        idempotency_key="turn-1:future",
    )

    with pytest.raises(ValueError, match="未支持"):
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "workflows": {existing.workflow_id: existing},
            },
            future_decision,
        )

    assert handler.commands == []


@pytest.mark.asyncio
async def test_start_workflow_uses_intent_registry_without_existing_projection() -> None:
    created = _workflow("wf-new-video", kind=WorkflowKind.VIDEO)
    handler = _RecordingHandler(created)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.VIDEO: handler})
    )

    result = await dispatcher.dispatch(
        {
            "conversation_id": "conv-1",
            "user_id": "user-conv-1",
            "turn_id": "turn-conv-1",
            "current_input": "新建视频流程",
            "workflows": {},
        },
        _decision(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id="wf-new-video",
        ),
    )

    assert result == created
    assert handler.commands[0].workflow is None
    assert handler.commands[0].kind == WorkflowKind.VIDEO
    assert handler.commands[0].workflow_id == "wf-new-video"


@pytest.mark.asyncio
async def test_start_workflow_rejects_existing_target_projection() -> None:
    existing = _workflow("wf-image")
    handler = _RecordingHandler(existing)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )

    with pytest.raises(ValueError, match="已存在"):
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "workflows": {existing.workflow_id: existing},
            },
            _decision(action=AgentAction.START_WORKFLOW),
        )

    assert handler.commands == []


@pytest.mark.asyncio
async def test_start_workflow_rejects_general_intent() -> None:
    dispatcher = WorkflowCommandDispatcher(FakeWorkflowRegistry({}))

    with pytest.raises(ValueError, match="intent"):
        await dispatcher.dispatch(
            {"conversation_id": "conv-1", "workflows": {}},
            _decision(
                action=AgentAction.START_WORKFLOW,
                intent=AgentIntent.GENERAL,
                target_workflow_id="wf-new",
            ),
        )


@pytest.mark.asyncio
async def test_dispatcher_fails_closed_when_handler_is_not_registered() -> None:
    video = _workflow("wf-video", kind=WorkflowKind.VIDEO)
    dispatcher = WorkflowCommandDispatcher(FakeWorkflowRegistry({}))

    with pytest.raises(LookupError, match="video"):
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "user_id": "user-conv-1",
                "turn_id": "turn-conv-1",
                "current_input": "继续视频流程",
                "workflows": {video.workflow_id: video},
            },
            _decision(
                intent=AgentIntent.VIDEO,
                target_workflow_id=video.workflow_id,
            ),
        )


@pytest.mark.asyncio
async def test_dispatcher_propagates_handler_error_without_fallback_or_pollution() -> None:
    existing = _workflow("wf-image").model_copy(
        update={"latest_artifact_refs": ["artifact://original"]}
    )
    sentinel = RuntimeError("sentinel workflow failure")
    failing_handler = _RaisingHandler(sentinel)
    fallback_handler = _RecordingHandler(
        _workflow("wf-video", kind=WorkflowKind.VIDEO)
    )
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry(
            {
                WorkflowKind.IMAGE: failing_handler,
                WorkflowKind.VIDEO: fallback_handler,
            }
        )
    )
    decision = _decision().model_copy(
        update={"patch": {"attempts": [1]}}
    )

    with pytest.raises(RuntimeError) as error:
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "workflows": {existing.workflow_id: existing},
            },
            decision,
        )

    assert error.value is sentinel
    assert len(failing_handler.commands) == 1
    assert fallback_handler.commands == []
    assert existing.latest_artifact_refs == ["artifact://original"]
    assert decision.patch == {"attempts": [1]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_result",
    [
        _workflow("wf-other"),
        _workflow("wf-image", conversation_id="conv-2"),
        _workflow("wf-image", kind=WorkflowKind.VIDEO),
    ],
)
async def test_dispatcher_rejects_handler_result_identity_changes(
    invalid_result: WorkflowRecord,
) -> None:
    existing = _workflow("wf-image")
    handler = _RecordingHandler(invalid_result)
    dispatcher = WorkflowCommandDispatcher(
        FakeWorkflowRegistry({WorkflowKind.IMAGE: handler})
    )

    with pytest.raises(ValueError, match="返回"):
        await dispatcher.dispatch(
            {
                "conversation_id": "conv-1",
                "workflows": {existing.workflow_id: existing},
            },
            _decision(),
        )
