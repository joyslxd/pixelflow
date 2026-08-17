"""原生 VideoAgent Runner 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.credentials import (
    TransientVideoAgentCredential,
    VideoAgentCredentialUnavailableError,
)
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint, video_agent_plan_id
from pixelflow.video_agent.runner import VideoAgentRunner, VideoAgentRunScope
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository

T0 = datetime(2026, 8, 12, tzinfo=UTC)


class _RecordingInvoker:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def invoke(self, request: Any) -> Any:
        self.calls.append(request)
        return None


@pytest.mark.asyncio
async def test_runner_invokes_native_invoker_and_marks_plan_completed() -> None:
    invoker = _RecordingInvoker()
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=event_repository,
        video_repository=repository,
        native_invoker=invoker,  # type: ignore[arg-type]
        clock=lambda: T0,
    )
    runner = VideoAgentRunner(
        repository=repository,
        native_invoker=invoker,  # type: ignore[arg-type]
    )

    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="看看现在项目状态",
        artifact_refs=(),
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

    assert len(invoker.calls) == 1
    assert invoker.calls[0].content == "看看现在项目状态"
    assert invoker.calls[0].plan_id == video_agent_plan_id("conversation-1", "turn-1")
    completed = await repository.get_plan("user-1", submission.plan.plan_id)
    assert completed is not None
    assert completed.status is AgentPlanStatus.COMPLETED
    with pytest.raises(VideoAgentCredentialUnavailableError):
        credential.borrow_authorization()


@pytest.mark.asyncio
async def test_runner_prefers_workspace_latest_input() -> None:
    invoker = _RecordingInvoker()
    repository = MemoryVideoAgentRepository(
        event_repository=MemoryAgentRuntimeRepository(),
    )
    workspace = await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={"latest_input": "用户补充后的指令"},
            created_at=T0,
            updated_at=T0,
        ),
    )
    plan = AgentPlan(
        plan_id="plan-1",
        workspace_id=workspace.workspace_id,
        conversation_id=workspace.conversation_id,
        status=AgentPlanStatus.RUNNING,
        public_goal="旧目标",
        steps=(),
        created_at=T0,
        updated_at=T0,
    )
    await repository.save_plan("user-1", plan, [])
    runner = VideoAgentRunner(
        repository=repository,
        native_invoker=invoker,  # type: ignore[arg-type]
    )

    await runner.notify_turn(
        VideoAgentRunScope(
            user_id="user-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            plan_id="plan-1",
        ),
        None,
    )

    assert invoker.calls[0].content == "用户补充后的指令"


@pytest.mark.asyncio
async def test_runner_rejects_cross_conversation_plan_and_discards_credential() -> None:
    invoker = _RecordingInvoker()
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    submission = await VideoAgentEntrypoint(
        runtime_repository=event_repository,
        video_repository=repository,
        native_invoker=invoker,  # type: ignore[arg-type]
        clock=lambda: T0,
    ).submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成商品视频",
        artifact_refs=(),
    )
    runner = VideoAgentRunner(
        repository=repository,
        native_invoker=invoker,  # type: ignore[arg-type]
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

    assert invoker.calls == []
    with pytest.raises(VideoAgentCredentialUnavailableError):
        credential.borrow_authorization()
