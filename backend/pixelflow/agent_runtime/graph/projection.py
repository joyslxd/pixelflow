"""Workflow 结果到 Supervisor 投影更新的 LangGraph Command 转换。"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Any

from langgraph.types import Command, Send

from pixelflow.agent_runtime.contracts import WorkflowRecord


def workflow_projection_command(
    workflow: WorkflowRecord,
    *,
    conversation_id: str,
    goto: Send | Sequence[Send | Hashable] | Hashable = (),
) -> Command:
    """深拷贝 Workflow 结果，并让投影更新先于后续节点生效。"""

    normalized = workflow.model_copy(deep=True)
    if normalized.conversation_id != conversation_id:
        raise ValueError("Workflow 投影的 conversation_id 与当前会话不一致")
    update: dict[str, Any] = {
        "workflows": {
            normalized.workflow_id: normalized,
        },
    }
    return Command(update=update, goto=goto)
