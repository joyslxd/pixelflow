import json
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    WorkflowStatus,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    DecisionClassificationError,
    DeterministicResolution,
    DeterministicResolutionStatus,
    LLMActionClassifier,
)


class FakeDecisionModel:
    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[tuple[str, str], ...]] = []

    async def ainvoke(
        self,
        messages: Sequence[tuple[str, str]],
    ) -> object:
        self.calls.append(tuple(messages))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _candidate() -> ActionClassificationCandidate:
    return ActionClassificationCandidate(
        workflow_id="wf-video",
        intent=AgentIntent.VIDEO,
        status=WorkflowStatus.AWAITING_USER,
        current_stage="plan_review",
        stage_version=2,
        context_version=5,
        allowed_actions=(
            AgentAction.ANSWER_ONLY,
            AgentAction.MODIFY_WORKFLOW,
            AgentAction.CONTINUE_WORKFLOW,
        ),
        targets=(
            ActionClassificationTarget(
                target_stage="plan_review",
                target_artifact_ref="artifact:plan:v2",
            ),
        ),
    )


def _request() -> ActionClassificationRequest:
    return ActionClassificationRequest(
        turn_id="turn-002",
        content="把这个方案改得更有科技感",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.MODIFY_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id="wf-video",
            target_stage="plan_review",
            target_artifact_ref="artifact:plan:v2",
            reason_code="verb_modify_workflow_reply_target",
            candidate_workflow_ids=("wf-video",),
        ),
        candidates=(_candidate(),),
        context_summary="用户已确认 30 秒、16:9，当前等待审核第二版 Plan。",
    )


def _valid_payload() -> dict[str, Any]:
    return {
        "action": "modify_workflow",
        "intent": "video",
        "target_workflow_id": "wf-video",
        "target_stage": "plan_review",
        "target_artifact_ref": "artifact:plan:v2",
        "confidence": 0.93,
        "requires_confirmation": False,
        "clarification_question": None,
        "patch": {"style": "科技感"},
        "reason_code": "modify_confirmed_plan",
        "idempotency_key": "decision:turn-002",
    }


def _clarify_payload() -> dict[str, Any]:
    return {
        "action": "clarify",
        "intent": "video",
        "target_workflow_id": None,
        "target_stage": None,
        "target_artifact_ref": None,
        "confidence": 0.42,
        "requires_confirmation": False,
        "clarification_question": "你要重新生成哪一个视频任务？",
        "patch": {},
        "reason_code": "ambiguous_video_target",
        "idempotency_key": "decision:turn-ambiguous",
    }


@pytest.mark.asyncio
async def test_classifier_returns_frozen_action_decision_and_builds_bounded_prompt() -> None:
    model = FakeDecisionModel(_valid_payload())

    decision = await LLMActionClassifier(model).classify(_request())

    assert isinstance(decision, ActionDecision)
    assert decision == ActionDecision.model_validate(_valid_payload())
    assert len(model.calls) == 1
    assert tuple(role for role, _ in model.calls[0]) == ("system", "human")
    prompt = "\n".join(content for _, content in model.calls[0])
    assert "确定性证据优先" in prompt
    assert "不得输出思维链" in prompt
    assert '"target_workflow_id":"wf-video"' in prompt
    assert '"idempotency_key":"decision:turn-002"' in prompt
    assert '"action_decision_schema":' in prompt
    assert '"AgentAction"' in prompt
    assert '"required":["action","intent","confidence","reason_code","idempotency_key"]' in prompt


@pytest.mark.asyncio
async def test_classifier_repairs_invalid_target_once_without_overriding_evidence() -> None:
    invalid = _valid_payload()
    invalid["target_workflow_id"] = "wf-other"
    model = FakeDecisionModel(invalid, _valid_payload())

    decision = await LLMActionClassifier(model).classify(_request())

    assert decision.target_workflow_id == "wf-video"
    assert len(model.calls) == 2
    repair_prompt = model.calls[1][-1][1]
    assert "重新输出完整 JSON" in repair_prompt
    assert "deterministic_target_conflict" in repair_prompt
    assert "wf-other" not in repair_prompt


@pytest.mark.asyncio
async def test_classifier_fails_closed_after_two_invalid_structured_outputs() -> None:
    model = FakeDecisionModel("{not-json", {"action": "unknown"})

    with pytest.raises(DecisionClassificationError) as exc_info:
        await LLMActionClassifier(model).classify(_request())

    assert exc_info.value.reason_code == "classifier_output_invalid"
    assert exc_info.value.attempts == 2
    assert exc_info.value.error_codes
    assert "{not-json" not in str(exc_info.value)
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_updates", "expected_error_code"),
    [
        ({"intent": "image"}, "target_intent_conflict"),
        ({"target_stage": "scene_review"}, "target_stage_mismatch"),
        (
            {"target_artifact_ref": "artifact:plan:v1"},
            "target_artifact_mismatch",
        ),
    ],
)
async def test_classifier_rejects_cross_candidate_target_fields(
    invalid_updates: dict[str, object],
    expected_error_code: str,
) -> None:
    request = ActionClassificationRequest(
        turn_id="turn-003",
        content="改一下这个方案",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.PARTIAL,
            intent=AgentIntent.GENERAL,
            target_workflow_id="wf-video",
            reason_code="reply_target",
            candidate_workflow_ids=("wf-video",),
        ),
        candidates=(_candidate(),),
    )
    valid = _valid_payload()
    valid["idempotency_key"] = "decision:turn-003"
    invalid = {**valid, **invalid_updates}
    model = FakeDecisionModel(invalid, valid)

    decision = await LLMActionClassifier(model).classify(request)

    assert decision.target_workflow_id == "wf-video"
    assert expected_error_code in model.calls[1][-1][1]


@pytest.mark.asyncio
async def test_classifier_accepts_valid_json_text() -> None:
    model = FakeDecisionModel(
        json.dumps(_valid_payload(), ensure_ascii=False),
    )

    decision = await LLMActionClassifier(model).classify(_request())

    assert decision.reason_code == "modify_confirmed_plan"
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_updates", "expected_error_code"),
    [
        ({"action": "continue_workflow"}, "deterministic_action_conflict"),
        ({"idempotency_key": "decision:other"}, "idempotency_key_conflict"),
        ({"reason_code": "这里包含模型解释"}, "invalid_reason_code"),
        ({"action": "unknown"}, "enum"),
        ({"model_rationale": "不应接收"}, "extra_forbidden"),
    ],
)
async def test_classifier_repairs_invalid_contract_fields(
    invalid_updates: dict[str, object],
    expected_error_code: str,
) -> None:
    invalid = {**_valid_payload(), **invalid_updates}
    model = FakeDecisionModel(invalid, _valid_payload())

    decision = await LLMActionClassifier(model).classify(_request())

    assert decision == ActionDecision.model_validate(_valid_payload())
    assert expected_error_code in model.calls[1][-1][1]
    assert "这里包含模型解释" not in model.calls[1][-1][1]


@pytest.mark.asyncio
async def test_classifier_reports_model_failure_without_leaking_exception() -> None:
    model = FakeDecisionModel(
        RuntimeError("Authorization=secret-model-error"),
    )

    with pytest.raises(DecisionClassificationError) as exc_info:
        await LLMActionClassifier(model).classify(_request())

    assert exc_info.value.reason_code == "classifier_model_failed"
    assert exc_info.value.attempts == 1
    assert exc_info.value.error_codes == ()
    assert "secret-model-error" not in str(exc_info.value)
    assert len(model.calls) == 1


def test_classification_request_rejects_unknown_deterministic_candidate() -> None:
    with pytest.raises(ValidationError, match="candidate_workflow_ids"):
        ActionClassificationRequest(
            turn_id="turn-004",
            content="继续",
            deterministic_resolution=DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                reason_code="ambiguous_workflow_target",
                candidate_workflow_ids=("wf-missing",),
            ),
            candidates=(_candidate(),),
        )


@pytest.mark.asyncio
async def test_classifier_allows_clarify_for_ambiguous_deterministic_action() -> None:
    request = ActionClassificationRequest(
        turn_id="turn-ambiguous",
        content="再生成一次",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.AMBIGUOUS,
            action=AgentAction.REGENERATE_STAGE,
            intent=AgentIntent.VIDEO,
            reason_code="ambiguous_workflow_target",
            candidate_workflow_ids=("wf-video", "wf-video-other"),
        ),
        candidates=(
            _candidate(),
            _candidate().model_copy(
                update={"workflow_id": "wf-video-other"},
            ),
        ),
    )
    model = FakeDecisionModel(_clarify_payload())

    decision = await LLMActionClassifier(model).classify(request)

    assert decision.action == AgentAction.CLARIFY
    assert decision.target_workflow_id is None
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_classifier_repairs_guessed_target_for_ambiguous_resolution() -> None:
    guessed = _valid_payload()
    guessed.update(
        action="regenerate_stage",
        patch={},
        idempotency_key="decision:turn-ambiguous",
    )
    request = ActionClassificationRequest(
        turn_id="turn-ambiguous",
        content="再生成一次",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.AMBIGUOUS,
            action=AgentAction.REGENERATE_STAGE,
            intent=AgentIntent.VIDEO,
            reason_code="ambiguous_workflow_target",
            candidate_workflow_ids=("wf-video", "wf-video-other"),
        ),
        candidates=(
            _candidate(),
            _candidate().model_copy(
                update={"workflow_id": "wf-video-other"},
            ),
        ),
    )
    model = FakeDecisionModel(guessed, _clarify_payload())

    decision = await LLMActionClassifier(model).classify(request)

    assert decision.action == AgentAction.CLARIFY
    assert "ambiguous_resolution_requires_clarify" in model.calls[1][-1][1]


@pytest.mark.asyncio
async def test_classifier_preserves_exact_historical_stage_artifact_target() -> None:
    candidate = _candidate().model_copy(
        update={
            "current_stage": "video_review",
            "targets": (
                ActionClassificationTarget(
                    target_stage="video_review",
                    target_artifact_ref="artifact:video:final",
                ),
                ActionClassificationTarget(
                    target_stage="plan_review",
                    target_artifact_ref="artifact:plan:v1",
                ),
            ),
        },
    )
    request = ActionClassificationRequest(
        turn_id="turn-history",
        content="把上一版 Plan 改得更简洁",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.MODIFY_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id="wf-video",
            target_stage="plan_review",
            target_artifact_ref="artifact:plan:v1",
            reason_code="verb_modify_workflow_artifact_target",
            candidate_workflow_ids=("wf-video",),
        ),
        candidates=(candidate,),
    )
    payload = _valid_payload()
    payload.update(
        target_stage="plan_review",
        target_artifact_ref="artifact:plan:v1",
        idempotency_key="decision:turn-history",
    )
    model = FakeDecisionModel(payload)

    decision = await LLMActionClassifier(model).classify(request)

    assert decision.target_stage == "plan_review"
    assert decision.target_artifact_ref == "artifact:plan:v1"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_classifier_repairs_stage_artifact_from_different_target_pairs() -> None:
    candidate = _candidate().model_copy(
        update={
            "current_stage": "video_review",
            "targets": (
                ActionClassificationTarget(
                    target_stage="video_review",
                    target_artifact_ref="artifact:video:final",
                ),
                ActionClassificationTarget(
                    target_stage="plan_review",
                    target_artifact_ref="artifact:plan:v1",
                ),
            ),
        },
    )
    request = ActionClassificationRequest(
        turn_id="turn-history",
        content="修改上一版 Plan",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.PARTIAL,
            intent=AgentIntent.VIDEO,
            target_workflow_id="wf-video",
            reason_code="workflow_target",
            candidate_workflow_ids=("wf-video",),
        ),
        candidates=(candidate,),
    )
    valid = _valid_payload()
    valid.update(
        target_stage="plan_review",
        target_artifact_ref="artifact:plan:v1",
        idempotency_key="decision:turn-history",
    )
    invalid = valid | {
        "target_artifact_ref": "artifact:video:final",
    }
    model = FakeDecisionModel(invalid, valid)

    decision = await LLMActionClassifier(model).classify(request)

    assert decision.target_artifact_ref == "artifact:plan:v1"
    assert "target_reference_mismatch" in model.calls[1][-1][1]
