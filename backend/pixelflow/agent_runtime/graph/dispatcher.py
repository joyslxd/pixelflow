"""把 Supervisor 结构化决策派发到唯一目标 Workflow。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    WorkflowKind,
    WorkflowRecord,
)

from .namespaces import GraphExecutionNamespace, workflow_namespace
from .registry import WorkflowRegistry

_NON_WORKFLOW_ACTIONS = {
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
}
_EXISTING_WORKFLOW_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
    AgentAction.SWITCH_WORKFLOW,
    AgentAction.CANCEL_WORKFLOW,
}
_INTENT_TO_WORKFLOW_KIND = {
    AgentIntent.IMAGE: WorkflowKind.IMAGE,
    AgentIntent.VIDEO: WorkflowKind.VIDEO,
    AgentIntent.PPT: WorkflowKind.PPT,
    AgentIntent.VIDEO_ANALYSIS: WorkflowKind.VIDEO_ANALYSIS,
}


@dataclass(frozen=True, slots=True)
class WorkflowCommand:
    """传给 Workflow 处理器的内部命令快照。"""

    conversation_id: str
    workflow_id: str
    kind: WorkflowKind
    decision: ActionDecision
    workflow: WorkflowRecord | None
    namespace: GraphExecutionNamespace


class WorkflowCommandDispatcher:
    """显式定位工作流、隔离对话并校验处理器返回身份。"""

    def __init__(self, registry: WorkflowRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        state: Mapping[str, Any],
        decision: ActionDecision,
        *,
        preallocated_workflow_id: str | None = None,
    ) -> WorkflowRecord:
        """派发一条业务命令，不负责更新 Supervisor 投影。"""

        normalized_decision = decision.model_copy(deep=True)
        if normalized_decision.action in _NON_WORKFLOW_ACTIONS:
            raise ValueError("非业务命令不可派发到 Workflow")

        target_workflow_id = normalized_decision.target_workflow_id
        if preallocated_workflow_id is not None:
            if normalized_decision.action != AgentAction.START_WORKFLOW or target_workflow_id is not None:
                raise ValueError("预分配 workflow_id 只允许用于无目标的新建动作")
            target_workflow_id = preallocated_workflow_id
        if target_workflow_id is None:
            raise ValueError("业务命令必须提供 target_workflow_id")

        conversation_id = state.get("conversation_id")
        if not isinstance(conversation_id, str):
            raise ValueError("Supervisor state 必须提供 conversation_id")

        workflows = state.get("workflows", {})
        if not isinstance(workflows, Mapping):
            raise ValueError("Supervisor state 的 workflows 必须是映射")

        target = workflows.get(target_workflow_id)
        action = normalized_decision.action
        if action == AgentAction.START_WORKFLOW:
            if target is not None:
                raise ValueError("start_workflow 的目标投影已存在")
            kind = _INTENT_TO_WORKFLOW_KIND.get(normalized_decision.intent)
            if kind is None:
                raise ValueError("start_workflow 的 intent 必须对应业务 Workflow")
            workflow = None
        elif action in _EXISTING_WORKFLOW_ACTIONS:
            if target is None:
                raise KeyError(f"目标 Workflow 不存在：{target_workflow_id}")
            workflow = _copy_target_workflow(
                target_workflow_id,
                target,
                conversation_id,
            )
            kind = workflow.kind
        else:
            raise ValueError(f"未支持的 Workflow 业务动作：{action}")

        command = WorkflowCommand(
            conversation_id=conversation_id,
            workflow_id=target_workflow_id,
            kind=kind,
            decision=normalized_decision,
            workflow=workflow,
            namespace=workflow_namespace(
                conversation_id,
                target_workflow_id,
            ),
        )
        handler = self._registry.resolve(kind)
        result = await handler.dispatch(command)
        normalized_result = WorkflowRecord.model_validate(result).model_copy(deep=True)
        _validate_result_identity(command, normalized_result)
        return normalized_result


def _copy_target_workflow(
    target_workflow_id: str,
    target: Any,
    conversation_id: str,
) -> WorkflowRecord:
    """复制并校验目标投影，避免处理器修改 Supervisor 原状态。"""

    workflow = WorkflowRecord.model_validate(target).model_copy(deep=True)
    if workflow.workflow_id != target_workflow_id:
        raise ValueError("工作流投影键必须与 workflow_id 一致")
    if workflow.conversation_id != conversation_id:
        raise ValueError("目标 Workflow 的 conversation_id 与当前会话不一致")
    return workflow


def _validate_result_identity(
    command: WorkflowCommand,
    result: WorkflowRecord,
) -> None:
    """阻止处理器把结果写入其他 Workflow、会话或业务类型。"""

    if result.workflow_id != command.workflow_id:
        raise ValueError("Workflow 处理器返回了不同的 workflow_id")
    if result.conversation_id != command.conversation_id:
        raise ValueError("Workflow 处理器返回了不同的 conversation_id")
    if result.kind != command.kind:
        raise ValueError("Workflow 处理器返回了不同的 kind")
