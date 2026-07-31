from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

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


class InvalidProfileContextAssembler:
    async def assemble(self, request: object) -> ContextEnvelope:
        raise ValueError("模型缺少当前有效且已验证的 context_profile")


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
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id="conv-1",
        kind=WorkflowKind.VIDEO,
        status=status,
        current_stage="plan_review",
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
        visible_messages=(),
        workflows=workflows or (_workflow(),),
        active_workflow_id=active_workflow_id,
        explicit_action=explicit_action,
        expected_context_version=expected_context_version,
        authoritative_context_version=authoritative_context_version,
    )


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
    with pytest.raises(SupervisorDecisionUnavailableError) as exc_info:
        await _service(
            CountingDecisionModel(),
            context_assembler=InvalidProfileContextAssembler(),
        ).decide(_evidence())

    assert exc_info.value.reason_code == "model_profile_invalid"
    assert str(exc_info.value) == "model_profile_invalid"
