"""为 Planner 构造可公开的 Workspace / Operation 摘要（不含密钥与原文长文）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pixelflow.agent_runtime.contracts.enums import ExternalJobStatus
from pixelflow.agent_runtime.persistence.repositories import OperationRecord
from pixelflow.video_agent.contracts import AgentPlan, PlanStepStatus, VideoWorkspace

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
    product = _as_mapping(payload.get("product_info")) or {}
    safe_product = {
        key: value
        for key, value in product.items()
        if not any(fragment in str(key).lower() for fragment in _SECRET_KEY_FRAGMENTS)
        and key in {"name", "category", "brand"}
    }
    return {
        key: value
        for key, value in {
            "workspace_id": workspace.workspace_id,
            "revision": workspace.revision,
            "has_script": bool(script_content) or bool(pipeline_stages),
            "script_status": str(script.get("status") or "") or None,
            "script_chars": _safe_len(script_content),
            "script_pipeline_stages": pipeline_stages,
            "script_entry_path": str(payload.get("script_entry_path") or "") or None,
            "script_plan_confirmed": bool(payload.get("script_plan_confirmed")),
            "character_count": _count_named_items(global_assets.get("characters")),
            "scene_asset_count": _count_named_items(global_assets.get("scenes")),
            "prop_count": _count_named_items(global_assets.get("props")),
            "scene_count": len(scenes),
            "dirty_scene_ids": dirty_ids[:32],
            "dirty_scene_count": len(dirty_ids),
            "qc_status": str(qc.get("status") or qc.get("verdict") or "") or None,
            "qc_issue_count": _safe_len(qc.get("issues") or qc.get("findings")),
            "pending_confirmations": bool(payload.get("pending_confirmations")),
            "failed_scene_asset_count": _safe_len(payload.get("scene_asset_failures")),
            "has_scene_packages": bool(scenes),
            "product_info": safe_product or None,
            "latest_input_chars": _safe_len(payload.get("latest_input")),
            "has_materials": bool(_as_list(payload.get("materials"))),
        }.items()
        if value is not None
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
