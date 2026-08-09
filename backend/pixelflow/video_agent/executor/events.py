"""由已持久化 VideoAgent 记录构建公开 SSE 事件。"""

from __future__ import annotations

from datetime import UTC, datetime

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.video_agent.contracts import AgentPlan, AgentPlanStep, PlanStepStatus


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


def build_step_started_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    step: AgentPlanStep,
) -> AgentEvent:
    if step.started_at is None:
        raise ValueError("started step event requires persisted start timestamp")
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=AgentEventType.AGENT_STEP_STARTED,
        payload={
            "plan_id": step.plan_id,
            "step_id": step.step_id,
            "sequence": step.sequence,
            "title": step.title,
            "status": step.status.value,
            "started_at": _iso(step.started_at),
        },
    )


def build_step_progressed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    step: AgentPlanStep,
    progress_phase: str,
) -> AgentEvent:
    """公开步骤阶段性进度；只含安全文案，不含 prompt / 供应商细节。"""

    if step.started_at is None:
        raise ValueError("progressed step event requires persisted start timestamp")
    if step.status is not PlanStepStatus.RUNNING:
        raise ValueError("progressed step event requires a running step")
    phase = progress_phase.strip()
    if not phase or len(phase) > 64:
        raise ValueError("progress_phase 无效")
    summary = (step.public_summary or "").strip()
    if not summary:
        raise ValueError("progressed step event requires public_summary")
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=AgentEventType.AGENT_STEP_PROGRESSED,
        payload={
            "plan_id": step.plan_id,
            "step_id": step.step_id,
            "sequence": step.sequence,
            "title": step.title,
            "status": step.status.value,
            "public_summary": summary,
            "progress_phase": phase,
            "started_at": _iso(step.started_at),
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


def build_step_failed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    step: AgentPlanStep,
    reason_code: str,
) -> AgentEvent:
    """只公开固定失败码和安全摘要，不透传供应商原始错误。"""

    if step.duration_ms is None or step.started_at is None or step.completed_at is None:
        raise ValueError("failed step event requires persisted timestamps")
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=AgentEventType.AGENT_STEP_FAILED,
        payload={
            "plan_id": step.plan_id,
            "step_id": step.step_id,
            "sequence": step.sequence,
            "title": step.title,
            "status": step.status.value,
            "public_summary": step.public_summary,
            "reason_code": reason_code,
            "started_at": _iso(step.started_at),
            "completed_at": _iso(step.completed_at),
            "duration_ms": step.duration_ms,
        },
    )
