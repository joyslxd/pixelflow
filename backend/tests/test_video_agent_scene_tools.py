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


def _scene_by_id(scenes: object, scene_id: str) -> dict:
    assert isinstance(scenes, list)
    for item in scenes:
        if isinstance(item, dict) and item.get("scene_id") == scene_id:
            return item
    raise AssertionError(f"missing scene {scene_id}")


def _merge_payload_scenes(base: dict, patch: dict) -> dict:
    """测试里模拟 repository 按 scene_id 合并，避免只含变更镜的 patch 冲掉其它镜。"""

    merged = {**base, **patch}
    for key in ("scenes", "scene_packages"):
        if key not in patch:
            continue
        existing = base.get(key) if isinstance(base.get(key), list) else []
        incoming = patch.get(key) if isinstance(patch.get(key), list) else []
        by_id = {
            str(item.get("scene_id") or ""): dict(item)
            for item in existing
            if isinstance(item, dict)
        }
        order = [str(item.get("scene_id") or "") for item in existing if isinstance(item, dict)]
        for item in incoming:
            if not isinstance(item, dict):
                continue
            scene_id = str(item.get("scene_id") or "")
            if not scene_id:
                continue
            if scene_id not in by_id:
                order.append(scene_id)
            by_id[scene_id] = dict(item)
        merged[key] = [by_id[scene_id] for scene_id in order if scene_id in by_id]
    return merged


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
    assert [item["scene_id"] for item in scenes] == ["scene-3"]
    target = scenes[0]
    assert target["prompt"] == "固定商品主体，使用稳定推进镜头"
    assert target["camera_movement"] == "缓慢推进"
    assert target["variants"] == _context().workspace.payload["scenes"][1]["variants"]
    assert target["edit_status"] == "待重新生成"
    assert result.workspace_patch["dirty_scene_ids"] == ["scene-3"]


@pytest.mark.asyncio
async def test_patch_scene_via_registry_keeps_optional_fields_unset() -> None:
    """Registry dump 不得把未设 Optional 填成 null，否则二次校验会误报镜头补丁参数无效。"""

    result = await VideoToolRegistry([PatchSceneTool()]).execute(
        _context(),
        "patch_scene",
        {
            "scene_id": "scene-3",
            "patch": {
                "shot_description": "0-10秒: 画面：安然盯着手机。\n旁白（对白）：安然：如果失败呢？",
                "reference_asset_ids": ["character-1"],
            },
        },
    )
    assert "参数无效" not in result.public_summary
    target = _scene_by_id(result.workspace_patch["scenes"], "scene-3")
    assert "旁白（对白）" in target["shot_description"]["text"]
    assert target["reference_asset_ids"] == ["character-1"]
    assert target["shot_description"]["mentions"][0]["asset_id"] == "character-1"


@pytest.mark.asyncio
async def test_patch_scene_writes_shot_description_text_and_prompt() -> None:
    result = await PatchSceneTool().execute(
        _context(),
        {
            "scene_id": "scene-3",
            "patch": {
                "shot_description": "0-10秒: 画面：安然盯着手机。",
                "narration": "安然：如果失败呢？",
            },
        },
    )

    target = _scene_by_id(result.workspace_patch["scenes"], "scene-3")
    assert target["shot_description"]["text"] == "0-10秒: 画面：安然盯着手机。"
    assert target["prompt"] == "0-10秒: 画面：安然盯着手机。"
    assert target["narration"] == "安然：如果失败呢？"
    assert result.workspace_patch["dirty_scene_ids"] == ["scene-3"]


@pytest.mark.asyncio
async def test_patch_scene_accepts_shot_description_object_from_model() -> None:
    """模型偶发传 {text, mentions} 时仍应落库 text，不得整单校验失败。"""

    result = await PatchSceneTool().execute(
        _context(),
        {
            "scene_id": "scene-3",
            "patch": {
                "shot_description": {
                    "text": "0-10秒: 角色:@安然 盯着手机。",
                    "mentions": [{"asset_id": "character-1", "name": "安然"}],
                },
            },
        },
    )
    target = _scene_by_id(result.workspace_patch["scenes"], "scene-3")
    assert target["shot_description"]["text"] == "0-10秒: 角色:@安然 盯着手机。"


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
    assert {item["scene_id"] for item in scenes} == {"scene-1", "scene-3"}
    assert _scene_by_id(scenes, "scene-1")["asset_refs"] == ["artifact:product-new"]
    assert _scene_by_id(scenes, "scene-3")["asset_refs"] == ["artifact:product-new"]
    assert _scene_by_id(scenes, "scene-1")["variants"] == _context().workspace.payload["scenes"][0]["variants"]
    assert _scene_by_id(scenes, "scene-3")["variants"] == _context().workspace.payload["scenes"][1]["variants"]
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
    # 只写变更镜；未选中的 scene-1 不在补丁里。
    assert [item["scene_id"] for item in scenes] == ["scene-3"]
    target = scenes[0]
    assert [item["job_id"] for item in target["generation_jobs"]] == [
        "job-scene-3-1",
        "job-scene-3-2",
        "job-scene-3-3",
    ]
    assert len(target["variants"]) == 4
    assert target["variants"][-1]["video_url"].endswith("scene-3-v4.mp4")
    assert result.workspace_patch["assets"][-1]["scene_id"] == "scene-3"
    # 单镜启动须带 scene_id，前端可立刻给该镜盖「生成中」蒙版。
    assert result.workspace_patch["scene_video_progress"]["scene_id"] == "scene-3"
    assert result.workspace_patch["scene_video_progress"]["scene_index"] == 3


@pytest.mark.asyncio
async def test_generate_scenes_rejects_partial_scene_asset_package() -> None:
    operation_port = FakeSceneGenerationOperationPort()
    base = _context()
    context = VideoToolContext(
        user_id=base.user_id,
        plan_id=base.plan_id,
        step_id=base.step_id,
        workspace=base.workspace.model_copy(
            update={
                "payload": {
                    **dict(base.workspace.payload),
                    "global_assets": {
                        "characters": [
                            {
                                "asset_id": "character-host",
                                "three_view_images": ["https://cdn.example/host.png"],
                            }
                        ],
                        "scenes": [{"asset_id": "scene-room", "images": []}],
                        "props": [],
                    },
                }
            }
        ),
    )

    with pytest.raises(VideoToolValidationError, match="参考图仅完成 1/2"):
        await GenerateScenesTool(operation_port=operation_port).execute(
            context,
            {"scene_ids": ["scene-3"], "variant_count": 1},
        )

    assert operation_port.calls == []


@pytest.mark.asyncio
async def test_generate_scenes_progress_omits_scene_id_for_multi_scene_batch() -> None:
    """多镜一批启动时不写单一 scene_id，避免误蒙某一镜。"""

    operation_port = FakeSceneGenerationOperationPort()
    tool = GenerateScenesTool(operation_port=operation_port)
    result = await tool.execute(
        _context(),
        {"scene_ids": ["scene-1", "scene-3"], "variant_count": 1},
    )
    progress = result.workspace_patch["scene_video_progress"]
    assert progress["scene_id"] is None
    assert progress["total"] >= 2

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
                "payload": _merge_payload_scenes(
                    dict(_context().workspace.payload),
                    dict(generated.workspace_patch),
                )
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

    target = _scene_by_id(reviewed.workspace_patch["scenes"], "scene-3")
    assert [item["variant_id"] for item in target["variants"]] == [
        "scene-3-v1",
        "scene-3-v2",
    ]
    assert target["approved_variant_id"] == "scene-3-v2"
    assert target["edit_status"] == "重新生成完成"
    assert target["regenerated_at"] == NOW.isoformat()
    assert reviewed.workspace_patch["dirty_scene_ids"] == []


@pytest.mark.asyncio
async def test_patch_scene_mirrors_scene_packages_and_marks_dirty() -> None:
    result = await PatchSceneTool().execute(
        _context(),
        {
            "scene_id": "scene-3",
            "patch": {"storyline": "只改第三镜故事线", "duration_ms": 5000},
        },
    )
    target = _scene_by_id(result.workspace_patch["scenes"], "scene-3")
    assert target["storyline"] == "只改第三镜故事线"
    assert target["duration_sec"] == 5.0
    assert result.workspace_patch["scene_packages"] == result.workspace_patch["scenes"]
    assert result.workspace_patch["dirty_scene_ids"] == ["scene-3"]
    assert [item["scene_id"] for item in result.workspace_patch["scenes"]] == ["scene-3"]


@pytest.mark.asyncio
async def test_generate_scenes_uses_dirty_ids_when_scene_ids_omitted() -> None:
    operation_port = FakeSceneGenerationOperationPort()
    tool = GenerateScenesTool(operation_port=operation_port, clock=lambda: NOW)
    result = await tool.execute(_context(), {"variant_count": 1})
    assert operation_port.calls == [("scene-3", 1)]
    assert [item["scene_id"] for item in result.workspace_patch["scenes"]] == ["scene-3"]
    target = result.workspace_patch["scenes"][0]
    assert target["edit_status"] == "重新生成完成"
    assert target["approved_variant_id"] == "scene-3-v2"
    assert result.workspace_patch["dirty_scene_ids"] == []
    assert result.workspace_patch["scene_packages"] == result.workspace_patch["scenes"]


@pytest.mark.asyncio
async def test_generate_scenes_registry_accepts_quota_interrupt_root() -> None:
    """回归：成功 patch 含 quota_interrupt 时，不得再报「工具结果无效」。"""
    registry = VideoToolRegistry(
        [GenerateScenesTool(operation_port=FakeSceneGenerationOperationPort())]
    )
    result = await registry.execute(
        _context(),
        "generate_scenes",
        {"scene_ids": ["scene-3"], "variant_count": 1},
    )
    assert "quota_interrupt" in result.workspace_patch
    assert set(result.workspace_patch).issubset(
        {
            mutation.split(".", maxsplit=1)[0]
            for mutation in GenerateScenesTool.spec.workspace_mutations
        }
        | {"scenes_replace"}
    )
