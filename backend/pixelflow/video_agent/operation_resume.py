"""M06完成事件恢复到VideoAgent原计划步骤的适配层。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from pixelflow.agent_runtime.contracts import AgentEvent, ExternalJobStatus
from pixelflow.agent_runtime.jobs import (
    OperationQuotaEventPayload,
    OperationQuotaState,
)
from pixelflow.agent_runtime.operation_namespace import (
    OperationExecutionNamespace,
    workflow_operation_namespace,
)
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.video_agent.contracts import AgentPlanStatus, PlanStepStatus
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.workspace import VideoAgentRepository

_FAILED_OPERATION_STATUSES = frozenset(
    {
        ExternalJobStatus.FAILED.value,
        ExternalJobStatus.TIMEOUT.value,
        ExternalJobStatus.EXPIRED.value,
    }
)


class VideoAgentOperationResumer:
    """用完成事件ID作为checkpoint，恢复原Plan而不重新启动Provider。"""

    def __init__(
        self,
        *,
        repository: VideoAgentRepository,
        executor: VideoAgentExecutor,
    ) -> None:
        self._repository = repository
        self._executor = executor

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
                "VideoAgent完成事件身份不完整或namespace不匹配"
            )
        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None or plan.conversation_id != conversation_id:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent完成事件不属于当前用户或会话"
            )
        workspace = await self._repository.get_workspace(
            user_id,
            plan.workspace_id,
        )
        if (
            workspace is None
            or workspace.conversation_id != conversation_id
        ):
            raise AgentRuntimeRecordConflictError(
                "VideoAgent完成事件未绑定原工作区Operation"
            )
        bound_step_ids = _operation_step_ids(workspace.payload, job_id)
        if len(bound_step_ids) != 1:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent完成事件未绑定唯一Plan步骤"
            )
        bound_step = next(
            (
                step
                for step in plan.steps
                if step.step_id == next(iter(bound_step_ids))
            ),
            None,
        )
        if bound_step is None or not _stage_matches_tool(
            stage,
            bound_step.tool_name,
        ):
            raise AgentRuntimeRecordConflictError(
                "VideoAgent完成事件未绑定唯一运行步骤"
            )
        if status == ExternalJobStatus.SUCCEEDED.value:
            if (
                plan.status is AgentPlanStatus.COMPLETED
                and bound_step.status is PlanStepStatus.COMPLETED
            ):
                return
            if bound_step.status is not PlanStepStatus.RUNNING:
                raise AgentRuntimeRecordConflictError(
                    "VideoAgent成功事件目标步骤不是运行中"
                )
            await self._executor.resume_plan(user_id, plan_id)
            return
        if status not in _FAILED_OPERATION_STATUSES:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent完成事件不是可恢复终态"
            )
        if (
            plan.status is AgentPlanStatus.FAILED
            and bound_step.status is PlanStepStatus.FAILED
        ):
            return
        if bound_step.status is not PlanStepStatus.RUNNING:
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


class VideoAgentQuotaResumer:
    """把M06额度暂停/恢复事件投影为V2工作区的独立中断状态。"""

    def __init__(self, *, repository: VideoAgentRepository) -> None:
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
                "VideoAgent额度事件身份或namespace不匹配"
            )
        plan = await self._repository.get_plan(user_id, payload.workflow_id)
        if plan is None or plan.conversation_id != conversation_id:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent额度事件不属于当前用户或会话"
            )
        workspace = await self._repository.get_workspace(
            user_id,
            plan.workspace_id,
        )
        if workspace is None or workspace.conversation_id != conversation_id:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent额度事件缺少原工作区"
            )
        step_ids = _operation_step_ids(workspace.payload, payload.job_id)
        if len(step_ids) != 1:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent额度事件未绑定唯一Plan步骤"
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
                "VideoAgent额度事件目标步骤不是运行中"
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
            await self._repository.apply_workspace_patch(
                user_id,
                workspace.workspace_id,
                {"quota_interrupt": projection},
                expected_revision=workspace.revision,
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
                "VideoAgent额度恢复与当前中断不匹配"
            )
        await self._repository.apply_workspace_patch(
            user_id,
            workspace.workspace_id,
            {
                "quota_interrupt": None,
                "last_quota_resolution": {
                    "event_id": quota_event.event_id,
                    "job_id": payload.job_id,
                    "quota_pause_revision": payload.quota_pause_revision,
                    "state": "resumed",
                },
            },
            expected_revision=workspace.revision,
            now=quota_event.occurred_at,
        )


def _stage_matches_tool(stage: str, tool_name: str) -> bool:
    if stage.startswith("analyze_reference:"):
        return tool_name == "analyze_reference_video"
    if stage.startswith("generate_scene:"):
        return tool_name == "generate_scenes"
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


__all__ = ["VideoAgentOperationResumer", "VideoAgentQuotaResumer"]
