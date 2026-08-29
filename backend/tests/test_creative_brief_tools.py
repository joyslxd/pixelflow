from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.creative_brief import (
    InspectCreativeBriefTool,
    SelectCreativeOptionTool,
    UpdateCreativeBriefTool,
)
from pixelflow.video.contracts import VideoWorkspace


def _context(payload: dict[str, object] | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user",
        workspace=VideoWorkspace(
            workspace_id="workspace-brief",
            conversation_id="conversation-brief",
            revision=1,
            payload=payload or {},
        ),
    )


@pytest.mark.asyncio
async def test_update_creative_brief_creates_and_merges_multiple_options() -> None:
    first = await UpdateCreativeBriefTool().execute(
        _context(),
        {"option_id": "option-a", "title": "温情版", "concept": "时间胶囊"},
    )
    second_context = _context(first.workspace_patch)
    second = await UpdateCreativeBriefTool().execute(
        second_context,
        {"option_id": "option-b", "title": "产品版", "platform": "douyin"},
    )
    options = second.workspace_patch["creative_brief"]["options"]
    assert [item["option_id"] for item in options] == ["option-a", "option-b"]
    assert second.workspace_patch["creative_brief"].get("active_option_id") is None


@pytest.mark.asyncio
async def test_select_creative_option_only_changes_brief_and_does_not_create_scene_data() -> None:
    payload = {
        "creative_brief": {
            "options": [
                {"option_id": "option-a", "title": "温情版", "status": "draft"},
                {"option_id": "option-b", "title": "产品版", "status": "draft"},
            ]
        },
        "scenes": [{"scene_id": "existing"}],
    }
    result = await SelectCreativeOptionTool().execute(
        _context(payload),
        {"option_id": "option-b"},
    )
    brief = result.workspace_patch["creative_brief"]
    assert brief["active_option_id"] == "option-b"
    assert brief["title"] == "产品版"
    assert "scenes" not in result.workspace_patch
    assert "asset_registry" not in result.workspace_patch


@pytest.mark.asyncio
async def test_inspect_creative_brief_returns_bounded_options() -> None:
    result = await InspectCreativeBriefTool().execute(
        _context(
            {
                "creative_brief": {
                    "active_option_id": "option-a",
                    "options": [{"option_id": "option-a", "title": "温情版"}],
                }
            }
        ),
        {},
    )
    assert result.model_observation["active_option_id"] == "option-a"
    assert result.model_observation["options"][0]["title"] == "温情版"
