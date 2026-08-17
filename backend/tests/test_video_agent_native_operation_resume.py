"""P0-3.2 原生 Operation 终态 resume Turn。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.agent_runtime.operation_namespace import workflow_operation_namespace
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.native_operation_resume import NativeOperationResumeHandler
from pixelflow.video_agent.operation_resume import VideoAgentOperationResumer
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class _RecordingInvoker:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def invoke(self, request: Any) -> Any:
        self.calls.append(request)
        return None


class _UnusedExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def resume_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        self.calls += 1
        raise AssertionError("native resume 路径不应调用 executor.resume_plan")


@pytest.mark.asyncio
async def test_native_operation_resume_invokes_agent_once() -> None:
    now = datetime(2026, 8, 12, 13, tzinfo=UTC)
    events = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=events)
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

    invoker = _RecordingInvoker()
    handler = NativeOperationResumeHandler(
        repository=repository,
        native_invoker=invoker,  # type: ignore[arg-type]
    )
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=_UnusedExecutor(),  # type: ignore[arg-type]
        native_resume=handler,
    )
    completion = AgentEvent(
        event_id="evt-op-1",
        sequence=1,
        cursor="cursor-op-1",
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-1",
            "workflow_id": "plan-1",
            "stage": "analyze_reference:0123456789abcdef",
            "status": "succeeded",
        },
    )

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=completion,
        idempotency_key="evt-op-1",
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=completion,
        idempotency_key="evt-op-1",
    )

    assert len(invoker.calls) == 1
    assert "内部恢复" in invoker.calls[0].content
    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    assert plan.status is AgentPlanStatus.COMPLETED
    assert plan.steps[0].status is PlanStepStatus.COMPLETED
