"""Task 11独立VideoAgent Runner生命周期测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.credentials import (
    TransientVideoAgentCredential,
    VideoAgentCredentialUnavailableError,
)
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.runner import VideoAgentRunner, VideoAgentRunScope
from pixelflow.video_agent.tools import InspectVideoWorkspaceTool, VideoToolRegistry
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


@pytest.mark.asyncio
async def test_runner_executes_persisted_plan_and_discards_credential() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    now = datetime(2026, 8, 6, tzinfo=UTC)
    submission = await VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: now,
    ).submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=("artifact:product-1",),
    )
    runner = VideoAgentRunner(
        repository=video_repository,
        executor=VideoAgentExecutor(
            repository=video_repository,
            registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
            clock=lambda: now,
        ),
    )
    credential = TransientVideoAgentCredential("Bearer transient-test")

    await runner.notify_turn(
        VideoAgentRunScope(
            user_id="user-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            plan_id=submission.plan.plan_id,
        ),
        credential,
    )

    restored = await video_repository.get_plan("user-1", submission.plan.plan_id)
    assert restored is not None
    assert restored.status.value == "completed"
    assert restored.steps[0].status.value == "completed"
    with pytest.raises(VideoAgentCredentialUnavailableError):
        credential.borrow_authorization()


@pytest.mark.asyncio
async def test_runner_rejects_cross_conversation_plan_and_discards_credential() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    submission = await VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
    ).submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=(),
    )
    runner = VideoAgentRunner(
        repository=video_repository,
        executor=VideoAgentExecutor(
            repository=video_repository,
            registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
        ),
    )
    credential = TransientVideoAgentCredential("Bearer transient-test")

    with pytest.raises(AgentRuntimeRecordConflictError):
        await runner.notify_turn(
            VideoAgentRunScope(
                user_id="user-1",
                conversation_id="conversation-other",
                turn_id="turn-1",
                plan_id=submission.plan.plan_id,
            ),
            credential,
        )

    with pytest.raises(VideoAgentCredentialUnavailableError):
        credential.borrow_authorization()
