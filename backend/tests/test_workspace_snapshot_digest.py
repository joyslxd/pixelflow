"""验证首批 Snapshot 业务面板所依赖的安全 Workspace 摘要。"""

from __future__ import annotations

from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace import build_workspace_digest


def test_workspace_digest_exposes_v2_workspace_details_without_media_urls() -> None:
    """工作台可读取用户自己的规划文本，但不能取得媒体 URL 或 Provider 私有字段。"""

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
            "workspace_schema_version": 2,
            "creative_brief": {
                "brand": "示例品牌",
                "product": "示例产品",
                "creative_direction": "先展示生活痛点，再以产品收束。",
                "target_duration_sec": 60,
            },
            "narrative_plan": {
                "concept": "日常反转",
                "outline": "痛点、证明、收束。",
            },
            "asset_registry": [
                {
                    "asset_id": "product-1",
                    "slot": "@图片1",
                    "kind": "product",
                    "role": "产品外观锚点",
                    "origin": "planned_generation",
                    "generation_prompt": "产品三视图设定",
                    "state": "planned",
                    "provider_url": "https://example.invalid/provider-private.png",
                    "usable_for_video": False,
                }
            ],
            "prompt_packages": [
                {
                    "segment_id": "S01",
                    "sequence": 1,
                    "duration_sec": 8,
                    "generation_mode": "independent",
                    "prompt": "画面无任何字幕。晨光厨房内，产品在右侧台面，镜头缓慢推进。",
                    "state": "planned",
                    "provider_payload": {"raw": "不得公开"},
                }
            ],
            "reference_images": [
                {
                    "reference_id": "reference-1",
                    "asset_id": "asset-1",
                    "name": "口红主图.png",
                    "url": "https://example.invalid/user-reference.png",
                }
            ],
            "materials": [
                {
                    "material_id": "6dd156e5-1174-4c70-a6d3-9d1796647f4b",
                    "kind": "image",
                    "name": "安然角色图.png",
                    "reference_label": "参考图1",
                    "url": "https://example.invalid/private-material.png",
                    "asset_id": "asset-private-1",
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
    assert digest["reference_image_count"] == 1
    assert digest["reference_image_summaries"] == [
        {"reference_id": "reference-1", "asset_id": "asset-1", "name": "口红主图.png"}
    ]
    assert digest["material_count"] == 1
    assert digest["material_summaries"] == [
        {
            "material_id": "6dd156e5-1174-4c70-a6d3-9d1796647f4b",
            "kind": "image",
            "name": "安然角色图.png",
            "reference_label": "参考图1",
        }
    ]
    assert digest["workspace_schema_version"] == 2
    assert digest["creative_brief"]["creative_direction"] == "先展示生活痛点，再以产品收束。"
    assert digest["narrative_plan"]["outline"] == "痛点、证明、收束。"
    assert digest["asset_registry"] == [
        {
            "asset_id": "product-1",
            "slot": "@图片1",
            "kind": "product",
            "role": "产品外观锚点",
            "origin": "planned_generation",
            "generation_prompt": "产品三视图设定",
            "state": "planned",
            "reference_asset_ids": [],
            "usable_for_video": False,
        }
    ]
    assert digest["prompt_packages"][0]["duration_sec"] == 8
    assert digest["prompt_packages"][0]["prompt_summary"].startswith("画面无任何字幕")
    rendered = str(digest)
    assert "private-image" not in rendered
    assert "private-video" not in rendered
    assert "user-reference" not in rendered
    assert "private-material" not in rendered
    assert "asset-private" not in rendered
    assert "provider-private" not in rendered
