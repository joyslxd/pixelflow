"""验证 GenerationJob 视频终态的增量 Workspace 投影。"""

from __future__ import annotations

from datetime import UTC, datetime

from pixelflow.generation_jobs.projector import (
    build_scene_generation_failure_patch,
    build_scene_generation_success_patch,
    count_polling_scene_generation_jobs,
)

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)


def test_generation_job_success_updates_only_target_scene() -> None:
    payload = {
        "dirty_scene_ids": ["scene-1", "scene-2"],
        "scenes": [
            {
                "scene_id": "scene-1",
                "scene_index": 1,
                "edit_status": "重新生成中",
                "generation_jobs": [{"job_id": "job-1", "status": "polling", "variant_index": 1}],
                "variants": [],
            },
            {
                "scene_id": "scene-2",
                "scene_index": 2,
                "generation_jobs": [{"job_id": "job-2", "status": "polling", "variant_index": 1}],
                "variants": [],
            },
        ],
    }
    patch = build_scene_generation_success_patch(
        payload,
        job_id="job-1",
        result={
            "variant_id": "variant-1",
            "artifact_ref": "artifact:scene-1-v1",
            "video_url": "https://cdn.example.invalid/scene-1.mp4",
        },
        now=NOW,
    )
    assert patch is not None
    assert patch["scenes"][0]["generation_jobs"][0]["status"] == "succeeded"
    assert patch["scenes"][0]["variants"][0]["video_url"].endswith("scene-1.mp4")
    assert patch["dirty_scene_ids"] == ["scene-2"]
    assert patch["scene_video_progress"]["scene_index"] == 1


def test_generation_job_failure_records_controlled_reason() -> None:
    payload = {
        "scenes": [{
            "scene_id": "scene-2",
            "scene_index": 2,
            "edit_status": "重新生成中",
            "generation_jobs": [{"job_id": "job-fail", "status": "polling"}],
            "variants": [],
        }],
    }
    patch = build_scene_generation_failure_patch(
        payload,
        job_id="job-fail",
        status="failed",
        reason_code="provider_business_failed",
        now=NOW,
    )
    assert patch is not None
    job = patch["scenes"][0]["generation_jobs"][0]
    assert job["status"] == "failed"
    assert job["reason_code"] == "provider_business_failed"
    assert job["error"] == "生成任务执行失败"


def test_generation_job_projector_counts_polling_jobs() -> None:
    assert count_polling_scene_generation_jobs({
        "scenes": [{"generation_jobs": [{"status": "polling"}, {"status": "succeeded"}]}]
    }) == 1
