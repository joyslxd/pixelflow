"""验证首批 Snapshot 业务面板所依赖的安全 Workspace 摘要。"""

from __future__ import annotations

from pixelflow.agent_harness.context_builder import PixelFlowContextBuilder
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
    assert digest["prompt_packages"][0]["state"] == "planned"
    assert "has_preview" not in digest["prompt_packages"][0]
    rendered = str(digest)
    assert "private-image" not in rendered
    assert "private-video" not in rendered
    assert "user-reference" not in rendered
    assert "private-material" not in rendered
    assert "asset-private" not in rendered
    assert "provider-private" not in rendered


def test_workspace_digest_with_ready_material_asset_is_safe_for_harness_context() -> None:
    """内部 Artifact 仅供 Gateway 解析，不以 provider 字段进入 Sidecar 上下文。"""

    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-ready-asset",
            conversation_id="conversation-ready-asset",
            payload={
                "workspace_schema_version": 2,
                "asset_registry": [{
                    "asset_id": "asset_material_product",
                    "slot": "@产品图1",
                    "kind": "reference_image",
                    "role": "M20 产品图",
                    "origin": "existing_material",
                    "source_material_id": "material-product",
                    "state": "ready",
                    "provider_artifact_ref": "artifact:material:material-product",
                    "usable_for_video": True,
                }],
            },
        )
    )

    assert digest["asset_registry"] == [{
        "asset_id": "asset_material_product",
        "slot": "@产品图1",
        "kind": "reference_image",
        "role": "M20 产品图",
        "origin": "existing_material",
        "state": "ready",
        "reference_asset_ids": [],
        "usable_for_video": True,
    }]
    PixelFlowContextBuilder().build({"workspace_projection": digest})


def test_workspace_digest_projects_image_generation_progress_without_media_urls() -> None:
    """工作台必须能看到图片任务进度，但不能取得生成图 URL。"""

    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-generating",
            conversation_id="conversation-generating",
            payload={
                "workspace_schema_version": 2,
                "asset_registry": [
                    {
                        "asset_id": "asset_character_01",
                        "slot": "@女主人",
                        "kind": "character",
                        "role": "女主设定图",
                        "origin": "planned_generation",
                        "generation_prompt": "女主锁骨相",
                        "state": "planned",
                        "generation_job_id": "generation-job-character",
                        "generation_job_status": "queued",
                        "image_url": "https://example.invalid/private-generated.png",
                    },
                    {
                        "asset_id": "asset_scene_01",
                        "slot": "@厨房",
                        "kind": "scene",
                        "role": "厨房场景图",
                        "origin": "planned_generation",
                        "generation_prompt": "空厨房",
                        "state": "failed",
                        "generation_job_id": "generation-job-scene",
                        "generation_job_status": "queued",
                        "failure_reason_code": "provider_start_provider_response_not_json",
                    },
                ],
            },
        )
    )

    assert digest["asset_registry"][0]["state"] == "generating"
    assert digest["asset_registry"][0]["generation_job_status"] == "queued"
    assert digest["asset_registry"][1]["state"] == "failed"
    assert digest["asset_registry"][1]["generation_job_status"] == "failed"
    rendered = str(digest)
    assert "private-generated" not in rendered
    assert "https://" not in rendered


def test_workspace_digest_exposes_allowlisted_scene_preview_url() -> None:
    """成片预览把白名单 TOS 地址交给工作台直连播放，不中转视频字节。"""

    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-scene-ready",
            conversation_id="conversation-scene-ready",
            payload={
                "workspace_schema_version": 2,
                "scenes": [
                    {
                        "scene_id": "s1",
                        "segment_id": "s1",
                        "scene_index": 1,
                        "title": "厨房开场",
                        "video_url": "https://bucket.tos-cn-beijing.volces.com/s1.mp4",
                        "variants": [{
                            "variant_id": "v1",
                            "video_url": "https://bucket.tos-cn-beijing.volces.com/s1.mp4",
                            "selected": True,
                        }],
                    }
                ],
                "prompt_packages": [
                    {
                        "segment_id": "s1",
                        "sequence": 1,
                        "duration_sec": 12,
                        "generation_mode": "independent",
                        "prompt": "厨房中的女主人缓步走向灶台。",
                        "state": "planned",
                    }
                ],
            },
        )
    )

    assert digest["prompt_packages"][0]["state"] == "ready"
    assert digest["prompt_packages"][0]["has_preview"] is True
    assert digest["prompt_packages"][0]["preview_url"] == "https://bucket.tos-cn-beijing.volces.com/s1.mp4"
    assert digest["scene_videos_ready_count"] == 1


def test_workspace_digest_exposes_allowlisted_merged_video_preview_url() -> None:
    """合并成片同样只公开白名单 TOS 地址，供工作台回显。"""

    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-merged",
            conversation_id="conversation-merged",
            payload={
                "workspace_schema_version": 2,
                "merged_video": {
                    "ok": True,
                    "merged_video_url": "https://bucket.tos-cn-beijing.volces.com/merged.mp4",
                    "task_id": "delivery-secret",
                },
                "outputs": [
                    {
                        "output_type": "mp4",
                        "video_url": "https://should-not-render.invalid/other.mp4",
                    }
                ],
            },
        )
    )

    assert digest["merged_video"] == {
        "ok": True,
        "preview_url": "https://bucket.tos-cn-beijing.volces.com/merged.mp4",
    }
    assert "delivery-secret" not in str(digest)
    assert "should-not-render" not in str(digest)


def test_workspace_digest_hides_merged_video_on_non_allowlisted_host() -> None:
    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-merged-blocked",
            conversation_id="conversation-merged-blocked",
            payload={
                "merged_video": {
                    "ok": True,
                    "merged_video_url": "https://cdn.example.invalid/merged.mp4",
                }
            },
        )
    )

    assert digest["merged_video"] == {"ok": True}
    assert "preview_url" not in digest["merged_video"]
    assert "example.invalid" not in str(digest)


def test_workspace_digest_treats_retry_queued_job_as_polling_not_old_failure() -> None:
    """历史额度失败不能把当前 queued 重试投影成失败，否则工作台会停转。"""

    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-retrying",
            conversation_id="conversation-retrying",
            payload={
                "workspace_schema_version": 2,
                "asset_registry": [
                    {
                        "asset_id": "asset_character_01",
                        "slot": "@女主人",
                        "kind": "character",
                        "role": "女主设定图",
                        "origin": "planned_generation",
                        "state": "ready",
                        "generation_job_id": "generation-job-character",
                        "generation_job_status": "succeeded",
                        "usable_for_video": True,
                    },
                    {
                        "asset_id": "asset_product_01",
                        "slot": "@产品",
                        "kind": "product",
                        "role": "产品图",
                        "origin": "planned_generation",
                        "state": "ready",
                        "generation_job_id": "generation-job-product",
                        "generation_job_status": "succeeded",
                        "usable_for_video": True,
                    },
                ],
                "scenes": [
                    {
                        "scene_id": "scene_01_pain_solution",
                        "segment_id": "scene_01_pain_solution",
                        "scene_index": 1,
                        "edit_status": "重新生成中",
                        "generation_jobs": [
                            {"job_id": "old-failed", "status": "failed", "reason_code": "provider_quota_insufficient"},
                            {"job_id": "generation-job-new", "status": "queued"},
                        ],
                    }
                ],
                "prompt_packages": [
                    {
                        "segment_id": "scene_01_pain_solution",
                        "sequence": 1,
                        "duration_sec": 12,
                        "generation_mode": "independent",
                        "prompt": "厨房中的女主人缓步走向灶台。",
                        "state": "planned",
                    }
                ],
            },
        )
    )

    assert digest["scene_videos_polling_count"] == 1
    assert digest["scene_videos_failed_count"] == 0
    assert digest["prompt_packages"][0]["state"] == "generating"
    assert digest["generation_jobs"] == [
        {
            "generation_job_id": "generation-job-new",
            "item_id": "scene_01_pain_solution",
            "kind": "video",
            "status": "queued",
        }
    ]

