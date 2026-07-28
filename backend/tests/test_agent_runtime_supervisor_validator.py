import pytest

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
    DecisionValidationError,
    DecisionValidationRequest,
    DecisionValidator,
    DeterministicResolution,
    DeterministicResolutionStatus,
)


def _candidate(
    *,
    workflow_id: str = "wf-video",
    intent: AgentIntent = AgentIntent.VIDEO,
    status: WorkflowStatus = WorkflowStatus.AWAITING_USER,
    current_stage: str = "plan_review",
    stage_version: int = 2,
    context_version: int = 5,
    allowed_actions: tuple[AgentAction, ...] = (
        AgentAction.ANSWER_ONLY,
        AgentAction.MODIFY_WORKFLOW,
        AgentAction.CONTINUE_WORKFLOW,
    ),
    targets: tuple[ActionClassificationTarget, ...] | None = None,
) -> ActionClassificationCandidate:
    return ActionClassificationCandidate(
        workflow_id=workflow_id,
        intent=intent,
        status=status,
        current_stage=current_stage,
        stage_version=stage_version,
        context_version=context_version,
        allowed_actions=allowed_actions,
        targets=(
            targets
            if targets is not None
            else (
                ActionClassificationTarget(
                    target_stage="plan_review",
                    target_artifact_ref="artifact:plan:v2",
                ),
            )
        ),
    )


def _classification_request(
    *candidates: ActionClassificationCandidate,  # 可变位置参数用于组合分类候选
    action: AgentAction = AgentAction.MODIFY_WORKFLOW,
    deterministic_resolution: DeterministicResolution | None = None,
) -> ActionClassificationRequest:
    return ActionClassificationRequest(
        turn_id="turn-validator",
        content="把这个方案改得更有科技感",
        deterministic_resolution=(
            deterministic_resolution
            or DeterministicResolution(
                status=DeterministicResolutionStatus.RESOLVED,
                action=action,
                intent=AgentIntent.VIDEO,
                target_workflow_id="wf-video",
                target_stage="plan_review",
                target_artifact_ref="artifact:plan:v2",
                reason_code="verb_modify_workflow_reply_target",
                candidate_workflow_ids=("wf-video",),
            )
        ),
        candidates=candidates or (_candidate(),),
    )


def _decision(
    *,
    action: AgentAction = AgentAction.MODIFY_WORKFLOW,
    confidence: float = 0.93,
) -> ActionDecision:
    return ActionDecision(
        action=action,
        intent=AgentIntent.VIDEO,
        target_workflow_id="wf-video",
        target_stage="plan_review",
        target_artifact_ref="artifact:plan:v2",
        confidence=confidence,
        requires_confirmation=False,
        clarification_question=None,
        patch=({"style": "科技感"} if action == AgentAction.MODIFY_WORKFLOW else {}),
        reason_code="modify_confirmed_plan",
        idempotency_key="decision:turn-validator",
    )


def _untargeted_decision(
    *,
    action: AgentAction,
    intent: AgentIntent,
    confidence: float = 0.93,
    clarification_question: str | None = None,
) -> ActionDecision:
    return ActionDecision(
        action=action,
        intent=intent,
        target_workflow_id=None,
        target_stage=None,
        target_artifact_ref=None,
        confidence=confidence,
        requires_confirmation=False,
        clarification_question=clarification_question,
        patch={},
        reason_code="untargeted_action",
        idempotency_key="decision:turn-validator",
    )


def _untargeted_classification_request(
    *,
    action: AgentAction,
    intent: AgentIntent,
) -> ActionClassificationRequest:
    return ActionClassificationRequest(
        turn_id="turn-validator",
        content="请处理这个请求",
        deterministic_resolution=DeterministicResolution(
            status=DeterministicResolutionStatus.PARTIAL,
            action=action,
            intent=intent,
            reason_code="untargeted_action",
        ),
        candidates=(),
    )


def test_validator_approves_allowed_high_confidence_decision() -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated == request.decision
    assert validated is not request.decision


def test_validator_rejects_action_outside_current_allowed_actions() -> None:
    snapshot = _candidate()
    current = _candidate(
        allowed_actions=(
            AgentAction.ANSWER_ONLY,
            AgentAction.CONTINUE_WORKFLOW,
        ),
    )
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(snapshot),
        current_candidates=(current,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "action_not_allowed"
    assert "modify_workflow" not in str(exc_info.value)


def test_validator_rejects_action_outside_classification_snapshot_whitelist() -> None:
    snapshot = _candidate(
        allowed_actions=(AgentAction.ANSWER_ONLY,),
    )
    current = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(snapshot),
        current_candidates=(current,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "action_not_allowed"


def test_validator_rejects_stale_conversation_context_version() -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=6,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "context_version_conflict"


@pytest.mark.parametrize(
    ("current_candidate", "expected_reason_code"),
    [
        (
            _candidate(stage_version=3),
            "stage_version_conflict",
        ),
        (
            _candidate(context_version=6),
            "workflow_context_version_conflict",
        ),
    ],
)
def test_validator_rejects_stale_target_snapshot(
    current_candidate: ActionClassificationCandidate,
    expected_reason_code: str,
) -> None:
    snapshot = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(snapshot),
        current_candidates=(current_candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == expected_reason_code


@pytest.mark.parametrize(
    ("current_candidate", "expected_reason_code"),
    [
        (
            _candidate(status=WorkflowStatus.RUNNING),
            "workflow_state_conflict",
        ),
        (
            _candidate(current_stage="scene_review"),
            "workflow_state_conflict",
        ),
        (
            _candidate(intent=AgentIntent.IMAGE),
            "target_intent_conflict",
        ),
        (
            _candidate(targets=()),
            "target_reference_stale",
        ),
    ],
)
def test_validator_rejects_changed_or_mismatched_target_state(
    current_candidate: ActionClassificationCandidate,
    expected_reason_code: str,
) -> None:
    snapshot = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(snapshot),
        current_candidates=(current_candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == expected_reason_code


def test_validator_turns_low_confidence_decision_into_clarification() -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(confidence=0.54),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.target_workflow_id is None
    assert validated.target_stage is None
    assert validated.target_artifact_ref is None
    assert validated.patch == {}
    assert validated.requires_confirmation is True
    assert validated.clarification_question
    assert validated.reason_code == "low_confidence_requires_clarification"


def test_validator_rejects_required_clarification_outside_global_whitelist() -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(confidence=0.4),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
        allowed_global_actions=(AgentAction.ANSWER_ONLY,),
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "clarification_not_allowed"


def test_validator_clarifies_medium_confidence_billing_action() -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(confidence=0.7),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "medium_confidence_billing_risk"
    assert validated.patch == {}


@pytest.mark.parametrize(
    ("confidence", "expected_action"),
    [
        (0.55, AgentAction.CLARIFY),
        (0.82, AgentAction.MODIFY_WORKFLOW),
    ],
)
def test_validator_applies_frozen_confidence_boundaries(
    confidence: float,
    expected_action: AgentAction,
) -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision(confidence=confidence),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == expected_action


def test_validator_approves_medium_confidence_non_billing_unique_target() -> None:
    candidate = _candidate(
        allowed_actions=(AgentAction.CANCEL_WORKFLOW,),
    )
    decision = _decision(
        action=AgentAction.CANCEL_WORKFLOW,
        confidence=0.7,
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(
            candidate,
            action=AgentAction.CANCEL_WORKFLOW,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated == decision


def test_validator_clarifies_ambiguous_high_confidence_billing_target() -> None:
    first = _candidate()
    second = _candidate(workflow_id="wf-video-other")
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.MODIFY_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="verb_modify_workflow",
    )
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(
            first,
            second,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(first, second),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "ambiguous_billing_target"


def test_validator_preserves_classification_time_target_ambiguity() -> None:
    first = _candidate()
    second = _candidate(workflow_id="wf-video-other")
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.MODIFY_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="verb_modify_workflow",
    )
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(
            first,
            second,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(first,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "ambiguous_billing_target"


def test_validator_clarifies_multiple_artifacts_inside_one_workflow() -> None:
    candidate = _candidate(
        allowed_actions=(AgentAction.REGENERATE_STAGE,),
        current_stage="image_review",
        targets=(
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:1",
            ),
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:2",
            ),
        ),
    )
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.REGENERATE_STAGE,
        intent=AgentIntent.VIDEO,
        reason_code="verb_regenerate_stage",
    )
    decision = ActionDecision(
        action=AgentAction.REGENERATE_STAGE,
        intent=AgentIntent.VIDEO,
        target_workflow_id="wf-video",
        target_stage="image_review",
        target_artifact_ref="artifact:image:1",
        confidence=0.93,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="regenerate_selected_image",
        idempotency_key="decision:turn-validator",
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(
            candidate,
            action=AgentAction.REGENERATE_STAGE,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "ambiguous_billing_target"


def test_validator_clarifies_multiple_interrupts_inside_one_workflow() -> None:
    candidate = _candidate(
        allowed_actions=(AgentAction.CONTINUE_WORKFLOW,),
        targets=(
            ActionClassificationTarget(
                target_stage="plan_review",
                target_artifact_ref="artifact:plan:v2",
            ),
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:1",
            ),
        ),
    )
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.CONTINUE_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="verb_continue_workflow",
    )
    request = DecisionValidationRequest(
        decision=_decision(
            action=AgentAction.CONTINUE_WORKFLOW,
        ),
        classification_request=_classification_request(
            candidate,
            action=AgentAction.CONTINUE_WORKFLOW,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "ambiguous_billing_target"


def test_validator_clarifies_when_target_pairs_change_without_version_bump() -> None:
    snapshot = _candidate(
        allowed_actions=(AgentAction.REGENERATE_STAGE,),
        current_stage="image_review",
        targets=(
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:1",
            ),
        ),
    )
    current = _candidate(
        allowed_actions=(AgentAction.REGENERATE_STAGE,),
        current_stage="image_review",
        targets=(
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:2",
            ),
        ),
    )
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.REGENERATE_STAGE,
        intent=AgentIntent.VIDEO,
        reason_code="verb_regenerate_stage",
    )
    decision = ActionDecision(
        action=AgentAction.REGENERATE_STAGE,
        intent=AgentIntent.VIDEO,
        target_workflow_id="wf-video",
        target_stage="image_review",
        target_artifact_ref="artifact:image:2",
        confidence=0.93,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="regenerate_selected_image",
        idempotency_key="decision:turn-validator",
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(
            snapshot,
            action=AgentAction.REGENERATE_STAGE,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(current,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "ambiguous_billing_target"


def test_validator_approves_explicit_artifact_among_multiple_targets() -> None:
    candidate = _candidate(
        allowed_actions=(AgentAction.REGENERATE_STAGE,),
        current_stage="image_review",
        targets=(
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:1",
            ),
            ActionClassificationTarget(
                target_stage="image_review",
                target_artifact_ref="artifact:image:2",
            ),
        ),
    )
    resolution = DeterministicResolution(
        status=DeterministicResolutionStatus.RESOLVED,
        action=AgentAction.REGENERATE_STAGE,
        intent=AgentIntent.VIDEO,
        target_workflow_id="wf-video",
        target_stage="image_review",
        target_artifact_ref="artifact:image:2",
        reason_code="explicit_action",
        candidate_workflow_ids=("wf-video",),
    )
    decision = ActionDecision(
        action=AgentAction.REGENERATE_STAGE,
        intent=AgentIntent.VIDEO,
        target_workflow_id="wf-video",
        target_stage="image_review",
        target_artifact_ref="artifact:image:2",
        confidence=0.93,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="regenerate_selected_image",
        idempotency_key="decision:turn-validator",
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(
            candidate,
            action=AgentAction.REGENERATE_STAGE,
            deterministic_resolution=resolution,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated == decision


def test_validator_approves_explicit_billing_target_among_multiple_workflows() -> None:
    first = _candidate()
    second = _candidate(workflow_id="wf-video-other")
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(first, second),
        current_candidates=(first, second),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.MODIFY_WORKFLOW


def test_validator_clarifies_medium_confidence_ambiguous_non_billing_target() -> None:
    first = _candidate(
        allowed_actions=(AgentAction.CANCEL_WORKFLOW,),
    )
    second = _candidate(
        workflow_id="wf-video-other",
        allowed_actions=(AgentAction.CANCEL_WORKFLOW,),
    )
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.CANCEL_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="verb_cancel_workflow",
    )
    request = DecisionValidationRequest(
        decision=_decision(
            action=AgentAction.CANCEL_WORKFLOW,
            confidence=0.7,
        ),
        classification_request=_classification_request(
            first,
            second,
            action=AgentAction.CANCEL_WORKFLOW,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(first, second),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "medium_confidence_ambiguous_target"


def test_validator_clarifies_high_confidence_ambiguous_target() -> None:
    first = _candidate(
        allowed_actions=(AgentAction.CANCEL_WORKFLOW,),
    )
    second = _candidate(
        workflow_id="wf-video-other",
        allowed_actions=(AgentAction.CANCEL_WORKFLOW,),
    )
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.CANCEL_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="verb_cancel_workflow",
    )
    request = DecisionValidationRequest(
        decision=_decision(
            action=AgentAction.CANCEL_WORKFLOW,
            confidence=0.93,
        ),
        classification_request=_classification_request(
            first,
            second,
            action=AgentAction.CANCEL_WORKFLOW,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(first, second),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "ambiguous_target"


def test_validator_clarifies_decision_that_requires_confirmation() -> None:
    candidate = _candidate()
    decision = _decision().model_copy(
        update={
            "requires_confirmation": True,
            "clarification_question": "确认重新处理这个方案吗？",
        },
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    validated = DecisionValidator().validate(request)

    assert validated.action == AgentAction.CLARIFY
    assert validated.reason_code == "decision_requires_confirmation"
    assert validated.clarification_question == "确认重新处理这个方案吗？"


def test_validator_rejects_unlisted_global_action() -> None:
    request = DecisionValidationRequest(
        decision=_untargeted_decision(
            action=AgentAction.ANSWER_ONLY,
            intent=AgentIntent.GENERAL,
        ),
        classification_request=_untargeted_classification_request(
            action=AgentAction.ANSWER_ONLY,
            intent=AgentIntent.GENERAL,
        ),
        current_candidates=(),
        expected_context_version=5,
        current_context_version=5,
        allowed_global_actions=(),
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "action_not_allowed"


def test_validator_rejects_targeted_global_action_outside_global_whitelist() -> None:
    candidate = _candidate(
        allowed_actions=(AgentAction.ANSWER_ONLY,),
    )
    request = DecisionValidationRequest(
        decision=_decision(
            action=AgentAction.ANSWER_ONLY,
        ),
        classification_request=_classification_request(
            candidate,
            action=AgentAction.ANSWER_ONLY,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
        allowed_global_actions=(),
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "action_not_allowed"


def test_validator_approves_explicitly_allowed_new_workflow_action() -> None:
    decision = _untargeted_decision(
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_untargeted_classification_request(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
        ),
        current_candidates=(),
        expected_context_version=5,
        current_context_version=5,
        allowed_global_actions=(
            AgentAction.ANSWER_ONLY,
            AgentAction.CLARIFY,
            AgentAction.START_WORKFLOW,
        ),
    )

    validated = DecisionValidator().validate(request)

    assert validated == decision


def test_validator_rejects_existing_workflow_action_without_target() -> None:
    candidate = _candidate()
    decision = _untargeted_decision(
        action=AgentAction.MODIFY_WORKFLOW,
        intent=AgentIntent.VIDEO,
    )
    unresolved = DeterministicResolution(
        status=DeterministicResolutionStatus.UNRESOLVED,
        action=AgentAction.MODIFY_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="verb_modify_workflow",
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(
            candidate,
            deterministic_resolution=unresolved,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "target_workflow_required"


def test_validator_rejects_new_workflow_with_general_intent() -> None:
    request = DecisionValidationRequest(
        decision=_untargeted_decision(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.GENERAL,
        ),
        classification_request=_untargeted_classification_request(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.GENERAL,
        ),
        current_candidates=(),
        expected_context_version=5,
        current_context_version=5,
        allowed_global_actions=(
            AgentAction.CLARIFY,
            AgentAction.START_WORKFLOW,
        ),
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "start_workflow_intent_invalid"


def test_validator_rejects_new_workflow_pointing_to_existing_projection() -> None:
    candidate = _candidate()
    decision = _decision(
        action=AgentAction.START_WORKFLOW,
    )
    request = DecisionValidationRequest(
        decision=decision,
        classification_request=_classification_request(
            candidate,
            action=AgentAction.START_WORKFLOW,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
        allowed_global_actions=(
            AgentAction.CLARIFY,
            AgentAction.START_WORKFLOW,
        ),
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "start_workflow_target_must_be_new"


def test_validator_rechecks_deterministic_action_evidence() -> None:
    candidate = _candidate(
        allowed_actions=(
            AgentAction.MODIFY_WORKFLOW,
            AgentAction.CANCEL_WORKFLOW,
        ),
    )
    request = DecisionValidationRequest(
        decision=_decision(),
        classification_request=_classification_request(
            candidate,
            action=AgentAction.CANCEL_WORKFLOW,
        ),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "deterministic_action_conflict"


def test_validator_rechecks_decision_idempotency_key() -> None:
    candidate = _candidate()
    request = DecisionValidationRequest(
        decision=_decision().model_copy(
            update={"idempotency_key": "decision:other-turn"},
        ),
        classification_request=_classification_request(candidate),
        current_candidates=(candidate,),
        expected_context_version=5,
        current_context_version=5,
    )

    with pytest.raises(DecisionValidationError) as exc_info:
        DecisionValidator().validate(request)

    assert exc_info.value.reason_code == "idempotency_key_conflict"
