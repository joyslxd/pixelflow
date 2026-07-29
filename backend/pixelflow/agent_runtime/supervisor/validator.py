"""在图派发前校验 Supervisor 决策的权威状态与风险。"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
)

from .classifier import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
)
from .resolver import DeterministicResolutionStatus

_MEDIUM_CONFIDENCE_THRESHOLD = 0.55
_HIGH_CONFIDENCE_THRESHOLD = 0.82
_POTENTIALLY_BILLING_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
    AgentAction.START_WORKFLOW,
}
_TARGET_SENSITIVE_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
}
_EXISTING_WORKFLOW_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
    AgentAction.SWITCH_WORKFLOW,
    AgentAction.CANCEL_WORKFLOW,
}
_GLOBAL_SCOPED_ACTIONS = {
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
    AgentAction.START_WORKFLOW,
}
_DEFAULT_GLOBAL_ACTIONS = (
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
)


class _ValidatorModel(BaseModel):
    """为 Validator 输入提供严格、不可变的边界。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class DecisionValidationRequest(_ValidatorModel):
    """同时保存分类快照与派发前重新读取的权威状态。"""

    decision: ActionDecision
    classification_request: ActionClassificationRequest
    current_candidates: tuple[ActionClassificationCandidate, ...] = Field(
        default=(),
        max_length=32,
    )
    allowed_global_actions: tuple[AgentAction, ...] = Field(
        default=_DEFAULT_GLOBAL_ACTIONS,
        max_length=16,
    )
    expected_context_version: int = Field(ge=0)
    current_context_version: int = Field(ge=0)

    @model_validator(mode="after")
    def require_unique_current_candidates(self) -> Self:
        """拒绝同一 Workflow 的多个权威投影。"""

        workflow_ids = tuple(candidate.workflow_id for candidate in self.current_candidates)
        if len(set(workflow_ids)) != len(workflow_ids):
            raise ValueError("current_candidates 的 workflow_id 不能重复")
        if len(set(self.allowed_global_actions)) != len(self.allowed_global_actions):
            raise ValueError("allowed_global_actions 不能重复")
        return self


class DecisionValidationError(ValueError):
    """返回不包含用户内容或内部状态详情的校验失败摘要。"""

    def __init__(self, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"Supervisor 决策校验失败：{reason_code}")


class DecisionValidator:
    """在任何业务命令派发前执行最后一道安全校验。"""

    def validate(
        self,
        request: DecisionValidationRequest,
    ) -> ActionDecision:
        """返回与输入隔离的合法决策副本。"""

        if request.expected_context_version != request.current_context_version:
            raise DecisionValidationError(
                reason_code="context_version_conflict",
            )
        decision = request.decision.model_copy(deep=True)
        _validate_classification_evidence(
            request.classification_request,
            decision,
        )
        snapshot_candidates_by_id = {candidate.workflow_id: candidate for candidate in request.classification_request.candidates}
        candidates_by_id = {candidate.workflow_id: candidate for candidate in request.current_candidates}
        if decision.action in _EXISTING_WORKFLOW_ACTIONS and decision.target_workflow_id is None:
            raise DecisionValidationError(
                reason_code="target_workflow_required",
            )
        if decision.action == AgentAction.START_WORKFLOW and decision.target_workflow_id is not None:
            raise DecisionValidationError(
                reason_code="start_workflow_target_must_be_new",
            )
        if decision.action == AgentAction.START_WORKFLOW and decision.intent == AgentIntent.GENERAL:
            raise DecisionValidationError(
                reason_code="start_workflow_intent_invalid",
            )
        if decision.action in _GLOBAL_SCOPED_ACTIONS and decision.action not in request.allowed_global_actions:
            raise DecisionValidationError(
                reason_code="action_not_allowed",
            )
        if decision.target_workflow_id is not None:
            candidate = candidates_by_id.get(decision.target_workflow_id)
            if candidate is None:
                raise DecisionValidationError(
                    reason_code="target_workflow_not_found",
                )
            snapshot = snapshot_candidates_by_id.get(decision.target_workflow_id)
            if snapshot is None:
                raise DecisionValidationError(
                    reason_code="target_workflow_not_in_snapshot",
                )
            if snapshot.stage_version != candidate.stage_version:
                raise DecisionValidationError(
                    reason_code="stage_version_conflict",
                )
            if snapshot.context_version != candidate.context_version:
                raise DecisionValidationError(
                    reason_code="workflow_context_version_conflict",
                )
            if snapshot.intent != candidate.intent or decision.intent != candidate.intent:
                raise DecisionValidationError(
                    reason_code="target_intent_conflict",
                )
            if snapshot.status != candidate.status or snapshot.current_stage != candidate.current_stage:
                raise DecisionValidationError(
                    reason_code="workflow_state_conflict",
                )
            if (decision.target_stage is not None or decision.target_artifact_ref is not None) and not _matches_target_reference(candidate, decision):
                raise DecisionValidationError(
                    reason_code="target_reference_stale",
                )
            if decision.action not in snapshot.allowed_actions or decision.action not in candidate.allowed_actions:
                raise DecisionValidationError(
                    reason_code="action_not_allowed",
                )
        elif decision.action not in _GLOBAL_SCOPED_ACTIONS:
            raise DecisionValidationError(
                reason_code="action_not_allowed",
            )
        if decision.action != AgentAction.CLARIFY and decision.requires_confirmation:
            return _clarify_decision(
                request,
                decision,
                reason_code="decision_requires_confirmation",
                question=(decision.clarification_question or "请确认是否执行这个操作。"),
            )
        if decision.action != AgentAction.CLARIFY and decision.confidence < _MEDIUM_CONFIDENCE_THRESHOLD:
            return _clarify_decision(
                request,
                decision,
                reason_code="low_confidence_requires_clarification",
                question="我还不能确定你的操作意图，请说明要处理哪个任务或产物。",
            )
        target_is_unique = _has_unique_target(request, decision)
        if decision.action in _POTENTIALLY_BILLING_ACTIONS and not target_is_unique:
            return _clarify_decision(
                request,
                decision,
                reason_code="ambiguous_billing_target",
                question="这个操作可能启动新的生成任务，请明确要处理哪个任务或产物。",
            )
        if decision.confidence < _HIGH_CONFIDENCE_THRESHOLD and not target_is_unique:
            return _clarify_decision(
                request,
                decision,
                reason_code="medium_confidence_ambiguous_target",
                question="请明确要处理哪个任务或产物。",
            )
        if not target_is_unique and decision.action not in {
            AgentAction.ANSWER_ONLY,
            AgentAction.CLARIFY,
        }:
            return _clarify_decision(
                request,
                decision,
                reason_code="ambiguous_target",
                question="请明确要处理哪个任务或产物。",
            )
        if decision.confidence < _HIGH_CONFIDENCE_THRESHOLD and decision.action in _POTENTIALLY_BILLING_ACTIONS:
            return _clarify_decision(
                request,
                decision,
                reason_code="medium_confidence_billing_risk",
                question="这个操作可能启动新的生成任务，请确认要处理哪个任务或产物。",
            )
        return decision


def _validate_classification_evidence(
    classification_request: ActionClassificationRequest,
    decision: ActionDecision,
) -> None:
    """再次校验模型输出与确定性证据，避免绕过分类器边界。"""

    if decision.idempotency_key != classification_request.idempotency_key:
        raise DecisionValidationError(
            reason_code="idempotency_key_conflict",
        )
    resolution = classification_request.deterministic_resolution
    if resolution.status == DeterministicResolutionStatus.AMBIGUOUS:
        if decision.action != AgentAction.CLARIFY or decision.target_workflow_id is not None or decision.target_stage is not None or decision.target_artifact_ref is not None:
            raise DecisionValidationError(
                reason_code="ambiguous_resolution_requires_clarify",
            )
    elif resolution.action is not None and decision.action != resolution.action:
        raise DecisionValidationError(
            reason_code="deterministic_action_conflict",
        )
    if resolution.intent != AgentIntent.GENERAL and decision.intent != resolution.intent:
        raise DecisionValidationError(
            reason_code="deterministic_intent_conflict",
        )
    expected_targets = (
        resolution.target_workflow_id,
        resolution.target_stage,
        resolution.target_artifact_ref,
    )
    actual_targets = (
        decision.target_workflow_id,
        decision.target_stage,
        decision.target_artifact_ref,
    )
    if any(
        expected is not None and actual != expected
        for expected, actual in zip(
            expected_targets,
            actual_targets,
            strict=True,
        )
    ):
        raise DecisionValidationError(
            reason_code="deterministic_target_conflict",
        )


def _matches_target_reference(
    candidate: ActionClassificationCandidate,
    decision: ActionDecision,
) -> bool:
    """要求 stage 与 artifact 仍属于同一条权威目标证据。"""

    return any((decision.target_stage is None or target.target_stage == decision.target_stage) and (decision.target_artifact_ref is None or target.target_artifact_ref == decision.target_artifact_ref) for target in candidate.targets)


def _has_unique_target(
    request: DecisionValidationRequest,
    decision: ActionDecision,
) -> bool:
    """只把确定性单目标或唯一合法候选视为可安全派发。"""

    if decision.action == AgentAction.START_WORKFLOW:
        return decision.intent != AgentIntent.GENERAL
    target_workflow_id = decision.target_workflow_id
    if target_workflow_id is None:
        return decision.action in {
            AgentAction.ANSWER_ONLY,
            AgentAction.CLARIFY,
        }
    resolution = request.classification_request.deterministic_resolution
    snapshot_candidate = next(
        (candidate for candidate in request.classification_request.candidates if candidate.workflow_id == target_workflow_id),
        None,
    )
    current_candidate = next(
        (candidate for candidate in request.current_candidates if candidate.workflow_id == target_workflow_id),
        None,
    )
    if snapshot_candidate is None or current_candidate is None:
        return False
    target_detail_is_unique = True
    if decision.action in _TARGET_SENSITIVE_ACTIONS:
        snapshot_target_pairs = _eligible_target_pairs(
            snapshot_candidate,
            resolution_stage=resolution.target_stage,
            resolution_artifact_ref=resolution.target_artifact_ref,
        )
        current_target_pairs = _eligible_target_pairs(
            current_candidate,
            resolution_stage=resolution.target_stage,
            resolution_artifact_ref=resolution.target_artifact_ref,
        )
        target_detail_is_unique = snapshot_target_pairs == current_target_pairs and len(snapshot_target_pairs) == 1
    if (
        resolution.status
        in {
            DeterministicResolutionStatus.RESOLVED,
            DeterministicResolutionStatus.PARTIAL,
        }
        and resolution.target_workflow_id == target_workflow_id
        and set(resolution.candidate_workflow_ids) == {target_workflow_id}
    ):
        return target_detail_is_unique
    snapshot_eligible_ids = {candidate.workflow_id for candidate in request.classification_request.candidates if candidate.intent == decision.intent and decision.action in candidate.allowed_actions}
    current_eligible_ids = {candidate.workflow_id for candidate in request.current_candidates if candidate.intent == decision.intent and decision.action in candidate.allowed_actions}
    expected_ids = {target_workflow_id}
    return snapshot_eligible_ids == expected_ids and current_eligible_ids == expected_ids and target_detail_is_unique


def _eligible_target_pairs(
    candidate: ActionClassificationCandidate,
    *,
    resolution_stage: str | None,
    resolution_artifact_ref: str | None,
) -> frozenset[tuple[str | None, str | None]]:
    """返回被确定性证据筛中的权威 stage/artifact 目标对。"""

    return frozenset(
        (
            target.target_stage,
            target.target_artifact_ref,
        )
        for target in candidate.targets
        if (resolution_stage is None or target.target_stage == resolution_stage) and (resolution_artifact_ref is None or target.target_artifact_ref == resolution_artifact_ref)
    )


def _clarify_decision(
    request: DecisionValidationRequest,
    decision: ActionDecision,
    *,
    reason_code: str,
    question: str,
) -> ActionDecision:
    """清除所有可派发字段，生成只允许追问的安全决策。"""

    if AgentAction.CLARIFY not in request.allowed_global_actions:
        raise DecisionValidationError(
            reason_code="clarification_not_allowed",
        )
    payload = decision.model_dump(mode="python")
    payload.update(
        action=AgentAction.CLARIFY,
        target_workflow_id=None,
        target_stage=None,
        target_artifact_ref=None,
        requires_confirmation=True,
        clarification_question=question,
        patch={},
        reason_code=reason_code,
    )
    return ActionDecision.model_validate(payload)


__all__ = [
    "DecisionValidationError",
    "DecisionValidationRequest",
    "DecisionValidator",
]
