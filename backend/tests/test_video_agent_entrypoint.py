"""原生 VideoAgent Entrypoint 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from pixelflow.agent_runtime.contracts import AgentEventType
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.contracts import AgentPlanStatus
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint, video_agent_plan_id
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository

T0 = datetime(2026, 8, 12, tzinfo=UTC)


class _NoopInvoker:
    async def invoke(self, request: Any) -> Any:
        del request
        return None


@pytest.mark.asyncio
async def test_submit_turn_creates_native_workspace_and_running_plan() -> None:
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=event_repository,
        video_repository=repository,
        native_invoker=_NoopInvoker(),  # type: ignore[arg-type]
        clock=lambda: T0,
    )

    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=("artifact:product-1",),
    )

    assert submission.plan.status is AgentPlanStatus.RUNNING
    assert submission.plan.steps == ()
    assert submission.plan.plan_id == video_agent_plan_id("conversation-1", "turn-1")
    assert submission.workspace.payload["native_agent"] is True
    assert submission.workspace.payload["latest_input"] == "生成商品视频"
    assert submission.workspace.payload["artifact_refs"] == ["artifact:product-1"]

    events = await event_repository.list_events("user-1", "conversation-1")
    assert any(event.type is AgentEventType.AGENT_PLAN_CREATED for event in events)
    assert any(event.type is AgentEventType.AGENT_PLAN_UPDATED for event in events)


@pytest.mark.asyncio
async def test_submit_turn_is_idempotent_for_same_turn() -> None:
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=event_repository,
        video_repository=repository,
        native_invoker=_NoopInvoker(),  # type: ignore[arg-type]
        clock=lambda: T0,
    )

    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=(),
    )
    second = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="不应再次写入",
        artifact_refs=(),
    )

    assert second.plan.plan_id == first.plan.plan_id
    assert second.workspace.workspace_id == first.workspace.workspace_id
    assert second.workspace.payload["latest_input"] == "生成商品视频"


@pytest.mark.asyncio
async def test_submit_turn_rejects_empty_content() -> None:
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=MemoryAgentRuntimeRepository(),
        video_repository=MemoryVideoAgentRepository(),
        native_invoker=_NoopInvoker(),  # type: ignore[arg-type]
        clock=lambda: T0,
    )

    with pytest.raises(ValueError, match="内容"):
        await entrypoint.submit_turn(
            user_id="user-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            content="   ",
            artifact_refs=(),
        )
