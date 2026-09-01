from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.creative_brief import (
    InspectCreativeBriefTool,
    SelectCreativeOptionTool,
    UpdateCreativeBriefTool,
)
from pixelflow.agent_tools.video.storyboard import ReviseStoryboardTool
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


@pytest.mark.asyncio
async def test_revise_storyboard_updates_multiple_segments_and_marks_assets_stale() -> None:
    result = await ReviseStoryboardTool().execute(
        _context(
            {
                "creative_brief": {"active_option_id": "option-a"},
                "scenes": [
                    {
                        "scene_id": "A",
                        "segment_id": "A",
                        "prompt": "旧开场",
                        "duration_sec": 20,
                        "variants": [{"video_url": "https://example.com/a.mp4"}],
                    },
                    {
                        "scene_id": "B",
                        "segment_id": "B",
                        "prompt": "旧结尾",
                        "duration_sec": 20,
                    },
                ],
                "prompt_packages": [
                    {
                        "segment_id": "A",
                        "sequence": 1,
                        "duration_sec": 20,
                        "prompt": "旧开场",
                        "reference_asset_ids": ["asset-product"],
                    },
                    {
                        "segment_id": "B",
                        "sequence": 2,
                        "duration_sec": 20,
                        "prompt": "旧结尾",
                        "reference_asset_ids": ["asset-product"],
                    },
                ],
                "asset_registry": [{"asset_id": "asset-product"}],
            }
        ),
        {
            "option_id": "option-a",
            "revisions": [
                {"segment_id": "A", "prompt": "新开场", "duration_sec": 26},
                {"segment_id": "B", "sound": "加入门锁声"},
            ],
        },
    )
    assert result.model_observation["affected_segment_ids"] == ["A", "B"]
    assert result.model_observation["stale_video_count"] == 1
    assert result.workspace_patch["dirty_scene_ids"] == ["A", "B"]
    assert result.workspace_patch["scenes"][0]["prompt"] == "新开场"
    assert result.workspace_patch["scenes"][0]["video_asset_state"] == "stale"
    assert result.workspace_patch["prompt_packages"][1]["sound"] == "加入门锁声"
