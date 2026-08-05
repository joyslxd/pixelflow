from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.contracts import AgentEventType
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.agent_runtime.service import AgentRuntimeService
from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowConversationRecord
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository


@pytest.mark.asyncio
async def test_entrypoint_creates_recoverable_workspace_plan_and_public_event() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )

    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="我有一个护肤品脚本，帮我生成视频",
        artifact_refs=("artifact:product-1",),
    )

    workspace = await video_repository.get_workspace("user-1", submission.workspace.workspace_id)
    steps = await video_repository.list_plan_steps("user-1", submission.plan.plan_id)
    events = await runtime_repository.list_events("user-1", "conversation-1")

    assert workspace == submission.workspace
    assert workspace.payload["latest_input"] == "我有一个护肤品脚本，帮我生成视频"
    assert workspace.payload["artifact_refs"] == ["artifact:product-1"]
    assert submission.plan.public_goal == "处理视频创作请求"
    assert [step.tool_name for step in steps] == ["inspect_video_workspace"]
    assert events[-1].type is AgentEventType.AGENT_PLAN_CREATED
    assert events[-1].payload["plan_id"] == submission.plan.plan_id


@pytest.mark.asyncio
async def test_entrypoint_replay_returns_existing_plan_without_duplicate_event() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )

    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成视频",
        artifact_refs=(),
    )
    replay = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成视频",
        artifact_refs=(),
    )

    assert replay == first
    assert len(await runtime_repository.list_events("user-1", "conversation-1")) == 1


@pytest.mark.asyncio
async def test_runtime_routes_primary_video_turn_to_v2_entrypoint_without_live_executor() -> None:
    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_entrypoint=entrypoint,
        primary_execution_intents=("video",),
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )
    assignment = service.assignment_for_new_conversation({}, initial_intent="video")
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-v2-entry",
            user_id="user-1",
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=assignment.orchestration_version,
            context=assignment.context,
        ),
    )

    started = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-v2-entry",
        request={
            "client_input_id": "11111111-1111-4111-8111-111111111111",
            "content": "根据护肤品脚本生成视频",
            "materials": [],
            "artifact_refs": ["artifact:product-1"],
            "expected_context_version": 0,
        },
    )

    events = await runtime_repository.list_events("user-1", "conversation-v2-entry")
    plan_event = next(event for event in events if event.type is AgentEventType.AGENT_PLAN_CREATED)
    workspace = await video_repository.get_workspace(
        "user-1",
        plan_event.payload["workspace_id"],
    )
    assert started.status == "accepted"
    assert workspace.payload == {
        "latest_input": "根据护肤品脚本生成视频",
        "artifact_refs": ["artifact:product-1"],
    }
