"""分镜视频 Operation 完成事件增量投影。"""

from __future__ import annotations

from datetime import UTC, datetime

from pixelflow.video_agent.operations.projector import (
    build_scene_generation_success_patch,
    count_polling_scene_generation_jobs,
)


NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)


def test_build_scene_generation_success_patch_updates_single_job() -> None:
    payload = {
        "dirty_scene_ids": ["scene-1", "scene-2"],
        "scenes": [
            {
                "scene_id": "scene-1",
                "scene_index": 1,
                "edit_status": "重新生成中",
                "generation_jobs": [
                    {
                        "job_id": "job-1",
                        "status": "polling",
                        "plan_step_id": "step-1",
                        "variant_index": 1,
                    }
                ],
                "variants": [],
            },
            {
                "scene_id": "scene-2",
                "scene_index": 2,
                "edit_status": "重新生成中",
                "generation_jobs": [
                    {
                        "job_id": "job-2",
                        "status": "polling",
                        "plan_step_id": "step-1",
                        "variant_index": 1,
                    }
                ],
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
            "completed_at": NOW.isoformat(),
        },
        now=NOW,
    )
    assert patch is not None
    assert patch["scene_video_progress"]["completed"] == 1
    assert patch["scene_video_progress"]["total"] == 2
    assert patch["scene_video_progress"]["scene_index"] == 1
    # 只回写完成镜，避免整表覆盖并发生成中的其它镜。
    assert [item["scene_id"] for item in patch["scenes"]] == ["scene-1"]
    scene_1 = patch["scenes"][0]
    assert scene_1["edit_status"] == "重新生成完成"
    assert scene_1["generation_jobs"][0]["status"] == "succeeded"
    assert scene_1["variants"][0]["video_url"].endswith("scene-1.mp4")
    assert "scene-1" not in patch["dirty_scene_ids"]
    assert "scene-2" in patch["dirty_scene_ids"]
    # 合并后统计：补丁内只有已成功的 scene-1，polling 计数为 0。
    assert count_polling_scene_generation_jobs(patch, plan_step_id="step-1") == 0
    merged_scenes = [
        scene_1 if item["scene_id"] == "scene-1" else item
        for item in payload["scenes"]
    ]
    assert count_polling_scene_generation_jobs(
        {**payload, "scenes": merged_scenes},
        plan_step_id="step-1",
    ) == 1


def test_build_scene_generation_success_patch_by_stage_digest_without_jobs() -> None:
    """旧冲突批次：Workspace 没有 generation_jobs 时，仍按 stage digest 回填 URL。"""
    import hashlib

    scene_id = "scene-hero-1"
    digest = hashlib.sha256(scene_id.encode()).hexdigest()[:12]
    payload = {
        "scenes": [
            {
                "scene_id": scene_id,
                "scene_index": 1,
                "edit_status": "重新生成中",
                "generation_jobs": [],
                "variants": [],
            }
        ],
        "dirty_scene_ids": [scene_id],
    }
    patch = build_scene_generation_success_patch(
        payload,
        job_id="orphan-job-1",
        result={
            "variant_id": "variant-orphan",
            "artifact_ref": "artifact:orphan",
            "video_url": "https://cdn.example.invalid/orphan.mp4",
        },
        now=NOW,
        stage=f"generate_scene:{digest}:v1",
        plan_step_id="step-scenes",
    )
    assert patch is not None
    scene = patch["scenes"][0]
    assert scene["edit_status"] == "重新生成完成"
    assert scene["variants"][0]["video_url"].endswith("orphan.mp4")
    assert scene["generation_jobs"][0]["job_id"] == "orphan-job-1"
    assert patch["scene_video_progress"]["completed"] == 1


def test_build_scene_generation_success_patch_is_idempotent() -> None:
    payload = {
        "scenes": [
            {
                "scene_id": "scene-1",
                "scene_index": 1,
                "edit_status": "重新生成完成",
                "generation_jobs": [
                    {
                        "job_id": "job-1",
                        "status": "succeeded",
                        "variant_id": "variant-1",
                        "artifact_ref": "artifact:scene-1-v1",
                        "video_url": "https://cdn.example.invalid/scene-1.mp4",
                        "plan_step_id": "step-1",
                    }
                ],
                "variants": [
                    {
                        "variant_id": "variant-1",
                        "artifact_ref": "artifact:scene-1-v1",
                        "video_url": "https://cdn.example.invalid/scene-1.mp4",
                        "selected": True,
                    }
                ],
            }
        ],
        "dirty_scene_ids": [],
    }
    assert (
        build_scene_generation_success_patch(
            payload,
            job_id="job-1",
            result={
                "variant_id": "variant-1",
                "artifact_ref": "artifact:scene-1-v1",
                "video_url": "https://cdn.example.invalid/scene-1.mp4",
            },
            now=NOW,
        )
        is None
    )


def test_build_scene_generation_failure_patch_records_error() -> None:
    from pixelflow.video_agent.operations.projector import (
        build_scene_generation_failure_patch,
    )

    payload = {
        "scenes": [
            {
                "scene_id": "scene-2",
                "scene_index": 2,
                "edit_status": "重新生成中",
                "generation_jobs": [
                    {
                        "job_id": "job-fail",
                        "status": "polling",
                        "plan_step_id": "step-1",
                        "variant_index": 1,
                    }
                ],
                "variants": [],
            }
        ],
    }
    patch = build_scene_generation_failure_patch(
        payload,
        job_id="job-fail",
        status="failed",
        reason_code="provider_business_failed",
        message="供应商任务执行失败。",
        now=NOW,
    )
    assert patch is not None
    scene = patch["scenes"][0]
    assert scene["edit_status"] == "重新生成失败"
    assert scene["generation_jobs"][0]["status"] == "failed"
    assert scene["generation_jobs"][0]["error"] == "供应商任务执行失败。"
    assert patch["scene_video_progress"]["completed"] == 1
    assert patch["scene_video_progress"]["total"] == 1

