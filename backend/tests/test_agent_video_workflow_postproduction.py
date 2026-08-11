from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import timedelta

import pytest

import test_agent_video_workflow_generation as generation_tests
from test_agent_video_workflow_generation import _AtomicFakeOperationPort, _reviewed_scene_package_state

from pixelflow.agent_runtime.contracts import ExternalJobStatus, WorkflowStatus
from pixelflow.agent_runtime.fakes import FakeOperationPort
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.agent_workflows.video import (
    VideoPostProductionStage,
    VideoPostProductionWorkflowService,
    VideoQualityReviewWorkflowResult,
    VideoSceneGenerationStage,
    VideoSceneGenerationWorkflowService,
)
from pixelflow.agent_workflows.video import postproduction as postproduction_module
from pixelflow.skills.base import GenerationResult, VideoQualityReviewResult



pytestmark = pytest.mark.v1_workflow_legacy

def _generated_state():
    package = _reviewed_scene_package_state()
    port = _AtomicFakeOperationPort()
    service = VideoSceneGenerationWorkflowService(port)
    return package, port, service


async def _complete_generation(package=None):
    if package is None:
        package, port, service = _generated_state()
    else:
        port = _AtomicFakeOperationPort()
        service = VideoSceneGenerationWorkflowService(port)
    state = await service.start_from_reviewed_scene_package(package)
    for scene in package.scene_package.scene_packages:
        state = await service.record_scene_success(
            state,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}",
        )
    return state, port, service


async def _claim_started(state, port):
    owner = "test-video-postproduction"
    claim = await port.claim_video_operation_start(
        expected=state.pending_operation,
        owner=owner,
        now=state.updated_at,
        lease_seconds=30,
    )
    assert claim.acquired is True
    started = await port.mark_video_operation_call_started(expected=claim.job, owner=owner, now=state.updated_at)
    return replace(state, _pending_operation=started)


@pytest.mark.asyncio
async def test_merge_claims_once_orders_scenes_and_single_scene_passthrough():
    generation, port, _ = await _complete_generation()
    service = VideoPostProductionWorkflowService(port)

    state = await service.start_merge(generation, now=generation.updated_at + timedelta(seconds=1))
    duplicate = await service.start_merge(generation, now=generation.updated_at + timedelta(seconds=1))

    assert state.current_stage is VideoPostProductionStage.MERGE_VIDEO
    assert duplicate.pending_operation == state.pending_operation
    ordered = [item["scene_id"] for item in state.merge_request["scene_videos"]]
    assert ordered == [item["scene_id"] for item in sorted(generation.scene_videos, key=lambda item: item["scene_index"])]

    single_form = copy.deepcopy(generation_tests.VIDEO_FORM)
    single_form["video_duration_sec"] = 4
    single_package = generation_tests._reviewed_scene_package_state(single_form)
    single_generation, single_port, _ = await _complete_generation(single_package)
    assert len(single_generation.scene_videos) == 1
    single_service = VideoPostProductionWorkflowService(single_port)
    single_state = await single_service.start_merge(single_generation)
    assert single_state.merge_request["video_urls"] == [single_generation.scene_videos[0]["video_url"]]
    single_state = await _claim_started(single_state, single_port)
    completed = await single_service.record_merge_success(
        single_state,
        merged_video_url=single_generation.scene_videos[0]["video_url"],
        provider_job_id=None,
    )
    assert completed.merged_video["task_id"].startswith("passthrough:")


@pytest.mark.asyncio
async def test_postproduction_cancel_preserves_pending_operation_without_port_calls():
    generation, port, _ = await _complete_generation()
    service = VideoPostProductionWorkflowService(port)
    state = await service.start_merge(
        generation,
        now=generation.updated_at + timedelta(seconds=1),
    )
    jobs_before = copy.deepcopy(port._jobs_by_id)
    requests_before = copy.deepcopy(port._requests_by_idempotency_key)

    cancelled = service.cancel(
        state,
        now=state.updated_at + timedelta(seconds=1),
    )

    assert cancelled.current_stage is state.current_stage
    assert cancelled.status is WorkflowStatus.CANCELLED
    assert cancelled.stage_version == state.stage_version + 1
    assert cancelled.context_version == state.context_version + 1
    assert cancelled.updated_at > state.updated_at
    assert cancelled.generation_state == state.generation_state
    assert cancelled.merge_request == state.merge_request
    assert cancelled.pending_operation == state.pending_operation
    assert port._jobs_by_id == jobs_before
    assert port._requests_by_idempotency_key == requests_before
    assert service.to_workflow_record(cancelled).pending_external_job == state.pending_operation
    assert service.to_workflow_record(cancelled).status is WorkflowStatus.CANCELLED

    with pytest.raises(ValueError, match="终态"):
        service.cancel(cancelled, now=cancelled.updated_at + timedelta(seconds=1))

    running = await _claim_started(state, port)
    review = await service.record_merge_success(
        running,
        merged_video_url="https://videos.example.com/cancel-contract.mp4",
        provider_job_id="merge-cancel-contract",
        now=running.updated_at + timedelta(seconds=1),
    )
    completed = await service.finish(
        review,
        now=review.updated_at + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="终态"):
        service.cancel(completed, now=completed.updated_at + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_merge_success_then_quality_review_preserves_qc_result_and_manual_finish():
    generation, port, _ = await _complete_generation()
    service = VideoPostProductionWorkflowService(port)
    state = await service.start_merge(generation)
    state = await _claim_started(state, port)
    state = await service.record_merge_success(
        state,
        merged_video_url="https://videos.example.com/merged.mp4",
        provider_job_id="merge-provider-1",
        raw={"endpoint": "/api/video/merge", "Authorization": "Bearer secret"},
    )
    assert state.current_stage is VideoPostProductionStage.VIDEO_REVIEW
    assert state.merged_video["video_url"] == "https://videos.example.com/merged.mp4"
    assert state.merged_video["raw"]["Authorization"] == "[REDACTED]"
    directly_finished = await service.finish(state)
    assert directly_finished.current_stage is VideoPostProductionStage.COMPLETED
    assert directly_finished.finalized_by_user is True

    state = await service.start_quality_review(state, user_feedback="请检查商品露出并按意见定位分镜")
    state = await _claim_started(state, port)
    result = VideoQualityReviewResult(
        ok=True,
        summary_markdown="质检通过",
        quality_report_markdown="画面、节奏和商品露出均符合要求",
        raw={"passed": True, "score": 0.98},
    )
    state = await service.record_quality_success(state, result=result, provider_job_id="qc-provider-1")
    assert state.current_stage is VideoPostProductionStage.VIDEO_REVIEW
    assert state.quality_review["passed"] is True
    assert state.status is WorkflowStatus.AWAITING_USER

    finished = await service.finish(state)
    assert finished.current_stage is VideoPostProductionStage.COMPLETED
    assert finished.status is WorkflowStatus.COMPLETED
    assert finished.finalized_by_user is True


@pytest.mark.asyncio
async def test_merge_quota_pause_is_recoverable_without_automatic_second_start():
    generation, port, _ = await _complete_generation()
    service = VideoPostProductionWorkflowService(port)
    state = await service.start_merge(generation)
    state = await _claim_started(state, port)
    paused = await service.record_merge_failure(
        state,
        error="额度不足，请充值后继续",
        attempts=1,
        quota_insufficient=True,
        raw={"status_code": 402, "message": "余额不足", "token": "secret"},
    )
    assert paused.status is WorkflowStatus.PAUSED_QUOTA
    assert paused.merge_error["quota_insufficient"] is True
    assert paused.pending_operation is None
    assert len(port._jobs_by_id) == len(generation.scene_videos) + 1

    retried = await service.retry_merge(paused)
    assert retried.status is WorkflowStatus.RUNNING
    assert retried.pending_operation is not None
    assert retried.pending_operation.attempt == 2
    assert len(port._jobs_by_id) == len(generation.scene_videos) + 2


@pytest.mark.asyncio
async def test_merge_resume_never_reclaims_and_terminal_write_fails_closed():
    generation, _, _ = await _complete_generation()
    plain_port = FakeOperationPort()
    post = VideoPostProductionWorkflowService(plain_port)
    state = await post.start_merge(generation)
    claimed = len(plain_port._jobs_by_id)
    resumed = await post.resume(state)
    assert len(plain_port._jobs_by_id) == claimed
    assert resumed.pending_operation == state.pending_operation

    with pytest.raises(ValueError, match="原子终态"):
        await post.record_merge_success(
            state,
            merged_video_url="https://videos.example.com/merged.mp4",
            provider_job_id="merge-fail-closed",
        )
    with pytest.raises(ValueError, match="不存在或已过期"):
        await post.resume(state, operation_port=FakeOperationPort())

    class CountingMergeSkill:
        def __init__(self):
            self.calls = 0

        async def merge_videos(self, **kwargs):
            self.calls += 1
            return GenerationResult(ok=True, url="https://videos.example.com/should-not-start.mp4", task_id="unexpected")

    skill = CountingMergeSkill()
    with pytest.raises(ValueError, match="原子"):
        await post.merge_with_skill(generation, skill=skill)
    assert skill.calls == 0


@pytest.mark.asyncio
async def test_merge_terminal_rejects_provider_identity_drift():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    pending = state.pending_operation
    await port.save(
        pending.model_copy(
            update={"provider_job_id": "merge-original", "status": ExternalJobStatus.POLLING},
        )
    )
    with pytest.raises(OperationConflictError, match="供应商任务 ID"):
        await post.record_merge_success(
            state,
            merged_video_url="https://videos.example.com/merged.mp4",
            provider_job_id="merge-conflicting",
        )


@pytest.mark.asyncio
async def test_quality_failure_allows_user_revision_and_reuses_unaffected_scene_videos():
    generation, port, generation_service = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(state, merged_video_url="https://videos.example.com/merged.mp4", provider_job_id="merge-1")
    state = await post.start_quality_review(state, user_feedback="请修正第二镜商品露出")
    state = await _claim_started(state, port)
    state = await post.record_quality_success(
        state,
        result=VideoQualityReviewWorkflowResult(
            ok=True,
            passed=False,
            affected_scene_ids=[generation.scene_packages[1]["scene_id"]],
            revision_prompt="请修正第二镜的商品露出",
            issues=[{"scene_id": generation.scene_packages[1]["scene_id"], "message": "商品露出不足"}],
        ),
        provider_job_id="qc-1",
    )

    target = generation.scene_packages[1]["scene_id"]
    revised_generation = await post.apply_user_revision(
        state,
        scene_patches={target: {"storyline": "第二镜增加商品特写"}},
        generation_service=generation_service,
        now=state.updated_at + timedelta(seconds=1),
    )
    assert revised_generation.current_stage is VideoSceneGenerationStage.GENERATE_SCENE_VIDEOS
    assert revised_generation.dirty_scene_ids == [target]
    assert {item["scene_id"] for item in revised_generation.scene_videos} == {
        item["scene_id"] for item in generation.scene_videos if item["scene_id"] != target
    }
    assert revised_generation.source_scene_package.checksum == generation.source_scene_package.checksum
    assert revised_generation.stage_version > state.stage_version
    assert revised_generation.context_version > state.context_version


@pytest.mark.asyncio
async def test_qc_quota_and_failed_result_are_not_treated_as_passed():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(state, merged_video_url="https://videos.example.com/merged.mp4", provider_job_id="merge-1")
    state = await post.start_quality_review(state, user_feedback="请根据我的意见检查视频")
    state = await _claim_started(state, port)
    paused = await post.record_quality_failure(
        state,
        error="额度不足 Authorization: Bearer qc-secret token=qc-token https://billing.example.com/pay?token=query-secret",
        attempts=1,
        quota_insufficient=True,
        raw={"status_code": 402, "api_key": "raw-secret"},
    )
    assert paused.status is WorkflowStatus.PAUSED_QUOTA
    persisted = json.dumps(paused.quality_review, ensure_ascii=False)
    assert "qc-secret" not in persisted
    assert "qc-token" not in persisted
    assert "query-secret" not in persisted
    assert "raw-secret" not in persisted
    with pytest.raises(ValueError, match="人工确认"):
        await post.finish(paused)

    retry = await post.retry_quality_review(paused)
    assert retry.status is WorkflowStatus.RUNNING
    assert retry.pending_operation is not None


@pytest.mark.asyncio
async def test_skill_calls_use_existing_contract_without_extra_vendor_fields():
    generation, port, _ = await _complete_generation()

    class MergeSkill:
        def __init__(self):
            self.call = None
            self.calls = 0

        async def merge_videos(self, video_urls, duration=30, size="1080p", model=None):
            self.calls += 1
            self.call = {"video_urls": video_urls, "duration": duration, "size": size, "model": model}
            return GenerationResult(ok=True, url="https://videos.example.com/merged.mp4", task_id="merge-contract")

    merge_skill = MergeSkill()
    post = VideoPostProductionWorkflowService(port)
    state = await post.merge_with_skill(generation, skill=merge_skill)
    assert merge_skill.call["video_urls"] == [item["video_url"] for item in sorted(generation.scene_videos, key=lambda item: item["scene_index"])]
    duplicate_merge = await post.merge_with_skill(generation, skill=merge_skill)
    assert merge_skill.calls == 1
    assert duplicate_merge.pending_operation is None
    assert duplicate_merge.merged_video == state.merged_video
    assert duplicate_merge.terminal_claims == state.terminal_claims

    class QualitySkill:
        def __init__(self):
            self.call = None
            self.calls = 0

        async def review_video_quality(
            self,
            merged_video_url,
            scene_videos,
            scene_packages=None,
            brief=None,
            materials=None,
            user_feedback=None,
            checks=None,
            platform=None,
            ratio=None,
            size=None,
        ):
            self.calls += 1
            self.call = {
                "merged_video_url": merged_video_url,
                "scene_videos": scene_videos,
                "scene_packages": scene_packages,
                "brief": brief,
                "materials": materials,
                "user_feedback": user_feedback,
                "ratio": ratio,
                "size": size,
            }
            return VideoQualityReviewResult(ok=True, task_id="qc-contract", raw={"passed": True})

    quality_skill = QualitySkill()
    reviewed = await post.quality_review_with_skill(state, user_feedback="请按用户意见检查商品露出", skill=quality_skill)
    assert quality_skill.call["brief"]["creation_contract"]["video_model"] == "seedance-2.0"
    assert quality_skill.call["user_feedback"] == "请按用户意见检查商品露出"
    assert reviewed.current_stage is VideoPostProductionStage.VIDEO_REVIEW
    duplicate_review = await post.quality_review_with_skill(state, user_feedback="请按用户意见检查商品露出", skill=quality_skill)
    assert quality_skill.calls == 1
    assert duplicate_review.pending_operation is None
    assert duplicate_review.quality_review == reviewed.quality_review
    assert duplicate_review.terminal_claims == reviewed.terminal_claims


@pytest.mark.asyncio
async def test_prestart_lease_recovers_only_before_provider_call_marker():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    started_at = state.updated_at

    first = await port.claim_video_operation_start(
        expected=state.pending_operation,
        owner="worker-a",
        now=started_at,
        lease_seconds=30,
    )
    assert first.acquired is True
    assert first.job.status is ExternalJobStatus.CREATED
    blocked = await port.claim_video_operation_start(
        expected=first.job,
        owner="worker-b",
        now=started_at + timedelta(seconds=1),
        lease_seconds=30,
    )
    assert blocked.acquired is False

    takeover = await port.claim_video_operation_start(
        expected=blocked.job,
        owner="worker-b",
        now=started_at + timedelta(seconds=31),
        lease_seconds=30,
    )
    assert takeover.acquired is True
    polling = await port.mark_video_operation_call_started(
        expected=takeover.job,
        owner="worker-b",
        now=started_at + timedelta(seconds=31),
    )
    assert polling.status is ExternalJobStatus.POLLING
    no_takeover = await port.claim_video_operation_start(
        expected=polling,
        owner="worker-c",
        now=started_at + timedelta(minutes=5),
        lease_seconds=30,
    )
    assert no_takeover.acquired is False


@pytest.mark.asyncio
async def test_merge_result_cannot_bypass_qc_before_user_revision():
    generation, port, generation_service = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(
        state,
        merged_video_url="https://videos.example.com/merged.mp4",
        provider_job_id="merge-no-qc-bypass",
    )

    with pytest.raises(ValueError, match="不能直接绕过质检"):
        await post.apply_user_revision(
            state,
            scene_patches={generation.scene_packages[0]["scene_id"]: {"storyline": "绕过质检的修改"}},
            generation_service=generation_service,
        )


@pytest.mark.asyncio
async def test_quality_feedback_is_frozen_while_pending_and_after_terminal():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(
        state,
        merged_video_url="https://videos.example.com/merged.mp4",
        provider_job_id="merge-feedback-freeze",
    )
    state = await post.start_quality_review(state, user_feedback="请保留这条原始用户意见")
    state = await _claim_started(state, port)
    pending_tampered = replace(
        state,
        _quality_feedback_json=json.dumps("篡改后的意见", ensure_ascii=False),
    )
    with pytest.raises(ValueError, match="幂等键"):
        await post.record_quality_success(
            pending_tampered,
            result=VideoQualityReviewWorkflowResult(ok=True, passed=True),
            provider_job_id="qc-feedback-freeze",
        )

    state = await post.record_quality_success(
        state,
        result=VideoQualityReviewWorkflowResult(ok=True, passed=True),
        provider_job_id="qc-feedback-freeze",
    )
    terminal_tampered = replace(
        state,
        _quality_feedback_json=json.dumps("终态后的篡改意见", ensure_ascii=False),
    )
    with pytest.raises(ValueError, match="幂等键"):
        await post.finish(terminal_tampered)


@pytest.mark.asyncio
async def test_qc_call_failure_still_allows_user_scoped_revision():
    generation, port, generation_service = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(
        state,
        merged_video_url="https://videos.example.com/merged.mp4",
        provider_job_id="merge-user-revision",
    )
    state = await post.start_quality_review(state, user_feedback="请定位首镜商品卖点问题")
    state = await _claim_started(state, port)
    state = await post.record_quality_failure(state, error="模型网关失败", attempts=1, retryable=True)
    target = generation.scene_packages[0]["scene_id"]
    revised = await post.apply_user_revision(
        state,
        scene_patches={target: {"storyline": "按用户意见突出首镜商品卖点"}},
        generation_service=generation_service,
    )
    assert revised.dirty_scene_ids == [target]
    assert {item["scene_id"] for item in revised.scene_videos} == {
        item["scene_id"] for item in generation.scene_videos if item["scene_id"] != target
    }


@pytest.mark.asyncio
async def test_postproduction_workflow_record_exposes_only_stable_artifact_refs():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(
        state,
        merged_video_url="https://videos.example.com/merged.mp4?token=secret",
        provider_job_id="merge-artifact",
    )
    state = await post.start_quality_review(state, user_feedback="请检查成片质量")
    state = await _claim_started(state, port)
    state = await post.record_quality_success(
        state,
        result=VideoQualityReviewWorkflowResult(ok=True, passed=True, raw={"details": "https://qc.example.com/report?id=secret"}),
        provider_job_id="qc-artifact",
    )
    record = post.to_workflow_record(state)
    assert record.pending_external_job is None
    assert all(item.startswith("artifact:") for item in record.latest_artifact_refs)
    assert "https://" not in " ".join(record.latest_artifact_refs)
    assert state.merged_video["video_url"] == "https://videos.example.com/merged.mp4"
    assert state.quality_review["raw"]["details"] == "https://qc.example.com/report"
    assert VideoPostProductionStage.COMPLETED.value == "completed"
    assert OperationConflictError is not None


@pytest.mark.asyncio
async def test_quota_message_is_sanitized_and_forged_review_cannot_finish():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    paused = await post.record_merge_failure(
        state,
        error="额度不足 Authorization: Bearer secret token=abc https://billing.example.com/pay?token=xyz",
        attempts=1,
        raw={"status_code": 402},
    )
    persisted = json.dumps(paused.merge_error, ensure_ascii=False)
    assert "secret" not in persisted
    assert "token=abc" not in persisted
    assert "token=xyz" not in persisted

    forged = replace(
        state,
        current_stage=VideoPostProductionStage.VIDEO_REVIEW,
        status=WorkflowStatus.AWAITING_USER,
        _merged_video_json=json.dumps(
            {
                "video_url": "https://videos.example.com/forged.mp4",
                "task_id": "forged",
                "endpoint": "/api/video/merge",
                "scene_videos": state.merge_request["scene_videos"],
                "raw": {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        _pending_operation=None,
    )
    with pytest.raises(ValueError, match="终态|权威"):
        await post.finish(forged)


@pytest.mark.asyncio
async def test_authoritative_terminal_claim_rejects_merged_payload_and_job_tampering():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    state = await _claim_started(state, port)
    state = await post.record_merge_success(
        state,
        merged_video_url="https://videos.example.com/merged.mp4",
        provider_job_id="merge-authority",
    )

    changed_url = copy.deepcopy(state.merged_video)
    changed_url["video_url"] = "https://videos.example.com/tampered.mp4"
    with pytest.raises(ValueError, match="权威终态 claim"):
        await post.finish(
            replace(
                state,
                _merged_video_json=json.dumps(changed_url, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )

    changed_scenes = copy.deepcopy(state.merged_video)
    changed_scenes["scene_videos"] = list(reversed(changed_scenes["scene_videos"]))
    with pytest.raises(ValueError, match="权威分镜顺序"):
        await post.finish(
            replace(
                state,
                _merged_video_json=json.dumps(changed_scenes, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )

    claims = state.terminal_claims
    claims[0]["job"]["attempt"] += 1
    with pytest.raises(ValueError, match="幂等键"):
        await post.finish(
            replace(
                state,
                _terminal_claims_json=json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )

    claims = state.terminal_claims
    claims[0]["job"]["idempotency_key"] = "pf:video-post:tampered"
    with pytest.raises(ValueError, match="幂等键"):
        await post.finish(
            replace(
                state,
                _terminal_claims_json=json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )


@pytest.mark.asyncio
async def test_self_consistent_forged_checkpoint_cannot_finish_without_repository_terminal():
    generation, port, _ = await _complete_generation()
    post = VideoPostProductionWorkflowService(port)
    state = await post.start_merge(generation)
    payload = {
        "video_url": "https://videos.example.com/forged.mp4",
        "task_id": "forged-provider",
        "endpoint": "/api/video/merge",
        "scene_videos": state.merge_request["scene_videos"],
        "raw": {},
    }
    result_hash = postproduction_module._terminal_hash("merge_succeeded", payload)
    forged_job = state.pending_operation.model_copy(
        update={
            "status": ExternalJobStatus.SUCCEEDED,
            "provider_job_id": "forged-provider",
        }
    )
    terminal_claims = [
        {
            "result_type": "merge_succeeded",
            "result_hash": result_hash,
            "stage_version": state.stage_version,
            "job": forged_job.model_dump(mode="json"),
            "payload": payload,
        }
    ]
    forged = replace(
        state,
        current_stage=VideoPostProductionStage.VIDEO_REVIEW,
        status=WorkflowStatus.AWAITING_USER,
        stage_version=state.stage_version + 1,
        context_version=state.context_version + 1,
        _merged_video_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        _pending_operation=None,
        _terminal_result_hash=result_hash,
        _terminal_claims_json=json.dumps(terminal_claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )

    with pytest.raises(OperationConflictError, match="可信 Repository"):
        await post.finish(forged)
    persisted = await port.get(state.pending_operation.job_id)
    assert persisted.status is ExternalJobStatus.CREATED
    assert persisted.provider_job_id is None
