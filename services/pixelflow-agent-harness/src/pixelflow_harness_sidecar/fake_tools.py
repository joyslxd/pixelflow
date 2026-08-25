"""提供 M0 可用于隔离验证的只读 Fake Tool。"""

from __future__ import annotations

from pydantic import Field

from .contracts import StrictModel


class InspectVideoWorkspaceInput(StrictModel):
    """定义只读视频工作区检查 Tool 的最小公开输入。"""

    workspace_ref: str = Field(pattern=r"^opaque:", max_length=300)


class InspectVideoWorkspaceObservation(StrictModel):
    """定义不包含用户身份或 Provider 原始数据的公开观察结果。"""

    code: str = "workspace_inspected"
    public_summary: str = "已读取模拟视频工作区摘要"
    workspace_revision: int = Field(ge=0)


def inspect_video_workspace(payload: InspectVideoWorkspaceInput) -> InspectVideoWorkspaceObservation:
    """返回确定性只读观察结果，不访问数据库或外部服务。"""

    _ = payload.workspace_ref
    return InspectVideoWorkspaceObservation(workspace_revision=0)
