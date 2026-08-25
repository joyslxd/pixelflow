"""定义 Gateway Tool Broker 的稳定内部 HTTP DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """拒绝未知字段，防止 Sidecar 绕过隐藏参数与 owner 校验。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolCallRequest(ContractModel):
    """Sidecar 调用 PixelFlow Capability Tool 的最小稳定请求。"""

    protocol_version: Literal["v1"]
    run_id: str = Field(pattern=r"^hrun_", max_length=64)
    session_id: str = Field(pattern=r"^pfh_", max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_workspace_revision: int = Field(ge=1)
    context_digest: str = Field(pattern=r"^sha256:", max_length=71)
    toolset_version: str = Field(min_length=1, max_length=120)


class ToolCallResponse(ContractModel):
    """只返回模型可消费的稳定 Observation，不回传 Workspace 原始 payload。"""

    protocol_version: Literal["v1"]
    status: Literal["completed", "pending_operation", "awaiting_confirmation", "authorization_required", "rejected", "failed"]
    public_summary: str = Field(min_length=1, max_length=512)
    model_observation: dict[str, Any]
    suspension: dict[str, Any] | None = None


class ToolManifestResponse(ContractModel):
    """发布时冻结的 Capability Tool Manifest。"""

    protocol_version: Literal["v1"]
    version: str = Field(min_length=1, max_length=120)
    digest: str = Field(pattern=r"^sha256:", max_length=71)
    tools: list[dict[str, Any]]
