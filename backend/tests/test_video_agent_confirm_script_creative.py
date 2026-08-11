"""confirm_script_creative：Path B 导入脚本也可确认。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools.registry import VideoToolContext, VideoToolValidationError
from pixelflow.video_agent.tools.script_skill_pipeline import ConfirmScriptCreativeTool


def _workspace(payload: dict) -> VideoWorkspace:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    return VideoWorkspace(
        workspace_id="ws1",
        conversation_id="c1",
        payload=payload,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_confirm_creative_accepts_imported_script_without_start() -> None:
    tool = ConfirmScriptCreativeTool()
    workspace = _workspace(
        {
            "latest_input": "同意创作",
            "script": {
                "content": "0—10秒｜开场\n170—180秒｜收束\n画幅 9:16\n结尾就这样收束",
                "missing_requirements": [],
                "artifact_ref": "artifact:script-1",
                "version": 1,
            },
        }
    )
    result = await tool.execute(
        VideoToolContext(user_id="u1", workspace=workspace),
        {},
    )
    assert "已确认选题创意" in result.public_summary
    assert result.workspace_patch["script_pipeline"]["creative_confirmed"]["confirmed"] is True


@pytest.mark.asyncio
async def test_confirm_creative_blocks_when_workspace_missing_fields() -> None:
    tool = ConfirmScriptCreativeTool()
    workspace = _workspace(
        {
            "latest_input": "同意创作",
            "script": {
                "content": "完整脚本正文",
                "missing_requirements": ["视频画幅", "结尾行动引导"],
            },
        }
    )
    with pytest.raises(VideoToolValidationError, match="请先补充"):
        await tool.execute(
            VideoToolContext(user_id="u1", workspace=workspace),
            {},
        )
