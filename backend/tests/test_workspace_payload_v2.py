from __future__ import annotations

import pytest

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.production_contract import SetVideoGenerationContractTool
from pixelflow.agent_tools.video.registry import VideoToolRegistry
from pixelflow.agent_tools.video.scene import GenerateScenesTool
from pixelflow.agent_tools.video.storyboard import PrepareScenePackagesTool
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.services.tool_executor import VideoToolExecutor
from pixelflow.video.workspace import MemoryVideoAgentRepository
from pixelflow.video.workspace.payload import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceCreationContract,
    canonicalize_video_model,
    migrate_workspace_payload,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Seedance 2.5", "seedance-2.5"),
        ("SEEDANCE-2.5", "seedance-2.5"),
        ("seedance2.5", "seedance-2.5"),
        ("seedance-2.5-fast", "seedance-2.5-fast"),
        ("kling-1.6", "kling-1.6"),
    ],
)
def test_canonicalize_video_model_aliases(raw: str, expected: str) -> None:
    assert canonicalize_video_model(raw) == expected


def test_creation_contract_canonicalizes_seedance_display_name() -> None:
    contract = WorkspaceCreationContract.model_validate(
        {
            "video_model": "Seedance 2.5",
            "video_ratio": "9:16",
            "video_size": "1080p",
            "video_sound": "on",
        }
    )
    assert contract.video_model == "seedance-2.5"


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
            "creation_contract": {
                "video_model": "seedance-2.0",
                "video_ratio": "9:16",
                "video_size": "1080p",
                "video_sound": "on",
            },
            "narrative_plan": {"concept": "把爱变成分担"},
            "asset_registry": [
                {"asset_id": "image-1", "kind": "character", "role": "母亲", "generation_prompt": "家庭厨房中的母亲角色设定图"}
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
    assert result.workspace_patch["creation_contract"]["video_model"] == "seedance-2.0"
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
            "asset_registry": [{"asset_id": "product-image-1", "kind": "image", "role": "产品参考", "generation_prompt": "美的冰箱产品设定图"}],
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
async def test_set_generation_contract_writes_complete_non_billable_provider_route() -> None:
    """Agent 可单独补齐生产参数，无需为修复路由而重写已确认的分镜。"""

    workspace = VideoWorkspace(workspace_id="workspace-contract", conversation_id="conversation-contract")
    result = await SetVideoGenerationContractTool().execute(
        VideoToolContext(user_id="user", workspace=workspace),
        {
            "video_model": "seedance-2.0",
            "video_ratio": "9:16",
            "video_size": "1080p",
            "video_sound": "on",
        },
    )

    assert result.workspace_patch == {
        "creation_contract": {
            "video_model": "seedance-2.0",
            "video_ratio": "9:16",
            "video_size": "1080p",
            "video_sound": "on",
        }
    }
    assert result.model_observation["creation_contract_ready"] is True


@pytest.mark.asyncio
async def test_generate_scenes_rejects_missing_creation_contract_before_creating_job() -> None:
    """缺路由参数时不得创建注定会失败的 GenerationJob。"""

    class GenerationJobService:
        video_available = True
        called = False

        async def submit_videos(self, *args, **kwargs):
            self.called = True
            raise AssertionError("不应创建 GenerationJob")

    service = GenerationJobService()
    result = await VideoToolRegistry((GenerateScenesTool(generation_job_service=service),)).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-generate-contract",
                conversation_id="conversation-generate-contract",
                payload={
                    "dirty_scene_ids": ["SC01"],
                    "scenes": [{
                        "scene_id": "SC01",
                        "prompt": "产品展示",
                        "duration_sec": 8,
                    }],
                },
            ),
        ),
        "generate_scenes",
        {"scene_ids": ["SC01"]},
    )

    assert "尚未冻结视频生产合同" in result.public_summary
    assert service.called is False


@pytest.mark.asyncio
async def test_prepare_tool_returns_safe_validation_hint_for_agent_self_correction() -> None:
    """模型拼错分镜字段时得到安全纠正提示，不会回显用户正文或 Pydantic 原文。"""

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

    assert result.public_summary == (
        "工具参数无效，请修正：scenes.0.duration_sec（缺少必填字段）、"
        "scenes.0.reference_asset_ids（缺少必填字段）"
    )
    assert result.model_observation == {
        "validation_fields": ["scenes.0.duration_sec", "scenes.0.reference_asset_ids"],
        "validation_hints": [
            "scenes.0.duration_sec（缺少必填字段）",
            "scenes.0.reference_asset_ids（缺少必填字段）",
        ],
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
async def test_prepare_tool_keeps_uploaded_material_and_allows_agent_to_enrich_its_role() -> None:
    material_id = "material-product-2"
    result = await PrepareScenePackagesTool().execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-material-role",
                conversation_id="conversation-material-role",
                payload={"materials": [{
                    "material_id": material_id,
                    "kind": "image",
                    "name": "冰箱图",
                    "url": "https://example.invalid/m20.jpg",
                }]},
            ),
        ),
        {
            "script": "产品展示",
            "asset_updates": [{
                "asset_id": f"asset_material_{material_id}",
                "kind": "product_reference",
                "role": "美的 M20 冰箱产品外观",
            }],
            "scenes": [{
                "scene_id": "SC01", "prompt": "参考产品图展示冰箱", "duration_sec": 8,
                "reference_asset_ids": [f"asset_material_{material_id}"],
            }],
        },
    )

    asset = result.workspace_patch["asset_registry"][0]
    assert asset["kind"] == "product_reference"
    assert asset["role"] == "美的 M20 冰箱产品外观"
    assert asset["origin"] == "existing_material"
    assert asset["source_material_id"] == material_id
    assert asset["usable_for_video"] is True


@pytest.mark.asyncio
async def test_prepare_tool_rejects_existing_material_in_planned_asset_registry_with_safe_reason() -> None:
    """上传素材不得经 asset_registry 重传，避免模型伪造 Artifact、状态或来源。"""

    material_id = "material-product-3"
    result = await VideoToolRegistry((PrepareScenePackagesTool(),)).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-material-registry",
                conversation_id="conversation-material-registry",
                payload={"materials": [{
                    "material_id": material_id,
                    "kind": "image",
                    "name": "冰箱图",
                    "url": "https://example.invalid/m20.jpg",
                }]},
            ),
        ),
        "prepare_scene_packages",
        {
            "script": "产品展示",
            "asset_registry": [{
                "asset_id": f"asset_material_{material_id}",
                "kind": "product_reference",
                "role": "美的 M20 冰箱产品外观",
                "generation_prompt": "不应重传已有素材",
            }],
            "scenes": [{
                "scene_id": "SC01", "prompt": "参考产品图展示冰箱", "duration_sec": 8,
                "reference_asset_ids": [f"asset_material_{material_id}"],
            }],
        },
    )

    assert result.public_summary == "asset_registry 只能登记新的待生成资产；已有素材请使用 asset_updates"


@pytest.mark.asyncio
async def test_prepare_tool_rejects_partial_existing_material_record_with_actionable_hint() -> None:
    """错误提示应说明 DTO 角色，不再只暴露 asset_registry.0。"""

    result = await VideoToolRegistry((PrepareScenePackagesTool(),)).execute(
        VideoToolContext(
            user_id="user",
            workspace=VideoWorkspace(
                workspace_id="workspace-partial-material",
                conversation_id="conversation-partial-material",
            ),
        ),
        "prepare_scene_packages",
        {
            "script": "产品展示",
            "asset_registry": [{
                "asset_id": "asset_material_existing",
                "kind": "product_reference",
                "role": "冰箱产品图",
                "origin": "existing_material",
            }],
            "scenes": [{
                "scene_id": "SC01", "prompt": "展示冰箱", "duration_sec": 8,
                "reference_asset_ids": ["asset_material_existing"],
            }],
        },
    )

    assert result.public_summary == (
        "工具参数无效，请修正：asset_registry.0.generation_prompt（缺少必填字段）、"
        "asset_registry.0.origin（不允许该字段）"
    )
    assert result.model_observation["validation_hints"] == [
        "asset_registry.0.generation_prompt（缺少必填字段）",
        "asset_registry.0.origin（不允许该字段）",
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
            "asset_registry": [{"asset_id": "asset-product", "kind": "product", "role": "冰箱", "generation_prompt": "冰箱产品设定图"}],
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


@pytest.mark.asyncio
async def test_material_append_immediately_registers_existing_asset() -> None:
    repository = MemoryVideoAgentRepository()
    workspace = await repository.create_workspace(
        "user",
        VideoWorkspace(workspace_id="workspace-material-append", conversation_id="conversation-material-append"),
    )
    updated = await repository.apply_workspace_patch(
        "user",
        workspace.workspace_id,
        {"materials_append": [{
            "material_id": "m20-reference",
            "kind": "image",
            "name": "M20 冰箱参考图",
            "reference_label": "@产品图1",
            "url": "https://example.invalid/m20.jpg",
        }]},
        expected_revision=workspace.revision,
        now=workspace.updated_at,
    )

    assert updated.payload["asset_registry"] == [{
        "asset_id": "asset_material_m20-reference",
        "slot": "@产品图1",
        "kind": "reference_image",
        "role": "M20 冰箱参考图",
        "origin": "existing_material",
        "source_material_id": "m20-reference",
        "state": "ready",
        "reference_asset_ids": [],
        "provider_artifact_ref": "artifact:material:m20-reference",
        "usable_for_video": True,
    }]
