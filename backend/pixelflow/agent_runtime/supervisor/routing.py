"""把已分类决策在 Validator 之后分流到安全图分支。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.messages import AIMessage
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    InterruptResponseRequest,
    WorkflowRecord,
)
from pixelflow.agent_runtime.identity import interrupt_id as stable_interrupt_id

from .validator import (
    DecisionValidationError,
    DecisionValidationRequest,
    DecisionValidator,
)

ROUTE_ACTION_NODE = "route_action"
ANSWER_ONLY_NODE = "answer_only"
CLARIFICATION_NODE = "clarification"
WORKFLOW_COMMAND_NODE = "dispatch_workflow"


class SupervisorRoutingError(ValueError):
    """返回不包含消息正文或内部状态详情的图路由错误。"""

    def __init__(self, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"Supervisor 图路由失败：{reason_code}")


@dataclass(frozen=True, slots=True)
class SupervisorActionRouter:
    """先执行最后一道校验，再选择非业务或 Workflow 图分支。"""

    validator: DecisionValidator = field(default_factory=DecisionValidator)

    def route(self, state: Mapping[str, Any]) -> Command:
        """返回仅可到达三个冻结动作分支之一的 LangGraph Command。"""

        decision_value = state.get("decision")
        request_value = state.get("decision_validation_request")
        if decision_value is None or request_value is None:
            raise SupervisorRoutingError(
                reason_code="validation_request_required",
            )
        decision = _parse_decision(decision_value)
        request = _parse_validation_request(request_value)
        if decision != request.decision:
            raise DecisionValidationError(
                reason_code="decision_snapshot_conflict",
            )
        validated = self.validator.validate(request)
        _validate_request_binding(state, request)
        answer_message_update: AIMessage | None = None
        response_id = state.get("last_interrupt_response_id")
        if response_id is not None:
            try:
                normalized_response_id = str(UUID(str(response_id)))
            except (TypeError, ValueError, AttributeError):
                raise SupervisorRoutingError(
                    reason_code="invalid_clarification_response_id",
                ) from None
            original_key = validated.idempotency_key
            validated = validated.model_copy(
                update={
                    "idempotency_key": f"decision:{normalized_response_id}",
                },
                deep=True,
            )
            if validated.action is AgentAction.ANSWER_ONLY:
                source_answer = state.get("answer_message")
                if (
                    not isinstance(source_answer, AIMessage)
                    or source_answer.id != f"assistant:{original_key}"
                ):
                    raise SupervisorRoutingError(
                        reason_code="answer_message_required",
                    )
                answer_message_update = source_answer.model_copy(
                    update={"id": f"assistant:{validated.idempotency_key}"},
                    deep=True,
                )
        dispatch_workflow_id: str | None = None
        if validated.action == AgentAction.START_WORKFLOW:
            if validated.target_workflow_id is not None or validated.target_stage is not None or validated.target_artifact_ref is not None:
                raise DecisionValidationError(
                    reason_code="start_workflow_target_reference_forbidden",
                )
            dispatch_workflow_id = _stable_workflow_id(
                state,
                validated,
            )
        elif validated.target_workflow_id is not None:
            dispatch_workflow_id = validated.target_workflow_id

        if validated.action == AgentAction.ANSWER_ONLY:
            destination = ANSWER_ONLY_NODE
        elif validated.action == AgentAction.CLARIFY:
            destination = CLARIFICATION_NODE
        else:
            destination = WORKFLOW_COMMAND_NODE
        update: dict[str, Any] = {
                "decision": validated.model_copy(deep=True),
                "dispatch_workflow_id": dispatch_workflow_id,
            }
        if answer_message_update is not None:
            update["answer_message"] = answer_message_update
        return Command(
            update=update,
            goto=destination,
        )

    def save_answer_only(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """只追加已准备的助手消息，不改写任何 Workflow 业务投影。"""

        decision = _decision_for_action(
            state,
            expected=AgentAction.ANSWER_ONLY,
        )
        message = state.get("answer_message")
        if not isinstance(message, AIMessage) or not isinstance(message.id, str) or not message.id.strip() or not isinstance(message.content, str) or not message.content.strip():
            raise SupervisorRoutingError(
                reason_code="answer_message_required",
            )
        if message.id != f"assistant:{decision.idempotency_key}":
            raise SupervisorRoutingError(
                reason_code="answer_message_id_conflict",
            )
        if message.tool_calls or message.invalid_tool_calls:
            raise SupervisorRoutingError(
                reason_code="answer_message_tool_call_forbidden",
            )
        if decision.patch:
            raise SupervisorRoutingError(
                reason_code="answer_only_patch_forbidden",
            )
        return {
            "messages": [deepcopy(message)],
            "answer_message": None,
        }

    def open_clarification(
        self,
        state: Mapping[str, Any],
    ) -> Command:
        """打开全局追问，并只接受 Executor 构造的严格恢复信封。"""

        decision = _decision_for_action(
            state,
            expected=AgentAction.CLARIFY,
        )
        response = interrupt(
            {
                "type": "clarification",
                "question": decision.clarification_question,
                "reason_code": decision.reason_code,
                "idempotency_key": decision.idempotency_key,
            }
        )
        return _clarification_resume_command(
            state,
            source_decision=decision,
            response=response,
        )


def _clarification_resume_command(
    state: Mapping[str, Any],
    *,
    source_decision: ActionDecision,
    response: Any,
) -> Command:
    """复验全局追问恢复身份，并把新证据送回统一 Validator。"""

    expected_keys = {
        "answer_message",
        "client_response_id",
        "decision",
        "decision_validation_request",
        "interrupt_id",
        "resume_context_version",
        "source_decision_idempotency_key",
        "value",
    }
    if type(response) is not dict or set(response) != expected_keys:
        raise SupervisorRoutingError(
            reason_code="invalid_clarification_resume_envelope",
        )
    resume_context_version = response["resume_context_version"]
    if type(resume_context_version) is not int or resume_context_version < 0:
        raise SupervisorRoutingError(
            reason_code="invalid_clarification_resume_context_version",
        )
    checkpoint_context_version = state.get("context_version")
    if (
        type(checkpoint_context_version) is not int
        or checkpoint_context_version < 0
    ):
        raise SupervisorRoutingError(
            reason_code="clarification_context_state_corrupted",
        )
    if resume_context_version < checkpoint_context_version:
        raise SupervisorRoutingError(
            reason_code="clarification_resume_context_rollback",
        )
    turn_id = state.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        raise SupervisorRoutingError(reason_code="turn_id_required")
    previous_response_id = state.get("last_interrupt_response_id")
    if previous_response_id is not None and (
        not isinstance(previous_response_id, str)
        or not previous_response_id.strip()
    ):
        raise SupervisorRoutingError(
            reason_code="clarification_response_state_corrupted",
        )
    identity_turn_id = (
        turn_id
        if previous_response_id is None
        else f"{turn_id}:{previous_response_id}"
    )
    expected_interrupt_id = stable_interrupt_id(
        identity_turn_id,
        source_decision.reason_code,
    )
    if (
        response["interrupt_id"] != expected_interrupt_id
        or response["source_decision_idempotency_key"]
        != source_decision.idempotency_key
    ):
        raise SupervisorRoutingError(
            reason_code="clarification_resume_identity_conflict",
        )
    try:
        request = InterruptResponseRequest.model_validate(
            {
                "client_response_id": response["client_response_id"],
                "value": response["value"],
            }
        )
        decision = ActionDecision.model_validate(response["decision"])
        validation_request = DecisionValidationRequest.model_validate(
            response["decision_validation_request"]
        )
    except ValidationError:
        raise SupervisorRoutingError(
            reason_code="invalid_clarification_resume_contract",
        ) from None
    if (
        validation_request.decision != decision
        or validation_request.classification_request.turn_id != turn_id
        or validation_request.classification_request.content
        != request.value.content
    ):
        raise SupervisorRoutingError(
            reason_code="clarification_resume_evidence_conflict",
        )
    if (
        validation_request.expected_context_version
        != resume_context_version
        or validation_request.current_context_version
        != resume_context_version
    ):
        raise SupervisorRoutingError(
            reason_code="clarification_resume_context_conflict",
        )
    answer_message = response["answer_message"]
    if decision.action is AgentAction.ANSWER_ONLY:
        if not isinstance(answer_message, AIMessage):
            raise SupervisorRoutingError(
                reason_code="answer_message_required",
            )
    elif answer_message is not None:
        raise SupervisorRoutingError(
            reason_code="unexpected_answer_message",
        )
    return Command(
        update={
            "current_input": request.value.content,
            "materials": [dict(item) for item in request.value.materials],
            "reply_to_message_id": request.value.reply_to_message_id,
            "artifact_refs": list(request.value.artifact_refs),
            "context_version": resume_context_version,
            "decision": decision.model_copy(deep=True),
            "decision_validation_request": validation_request.model_copy(deep=True),
            "answer_message": deepcopy(answer_message),
            "dispatch_workflow_id": None,
            "workflow_dispatch_result": None,
            "last_interrupt_response_id": str(request.client_response_id),
        },
        goto=ROUTE_ACTION_NODE,
    )


def _decision_for_action(
    state: Mapping[str, Any],
    *,
    expected: AgentAction,
) -> ActionDecision:
    """校验分支拿到的仍是路由节点批准的同类决策。"""

    value = state.get("decision")
    if value is None:
        raise SupervisorRoutingError(reason_code="validated_decision_required")
    decision = _parse_decision(value)
    if decision.action != expected:
        raise SupervisorRoutingError(reason_code="route_action_conflict")
    return decision


def _validate_request_binding(
    state: Mapping[str, Any],
    request: DecisionValidationRequest,
) -> None:
    """把调用方提供的校验请求重新绑定到当前图 Turn 和投影。"""

    if state.get("turn_id") != request.classification_request.turn_id:
        raise DecisionValidationError(reason_code="turn_id_conflict")
    if state.get("context_version") != request.current_context_version:
        raise DecisionValidationError(
            reason_code="context_version_state_conflict",
        )
    current_input = state.get("current_input")
    if not isinstance(current_input, str) or current_input.strip() != request.classification_request.content:
        raise DecisionValidationError(
            reason_code="current_input_conflict",
        )
    workflows = state.get("workflows")
    if not isinstance(workflows, Mapping):
        raise DecisionValidationError(
            reason_code="workflow_projection_conflict",
        )
    conversation_id = state.get("conversation_id")
    for candidate in request.current_candidates:
        value = workflows.get(candidate.workflow_id)
        if value is None:
            raise DecisionValidationError(
                reason_code="workflow_projection_conflict",
            )
        try:
            workflow = WorkflowRecord.model_validate(value)
        except ValidationError:
            raise SupervisorRoutingError(
                reason_code="invalid_workflow_projection",
            ) from None
        if (
            workflow.conversation_id != conversation_id
            or AgentIntent(workflow.kind.value) != candidate.intent
            or workflow.status != candidate.status
            or workflow.current_stage != candidate.current_stage
            or workflow.stage_version != candidate.stage_version
            or workflow.context_version != candidate.context_version
        ):
            raise DecisionValidationError(
                reason_code="workflow_projection_conflict",
            )


def _stable_workflow_id(
    state: Mapping[str, Any],
    decision: ActionDecision,
) -> str:
    """用会话和决策幂等键派生可重放的新 Workflow ID。"""

    conversation_id = state.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise SupervisorRoutingError(reason_code="conversation_id_required")
    stable_value = uuid5(
        NAMESPACE_URL,
        (f"pixelflow-agent-runtime/{conversation_id}/{decision.idempotency_key}"),
    )
    return f"wf_{stable_value.hex}"


def _parse_decision(value: Any) -> ActionDecision:
    """把决策解析错误归一为不含输入值的公开短码。"""

    try:
        return ActionDecision.model_validate(value)
    except ValidationError:
        raise SupervisorRoutingError(reason_code="invalid_decision") from None


def _parse_validation_request(value: Any) -> DecisionValidationRequest:
    """把校验请求解析错误归一为不含输入值的公开短码。"""

    try:
        return DecisionValidationRequest.model_validate(value)
    except ValidationError:
        raise SupervisorRoutingError(
            reason_code="invalid_validation_request",
        ) from None


__all__ = [
    "ANSWER_ONLY_NODE",
    "CLARIFICATION_NODE",
    "ROUTE_ACTION_NODE",
    "SupervisorActionRouter",
    "SupervisorRoutingError",
    "WORKFLOW_COMMAND_NODE",
]
