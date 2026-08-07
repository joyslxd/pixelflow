from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import (
    GenerateScenesTool,
    InspectSceneTool,
    PatchSceneTool,
    ReplaceProjectAssetsTool,
    ReviewGeneratedScenesTool,
    SceneGenerationJob,
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolRegistry,
    VideoToolValidationError,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _context() -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-1",
        step_id="step-generate-scenes",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "assets": [
                    {"artifact_ref": "artifact:product-old", "media_type": "image"},
                    {"artifact_ref": "artifact:product-new", "media_type": "image"},
                ],
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "prompt": "保持不变的开场",
                        "asset_refs": ["artifact:product-old"],
                        "variants": [
                            {
                                "variant_id": "scene-1-v1",
                                "artifact_ref": "artifact:scene-1-v1",
                                "review_status": "approved",
                                "selected": True,
                            }
                        ],
                    },
                    {
                        "scene_id": "scene-3",
                        "scene_index": 3,
                        "prompt": "需要修复的商品特写",
                        "asset_refs": ["artifact:product-old"],
                        "qc_issues": ["商品边缘闪烁", "主体偏离画面中心"],
                        "repair_suggestion": "固定主体位置并降低镜头晃动。",
                        "variants": [
                            {
                                "variant_id": "scene-3-v1",
                                "artifact_ref": "artifact:scene-3-v1",
                                "review_status": "pending",
                                "selected": False,
                            }
                        ],
                    },
                ],
                "dirty_scene_ids": ["scene-3"],
            },
        ),
    )


class FakeSceneGenerationOperationPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def start_scene_variant(
        self,
        context: VideoToolContext,
        *,
        scene: dict,
        variant_index: int,
        attempt: int,
    ) -> SceneGenerationJob:
        del context, attempt
        scene_id = str(scene["scene_id"])
        self.calls.append((scene_id, variant_index))
        return SceneGenerationJob(
            job_id=f"job-{scene_id}-{variant_index}",
            scene_id=scene_id,
            variant_index=variant_index,
            status="succeeded",
            variant_id=f"{scene_id}-v{variant_index + 1}",
            artifact_ref=f"artifact:{scene_id}-v{variant_index + 1}",
            video_url=f"https://cdn.example.invalid/{scene_id}-v{variant_index + 1}.mp4",
            completed_at=NOW,
        )


@pytest.mark.asyncio
async def test_inspect_scene_returns_repairable_safe_evidence() -> None:
    result = await InspectSceneTool().execute(
        _context(),
        {"scene_id": "scene-3"},
    )

    evidence = result.workspace_patch["qc"]["scene-3"]
    assert evidence["issues"] == ["商品边缘闪烁", "主体偏离画面中心"]
    assert evidence["repair_suggestion"] == "固定主体位置并降低镜头晃动。"
    assert evidence["affected_assets"] == ["artifact:product-old"]
    assert evidence["evidence_refs"] == ["artifact:scene-3-v1"]
    assert "prompt" not in evidence


@pytest.mark.asyncio
async def test_patch_scene_changes_only_target_and_marks_it_dirty() -> None:
    result = await PatchSceneTool().execute(
        _context(),
        {
            "scene_id": "scene-3",
            "patch": {
                "prompt": "固定商品主体，使用稳定推进镜头",
                "camera_movement": "缓慢推进",
            },
        },
    )

    scenes = result.workspace_patch["scenes"]
    assert scenes[0] == _context().workspace.payload["scenes"][0]
    assert scenes[1]["prompt"] == "固定商品主体，使用稳定推进镜头"
    assert scenes[1]["camera_movement"] == "缓慢推进"
    assert scenes[1]["variants"] == _context().workspace.payload["scenes"][1]["variants"]
    assert scenes[1]["edit_status"] == "待重新生成"
    assert result.workspace_patch["dirty_scene_ids"] == ["scene-3"]


@pytest.mark.asyncio
async def test_patch_scene_rejects_undeclared_mutation_fields() -> None:
    result = await VideoToolRegistry([PatchSceneTool()]).execute(
        _context(),
        "patch_scene",
        {
            "scene_id": "scene-3",
            "patch": {"provider_job_id": "provider-secret-job"},
        },
    )

    assert result.public_summary == "工具参数无效，请修正后重试"
    assert result.workspace_patch == {}
    assert "provider-secret-job" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_replace_project_assets_only_updates_referencing_scenes() -> None:
    result = await ReplaceProjectAssetsTool().execute(
        _context(),
        {
            "replacements": [
                {
                    "source_asset_ref": "artifact:product-old",
                    "target_asset_ref": "artifact:product-new",
                }
            ]
        },
    )

    scenes = result.workspace_patch["scenes"]
    assert scenes[0]["asset_refs"] == ["artifact:product-new"]
    assert scenes[1]["asset_refs"] == ["artifact:product-new"]
    assert scenes[0]["variants"] == _context().workspace.payload["scenes"][0]["variants"]
    assert scenes[1]["variants"] == _context().workspace.payload["scenes"][1]["variants"]
    assert result.workspace_patch["dirty_scene_ids"] == ["scene-3", "scene-1"]
    assert result.workspace_patch["asset_replacements"][0][
        "affected_scene_ids"
    ] == ["scene-1", "scene-3"]
    assert result.requires_confirmation is True
    assert ReplaceProjectAssetsTool.spec.cost_level is VideoToolCostLevel.DESTRUCTIVE


@pytest.mark.asyncio
async def test_generate_scenes_requires_confirmation_and_scopes_operation_ids() -> None:
    operation_port = FakeSceneGenerationOperationPort()
    tool = GenerateScenesTool(operation_port=operation_port)

    result = await tool.execute(
        _context(),
        {"scene_ids": ["scene-3"], "variant_count": 3},
    )

    scenes = result.workspace_patch["scenes"]
    assert tool.spec.confirmation_required is True
    assert tool.spec.cost_level is VideoToolCostLevel.BILLABLE
    assert result.requires_confirmation is True
    assert operation_port.calls == [
        ("scene-3", 1),
        ("scene-3", 2),
        ("scene-3", 3),
    ]
    assert "generation_jobs" not in scenes[0]
    assert [item["job_id"] for item in scenes[1]["generation_jobs"]] == [
        "job-scene-3-1",
        "job-scene-3-2",
        "job-scene-3-3",
    ]
    assert len(scenes[1]["variants"]) == 4
    assert scenes[1]["variants"][-1]["video_url"].endswith("scene-3-v4.mp4")
    assert result.workspace_patch["assets"][-1]["scene_id"] == "scene-3"


@pytest.mark.asyncio
async def test_generate_scenes_rejects_unknown_id_before_operation() -> None:
    operation_port = FakeSceneGenerationOperationPort()
    tool = GenerateScenesTool(operation_port=operation_port)

    with pytest.raises(VideoToolValidationError, match="镜头不存在"):
        await tool.execute(
            _context(),
            {"scene_ids": ["scene-missing"], "variant_count": 3},
        )

    assert operation_port.calls == []


@pytest.mark.asyncio
async def test_review_generated_scene_preserves_history_and_marks_regeneration() -> None:
    generated = await GenerateScenesTool(
        operation_port=FakeSceneGenerationOperationPort(),
    ).execute(
        _context(),
        {"scene_ids": ["scene-3"], "variant_count": 1},
    )
    review_context = VideoToolContext(
        user_id="user-1",
        workspace=_context().workspace.model_copy(
            update={
                "payload": {
                    **_context().workspace.payload,
                    **generated.workspace_patch,
                }
            }
        ),
    )

    reviewed = await ReviewGeneratedScenesTool(clock=lambda: NOW).execute(
        review_context,
        {
            "scene_id": "scene-3",
            "variant_id": "scene-3-v2",
            "decision": "approve",
        },
    )

    target = reviewed.workspace_patch["scenes"][1]
    assert [item["variant_id"] for item in target["variants"]] == [
        "scene-3-v1",
        "scene-3-v2",
    ]
    assert target["approved_variant_id"] == "scene-3-v2"
    assert target["edit_status"] == "重新生成完成"
    assert target["regenerated_at"] == NOW.isoformat()
    assert reviewed.workspace_patch["dirty_scene_ids"] == []
