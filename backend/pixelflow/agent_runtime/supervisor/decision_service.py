"""按固定顺序组合确定性解析、模型分类和权威决策校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import AIMessage
from pydantic import Field, JsonValue

from pixelflow.agent_runtime.context import ContextAssembler
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    ContextEnvelope,
    ContextRequest,
    ExplicitActionSignal,
    TurnRecord,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.contracts.base import ContractModel

from .classifier import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    LLMActionClassifier,
)
from .resolver import (
    DeterministicResolution,
    DeterministicResolutionRequest,
    DeterministicResolutionStatus,
    DeterministicTargetResolver,
    ResolverCandidate,
)
from .validator import (
    DecisionValidationRequest,
    DecisionValidator,
)

_GLOBAL_ACTIONS = (
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
    AgentAction.START_WORKFLOW,
)
_NON_MUTATING_ACTIONS = {
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
}
_WORKFLOW_ACTIONS_BY_STATUS: dict[
    WorkflowStatus,
    tuple[AgentAction, ...],
] = {
    WorkflowStatus.DRAFT: (
        AgentAction.ANSWER_ONLY,
        AgentAction.CONTINUE_WORKFLOW,
        AgentAction.MODIFY_WORKFLOW,
        AgentAction.SWITCH_WORKFLOW,
        AgentAction.CANCEL_WORKFLOW,
    ),
    WorkflowStatus.AWAITING_USER: (
        AgentAction.ANSWER_ONLY,
        AgentAction.CONTINUE_WORKFLOW,
        AgentAction.MODIFY_WORKFLOW,
        AgentAction.REGENERATE_STAGE,
        AgentAction.SWITCH_WORKFLOW,
        AgentAction.CANCEL_WORKFLOW,
    ),
    WorkflowStatus.RUNNING: (
        AgentAction.ANSWER_ONLY,
        AgentAction.SWITCH_WORKFLOW,
        AgentAction.CANCEL_WORKFLOW,
    ),
    WorkflowStatus.PAUSED_QUOTA: (
        AgentAction.ANSWER_ONLY,
        AgentAction.RETRY_FAILED,
        AgentAction.SWITCH_WORKFLOW,
        AgentAction.CANCEL_WORKFLOW,
    ),
    WorkflowStatus.FAILED: (
        AgentAction.ANSWER_ONLY,
        AgentAction.RETRY_FAILED,
        AgentAction.SWITCH_WORKFLOW,
        AgentAction.CANCEL_WORKFLOW,
    ),
    WorkflowStatus.COMPLETED: (
        AgentAction.ANSWER_ONLY,
        AgentAction.MODIFY_WORKFLOW,
        AgentAction.REGENERATE_STAGE,
        AgentAction.SWITCH_WORKFLOW,
    ),
    WorkflowStatus.CANCELLED: (
        AgentAction.ANSWER_ONLY,
        AgentAction.SWITCH_WORKFLOW,
    ),
}


class SupervisorTurnEvidence(ContractModel):
    """保存一次 Supervisor 决策所需的权威 Turn 与投影证据。"""

    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    turn: TurnRecord
    content: str = Field(min_length=1)
    visible_messages: tuple[dict[str, JsonValue], ...]
    workflows: tuple[WorkflowRecord, ...]
    active_workflow_id: str | None = Field(default=None, min_length=1)
    materials: tuple[dict[str, JsonValue], ...] = ()
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: tuple[str, ...] = ()
    explicit_action: ExplicitActionSignal | None = None
    expected_context_version: int = Field(ge=0)
    authoritative_context_version: int = Field(ge=0)

    def to_resolver_input(self) -> DeterministicResolutionRequest:
        """只从服务端权威投影构造规则解析输入。"""

        return DeterministicResolutionRequest(
            content=self.content,
            candidates=_resolver_candidates(self),
            explicit_action=self.explicit_action,
            reply_to_message_id=self.reply_to_message_id,
            artifact_refs=self.artifact_refs,
            active_workflow_id=self.active_workflow_id,
        )


class SupervisorAnswerPort(Protocol):
    """隔离只读回答模型，禁止向执行层暴露工具调用。"""

    async def answer(self, context: ContextEnvelope) -> str: ...


@dataclass(frozen=True, slots=True)
class SupervisorDecisionResult:
    """返回已校验决策、校验快照和可选只读回答。"""

    decision: ActionDecision
    validation_request: DecisionValidationRequest
    context: ContextEnvelope
    answer_message: AIMessage | None = None


class SupervisorDecisionUnavailableError(RuntimeError):
    """用固定原因码隐藏模型档案或决策依赖的内部异常。"""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SupervisorDecisionService:
    """以 explicit、deterministic、classifier、validator 顺序生成决策。"""

    def __init__(
        self,
        *,
        resolver: DeterministicTargetResolver,
        classifier: LLMActionClassifier | None,
        validator: DecisionValidator,
        context_assembler: ContextAssembler,
        answer_port: SupervisorAnswerPort | None = None,
    ) -> None:
        self._resolver = resolver
        self._classifier = classifier
        self._validator = validator
        self._context_assembler = context_assembler
        self._answer_port = answer_port

    async def decide(
        self,
        evidence: SupervisorTurnEvidence,
    ) -> SupervisorDecisionResult:
        """组装已验证上下文，并在任何派发前完成最终校验。"""

        resolution = self._resolver.resolve(evidence.to_resolver_input())
        context = await self._assemble_context(evidence, resolution)
        classification_request = ActionClassificationRequest(
            turn_id=evidence.turn.turn_id,
            content=evidence.content,
            deterministic_resolution=resolution,
            candidates=_classification_candidates(evidence),
            context_summary=_context_summary(context),
        )

        if evidence.explicit_action is not None:
            decision = self._decision_from_explicit(evidence, resolution)
        elif resolution.status is DeterministicResolutionStatus.RESOLVED:
            decision = self._decision_from_resolution(evidence, resolution)
        else:
            decision, classification_request = await self._classify_or_clarify(
                evidence,
                classification_request,
            )

        validation_request = self._validation_request(
            evidence,
            decision,
            classification_request,
        )
        decision = self._validator.validate(validation_request)
        if decision.action is not AgentAction.ANSWER_ONLY:
            return SupervisorDecisionResult(
                decision=decision,
                validation_request=validation_request,
                context=context,
            )

        try:
            answer_message = await self._answer_message(decision, context)
        except Exception:
            decision = self._clarify_decision(
                evidence,
                reason_code="answer_model_unavailable_requires_clarification",
                question="暂时无法生成答复，请稍后重试或说明你希望了解的内容。",
            )
            classification_request = _clarification_classification_request(
                classification_request,
                reason_code=decision.reason_code,
            )
            validation_request = self._validation_request(
                evidence,
                decision,
                classification_request,
            )
            decision = self._validator.validate(validation_request)
            return SupervisorDecisionResult(
                decision=decision,
                validation_request=validation_request,
                context=context,
            )

        return SupervisorDecisionResult(
            decision=decision,
            validation_request=validation_request,
            context=context,
            answer_message=answer_message,
        )

    async def _assemble_context(
        self,
        evidence: SupervisorTurnEvidence,
        resolution: DeterministicResolution,
    ) -> ContextEnvelope:
        try:
            return await self._context_assembler.assemble(
                ContextRequest(
                    conversation_id=evidence.conversation_id,
                    user_id=evidence.user_id,
                    current_input=evidence.content,
                    target_workflow_id=resolution.target_workflow_id,
                    artifact_refs=list(evidence.artifact_refs),
                    expected_context_version=evidence.expected_context_version,
                )
            )
        except ValueError:
            raise SupervisorDecisionUnavailableError(
                "model_profile_invalid",
            ) from None

    def _decision_from_explicit(
        self,
        evidence: SupervisorTurnEvidence,
        resolution: DeterministicResolution,
    ) -> ActionDecision:
        signal = evidence.explicit_action
        assert signal is not None
        action = signal.action
        return ActionDecision(
            action=action,
            intent=signal.intent or resolution.intent,
            target_workflow_id=(
                signal.workflow_id or resolution.target_workflow_id
            ),
            target_stage=signal.stage or resolution.target_stage,
            target_artifact_ref=(
                signal.artifact_ref or resolution.target_artifact_ref
            ),
            confidence=1.0,
            clarification_question=(
                "请说明需要确认的具体内容。"
                if action is AgentAction.CLARIFY
                else None
            ),
            patch=(
                {}
                if action in _NON_MUTATING_ACTIONS
                else dict(signal.patch)
            ),
            reason_code=resolution.reason_code,
            idempotency_key=_idempotency_key(evidence),
        )

    def _decision_from_resolution(
        self,
        evidence: SupervisorTurnEvidence,
        resolution: DeterministicResolution,
    ) -> ActionDecision:
        if resolution.action is None:
            return self._clarify_decision(
                evidence,
                reason_code="deterministic_resolution_incomplete",
                question="请说明要执行的操作。",
                intent=resolution.intent,
            )
        return ActionDecision(
            action=resolution.action,
            intent=resolution.intent,
            target_workflow_id=resolution.target_workflow_id,
            target_stage=resolution.target_stage,
            target_artifact_ref=resolution.target_artifact_ref,
            confidence=1.0,
            clarification_question=(
                "请说明需要确认的具体内容。"
                if resolution.action is AgentAction.CLARIFY
                else None
            ),
            patch={},
            reason_code=resolution.reason_code,
            idempotency_key=_idempotency_key(evidence),
        )

    async def _classify_or_clarify(
        self,
        evidence: SupervisorTurnEvidence,
        request: ActionClassificationRequest,
    ) -> tuple[ActionDecision, ActionClassificationRequest]:
        if self._classifier is not None:
            try:
                return await self._classifier.classify(request), request
            except Exception:
                pass
        decision = self._clarify_decision(
            evidence,
            reason_code="classifier_unavailable_requires_clarification",
            question="我还不能确定你的操作意图，请明确要处理哪个任务或产物。",
            intent=request.deterministic_resolution.intent,
        )
        safe_request = _clarification_classification_request(
            request,
            reason_code=decision.reason_code,
        )
        return decision, safe_request

    def _validation_request(
        self,
        evidence: SupervisorTurnEvidence,
        decision: ActionDecision,
        classification_request: ActionClassificationRequest,
    ) -> DecisionValidationRequest:
        return DecisionValidationRequest(
            decision=decision,
            classification_request=classification_request,
            current_candidates=_classification_candidates(evidence),
            allowed_global_actions=_GLOBAL_ACTIONS,
            expected_context_version=evidence.expected_context_version,
            current_context_version=evidence.authoritative_context_version,
        )

    async def _answer_message(
        self,
        decision: ActionDecision,
        context: ContextEnvelope,
    ) -> AIMessage:
        if self._answer_port is None:
            raise RuntimeError("只读回答端口未配置")
        content = await self._answer_port.answer(context)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("只读回答不能为空")
        return AIMessage(
            content=content,
            id=f"assistant:{decision.idempotency_key}",
            tool_calls=[],
        )

    @staticmethod
    def _clarify_decision(
        evidence: SupervisorTurnEvidence,
        *,
        reason_code: str,
        question: str,
        intent: AgentIntent = AgentIntent.GENERAL,
    ) -> ActionDecision:
        return ActionDecision(
            action=AgentAction.CLARIFY,
            intent=intent,
            confidence=1.0,
            requires_confirmation=True,
            clarification_question=question,
            patch={},
            reason_code=reason_code,
            idempotency_key=_idempotency_key(evidence),
        )


def _idempotency_key(evidence: SupervisorTurnEvidence) -> str:
    return f"decision:{evidence.turn.turn_id}"


def _intent(workflow: WorkflowRecord) -> AgentIntent:
    return AgentIntent(workflow.kind.value)


def _resolver_candidates(
    evidence: SupervisorTurnEvidence,
) -> tuple[ResolverCandidate, ...]:
    candidates: list[ResolverCandidate] = []
    workflows_by_id = {
        workflow.workflow_id: workflow for workflow in evidence.workflows
    }
    for workflow in evidence.workflows:
        candidates.append(
            ResolverCandidate(
                workflow_id=workflow.workflow_id,
                intent=_intent(workflow),
                stage=workflow.current_stage,
            )
        )
        candidates.extend(
            ResolverCandidate(
                workflow_id=workflow.workflow_id,
                intent=_intent(workflow),
                stage=workflow.current_stage,
                artifact_ref=artifact_ref,
            )
            for artifact_ref in workflow.latest_artifact_refs
        )

    for message in evidence.visible_messages:
        workflow_id = _optional_string(message.get("workflow_id"))
        workflow = workflows_by_id.get(workflow_id or "")
        if workflow is None:
            continue
        message_id = _optional_string(
            message.get("message_id") or message.get("id")
        )
        stage = _optional_string(message.get("stage")) or workflow.current_stage
        mention_ref = _optional_string(message.get("mention_ref"))
        artifact_refs = _message_artifact_refs(message)
        if not artifact_refs:
            candidates.append(
                ResolverCandidate(
                    workflow_id=workflow.workflow_id,
                    intent=_intent(workflow),
                    stage=stage,
                    message_id=message_id,
                    mention_ref=mention_ref,
                )
            )
            continue
        candidates.extend(
            ResolverCandidate(
                workflow_id=workflow.workflow_id,
                intent=_intent(workflow),
                stage=stage,
                message_id=message_id,
                artifact_ref=artifact_ref,
                mention_ref=mention_ref,
            )
            for artifact_ref in artifact_refs
        )
    return tuple(candidates)


def _classification_candidates(
    evidence: SupervisorTurnEvidence,
) -> tuple[ActionClassificationCandidate, ...]:
    return tuple(
        ActionClassificationCandidate(
            workflow_id=workflow.workflow_id,
            intent=_intent(workflow),
            status=workflow.status,
            current_stage=workflow.current_stage,
            stage_version=workflow.stage_version,
            context_version=workflow.context_version,
            allowed_actions=_WORKFLOW_ACTIONS_BY_STATUS[workflow.status],
            targets=_classification_targets(evidence, workflow),
        )
        for workflow in evidence.workflows
    )


def _classification_targets(
    evidence: SupervisorTurnEvidence,
    workflow: WorkflowRecord,
) -> tuple[ActionClassificationTarget, ...]:
    target_pairs: list[tuple[str | None, str | None]] = [
        (workflow.current_stage, artifact_ref)
        for artifact_ref in workflow.latest_artifact_refs
    ]
    if not target_pairs:
        target_pairs.append((workflow.current_stage, None))
    for message in evidence.visible_messages:
        if _optional_string(message.get("workflow_id")) != workflow.workflow_id:
            continue
        stage = _optional_string(message.get("stage")) or workflow.current_stage
        artifact_refs = _message_artifact_refs(message)
        if artifact_refs:
            target_pairs.extend((stage, artifact_ref) for artifact_ref in artifact_refs)
        else:
            target_pairs.append((stage, None))
    return tuple(
        ActionClassificationTarget(
            target_stage=stage,
            target_artifact_ref=artifact_ref,
        )
        for stage, artifact_ref in dict.fromkeys(target_pairs)
    )


def _message_artifact_refs(
    message: dict[str, JsonValue],
) -> tuple[str, ...]:
    refs: list[str] = []
    direct = _optional_string(message.get("artifact_ref"))
    if direct is not None:
        refs.append(direct)
    raw_refs = message.get("artifact_refs")
    if isinstance(raw_refs, list):
        refs.extend(
            item.strip()
            for item in raw_refs
            if isinstance(item, str) and item.strip()
        )
    return tuple(dict.fromkeys(refs))


def _optional_string(value: JsonValue | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _context_summary(context: ContextEnvelope) -> str:
    if context.conversation_summary is None:
        return ""
    return context.conversation_summary.model_dump_json()


def _clarification_classification_request(
    request: ActionClassificationRequest,
    *,
    reason_code: str,
) -> ActionClassificationRequest:
    resolution = DeterministicResolution(
        status=DeterministicResolutionStatus.AMBIGUOUS,
        intent=request.deterministic_resolution.intent,
        reason_code=reason_code,
        candidate_workflow_ids=tuple(
            candidate.workflow_id for candidate in request.candidates
        ),
    )
    return request.model_copy(
        update={"deterministic_resolution": resolution},
    )


__all__ = [
    "SupervisorAnswerPort",
    "SupervisorDecisionResult",
    "SupervisorDecisionService",
    "SupervisorDecisionUnavailableError",
    "SupervisorTurnEvidence",
]
