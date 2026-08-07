"""Task 11 VideoAgent Operation完成事件恢复桥测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.agent_runtime.operation_namespace import (
    workflow_operation_namespace,
)
from pixelflow.agent_runtime.jobs import (
    OperationQuotaState,
    build_operation_quota_event_id,
)
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.operation_resume import (
    VideoAgentOperationResumer,
    VideoAgentQuotaResumer,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class CompletingExecutor:
    def __init__(self, repository: MemoryVideoAgentRepository, now: datetime) -> None:
        self.repository = repository
        self.now = now
        self.calls = 0

    async def resume_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        self.calls += 1
        plan = await self.repository.get_plan(user_id, plan_id)
        assert plan is not None
        step = next(item for item in plan.steps if item.status is PlanStepStatus.RUNNING)
        await self.repository.complete_step_with_event(
            user_id,
            plan_id,
            step.step_id,
            VideoToolResult(
                tool_name=step.tool_name,
                public_summary="参考视频解析完成",
            ),
            run_id=plan_id,
            now=self.now,
        )
        return await self.repository.update_plan_status(
            user_id,
            plan_id,
            AgentPlanStatus.COMPLETED,
            now=self.now,
        )


async def _runtime_state() -> tuple[
    MemoryAgentRuntimeRepository,
    MemoryVideoAgentRepository,
    datetime,
]:
    now = datetime(2026, 8, 6, 9, tzinfo=UTC)
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "reference_videos": [
                    {
                        "job_id": "job-1",
                        "plan_step_id": "step-1",
                        "status": "polling",
                    }
                ]
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.RUNNING,
            created_at=now,
            updated_at=now,
        ),
        [
            AgentPlanStep(
                step_id="step-1",
                plan_id="plan-1",
                sequence=1,
                tool_name="analyze_reference_video",
                title="解析参考视频",
                status=PlanStepStatus.RUNNING,
                started_at=now,
            )
        ],
    )
    return event_repository, repository, now


def _completion_event(status: str, now: datetime) -> AgentEvent:
    return AgentEvent(
        event_id="evt-completion-1",
        sequence=1,
        cursor="cursor-completion-1",
        conversation_id="conversation-1",
        run_id="run-completion-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-1",
            "workflow_id": "plan-1",
            "stage": "analyze_reference:0123456789abcdef",
            "status": status,
        },
    )


@pytest.mark.asyncio
async def test_success_completion_resumes_original_plan_without_credential() -> None:
    _events, repository, now = await _runtime_state()
    executor = CompletingExecutor(repository, now)
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=executor,  # type: ignore[arg-type]
    )

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("succeeded", now),
        idempotency_key="evt-completion-1",
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("succeeded", now),
        idempotency_key="evt-completion-1",
    )

    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    assert plan.status is AgentPlanStatus.COMPLETED
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_failed_completion_atomically_fails_bound_step_and_plan() -> None:
    events, repository, now = await _runtime_state()
    executor = CompletingExecutor(repository, now)
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=executor,  # type: ignore[arg-type]
    )

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("timeout", now),
        idempotency_key="evt-completion-1",
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("timeout", now),
        idempotency_key="evt-completion-1",
    )

    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    assert plan.status is AgentPlanStatus.FAILED
    assert plan.steps[0].status is PlanStepStatus.FAILED
    failure_events = [
        event
        for event in await events.list_events("user-1", "conversation-1")
        if event.type is AgentEventType.AGENT_STEP_FAILED
    ]
    assert len(failure_events) == 1
    assert failure_events[0].payload["reason_code"] == "provider_timeout"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_completion_rejects_cross_owner_and_unbound_step() -> None:
    _events, repository, now = await _runtime_state()
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=CompletingExecutor(repository, now),  # type: ignore[arg-type]
    )

    with pytest.raises(AgentRuntimeRecordConflictError):
        await resumer.resume_external_job(
            workflow_operation_namespace("conversation-1", "plan-1"),
            user_id="user-other",
            conversation_id="conversation-1",
            completion_event=_completion_event("succeeded", now),
            idempotency_key="evt-completion-1",
        )


def _quota_event(
    state: OperationQuotaState,
    now: datetime,
) -> AgentEvent:
    event_id = build_operation_quota_event_id("job-1", 1, state)
    return AgentEvent(
        event_id=event_id,
        sequence=2 if state is OperationQuotaState.PAUSED else 3,
        cursor=f"cursor-{state.value}",
        conversation_id="conversation-1",
        run_id=f"run-{state.value}",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED,
        payload={
            "job_id": "job-1",
            "workflow_id": "plan-1",
            "stage": "analyze_reference:0123456789abcdef",
            "stage_version": 1,
            "attempt": 1,
            "quota_pause_revision": 1,
            "quota_state": state.value,
            "reason_code": (
                "provider_quota_insufficient"
                if state is OperationQuotaState.PAUSED
                else "provider_quota_resume_authorized"
            ),
        },
    )


@pytest.mark.asyncio
async def test_quota_pause_and_resume_project_same_bound_step() -> None:
    _events, repository, now = await _runtime_state()
    resumer = VideoAgentQuotaResumer(repository=repository)
    paused = _quota_event(OperationQuotaState.PAUSED, now)
    resumed = _quota_event(OperationQuotaState.RESUMED, now)

    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=paused,
        idempotency_key=paused.event_id,
    )
    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=paused,
        idempotency_key=paused.event_id,
    )
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    assert workspace.payload["quota_interrupt"] == {
        "quota_interrupt_id": paused.event_id,
        "plan_id": "plan-1",
        "step_id": "step-1",
        "job_id": "job-1",
        "quota_pause_revision": 1,
        "state": "paused",
        "reason_code": "provider_quota_insufficient",
    }

    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=resumed,
        idempotency_key=resumed.event_id,
    )
    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=resumed,
        idempotency_key=resumed.event_id,
    )
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    assert workspace.payload["quota_interrupt"] is None
    assert workspace.payload["last_quota_resolution"]["event_id"] == resumed.event_id
