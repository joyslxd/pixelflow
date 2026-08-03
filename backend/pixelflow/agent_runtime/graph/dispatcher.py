"""把 Supervisor 结构化决策派发到唯一目标 Workflow。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
)

from .namespaces import GraphExecutionNamespace, workflow_namespace
from .registry import WorkflowRegistry

if TYPE_CHECKING:
    from pixelflow.agent_workflows.video.live_handler import WorkflowDispatchResult

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
    user_id: str
    turn_id: str
    current_input: str
    materials: list[dict[str, Any]]
    reply_to_message_id: str | None
    artifact_refs: list[str]
    source_interrupt_id: str | None = None


@dataclass(frozen=True, slots=True)
class _LegacyWorkflowDispatchResult:
    """给旧 Handler 提供与 live 结果同形的只读兼容外壳。"""

    state: None
    workflow: WorkflowRecord
    messages: tuple[()] = ()
    interrupt: None = None
    turn_status: TurnStatus = TurnStatus.COMPLETED
    update_active_workflow: bool = False
    active_workflow_id: str | None = None


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
        """保持旧入口只返回 Workflow 投影。"""

        result = await self.dispatch_result(
            state,
            decision,
            preallocated_workflow_id=preallocated_workflow_id,
        )
        return result.workflow.model_copy(deep=True)

    async def dispatch_result(
        self,
        state: Mapping[str, Any],
        decision: ActionDecision,
        *,
        preallocated_workflow_id: str | None = None,
    ) -> WorkflowDispatchResult | _LegacyWorkflowDispatchResult:
        """派发业务命令，并保留 live Handler 的完整结果。"""

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

        # R2 视频 Handler 必须消费完整 Turn 与附件上下文；尚未进入 R2 的其他
        # Workflow 继续兼容 M02 的纯路由内核调用，后续阶段接入时再提升为必填。
        context_text = _required_text if kind is WorkflowKind.VIDEO else _text_or_empty
        user_id = context_text(state, "user_id")
        turn_id = context_text(state, "turn_id")
        current_input = context_text(state, "current_input")
        materials = _materials_snapshot(state.get("materials", []))
        reply_to_message_id = _optional_text(
            state.get("reply_to_message_id"),
            "reply_to_message_id",
        )
        artifact_refs = _artifact_refs_snapshot(
            state.get("artifact_refs", []),
        )
        source_interrupt_id = _optional_text(
            state.get("source_interrupt_id"),
            "source_interrupt_id",
        )

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
            user_id=user_id,
            turn_id=turn_id,
            current_input=current_input,
            materials=materials,
            reply_to_message_id=reply_to_message_id,
            artifact_refs=artifact_refs,
            source_interrupt_id=source_interrupt_id,
        )
        handler = self._registry.resolve(kind)
        raw_result = await handler.dispatch(command)
        from pixelflow.agent_workflows.video.live_handler import (
            WorkflowDispatchResult,
        )

        if isinstance(raw_result, WorkflowDispatchResult):
            normalized_live_result = WorkflowDispatchResult.model_validate(
                raw_result.model_dump(mode="python")
            )
            _validate_result_identity(command, normalized_live_result.workflow)
            return normalized_live_result
        normalized_workflow = WorkflowRecord.model_validate(raw_result).model_copy(
            deep=True
        )
        _validate_result_identity(command, normalized_workflow)
        return _LegacyWorkflowDispatchResult(
            state=None,
            workflow=normalized_workflow,
        )


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


def _required_text(state: Mapping[str, Any], field_name: str) -> str:
    """读取 Handler 必需的 Turn 字段，拒绝缺失或空白值。"""

    value = state.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Supervisor state 必须提供 {field_name}")
    return value


def _text_or_empty(state: Mapping[str, Any], field_name: str) -> str:
    """兼容只测试路由内核的旧调用；存在字段时仍拒绝非法类型。"""

    value = state.get(field_name)
    if value is None:
        return ""
    return _required_text(state, field_name)


def _optional_text(value: Any, field_name: str) -> str | None:
    """复制可选文本引用，避免把非法目标交给业务 Handler。"""

    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Supervisor state 的 {field_name} 必须是非空字符串")
    return value


def _materials_snapshot(value: Any) -> list[dict[str, Any]]:
    """深拷贝首轮附件，使 Handler 看到与可见消息相同的素材快照。"""

    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Supervisor state 的 materials 必须是对象数组")
    return deepcopy(value)


def _artifact_refs_snapshot(value: Any) -> list[str]:
    """校验并复制 Artifact 引用，禁止静默丢弃非法目标。"""

    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip()
        for item in value
    ):
        raise ValueError("Supervisor state 的 artifact_refs 必须是非空字符串数组")
    return list(value)
