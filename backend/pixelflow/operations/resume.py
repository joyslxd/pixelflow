"""把 M06 完成与额度事件投影回视频领域权威状态。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from pixelflow.agent_control_plane.contracts import AgentEvent, ExternalJobStatus
from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.operations.jobs import (
    OperationQuotaEventPayload,
    OperationQuotaState,
)
from pixelflow.operations.namespace import (
    OperationExecutionNamespace,
    workflow_operation_namespace,
)
from pixelflow.video.adapters.operations.projector import (
    build_scene_generation_failure_patch,
    build_scene_generation_success_patch,
    count_polling_scene_generation_jobs,
)
from pixelflow.video.contracts import AgentPlanStatus, PlanStepStatus, VideoWorkspace
from pixelflow.video.services.tool_executor import VideoToolExecutor
from pixelflow.video.workspace.repository import VideoWorkspaceRepository

_FAILED_OPERATION_STATUSES = frozenset(
    {
        ExternalJobStatus.FAILED.value,
        ExternalJobStatus.TIMEOUT.value,
        ExternalJobStatus.EXPIRED.value,
    }
)
_WORKSPACE_PATCH_MAX_ATTEMPTS = 3


async def _apply_workspace_patch_resilient(
    repository: VideoWorkspaceRepository,
    *,
    user_id: str,
    workspace: VideoWorkspace,
    patch: Mapping[str, object],
    now: datetime,
) -> VideoWorkspace:
    """额度投影写回：冲突时按最新 revision 重试，避免 completion_dispatch 永久卡死。"""

    current = workspace
    last_error: AgentRuntimeRecordConflictError | None = None
    for _ in range(_WORKSPACE_PATCH_MAX_ATTEMPTS):
        try:
            return await repository.apply_workspace_patch(
                user_id,
                current.workspace_id,
                dict(patch),
                expected_revision=current.revision,
                now=now,
            )
        except AgentRuntimeRecordConflictError as exc:
            last_error = exc
            refreshed = await repository.get_workspace(user_id, current.workspace_id)
            if refreshed is None:
                raise
            current = refreshed
    assert last_error is not None
    raise last_error


class VideoOperationResumer:
    """用完成事件ID作为checkpoint，恢复原Plan或不重复唤醒原生 Agent。"""

    def __init__(
        self,
        *,
        repository: VideoWorkspaceRepository,
        executor: VideoToolExecutor,
        native_resume: object | None = None,
    ) -> None:
        self._repository = repository
        self._executor = executor
        # 用途：保留旧 Operation 恢复回调的稳定参数约定；影响：恢复时只回到原 job，不会创建新任务。
        self._native_resume = native_resume

    async def resume_external_job(
        self,
        namespace: OperationExecutionNamespace,
        *,
        user_id: str,
        conversation_id: str,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        """复核owner、Plan、运行步骤和Operation绑定后推进同一Plan。"""

        payload = completion_event.payload
        plan_id = payload.get("workflow_id")
        job_id = payload.get("job_id")
        stage = payload.get("stage")
        status = payload.get("status")
        if (
            not isinstance(plan_id, str)
            or not isinstance(job_id, str)
            or not isinstance(stage, str)
            or not isinstance(status, str)
            or idempotency_key != completion_event.event_id
            or completion_event.conversation_id != conversation_id
            or namespace != workflow_operation_namespace(conversation_id, plan_id)
        ):
            raise AgentRuntimeRecordConflictError(
                "视频事件身份不完整或namespace不匹配"
            )
        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None or plan.conversation_id != conversation_id:
            # 历史完成事件对应的 Plan 可能已被覆盖/清理；对场景包/生图/分镜终态直接 ack，
            # 避免 delivering 永久刷屏并饿死后续 polling。
            if _is_soft_ackable_scene_completion(stage=stage, status=status):
                return
            raise AgentRuntimeRecordConflictError(
                "视频事件不属于当前用户或会话"
            )
        workspace = await self._repository.get_workspace(
            user_id,
            plan.workspace_id,
        )
        if (
            workspace is None
            or workspace.conversation_id != conversation_id
        ):
            if _is_soft_ackable_scene_completion(stage=stage, status=status):
                return
            raise AgentRuntimeRecordConflictError(
                "视频事件未绑定原工作区Operation"
            )
        bound_step_ids = _operation_step_ids(workspace.payload, job_id)
        bound_step = None
        if len(bound_step_ids) == 1:
            bound_step = next(
                (
                    step
                    for step in plan.steps
                    if step.step_id == next(iter(bound_step_ids))
                ),
                None,
            )
            if bound_step is not None and not _stage_matches_tool(
                stage,
                bound_step.tool_name,
            ):
                bound_step = None

        # 分镜视频：即使 Workspace 尚未写入 generation_jobs（旧冲突批次），也按 stage digest 回填。
        if stage.startswith("generate_scene:"):
            if status == ExternalJobStatus.SUCCEEDED.value:
                result = payload.get("result")
                if isinstance(result, Mapping):
                    fallback_step = bound_step or next(
                        (
                            step
                            for step in plan.steps
                            if step.tool_name == "generate_scenes"
                        ),
                        None,
                    )
                    scene_patch = build_scene_generation_success_patch(
                        workspace.payload if isinstance(workspace.payload, Mapping) else {},
                        job_id=job_id,
                        result=result,
                        now=completion_event.occurred_at,
                        stage=stage,
                        plan_step_id=fallback_step.step_id if fallback_step else None,
                    )
                    if scene_patch is not None:
                        workspace = await _apply_workspace_patch_resilient(
                            self._repository,
                            user_id=user_id,
                            workspace=workspace,
                            patch=scene_patch,
                            now=completion_event.occurred_at,
                        )
            elif status in _FAILED_OPERATION_STATUSES:
                fallback_step = bound_step or next(
                    (
                        step
                        for step in plan.steps
                        if step.tool_name == "generate_scenes"
                    ),
                    None,
                )
                failure_patch = build_scene_generation_failure_patch(
                    workspace.payload if isinstance(workspace.payload, Mapping) else {},
                    job_id=job_id,
                    status=status,
                    reason_code=str(payload.get("reason_code") or "").strip() or None,
                    message=str(payload.get("message") or "").strip() or None,
                    now=completion_event.occurred_at,
                    stage=stage,
                    plan_step_id=fallback_step.step_id if fallback_step else None,
                )
                if failure_patch is not None:
                    workspace = await _apply_workspace_patch_resilient(
                        self._repository,
                        user_id=user_id,
                        workspace=workspace,
                        patch=failure_patch,
                        now=completion_event.occurred_at,
                    )
            remaining = count_polling_scene_generation_jobs(
                workspace.payload if isinstance(workspace.payload, Mapping) else {},
                plan_step_id=bound_step.step_id if bound_step else None,
            )
            if remaining > 0:
                return
            if bound_step is None or bound_step.status is not PlanStepStatus.RUNNING:
                # 已投影或无可唤醒步骤：吞掉事件，停止 completion_dispatch 僵尸刷屏。
                return
            if status in _FAILED_OPERATION_STATUSES:
                # 同批有成功镜时只投影失败并保留步骤，便于前端展示失败原因后局部重试。
                payload_map = (
                    workspace.payload if isinstance(workspace.payload, Mapping) else {}
                )
                has_success = False
                for scene in payload_map.get("scenes") or []:
                    if not isinstance(scene, Mapping):
                        continue
                    for job in scene.get("generation_jobs") or []:
                        if (
                            isinstance(job, Mapping)
                            and str(job.get("status") or "").strip().casefold()
                            == "succeeded"
                        ):
                            has_success = True
                            break
                    if has_success:
                        break
                if has_success:
                    return
                await self._repository.fail_step_with_event(
                    user_id,
                    plan_id,
                    bound_step.step_id,
                    reason_code=f"provider_{status}",
                    public_summary="分镜视频未成功完成，请查看失败场景后重试。",
                    run_id=idempotency_key,
                    now=datetime.now(UTC),
                )
                return

        if bound_step is None:
            # prepare/assets/分镜视频旧完成事件：步骤已收口或 Workspace 未绑 job 时吞掉，避免 delivering 永久刷屏。
            if _is_soft_ackable_scene_completion(stage=stage, status=status):
                return
            raise AgentRuntimeRecordConflictError(
                "视频事件未绑定唯一Plan步骤"
            )
        if status == ExternalJobStatus.SUCCEEDED.value:
            if (
                plan.status is AgentPlanStatus.COMPLETED
                and bound_step.status is PlanStepStatus.COMPLETED
            ):
                return
            if bound_step.status is not PlanStepStatus.RUNNING:
                if _is_soft_ackable_scene_completion(stage=stage, status=status):
                    return
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent成功事件目标步骤不是运行中"
                )
            if self._native_resume is None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent Operation 恢复缺少原生 resume handler"
                )
            await self._native_resume(  # type: ignore[operator]
                user_id=user_id,
                conversation_id=conversation_id,
                plan_id=plan_id,
                job_id=job_id,
                status=status,
                completion_event_id=completion_event.event_id,
            )
            return
        if status not in _FAILED_OPERATION_STATUSES:
            raise AgentRuntimeRecordConflictError(
                "视频事件不是可恢复终态"
            )
        if (
            plan.status is AgentPlanStatus.FAILED
            and bound_step.status is PlanStepStatus.FAILED
        ):
            return
        if bound_step.status is not PlanStepStatus.RUNNING:
            # 旧 failed 资产事件在步骤已收口后仍 delivering：吞掉以免饿死分镜 status 轮询。
            if _is_soft_ackable_scene_completion(stage=stage, status=status):
                return
            raise AgentRuntimeRecordConflictError(
                "VideoAgent失败事件目标步骤不是运行中"
            )
        await self._repository.fail_step_with_event(
            user_id,
            plan_id,
            bound_step.step_id,
            reason_code=f"provider_{status}",
            public_summary="外部视频任务未成功完成，请按原步骤重试。",
            run_id=idempotency_key,
            now=datetime.now(UTC),
        )


class VideoQuotaResumer:
    """把M06额度暂停/恢复事件投影为V2工作区的独立中断状态。"""

    def __init__(self, *, repository: VideoWorkspaceRepository) -> None:
        self._repository = repository

    async def resume_external_job_quota(
        self,
        namespace: OperationExecutionNamespace,
        *,
        user_id: str,
        conversation_id: str,
        quota_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        payload = OperationQuotaEventPayload.model_validate(quota_event.payload)
        if (
            quota_event.conversation_id != conversation_id
            or idempotency_key != quota_event.event_id
            or namespace
            != workflow_operation_namespace(conversation_id, payload.workflow_id)
        ):
            raise AgentRuntimeRecordConflictError(
                "视频额度事件身份或namespace不匹配"
            )
        plan = await self._repository.get_plan(user_id, payload.workflow_id)
        if plan is None or plan.conversation_id != conversation_id:
            raise AgentRuntimeRecordConflictError(
                "视频额度事件不属于当前用户或会话"
            )
        workspace = await self._repository.get_workspace(
            user_id,
            plan.workspace_id,
        )
        if workspace is None or workspace.conversation_id != conversation_id:
            raise AgentRuntimeRecordConflictError(
                "视频额度事件缺少原工作区"
            )
        step_ids = _operation_step_ids(workspace.payload, payload.job_id)
        if len(step_ids) != 1:
            raise AgentRuntimeRecordConflictError(
                "视频额度事件未绑定唯一Plan步骤"
            )
        step_id = next(iter(step_ids))
        step = next(
            (item for item in plan.steps if item.step_id == step_id),
            None,
        )
        if (
            step is None
            or step.status is not PlanStepStatus.RUNNING
            or not _stage_matches_tool(payload.stage, step.tool_name)
        ):
            raise AgentRuntimeRecordConflictError(
                "视频额度事件目标步骤不是运行中"
            )
        if payload.quota_state is OperationQuotaState.PAUSED:
            current = workspace.payload.get("quota_interrupt")
            projection = {
                "quota_interrupt_id": quota_event.event_id,
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "job_id": payload.job_id,
                "quota_pause_revision": payload.quota_pause_revision,
                "state": "paused",
                "reason_code": payload.reason_code,
            }
            if current == projection:
                return
            if current is not None:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent已有不同额度中断"
                )
            await _apply_workspace_patch_resilient(
                self._repository,
                user_id=user_id,
                workspace=workspace,
                patch={"quota_interrupt": projection},
                now=quota_event.occurred_at,
            )
            return
        current = workspace.payload.get("quota_interrupt")
        resolution = workspace.payload.get("last_quota_resolution")
        if (
            current is None
            and isinstance(resolution, Mapping)
            and resolution.get("event_id") == quota_event.event_id
        ):
            return
        if (
            not isinstance(current, Mapping)
            or current.get("job_id") != payload.job_id
            or current.get("plan_id") != plan.plan_id
            or current.get("step_id") != step.step_id
            or current.get("quota_pause_revision")
            != payload.quota_pause_revision
        ):
            raise AgentRuntimeRecordConflictError(
                "视频额度恢复与当前中断不匹配"
            )
        await _apply_workspace_patch_resilient(
            self._repository,
            user_id=user_id,
            workspace=workspace,
            patch={
                "quota_interrupt": None,
                "last_quota_resolution": {
                    "event_id": quota_event.event_id,
                    "job_id": payload.job_id,
                    "quota_pause_revision": payload.quota_pause_revision,
                    "state": "resumed",
                },
            },
            now=quota_event.occurred_at,
        )


def _is_soft_ackable_scene_completion(*, stage: str, status: str) -> bool:
    """场景包/参考图/分镜视频的陈旧完成事件可安全吞掉（成功或失败终态）。"""

    if not stage.startswith(
        (
            "prepare_scene_packages:",
            "generate_scene_assets:",
            "generate_scene:",
        )
    ):
        return False
    return status in {
        ExternalJobStatus.SUCCEEDED.value,
        *_FAILED_OPERATION_STATUSES,
    }


def _stage_matches_tool(stage: str, tool_name: str) -> bool:
    if stage.startswith("analyze_reference:"):
        return tool_name == "analyze_reference_video"
    if stage.startswith("generate_scene:"):
        return tool_name == "generate_scenes"
    if stage.startswith("prepare_scene_packages:"):
        return tool_name == "prepare_scene_packages"
    if stage.startswith("generate_scene_assets:"):
        return tool_name == "generate_scene_assets"
    if stage in {"deliver:mp4", "deliver:jianying_package"}:
        return tool_name == "compose_or_export_video"
    return False


def _operation_step_ids(value: object, job_id: str) -> set[str]:
    """从同一记录的job与plan_step_id字段恢复显式步骤绑定。"""

    result: set[str] = set()
    if isinstance(value, Mapping):
        record_job_ids = {
            child
            for key, child in value.items()
            if key in {"job_id", "source_job_id"} and isinstance(child, str)
        }
        step_id = value.get("plan_step_id")
        if job_id in record_job_ids and isinstance(step_id, str) and step_id:
            result.add(step_id)
        for child in value.values():
            result.update(_operation_step_ids(child, job_id))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.update(_operation_step_ids(child, job_id))
    return result


__all__ = ["VideoOperationResumer", "VideoQuotaResumer"]
