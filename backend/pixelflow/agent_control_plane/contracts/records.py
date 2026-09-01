"""会话 Agent 的 Workflow、Turn 与外部任务投影合同。"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel
from .enums import ExternalJobStatus, TurnStatus, WorkflowKind, WorkflowStatus


class ExternalJobRef(ContractModel):
    """恢复时只查询原任务、绝不重新启动供应商任务的稳定引用。"""

    job_id: str = Field(min_length=1)
    provider_job_id: str | None = Field(default=None, min_length=1)
    workflow_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    status: ExternalJobStatus
    attempt: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    next_poll_at: datetime | None = None
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_expires_at: datetime | None = None


class WorkflowRecord(ContractModel):
    """面向查询、恢复和前端 Snapshot 的 Workflow 业务投影。"""

    workflow_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    kind: WorkflowKind
    status: WorkflowStatus
    current_stage: str = Field(min_length=1)
    stage_version: int = Field(ge=1)
    creation_contract_snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    pending_external_job: ExternalJobRef | None = None
    latest_artifact_refs: list[str] = Field(default_factory=list)
    context_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_pending_job_to_belong_to_workflow(self) -> Self:
        """阻止把其他 Workflow 的计费任务错误挂到当前投影。"""

        if self.pending_external_job is not None and self.pending_external_job.workflow_id != self.workflow_id:
            raise ValueError("pending_external_job.workflow_id must match workflow_id")
        return self


class TurnRecord(ContractModel):
    """按 ``conversation_id + client_input_id`` 幂等保存的用户输入记录。"""

    turn_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    client_input_id: UUID
    status: TurnStatus
    target_workflow_id: str | None = Field(default=None, min_length=1)
    expected_context_version: int = Field(ge=0)
    created_at: datetime
