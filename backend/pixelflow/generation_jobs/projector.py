"""GenerationJob 终态到 V2 Workspace 的增量投影。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime


def build_image_asset_success_patch(payload, *, asset_id: str, result: Mapping[str, object], now: datetime):
    image_url = str(result.get("image_url") or "").strip()
    artifact_ref = str(result.get("artifact_ref") or "").strip()
    if not image_url.startswith("https://") or not artifact_ref.startswith("artifact:"):
        return None
    assets = payload.get("asset_registry")
    if not isinstance(assets, list):
        return None
    changed = False
    next_assets = []
    for item in assets:
        if not isinstance(item, Mapping) or str(item.get("asset_id") or "").strip() != asset_id:
            next_assets.append(item)
            continue
        if item.get("origin") != "planned_generation":
            return None
        next_item = {
            **dict(item),
            "state": "ready",
            "usable_for_video": True,
            "provider_artifact_ref": artifact_ref,
            "image_url": image_url,
            "completed_at": now.isoformat(),
            "generation_job_status": "succeeded",
        }
        next_item.pop("failure_status", None)
        next_item.pop("failure_reason_code", None)
        next_item.pop("failed_at", None)
        next_assets.append(next_item)
        changed = True
    return {"asset_registry": next_assets} if changed else None


def build_image_asset_failure_patch(payload, *, asset_id: str, status: str, reason_code: str | None, now: datetime):
    assets = payload.get("asset_registry")
    if not isinstance(assets, list):
        return None
    changed = False
    next_assets = []
    for item in assets:
        if not isinstance(item, Mapping) or str(item.get("asset_id") or "").strip() != asset_id:
            next_assets.append(item)
            continue
        if item.get("origin") != "planned_generation":
            return None
        next_assets.append(
            {
                **dict(item),
                "state": "failed",
                "usable_for_video": False,
                "failure_status": status,
                "failure_reason_code": (reason_code or "provider_failed")[:128],
                "failed_at": now.isoformat(),
                "generation_job_status": status,
            }
        )
        changed = True
    return {"asset_registry": next_assets} if changed else None


def build_scene_generation_success_patch(payload, *, job_id: str, result: Mapping[str, object], now: datetime):
    video_url = str(result.get("video_url") or "").strip()
    variant_id = str(result.get("variant_id") or "").strip()
    artifact_ref = str(result.get("artifact_ref") or "").strip()
    if not video_url.startswith("https://") or not variant_id or not artifact_ref.startswith("artifact:"):
        return None
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return None
    changed = False
    target_scene = None
    next_scenes = []
    for scene in scenes:
        if not isinstance(scene, Mapping):
            next_scenes.append(scene)
            continue
        jobs = scene.get("generation_jobs")
        if not isinstance(jobs, list) or not any(
            isinstance(item, Mapping) and str(item.get("job_id") or "") == job_id
            for item in jobs
        ):
            next_scenes.append(scene)
            continue
        next_jobs = []
        for item in jobs:
            if not isinstance(item, Mapping) or str(item.get("job_id") or "") != job_id:
                next_jobs.append(item)
                continue
            next_jobs.append(
                {
                    **dict(item),
                    "status": "succeeded",
                    "variant_id": variant_id,
                    "artifact_ref": artifact_ref,
                    "video_url": video_url,
                    "completed_at": str(result.get("completed_at") or now.isoformat()),
                }
            )
            changed = True
        variants = [item for item in scene.get("variants", []) if isinstance(item, Mapping)]
        if not any(str(item.get("variant_id") or "") == variant_id for item in variants):
            variants.append(
                {
                    "variant_id": variant_id,
                    "artifact_ref": artifact_ref,
                    "video_url": video_url,
                    "selected": len(next_jobs) == 1,
                    "review_status": "approved" if len(next_jobs) == 1 else "pending",
                    "completed_at": str(result.get("completed_at") or now.isoformat()),
                    "source_job_id": job_id,
                }
            )
            changed = True
        updated_job = next(
            item
            for item in next_jobs
            if isinstance(item, Mapping) and str(item.get("job_id") or "") == job_id
        )
        step_id = str(updated_job.get("plan_step_id") or "").strip()
        cohort = [
            item
            for item in next_jobs
            if isinstance(item, Mapping)
            and (
                (step_id and str(item.get("plan_step_id") or "") == step_id)
                or (not step_id and str(item.get("job_id") or "") == job_id)
            )
        ]
        all_succeeded = bool(cohort) and all(item.get("status") == "succeeded" for item in cohort)
        updated = {
            **dict(scene),
            "generation_jobs": next_jobs,
            "variants": variants,
            "edit_status": "重新生成完成" if all_succeeded else "等待版本审核",
        }
        if all_succeeded and len(cohort) == 1:
            updated["approved_variant_id"] = variant_id
            updated["video_url"] = video_url
        target_scene = updated
        next_scenes.append(updated)
    if not changed or target_scene is None:
        return None
    target_id = str(target_scene.get("scene_id") or "")
    dirty = [
        str(item)
        for item in payload.get("dirty_scene_ids", [])
        if str(item) != target_id
    ] if isinstance(payload.get("dirty_scene_ids"), list) and target_scene.get("edit_status") == "重新生成完成" else list(payload.get("dirty_scene_ids", []))
    total = sum(
        len(scene.get("generation_jobs", []))
        for scene in next_scenes
        if isinstance(scene, Mapping) and isinstance(scene.get("generation_jobs"), list)
    )
    completed = sum(
        1
        for scene in next_scenes
        if isinstance(scene, Mapping)
        for item in scene.get("generation_jobs", [])
        if isinstance(item, Mapping) and item.get("status") not in {"queued", "starting", "polling"}
    )
    return {
        "scenes": [target_scene],
        "scene_packages": [target_scene],
        "dirty_scene_ids": dirty,
        "scene_video_progress": {
            "completed": completed,
            "total": total,
            "scene_id": target_id,
            "scene_index": target_scene.get("scene_index"),
            "ok": completed >= total and total > 0,
        },
    }


def build_scene_generation_failure_patch(payload, *, job_id: str, status: str, reason_code: str | None, now: datetime):
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return None
    changed = False
    next_scenes = []
    for scene in scenes:
        if not isinstance(scene, Mapping):
            next_scenes.append(scene)
            continue
        jobs = scene.get("generation_jobs")
        if not isinstance(jobs, list):
            next_scenes.append(scene)
            continue
        next_jobs = []
        matched = False
        for item in jobs:
            if not isinstance(item, Mapping) or str(item.get("job_id") or "") != job_id:
                next_jobs.append(item)
                continue
            matched = True
            changed = True
            next_jobs.append(
                {
                    **dict(item),
                    "status": status,
                    "error": "生成任务执行失败",
                    "reason_code": reason_code or "provider_failed",
                    "completed_at": now.isoformat(),
                }
            )
        if not matched:
            next_scenes.append(scene)
            continue
        next_scenes.append({**dict(scene), "generation_jobs": next_jobs, "edit_status": "重新生成失败"})
    if not changed:
        return None
    return {
        "scenes": [scene for scene in next_scenes if isinstance(scene, Mapping) and any(
            isinstance(item, Mapping) and str(item.get("job_id") or "") == job_id
            for item in item_list(scene.get("generation_jobs"))
        )],
        "scene_packages": [scene for scene in next_scenes if isinstance(scene, Mapping) and any(
            isinstance(item, Mapping) and str(item.get("job_id") or "") == job_id
            for item in item_list(scene.get("generation_jobs"))
        )],
    }


def item_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def count_polling_scene_generation_jobs(payload, *, plan_step_id: str | None = None) -> int:
    """统计 Workspace 中仍在等待 Provider 的视频任务。"""

    total = 0
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return total
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        for job in item_list(scene.get("generation_jobs")):
            if not isinstance(job, Mapping):
                continue
            if plan_step_id and job.get("plan_step_id") not in {None, plan_step_id}:
                continue
            if job.get("status") in {"queued", "starting", "polling"}:
                total += 1
    return total


__all__ = [
    "build_image_asset_failure_patch",
    "build_image_asset_success_patch",
    "build_scene_generation_failure_patch",
    "build_scene_generation_success_patch",
    "count_polling_scene_generation_jobs",
]
