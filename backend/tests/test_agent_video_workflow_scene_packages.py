from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import pixelflow.agent_workflows.video.scene_packages as scene_package_module
from pixelflow.agent_runtime.contracts import WorkflowKind, WorkflowStatus
from pixelflow.agent_workflows.video import (
    VideoPlanAuthoritySnapshot,
    VideoPlanningWorkflowService,
    VideoScenePackageAuthoritySnapshot,
    VideoScenePackageStage,
    VideoScenePackageWorkflowService,
)
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.intake.forms import draft_creative_directions, validate_form

VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 30,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_model_capabilities": {
        "generation_types": ["text_to_video", "image_to_video"],
        "upload_file_types": ["image"],
        "aspect_ratios": ["9:16", "16:9"],
        "sizes": ["1080p"],
        "sound_options": ["on", "off"],
        "durations_sec": [4, 5, 6, 8, 10, 12, 15],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


def _plan_review_state():
    planning = VideoPlanningWorkflowService()
    now = datetime(2026, 7, 28, 3, 0, tzinfo=UTC)
    state = planning.start(
        workflow_id="wf-video-scene-package",
        conversation_id="conv-video-scene-package",
        intent="video",
        intake_context={"source_prompt": "生成一条智能戒指新品广告"},
        now=now,
    )
    state = planning.confirm_intake(
        state,
        validate_form("video", VIDEO_FORM),
        now=now + timedelta(seconds=1),
    )
    directions = draft_creative_directions("video", VIDEO_FORM)
    state = planning.publish_directions(state, directions, now=now + timedelta(seconds=2))
    state = planning.select_direction(state, "direction_1", now=now + timedelta(seconds=3))
    plan = _with_concrete_asset_names(build_plan_markdown("video", VIDEO_FORM, state.selected_direction))
    return planning.publish_initial_plan(state, plan, now=now + timedelta(seconds=4))


def _approved_plan_state():
    state = _plan_review_state()
    return VideoPlanningWorkflowService().approve_plan(
        state,
        now=state.updated_at + timedelta(seconds=1),
    )


def _with_concrete_asset_names(result):
    blueprints = copy.deepcopy(result.scene_blueprints)
    manifest = copy.deepcopy(result.asset_manifest)
    replacements = {
        "目标用户": "都市健康管理师林岚",
        "真实使用场景": "晨间公寓健康监测区",
    }
    for blueprint in blueprints:
        requirements = blueprint["asset_requirements"]
        for collection in ("characters", "scenes", "props"):
            requirements[collection] = [replacements.get(name, name) for name in requirements[collection]]
        for source, target in replacements.items():
            blueprint["shot_description"] = blueprint["shot_description"].replace(source, target)
            blueprint["storyline"] = blueprint["storyline"].replace(source, target)
            blueprint["narration"] = blueprint["narration"].replace(source, target)
    for collection in ("characters", "scenes", "props"):
        for item in manifest[collection]:
            old_name = item["name"]
            new_name = replacements.get(old_name, old_name)
            item["name"] = new_name
            for field_name in ("description", "three_view_prompt", "image_prompt"):
                if field_name in item:
                    item[field_name] = item[field_name].replace(old_name, new_name)
    manifest = normalize_asset_manifest(manifest, blueprints)
    history = copy.deepcopy(result.plan_history)
    history[-1]["scene_blueprints"] = copy.deepcopy(blueprints)
    history[-1]["asset_manifest"] = copy.deepcopy(manifest)
    return replace(
        result,
        scene_blueprints=blueprints,
        asset_manifest=manifest,
        plan_history=history,
    )


def _manifest_items(manifest: dict) -> list[dict]:
    return list(manifest["characters"]) + list(manifest["scenes"]) + list(manifest["props"])


def _generated_global_assets(prepared_assets: dict) -> dict:
    assets = copy.deepcopy(prepared_assets)
    for item in assets["characters"]:
        item["three_view_images"] = [f"https://assets.example.com/{item['asset_id']}.png"]
    for collection in ("scenes", "props"):
        for item in assets[collection]:
            item["images"] = [f"https://assets.example.com/{item['asset_id']}.png"]
    return assets


def _restore_asset_names(text: str, global_assets: dict) -> str:
    restored = text
    for item in _manifest_items(global_assets):
        restored = restored.replace(f"@{item['asset_id']}", item["name"])
    return restored


def test_approved_plan_mechanically_builds_scene_packages_and_global_asset_graph():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    materials = [
        {
            "type": "image",
            "url": "https://materials.example.com/ring-reference.png",
            "name": "用户上传参考图.png",
        }
    ]

    state = service.prepare_from_approved_plan(
        planning_state,
        materials=materials,
        now=planning_state.updated_at + timedelta(seconds=1),
    )

    assert state.current_stage is VideoScenePackageStage.GENERATE_SCENE_ASSETS
    assert state.status is WorkflowStatus.RUNNING
    assert state.stage_version == planning_state.stage_version + 1
    assert state.context_version == planning_state.context_version + 1
    assert state.source_plan.checksum == planning_state.active_plan.checksum
    assert state.scene_package.source_plan_checksum == planning_state.active_plan.checksum
    assert state.scene_package.target_duration_ms == 30_000
    assert state.scene_package.creation_contract == planning_state.active_plan.creation_contract

    manifest = planning_state.active_plan.asset_manifest
    global_assets = state.scene_package.global_assets
    for collection, image_field in (
        ("characters", "three_view_images"),
        ("scenes", "images"),
        ("props", "images"),
    ):
        assert [{key: value for key, value in item.items() if key != image_field} for item in global_assets[collection]] == manifest[collection]
        assert all(item[image_field] == [] for item in global_assets[collection])

    all_ids = [item["asset_id"] for item in _manifest_items(manifest)]
    all_graph_ids = [item["asset_id"] for item in _manifest_items(global_assets)]
    assert all_graph_ids == all_ids
    assert len(set([*all_graph_ids, global_assets["visual_style"]["asset_id"]])) == len(all_graph_ids) + 1

    manifest_by_name = {(collection, item["name"]): item["asset_id"] for collection in ("characters", "scenes", "props") for item in manifest[collection]}
    for scene, blueprint in zip(
        state.scene_package.scene_packages,
        planning_state.active_plan.scene_blueprints,
        strict=True,
    ):
        expected_refs = [manifest_by_name[(collection, name)] for collection in ("characters", "scenes", "props") for name in blueprint["asset_requirements"][collection]]
        assert scene["scene_id"] == blueprint["scene_id"]
        assert scene["scene_index"] == blueprint["scene_index"]
        assert scene["title"] == blueprint["title"]
        assert scene["duration_ms"] == blueprint["duration_sec"] * 1000
        assert scene["storyline"] == blueprint["storyline"]
        assert _restore_asset_names(
            scene["shot_description"]["text"],
            global_assets,
        ).startswith(blueprint["shot_description"])
        assert scene["narration"] == blueprint["narration"]
        assert scene["transition"] == blueprint["transition"]
        assert planning_state.active_plan.creation_contract["video_model"] in scene["prompt"]
        assert scene["reference_asset_ids"] == expected_refs
        assert len(scene["reference_asset_ids"]) <= 9
        assert [item["asset_id"] for item in scene["shot_description"]["mentions"]] == expected_refs
        assert scene["image_urls"] == [materials[0]["url"]]


def test_scene_package_snapshot_isolated_from_plan_materials_and_property_mutation():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    materials = [{"type": "image", "url": "https://materials.example.com/source.png"}]

    state = service.prepare_from_approved_plan(
        planning_state,
        materials=materials,
        now=planning_state.updated_at + timedelta(seconds=1),
    )
    checksum = state.scene_package.checksum
    package_copy = state.scene_package.to_dict()

    materials[0]["url"] = "https://attacker.example.com/changed.png"
    package_copy["global_assets"]["props"][0]["name"] = "被污染名称"
    state.scene_package.scene_packages[0]["storyline"] = "调用方属性副本污染"
    planning_state.active_plan.to_dict()["asset_manifest"]["props"][0]["name"] = "计划副本污染"

    assert state.scene_package.checksum == checksum
    assert state.scene_package.global_assets["props"][0]["name"] != "被污染名称"
    assert state.scene_package.scene_packages[0]["storyline"] != "调用方属性副本污染"
    assert state.scene_package.scene_packages[0]["image_urls"] == ["https://materials.example.com/source.png"]


def test_scene_package_boundary_rejects_unapproved_or_forged_plan_authority():
    planning_state = _plan_review_state()
    service = VideoScenePackageWorkflowService()

    with pytest.raises(ValueError, match="显式同意"):
        service.prepare_from_approved_plan(
            planning_state,
            now=planning_state.updated_at + timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="等待人工审核"):
        VideoPlanningWorkflowService().approve_plan(
            replace(planning_state, status=WorkflowStatus.RUNNING),
            now=planning_state.updated_at + timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="显式同意"):
        service.prepare_from_approved_plan(
            replace(planning_state, status=WorkflowStatus.RUNNING),
            now=planning_state.updated_at + timedelta(seconds=1),
        )

    planning_state = VideoPlanningWorkflowService().approve_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )

    forged_payload = planning_state.active_plan.to_dict()
    extra_assets = []
    for index in range(10):
        name = f"超限道具{index + 1}"
        extra_assets.append(
            {
                "asset_id": f"prop-overflow-{index + 1}",
                "name": name,
                "description": f"{name}说明",
                "image_prompt": f"{name}参考图",
            }
        )
    for blueprint in forged_payload["scene_blueprints"]:
        blueprint["asset_requirements"] = {
            "characters": [],
            "scenes": [],
            "props": [item["name"] for item in extra_assets],
        }
    forged_payload["asset_manifest"] = normalize_asset_manifest(
        {
            "characters": [],
            "scenes": [],
            "props": extra_assets,
        },
        forged_payload["scene_blueprints"],
    )
    forged_payload["plan_history"][-1]["scene_blueprints"] = copy.deepcopy(forged_payload["scene_blueprints"])
    forged_payload["plan_history"][-1]["asset_manifest"] = copy.deepcopy(forged_payload["asset_manifest"])
    forged_state = replace(
        planning_state,
        _active_plan=VideoPlanAuthoritySnapshot(forged_payload),
    )

    with pytest.raises(ValueError, match="最多允许 9 个"):
        service.prepare_from_approved_plan(
            forged_state,
            now=planning_state.updated_at + timedelta(seconds=1),
        )


def test_scene_package_boundary_rejects_plan_history_drift_hidden_behind_valid_current_fields():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    forged_payload = planning_state.active_plan.to_dict()
    forged_payload["plan_history"][-1]["creation_contract"]["confirmed_by_user"] = False
    forged_state = replace(
        planning_state,
        _active_plan=VideoPlanAuthoritySnapshot(forged_payload),
    )

    with pytest.raises(ValueError, match="历史|用户确认|权威"):
        service.prepare_from_approved_plan(
            forged_state,
            now=planning_state.updated_at + timedelta(seconds=1),
        )


def test_scene_package_boundary_rejects_arbitrary_story_suffix_from_preparation(monkeypatch):
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    original_prepare = scene_package_module.prepare_video_scene_packages

    def tampered_prepare(**kwargs):
        prepared = original_prepare(**kwargs)
        prepared["scene_packages"][0]["shot_description"]["text"] += " 供应商擅自追加全新故事。"
        return prepared

    monkeypatch.setattr(
        scene_package_module,
        "prepare_video_scene_packages",
        tampered_prepare,
    )

    with pytest.raises(ValueError, match="镜头描述必须逐字继承 Plan"):
        service.prepare_from_approved_plan(
            planning_state,
            now=planning_state.updated_at + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda prepared: prepared["scene_packages"][0].update(prompt="供应商改写执行提示词"),
        lambda prepared: prepared["scene_packages"][0].update(supplier_payload={"unsafe": True}),
        lambda prepared: prepared["scene_packages"][0]["shot_description"].update(supplier_note="额外镜头指令"),
        lambda prepared: prepared["global_assets"].update(supplier_assets=[]),
    ],
)
def test_scene_package_boundary_rejects_prompt_or_extra_supplier_fields(monkeypatch, mutate):
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    original_prepare = scene_package_module.prepare_video_scene_packages

    def tampered_prepare(**kwargs):
        prepared = original_prepare(**kwargs)
        mutate(prepared)
        return prepared

    monkeypatch.setattr(
        scene_package_module,
        "prepare_video_scene_packages",
        tampered_prepare,
    )

    with pytest.raises(ValueError, match="不得|字段|逐字继承"):
        service.prepare_from_approved_plan(
            planning_state,
            now=planning_state.updated_at + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda prepared: prepared["scene_packages"][0].update(scene_index=True),
        lambda prepared: prepared["scene_packages"][0].update(duration_ms=10_000.0),
        lambda prepared: prepared.update(target_duration_ms=30_000.0),
    ],
)
def test_scene_package_boundary_rejects_boolean_or_float_integer_fields(monkeypatch, mutate):
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    original_prepare = scene_package_module.prepare_video_scene_packages

    def tampered_prepare(**kwargs):
        prepared = original_prepare(**kwargs)
        mutate(prepared)
        return prepared

    monkeypatch.setattr(
        scene_package_module,
        "prepare_video_scene_packages",
        tampered_prepare,
    )

    with pytest.raises(ValueError, match="必须是整数"):
        service.prepare_from_approved_plan(
            planning_state,
            now=planning_state.updated_at + timedelta(seconds=1),
        )


def test_scene_package_boundary_rejects_material_url_replacement(monkeypatch):
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    original_prepare = scene_package_module.prepare_video_scene_packages

    def tampered_prepare(**kwargs):
        prepared = original_prepare(**kwargs)
        for scene in prepared["scene_packages"]:
            scene["image_urls"] = ["https://attacker.example.com/replaced.png"]
        return prepared

    monkeypatch.setattr(
        scene_package_module,
        "prepare_video_scene_packages",
        tampered_prepare,
    )

    with pytest.raises(ValueError, match="素材图片.*继承"):
        service.prepare_from_approved_plan(
            planning_state,
            materials=[{"type": "image", "url": "https://materials.example.com/source.png"}],
            now=planning_state.updated_at + timedelta(seconds=1),
        )


def test_generated_global_asset_images_are_bound_once_and_projected_for_review():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    generating = service.prepare_from_approved_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )

    review = service.publish_generated_asset_images(
        generating,
        _generated_global_assets(generating.scene_package.global_assets),
        now=generating.updated_at + timedelta(seconds=1),
    )
    projection = service.to_workflow_record(review)

    assert review.current_stage is VideoScenePackageStage.SCENE_PACKAGE_REVIEW
    assert review.status is WorkflowStatus.AWAITING_USER
    assert review.scene_package.source_plan_checksum == planning_state.active_plan.checksum
    assert projection.kind is WorkflowKind.VIDEO
    assert projection.current_stage == "scene_package_review"
    assert projection.pending_external_job is None
    assert projection.creation_contract_snapshot == planning_state.active_plan.creation_contract
    assert projection.latest_artifact_refs == [
        planning_state.active_plan_artifact_ref,
        review.scene_package_artifact_ref,
    ]

    url_by_id = {item["asset_id"]: (item.get("three_view_images") or item.get("images"))[0] for item in _manifest_items(review.scene_package.global_assets)}
    for scene in review.scene_package.scene_packages:
        assert len(scene["reference_asset_ids"]) <= 9
        assert [item["asset_id"] for item in scene["shot_description"]["mentions"]] == scene["reference_asset_ids"]
        assert [item["image_url"] for item in scene["shot_description"]["mentions"]] == [url_by_id[asset_id] for asset_id in scene["reference_asset_ids"]]


@pytest.mark.parametrize("at_review", [False, True])
def test_scene_package_cancel_preserves_authoritative_snapshots(at_review: bool) -> None:
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    state = service.prepare_from_approved_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )
    if at_review:
        state = service.publish_generated_asset_images(
            state,
            _generated_global_assets(state.scene_package.global_assets),
            now=state.updated_at + timedelta(seconds=1),
        )

    cancelled = service.cancel(
        state,
        now=state.updated_at + timedelta(seconds=1),
    )

    assert cancelled.current_stage is state.current_stage
    assert cancelled.status is WorkflowStatus.CANCELLED
    assert cancelled.stage_version == state.stage_version + 1
    assert cancelled.context_version == state.context_version + 1
    assert cancelled.updated_at > state.updated_at
    assert cancelled.source_plan == state.source_plan
    assert cancelled.source_plan_artifact_ref == state.source_plan_artifact_ref
    assert cancelled.scene_package == state.scene_package
    assert service.to_workflow_record(cancelled).status is WorkflowStatus.CANCELLED

    with pytest.raises(ValueError, match="终态"):
        service.cancel(cancelled, now=cancelled.updated_at + timedelta(seconds=1))


def test_generated_asset_publication_revalidates_restored_scene_package_authority():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    generating = service.prepare_from_approved_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )
    forged_payload = generating.scene_package.to_dict()
    forged_payload["source_plan_checksum"] = "0" * 64
    forged_state = replace(
        generating,
        _scene_package=VideoScenePackageAuthoritySnapshot(forged_payload),
    )

    with pytest.raises(ValueError, match="来源 Plan|权威快照"):
        service.publish_generated_asset_images(
            forged_state,
            _generated_global_assets(generating.scene_package.global_assets),
            now=generating.updated_at + timedelta(seconds=1),
        )


def test_review_projection_revalidates_scene_package_authority():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    generating = service.prepare_from_approved_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )
    review = service.publish_generated_asset_images(
        generating,
        _generated_global_assets(generating.scene_package.global_assets),
        now=generating.updated_at + timedelta(seconds=1),
    )
    forged_payload = review.scene_package.to_dict()
    forged_payload["creation_contract"]["video_duration_sec"] = 99
    forged_review = replace(
        review,
        _scene_package=VideoScenePackageAuthoritySnapshot(forged_payload),
    )

    with pytest.raises(ValueError, match="权威|创作合同|Plan"):
        service.to_workflow_record(forged_review)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda assets: assets["props"][0].update(name="供应商改写名称"),
            "不得改写",
        ),
        (
            lambda assets: assets["props"][0].update(images=[]),
            "恰好绑定一张",
        ),
        (
            lambda assets: assets["props"][0].update(
                images=[
                    "https://assets.example.com/one.png",
                    "https://assets.example.com/two.png",
                ]
            ),
            "恰好绑定一张",
        ),
        (
            lambda assets: assets["props"][0].update(images=["http://unsafe.example.com/image.png"]),
            "HTTPS",
        ),
        (
            lambda assets: assets["props"][0].update(images=["  https://assets.example.com/image.png  "]),
            "规范值",
        ),
    ],
)
def test_generated_asset_result_rejects_contract_drift_or_invalid_image_binding(mutate, message):
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    generating = service.prepare_from_approved_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )
    assets = _generated_global_assets(generating.scene_package.global_assets)
    mutate(assets)

    with pytest.raises(ValueError, match=message):
        service.publish_generated_asset_images(
            generating,
            assets,
            now=generating.updated_at + timedelta(seconds=1),
        )

    assert generating.current_stage is VideoScenePackageStage.GENERATE_SCENE_ASSETS
    assert generating.scene_package.global_assets["props"][0]["images"] == []


def test_scene_package_transitions_reject_wrong_stage_and_time_regression():
    planning_state = _approved_plan_state()
    service = VideoScenePackageWorkflowService()
    generating = service.prepare_from_approved_plan(
        planning_state,
        now=planning_state.updated_at + timedelta(seconds=1),
    )
    assets = _generated_global_assets(generating.scene_package.global_assets)

    with pytest.raises(ValueError, match="更新时间"):
        service.publish_generated_asset_images(
            generating,
            assets,
            now=generating.updated_at - timedelta(seconds=1),
        )

    review = service.publish_generated_asset_images(
        generating,
        assets,
        now=generating.updated_at + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="generate_scene_assets"):
        service.publish_generated_asset_images(
            review,
            assets,
            now=review.updated_at + timedelta(seconds=1),
        )
