"""P0-2.2 原生 Video Agent 公开事件合同。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from pixelflow.agent_runtime.contracts import AgentEventType
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.events import (
    NativeAgentEventPublisher,
    build_response_completed_event,
    build_tool_started_event,
)
from pixelflow.video_agent.middleware.progress import VideoProgressMiddleware
from pixelflow.video_agent.tool_runtime_context import bind_tool_runtime_context


def test_native_tool_started_event_carries_required_fields() -> None:
    occurred = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    event = build_tool_started_event(
        event_id="evt-1",
        cursor="cur-1",
        sequence=3,
        conversation_id="conv-1",
        turn_id="turn-1",
        occurred_at=occurred,
        tool_name="prepare_scene_packages",
        tool_call_id="call-1",
        plan_id="plan-1",
        step_id="plan-1-native",
        title="准备场景包",
    )
    assert event.type is AgentEventType.AGENT_TOOL_STARTED
    assert event.conversation_id == "conv-1"
    assert event.run_id == "turn-1"
    assert event.sequence == 3
    assert event.payload["turn_id"] == "turn-1"
    assert event.payload["tool_name"] == "prepare_scene_packages"
    assert event.payload["server_time"].endswith("Z")
    assert event.payload["plan_id"] == "plan-1"


def test_native_response_completed_event_truncates_text() -> None:
    occurred = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    event = build_response_completed_event(
        event_id="evt-2",
        cursor="cur-2",
        sequence=4,
        conversation_id="conv-1",
        turn_id="turn-1",
        occurred_at=occurred,
        text="x" * 9_000,
    )
    assert event.type is AgentEventType.AGENT_RESPONSE_COMPLETED
    assert len(event.payload["text"]) == 8_000
    assert event.payload["turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_native_event_publisher_sequences_tool_lifecycle() -> None:
    repository = MemoryAgentRuntimeRepository()
    clock_values = [
        datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 12, 12, 0, 1, tzinfo=UTC),
    ]
    clock_iter = iter(clock_values)

    publisher = NativeAgentEventPublisher(
        repository=repository,
        user_id="user-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        clock=lambda: next(clock_iter),
    )
    started = await publisher.tool_started(
        tool_name="inspect_video_workspace",
        tool_call_id="call-9",
        plan_id="plan-9",
        step_id="plan-9-native",
    )
    completed = await publisher.tool_completed(
        tool_name="inspect_video_workspace",
        tool_call_id="call-9",
        public_summary="已检查工作区",
        artifact_refs=("artifact:ws-1",),
        duration_ms=12,
    )
    events = await repository.list_events("user-1", "conv-1")
    assert [item.type.value for item in events] == [
        "agent.tool.started",
        "agent.tool.completed",
    ]
    assert started.sequence == 1
    assert completed.sequence == 2
    assert completed.payload["public_summary"] == "已检查工作区"
    assert completed.payload["artifact_refs"] == ["artifact:ws-1"]


@pytest.mark.asyncio
async def test_progress_middleware_emits_tool_started_and_completed() -> None:
    repository = MemoryAgentRuntimeRepository()
    middleware = VideoProgressMiddleware(runtime_repository=repository)
    request = SimpleNamespace(
        tool_call={"name": "inspect_video_workspace", "id": "call-mw-1", "args": {}},
    )
    handler = AsyncMock(
        return_value=ToolMessage(
            content='{"public_summary":"工作区正常","artifact_refs":["artifact:a1"]}',
            tool_call_id="call-mw-1",
        )
    )

    with bind_tool_runtime_context(
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
        }
    ):
        result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    events = await repository.list_events("user-1", "conv-1")
    assert [item.type.value for item in events] == [
        "agent.tool.started",
        "agent.tool.completed",
    ]
    assert events[1].payload["public_summary"] == "工作区正常"


@pytest.mark.asyncio
async def test_progress_middleware_skips_framework_tools() -> None:
    repository = MemoryAgentRuntimeRepository()
    middleware = VideoProgressMiddleware(runtime_repository=repository)
    request = SimpleNamespace(
        tool_call={"name": "update_video_plan", "id": "call-fw", "args": {}},
    )
    handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="call-fw"))

    with bind_tool_runtime_context(
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
        }
    ):
        await middleware.awrap_tool_call(request, handler)

    assert await repository.list_events("user-1", "conv-1") == []
