from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.contracts import AgentEventType
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolCall,
)


def test_completed_step_calculates_duration_from_persisted_timestamps() -> None:
    step = AgentPlanStep(
        step_id="step-1",
        plan_id="plan-1",
        sequence=1,
        tool_name="inspect_video_workspace",
        title="读取项目",
        status=PlanStepStatus.COMPLETED,
        started_at=datetime(2026, 8, 4, tzinfo=UTC),
        completed_at=datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC),
    )

    assert step.duration_ms == 3000


def test_completed_step_requires_started_and_completed_timestamps() -> None:
    with pytest.raises(ValidationError, match="started_at"):
        AgentPlanStep(
            step_id="step-1",
            plan_id="plan-1",
            sequence=1,
            tool_name="inspect_video_workspace",
            title="读取项目",
            status=PlanStepStatus.COMPLETED,
            completed_at=datetime(2026, 8, 4, tzinfo=UTC),
        )


def test_pending_step_rejects_execution_timestamps() -> None:
    with pytest.raises(ValidationError, match="pending"):
        AgentPlanStep(
            step_id="step-1",
            plan_id="plan-1",
            sequence=1,
            tool_name="inspect_video_workspace",
            title="读取项目",
            status=PlanStepStatus.PENDING,
            started_at=datetime(2026, 8, 4, tzinfo=UTC),
        )


def test_waiting_for_input_plan_allows_empty_steps() -> None:
    plan = AgentPlan(
        plan_id="plan-wait-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=AgentPlanStatus.WAITING_FOR_INPUT,
        public_goal="请补充：视频画幅、结尾行动引导",
        steps=(),
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        updated_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert plan.status is AgentPlanStatus.WAITING_FOR_INPUT
    assert plan.steps == ()


def test_plan_and_tool_call_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.PLANNING,
            unexpected="value",
        )

    with pytest.raises(ValidationError, match="extra_forbidden"):
        VideoToolCall(
            tool_name="inspect_video_workspace",
            unexpected="value",
        )


def test_video_agent_public_event_types_are_part_of_the_runtime_protocol() -> None:
    assert {
        AgentEventType.AGENT_PLAN_CREATED.value,
        AgentEventType.AGENT_PLAN_UPDATED.value,
        AgentEventType.AGENT_STEP_STARTED.value,
        AgentEventType.AGENT_STEP_PROGRESSED.value,
        AgentEventType.AGENT_STEP_COMPLETED.value,
        AgentEventType.AGENT_STEP_FAILED.value,
        AgentEventType.AGENT_THINKING_STARTED.value,
        AgentEventType.AGENT_THINKING_DELTA.value,
        AgentEventType.AGENT_THINKING_COMPLETED.value,
        AgentEventType.AGENT_REASONING_SUMMARY_DELTA.value,
        AgentEventType.AGENT_REASONING_SUMMARY_COMPLETED.value,
        AgentEventType.AGENT_TOOL_STARTED.value,
        AgentEventType.AGENT_TOOL_PROGRESS.value,
        AgentEventType.AGENT_TOOL_COMPLETED.value,
        AgentEventType.AGENT_TOOL_FAILED.value,
        AgentEventType.AGENT_OPERATION_UPDATED.value,
        AgentEventType.AGENT_ARTIFACT_UPDATED.value,
        AgentEventType.AGENT_RESPONSE_DELTA.value,
        AgentEventType.AGENT_RESPONSE_COMPLETED.value,
        AgentEventType.AGENT_CONFIRMATION_REQUESTED.value,
    } == {
        "agent.plan.created",
        "agent.plan.updated",
        "agent.step.started",
        "agent.step.progressed",
        "agent.step.completed",
        "agent.step.failed",
        "agent.thinking.started",
        "agent.thinking.delta",
        "agent.thinking.completed",
        "agent.reasoning_summary.delta",
        "agent.reasoning_summary.completed",
        "agent.tool.started",
        "agent.tool.progress",
        "agent.tool.completed",
        "agent.tool.failed",
        "agent.operation.updated",
        "agent.artifact.updated",
        "agent.response.delta",
        "agent.response.completed",
        "agent.confirmation.requested",
    }
