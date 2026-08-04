from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
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
async def repository(kind: RepositoryKind) -> AsyncIterator[VideoAgentRepository]:
    if kind == "memory":
        yield MemoryVideoAgentRepository()
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield SQLVideoAgentRepository(async_sessionmaker(engine, expire_on_commit=False))
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
    async with repository(kind) as store:
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
async def test_terminal_step_completion_is_idempotent_for_same_result(kind: RepositoryKind) -> None:
    result = VideoToolResult(
        tool_name="inspect_video_workspace",
        public_summary="项目资料已读取",
    )
    async with repository(kind) as store:
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])
        await store.start_step("user-a", "plan-1", "step-1", now=T0)

        first = await store.complete_step("user-a", "plan-1", "step-1", result, now=T3)
        second = await store.complete_step("user-a", "plan-1", "step-1", result, now=T3)

        assert second == first
        assert len(await store.list_plan_steps("user-a", "plan-1")) == 1
