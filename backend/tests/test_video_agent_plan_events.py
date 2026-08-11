from __future__ import annotations

from datetime import UTC, datetime

from pixelflow.agent_runtime.contracts import AgentEventType
from pixelflow.video_agent.contracts import AgentPlan, AgentPlanStatus, AgentPlanStep, PlanStepStatus
from pixelflow.video_agent.executor.events import (
    build_plan_created_event,
    build_plan_updated_event,
    build_step_completed_event,
    build_step_progressed_event,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def test_plan_created_event_contains_only_public_plan_fields() -> None:
    event = build_plan_created_event(
        event_id="event-1",
        cursor="cursor-1",
        sequence=1,
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=NOW,
        plan=AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.PLANNING,
            public_goal="生成商品视频",
        ),
    )

    assert event.type is AgentEventType.AGENT_PLAN_CREATED
    assert event.payload == {
        "plan_id": "plan-1",
        "workspace_id": "workspace-1",
        "status": "planning",
        "public_goal": "生成商品视频",
        "steps": [],
    }


def test_plan_updated_event_includes_pending_step_list() -> None:
    event = build_plan_updated_event(
        event_id="event-1b",
        cursor="cursor-1b",
        sequence=2,
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=NOW,
        plan=AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.PLANNING,
            public_goal="导入成熟脚本",
            steps=(
                AgentPlanStep(
                    step_id="step-1",
                    plan_id="plan-1",
                    sequence=1,
                    tool_name="import_script",
                    title="导入脚本",
                    status=PlanStepStatus.PENDING,
                ),
            ),
        ),
    )
    assert event.type is AgentEventType.AGENT_PLAN_UPDATED
    assert event.payload["public_goal"] == "导入成熟脚本"
    assert event.payload["steps"] == [
        {
            "step_id": "step-1",
            "sequence": 1,
            "title": "导入脚本",
            "status": "pending",
            "confirmation_required": False,
        }
    ]


def test_completed_step_event_includes_persisted_duration_without_tool_arguments() -> None:
    event = build_step_completed_event(
        event_id="event-2",
        cursor="cursor-2",
        sequence=2,
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=NOW,
        step=AgentPlanStep(
            step_id="step-1",
            plan_id="plan-1",
            sequence=1,
            tool_name="inspect_video_workspace",
            title="读取项目",
            status=PlanStepStatus.COMPLETED,
            public_summary="项目资料已读取",
            artifact_refs=("artifact:workspace-1",),
            started_at=NOW,
            completed_at=datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC),
        ),
    )

    assert event.type is AgentEventType.AGENT_STEP_COMPLETED
    assert event.payload == {
        "plan_id": "plan-1",
        "step_id": "step-1",
        "sequence": 1,
        "title": "读取项目",
        "status": "completed",
        "public_summary": "项目资料已读取",
        "artifact_refs": ["artifact:workspace-1"],
        "started_at": "2026-08-04T00:00:00Z",
        "completed_at": "2026-08-04T00:00:03Z",
        "duration_ms": 3000,
    }


def test_progressed_step_event_exposes_phase_without_tool_arguments() -> None:
    event = build_step_progressed_event(
        event_id="event-3",
        cursor="cursor-3",
        sequence=3,
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=NOW,
        step=AgentPlanStep(
            step_id="step-2",
            plan_id="plan-1",
            sequence=2,
            tool_name="brainstorm_script",
            title="生成广告脚本草稿",
            status=PlanStepStatus.RUNNING,
            public_summary="已交给大模型生成脚本草稿，请稍候…",
            started_at=NOW,
        ),
        progress_phase="await_model",
    )

    assert event.type is AgentEventType.AGENT_STEP_PROGRESSED
    assert event.payload == {
        "plan_id": "plan-1",
        "step_id": "step-2",
        "sequence": 2,
        "title": "生成广告脚本草稿",
        "status": "running",
        "public_summary": "已交给大模型生成脚本草稿，请稍候…",
        "progress_phase": "await_model",
        "started_at": "2026-08-04T00:00:00Z",
    }
    assert "arguments" not in event.payload
