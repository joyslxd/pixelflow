from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.context import (
    ContextAssembler,
    ContextAssemblySnapshot,
)
from pixelflow.agent_runtime.contracts import (
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
from pixelflow.agent_runtime.supervisor import (
    DecisionValidationError,
    DecisionValidator,
    DeterministicTargetResolver,
    LLMActionClassifier,
    SupervisorDecisionService,
    SupervisorDecisionUnavailableError,
    SupervisorTurnEvidence,
)

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


class FixedDecisionModel:
    def __init__(
        self,
        *,
        action: AgentAction,
        requires_confirmation: bool = False,
    ) -> None:
        self.action = action
        self.requires_confirmation = requires_confirmation
        self.calls = 0

    async def ainvoke(
        self,
        messages: Sequence[tuple[str, str]],
    ) -> object:
        self.calls += 1
        targeted = self.action not in {
            AgentAction.ANSWER_ONLY,
            AgentAction.CLARIFY,
        }
        return {
            "action": self.action.value,
            "intent": (
                AgentIntent.VIDEO.value
                if targeted or self.action is AgentAction.CLARIFY
                else AgentIntent.GENERAL.value
            ),
            "target_workflow_id": "wf-1" if targeted else None,
            "target_stage": None,
            "target_artifact_ref": None,
            "confidence": 0.95,
            "requires_confirmation": self.requires_confirmation,
            "clarification_question": (
                "请确认是否继续处理当前视频。"
                if self.requires_confirmation
                else (
                    "请明确要处理哪个视频任务。"
                    if self.action is AgentAction.CLARIFY
                    else None
                )
            ),
            "patch": {},
            "reason_code": "fixed_classifier_decision",
            "idempotency_key": "decision:turn-1",
        }


class CountingDecisionModel:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(
        self,
        messages: Sequence[tuple[str, str]],
    ) -> object:
        self.calls += 1
        raise AssertionError("确定性决策不应调用分类模型")


class FailingDecisionModel:
    async def ainvoke(
        self,
        messages: Sequence[tuple[str, str]],
    ) -> object:
        raise RuntimeError("Authorization=secret-provider-value")


class FixedContextAssembler:
    async def assemble(self, request: object) -> ContextEnvelope:
        return ContextEnvelope(
            current_input=getattr(request, "current_input"),
            budget_report=ContextBudgetReport(
                estimated_input_tokens=1,
                effective_context_tokens=100,
                usable_input_tokens=80,
                max_output_tokens=10,
                safety_reserve_tokens=10,
                utilization=1 / 80,
            ),
        )


class FailingContextAssembler:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def assemble(self, request: object) -> ContextEnvelope:
        raise self._error


class MutatingContextAssembler:
    def __init__(self, evidence: SupervisorTurnEvidence) -> None:
        self._evidence = evidence
        self.request: object | None = None

    async def assemble(self, request: object) -> ContextEnvelope:
        self.request = request
        workflow = self._evidence.workflows[0]
        workflow.status = WorkflowStatus.CANCELLED
        workflow.current_stage = "tampered_stage"
        workflow.latest_artifact_refs[0] = "artifact:tampered"
        self._evidence.visible_messages[0]["artifact_ref"] = "artifact:tampered"
        self._evidence.materials[0]["metadata"]["source"] = "tampered"
        assert self._evidence.explicit_action is not None
        self._evidence.explicit_action.patch["approved"] = False
        object.__setattr__(
            self._evidence,
            "authoritative_context_version",
            99,
        )
        return await FixedContextAssembler().assemble(request)


class MutatingDecisionModel:
    def __init__(self, evidence: SupervisorTurnEvidence) -> None:
        self._evidence = evidence
        self.calls = 0

    async def ainvoke(
        self,
        messages: Sequence[tuple[str, str]],
    ) -> object:
        self.calls += 1
        workflow = self._evidence.workflows[0]
        workflow.status = WorkflowStatus.CANCELLED
        workflow.current_stage = "tampered_stage"
        workflow.latest_artifact_refs[0] = "artifact:tampered"
        return await FixedDecisionModel(
            action=AgentAction.CONTINUE_WORKFLOW,
        ).ainvoke(messages)


class FixedAnswerPort:
    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def answer(self, context: ContextEnvelope) -> str:
        return self._answer


class FailingAnswerPort:
    async def answer(self, context: ContextEnvelope) -> str:
        raise RuntimeError("provider answer failed")


def _workflow(
    workflow_id: str = "wf-1",
    *,
    status: WorkflowStatus = WorkflowStatus.AWAITING_USER,
    conversation_id: str = "conv-1",
    kind: WorkflowKind = WorkflowKind.VIDEO,
    current_stage: str = "plan_review",
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=kind,
        status=status,
        current_stage=current_stage,
        stage_version=2,
        latest_artifact_refs=[f"artifact:{workflow_id}:plan:v2"],
        context_version=5,
        created_at=NOW,
        updated_at=NOW,
    )


def _evidence(
    *,
    content: str = "继续生成视频",
    workflows: tuple[WorkflowRecord, ...] | None = None,
    active_workflow_id: str | None = "wf-1",
    explicit_action: ExplicitActionSignal | None = None,
    expected_context_version: int = 5,
    authoritative_context_version: int = 5,
    visible_messages: tuple[dict[str, object], ...] = (),
    materials: tuple[dict[str, object], ...] = (),
    reply_to_message_id: str | None = None,
    artifact_refs: tuple[str, ...] = (),
) -> SupervisorTurnEvidence:
    return SupervisorTurnEvidence(
        user_id="user-1",
        conversation_id="conv-1",
        turn=TurnRecord(
            turn_id="turn-1",
            conversation_id="conv-1",
            client_input_id=UUID("00000000-0000-0000-0000-000000000001"),
            status=TurnStatus.PROCESSING,
            expected_context_version=expected_context_version,
            created_at=NOW,
        ),
        content=content,
        visible_messages=visible_messages,
        workflows=workflows or (_workflow(),),
        active_workflow_id=active_workflow_id,
        materials=materials,
        reply_to_message_id=reply_to_message_id,
        artifact_refs=artifact_refs,
        explicit_action=explicit_action,
        expected_context_version=expected_context_version,
        authoritative_context_version=authoritative_context_version,
    )


def _unchecked_evidence(**updates: object) -> SupervisorTurnEvidence:
    valid = _evidence()
    payload = {
        field_name: getattr(valid, field_name)
        for field_name in SupervisorTurnEvidence.model_fields
    }
    payload.update(updates)
    return SupervisorTurnEvidence.model_construct(**payload)


def _service(
    model: object | None,
    *,
    answer_port: object | None = None,
    context_assembler: object | None = None,
) -> SupervisorDecisionService:
    return SupervisorDecisionService(
        resolver=DeterministicTargetResolver(),
        classifier=(LLMActionClassifier(model) if model is not None else None),
        validator=DecisionValidator(),
        context_assembler=context_assembler or FixedContextAssembler(),
        answer_port=answer_port,
    )


@pytest.mark.asyncio
async def test_explicit_action_bypasses_model_but_still_uses_validator() -> None:
    model = CountingDecisionModel()

    result = await _service(model).decide(
        _evidence(
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-1",
                stage="plan_review",
                patch={"approved": True},
            )
        )
    )

    assert result.decision.action is AgentAction.CONTINUE_WORKFLOW
    assert result.decision.patch == {"approved": True}
    assert result.decision.idempotency_key == "decision:turn-1"
    assert result.validation_request.current_context_version == 5
    assert model.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [AgentAction.CONTINUE_WORKFLOW, AgentAction.RETRY_FAILED],
)
async def test_completed_video_delivery_allows_explicit_delivery_actions(
    action: AgentAction,
) -> None:
    """仅视频 completed 交付态允许下载继续和失败草稿重试。"""

    result = await _service(None).decide(
        _evidence(
            workflows=(
                _workflow(
                    status=WorkflowStatus.COMPLETED,
                    current_stage="completed",
                ),
            ),
            explicit_action=ExplicitActionSignal(
                action=action,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-1",
                stage="completed",
            ),
        )
    )

    assert result.decision.action is action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow",
    [
        _workflow(
            status=WorkflowStatus.COMPLETED,
            kind=WorkflowKind.IMAGE,
            current_stage="completed",
        ),
        _workflow(
            status=WorkflowStatus.COMPLETED,
            current_stage="video_review",
        ),
    ],
)
async def test_delivery_action_exception_does_not_expand_other_completed_states(
    workflow: WorkflowRecord,
) -> None:
    """完成态例外不能扩大到其他 intent 或伪造的视频阶段。"""

    with pytest.raises(DecisionValidationError, match="action_not_allowed"):
        await _service(None).decide(
            _evidence(
                workflows=(workflow,),
                explicit_action=ExplicitActionSignal(
                    action=AgentAction.CONTINUE_WORKFLOW,
                    intent=AgentIntent(workflow.kind.value),
                    workflow_id="wf-1",
                    stage=workflow.current_stage,
                ),
            )
        )


@pytest.mark.asyncio
async def test_deterministic_resolution_bypasses_classifier() -> None:
    model = CountingDecisionModel()

    result = await _service(model).decide(_evidence())

    assert result.decision.action is AgentAction.CONTINUE_WORKFLOW
    assert result.decision.target_workflow_id == "wf-1"
    assert result.decision.confidence == 1.0
    assert model.calls == 0


@pytest.mark.asyncio
async def test_ambiguous_resolution_calls_classifier() -> None:
    model = FixedDecisionModel(action=AgentAction.CLARIFY)

    result = await _service(model).decide(
        _evidence(
            content="重新生成视频",
            workflows=(_workflow("wf-1"), _workflow("wf-2")),
            active_workflow_id=None,
        )
    )

    assert result.decision.action is AgentAction.CLARIFY
    assert model.calls == 1


@pytest.mark.asyncio
async def test_classifier_failure_returns_stable_clarification() -> None:
    result = await _service(FailingDecisionModel()).decide(
        _evidence(
            content="把那个再处理一下",
            workflows=(_workflow("wf-1"), _workflow("wf-2")),
            active_workflow_id=None,
        )
    )

    assert result.decision.action is AgentAction.CLARIFY
    assert (
        result.decision.reason_code
        == "classifier_unavailable_requires_clarification"
    )
    assert "provider" not in result.decision.clarification_question.lower()
    assert result.decision.idempotency_key == "decision:turn-1"


@pytest.mark.asyncio
async def test_stale_authoritative_context_version_is_rejected() -> None:
    with pytest.raises(DecisionValidationError) as exc_info:
        await _service(CountingDecisionModel()).decide(
            _evidence(
                explicit_action=ExplicitActionSignal(
                    action=AgentAction.CONTINUE_WORKFLOW,
                    intent=AgentIntent.VIDEO,
                    workflow_id="wf-1",
                ),
                expected_context_version=4,
                authoritative_context_version=5,
            )
        )

    assert exc_info.value.reason_code == "context_version_conflict"


@pytest.mark.asyncio
async def test_requires_confirmation_is_converted_to_clarify_before_dispatch() -> None:
    result = await _service(
        FixedDecisionModel(
            action=AgentAction.CONTINUE_WORKFLOW,
            requires_confirmation=True,
        )
    ).decide(_evidence(content="执行这一步"))

    assert result.decision.action is AgentAction.CLARIFY
    assert result.decision.reason_code == "decision_requires_confirmation"


@pytest.mark.asyncio
async def test_answer_only_builds_stable_tool_free_ai_message() -> None:
    result = await _service(
        FixedDecisionModel(action=AgentAction.ANSWER_ONLY),
        answer_port=FixedAnswerPort("当前视频方案仍在等待你确认。"),
    ).decide(_evidence(content="当前做到哪一步了？"))

    assert result.answer_message is not None
    assert result.answer_message.content == "当前视频方案仍在等待你确认。"
    assert result.answer_message.id == f"assistant:{result.decision.idempotency_key}"
    assert result.answer_message.tool_calls == []


@pytest.mark.asyncio
async def test_answer_failure_returns_stable_clarification() -> None:
    result = await _service(
        FixedDecisionModel(action=AgentAction.ANSWER_ONLY),
        answer_port=FailingAnswerPort(),
    ).decide(_evidence(content="当前做到哪一步了？"))

    assert result.answer_message is None
    assert result.decision.action is AgentAction.CLARIFY
    assert (
        result.decision.reason_code
        == "answer_model_unavailable_requires_clarification"
    )
    assert "provider" not in result.decision.clarification_question.lower()


@pytest.mark.asyncio
async def test_invalid_model_profile_raises_stable_unavailable_error() -> None:
    class OwnedSnapshotSource:
        async def load_context_snapshot(
            self,
            *,
            user_id: str,
            conversation_id: str,
        ) -> ContextAssemblySnapshot:
            return ContextAssemblySnapshot(
                user_id=user_id,
                conversation_id=conversation_id,
                context_version=5,
                active_workflow_id="wf-1",
                workflows=(_workflow(),),
            )

    with pytest.raises(SupervisorDecisionUnavailableError) as exc_info:
        await _service(
            CountingDecisionModel(),
            context_assembler=ContextAssembler(
                source=OwnedSnapshotSource(),
                model_name="deepseek-v4-pro",
                model_profiles={},
                budget_node="supervisor",
            ),
        ).decide(_evidence())

    assert exc_info.value.reason_code == "model_profile_invalid"
    assert str(exc_info.value) == "model_profile_invalid"


@pytest.mark.asyncio
async def test_regular_context_value_error_is_not_mislabeled_as_profile_error() -> None:
    error = ValueError("token estimator contract invalid")

    with pytest.raises(ValueError) as exc_info:
        await _service(
            CountingDecisionModel(),
            context_assembler=FailingContextAssembler(error),
        ).decide(_evidence())

    assert exc_info.value is error


@pytest.mark.asyncio
async def test_forged_profile_message_from_fake_assembler_is_not_mapped() -> None:
    error = ValueError(
        "模型 deepseek-v4-pro 缺少当前有效且已验证的 context_profile"
    )

    with pytest.raises(ValueError) as exc_info:
        await _service(
            CountingDecisionModel(),
            context_assembler=FailingContextAssembler(error),
        ).decide(_evidence())

    assert exc_info.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["\r", "\n", "\t", "\x00"])
async def test_real_strict_profile_error_is_mapped_by_type_for_any_model_name(
    control: str,
) -> None:
    class OwnedSnapshotSource:
        async def load_context_snapshot(
            self,
            *,
            user_id: str,
            conversation_id: str,
        ) -> ContextAssemblySnapshot:
            return ContextAssemblySnapshot(
                user_id=user_id,
                conversation_id=conversation_id,
                context_version=5,
                active_workflow_id="wf-1",
                workflows=(_workflow(),),
            )

    with pytest.raises(SupervisorDecisionUnavailableError) as exc_info:
        await _service(
            CountingDecisionModel(),
            context_assembler=ContextAssembler(
                source=OwnedSnapshotSource(),
                model_name=f"deepseek{control}v4-pro",
                model_profiles={},
                budget_node="supervisor",
            ),
        ).decide(_evidence())

    assert exc_info.value.reason_code == "model_profile_invalid"


@pytest.mark.asyncio
async def test_ordinary_value_error_with_real_profile_traceback_is_not_mapped() -> None:
    from pixelflow.agent_runtime.config import ContextBudgetConfig
    from pixelflow.agent_runtime.context import ContextBudgetPolicyProvider

    provider = ContextBudgetPolicyProvider(
        ContextBudgetConfig(require_verified_model_profile=True),
    )
    try:
        provider.resolve_model_profile(
            "missing-model",
            {},
            now=NOW,
        )
    except ValueError as source_error:
        error = ValueError(*source_error.args).with_traceback(
            source_error.__traceback__,
        )
    else:
        raise AssertionError("严格 Provider 必须拒绝缺失档案")

    with pytest.raises(ValueError) as exc_info:
        await _service(
            CountingDecisionModel(),
            context_assembler=FailingContextAssembler(error),
        ).decide(_evidence())

    assert exc_info.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"user_id": "user-foreign"},
        {"nested": {"conversation_id": "conv-foreign"}},
        {"items": [{"workflow_id": "wf-missing"}]},
        {"nested": {"message_id": "msg-missing"}},
        {"items": [{"artifact_ref": "artifact:missing"}]},
        {
            "nested": {
                "artifact_refs": [
                    "artifact:wf-1:plan:v2",
                    "artifact:missing",
                ]
            }
        },
        {"items": [{"ref": "artifact:missing"}]},
        {"artifact_refs": [{"ref": "artifact:wf-1:plan:v2"}]},
    ],
)
async def test_explicit_patch_foreign_reserved_reference_is_rejected_before_model(
    patch: dict[str, object],
) -> None:
    model = CountingDecisionModel()
    with pytest.raises(ValidationError):
        _evidence(
            visible_messages=(
                {
                    "message_id": "msg-1",
                    "user_id": "user-1",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-1",
                    "artifact_ref": "artifact:wf-1:plan:v2",
                },
            ),
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-1",
                patch=patch,
            ),
        )

    assert model.calls == 0


@pytest.mark.parametrize(
    "patch",
    [
        {
            "workflow_id": "wf-2",
            "artifact_ref": "artifact:wf-2:plan:v2",
        },
        {"nested": {"artifact_ref": "artifact:wf-2:plan:v2"}},
        {
            "workflow_id": "wf-1",
            "nested": {"ref": "artifact:wf-2:plan:v2"},
        },
        {"nested": {"message_id": "msg-2"}},
        {"items": [{"artifact_refs": ["artifact:wf-2:plan:v2"]}]},
    ],
)
def test_explicit_patch_reference_must_match_signal_workflow(
    patch: dict[str, object],
) -> None:
    model = CountingDecisionModel()

    with pytest.raises(ValidationError):
        _evidence(
            workflows=(_workflow(), _workflow("wf-2")),
            visible_messages=(
                {"message_id": "msg-1", "workflow_id": "wf-1"},
                {"message_id": "msg-2", "workflow_id": "wf-2"},
            ),
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-1",
                patch=patch,
            ),
        )

    assert model.calls == 0


def test_explicit_patch_allows_references_owned_by_signal_workflow() -> None:
    patch = {
        "workflow_id": "wf-1",
        "nested": {
            "message_id": "msg-1",
            "artifact_refs": ["artifact:wf-1:plan:v2"],
        },
    }

    evidence = _evidence(
        workflows=(_workflow(), _workflow("wf-2")),
        visible_messages=(
            {"message_id": "msg-1", "workflow_id": "wf-1"},
            {"message_id": "msg-2", "workflow_id": "wf-2"},
        ),
        explicit_action=ExplicitActionSignal(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.VIDEO,
            workflow_id="wf-1",
            patch=patch,
        ),
    )

    assert evidence.explicit_action is not None
    assert evidence.explicit_action.patch == patch


def test_explicit_patch_without_signal_target_rejects_multiple_workflow_owners() -> None:
    with pytest.raises(ValidationError):
        _evidence(
            workflows=(_workflow(), _workflow("wf-2")),
            explicit_action=ExplicitActionSignal(
                action=AgentAction.START_WORKFLOW,
                intent=AgentIntent.VIDEO,
                patch={
                    "items": [
                        {"artifact_ref": "artifact:wf-1:plan:v2"},
                        {"artifact_ref": "artifact:wf-2:plan:v2"},
                    ]
                },
            ),
        )


@pytest.mark.asyncio
async def test_switch_workflow_treats_signal_workflow_as_selected_target() -> None:
    patch = {
        "workflow_id": "wf-2",
        "nested": {"artifact_ref": "artifact:wf-2:plan:v2"},
    }

    result = await _service(CountingDecisionModel()).decide(
        _evidence(
            workflows=(_workflow(), _workflow("wf-2")),
            explicit_action=ExplicitActionSignal(
                action=AgentAction.SWITCH_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-2",
                patch=patch,
            ),
        )
    )

    assert result.decision.action is AgentAction.SWITCH_WORKFLOW
    assert result.decision.target_workflow_id == "wf-2"
    assert result.decision.patch == patch


@pytest.mark.asyncio
async def test_explicit_patch_allows_unknown_business_fields_after_scope_check() -> None:
    patch = {
        "approved": True,
        "selected_direction_id": "direction-1",
        "metadata": {
            "note": "user_id=foreign 只是普通文本",
            "items": ["workflow_id", "artifact:missing"],
        },
    }
    model = CountingDecisionModel()

    result = await _service(model).decide(
        _evidence(
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id="wf-1",
                patch=patch,
            )
        )
    )

    assert result.decision.patch == patch
    assert result.decision.patch is not patch
    assert model.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {
            "turn": TurnRecord(
                turn_id="turn-foreign",
                conversation_id="conv-foreign",
                client_input_id=UUID(
                    "00000000-0000-0000-0000-000000000002"
                ),
                status=TurnStatus.PROCESSING,
                expected_context_version=5,
                created_at=NOW,
            )
        },
        {"workflows": (_workflow(conversation_id="conv-foreign"),)},
        {"workflows": (_workflow(), _workflow())},
        {"active_workflow_id": "wf-missing"},
        {
            "visible_messages": (
                {
                    "message_id": "msg-1",
                    "user_id": "user-foreign",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-1",
                },
            )
        },
        {
            "visible_messages": (
                {
                    "message_id": "msg-1",
                    "user_id": "user-1",
                    "conversation_id": "conv-foreign",
                    "workflow_id": "wf-1",
                },
            )
        },
        {
            "visible_messages": (
                {
                    "message_id": "msg-1",
                    "workflow_id": "wf-missing",
                },
            )
        },
        {
            "visible_messages": (
                {"message_id": "msg-1", "workflow_id": "wf-1"},
                {"message_id": "msg-1", "workflow_id": "wf-1"},
            )
        },
        {
            "visible_messages": (
                {"message_id": "msg-1", "workflow_id": "wf-1"},
            ),
            "reply_to_message_id": "msg-missing",
        },
        {"artifact_refs": ("artifact:missing",)},
        {"artifact_refs": ("artifact:wf-1:plan:v2",) * 2},
        {
            "turn": TurnRecord(
                turn_id="turn-target-missing",
                conversation_id="conv-1",
                client_input_id=UUID(
                    "00000000-0000-0000-0000-000000000003"
                ),
                status=TurnStatus.PROCESSING,
                target_workflow_id="wf-missing",
                expected_context_version=5,
                created_at=NOW,
            )
        },
        {
            "materials": (
                {
                    "user_id": "user-foreign",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-1",
                },
            )
        },
        {
            "materials": (
                {
                    "user_id": "user-1",
                    "conversation_id": "conv-foreign",
                    "workflow_id": "wf-1",
                },
            )
        },
        {
            "materials": (
                {
                    "user_id": "user-1",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-missing",
                },
            )
        },
        {
            "materials": (
                {
                    "user_id": "user-1",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-1",
                    "message_id": "msg-missing",
                },
            )
        },
        {
            "materials": (
                {
                    "user_id": "user-1",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-1",
                    "artifact_ref": "artifact:missing",
                },
            )
        },
        {
            "materials": (
                {
                    "user_id": "user-1",
                    "conversation_id": "conv-1",
                    "workflow_id": "wf-1",
                    "ref": "artifact:missing",
                },
            )
        },
        {
            "explicit_action": ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                workflow_id="wf-missing",
            )
        },
        {
            "explicit_action": ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                workflow_id="wf-1",
                artifact_ref="artifact:missing",
            )
        },
    ],
)
async def test_invalid_evidence_is_rejected_before_classifier(
    updates: dict[str, object],
) -> None:
    model = CountingDecisionModel()

    with pytest.raises(ValidationError):
        await _service(model).decide(_unchecked_evidence(**updates))

    assert model.calls == 0


@pytest.mark.asyncio
async def test_real_context_assembler_rejects_cross_user_owner_before_model() -> None:
    class ForeignOwnerSource:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def load_context_snapshot(
            self,
            *,
            user_id: str,
            conversation_id: str,
        ) -> ContextAssemblySnapshot:
            self.calls.append((user_id, conversation_id))
            return ContextAssemblySnapshot(
                user_id="user-foreign",
                conversation_id=conversation_id,
                context_version=5,
            )

    source = ForeignOwnerSource()
    assembler = ContextAssembler(
        source=source,
        model_name="deepseek-v4-pro",
        model_profiles={},
        budget_node="supervisor",
    )
    model = CountingDecisionModel()

    with pytest.raises(KeyError):
        await _service(
            model,
            context_assembler=assembler,
        ).decide(_evidence())

    assert source.calls == [("user-1", "conv-1")]
    assert model.calls == 0


@pytest.mark.asyncio
async def test_context_await_mutation_cannot_change_authoritative_snapshot() -> None:
    evidence = _evidence(
        visible_messages=(
            {
                "message_id": "msg-1",
                "user_id": "user-1",
                "conversation_id": "conv-1",
                "workflow_id": "wf-1",
                "stage": "plan_review",
                "artifact_ref": "artifact:wf-1:plan:v2",
            },
        ),
        materials=(
            {
                "user_id": "user-1",
                "conversation_id": "conv-1",
                "workflow_id": "wf-1",
                "artifact_ref": "artifact:wf-1:plan:v2",
                "metadata": {"source": "original"},
            },
        ),
        artifact_refs=("artifact:wf-1:plan:v2",),
        explicit_action=ExplicitActionSignal(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.VIDEO,
            workflow_id="wf-1",
            stage="plan_review",
            artifact_ref="artifact:wf-1:plan:v2",
            patch={"approved": True},
        ),
    )
    assembler = MutatingContextAssembler(evidence)

    result = await _service(
        CountingDecisionModel(),
        context_assembler=assembler,
    ).decide(evidence)

    assert evidence.workflows[0].status is WorkflowStatus.CANCELLED
    assert evidence.explicit_action.patch == {"approved": False}
    assert evidence.materials[0]["metadata"] == {"source": "tampered"}
    assert result.decision.patch == {"approved": True}
    assert result.validation_request.current_context_version == 5
    snapshot = result.validation_request.classification_request.candidates[0]
    current = result.validation_request.current_candidates[0]
    assert snapshot.status is WorkflowStatus.AWAITING_USER
    assert current.status is WorkflowStatus.AWAITING_USER
    assert snapshot.current_stage == "plan_review"
    assert current.current_stage == "plan_review"
    assert snapshot.targets[0].target_artifact_ref == "artifact:wf-1:plan:v2"
    assert current.targets[0].target_artifact_ref == "artifact:wf-1:plan:v2"
    assert getattr(assembler.request, "user_id") == "user-1"
    assert getattr(assembler.request, "conversation_id") == "conv-1"
    assert getattr(assembler.request, "artifact_refs") == [
        "artifact:wf-1:plan:v2"
    ]


@pytest.mark.asyncio
async def test_classifier_await_mutation_cannot_change_current_candidates() -> None:
    evidence = _evidence(content="执行这一步")
    model = MutatingDecisionModel(evidence)

    result = await _service(model).decide(evidence)

    assert evidence.workflows[0].status is WorkflowStatus.CANCELLED
    classification_candidate = (
        result.validation_request.classification_request.candidates[0]
    )
    current_candidate = result.validation_request.current_candidates[0]
    assert classification_candidate is not current_candidate
    assert classification_candidate.status is WorkflowStatus.AWAITING_USER
    assert current_candidate.status is WorkflowStatus.AWAITING_USER
    assert classification_candidate.current_stage == "plan_review"
    assert current_candidate.current_stage == "plan_review"
    assert result.decision.action is AgentAction.CONTINUE_WORKFLOW
    assert model.calls == 1
