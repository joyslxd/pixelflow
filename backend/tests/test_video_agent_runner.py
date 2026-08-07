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
from pixelflow.video_agent.tools import (
    BrainstormScriptTool,
    InspectVideoWorkspaceTool,
    VideoToolRegistry,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class _StubVideoDomainAdapter:
    async def brainstorm_script(self, **kwargs):
        return (
            "# 带货脚本\n"
            "时长：15秒\n"
            "画幅：9:16\n"
            "结尾请下单购买\n"
        )

    async def analyze_reference_video(self, video_url: str):
        raise AssertionError("unexpected reference analysis")


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
            registry=VideoToolRegistry(
                [
                    InspectVideoWorkspaceTool(),
                    BrainstormScriptTool(adapter=_StubVideoDomainAdapter()),
                ]
            ),
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
    assert [step.tool_name for step in restored.steps] == [
        "inspect_video_workspace",
        "brainstorm_script",
    ]
    assert all(step.status.value == "completed" for step in restored.steps)
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
