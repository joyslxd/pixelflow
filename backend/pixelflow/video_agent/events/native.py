"""原生 Video Agent 统一公开事件构建器（设计 §12）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _base_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    event_type: AgentEventType,
    payload: Mapping[str, Any],
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=event_type,
        payload=dict(payload),
    )


def build_reasoning_summary_delta_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    delta: str,
    channel: str = "summary",
) -> AgentEvent:
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_REASONING_SUMMARY_DELTA,
        payload={
            "turn_id": turn_id,
            "delta": delta,
            "channel": channel,
            "server_time": _iso(occurred_at),
        },
    )


def build_reasoning_summary_completed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    summary: str,
    duration_ms: int | None = None,
) -> AgentEvent:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "summary": summary[:2_000],
        "server_time": _iso(occurred_at),
    }
    if duration_ms is not None:
        payload["duration_ms"] = max(0, int(duration_ms))
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_REASONING_SUMMARY_COMPLETED,
        payload=payload,
    )


def build_tool_started_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    tool_name: str,
    tool_call_id: str,
    plan_id: str | None = None,
    step_id: str | None = None,
    title: str | None = None,
) -> AgentEvent:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "status": "running",
        "started_at": _iso(occurred_at),
        "server_time": _iso(occurred_at),
    }
    if plan_id:
        payload["plan_id"] = plan_id
    if step_id:
        payload["step_id"] = step_id
    if title:
        payload["title"] = title[:200]
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_TOOL_STARTED,
        payload=payload,
    )


def build_tool_progress_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    tool_name: str,
    tool_call_id: str,
    public_summary: str,
    phase: str | None = None,
) -> AgentEvent:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "status": "running",
        "public_summary": public_summary[:2_000],
        "server_time": _iso(occurred_at),
    }
    if phase:
        payload["phase"] = phase[:64]
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_TOOL_PROGRESS,
        payload=payload,
    )


def build_tool_completed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    tool_name: str,
    tool_call_id: str,
    public_summary: str,
    artifact_refs: tuple[str, ...] = (),
    duration_ms: int | None = None,
) -> AgentEvent:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "status": "completed",
        "public_summary": public_summary[:2_000],
        "artifact_refs": list(artifact_refs)[:32],
        "completed_at": _iso(occurred_at),
        "server_time": _iso(occurred_at),
    }
    if duration_ms is not None:
        payload["duration_ms"] = max(0, int(duration_ms))
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_TOOL_COMPLETED,
        payload=payload,
    )


def build_tool_failed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    tool_name: str,
    tool_call_id: str,
    public_summary: str,
) -> AgentEvent:
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_TOOL_FAILED,
        payload={
            "turn_id": turn_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "status": "failed",
            "public_summary": public_summary[:2_000],
            "completed_at": _iso(occurred_at),
            "server_time": _iso(occurred_at),
        },
    )


def build_response_delta_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    delta: str,
) -> AgentEvent:
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_RESPONSE_DELTA,
        payload={
            "turn_id": turn_id,
            "delta": delta,
            "server_time": _iso(occurred_at),
        },
    )


def build_response_completed_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    text: str,
) -> AgentEvent:
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_RESPONSE_COMPLETED,
        payload={
            "turn_id": turn_id,
            "text": text[:8_000],
            "server_time": _iso(occurred_at),
        },
    )


def build_operation_updated_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    operation_id: str,
    status: str,
    stage: str | None = None,
    public_summary: str | None = None,
) -> AgentEvent:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "operation_id": operation_id,
        "status": status,
        "server_time": _iso(occurred_at),
    }
    if stage:
        payload["stage"] = stage
    if public_summary:
        payload["public_summary"] = public_summary[:2_000]
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_OPERATION_UPDATED,
        payload=payload,
    )


def build_artifact_updated_event(
    *,
    event_id: str,
    cursor: str,
    sequence: int,
    conversation_id: str,
    turn_id: str,
    occurred_at: datetime,
    artifact_refs: tuple[str, ...],
    kind: str | None = None,
) -> AgentEvent:
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "artifact_refs": list(artifact_refs)[:64],
        "server_time": _iso(occurred_at),
    }
    if kind:
        payload["kind"] = kind[:64]
    return _base_event(
        event_id=event_id,
        cursor=cursor,
        sequence=sequence,
        conversation_id=conversation_id,
        run_id=turn_id,
        occurred_at=occurred_at,
        event_type=AgentEventType.AGENT_ARTIFACT_UPDATED,
        payload=payload,
    )
