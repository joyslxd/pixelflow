"""定义不泄露 Harness 私有类型的 Sidecar 稳定 DTO。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """拒绝未知字段，避免协议升级时静默丢失安全语义。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStatus(StrEnum):
    """表示 Sidecar 对外公开的 Run 生命周期状态。"""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUSPENDED_OPERATION = "suspended_operation"
    SUSPENDED_CONFIRMATION = "suspended_confirmation"
    SUSPENDED_AUTHORIZATION = "suspended_authorization"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TerminationReason(StrEnum):
    """表示终态或挂起态的固定安全原因。"""

    COMPLETED = "completed"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    MAX_MODEL_STEPS = "max_model_steps"
    MAX_BUSINESS_TOOLS = "max_business_tools"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    ENGINE_ERROR = "engine_error"
    CANCELLED = "cancelled"
    SUSPENDED_OPERATION = "suspended_operation"
    SUSPENDED_CONFIRMATION = "suspended_confirmation"
    SUSPENDED_AUTHORIZATION = "suspended_authorization"


class RunTrigger(StrictModel):
    """绑定能够稳定去重的 PixelFlow 触发事件。"""

    type: Literal["user_turn", "confirmation_resume", "operation_resume", "quota_resume", "run_recovery"]
    trigger_id: str = Field(min_length=1, max_length=200)


class RunBinding(StrictModel):
    """保存 Sidecar 可见但不可反推用户身份的业务引用。"""

    conversation_ref: str = Field(pattern=r"^opaque:", max_length=300)
    workspace_ref: str = Field(pattern=r"^opaque:", max_length=300)
    workspace_revision: int = Field(ge=0)
    context_digest: str = Field(pattern=r"^sha256:", max_length=80)


class ModelProfile(StrictModel):
    """固定本 Run 可使用的模型路由档案。"""

    profile_name: str = Field(min_length=1, max_length=120)
    profile_digest: str = Field(pattern=r"^sha256:", max_length=80)
    max_output_tokens: int = Field(gt=0, le=131_072)


class ContextBudget(StrictModel):
    """传递 PixelFlow 已验证的唯一上下文预算快照。"""

    effective_context_k: int = Field(gt=0)
    output_reserve_k: int = Field(gt=0)
    safety_reserve_k: int = Field(gt=0)
    require_verified_model_profile: bool
    policy_digest: str = Field(pattern=r"^sha256:", max_length=80)


class RunLimits(StrictModel):
    """限定单次 Agent loop 的公开资源上限。"""

    profile: str = Field(default="legacy_test_v1", min_length=1, max_length=120)
    digest: str = Field(default="sha256:" + "0" * 64, pattern=r"^sha256:[a-f0-9]{64}$")
    max_model_steps: int = Field(gt=0, le=64)
    max_business_tools: int = Field(ge=0, le=32)
    max_billable_batch_starts: int = Field(default=0, ge=0, le=8)
    deadline_seconds: int = Field(gt=0, le=3_600)


class ToolsetRef(StrictModel):
    """引用在 Run 接受时冻结的 Tool Manifest。"""

    version: str = Field(min_length=1, max_length=120)
    manifest_digest: str = Field(pattern=r"^sha256:", max_length=80)


class RunContext(StrictModel):
    """承载 PixelFlow 清洗后的模型输入投影。"""

    system_instruction: str = Field(min_length=1, max_length=32_000)
    user_input: str = Field(min_length=1, max_length=32_000)
    workspace_projection: dict[str, Any]
    conversation_projection: dict[str, Any]
    preference_projection: dict[str, Any]
    brand_profile_projection: dict[str, Any]
    long_term_memory_projection: list[dict[str, Any]]


class HarnessRunRequest(StrictModel):
    """表示 PixelFlow 发给 Sidecar 的稳定创建 Run 请求。"""

    protocol_version: Literal["v1"]
    run_request_key: str = Field(pattern=r"^sha256:", max_length=80)
    request_digest: str = Field(pattern=r"^sha256:", max_length=80)
    session_id: str = Field(pattern=r"^pfh_", max_length=200)
    trigger: RunTrigger
    binding: RunBinding
    model: ModelProfile
    context_budget: ContextBudget
    limits: RunLimits
    toolset: ToolsetRef
    context: RunContext


class HarnessRunHandle(StrictModel):
    """表示 Sidecar 已接受请求后的不可变身份。"""

    run_id: str = Field(pattern=r"^hrun_", max_length=200)
    status: RunStatus
    engine_id: str = Field(min_length=1, max_length=120)
    engine_version: str = Field(min_length=1, max_length=120)
    skill_catalog_digest: str = Field(pattern=r"^sha256:", max_length=80)


class HarnessRunEvent(StrictModel):
    """表示可断点消费的 Sidecar 稳定事件。"""

    protocol_version: Literal["v1"]
    run_id: str = Field(pattern=r"^hrun_", max_length=200)
    event_id: str = Field(pattern=r"^hevt_", max_length=200)
    sequence: int = Field(gt=0)
    type: str = Field(pattern=r"^(run|tool|response|public_summary)\.[a-z_]+$", max_length=120)
    occurred_at: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


class HarnessRunState(StrictModel):
    """表示内部 HTTP API 返回的可查询 Run 状态，不包含模型或用户原文。"""

    protocol_version: Literal["v1"]
    run_id: str = Field(pattern=r"^hrun_", max_length=200)
    status: RunStatus
    termination_reason: TerminationReason | None = None
    engine_id: str = Field(min_length=1, max_length=120)
    engine_version: str = Field(min_length=1, max_length=120)
    skill_catalog_digest: str = Field(pattern=r"^sha256:", max_length=80)
    accepted_at: str = Field(min_length=1, max_length=64)
    completed_at: str | None = Field(default=None, max_length=64)
