from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import test_agent_video_workflow_postproduction as postproduction_tests
from test_agent_video_workflow_generation import _AtomicFakeOperationPort, _reviewed_scene_package_state

from pixelflow.agent_runtime.contracts import ExternalJobStatus, WorkflowStatus
from pixelflow.agent_runtime.fakes import FakeOperationPort
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.agent_workflows.video import (
    VideoDeliveryWorkflowService,
    VideoPostProductionStage,
    VideoPostProductionWorkflowService,
    VideoQualityReviewWorkflowResult,
    VideoSceneGenerationWorkflowService,
)
from pixelflow.agent_workflows.video import video_generation as generation_module
from pixelflow.jianying_draft.models import (
    JianyingDraftResult,
    JianyingDraftStatus,
    compute_storyboard_version_id,
)
from pixelflow.jianying_draft.skill import JianyingDraftCapability


class _FakeJianyingDraftSkill:
    def __init__(
        self,
        results: list[JianyingDraftResult],
        *,
        available: bool = True,
    ) -> None:
        self._results = list(results)
        self._available = available
        self.capability_calls = 0
        self.calls = 0
        self.requests = []

    async def capability(self) -> JianyingDraftCapability:
        self.capability_calls += 1
        return JianyingDraftCapability(available=self._available)

    async def generate(self, request):
        self.calls += 1
        self.requests.append(request.model_copy(deep=True))
        if not self._results:
            raise AssertionError("测试未配置剪映草稿结果")
        return self._results.pop(0).model_copy(deep=True)


async def _video_review_state(*, suffix: str = ""):
    package = _reviewed_scene_package_state()
    port = _AtomicFakeOperationPort()
    generation_service = VideoSceneGenerationWorkflowService(port)
    generation = await generation_service.start_from_reviewed_scene_package(package)
    for scene in package.scene_package.scene_packages:
        generation = await generation_service.record_scene_success(
            generation,
            scene_id=scene["scene_id"],
            video_url=f"https://videos.example.com/{scene['scene_id']}{suffix}.mp4",
            provider_job_id=f"provider-{scene['scene_id']}{suffix}",
        )
    postproduction_service = VideoPostProductionWorkflowService(port)
    review = await postproduction_service.start_merge(generation)
    review = await postproduction_tests._claim_started(review, port)
    review = await postproduction_service.record_merge_success(
        review,
        merged_video_url=f"https://videos.example.com/merged{suffix}.mp4",
        provider_job_id=f"merge-provider{suffix or '-1'}",
    )
    return review, port, generation_service, postproduction_service


def _succeeded_result(
    *,
    provider_task_id: str = "jianying-provider-1",
    download_url: str = "https://tos.example.com/jianying-draft.zip",
    expire_at: datetime | None = None,
) -> JianyingDraftResult:
    return JianyingDraftResult(
        status=JianyingDraftStatus.SUCCEEDED,
        provider_task_id=provider_task_id,
        download_url=download_url,
        file_name="jianying-draft.zip",
        expire_at=expire_at,
        message="剪映草稿已生成",
    )


@pytest.mark.asyncio
async def test_jianying_uses_only_ordered_successful_scenes_and_never_merged_video():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    started = await service.start_jianying_draft(delivery, project_name="智能戒指新品广告")

    request = started.pending_jianying_request
    assert request is not None
    assert [item["scene_index"] for item in request["scenes"]] == sorted(item["scene_index"] for item in request["scenes"])
    assert [item["video_url"] for item in request["scenes"]] == [item["video_url"] for item in sorted(review.generation_state.scene_videos, key=lambda item: item["scene_index"])]
    assert review.merged_video["video_url"] not in {item["video_url"] for item in request["scenes"]}
    assert request["storyboard_version_id"] == compute_storyboard_version_id(service.current_jianying_scenes(delivery))
    assert started.pending_operation is not None
    assert started.pending_operation.stage == "jianying_draft"


@pytest.mark.asyncio
async def test_same_storyboard_claim_and_skill_call_are_idempotent():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    expire_at = delivery.updated_at + timedelta(hours=1)
    skill = _FakeJianyingDraftSkill([_succeeded_result(expire_at=expire_at)])

    completed = await service.generate_jianying_with_skill(delivery, skill=skill)
    duplicate = await service.generate_jianying_with_skill(delivery, skill=skill)

    assert skill.calls == 1
    assert duplicate.jianying_draft_records == completed.jianying_draft_records
    assert duplicate.pending_operation is None
    record = completed.jianying_draft_records[completed.current_storyboard_version_id]
    assert record["status"] == "succeeded"
    assert record["job_id"].startswith("fake-job-")
    assert record["provider_task_id"] == "jianying-provider-1"
    assert completed.jianying_artifact_refs


@pytest.mark.asyncio
async def test_resume_queries_original_operation_and_never_restarts_skill():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    started = await service.start_jianying_draft(delivery)
    resumed = await service.resume_jianying_draft(started)

    assert resumed.pending_operation == started.pending_operation
    assert len(port._jobs_by_id) == len(review.generation_state.scene_videos) + 2

    assert started.pending_operation is not None
    port._jobs_by_id.pop(started.pending_operation.job_id)
    with pytest.raises(OperationConflictError, match="可信 Repository 中不存在"):
        await service.resume_jianying_draft(started)
    with pytest.raises(ValueError, match="原子"):
        await VideoDeliveryWorkflowService(FakeOperationPort()).initialize(review)


@pytest.mark.asyncio
async def test_forged_pending_operation_must_exist_in_trusted_repository():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    started = await service.start_jianying_draft(delivery)
    assert started.pending_operation is not None
    forged = replace(
        started,
        _pending_operation=started.pending_operation.model_copy(update={"job_id": "fake-job-forged"}),
    )

    with pytest.raises(OperationConflictError, match="可信 Repository"):
        await service.start_jianying_draft(forged)


@pytest.mark.asyncio
async def test_pending_capability_check_uses_trusted_repository_status():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    started = await service.start_jianying_draft(delivery)
    assert started.pending_operation is not None
    forged = replace(
        started,
        _pending_operation=started.pending_operation.model_copy(update={"status": ExternalJobStatus.POLLING}),
    )
    unavailable = _FakeJianyingDraftSkill([], available=False)

    unchanged = await service.generate_jianying_with_skill(forged, skill=unavailable)
    trusted = await port.get(started.pending_operation.job_id)
    assert unchanged.pending_operation is not None
    assert unchanged.pending_operation.status is ExternalJobStatus.CREATED
    assert unavailable.capability_calls == 1
    assert unavailable.calls == 0
    assert trusted is not None
    assert trusted.status is ExternalJobStatus.CREATED


@pytest.mark.asyncio
async def test_failed_draft_requires_explicit_retry_and_creates_next_attempt():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    failed_skill = _FakeJianyingDraftSkill([JianyingDraftResult(status=JianyingDraftStatus.FAILED, message="第三方剪映草稿任务处理失败：素材校验失败")])
    failed = await service.generate_jianying_with_skill(delivery, skill=failed_skill)

    with pytest.raises(ValueError, match="显式重试"):
        await service.start_jianying_draft(failed)

    retry_started = await service.start_jianying_draft(failed, retry_failed=True)
    pending_projection = service.to_artifact_projection(retry_started)["pendingJianyingDraftJob"]
    assert pending_projection["request"]["retry_failed"] is True
    retry_skill = _FakeJianyingDraftSkill([_succeeded_result(provider_task_id="jianying-provider-2")])
    retried = await service.generate_jianying_with_skill(
        retry_started,
        skill=retry_skill,
        retry_failed=True,
    )
    record = retried.jianying_draft_records[retried.current_storyboard_version_id]
    assert record["status"] == "succeeded"
    assert record["provider_task_id"] == "jianying-provider-2"
    assert retried.operation_attempts[retried.current_storyboard_version_id] == 2
    terminal_claim = record["terminal_claim"]
    operation_request = port._requests_by_idempotency_key[terminal_claim["job"]["idempotency_key"]]
    assert terminal_claim["payload"]["request_hash"] == operation_request.request_hash


@pytest.mark.asyncio
async def test_unexpired_success_is_reused_but_expired_success_can_restart():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    first_time = delivery.updated_at + timedelta(seconds=1)
    first_skill = _FakeJianyingDraftSkill([_succeeded_result(expire_at=first_time + timedelta(minutes=5))])
    succeeded = await service.generate_jianying_with_skill(delivery, skill=first_skill, now=first_time)

    unused_skill = _FakeJianyingDraftSkill([_succeeded_result(provider_task_id="unexpected")])
    reused = await service.generate_jianying_with_skill(
        succeeded,
        skill=unused_skill,
        now=first_time + timedelta(minutes=1),
    )
    assert reused is succeeded
    assert unused_skill.calls == 0

    replacement_skill = _FakeJianyingDraftSkill([_succeeded_result(provider_task_id="jianying-provider-new", download_url="https://tos.example.com/new.zip")])
    replaced = await service.generate_jianying_with_skill(
        succeeded,
        skill=replacement_skill,
        now=first_time + timedelta(minutes=6),
    )
    assert replacement_skill.calls == 1
    assert replaced.jianying_draft_records[replaced.current_storyboard_version_id]["download_url"] == "https://tos.example.com/new.zip"


@pytest.mark.asyncio
async def test_unavailable_capability_does_not_claim_empty_job_and_can_recover():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    original_job_count = len(port._jobs_by_id)
    skill = _FakeJianyingDraftSkill([], available=False)

    unavailable = await service.generate_jianying_with_skill(delivery, skill=skill)
    assert unavailable is delivery
    assert unavailable.jianying_draft_records == {}
    assert len(port._jobs_by_id) == original_job_count
    assert skill.calls == 0

    recovered = await service.generate_jianying_with_skill(
        unavailable,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    assert recovered.jianying_draft_records[recovered.current_storyboard_version_id]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_jianying_generation_timeout_becomes_retryable_terminal():
    class NeverCompletesSkill:
        def __init__(self) -> None:
            self.calls = 0

        async def capability(self) -> JianyingDraftCapability:
            return JianyingDraftCapability(available=True)

        async def generate(self, request):
            self.calls += 1
            await asyncio.Event().wait()
            raise AssertionError("不可到达")

    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port, timeout_seconds=0.01)
    delivery = await service.initialize(review)
    skill = NeverCompletesSkill()

    timed_out = await service.generate_jianying_with_skill(delivery, skill=skill)
    record = timed_out.jianying_draft_records[timed_out.current_storyboard_version_id]
    assert skill.calls == 1
    assert record["status"] == "timeout"
    assert record["message"] == "剪映草稿生成超时，请重试。"
    with pytest.raises(ValueError, match="显式重试"):
        await service.start_jianying_draft(timed_out)


@pytest.mark.asyncio
async def test_legacy_not_configured_record_can_recover_without_failed_retry_flag():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    not_configured = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill(
            [
                JianyingDraftResult(
                    status=JianyingDraftStatus.NOT_CONFIGURED,
                    message="剪映草稿服务待接入",
                )
            ]
        ),
    )

    recovered = await service.generate_jianying_with_skill(
        not_configured,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    record = recovered.jianying_draft_records[recovered.current_storyboard_version_id]
    assert record["status"] == "succeeded"
    assert recovered.operation_attempts[recovered.current_storyboard_version_id] == 2


@pytest.mark.asyncio
async def test_failed_result_drops_non_public_provider_fields():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    failed = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill(
            [
                JianyingDraftResult(
                    status=JianyingDraftStatus.FAILED,
                    provider_task_id="provider-internal-id",
                    download_url="https://provider.example.com/debug.zip",
                    file_name="debug.zip",
                    expire_at=delivery.updated_at + timedelta(hours=1),
                    message="第三方剪映草稿任务处理失败：素材校验失败",
                )
            ]
        ),
    )
    record = failed.jianying_draft_records[failed.current_storyboard_version_id]
    assert record["provider_task_id"] is None
    assert record["download_url"] is None
    assert record["file_name"] is None
    assert record["expire_at"] is None


@pytest.mark.asyncio
async def test_new_storyboard_keeps_old_draft_history_and_clears_old_final_delivery():
    review, port, generation_service, postproduction_service = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    delivery = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    delivery = await service.record_final_video_download(
        delivery,
        download_url=review.merged_video["video_url"],
        downloaded_at=delivery.updated_at + timedelta(seconds=1),
    )
    old_version = delivery.current_storyboard_version_id

    qc = await postproduction_service.start_quality_review(
        review,
        user_feedback="请把第二镜的产品特写拉近",
        now=delivery.updated_at + timedelta(seconds=2),
    )
    qc = await postproduction_tests._claim_started(qc, port)
    target_scene_id = review.generation_state.scene_packages[1]["scene_id"]
    qc = await postproduction_service.record_quality_success(
        qc,
        result=VideoQualityReviewWorkflowResult(
            ok=True,
            passed=False,
            affected_scene_ids=[target_scene_id],
            revision_prompt="第二镜增加产品近景",
        ),
        provider_job_id="qc-provider-new-version",
        now=qc.updated_at + timedelta(seconds=1),
    )
    revised_generation = await postproduction_service.apply_user_revision(
        qc,
        scene_patches={target_scene_id: {"storyline": "第二镜切换为产品近景"}},
        generation_service=generation_service,
        now=qc.updated_at + timedelta(seconds=1),
    )
    revised_generation = await generation_service.record_scene_success(
        revised_generation,
        scene_id=target_scene_id,
        video_url="https://videos.example.com/revised-scene.mp4",
        provider_job_id="provider-revised-scene",
        now=revised_generation.updated_at + timedelta(seconds=1),
    )
    revised_review = await postproduction_service.start_merge(
        revised_generation,
        now=revised_generation.updated_at + timedelta(seconds=1),
    )
    revised_review = await postproduction_tests._claim_started(revised_review, port)
    revised_review = await postproduction_service.record_merge_success(
        revised_review,
        merged_video_url="https://videos.example.com/revised-merged.mp4",
        provider_job_id="merge-provider-revised",
        now=revised_review.updated_at + timedelta(seconds=1),
    )
    synchronized = await service.synchronize_postproduction(delivery, revised_review)

    assert synchronized.current_storyboard_version_id != old_version
    assert old_version in synchronized.jianying_draft_records
    assert synchronized.final_video_delivery is None
    assert synchronized.jianying_draft_records[old_version]["download_url"] == "https://tos.example.com/jianying-draft.zip"


@pytest.mark.asyncio
async def test_video_finish_preserves_same_version_history_and_download_projection():
    review, port, _, postproduction_service = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    delivery = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    finished = await postproduction_service.finish(review)
    synchronized = await service.synchronize_postproduction(delivery, finished)

    assert synchronized.status is WorkflowStatus.COMPLETED
    assert synchronized.current_stage is VideoPostProductionStage.COMPLETED
    assert synchronized.current_storyboard_version_id == delivery.current_storyboard_version_id
    assert synchronized.jianying_draft_records == delivery.jianying_draft_records
    projection = service.to_artifact_projection(synchronized)
    assert projection["videoAccepted"] is True
    assert projection["jianyingDraftRecords"][synchronized.current_storyboard_version_id]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_only_merged_video_download_completes_final_delivery_projection():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    delivery = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    version_id = delivery.current_storyboard_version_id
    draft_url = delivery.jianying_draft_records[version_id]["download_url"]
    draft_downloaded = await service.record_jianying_download(
        delivery,
        storyboard_version_id=version_id,
        download_url=draft_url,
        downloaded_at=delivery.updated_at + timedelta(seconds=1),
    )
    assert draft_downloaded.final_video_delivery is None
    assert draft_downloaded.jianying_draft_records[version_id]["draftDownloadedAt"]

    scene_url = review.generation_state.scene_videos[0]["video_url"]
    with pytest.raises(ValueError, match="合并成品"):
        await service.record_final_video_download(
            draft_downloaded,
            download_url=scene_url,
            downloaded_at=draft_downloaded.updated_at + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="合并成品"):
        await service.record_final_video_download(
            draft_downloaded,
            download_url=draft_url,
            downloaded_at=draft_downloaded.updated_at + timedelta(seconds=1),
        )

    timestamp = draft_downloaded.updated_at + timedelta(seconds=2)
    downloaded = await service.record_final_video_download(
        draft_downloaded,
        download_url=review.merged_video["video_url"],
        downloaded_at=timestamp,
    )
    duplicate = await service.record_final_video_download(
        downloaded,
        download_url=review.merged_video["video_url"],
        downloaded_at=timestamp + timedelta(seconds=10),
    )
    assert duplicate.final_video_delivery == downloaded.final_video_delivery
    projection = service.to_artifact_projection(downloaded)
    assert projection["deliveryDownloadedAt"] == timestamp.isoformat()
    assert projection["deliveryDownloadedUrl"] == review.merged_video["video_url"]
    assert downloaded.delivery_artifact_ref in service.to_workflow_record(downloaded).latest_artifact_refs


@pytest.mark.asyncio
async def test_forged_draft_history_is_rejected_by_trusted_terminal_claim():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    delivery = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill([_succeeded_result()]),
    )
    records = delivery.jianying_draft_records
    version_id = delivery.current_storyboard_version_id
    records[version_id]["download_url"] = "https://attacker.example.com/forged.zip"
    claim = records[version_id]["terminal_claim"]
    payload = copy.deepcopy(claim["payload"])
    payload["result"]["download_url"] = records[version_id]["download_url"]
    claim["payload"] = payload
    claim["result_hash"] = hashlib.sha256(
        json.dumps(
            {"result_type": claim["result_type"], "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    forged = replace(
        delivery,
        _jianying_draft_records_json=json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )

    with pytest.raises(OperationConflictError, match="可信 Repository"):
        await service.record_jianying_download(
            forged,
            storyboard_version_id=version_id,
            download_url=records[version_id]["download_url"],
            downloaded_at=delivery.updated_at + timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_forged_generation_provider_task_id_is_rejected_before_new_draft_call():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    normal = await service.initialize(review)
    normal_version_id = normal.current_storyboard_version_id
    videos = copy.deepcopy(review.generation_state.scene_videos)
    claims = copy.deepcopy(review.generation_state.terminal_claims)
    videos[0]["task_id"] = "forged-provider-task-id"
    claim = claims[0]
    claim["job"]["provider_job_id"] = "forged-provider-task-id"
    video = videos[0]
    claim["result_hash"] = generation_module._terminal_result_hash(
        "succeeded",
        {
            "scene_id": video["scene_id"],
            "video_url": video["video_url"],
            "mode": video["mode"],
            "endpoint": video["endpoint"],
            "provider_job_id": video["task_id"],
            "raw": video["raw"],
        },
    )
    forged_generation = replace(
        review.generation_state,
        _scene_videos_json=json.dumps(
            videos,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        _terminal_claims_json=json.dumps(
            claims,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    forged_review = replace(review, _generation_state=forged_generation)
    skill = _FakeJianyingDraftSkill([_succeeded_result()])

    with pytest.raises(OperationConflictError, match="分镜终态.*可信 Repository"):
        forged = await service.initialize(forged_review)
        assert forged.current_storyboard_version_id != normal_version_id
        await service.generate_jianying_with_skill(forged, skill=skill)
    assert skill.calls == 0


@pytest.mark.asyncio
async def test_sensitive_provider_failure_is_redacted_before_history_projection():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    failed = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill(
            [
                JianyingDraftResult(
                    status=JianyingDraftStatus.FAILED,
                    message="Authorization: Bearer secret token=abc https://provider.example.com/fail?api_key=hidden",
                )
            ]
        ),
    )
    serialized = json.dumps(failed.jianying_draft_records, ensure_ascii=False)
    assert "secret" not in serialized
    assert "abc" not in serialized
    assert "hidden" not in serialized
    assert "Authorization" not in serialized


@pytest.mark.asyncio
async def test_unapproved_provider_diagnostic_is_replaced_with_public_failure():
    review, port, _, _ = await _video_review_state()
    service = VideoDeliveryWorkflowService(port)
    delivery = await service.initialize(review)
    failed = await service.generate_jianying_with_skill(
        delivery,
        skill=_FakeJianyingDraftSkill(
            [
                JianyingDraftResult(
                    status=JianyingDraftStatus.FAILED,
                    message="provider worker-17 database shard unavailable",
                )
            ]
        ),
    )
    record = failed.jianying_draft_records[failed.current_storyboard_version_id]
    assert record["message"] == "剪映草稿生成失败，请稍后重试。"


@pytest.mark.asyncio
async def test_delivery_mutations_fail_closed_without_atomic_operation_port():
    review, _, _, _ = await _video_review_state()
    plain_service = VideoDeliveryWorkflowService(FakeOperationPort())
    with pytest.raises(ValueError, match="原子"):
        await plain_service.initialize(review)
