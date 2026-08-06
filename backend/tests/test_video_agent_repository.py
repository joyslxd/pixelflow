from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.workspace.repository import (
    MemoryVideoAgentRepository,
    SQLVideoAgentRepository,
    VideoAgentRepository,
)

RepositoryKind = Literal["memory", "sql"]
T0 = datetime(2026, 8, 4, tzinfo=UTC)
T3 = datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC)


@asynccontextmanager
async def repository(
    kind: RepositoryKind,
) -> AsyncIterator[tuple[VideoAgentRepository, MemoryAgentRuntimeRepository | SQLAgentRuntimeRepository]]:
    if kind == "memory":
        event_repository = MemoryAgentRuntimeRepository()
        yield MemoryVideoAgentRepository(event_repository=event_repository), event_repository
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        yield SQLVideoAgentRepository(session_factory), SQLAgentRuntimeRepository(session_factory)
    finally:
        await engine.dispose()


def workspace() -> VideoWorkspace:
    return VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={"script": {"content": "展示商品"}},
        created_at=T0,
        updated_at=T0,
    )


def plan() -> AgentPlan:
    return AgentPlan(
        plan_id="plan-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=AgentPlanStatus.PLANNING,
        public_goal="生成商品视频",
        created_at=T0,
        updated_at=T0,
    )


def pending_step() -> AgentPlanStep:
    return AgentPlanStep(
        step_id="step-1",
        plan_id="plan-1",
        sequence=1,
        tool_name="inspect_video_workspace",
        title="读取项目",
        status=PlanStepStatus.PENDING,
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_complete_step_persists_duration_and_owner_isolation(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        started = await store.start_step("user-a", "plan-1", "step-1", now=T0)
        completed = await store.complete_step(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
                artifact_refs=("artifact:workspace-1",),
            ),
            now=T3,
        )

        assert started.status is PlanStepStatus.RUNNING
        assert completed.status is PlanStepStatus.COMPLETED
        assert completed.duration_ms == 3000
        assert completed.artifact_refs == ("artifact:workspace-1",)
        assert await store.get_workspace("user-b", "workspace-1") is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_workspace_patch_uses_revision_and_replays_same_snapshot(
    kind: RepositoryKind,
) -> None:
    patch = {
        "script": {
            "source": "user_import",
            "version": 1,
            "content": "展示商品",
        }
    }
    async with repository(kind) as (store, _):
        created = await store.create_workspace("user-a", workspace())

        updated = await store.apply_workspace_patch(
            "user-a",
            created.workspace_id,
            patch,
            expected_revision=created.revision,
            now=T3,
        )
        replay = await store.apply_workspace_patch(
            "user-a",
            created.workspace_id,
            patch,
            expected_revision=created.revision,
            now=T3,
        )

        assert updated.revision == 2
        assert updated.payload["script"] == patch["script"]
        assert replay == updated
        with pytest.raises(AgentRuntimeRecordConflictError, match="revision"):
            await store.apply_workspace_patch(
                "user-a",
                created.workspace_id,
                {"script": {"source": "different"}},
                expected_revision=created.revision,
                now=T3,
            )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_load_conversation_state_returns_latest_plan_with_ordered_steps(
    kind: RepositoryKind,
) -> None:
    """Snapshot Repository 只返回当前用户、当前会话的权威工作区和最新计划。"""

    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        first = plan()
        await store.save_plan("user-a", first, [pending_step()])
        second = AgentPlan(
            plan_id="plan-2",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.RUNNING,
            public_goal="按新指令修改商品视频",
            created_at=T3,
            updated_at=T3,
        )
        await store.save_plan(
            "user-a",
            second,
            [
                AgentPlanStep(
                    step_id="step-2b",
                    plan_id="plan-2",
                    sequence=2,
                    tool_name="update_scene",
                    title="更新分镜",
                    status=PlanStepStatus.PENDING,
                ),
                AgentPlanStep(
                    step_id="step-2a",
                    plan_id="plan-2",
                    sequence=1,
                    tool_name="inspect_video_workspace",
                    title="读取项目",
                    status=PlanStepStatus.PENDING,
                ),
            ],
        )

        state = await store.load_conversation_state("user-a", "conversation-1")

        assert state is not None
        loaded_workspace, loaded_plan = state
        assert loaded_workspace.workspace_id == "workspace-1"
        assert loaded_plan is not None and loaded_plan.plan_id == "plan-2"
        assert [step.step_id for step in loaded_plan.steps] == ["step-2a", "step-2b"]
        assert await store.load_conversation_state("user-b", "conversation-1") is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_terminal_step_completion_is_idempotent_for_same_result(kind: RepositoryKind) -> None:
    result = VideoToolResult(
        tool_name="inspect_video_workspace",
        public_summary="项目资料已读取",
    )
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])
        await store.start_step("user-a", "plan-1", "step-1", now=T0)

        first = await store.complete_step("user-a", "plan-1", "step-1", result, now=T3)
        second = await store.complete_step("user-a", "plan-1", "step-1", result, now=T3)

        assert second == first
        assert len(await store.list_plan_steps("user-a", "plan-1")) == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_step_transitions_persist_their_public_events_in_order(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, event_repository):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        started, started_event = await store.start_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            run_id="turn-1",
            now=T0,
        )
        completed, completed_event = await store.complete_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
                artifact_refs=("artifact:workspace-1",),
            ),
            run_id="turn-1",
            now=T3,
        )

        events = await event_repository.list_events("user-a", "conversation-1")
        assert started.status is PlanStepStatus.RUNNING
        assert completed.status is PlanStepStatus.COMPLETED
        assert [event.type.value for event in events] == [
            "agent.step.started",
            "agent.step.completed",
        ]
        assert [event.sequence for event in events] == [1, 2]
        assert started_event == events[0]
        assert completed_event == events[1]
        assert completed_event.payload["duration_ms"] == 3000


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_event_conflict_rolls_back_step_transition(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, event_repository):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])
        event_id = f"evt_{uuid5(NAMESPACE_URL, 'pixelflow-video-agent:step-event:plan-1:step-1:started').hex}"
        await event_repository.create_event(
            "user-b",
            AgentEvent(
                event_id=event_id,
                sequence=1,
                cursor="cursor-existing",
                conversation_id="other-conversation",
                run_id="turn-other",
                occurred_at=T0,
                type=AgentEventType.AGENT_STEP_STARTED,
                payload={"plan_id": "other-plan"},
            ),
        )

        with pytest.raises(AgentRuntimeRecordConflictError):
            await store.start_step_with_event(
                "user-a",
                "plan-1",
                "step-1",
                run_id="turn-1",
                now=T0,
            )

        assert (await store.list_plan_steps("user-a", "plan-1"))[0].status is PlanStepStatus.PENDING
        assert await event_repository.list_events("user-a", "conversation-1") == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_step_transition_event_is_idempotent(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, event_repository):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        first = await store.start_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            run_id="turn-1",
            now=T0,
        )
        replay = await store.start_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            run_id="turn-1",
            now=T0,
        )
        completed = await store.complete_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
            ),
            run_id="turn-1",
            now=T3,
        )
        completed_replay = await store.complete_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
            ),
            run_id="turn-1",
            now=T3,
        )

        assert replay == first
        assert completed_replay == completed
        assert len(await event_repository.list_events("user-a", "conversation-1")) == 2


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_plan_read_and_status_update_use_authoritative_steps(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        running = await store.update_plan_status(
            "user-a",
            "plan-1",
            AgentPlanStatus.RUNNING,
            now=T0,
        )
        restored = await store.get_plan("user-a", "plan-1")

        assert running.status is AgentPlanStatus.RUNNING
        assert restored is not None
        assert restored.status is AgentPlanStatus.RUNNING
        assert restored.steps == (pending_step(),)
        assert await store.get_plan("user-b", "plan-1") is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_confirmation_required_step_cannot_start_without_persisted_approval(
    kind: RepositoryKind,
) -> None:
    step = AgentPlanStep(
        step_id="step-confirm",
        plan_id="plan-1",
        sequence=1,
        tool_name="generate_scenes",
        title="生成分镜",
        status=PlanStepStatus.PENDING,
        arguments={"scene_ids": ["scene-3"], "variant_count": 3},
        confirmation_required=True,
    )
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [step])

        with pytest.raises(AgentRuntimeRecordConflictError, match="确认"):
            await store.start_step("user-a", "plan-1", "step-confirm", now=T0)

        waiting = await store.request_step_confirmation(
            "user-a",
            "plan-1",
            "step-confirm",
        )
        running = await store.confirm_step(
            "user-a",
            "plan-1",
            "step-confirm",
            now=T3,
        )
        restored = (await store.list_plan_steps("user-a", "plan-1"))[0]

        assert waiting.status is PlanStepStatus.AWAITING_CONFIRMATION
        assert waiting.started_at is None
        assert running.status is PlanStepStatus.RUNNING
        assert running.started_at == T3
        assert restored.arguments == {"scene_ids": ["scene-3"], "variant_count": 3}
        assert restored.confirmation_required is True
