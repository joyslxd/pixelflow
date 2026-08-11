from __future__ import annotations

import asyncio
import copy
import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.agent_runtime.contracts import ExternalJobStatus, OperationRequest, WorkflowStatus
from pixelflow.agent_runtime.fakes import FakeOperationPort
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.agent_workflows.video import (
    VideoOperationStartClaim,
    VideoOperationTerminalClaim,
    VideoPlanningWorkflowService,
    VideoSceneGenerationStage,
    VideoSceneGenerationWorkflowService,
    VideoSceneOperationTerminalClaim,
    VideoScenePackageStage,
    VideoScenePackageWorkflowService,
)
from pixelflow.agent_workflows.video import video_generation as video_generation_module
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.intake.forms import draft_creative_directions, validate_form


pytestmark = pytest.mark.v1_workflow_legacy

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
        "durations_sec": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {"aspect_ratios": ["1:1"], "sizes": ["1080p"]},
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


class _AtomicFakeOperationPort(FakeOperationPort):
    """为 M11.3 测试提供原子终态 claim，并绑定结果摘要。"""

    def __init__(self) -> None:
        super().__init__()
        self._terminal_lock = asyncio.Lock()
        self._terminal_result_hashes: dict[str, str] = {}
        self._video_terminal_claims: dict[str, VideoOperationTerminalClaim] = {}

    async def finalize_scene_operation(
        self,
        *,
        expected,
        target_status,
        provider_job_id,
        result_hash,
    ) -> VideoSceneOperationTerminalClaim:
        async with self._terminal_lock:
            current = await self.get(expected.job_id)
            if current is None:
                raise KeyError(expected.job_id)
            expected_identity = (
                expected.job_id,
                expected.workflow_id,
                expected.stage,
                expected.attempt,
                expected.idempotency_key,
            )
            current_identity = (
                current.job_id,
                current.workflow_id,
                current.stage,
                current.attempt,
                current.idempotency_key,
            )
            if current_identity != expected_identity:
                raise OperationConflictError("Operation 身份漂移")
            existing_hash = self._terminal_result_hashes.get(current.job_id)
            if existing_hash is not None:
                if (
                    existing_hash != result_hash
                    or current.status is not target_status
                    or provider_job_id is not None
                    and provider_job_id != current.provider_job_id
                ):
                    raise OperationConflictError("Operation 终态结果冲突")
                return VideoSceneOperationTerminalClaim(job=current, result_hash=existing_hash)
            if current.status in {
                ExternalJobStatus.SUCCEEDED,
                ExternalJobStatus.FAILED,
                ExternalJobStatus.TIMEOUT,
                ExternalJobStatus.EXPIRED,
            }:
                raise OperationConflictError("Operation 已终结但缺少绑定结果摘要")
            resolved_provider_job_id = provider_job_id or current.provider_job_id
            if target_status is ExternalJobStatus.SUCCEEDED and not resolved_provider_job_id:
                raise OperationConflictError("成功 Operation 必须绑定供应商任务 ID")
            saved = await self.save(
                current.model_copy(
                    update={
                        "provider_job_id": resolved_provider_job_id,
                        "status": target_status,
                    }
                )
            )
            self._terminal_result_hashes[current.job_id] = result_hash
            return VideoSceneOperationTerminalClaim(job=saved, result_hash=result_hash)

    async def get_scene_operation_terminal_claim(self, *, job_id):
        """读取可信假 Repository 中的分镜 Operation 终态。"""

        job = await self.get(job_id)
        result_hash = self._terminal_result_hashes.get(job_id)
        if job is None or result_hash is None:
            return None
        return VideoSceneOperationTerminalClaim(job=job, result_hash=result_hash)

    async def finalize_video_operation(self, *, result_type, payload, stage_version, **kwargs):
        """为 M11.4 原子保存可查询的完整业务终态。"""

        claim = await self.finalize_scene_operation(**kwargs)
        terminal = VideoOperationTerminalClaim(
            job=claim.job,
            result_hash=claim.result_hash,
            result_type=result_type,
            payload=copy.deepcopy(dict(payload)),
            stage_version=stage_version,
        )
        existing = self._video_terminal_claims.get(claim.job.job_id)
        if existing is not None and existing != terminal:
            raise OperationConflictError("视频 Operation 业务终态冲突")
        self._video_terminal_claims[claim.job.job_id] = terminal
        return copy.deepcopy(terminal)

    async def get_video_operation_terminal_claim(self, *, job_id):
        """读取可信假 Repository 中的 M11.4 业务终态。"""

        claim = self._video_terminal_claims.get(job_id)
        return copy.deepcopy(claim) if claim is not None else None

    async def claim_video_operation_start(self, *, expected, owner, now, lease_seconds):
        """以可过期租约领取 M11.4 外调前启动权。"""

        async with self._terminal_lock:
            current = await self.get(expected.job_id)
            if current is None:
                raise KeyError(expected.job_id)
            expected_identity = (
                expected.job_id,
                expected.workflow_id,
                expected.stage,
                expected.attempt,
                expected.idempotency_key,
            )
            current_identity = (
                current.job_id,
                current.workflow_id,
                current.stage,
                current.attempt,
                current.idempotency_key,
            )
            if current_identity != expected_identity:
                raise OperationConflictError("Operation 启动身份漂移")
            lease_available = current.lease_expires_at is None or current.lease_expires_at <= now
            if current.status is ExternalJobStatus.CREATED and current.provider_job_id is None and lease_available:
                saved = await self.save(
                    current.model_copy(
                        update={
                            "lease_owner": owner,
                            "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        }
                    )
                )
                return VideoOperationStartClaim(job=saved, acquired=True)
            return VideoOperationStartClaim(job=current, acquired=False)

    async def mark_video_operation_call_started(self, *, expected, owner, now):
        """原子标记已进入不可自动接管的供应商外调窗口。"""

        async with self._terminal_lock:
            current = await self.get(expected.job_id)
            if current != expected or current.status is not ExternalJobStatus.CREATED:
                raise OperationConflictError("视频 Operation 外调开始状态漂移")
            if current.lease_owner != owner or current.lease_expires_at is None:
                raise OperationConflictError("视频 Operation 外调开始租约不匹配")
            if current.lease_expires_at <= now:
                raise OperationConflictError("视频 Operation 外调开始租约已过期")
            return await self.save(
                current.model_copy(
                    update={
                        "status": ExternalJobStatus.POLLING,
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            )


class _BarrierAtomicFakeOperationPort(_AtomicFakeOperationPort):
    """让两个终态回调同时抵达原子边界，用于验证竞争写入。"""

    def __init__(self) -> None:
        super().__init__()
        self._arrivals = 0
        self._barrier = asyncio.Event()

    async def finalize_scene_operation(self, **kwargs):
        self._arrivals += 1
        if self._arrivals >= 2:
            self._barrier.set()
        await self._barrier.wait()
        return await super().finalize_scene_operation(**kwargs)


class _CrashAfterNAtomicFakeOperationPort(_AtomicFakeOperationPort):
    """在指定终态 claim 前模拟进程崩溃，验证同一输入可安全重放。"""

    def __init__(self, crash_before_call: int) -> None:
        super().__init__()
        self._crash_before_call = crash_before_call
        self._finalize_calls = 0
        self._crashed = False

    async def finalize_scene_operation(self, **kwargs):
        self._finalize_calls += 1
        if self._finalize_calls == self._crash_before_call and not self._crashed:
            self._crashed = True
            raise RuntimeError("模拟额度冻结中途崩溃")
        return await super().finalize_scene_operation(**kwargs)


def _reviewed_scene_package_state(video_form=None):
    planning = VideoPlanningWorkflowService()
    form = copy.deepcopy(video_form or VIDEO_FORM)
    now = datetime(2026, 7, 28, 4, 0, tzinfo=UTC)
    state = planning.start(
        workflow_id="wf-video-generation",
        conversation_id="conv-video-generation",
        intent="video",
        intake_context={"source_prompt": "生成一条智能戒指新品广告"},
        now=now,
    )
    state = planning.confirm_intake(state, validate_form("video", form), now=now + timedelta(seconds=1))
    state = planning.publish_directions(state, draft_creative_directions("video", form), now=now + timedelta(seconds=2))
    state = planning.select_direction(state, "direction_1", now=now + timedelta(seconds=3))
    plan = _with_concrete_asset_names(build_plan_markdown("video", form, state.selected_direction))
    state = planning.publish_initial_plan(state, plan, now=now + timedelta(seconds=4))
    state = planning.approve_plan(state, now=now + timedelta(seconds=5))
    package = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        state,
        materials=[{"type": "image", "url": "https://materials.example.com/ring.png"}],
        now=now + timedelta(seconds=6),
    )
    assets = copy.deepcopy(package.scene_package.global_assets)
    for item in assets["characters"]:
        item["three_view_images"] = [f"https://assets.example.com/{item['asset_id']}.png"]
    for collection in ("scenes", "props"):
        for item in assets[collection]:
            item["images"] = [f"https://assets.example.com/{item['asset_id']}.png"]
    return VideoScenePackageWorkflowService().publish_generated_asset_images(
        package,
        assets,
        now=now + timedelta(seconds=7),
    )


def _with_concrete_asset_names(result):
    blueprints = copy.deepcopy(result.scene_blueprints)
    manifest = copy.deepcopy(result.asset_manifest)
    replacements = {"目标用户": "都市健康管理师林岚", "真实使用场景": "晨间公寓健康监测区"}
    for blueprint in blueprints:
        requirements = blueprint["asset_requirements"]
        for collection in ("characters", "scenes", "props"):
            requirements[collection] = [replacements.get(name, name) for name in requirements[collection]]
        for source, target in replacements.items():
            for field_name in ("shot_description", "storyline", "narration"):
                blueprint[field_name] = blueprint[field_name].replace(source, target)
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
    return replace(result, scene_blueprints=blueprints, asset_manifest=manifest, plan_history=history)


def test_generation_requests_accept_prompt_over_2500_characters() -> None:
    long_prompt = "完整分镜提示词。" * 400
    scenes = [
        {
            "scene_id": "scene-1",
            "scene_index": 1,
            "duration_ms": 5000,
            "prompt": long_prompt,
            "storyline": "展示智能戒指的健康监测价值。",
            "shot_description": {"text": "0-5秒：产品在晨光中旋转展示。"},
            "narration": "健康数据，抬手可见。",
            "transition": "淡出。",
            "image_urls": [],
            "video_urls": [],
            "audio_urls": [],
        }
    ]

    requests = video_generation_module._generation_requests(
        scenes,
        ["scene-1"],
        VIDEO_FORM,
    )

    assert requests[0]["prompt"] == long_prompt
    assert len(requests[0]["prompt"]) > 2500


@pytest.mark.asyncio
async def test_start_claims_each_scene_once_and_keeps_contract_parameters():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)

    state = await service.start_from_reviewed_scene_package(
        package_state,
        now=package_state.updated_at + timedelta(seconds=1),
    )
    duplicate = await service.start_from_reviewed_scene_package(
        package_state,
        now=package_state.updated_at + timedelta(seconds=1),
    )

    assert state.current_stage is VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS
    assert state.status is WorkflowStatus.RUNNING
    assert duplicate.pending_operations == state.pending_operations
    assert len(state.pending_operations) == len(package_state.scene_package.scene_packages)
    assert {item["scene_id"] for item in state.generation_requests} == {
        item["scene_id"] for item in package_state.scene_package.scene_packages
    }
    for request in state.generation_requests:
        assert request["model"] == VIDEO_FORM["video_model"]
        assert request["ratio"] == VIDEO_FORM["video_ratio"]
        assert request["size"] == VIDEO_FORM["video_size"]
        assert request["sound"] == VIDEO_FORM["video_sound"]
        assert 4 <= request["duration"] <= 15
        assert len(request["image_urls"]) <= 9


@pytest.mark.asyncio
async def test_generation_cancel_preserves_pending_operations_without_port_calls():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    jobs_before = copy.deepcopy(operation_port._jobs_by_id)
    requests_before = copy.deepcopy(operation_port._requests_by_idempotency_key)

    cancelled = service.cancel(
        state,
        now=state.updated_at + timedelta(seconds=1),
    )

    assert cancelled.current_stage is state.current_stage
    assert cancelled.status is WorkflowStatus.CANCELLED
    assert cancelled.stage_version == state.stage_version + 1
    assert cancelled.context_version == state.context_version + 1
    assert cancelled.updated_at > state.updated_at
    assert cancelled.scene_packages == state.scene_packages
    assert cancelled.scene_videos == state.scene_videos
    assert cancelled.failed_scenes == state.failed_scenes
    assert cancelled.generation_requests == state.generation_requests
    assert cancelled.pending_operations == state.pending_operations
    assert operation_port._jobs_by_id == jobs_before
    assert operation_port._requests_by_idempotency_key == requests_before
    assert service.to_workflow_record(cancelled).pending_external_job == state.pending_operations[0]
    assert service.to_workflow_record(cancelled).status is WorkflowStatus.CANCELLED

    with pytest.raises(ValueError, match="终态"):
        service.cancel(cancelled, now=cancelled.updated_at + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_resume_queries_original_operations_without_new_claims():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    original_jobs = state.pending_operations

    for job in original_jobs:
        await operation_port.save(job.model_copy(update={"status": ExternalJobStatus.POLLING}))
    resumed = await service.resume(state)

    assert [item.job_id for item in resumed.pending_operations] == [item.job_id for item in original_jobs]
    assert all(item.status is ExternalJobStatus.POLLING for item in resumed.pending_operations)


@pytest.mark.asyncio
async def test_partial_failure_preserves_success_and_retry_claims_only_failed_scene():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    scenes = package_state.scene_package.scene_packages
    first, second = scenes[0], scenes[1]

    state = await service.record_scene_success(
        state,
        scene_id=first["scene_id"],
        video_url="https://videos.example.com/scene-1.mp4",
        provider_job_id="provider-scene-1",
    )
    state = await service.record_scene_failure(
        state,
        scene_id=second["scene_id"],
        error="供应商暂时不可用",
        attempts=3,
        raw={"status_code": 503, "details": "temporary"},
    )
    for scene in scenes[2:]:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )

    assert state.current_stage is VideoSceneGenerationStage.SCENE_VIDEO_REVIEW
    assert state.status is WorkflowStatus.AWAITING_USER
    assert [item["scene_id"] for item in state.failed_scenes] == [second["scene_id"]]
    assert state.failed_scenes[0]["attempts"] == 3
    assert {item["scene_id"] for item in state.scene_videos} == {
        item["scene_id"] for item in scenes if item["scene_id"] != second["scene_id"]
    }

    retried = await service.retry_failed_scenes(state)
    assert [item["scene_id"] for item in retried.generation_requests] == [second["scene_id"]]
    assert [item.attempt for item in retried.pending_operations] == [2]
    assert retried.scene_videos == state.scene_videos


@pytest.mark.asyncio
async def test_quota_failure_pauses_once_but_keeps_each_failed_scene():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)

    state = await service.record_scene_failure(
        state,
        scene_id=package_state.scene_package.scene_packages[0]["scene_id"],
        error="额度不足，请充值后继续",
        attempts=1,
        quota_insufficient=True,
        retryable=False,
    )

    assert state.status is WorkflowStatus.PAUSED_QUOTA
    assert state.quota_insufficient is True
    assert len(state.failed_scenes) == len(package_state.scene_package.scene_packages)
    assert all(item["quota_insufficient"] is True for item in state.failed_scenes)


@pytest.mark.asyncio
async def test_modify_one_scene_invalidates_only_that_video_and_reuses_siblings():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)
    scenes = package_state.scene_package.scene_packages
    for scene in scenes:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )
    original_package_checksum = state.source_scene_package.checksum
    target = scenes[1]
    sibling_urls = {
        item["scene_id"]: item["video_url"]
        for item in state.scene_videos
        if item["scene_id"] != target["scene_id"]
    }

    modified = service.modify_scene(
        state,
        scene_id=target["scene_id"],
        patch={
            "storyline": "戒指数据界面在晨光中展开，强调全天健康趋势。",
            "narration": "全天趋势，一眼掌握。",
        },
    )
    restarted = await service.regenerate_modified_scenes(modified)

    assert modified.source_scene_package.checksum == original_package_checksum
    assert modified.edited_scene_ids == [target["scene_id"]]
    assert {item["scene_id"]: item["video_url"] for item in modified.scene_videos} == sibling_urls
    assert [item["scene_id"] for item in restarted.generation_requests] == [target["scene_id"]]
    assert {item["scene_id"]: item["video_url"] for item in restarted.scene_videos} == sibling_urls


@pytest.mark.asyncio
async def test_boundary_rejects_unreviewed_package_and_cross_scene_or_identity_edits():
    reviewed = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    forged = replace(
        reviewed,
        current_stage=VideoScenePackageStage.GENERATE_SCENE_ASSETS,
        status=WorkflowStatus.RUNNING,
    )
    with pytest.raises(ValueError, match="人工确认"):
        await service.start_from_reviewed_scene_package(forged)

    state = await service.start_from_reviewed_scene_package(reviewed)
    for scene in reviewed.scene_package.scene_packages:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )
    scene_id = reviewed.scene_package.scene_packages[0]["scene_id"]
    with pytest.raises(ValueError, match="允许修改"):
        service.modify_scene(state, scene_id=scene_id, patch={"duration_ms": 99_000})
    with pytest.raises(ValueError, match="不存在"):
        service.modify_scene(state, scene_id="scene-missing", patch={"storyline": "篡改"})


@pytest.mark.asyncio
async def test_failure_policy_rejects_early_retryable_and_retried_business_failures():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)
    scene_id = package_state.scene_package.scene_packages[0]["scene_id"]

    with pytest.raises(ValueError, match="最多 3 次"):
        await service.record_scene_failure(
            state,
            scene_id=scene_id,
            error="临时网络异常",
            attempts=1,
        )
    with pytest.raises(ValueError, match="不可重试"):
        await service.record_scene_failure(
            state,
            scene_id=scene_id,
            error="参数验证失败",
            attempts=3,
            raw={"status_code": 400, "details": "invalid ratio"},
        )


@pytest.mark.asyncio
async def test_result_raw_redacts_credentials_and_forged_edited_prompt_fails_closed():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)
    scenes = package_state.scene_package.scene_packages
    for scene in scenes:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
            raw={"status_code": 200, "Authorization": "Bearer secret-value"},
        )
    assert all(item["raw"]["Authorization"] == "[REDACTED]" for item in state.scene_videos)

    target = scenes[0]
    modified = service.modify_scene(
        state,
        scene_id=target["scene_id"],
        patch={"storyline": "新的镜头故事线"},
    )
    forged_scenes = modified.scene_packages
    forged_scenes[0]["prompt"] = "绕过机械重建的供应商提示词"
    forged = replace(
        modified,
        _scene_packages_json=json.dumps(
            forged_scenes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    with pytest.raises(ValueError, match="机械重建"):
        service.to_workflow_record(forged)


@pytest.mark.asyncio
async def test_modified_scene_keeps_edit_lineage_after_regeneration_success():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)
    for scene in package_state.scene_package.scene_packages:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )

    target = package_state.scene_package.scene_packages[0]
    state = service.modify_scene(
        state,
        scene_id=target["scene_id"],
        patch={"storyline": "修改后的单镜故事线"},
    )
    assert state.dirty_scene_ids == [target["scene_id"]]
    state = await service.regenerate_modified_scenes(state)
    state = await service.record_scene_success(
        state,
        scene_id=target["scene_id"],
        video_url="https://videos.example.com/scene-1-v2.mp4",
        provider_job_id="provider-scene-1-v2",
    )

    assert state.current_stage is VideoSceneGenerationStage.SCENE_VIDEO_REVIEW
    assert state.edited_scene_ids == [target["scene_id"]]
    assert state.dirty_scene_ids == []
    assert next(item for item in state.scene_videos if item["scene_id"] == target["scene_id"])["video_url"].endswith("-v2.mp4")


@pytest.mark.asyncio
async def test_http_402_immediately_pauses_batch_and_marks_unstarted_scenes():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    original_jobs = state.pending_operations

    state = await service.record_scene_failure(
        state,
        scene_id=package_state.scene_package.scene_packages[0]["scene_id"],
        error="content-app 返回 Payment Required",
        attempts=1,
        raw={"status_code": 402, "message": "余额不足，请充值"},
    )

    assert state.status is WorkflowStatus.PAUSED_QUOTA
    assert state.quota_insufficient is True
    assert state.pending_operations == []
    assert len(state.failed_scenes) == len(package_state.scene_package.scene_packages)
    assert sum(item["not_started_due_to_quota"] is True for item in state.failed_scenes) == len(original_jobs) - 1
    saved_jobs = [await operation_port.get(item.job_id) for item in original_jobs]
    assert all(item is not None and item.status is ExternalJobStatus.FAILED for item in saved_jobs)

    resumed = await service.retry_failed_scenes(state)
    assert {item["scene_id"] for item in resumed.generation_requests} == {
        item["scene_id"] for item in package_state.scene_package.scene_packages
    }


@pytest.mark.asyncio
async def test_non_retryable_failure_cannot_claim_a_new_operation():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    first, *remaining = package_state.scene_package.scene_packages
    state = await service.record_scene_failure(
        state,
        scene_id=first["scene_id"],
        error="参数验证失败",
        attempts=1,
        retryable=False,
        raw={"status_code": 400},
    )
    for scene in remaining:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )

    claimed_before = len(operation_port._jobs_by_id)
    with pytest.raises(ValueError, match="不可直接重试"):
        await service.retry_failed_scenes(state, scene_ids=[first["scene_id"]])
    assert len(operation_port._jobs_by_id) == claimed_before


@pytest.mark.asyncio
async def test_terminal_write_uses_fresh_operation_and_rejects_conflicting_terminal():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]
    pending = state.pending_operations[0]
    await operation_port.save(
        pending.model_copy(
            update={
                "provider_job_id": "provider-original",
                "status": ExternalJobStatus.POLLING,
            }
        )
    )

    completed = await service.record_scene_success(
        state,
        scene_id=target["scene_id"],
        video_url="https://videos.example.com/scene-1.mp4",
    )
    saved = await operation_port.get(pending.job_id)
    assert saved.provider_job_id == "provider-original"
    assert next(item for item in completed.scene_videos if item["scene_id"] == target["scene_id"])["task_id"] == "provider-original"

    with pytest.raises(OperationConflictError, match="终态"):
        await service.record_scene_failure(
            state,
            scene_id=target["scene_id"],
            error="迟到的失败回调",
            attempts=3,
        )


@pytest.mark.asyncio
async def test_first_generation_has_no_untrusted_scene_reuse_entrypoint():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    parameters = inspect.signature(service.start_from_reviewed_scene_package).parameters
    assert "scene_ids" not in parameters
    assert "reused_scene_videos" not in parameters

    state = await service.start_from_reviewed_scene_package(package_state)
    assert len(state.pending_operations) == len(package_state.scene_package.scene_packages)
    assert state.scene_videos == []


@pytest.mark.asyncio
async def test_failure_summary_redacts_secrets_inside_values_and_url_queries():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]

    state = await service.record_scene_failure(
        state,
        scene_id=target["scene_id"],
        error="Authorization: Bearer demo-secret 请求失败",
        attempts=3,
        raw={
            "message": "API key=demo-api-key",
            "download_url": "https://provider.example.com/result.zip?token=demo-query-token&user=42",
            "nested": {"detail": "credential: demo-credential"},
        },
    )

    persisted = json.dumps(state.failed_scenes, ensure_ascii=False)
    assert "demo-secret" not in persisted
    assert "demo-api-key" not in persisted
    assert "demo-query-token" not in persisted
    assert "demo-credential" not in persisted
    assert "?token=" not in persisted


@pytest.mark.asyncio
async def test_operation_business_identity_is_stable_when_request_hash_drifts():
    first_key = video_generation_module._operation_idempotency_key(
        workflow_id="wf-video-generation",
        stage_version=8,
        scene_id="scene-1",
        attempt=1,
        request_hash="hash-a",
    )
    second_key = video_generation_module._operation_idempotency_key(
        workflow_id="wf-video-generation",
        stage_version=8,
        scene_id="scene-1",
        attempt=1,
        request_hash="hash-b",
    )
    assert first_key == second_key

    operation_port = _AtomicFakeOperationPort()
    common = {
        "workflow_id": "wf-video-generation",
        "stage": "generate_scene_video:scene-1",
        "stage_version": 8,
        "attempt": 1,
        "idempotency_key": first_key,
    }
    await operation_port.claim(OperationRequest(request_hash="hash-a", **common))
    with pytest.raises(OperationConflictError):
        await operation_port.claim(OperationRequest(request_hash="hash-b", **common))


@pytest.mark.asyncio
async def test_quota_pause_keeps_started_sibling_operation_and_never_reclaims_it():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    first, second, *_ = package_state.scene_package.scene_packages
    second_job = next(item for item in state.pending_operations if item.stage.endswith(f":{second['scene_id']}"))
    await operation_port.save(
        second_job.model_copy(
            update={
                "provider_job_id": "already-started-provider-job",
                "status": ExternalJobStatus.POLLING,
            }
        )
    )

    paused = await service.record_scene_failure(
        state,
        scene_id=first["scene_id"],
        error="额度不足",
        attempts=1,
        raw={"status_code": 402},
    )

    assert paused.current_stage is VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS
    assert paused.status is WorkflowStatus.PAUSED_QUOTA
    assert [item.job_id for item in paused.pending_operations] == [second_job.job_id]
    still_running = await operation_port.get(second_job.job_id)
    assert still_running.status is ExternalJobStatus.POLLING
    assert still_running.provider_job_id == "already-started-provider-job"
    with pytest.raises(ValueError, match="复核阶段"):
        await service.retry_failed_scenes(paused)

    paused = await service.record_scene_success(
        paused,
        scene_id=second["scene_id"],
        video_url="https://videos.example.com/scene-2.mp4",
    )
    assert paused.current_stage is VideoSceneGenerationStage.SCENE_VIDEO_REVIEW
    retried = await service.retry_failed_scenes(paused)
    assert second["scene_id"] not in {item["scene_id"] for item in retried.generation_requests}


@pytest.mark.asyncio
async def test_atomic_terminal_claim_rejects_concurrent_success_and_failure():
    package_state = _reviewed_scene_package_state()
    operation_port = _BarrierAtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]

    results = await asyncio.gather(
        service.record_scene_success(
            state,
            scene_id=target["scene_id"],
            video_url="https://videos.example.com/concurrent.mp4",
            provider_job_id="provider-concurrent",
        ),
        service.record_scene_failure(
            state,
            scene_id=target["scene_id"],
            error="并发失败回调",
            attempts=3,
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(item, OperationConflictError) for item in results) == 1
    assert sum(not isinstance(item, Exception) for item in results) == 1


@pytest.mark.asyncio
async def test_atomic_terminal_claim_binds_success_result_hash():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]

    await service.record_scene_success(
        state,
        scene_id=target["scene_id"],
        video_url="https://videos.example.com/a.mp4",
        provider_job_id="provider-scene-1",
    )
    with pytest.raises(OperationConflictError, match="结果冲突"):
        await service.record_scene_success(
            state,
            scene_id=target["scene_id"],
            video_url="https://videos.example.com/b.mp4",
            provider_job_id="provider-scene-1",
        )


@pytest.mark.asyncio
async def test_success_result_uses_frozen_mode_endpoint_and_requires_provider_job_id():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]
    request = next(item for item in state.generation_requests if item["scene_id"] == target["scene_id"])
    parameters = inspect.signature(service.record_scene_success).parameters
    assert "mode" not in parameters
    assert "endpoint" not in parameters

    with pytest.raises(OperationConflictError, match="供应商任务 ID"):
        await service.record_scene_success(
            state,
            scene_id=target["scene_id"],
            video_url="https://videos.example.com/no-provider-id.mp4",
        )

    completed = await service.record_scene_success(
        state,
        scene_id=target["scene_id"],
        video_url="https://videos.example.com/authoritative.mp4",
        provider_job_id="provider-authoritative",
    )
    result = next(item for item in completed.scene_videos if item["scene_id"] == target["scene_id"])
    assert result["mode"] == request["generation_mode"]
    assert result["endpoint"] == video_generation_module._endpoint_for_mode(request["generation_mode"])
    assert result["task_id"] == "provider-authoritative"

    forged_videos = completed.scene_videos
    forged_videos[0]["mode"] = "edit_video"
    forged_videos[0]["endpoint"] = "/api/video/edit-video"
    forged = replace(
        completed,
        _scene_videos_json=json.dumps(
            forged_videos,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    with pytest.raises(ValueError, match="权威生成请求"):
        service.to_workflow_record(forged)


@pytest.mark.asyncio
async def test_terminal_callback_fails_closed_without_atomic_video_port():
    package_state = _reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(FakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]

    with pytest.raises(ValueError, match="原子终态"):
        await service.record_scene_success(
            state,
            scene_id=target["scene_id"],
            video_url="https://videos.example.com/fail-closed.mp4",
            provider_job_id="provider-fail-closed",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    [
        ("model", "forged-non-contract-model"),
        ("ratio", "1:1"),
        ("duration", 15),
        ("prompt", "绕过权威场景包的供应商提示词"),
        ("image_urls", ["https://attacker.example.com/forged.png"]),
    ],
)
async def test_resume_rebuilds_pending_request_and_idempotency_identity(field_name, forged_value):
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    requests = state.generation_requests
    requests[0][field_name] = forged_value
    forged_request = replace(
        state,
        _generation_requests_json=json.dumps(
            requests,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    with pytest.raises(ValueError, match="权威生成请求"):
        await service.resume(forged_request)

    pending = state.pending_operations
    pending[0] = pending[0].model_copy(update={"idempotency_key": "pf:video-scene:forged-key"})
    forged_key = replace(state, _pending_operations=tuple(pending))
    with pytest.raises(ValueError, match="幂等键"):
        service.to_workflow_record(forged_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    [
        ("task_id", "forged-provider-task"),
        ("video_url", "https://attacker.example.com/forged.mp4"),
        ("raw", {"message": "forged-provider-result"}),
    ],
)
async def test_completed_video_must_match_persisted_terminal_claim(field_name, forged_value):
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    target = package_state.scene_package.scene_packages[0]
    completed = await service.record_scene_success(
        state,
        scene_id=target["scene_id"],
        video_url="https://videos.example.com/authoritative.mp4",
        provider_job_id="provider-authoritative",
        raw={"status_code": 200},
    )
    videos = completed.scene_videos
    videos[0][field_name] = forged_value
    forged = replace(
        completed,
        _scene_videos_json=json.dumps(
            videos,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )

    with pytest.raises(ValueError, match="终态 claim"):
        service.to_workflow_record(forged)


@pytest.mark.asyncio
async def test_non_retryable_failure_cannot_be_forged_into_billable_retry():
    package_state = _reviewed_scene_package_state()
    operation_port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    first, *remaining = package_state.scene_package.scene_packages
    state = await service.record_scene_failure(
        state,
        scene_id=first["scene_id"],
        error="参数验证失败",
        attempts=1,
        retryable=False,
        raw={"status_code": 400},
    )
    for scene in remaining:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )
    failures = state.failed_scenes
    failures[0]["retryable"] = True
    forged = replace(
        state,
        _failed_scenes_json=json.dumps(
            failures,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )

    with pytest.raises(ValueError, match="终态 claim"):
        await service.retry_failed_scenes(forged)


@pytest.mark.asyncio
async def test_unknown_nonempty_generation_capability_fails_closed():
    form = copy.deepcopy(VIDEO_FORM)
    form["video_model_capabilities"]["generation_types"] = ["future_vendor_mode"]
    package_state = _reviewed_scene_package_state(form)

    with pytest.raises(ValueError, match="能力不支持"):
        await VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort()).start_from_reviewed_scene_package(package_state)


@pytest.mark.asyncio
async def test_scene_duration_must_match_live_model_capability():
    form = copy.deepcopy(VIDEO_FORM)
    form["video_model_capabilities"]["durations_sec"] = [4]
    package_state = _reviewed_scene_package_state(form)

    with pytest.raises(ValueError, match="实时支持时长"):
        await VideoSceneGenerationWorkflowService(_AtomicFakeOperationPort()).start_from_reviewed_scene_package(package_state)


@pytest.mark.asyncio
async def test_quota_freeze_is_replayable_after_mid_batch_crash():
    package_state = _reviewed_scene_package_state()
    operation_port = _CrashAfterNAtomicFakeOperationPort(crash_before_call=3)
    service = VideoSceneGenerationWorkflowService(operation_port)
    state = await service.start_from_reviewed_scene_package(package_state)
    first = package_state.scene_package.scene_packages[0]

    with pytest.raises(RuntimeError, match="中途崩溃"):
        await service.record_scene_failure(
            state,
            scene_id=first["scene_id"],
            error="额度不足",
            attempts=1,
            raw={"status_code": 402},
        )

    replayed = await service.record_scene_failure(
        state,
        scene_id=first["scene_id"],
        error="额度不足",
        attempts=1,
        raw={"status_code": 402},
    )
    assert replayed.status is WorkflowStatus.PAUSED_QUOTA
    assert replayed.pending_operations == []
    assert len(replayed.failed_scenes) == len(package_state.scene_package.scene_packages)
