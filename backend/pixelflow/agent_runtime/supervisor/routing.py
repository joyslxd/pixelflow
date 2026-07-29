"""把已分类决策在 Validator 之后分流到安全图分支。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from langchain_core.messages import AIMessage
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    WorkflowRecord,
)

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
        return Command(
            update={
                "decision": validated.model_copy(deep=True),
                "dispatch_workflow_id": dispatch_workflow_id,
            },
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
    ) -> dict[str, Any]:
        """打开可恢复追问中断，恢复后仍不修改 Workflow 业务状态。"""

        decision = _decision_for_action(
            state,
            expected=AgentAction.CLARIFY,
        )
        interrupt(
            {
                "type": "clarification",
                "question": decision.clarification_question,
                "reason_code": decision.reason_code,
                "idempotency_key": decision.idempotency_key,
            }
        )
        return {}


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
