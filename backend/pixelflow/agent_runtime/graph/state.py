"""Supervisor 图状态及其工作流投影 reducer。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from pixelflow.agent_runtime.contracts import ActionDecision, WorkflowRecord

WorkflowRecordLike = WorkflowRecord | Mapping[str, Any]


def _copy_workflow_record(record: WorkflowRecordLike) -> WorkflowRecord:
    """校验并深拷贝工作流投影，避免 reducer 结果共享可变引用。"""

    if isinstance(record, WorkflowRecord):
        return record.model_copy(deep=True)
    return WorkflowRecord.model_validate(record).model_copy(deep=True)


def _validate_workflow_key(key: str, record: WorkflowRecord) -> None:
    """保证投影 Map 的键与合同内 workflow_id 一致。"""

    if key != record.workflow_id:
        raise ValueError("工作流投影键必须与 workflow_id 一致")


def merge_workflow_records(
    existing: Mapping[str, WorkflowRecordLike] | None,
    updates: Mapping[str, WorkflowRecordLike] | None,
) -> dict[str, WorkflowRecord]:
    """按 workflow_id 合并投影，并拒绝修改已有工作流身份。"""

    merged: dict[str, WorkflowRecord] = {}
    for key, record in (existing or {}).items():
        normalized = _copy_workflow_record(record)
        _validate_workflow_key(key, normalized)
        merged[key] = normalized

    for key, record in (updates or {}).items():
        normalized = _copy_workflow_record(record)
        _validate_workflow_key(key, normalized)
        current = merged.get(key)
        if current is not None:
            if current.conversation_id != normalized.conversation_id:
                raise ValueError("已有工作流的 conversation_id 不可变更")
            if current.kind != normalized.kind:
                raise ValueError("已有工作流的 kind 不可变更")
        merged[key] = normalized

    return merged


class SupervisorState(TypedDict, total=False):
    """统一会话 Supervisor 的最小共享状态合同。"""

    conversation_id: str
    user_id: str
    turn_id: str
    run_id: str
    current_input: str
    materials: list[dict[str, Any]]
    reply_to_message_id: str | None
    artifact_refs: list[str]
    context_version: int
    messages: Annotated[list[AnyMessage], add_messages]
    workflows: Annotated[
        dict[str, WorkflowRecord],
        merge_workflow_records,
    ]
    active_workflow_id: str | None
    decision: ActionDecision | None
    decision_validation_request: Any
    answer_message: AnyMessage | None
    dispatch_workflow_id: str | None
    workflow_dispatch_result: dict[str, Any] | None
    last_interrupt_response_id: str | None
    source_interrupt_id: str | None
