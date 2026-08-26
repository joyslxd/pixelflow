"""验证首批 Snapshot 业务面板所依赖的安全 Workspace 摘要。"""

from __future__ import annotations

from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace import build_workspace_digest


def test_workspace_digest_exposes_panel_fields_without_media_or_prompt() -> None:
    """脚本、素材与分镜面板只取得可公开摘要，不能取得媒体 URL 或提示词。"""

    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        revision=7,
        payload={
            "script": {
                "content": "夏季产品创意脚本",
                "status": "ready",
                "missing_requirements": ["结尾口播"],
            },
            "global_assets": {
                "characters": [
                    {
                        "asset_id": "character-1",
                        "name": "主角",
                        "image_url": "https://example.invalid/private-image.png",
                        "prompt": "不得公开的提示词",
                    }
                ],
                "scenes": [{"asset_id": "scene-1", "name": "海边"}],
                "props": [{"asset_id": "prop-1", "name": "饮料"}],
            },
            "scenes": [
                {
                    "scene_id": "shot-1",
                    "scene_index": 1,
                    "title": "开场",
                    "video_url": "https://example.invalid/private-video.mp4",
                    "prompt": "不得公开的分镜提示词",
                }
            ],
        },
    )

    digest = build_workspace_digest(workspace)

    assert digest["script_preview"] == "夏季产品创意脚本"
    assert digest["character_summaries"] == [{"asset_id": "character-1", "name": "主角"}]
    assert digest["scene_summaries"] == [
        {"scene_id": "shot-1", "scene_index": 1, "title": "开场", "state": "ready"}
    ]
    rendered = str(digest)
    assert "private-image" not in rendered
    assert "private-video" not in rendered
    assert "不得公开" not in rendered
