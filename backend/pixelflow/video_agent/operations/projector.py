"""Operation 完成结果投影回 VideoWorkspace。

Turn 入口不应顺便做数据修复；由本模块在规划前按需对账，
或供 recovery 扫描显式调用。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from pixelflow.agent_runtime.contracts import ExternalJobStatus
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.workspace.repository import VideoAgentRepository

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
        video_repository: VideoAgentRepository,
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


__all__ = [
    "ScenePackageCompletionProjector",
    "scene_package_result_from_events",
    "workspace_has_scene_packages",
]
