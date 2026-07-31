"""新会话 Runtime API 与 Port 入口使用的请求合同。"""

from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator

from .base import ContractModel
from .enums import OrchestrationMode
from .live import ExplicitActionSignal, validate_strict_json_array


class ConversationOrchestration(ContractModel):
    """服务端拥有的对话编排归属快照。"""

    orchestration_mode: OrchestrationMode
    orchestration_version: Literal[1] = 1


class TurnStartRequest(ContractModel):
    """原子保存用户输入并创建或复用 Turn 的请求。"""

    client_input_id: UUID
    content: str = Field(min_length=1)
    materials: list[dict[str, JsonValue]] = Field(default_factory=list)
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    expected_context_version: int = Field(ge=0)
    explicit_action: ExplicitActionSignal | None = None

    _validate_materials = field_validator("materials", mode="before")(
        validate_strict_json_array
    )


class OperationRequest(ContractModel):
    """按工作流阶段和请求摘要幂等领取外部 Operation。"""

    workflow_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    stage_version: int = Field(ge=1)
    attempt: int = Field(ge=1)
    request_hash: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
