"""定义 Gateway 与 Agent Harness 之间框架无关的稳定 DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """拒绝未知字段，避免 Sidecar 协议在迁移期间静默漂移。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class HarnessRunRequest(_StrictModel):
    """Gateway 清洗后的 Run 输入；用户身份只允许留在 Gateway binding。"""

    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    workspace_revision: int = Field(ge=1)
    trigger_id: str = Field(min_length=1, max_length=200)
    trigger_type: Literal["user_turn", "operation_resume", "confirmation_resume", "run_recovery"] = "user_turn"
    user_input: str = Field(min_length=1, max_length=32_000)
    system_instruction: str = Field(min_length=1, max_length=32_000)
    context_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    model_profile_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    context_budget_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    run_limits_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    limit_profile: str = Field(min_length=1, max_length=120)
    max_model_steps: int = Field(ge=1, le=64)
    max_business_tools: int = Field(ge=0, le=32)
    max_billable_batch_starts: int = Field(ge=0, le=8)
    deadline_seconds: int = Field(ge=1, le=3_600)
    model_profile_name: str = Field(default="deepseek-v4-pro", min_length=1, max_length=120)
    max_output_tokens: int = Field(default=256, ge=1, le=131_072)
    # Gateway 本地的瞬时凭据票据；不会序列化到 Sidecar 请求、Run binding 或事件。
    transient_credential_grant_id: str | None = Field(default=None, min_length=1, max_length=128)
    workspace_projection: dict[str, Any] = Field(default_factory=dict)
    conversation_projection: dict[str, Any] = Field(default_factory=dict)
    preference_projection: dict[str, Any] = Field(default_factory=dict)
    brand_profile_projection: dict[str, Any] = Field(default_factory=dict)
    long_term_memory_projection: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class HarnessRunHandle(_StrictModel):
    """Sidecar 接受并完成 Gateway binding 后返回的稳定 Run 身份。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: str = Field(min_length=1, max_length=64)


class HarnessRunEvent(_StrictModel):
    """只供 Gateway 投影层消费的稳定事件，不可直接暴露给浏览器。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    event_id: str = Field(pattern=r"^hevt_[a-f0-9]{32}$")
    sequence: int = Field(ge=1)
    type: str = Field(pattern=r"^(run|tool|response|public_summary)\.[a-z_]+$")
    occurred_at: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


class HarnessRunResult(_StrictModel):
    """预留 M2 Run 查询和终态映射使用的稳定结果 DTO。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: str = Field(min_length=1, max_length=64)
    termination_reason: str | None = Field(default=None, max_length=120)


__all__ = [
    "HarnessRunEvent",
    "HarnessRunHandle",
    "HarnessRunRequest",
    "HarnessRunResult",
]
