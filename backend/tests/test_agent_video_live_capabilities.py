from __future__ import annotations

import copy
import json
import pickle
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pixelflow.agent_runtime.contracts import WorkflowStatus
from pixelflow.agent_workflows.video import (
    VideoPlanningWorkflowService,
    VideoScenePackageWorkflowService,
)
from pixelflow.agent_workflows.video.live_capabilities import (
    DefaultVideoLiveCapabilities,
    TransientTurnCredential,
)
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.intake.forms import draft_creative_directions, validate_form
from pixelflow.skills import ImageGenerationResult

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
MATERIALS = [
    {
        "type": "image",
        "url": "https://materials.example.com/ring.png",
        "name": "智能戒指参考图.png",
    }
]


class _TimeoutModel:
    def invoke(self, _prompt: object) -> object:
        raise TimeoutError("测试模型按约定触发确定性降级")


class _MemorySearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query_values, categories):
        self.calls.append({"query_values": list(query_values), "categories": list(categories)})
        return []


class _MemoryRecord:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record(self, *, summary, category, metadata):
        self.records.append({"summary": summary, "category": category, "metadata": dict(metadata)})


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


class _SceneAssetSkill:
    async def text_to_image(self, **kwargs):
        from app.gateway.content_app_auth_context import require_current_authorization

        assert require_current_authorization() == "Bearer turn-secret"
        prompt = str(kwargs["prompt"])
        suffix = "character" if "三视图" in prompt else "scene" if "场景" in prompt else "prop"
        return ImageGenerationResult(
            ok=True,
            images=[{"url": f"https://assets.example.com/{suffix}.png"}],
            raw={"provider": "fake"},
        )

    async def reference_image(self, **kwargs):
        return await self.text_to_image(**kwargs)


class _FailingSceneAssetSkill:
    async def text_to_image(self, **_kwargs):
        raise RuntimeError("Bearer turn-secret 供应商原始失败")

    async def reference_image(self, **_kwargs):
        raise RuntimeError("Bearer turn-secret 供应商原始失败")


def _capabilities(*, scene_asset_skill=None) -> DefaultVideoLiveCapabilities:
    return DefaultVideoLiveCapabilities(
        model_factory=lambda *_args, **_kwargs: _TimeoutModel(),
        scene_asset_skill=scene_asset_skill or _SceneAssetSkill(),
        memory_search=_MemorySearch(),
        memory_record=_MemoryRecord(),
        clock=_Clock(),
    )


def _concrete_plan():
    direction = draft_creative_directions("video", VIDEO_FORM)[0].to_dict()
    result = build_plan_markdown("video", VIDEO_FORM, direction)
    blueprints = copy.deepcopy(result.scene_blueprints)
    manifest = copy.deepcopy(result.asset_manifest)
    replacements = {
        "目标用户": "健康管理师林岚",
        "真实使用场景": "晨间公寓健康监测区",
    }
    for blueprint in blueprints:
        for collection in ("characters", "scenes", "props"):
            blueprint["asset_requirements"][collection] = [
                replacements.get(name, name)
                for name in blueprint["asset_requirements"][collection]
            ]
        for old_name, new_name in replacements.items():
            for field_name in ("shot_description", "storyline", "narration"):
                blueprint[field_name] = blueprint[field_name].replace(old_name, new_name)
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


def _planning_state(*, approved: bool = False):
    service = VideoPlanningWorkflowService()
    now = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    state = service.start(
        workflow_id="wf-live-video",
        conversation_id="conv-live-video",
        intent="video",
        intake_context={"materials": MATERIALS},
        now=now,
    )
    state = service.confirm_intake(state, validate_form("video", VIDEO_FORM), now=now + timedelta(seconds=1))
    directions = draft_creative_directions("video", VIDEO_FORM)
    state = service.publish_directions(state, directions, now=now + timedelta(seconds=2))
    state = service.select_direction(state, "direction_1", now=now + timedelta(seconds=3))
    state = service.publish_initial_plan(state, _concrete_plan(), now=now + timedelta(seconds=4))
    if approved:
        state = service.approve_plan(state, now=now + timedelta(seconds=5))
    return state


@pytest.mark.asyncio
async def test_default_capabilities_feed_m11_planning_dtos() -> None:
    capabilities = _capabilities()

    validation = await capabilities.validate_intake(VIDEO_FORM, intake_rounds=0)
    directions = await capabilities.generate_directions(validation.values, {})
    plan = await capabilities.generate_initial_plan(
        form_values=validation.values,
        selected_direction=directions[0].to_dict(),
        intake_context={},
        materials=MATERIALS,
    )

    assert validation.is_complete
    assert len(directions) == 3
    assert plan.error is None
    assert sum(plan.scene_durations_sec) == validation.values["video_duration_sec"]


@pytest.mark.asyncio
async def test_default_capabilities_revise_and_restore_authoritative_plan() -> None:
    capabilities = _capabilities()
    state = _planning_state()

    revision = await capabilities.revise_plan(state, revision_feedback="把风格保持为电影写实风")
    restored = await capabilities.restore_plan(state, plan_version=1)

    assert revision.plan_version == 1
    assert revision.error
    assert restored.plan_version == 1
    assert restored.restored_from_version == 1
    assert restored.plan_history == state.active_plan.plan_history


@pytest.mark.asyncio
async def test_default_capabilities_generate_scene_assets_with_transient_credential_only() -> None:
    capabilities = _capabilities()
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )
    credential = TransientTurnCredential("Bearer turn-secret")

    assert scene_state.status is WorkflowStatus.RUNNING
    result = await capabilities.generate_scene_assets(scene_state, credential=credential)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["global_assets"]["characters"][0]["three_view_images"]
    assert "turn-secret" not in serialized
    assert "Bearer" not in serialized
    assert "turn-secret" not in repr(credential)
    assert not hasattr(credential, "to_dict")
    assert not hasattr(credential, "model_dump")
    with pytest.raises(TypeError, match="临时凭据"):
        asdict(credential)
    with pytest.raises(TypeError, match="临时凭据"):
        pickle.dumps(credential)
    with pytest.raises(TypeError, match="临时凭据"):
        copy.deepcopy(credential)
    with pytest.raises(TypeError) as json_error:
        json.dumps(credential)
    assert "turn-secret" not in str(json_error.value)


@pytest.mark.asyncio
async def test_scene_asset_failure_restores_previous_authorization_context() -> None:
    from app.gateway.content_app_auth_context import (
        require_current_authorization,
        reset_current_content_app_auth,
        set_current_content_app_auth,
    )

    capabilities = _capabilities(scene_asset_skill=_FailingSceneAssetSkill())
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )
    outer_token = set_current_content_app_auth("Bearer outer-session", username="outer-user")
    try:
        with pytest.raises(RuntimeError, match="场景资产生成失败") as error_info:
            await capabilities.generate_scene_assets(
                scene_state,
                credential=TransientTurnCredential("Bearer turn-secret"),
            )
        assert "turn-secret" not in str(error_info.value)
        assert "Bearer" not in str(error_info.value)
        assert require_current_authorization() == "Bearer outer-session"
    finally:
        reset_current_content_app_auth(outer_token)
    from app.gateway.content_app_auth_context import get_current_content_app_auth

    assert get_current_content_app_auth() is None


@pytest.mark.asyncio
async def test_default_capabilities_fail_closed_for_missing_or_mismatched_capabilities() -> None:
    capabilities = _capabilities()

    missing = await capabilities.validate_intake(
        {"product_info": "智能戒指"},
        intake_rounds=0,
    )
    mismatched_values = copy.deepcopy(VIDEO_FORM)
    mismatched_values["video_ratio"] = "1:1"
    mismatched_values["video_model_capabilities"]["aspect_ratios"] = ["9:16"]
    mismatched = await capabilities.validate_intake(
        mismatched_values,
        intake_rounds=0,
    )

    assert missing.is_complete is False
    assert "product_category" in missing.missing_fields
    assert mismatched.is_complete is False
    assert mismatched.missing_fields


def test_routers_and_live_port_share_application_functions_without_reverse_imports() -> None:
    from app.gateway.routers import pixelflow_intake, pixelflow_planning, pixelflow_video
    from pixelflow.agent_workflows.video import live_capabilities

    assert pixelflow_intake.validate_form is live_capabilities.validate_video_application_form
    assert pixelflow_intake.draft_creative_directions_with_llm is live_capabilities.generate_application_directions
    assert pixelflow_planning.build_plan_markdown_with_llm is live_capabilities.generate_application_plan
    assert pixelflow_planning.revise_plan_markdown_with_llm is live_capabilities.revise_application_plan
    assert pixelflow_planning.restore_plan_version is live_capabilities.restore_application_plan
    assert pixelflow_video.run_generate_scene_assets is live_capabilities.generate_application_scene_assets
    assert not hasattr(live_capabilities, "Request")
    assert not hasattr(live_capabilities, "GenerateSceneAssetsRequest")


def test_clean_process_imports_live_capability_without_runtime_preload() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pixelflow.agent_workflows.video.live_capabilities "
                "import DefaultVideoLiveCapabilities; "
                "print(DefaultVideoLiveCapabilities.__name__)"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "DefaultVideoLiveCapabilities"


def test_clean_process_imports_scene_package_module_without_partial_cycle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pixelflow.agent_workflows.video.scene_packages "
                "import VideoScenePackageAuthoritySnapshot; "
                "print(VideoScenePackageAuthoritySnapshot.__name__)"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "VideoScenePackageAuthoritySnapshot"


def test_video_package_keeps_all_public_exports_in_clean_process() -> None:
    module_by_name = {
        "VideoDeliveryWorkflowService": "delivery",
        "VideoDeliveryWorkflowState": "delivery",
        "VideoPlanAuthoritySnapshot": "planning",
        "VideoPlanningStage": "planning",
        "VideoPlanningWorkflowService": "planning",
        "VideoPlanningWorkflowState": "planning",
        "VideoScenePackageAuthoritySnapshot": "scene_packages",
        "VideoScenePackageStage": "scene_packages",
        "VideoScenePackageWorkflowService": "scene_packages",
        "VideoScenePackageWorkflowState": "scene_packages",
        "VideoSceneGenerationStage": "video_generation",
        "VideoSceneGenerationWorkflowService": "video_generation",
        "VideoSceneGenerationWorkflowState": "video_generation",
        "VideoSceneAtomicOperationPort": "video_generation",
        "VideoSceneOperationTerminalClaim": "video_generation",
        "VideoSceneVideoStage": "video_generation",
        "VideoSceneVideoWorkflowService": "video_generation",
        "VideoSceneVideoWorkflowState": "video_generation",
        "VideoWorkflowState": "state_codec",
        "VideoWorkflowStateEnvelope": "state_codec",
        "VideoWorkflowStateKind": "state_codec",
        "VideoMergeSkillPort": "postproduction",
        "VideoOperationStartClaim": "postproduction",
        "VideoOperationTerminalClaim": "postproduction",
        "VideoPostProductionAtomicOperationPort": "postproduction",
        "VideoPostProductionStage": "postproduction",
        "VideoPostProductionWorkflowService": "postproduction",
        "VideoPostProductionWorkflowState": "postproduction",
        "VideoQualityReviewSkillPort": "postproduction",
        "VideoQualityReviewWorkflowResult": "postproduction",
        "canonical_payload_sha256": "state_codec",
        "canonical_video_workflow_envelope_sha256": "state_codec",
        "decode_video_workflow_state": "state_codec",
        "encode_video_workflow_state": "state_codec",
        "project_video_workflow_state": "state_codec",
    }
    import_names = ", ".join(module_by_name)
    script = (
        "import importlib\n"
        f"from pixelflow.agent_workflows.video import ({import_names})\n"
        "from pixelflow.agent_workflows import video\n"
        f"expected = {module_by_name!r}\n"
        "assert video.__all__ == list(expected)\n"
        "for name, module_name in expected.items():\n"
        "    public = globals()[name]\n"
        "    defining = importlib.import_module(f'pixelflow.agent_workflows.video.{module_name}')\n"
        "    assert public is getattr(defining, name), name\n"
        "print(len(expected))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(len(module_by_name))
