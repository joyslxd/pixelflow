"""把外部 Operation 完成结果投影回视频领域工作区。

Turn 入口不应顺便做数据修复；由本模块在规划前按需对账，
或供 recovery 扫描显式调用。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from pixelflow.agent_control_plane.contracts import ExternalJobStatus
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRepository
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace.repository import VideoWorkspaceRepository

logger = logging.getLogger(__name__)


def workspace_has_scene_packages(workspace: VideoWorkspace) -> bool:
    payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
    scenes = payload.get("scene_packages") or payload.get("scenes")
    return isinstance(scenes, list) and len(scenes) > 0


def scene_package_result_from_events(
    events: Sequence[Any],
    *,
    job_id: str | None = None,
) -> Mapping[str, Any] | None:
    """从 Operation 完成事件载荷里取 scene_packages / global_assets。

    job_id 为空时，回落匹配最近一次 prepare_scene_packages 成功结果
    （executor 写回冲突导致 scene_package_job 未落库时的恢复路径）。
    """

    for event in reversed(tuple(events)):
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        if job_id:
            if str(payload.get("job_id") or "").strip() != job_id:
                continue
        else:
            stage = str(payload.get("stage") or "")
            if not stage.startswith("prepare_scene_packages:"):
                continue
            status = str(payload.get("status") or "")
            if status and status != ExternalJobStatus.SUCCEEDED.value:
                continue
        result = payload.get("result")
        if not isinstance(result, Mapping):
            continue
        packages = result.get("scene_packages")
        assets = result.get("global_assets")
        if isinstance(packages, list) and packages:
            return result
        if isinstance(assets, Mapping) and assets:
            return result
    return None


class ScenePackageCompletionProjector:
    """把 prepare_scene_packages 完成事件回填到 Workspace。"""

    def __init__(
        self,
        *,
        runtime_repository: AgentRuntimeRepository,
        video_repository: VideoWorkspaceRepository,
        apply_patch: Callable[..., Any],
    ) -> None:
        self._runtime_repository = runtime_repository
        self._video_repository = video_repository
        self._apply_patch = apply_patch

    async def hydrate_if_missing(
        self,
        *,
        owner: str,
        conversation_id: str,
        workspace: VideoWorkspace,
        occurred_at: datetime,
    ) -> VideoWorkspace:
        if workspace_has_scene_packages(workspace):
            return workspace
        job = workspace.payload.get("scene_package_job")
        job_id = ""
        if isinstance(job, Mapping):
            job_id = str(job.get("job_id") or "").strip()
        try:
            events = await asyncio.wait_for(
                self._runtime_repository.list_events(owner, conversation_id),
                timeout=5.0,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "回填资产包时 list_events 失败 conversation_id=%s",
                conversation_id,
            )
            return workspace
        result = scene_package_result_from_events(
            events,
            job_id=job_id or None,
        )
        if result is None:
            return workspace
        packages = result.get("scene_packages")
        assets = result.get("global_assets")
        patch: dict[str, Any] = {}
        if isinstance(job, Mapping) and job_id:
            patch["scene_package_job"] = {
                **dict(job),
                "status": "succeeded",
            }
        else:
            resolved_job_id = job_id or str(result.get("job_id") or "").strip()
            if resolved_job_id:
                patch["scene_package_job"] = {
                    "job_id": resolved_job_id,
                    "status": "succeeded",
                }
        if isinstance(packages, list) and packages:
            patch["scene_packages"] = list(packages)
            patch["scenes"] = list(packages)
            # 回填路径也要落稳定摘要，避免再次确认误判为必须重拆；摘要只覆盖
            # 已持久化工作区 JSON，不再依赖旧创意工作流模块。
            patch["scene_packages_source_digest"] = "sha256:" + hashlib.sha256(
                repr(sorted(workspace.payload.items())).encode("utf-8")
            ).hexdigest()
        if isinstance(assets, Mapping) and assets:
            patch["global_assets"] = dict(assets)
        contract = result.get("creation_contract")
        if isinstance(contract, Mapping):
            patch["creation_contract"] = dict(contract)
        if not patch:
            return workspace
        logger.info(
            "从 Operation 完成事件回填 scene_packages job_id=%s packages=%s",
            job_id or "(stage-fallback)",
            len(packages) if isinstance(packages, list) else 0,
        )
        return await self._apply_patch(
            owner=owner,
            workspace=workspace,
            patch=patch,
            now=occurred_at,
        )


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def count_polling_scene_generation_jobs(
    payload: Mapping[str, Any],
    *,
    plan_step_id: str | None = None,
) -> int:
    """统计仍处于 polling / start_paused_quota 的分镜生成 Job。"""

    total = 0
    for scene in _as_list(payload.get("scenes")):
        scene_map = _as_mapping(scene)
        if scene_map is None:
            continue
        for job in _as_list(scene_map.get("generation_jobs")):
            job_map = _as_mapping(job)
            if job_map is None:
                continue
            if plan_step_id:
                step = str(job_map.get("plan_step_id") or "").strip()
                if step and step != plan_step_id:
                    continue
            status = str(job_map.get("status") or "").strip().casefold()
            if status in {"polling", "start_paused_quota"}:
                total += 1
    return total


def _parse_generate_scene_stage(stage: str | None) -> tuple[str | None, int | None]:
    """解析 generate_scene:{digest}:v{index} → (digest, variant_index)。"""

    text = str(stage or "").strip()
    if not text.startswith("generate_scene:"):
        return None, None
    parts = text.split(":")
    if len(parts) < 3:
        return None, None
    digest = parts[1].strip() or None
    variant_token = parts[2].strip()
    variant_index: int | None = None
    if variant_token.startswith("v") and variant_token[1:].isdigit():
        variant_index = int(variant_token[1:])
    return digest, variant_index


def _scene_digest(scene_id: str) -> str:
    return hashlib.sha256(scene_id.encode("utf-8")).hexdigest()[:12]


def build_scene_generation_success_patch(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    result: Mapping[str, Any],
    now: datetime,
    stage: str | None = None,
    plan_step_id: str | None = None,
) -> dict[str, Any] | None:
    """把单镜 Operation 成功结果写回 scenes.generation_jobs / variants。

    优先按 job_id 匹配；若 Workspace 尚未写入 generation_jobs（旧冲突批次），
    再按 stage 中的 scene digest 落到对应分镜，保证前端能拿到 video_url。
    """

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return None
    video_url = str(result.get("video_url") or "").strip()
    variant_id = str(result.get("variant_id") or "").strip()
    artifact_ref = str(result.get("artifact_ref") or "").strip()
    if not video_url or not variant_id or not artifact_ref:
        return None
    completed_at = str(result.get("completed_at") or "").strip() or now.isoformat()
    stage_digest, stage_variant_index = _parse_generate_scene_stage(stage)

    scenes = _as_list(payload.get("scenes"))
    if not scenes:
        return None

    next_scenes: list[Any] = []
    changed = False
    matched_scene_id = ""
    matched_scene_index: int | None = None
    for scene in scenes:
        scene_map = _as_mapping(scene)
        if scene_map is None:
            next_scenes.append(scene)
            continue
        scene_id = str(scene_map.get("scene_id") or "").strip()
        jobs = _as_list(scene_map.get("generation_jobs"))
        matched_job: Mapping[str, Any] | None = None
        next_jobs: list[Any] = []
        for job in jobs:
            job_map = _as_mapping(job)
            if job_map is None:
                next_jobs.append(job)
                continue
            if str(job_map.get("job_id") or "").strip() != normalized_job_id:
                next_jobs.append(dict(job_map))
                continue
            matched_job = job_map
            if (
                str(job_map.get("status") or "").strip().casefold() == "succeeded"
                and str(job_map.get("video_url") or "").strip() == video_url
            ):
                next_jobs.append(dict(job_map))
            else:
                next_jobs.append(
                    {
                        **dict(job_map),
                        "status": "succeeded",
                        "variant_id": variant_id,
                        "artifact_ref": artifact_ref,
                        "video_url": video_url,
                        "completed_at": completed_at,
                    }
                )
                changed = True

        digest_hit = (
            matched_job is None
            and bool(scene_id)
            and stage_digest is not None
            and _scene_digest(scene_id) == stage_digest
        )
        if matched_job is None and not digest_hit:
            next_scenes.append(dict(scene_map))
            continue

        if digest_hit:
            # 旧批次：generation_jobs 缺 job_id，按 digest 补一条成功记录。
            next_jobs = [
                *[
                    dict(item)
                    for item in jobs
                    if isinstance(item, Mapping)
                    and str(item.get("job_id") or "").strip() != normalized_job_id
                ],
                {
                    "job_id": normalized_job_id,
                    "scene_id": scene_id,
                    "variant_index": stage_variant_index or 1,
                    "status": "succeeded",
                    "variant_id": variant_id,
                    "artifact_ref": artifact_ref,
                    "video_url": video_url,
                    "completed_at": completed_at,
                    **(
                        {"plan_step_id": plan_step_id}
                        if plan_step_id
                        else {}
                    ),
                },
            ]
            changed = True

        matched_scene_id = scene_id
        scene_index = scene_map.get("scene_index")
        if isinstance(scene_index, int) and not isinstance(scene_index, bool):
            matched_scene_index = scene_index

        variants = [
            dict(item)
            for item in _as_list(scene_map.get("variants"))
            if isinstance(item, Mapping)
        ]
        if not any(str(item.get("variant_id") or "") == variant_id for item in variants):
            auto_select = len(next_jobs) <= 1 or all(
                isinstance(job, Mapping)
                and str(job.get("status") or "").strip().casefold() == "succeeded"
                for job in next_jobs
            )
            variants.append(
                {
                    "variant_id": variant_id,
                    "artifact_ref": artifact_ref,
                    "video_url": video_url,
                    "review_status": "approved" if auto_select else "pending",
                    "selected": auto_select,
                    "completed_at": completed_at,
                    "source_job_id": normalized_job_id,
                }
            )
            changed = True
        else:
            # 已有同 variant_id 但可能缺 URL（旧脏数据）。
            repaired: list[dict[str, Any]] = []
            for item in variants:
                if str(item.get("variant_id") or "") != variant_id:
                    repaired.append(item)
                    continue
                if str(item.get("video_url") or "").strip() == video_url:
                    repaired.append(item)
                    continue
                repaired.append(
                    {
                        **item,
                        "video_url": video_url,
                        "artifact_ref": artifact_ref or item.get("artifact_ref"),
                        "completed_at": completed_at,
                        "source_job_id": normalized_job_id,
                        "selected": True,
                        "review_status": "approved",
                    }
                )
                changed = True
            variants = repaired

        all_succeeded = bool(next_jobs) and all(
            isinstance(job, Mapping)
            and str(job.get("status") or "").strip().casefold() == "succeeded"
            for job in next_jobs
        )
        updated: dict[str, Any] = {
            **dict(scene_map),
            "generation_jobs": next_jobs,
            "variants": variants,
        }
        if all_succeeded and len(next_jobs) == 1:
            updated["edit_status"] = "重新生成完成"
            updated["approved_variant_id"] = variant_id
            updated["regenerated_at"] = completed_at
            updated["video_url"] = video_url
        elif all_succeeded:
            updated["edit_status"] = "等待版本审核"
            if not updated.get("video_url"):
                updated["video_url"] = video_url
        else:
            updated["edit_status"] = "重新生成中"
        next_scenes.append(updated)

    if not changed:
        return None

    dirty = [
        str(item).strip()
        for item in _as_list(payload.get("dirty_scene_ids"))
        if str(item).strip()
    ]
    if matched_scene_id and any(
        isinstance(scene, Mapping)
        and str(scene.get("scene_id") or "") == matched_scene_id
        and str(scene.get("edit_status") or "") == "重新生成完成"
        for scene in next_scenes
    ):
        dirty = [item for item in dirty if item != matched_scene_id]

    completed = 0
    total = 0
    for scene in next_scenes:
        scene_map = _as_mapping(scene)
        if scene_map is None:
            continue
        jobs = _as_list(scene_map.get("generation_jobs"))
        if not jobs:
            # 无 job 记录但已有 variant URL 也计入进度。
            variants = _as_list(scene_map.get("variants"))
            if any(
                isinstance(item, Mapping) and str(item.get("video_url") or "").strip()
                for item in variants
            ):
                total += 1
                completed += 1
            continue
        for job in jobs:
            job_map = _as_mapping(job)
            if job_map is None:
                continue
            total += 1
            status = str(job_map.get("status") or "").strip().casefold()
            if status and status not in {"polling", "start_paused_quota", "created"}:
                completed += 1

    changed_scenes = [
        scene
        for scene in next_scenes
        if isinstance(scene, Mapping)
        and str(scene.get("scene_id") or "").strip() == matched_scene_id
    ]
    return {
        # 只写本镜，repository 按 scene_id 合并，避免并发生成互相覆盖。
        "scenes": changed_scenes,
        "scene_packages": changed_scenes,
        "dirty_scene_ids": dirty,
        "scene_video_progress": {
            "completed": completed,
            "total": total,
            "scene_id": matched_scene_id or None,
            "scene_index": matched_scene_index,
            "ok": True,
        },
    }


_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "timeout", "expired"})


def build_scene_generation_failure_patch(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    status: str,
    reason_code: str | None = None,
    message: str | None = None,
    now: datetime,
    stage: str | None = None,
    plan_step_id: str | None = None,
) -> dict[str, Any] | None:
    """把单镜 Operation 失败结果写回 generation_jobs，供前端展示失败镜与原因。"""

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return None
    normalized_status = str(status or "").strip().casefold() or "failed"
    if normalized_status not in _TERMINAL_FAILURE_STATUSES:
        normalized_status = "failed"
    error_text = (
        str(message or "").strip()
        or str(reason_code or "").strip()
        or "供应商任务执行失败"
    )[:2_000]
    reason = str(reason_code or "").strip() or f"provider_{normalized_status}"
    completed_at = now.isoformat()
    stage_digest, stage_variant_index = _parse_generate_scene_stage(stage)

    scenes = _as_list(payload.get("scenes"))
    if not scenes:
        return None

    next_scenes: list[Any] = []
    changed = False
    matched_scene_id = ""
    matched_scene_index: int | None = None
    for scene in scenes:
        scene_map = _as_mapping(scene)
        if scene_map is None:
            next_scenes.append(scene)
            continue
        scene_id = str(scene_map.get("scene_id") or "").strip()
        jobs = _as_list(scene_map.get("generation_jobs"))
        matched_job: Mapping[str, Any] | None = None
        next_jobs: list[Any] = []
        for job in jobs:
            job_map = _as_mapping(job)
            if job_map is None:
                next_jobs.append(job)
                continue
            if str(job_map.get("job_id") or "").strip() != normalized_job_id:
                next_jobs.append(dict(job_map))
                continue
            matched_job = job_map
            current_status = str(job_map.get("status") or "").strip().casefold()
            if (
                current_status == normalized_status
                and str(job_map.get("error") or "").strip() == error_text
            ):
                next_jobs.append(dict(job_map))
            else:
                next_jobs.append(
                    {
                        **dict(job_map),
                        "status": normalized_status,
                        "error": error_text,
                        "reason_code": reason,
                        "completed_at": completed_at,
                    }
                )
                changed = True

        digest_hit = (
            matched_job is None
            and bool(scene_id)
            and stage_digest is not None
            and _scene_digest(scene_id) == stage_digest
        )
        if matched_job is None and not digest_hit:
            next_scenes.append(dict(scene_map))
            continue

        if digest_hit:
            next_jobs = [
                *[
                    dict(item)
                    for item in jobs
                    if isinstance(item, Mapping)
                    and str(item.get("job_id") or "").strip() != normalized_job_id
                ],
                {
                    "job_id": normalized_job_id,
                    "scene_id": scene_id,
                    "variant_index": stage_variant_index or 1,
                    "status": normalized_status,
                    "error": error_text,
                    "reason_code": reason,
                    "completed_at": completed_at,
                    **({"plan_step_id": plan_step_id} if plan_step_id else {}),
                },
            ]
            changed = True

        matched_scene_id = scene_id
        scene_index = scene_map.get("scene_index")
        if isinstance(scene_index, int) and not isinstance(scene_index, bool):
            matched_scene_index = scene_index

        has_polling = any(
            isinstance(job, Mapping)
            and str(job.get("status") or "").strip().casefold()
            in {"polling", "start_paused_quota", "created"}
            for job in next_jobs
        )
        has_success = any(
            isinstance(job, Mapping)
            and str(job.get("status") or "").strip().casefold() == "succeeded"
            for job in next_jobs
        )
        updated = {
            **dict(scene_map),
            "generation_jobs": next_jobs,
        }
        if has_polling:
            updated["edit_status"] = "重新生成中"
        elif has_success:
            updated["edit_status"] = "等待版本审核"
        else:
            updated["edit_status"] = "重新生成失败"
        next_scenes.append(updated)

    if not changed:
        return None

    completed = 0
    total = 0
    for scene in next_scenes:
        scene_map = _as_mapping(scene)
        if scene_map is None:
            continue
        jobs = _as_list(scene_map.get("generation_jobs"))
        for job in jobs:
            job_map = _as_mapping(job)
            if job_map is None:
                continue
            total += 1
            status_value = str(job_map.get("status") or "").strip().casefold()
            if status_value and status_value not in {"polling", "start_paused_quota", "created"}:
                completed += 1

    changed_scenes = [
        scene
        for scene in next_scenes
        if isinstance(scene, Mapping)
        and str(scene.get("scene_id") or "").strip() == matched_scene_id
    ]
    return {
        # 只写本镜，避免失败回写整表覆盖并发生成中的其它镜。
        "scenes": changed_scenes,
        "scene_packages": changed_scenes,
        "scene_video_progress": {
            "completed": completed,
            "total": total,
            "scene_id": matched_scene_id or None,
            "scene_index": matched_scene_index,
            "ok": completed >= total and total > 0,
        },
    }


def build_image_asset_success_patch(
    payload: Mapping[str, Any],
    *,
    asset_id: str,
    result: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    """把图片 Provider 成功结果回写资产注册表；只更新匹配资产。"""

    image_url = str(result.get("image_url") or "").strip()
    artifact_ref = str(result.get("artifact_ref") or "").strip()
    if not image_url.startswith("https://") or not artifact_ref.startswith("artifact:"):
        return None
    assets = _as_list(payload.get("asset_registry"))
    changed = False
    next_assets: list[Any] = []
    for item in assets:
        if not isinstance(item, Mapping) or str(item.get("asset_id") or "").strip() != asset_id:
            next_assets.append(item)
            continue
        if item.get("origin") != "planned_generation":
            return None
        next_assets.append({**dict(item), "state": "ready", "usable_for_video": True, "provider_artifact_ref": artifact_ref, "image_url": image_url, "completed_at": now.isoformat()})
        changed = True
    return {"asset_registry": next_assets} if changed else None


def build_image_asset_failure_patch(
    payload: Mapping[str, Any],
    *,
    asset_id: str,
    status: str,
    reason_code: str | None,
    now: datetime,
) -> dict[str, Any] | None:
    """把图片 Provider 失败结果回写为不可用于视频的 failed。"""

    assets = _as_list(payload.get("asset_registry"))
    changed = False
    next_assets: list[Any] = []
    for item in assets:
        if not isinstance(item, Mapping) or str(item.get("asset_id") or "").strip() != asset_id:
            next_assets.append(item)
            continue
        if item.get("origin") != "planned_generation":
            return None
        next_assets.append({**dict(item), "state": "failed", "usable_for_video": False, "failure_status": status, "failure_reason_code": (reason_code or "provider_failed")[:128], "failed_at": now.isoformat()})
        changed = True
    return {"asset_registry": next_assets} if changed else None


__all__ = [
    "ScenePackageCompletionProjector",
    "build_scene_generation_success_patch",
    "build_scene_generation_failure_patch",
    "build_image_asset_success_patch",
    "build_image_asset_failure_patch",
    "count_polling_scene_generation_jobs",
    "scene_package_result_from_events",
    "workspace_has_scene_packages",
]
