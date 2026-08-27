"""验证 Plan 独立 revision 的迁移后 Repository CAS 语义。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.platform.persistence import Base, ensure_schema
from pixelflow.video.contracts import AgentPlan, AgentPlanStatus, VideoWorkspace
from pixelflow.video.workspace import MemoryVideoAgentRepository, SQLVideoAgentRepository


def _workspace(now: datetime) -> VideoWorkspace:
    return VideoWorkspace(
        workspace_id="plan-revision-workspace",
        conversation_id="plan-revision-conversation",
        revision=1,
        payload={},
        created_at=now,
        updated_at=now,
    )


def _plan(now: datetime, *, public_goal: str | None = "旧目标") -> AgentPlan:
    return AgentPlan(
        plan_id="plan-revision-plan",
        workspace_id="plan-revision-workspace",
        conversation_id="plan-revision-conversation",
        status=AgentPlanStatus.RUNNING,
        public_goal=public_goal,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_memory_plan_public_goal_uses_expected_revision_cas() -> None:
    """内存 Repository 与 SQL 版本合同一致：成功递增，旧版本冲突。"""

    repository = MemoryVideoAgentRepository()
    now = datetime.now(UTC)
    await repository.create_workspace("plan-user", _workspace(now))
    created = await repository.save_plan("plan-user", _plan(now), [])

    updated = await repository.update_plan_public_goal(
        "plan-user",
        created.plan_id,
        "新目标",
        expected_revision=created.revision,
        now=now,
    )

    assert updated.revision == 2
    assert updated.public_goal == "新目标"
    with pytest.raises(AgentRuntimeRecordConflictError):
        await repository.update_plan_public_goal(
            "plan-user",
            created.plan_id,
            "过期写入",
            expected_revision=1,
            now=now,
        )


@pytest.mark.asyncio
async def test_sql_save_plan_update_and_public_goal_are_revision_cas(tmp_path) -> None:
    """SQL Repository 的 save 与专用目标更新都不能覆盖并发版本。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plan-revision.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLVideoAgentRepository(session_factory)
    now = datetime.now(UTC)
    try:
        await repository.create_workspace("plan-user", _workspace(now))
        created = await repository.save_plan("plan-user", _plan(now), [])
        saved = await repository.save_plan(
            "plan-user",
            _plan(now, public_goal="save 更新"),
            [],
            expected_revision=created.revision,
        )
        assert saved.revision == 2
        assert saved.public_goal == "save 更新"
        updated = await repository.update_plan_public_goal(
            "plan-user",
            created.plan_id,
            "API 更新",
            expected_revision=saved.revision,
            now=now,
        )
        assert updated.revision == 3
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.save_plan(
                "plan-user",
                _plan(now, public_goal="过期 save"),
                [],
                expected_revision=1,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_schema_adds_revision_to_legacy_sqlite_plan_table(tmp_path) -> None:
    """部署升级时，既有 SQLite Plan 表也必须获得非空的第一版 revision。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-plan.db'}")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE pixelflow_video_agent_plans ("
                    "plan_id VARCHAR(64) PRIMARY KEY, workspace_id VARCHAR(64) NOT NULL, "
                    "conversation_id VARCHAR(64) NOT NULL, user_id VARCHAR(64) NOT NULL, "
                    "status VARCHAR(32) NOT NULL, public_goal TEXT, "
                    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
                )
            )
        await ensure_schema(engine)
        async with engine.connect() as connection:
            rows = await connection.exec_driver_sql("PRAGMA table_info(pixelflow_video_agent_plans)")
            revision = next(row for row in rows.all() if row[1] == "revision")
    finally:
        await engine.dispose()

    assert revision[3] == 1
    assert revision[4] == "1"
