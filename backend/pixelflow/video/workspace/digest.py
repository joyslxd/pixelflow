"""为原生 Agent 上下文构造可公开的 Workspace / Operation 摘要（不含密钥与原文长文）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pixelflow.agent_control_plane.contracts.enums import ExternalJobStatus
from pixelflow.agent_control_plane.persistence.repositories import OperationRecord
from pixelflow.video.contracts import AgentPlan, PlanStepStatus, VideoWorkspace
from pixelflow.video.services.production_fields import (
    workspace_has_ending_cta,
    workspace_resolved_aspect_ratio,
)


def _asset_has_image_url(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False
    for key in ("image_url", "url", "generation_reference_url"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip().startswith(("http://", "https://", "asset://")):
            return True
    for key in ("images", "three_view_images"):
        images = item.get(key)
        if isinstance(images, list):
            for image in images:
                if isinstance(image, str) and image.strip().startswith(("http://", "https://", "asset://")):
                    return True
                if isinstance(image, Mapping):
                    url = image.get("url") or image.get("image_url")
                    if isinstance(url, str) and url.strip().startswith(("http://", "https://", "asset://")):
                        return True
    return False


def workspace_has_scene_asset_images(payload: Mapping[str, Any] | None) -> bool:
    """全局资产是否已有至少一张参考图 URL。"""

    if not isinstance(payload, Mapping):
        return False
    global_assets = _as_mapping(payload.get("global_assets")) or {}
    for bucket in ("characters", "scenes", "props"):
        for item in _as_list(global_assets.get(bucket)):
            if _asset_has_image_url(item):
                return True
    return False


# 与前端 SCENE_ASSET_PREFERRED_MODELS / Borgrise 生图入口对齐；禁止向用户推荐未注册模型。
REGISTERED_SCENE_ASSET_IMAGE_MODELS: tuple[dict[str, str], ...] = (
    {"id": "gpt-image-2", "label": "image-2"},
    {"id": "seeddream-5.0", "label": "Seedream 5.0"},
)

_TERMINAL_OPERATION_STATUSES = {
    ExternalJobStatus.SUCCEEDED,
    ExternalJobStatus.FAILED,
    ExternalJobStatus.TIMEOUT,
    ExternalJobStatus.EXPIRED,
}

_SECRET_KEY_FRAGMENTS = (
    "credential",
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _safe_len(value: Any) -> int:
    if isinstance(value, str):
        return len(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return 0


def _count_named_items(value: Any) -> int:
    items = _as_list(value)
    count = 0
    for item in items:
        if isinstance(item, Mapping) and str(item.get("name") or "").strip():
            count += 1
        elif isinstance(item, str) and item.strip():
            count += 1
    return count


def _asset_summaries(value: object, *, limit: int = 12) -> list[dict[str, str]]:
    """提取全局素材的安全展示字段，不向浏览器发送 URL、提示词或 Provider 原始结果。"""

    result: list[dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, Mapping):
            continue
        asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
        name = str(item.get("name") or item.get("title") or "").strip()
        if not asset_id and not name:
            continue
        result.append({"asset_id": asset_id[:64], "name": name[:120] or "未命名素材"})
        if len(result) >= limit:
            break
    return result


def _scene_summaries(scenes: list[Any], states: object) -> list[dict[str, Any]]:
    """将场景状态压缩为看板所需字段，避免把镜头提示词和媒体 URL 放入 Snapshot。"""

    states_by_id = {
        str(item.get("scene_id") or ""): item
        for item in _as_list(states)
        if isinstance(item, Mapping) and str(item.get("scene_id") or "")
    }
    result: list[dict[str, Any]] = []
    for scene in scenes[:24]:
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue
        state = states_by_id.get(scene_id, {})
        index = scene.get("scene_index")
        result.append(
            {
                "scene_id": scene_id[:64],
                "scene_index": index if isinstance(index, int) and not isinstance(index, bool) else None,
                "title": str(scene.get("title") or scene.get("name") or "未命名分镜")[:160],
                "state": str(state.get("state") or "idle")[:32],
            }
        )
    return result


def summarize_scene_asset_status(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """参考图完整度摘要：区分任意图已生成与全部生成完成。"""

    if not isinstance(payload, Mapping):
        payload = {}
    global_assets = _as_mapping(payload.get("global_assets")) or {}
    asset_types = {
        "characters": "character",
        "scenes": "scene_image",
        "props": "prop_image",
    }
    targets: list[tuple[dict[str, str], bool]] = []
    for bucket, asset_type in asset_types.items():
        for item in _as_list(global_assets.get(bucket)):
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            target = {"asset_id": asset_id, "asset_type": asset_type}
            targets.append((target, _asset_has_image_url(item)))

    missing_targets = [target for target, ready in targets if not ready]
    missing_keys = {
        (target["asset_type"], target["asset_id"])
        for target in missing_targets
        if target["asset_id"]
    }
    failed_keys: set[tuple[str, str]] = set()
    for failure in _as_list(payload.get("scene_asset_failures")):
        if not isinstance(failure, Mapping):
            continue
        key = (
            str(failure.get("asset_type") or "").strip(),
            str(failure.get("asset_id") or "").strip(),
        )
        if key in missing_keys:
            failed_keys.add(key)

    required_count = len(targets)
    ready_count = sum(1 for _target, ready in targets if ready)
    missing_count = required_count - ready_count
    scene_asset_job = _as_mapping(payload.get("scene_asset_job")) or {}
    job_status = str(scene_asset_job.get("status") or "").strip().casefold()
    is_running = job_status in {
        "created",
        "pending",
        "polling",
        "queued",
        "running",
        "start_paused_quota",
    }
    if required_count == 0:
        status = "empty"
    elif missing_count == 0:
        status = "ready"
    elif is_running:
        status = "running"
    elif ready_count > 0:
        status = "partial"
    elif failed_keys:
        status = "failed"
    else:
        status = "empty"

    return {
        "scene_asset_status": status,
        "scene_asset_required_count": required_count,
        "scene_asset_ready_count": ready_count,
        "scene_asset_missing_count": missing_count,
        "scene_asset_failed_count": len(failed_keys),
        "scene_assets_ready": status == "ready",
        "scene_asset_missing_targets": [
            target for target in missing_targets if target["asset_id"]
        ][:64],
    }


def _scene_has_video_url(scene: Mapping[str, Any]) -> bool:
    if str(scene.get("video_url") or "").strip().lower().startswith(("http://", "https://")):
        return True
    for variant in _as_list(scene.get("variants")):
        if not isinstance(variant, Mapping):
            continue
        if str(variant.get("video_url") or "").strip().lower().startswith(("http://", "https://")):
            return True
    return False


def _job_status_bucket(status: str) -> str:
    normalized = status.strip().casefold()
    if normalized in {"succeeded", "success", "completed"}:
        return "succeeded"
    if normalized in {"failed", "timeout", "expired", "error"}:
        return "failed"
    if normalized in {"polling", "created", "running", "start_paused_quota"}:
        return "polling"
    return "other"


def summarize_scene_video_status(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """分镜视频进度公开摘要：供 digest / inspect 共用，不含 URL 原文。"""

    if not isinstance(payload, Mapping):
        return {
            "scene_count": 0,
            "scene_videos_ready_count": 0,
            "scene_videos_polling_count": 0,
            "scene_videos_failed_count": 0,
            "scene_videos_idle_count": 0,
        }
    scenes = _as_list(payload.get("scenes") or payload.get("scene_packages"))
    ready = 0
    polling = 0
    failed = 0
    idle = 0
    per_scene: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue
        has_video = _scene_has_video_url(scene)
        jobs = _as_list(scene.get("generation_jobs"))
        job_buckets = [
            _job_status_bucket(str(job.get("status") or ""))
            for job in jobs
            if isinstance(job, Mapping)
        ]
        if has_video:
            ready += 1
            state = "ready"
        elif any(bucket == "failed" for bucket in job_buckets) and not any(
            bucket == "polling" for bucket in job_buckets
        ):
            failed += 1
            state = "failed"
        elif any(bucket == "polling" for bucket in job_buckets) or str(
            scene.get("edit_status") or ""
        ).strip() == "重新生成中":
            polling += 1
            state = "polling"
        else:
            idle += 1
            state = "idle"
        if len(per_scene) < 24:
            entry: dict[str, Any] = {
                "scene_id": scene_id,
                "state": state,
            }
            index = scene.get("scene_index")
            if isinstance(index, int) and not isinstance(index, bool):
                entry["scene_index"] = index
            per_scene.append(entry)

    progress = _as_mapping(payload.get("scene_video_progress")) or {}
    completed = progress.get("completed")
    total = progress.get("total")
    result: dict[str, Any] = {
        "scene_count": len(scenes),
        "scene_videos_ready_count": ready,
        "scene_videos_polling_count": polling,
        "scene_videos_failed_count": failed,
        "scene_videos_idle_count": idle,
        "scene_video_states": per_scene or None,
    }
    if isinstance(completed, int) and not isinstance(completed, bool) and completed >= 0:
        result["scene_video_progress_completed"] = completed
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        result["scene_video_progress_total"] = total
    return {key: value for key, value in result.items() if value is not None}


def build_workspace_digest(workspace: VideoWorkspace) -> dict[str, Any]:
    """从 VideoWorkspace 抽取规划用公开摘要。"""

    payload = workspace.payload if isinstance(workspace.payload, dict) else {}
    script = _as_mapping(payload.get("script")) or {}
    script_content = str(script.get("content") or "").strip()
    pipeline = _as_mapping(payload.get("script_pipeline")) or {}
    pipeline_stages = sorted(
        str(key)
        for key, item in pipeline.items()
        if isinstance(item, Mapping) and str(item.get("content") or item.get("stage") or "").strip()
    )
    global_assets = _as_mapping(payload.get("global_assets")) or {}
    scenes = _as_list(payload.get("scenes") or payload.get("scene_packages"))
    dirty = payload.get("dirty_scene_ids")
    dirty_ids = [str(item) for item in dirty] if isinstance(dirty, list) else []
    qc = _as_mapping(payload.get("qc")) or {}
    quota_interrupt = _as_mapping(payload.get("quota_interrupt")) or {}
    product = _as_mapping(payload.get("product_info")) or {}
    safe_product = {
        key: value
        for key, value in product.items()
        if not any(fragment in str(key).lower() for fragment in _SECRET_KEY_FRAGMENTS)
        and key in {"name", "category", "brand"}
    }
    resolved_ratio = workspace_resolved_aspect_ratio(payload)
    asset_status = summarize_scene_asset_status(payload)
    video_status = summarize_scene_video_status(payload)
    scene_summaries = _scene_summaries(scenes, video_status.get("scene_video_states"))
    return {
        key: value
        for key, value in {
            "workspace_id": workspace.workspace_id,
            "revision": workspace.revision,
            "has_script": bool(script_content) or bool(pipeline_stages),
            "script_status": str(script.get("status") or "") or None,
            "script_source": str(script.get("source") or "") or None,
            "script_version": script.get("version"),
            "script_chars": _safe_len(script_content),
            "script_preview": script_content[:1_200] or None,
            # 前端脚本编辑走同一 Workspace Command；长度与 Tool 输入合同一致。
            "script_editor_content": script_content[:8_000] or None,
            "script_pipeline_stages": pipeline_stages,
            "script_entry_path": str(payload.get("script_entry_path") or "") or None,
            "script_plan_confirmed": bool(payload.get("script_plan_confirmed")),
            "script_plan_confirmed_version": payload.get(
                "script_plan_confirmed_version"
            ),
            "awaiting_production_fields": bool(payload.get("awaiting_production_fields")),
            "has_aspect_ratio": resolved_ratio is not None,
            "video_ratio": resolved_ratio,
            "has_ending_cta": workspace_has_ending_cta(payload),
            "script_missing_requirements": [
                str(item).strip()
                for item in (_as_list(script.get("missing_requirements")))
                if str(item).strip()
            ][:8]
            or None,
            "character_count": _count_named_items(global_assets.get("characters")),
            "scene_asset_count": _count_named_items(global_assets.get("scenes")),
            "prop_count": _count_named_items(global_assets.get("props")),
            "character_summaries": _asset_summaries(global_assets.get("characters")) or None,
            "scene_asset_summaries": _asset_summaries(global_assets.get("scenes")) or None,
            "prop_summaries": _asset_summaries(global_assets.get("props")) or None,
            "scene_count": len(scenes),
            "dirty_scene_ids": dirty_ids[:32],
            "dirty_scene_count": len(dirty_ids),
            "qc_status": str(qc.get("status") or qc.get("verdict") or "") or None,
            "qc_issue_count": _safe_len(qc.get("issues") or qc.get("findings")),
            "pending_confirmations": bool(payload.get("pending_confirmations")),
            "quota_interrupt_id": str(quota_interrupt.get("quota_interrupt_id") or "") or None,
            "quota_interrupt_state": str(quota_interrupt.get("state") or "") or None,
            "quota_interrupt_reason_code": str(quota_interrupt.get("reason_code") or "") or None,
            "failed_scene_asset_count": _safe_len(payload.get("scene_asset_failures")),
            "has_scene_packages": bool(scenes),
            "has_scene_asset_images": workspace_has_scene_asset_images(payload),
            **asset_status,
            "registered_scene_asset_image_models": [
                dict(item) for item in REGISTERED_SCENE_ASSET_IMAGE_MODELS
            ],
            "product_info": safe_product or None,
            "latest_input_chars": _safe_len(payload.get("latest_input")),
            "has_materials": bool(_as_list(payload.get("materials"))),
            **video_status,
            "scene_summaries": scene_summaries or None,
        }.items()
        if value is not None
    }


def build_plan_digest(plan: AgentPlan | None) -> dict[str, Any] | None:
    """将最新计划投影为只读看板数据，不暴露 Tool 参数、原始 Provider 结果或内部事件。"""

    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "status": plan.status.value,
        "goal": (plan.public_goal or "")[:800] or None,
        "steps": [
            {
                "step_id": step.step_id,
                "sequence": step.sequence,
                "title": step.title,
                "status": step.status.value,
                "summary": (step.public_summary or "")[:400] or None,
                "confirmation_required": step.confirmation_required,
            }
            for step in plan.steps[:20]
        ],
    }


def summarize_operations(operations: Sequence[OperationRecord]) -> list[dict[str, Any]]:
    """只暴露未完成 Operation 的安全字段。"""

    summaries: list[dict[str, Any]] = []
    for operation in operations:
        if operation.status in _TERMINAL_OPERATION_STATUSES:
            continue
        summaries.append(
            {
                "job_id": operation.job_id,
                "stage": operation.stage,
                "status": operation.status.value,
                "attempt": operation.attempt,
                "provider_job_id": operation.provider_job_id,
            }
        )
        if len(summaries) >= 20:
            break
    return summaries


def blocking_confirmation_from_plan(plan: AgentPlan | None) -> dict[str, Any] | None:
    """若最新计划卡在确认闸门，返回公开摘要。"""

    if plan is None or not plan.steps:
        return None
    for step in plan.steps:
        if step.status is PlanStepStatus.AWAITING_CONFIRMATION:
            return {
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "tool_name": step.tool_name,
                "title": step.title,
            }
    if plan.status.value == "awaiting_confirmation":
        waiting = next(
            (step for step in plan.steps if step.confirmation_required),
            plan.steps[0],
        )
        return {
            "plan_id": plan.plan_id,
            "step_id": waiting.step_id,
            "tool_name": waiting.tool_name,
            "title": waiting.title,
        }
    return None
