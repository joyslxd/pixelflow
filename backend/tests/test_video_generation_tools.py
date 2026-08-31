"""验证新 Harness 视频创建与结果查询 Tool 的安全合同。"""

import pytest

from pixelflow.agent_tools.video import (
    CreateVideoTool,
    InspectVideoResultsTool,
    VideoToolContext,
    VideoToolRegistry,
)
from pixelflow.video.contracts import VideoWorkspace


@pytest.mark.asyncio
async def test_create_video_keeps_unavailable_state_inside_manifest_contract() -> None:
    """Provider 未装配时必须给出可诊断状态，不能被 Broker 白名单误判为 ValueError。"""

    tool = CreateVideoTool()
    result = await VideoToolRegistry((tool,)).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-create-video",
                conversation_id="conversation-create-video",
                payload={},
            ),
        ),
        "create_video",
        {"scene_ids": ["scene-1"]},
    )

    assert result.tool_name == "create_video"
    assert result.model_observation == {"status": "unavailable", "scene_ids": ["scene-1"]}
    assert set(result.model_observation).issubset(tool.spec.model_observation_keys)


@pytest.mark.asyncio
async def test_inspect_video_results_projects_safe_scene_states() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-video-results",
        conversation_id="conversation-video-results",
        payload={
            "scenes": [
                {
                    "scene_id": "ready",
                    "approved_variant_id": "variant-ready",
                    "variants": [
                        {
                            "variant_id": "variant-ready",
                            "selected": True,
                            "artifact_ref": "artifact:video:ready",
                            "video_url": "https://cdn.example.invalid/ready.mp4",
                        }
                    ],
                },
                {"scene_id": "running", "generation_jobs": [{"status": "polling"}]},
                {"scene_id": "failed", "generation_jobs": [{"status": "failed"}]},
                {"scene_id": "pending"},
            ]
        },
    )
    result = await InspectVideoResultsTool().execute(
        VideoToolContext(user_id="user", workspace=workspace),
        {},
    )

    assert result.model_observation["status"] == "running"
    assert result.model_observation["total"] == 4
    assert result.model_observation["ready"] == 1
    assert result.model_observation["running"] == 1
    assert result.model_observation["failed"] == 1
    assert result.model_observation["pending"] == 1
    assert result.artifact_refs == ("artifact:video:ready",)
    assert "video_url" not in str(result.model_observation)
