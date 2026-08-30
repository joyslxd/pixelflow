from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.registry import VideoToolRegistry
from pixelflow.agent_tools.video.storyboard import PrepareScenePackagesTool
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.services.tool_executor import VideoToolExecutor
from pixelflow.video.workspace import MemoryVideoAgentRepository
from pixelflow.video.workspace.payload import (
    WORKSPACE_SCHEMA_VERSION,
    migrate_workspace_payload,
)


def test_legacy_workspace_payload_migrates_without_dropping_old_projection() -> None:
    migrated = migrate_workspace_payload(
        {
            "product_info": {"brand": "美的", "name": "可爱多"},
            "video_ratio": "9:16",
            "script": {"content": "时间胶囊", "status": "已编辑"},
            "scenes": [{"scene_id": "A", "prompt": "洗衣房", "duration_sec": 26}],
        }
    )
    assert migrated["workspace_schema_version"] == WORKSPACE_SCHEMA_VERSION
    assert migrated["product_info"] == {"brand": "美的", "name": "可爱多"}
    assert migrated["creative_brief"]["aspect_ratio"] == "9:16"
    assert migrated["narrative_plan"]["script"] == "时间胶囊"
    assert migrated["prompt_packages"][0]["segment_id"] == "A"


@pytest.mark.asyncio
async def test_prepare_tool_writes_four_layers_and_legacy_projections_together() -> None:
    workspace = VideoWorkspace(
        workspace_id="workspace-v2",
        conversation_id="conversation-v2",
        revision=3,
        payload={"product_info": {"brand": "美的", "name": "可爱多"}},
    )
    context = VideoToolContext(user_id="user", workspace=workspace)
    result = await PrepareScenePackagesTool().execute(
        context,
        {
            "script": "母女与时间胶囊",
            "creative_brief": {"platform": "douyin", "aspect_ratio": "9:16"},
            "narrative_plan": {"concept": "把爱变成分担"},
            "asset_registry": [
                {"asset_id": "image-1", "kind": "character", "role": "母亲"}
            ],
            "scenes": [
                {
                    "scene_id": "A",
                    "prompt": "滚筒内部向外看",
                    "duration_sec": 26,
                    "generation_mode": "reference",
                    "reference_asset_ids": ["image-1"],
                    "continuity_from": None,
                    "transition_out": "衣物遮挡",
                    "era": "2015",
                    "camera": "水平舷窗视角",
                    "sound": "门锁咔哒声",
                    "hard_constraints": ["不生成字幕"],
                }
            ],
        },
    )
    assert result.workspace_patch["workspace_schema_version"] == 2
    assert result.workspace_patch["creative_brief"]["platform"] == "douyin"
    assert result.workspace_patch["narrative_plan"]["concept"] == "把爱变成分担"
    assert result.workspace_patch["asset_registry"][0]["asset_id"] == "image-1"
    package = result.workspace_patch["prompt_packages"][0]
    assert package["segment_id"] == "A"
    assert package["generation_mode"] == "reference"
    assert result.workspace_patch["scenes"][0]["scene_id"] == "A"


@pytest.mark.asyncio
async def test_prepare_tool_executor_accepts_all_declared_workspace_roots() -> None:
    """经 Registry/Executor 写入时，V2 四层根字段不能被白名单误拒绝。"""

    repository = MemoryVideoAgentRepository()
    workspace = await repository.create_workspace(
        "user",
        VideoWorkspace(
            workspace_id="workspace-executor",
            conversation_id="conversation-executor",
            revision=1,
        ),
    )
    executor = VideoToolExecutor(
        repository=repository,
        registry=VideoToolRegistry((PrepareScenePackagesTool(),)),
    )

    result = await executor.execute_tool_call(
        context=VideoToolContext(user_id="user", workspace=workspace),
        tool_name="prepare_scene_packages",
        arguments={
            "script": "家庭保鲜产品片",
            "creative_brief": {"brand": "美的", "aspect_ratio": "9:16"},
            "asset_registry": [{"asset_id": "product-image-1", "kind": "image", "role": "产品参考"}],
            "scenes": [{"scene_id": "A", "prompt": "冰箱产品展示", "duration_sec": 8, "reference_asset_ids": ["product-image-1"]}],
        },
    )
    updated = await repository.get_workspace("user", workspace.workspace_id)

    assert result.public_summary.startswith("已准备 1 个分镜")
    assert updated is not None
    assert updated.revision == 2
    assert updated.payload["creative_brief"]["brand"] == "美的"
    assert updated.payload["prompt_packages"][0]["segment_id"] == "A"


@pytest.mark.asyncio
async def test_prepare_tool_returns_safe_validation_field_for_agent_self_correction() -> None:
    """模型拼错分镜字段时只得到字段路径，不会回显用户正文或 Pydantic 原文。"""

    registry = VideoToolRegistry((PrepareScenePackagesTool(),))
    result = await registry.execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-validation",
                conversation_id="conversation-validation",
            ),
        ),
        "prepare_scene_packages",
        {
            "script": "测试脚本",
            "scenes": [{"scene_id": "A", "prompt": "测试镜头", "duratio_sec": 8}],
        },
    )

    assert result.public_summary == "工具参数无效，请修正字段：scenes.0.duration_sec、scenes.0.reference_asset_ids"
    assert result.model_observation == {
        "validation_fields": ["scenes.0.duration_sec", "scenes.0.reference_asset_ids"],
    }


@pytest.mark.asyncio
async def test_prepare_tool_registers_uploaded_material_and_canonicalizes_its_reference() -> None:
    """上传图在规划写入时变为已有素材，Prompt 仅保留稳定资产身份而不保存 URL。"""

    material_id = "material-product-1"
    workspace = VideoWorkspace(
        workspace_id="workspace-material",
        conversation_id="conversation-material",
        payload={
            "materials": [{
                "material_id": material_id,
                "kind": "image",
                "name": "M20 产品参考图",
                "reference_label": "@产品图1",
                "url": "https://example.invalid/m20.jpg",
            }]
        },
    )
    result = await PrepareScenePackagesTool().execute(
        VideoToolContext(user_id="user", workspace=workspace),
        {
            "script": "产品展示",
            "scenes": [{
                "scene_id": "SC01",
                "prompt": "女主角参考 @产品图1 打开冰箱门",
                "duration_sec": 8,
                "reference_asset_ids": [material_id],
            }],
        },
    )

    assets = result.workspace_patch["asset_registry"]
    assert assets == [{
        "asset_id": "asset_material_material-product-1",
        "slot": "@产品图1",
        "kind": "reference_image",
        "role": "M20 产品参考图",
        "origin": "existing_material",
        "source_material_id": material_id,
        "state": "ready",
        "reference_asset_ids": [],
        "provider_artifact_ref": "artifact:material:material-product-1",
        "usable_for_video": True,
    }]
    assert result.workspace_patch["prompt_packages"][0]["reference_asset_ids"] == [
        "asset_material_material-product-1"
    ]


@pytest.mark.asyncio
async def test_prepare_tool_rejects_prompt_reference_not_in_asset_registry() -> None:
    result = await VideoToolRegistry((PrepareScenePackagesTool(),)).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(workspace_id="workspace-asset-validation", conversation_id="conversation"),
        ),
        "prepare_scene_packages",
        {
            "script": "测试",
            "asset_registry": [{"asset_id": "asset-product", "kind": "product", "role": "冰箱"}],
            "scenes": [{
                "scene_id": "SC01", "prompt": "展示产品", "duration_sec": 8,
                "reference_asset_ids": ["missing-asset"],
            }],
        },
    )
    assert result.public_summary == "分镜 SC01 引用了未登记资产"


@pytest.mark.asyncio
async def test_memory_repository_migrates_payload_on_first_cas_write() -> None:
    repository = MemoryVideoAgentRepository()
    workspace = await repository.create_workspace(
        "user",
        VideoWorkspace(
            workspace_id="workspace-migrate",
            conversation_id="conversation-migrate",
            revision=1,
            payload={"script": {"content": "旧脚本"}},
        ),
    )
    updated = await repository.apply_workspace_patch(
        "user",
        workspace.workspace_id,
        {"latest_input": "继续规划"},
        expected_revision=1,
        now=workspace.updated_at,
    )
    assert updated.payload["workspace_schema_version"] == 2
    assert updated.payload["narrative_plan"]["script"] == "旧脚本"
