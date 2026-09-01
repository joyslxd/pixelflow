"""验证 V2 Prompt Package 的图片素材边界。"""

from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolExecutionError
from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.generation_jobs.requests import build_scene_generation_request
from pixelflow.video.contracts import VideoWorkspace


def _context(payload):
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload,
        ),
        run_id="hrun_test",
        tool_call_id="tool-call-test",
        credential=TransientVideoAgentCredential("transient"),
    )


def test_v2_scene_rejects_planned_asset_before_video_generation() -> None:
    with pytest.raises(VideoToolExecutionError, match="尚未就绪"):
        build_scene_generation_request(
            _context({
                "creation_contract": {
                    "video_model": "seedance-2.0",
                    "video_ratio": "9:16",
                },
                "asset_registry": [{
                    "asset_id": "asset-character",
                    "state": "planned",
                    "usable_for_video": False,
                }],
            }),
            {
                "scene_id": "scene-1",
                "prompt": "厨房中的女主人",
                "duration_sec": 8,
                "reference_asset_ids": ["asset-character"],
            },
            1,
        )


def test_v2_scene_uses_only_ready_registered_asset_urls() -> None:
    request = build_scene_generation_request(
        _context({
            "creation_contract": {
                "video_model": "seedance-2.0",
                "video_ratio": "9:16",
            },
            "asset_registry": [{
                "asset_id": "asset-product",
                "state": "ready",
                "usable_for_video": True,
                "image_url": "https://cdn.example/product.png",
            }],
            "materials": [{
                "kind": "image",
                "url": "https://cdn.example/upload.png",
            }],
        }),
        {
            "scene_id": "scene-1",
            "prompt": "冰箱展示",
            "duration_sec": 8,
            "reference_asset_ids": ["asset-product"],
        },
        1,
    )
    assert request["image_urls"] == [
        "https://cdn.example/product.png",
        "https://cdn.example/upload.png",
    ]
