"""live Turn、人工响应与公开中断投影合同。"""

from datetime import datetime
from uuid import UUID

from pydantic import Field, JsonValue

from .base import ContractModel
from .enums import AgentAction, AgentIntent


class ExplicitActionSignal(ContractModel):
    """保存按钮或人工决策控件提交的结构化动作。"""

    action: AgentAction
    intent: AgentIntent | None = None
    workflow_id: str | None = Field(default=None, min_length=1)
    stage: str | None = Field(default=None, min_length=1)
    artifact_ref: str | None = Field(default=None, min_length=1)
    patch: dict[str, JsonValue] = Field(default_factory=dict)


class InterruptResponseValue(ContractModel):
    """恢复原 Turn 时交给 Graph 的权威人工响应值。"""

    content: str = Field(min_length=1)
    materials: list[dict[str, JsonValue]] = Field(default_factory=list)
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    explicit_action: ExplicitActionSignal | None = None


class InterruptResponseRequest(ContractModel):
    """保持 M12 外壳并按响应 ID 幂等恢复原 Turn。"""

    client_response_id: UUID
    value: InterruptResponseValue


class AgentInterruptProjection(ContractModel):
    """Snapshot 与 SSE 对外公开的中断投影。"""

    interrupt_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    workflow_id: str | None = Field(default=None, min_length=1)
    turn_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    opened_at: datetime


__all__ = [
    "AgentInterruptProjection",
    "ExplicitActionSignal",
    "InterruptResponseRequest",
    "InterruptResponseValue",
]
