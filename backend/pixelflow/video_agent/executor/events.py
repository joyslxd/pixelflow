"""由已持久化 VideoAgent 记录构建公开 SSE 事件。"""

from __future__ import annotations

from datetime import UTC, datetime

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.video_agent.contracts import AgentPlan, AgentPlanStep


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_plan_created_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    plan: AgentPlan,
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=AgentEventType.AGENT_PLAN_CREATED,
        payload={
            "plan_id": plan.plan_id,
            "workspace_id": plan.workspace_id,
            "status": plan.status.value,
            "public_goal": plan.public_goal,
        },
    )


def build_step_completed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    step: AgentPlanStep,
) -> AgentEvent:
    if step.duration_ms is None or step.started_at is None or step.completed_at is None:
        raise ValueError("completed step event requires persisted timestamps")
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=AgentEventType.AGENT_STEP_COMPLETED,
        payload={
            "plan_id": step.plan_id,
            "step_id": step.step_id,
            "sequence": step.sequence,
            "title": step.title,
            "status": step.status.value,
            "public_summary": step.public_summary,
            "artifact_refs": list(step.artifact_refs),
            "started_at": _iso(step.started_at),
            "completed_at": _iso(step.completed_at),
            "duration_ms": step.duration_ms,
        },
    )
