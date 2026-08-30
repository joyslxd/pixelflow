"""Harness Run 对外公开的控制面 DTO。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.agent_control_plane.contracts.enums import AgentEventType


class _ControlPlaneModel(BaseModel):
    """拒绝未知字段，防止 Sidecar 事件越过公开边界。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicAgentEventV1(_ControlPlaneModel):
    """浏览器可消费的公开事件，不包含推理、凭据或原始 Tool 参数。"""

    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    type: AgentEventType
    occurred_at: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    conversation_id: str = Field(default="", max_length=64)
    cursor: str = Field(default="", max_length=128)


class PublicMessageV1(_ControlPlaneModel):
    """Snapshot 内可恢复的公开消息，不含 Authorization 或内部轨迹。"""

    message_id: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(default="", max_length=32_000)


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
    messages: list[PublicMessageV1] = Field(default_factory=list)
    workspace: VideoWorkspaceProjectionV1 | None = None
    conversation_id: str = Field(default="", max_length=64)
    context_version: int = Field(default=0, ge=0)
    last_cursor: str = Field(default="", max_length=128)


class TurnMaterialV1(_ControlPlaneModel):
    """本次用户输入引用的已上传材料；二进制始终留在 content-app/TOS。"""

    material_id: UUID
    kind: Literal["image", "video", "audio", "file"]
    name: str = Field(min_length=1, max_length=255)
    reference_label: str = Field(min_length=1, max_length=80)
    content_type: str = Field(min_length=1, max_length=120)
    url: str = Field(pattern=r"^https?://", max_length=4_096)
    asset_id: str | None = Field(default=None, min_length=1, max_length=128)


class HarnessTurnStartRequestV1(_ControlPlaneModel):
    """公开 Turn 入参：工作区归属由 Gateway 回查，浏览器不得自造业务副本。"""

    client_input_id: UUID
    workspace_id: str = Field(min_length=1, max_length=64)
    expected_workspace_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=32_000)
    materials: list[TurnMaterialV1] = Field(default_factory=list, max_length=9)
    # 用途：为多轮创作和 Tool 调度保留足够的推理与最终回复预算；影响：默认提高到 32K，仍受 Sidecar 131K 硬上限、Run deadline 与模型档案约束。
    max_output_tokens: int = Field(default=32_768, ge=1, le=131_072)


class HarnessTurnStartResponseV1(_ControlPlaneModel):
    """公开返回已绑定并已激活的 Sidecar Run，不暴露 Session 或服务凭据。"""

    message_id: str
    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: Literal["accepted"]
    workspace_revision: int = Field(ge=1)


class HarnessRunCancelResponseV1(_ControlPlaneModel):
    """取消结果只包含稳定 Run 终态，不暴露 Harness 运行时细节。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: Literal["completed", "failed", "cancelled"]
    termination_reason: str | None = Field(default=None, max_length=120)


__all__ = [
    "AgentSnapshotV1",
    "HarnessRunCancelResponseV1",
    "HarnessTurnStartRequestV1",
    "HarnessTurnStartResponseV1",
    "PublicAgentEventV1",
    "PublicMessageV1",
    "TurnMaterialV1",
    "VideoWorkspaceProjectionV1",
]
