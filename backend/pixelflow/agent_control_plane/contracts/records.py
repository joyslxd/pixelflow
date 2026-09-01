"""会话 Agent 的 Workflow、Turn 与外部任务投影合同。"""

from datetime import datetime
from uuid import UUID

from pydantic import Field, JsonValue

from .base import ContractModel
from .enums import TurnStatus, WorkflowKind, WorkflowStatus


class WorkflowRecord(ContractModel):
    """面向查询、恢复和前端 Snapshot 的 Workflow 业务投影。"""

    workflow_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    kind: WorkflowKind
    status: WorkflowStatus
    current_stage: str = Field(min_length=1)
    stage_version: int = Field(ge=1)
    creation_contract_snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    latest_artifact_refs: list[str] = Field(default_factory=list)
    context_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

class TurnRecord(ContractModel):
    """按 ``conversation_id + client_input_id`` 幂等保存的用户输入记录。"""

    turn_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    client_input_id: UUID
    status: TurnStatus
    target_workflow_id: str | None = Field(default=None, min_length=1)
    expected_context_version: int = Field(ge=0)
    created_at: datetime
