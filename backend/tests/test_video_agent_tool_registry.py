from __future__ import annotations

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools.inspect_workspace import InspectVideoWorkspaceTool
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolRegistry,
    VideoToolValidationError,
)


def test_registry_exposes_only_declared_tools() -> None:
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])

    assert registry.names() == ("inspect_video_workspace",)
    assert registry.resolve("delete_database") is None


@pytest.mark.asyncio
async def test_inspect_workspace_returns_compact_public_evidence() -> None:
    tool = InspectVideoWorkspaceTool()
    result = await tool.execute(
        VideoToolContext(
            workspace=VideoWorkspace(
                workspace_id="workspace-1",
                conversation_id="conversation-1",
                payload={
                    "artifact_refs": ["artifact:product-1"],
                    "script": {"content": "展示商品"},
                    "provider_secret": "must-not-leak",
                },
            ),
        ),
        {},
    )

    assert result.public_summary == "已读取项目资料：1 个素材，已提供脚本"
    assert result.artifact_refs == ("artifact:product-1",)
    assert "provider_secret" not in result.workspace_patch


@pytest.mark.asyncio
async def test_registry_rejects_invalid_tool_arguments() -> None:
    registry = VideoToolRegistry([InspectVideoWorkspaceTool()])
    with pytest.raises(VideoToolValidationError):
        await registry.execute("inspect_video_workspace", VideoToolContext(workspace=None), {"unexpected": True})
