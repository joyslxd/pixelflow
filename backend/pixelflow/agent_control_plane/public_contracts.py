"""Harness Run 对外公开的控制面 DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ControlPlaneModel(BaseModel):
    """拒绝未知字段，防止 Sidecar 事件越过公开边界。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicAgentEventV1(_ControlPlaneModel):
    """浏览器可消费的公开事件，不包含推理、凭据或原始 Tool 参数。"""

    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    type: Literal["run.state_changed", "tool.completed", "response.completed"]
    occurred_at: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


class VideoWorkspaceProjectionV1(_ControlPlaneModel):
    """浏览器可读取的视频工作区安全摘要，不暴露内部 payload 或供应商原始结果。"""

    workspace_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    summary: dict[str, Any] = Field(default_factory=dict)


class AgentSnapshotV1(_ControlPlaneModel):
    """单一 Run 的可恢复公开快照。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: Literal["accepted", "running", "completed", "failed", "cancelled"]
    last_sequence: int = Field(ge=0)
    events: list[PublicAgentEventV1] = Field(default_factory=list)
    messages: list[dict[str, str]] = Field(default_factory=list)
    workspace: VideoWorkspaceProjectionV1 | None = None


__all__ = ["AgentSnapshotV1", "PublicAgentEventV1", "VideoWorkspaceProjectionV1"]
