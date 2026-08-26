"""验证 M1 PixelFlow 自有 ORM Base 与数据库引擎生命周期。"""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, String, inspect, select, text
from sqlalchemy.orm import Mapped, mapped_column

from pixelflow.platform.persistence import Base, close_engine, ensure_schema, get_engine, get_session_factory, init_engine


class _PlatformPersistenceProbeRow(Base):
    """仅用于确认自有 Base metadata 与异步会话可正常配合。"""

    __tablename__ = "platform_persistence_probe"

    identifier: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.mark.asyncio
async def test_platform_persistence_owns_sqlite_engine_and_metadata(tmp_path) -> None:
    """自有引擎必须能创建自有 Base 的表，并在关闭后清空全局引用。"""

    await init_engine(
        backend="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'platform.db'}",
        sqlite_dir=str(tmp_path),
    )
    engine = get_engine()
    factory = get_session_factory()
    assert engine is not None
    assert factory is not None
    try:
        await ensure_schema(engine)
        async with factory() as session:
            session.add(_PlatformPersistenceProbeRow(identifier=1, label="M1"))
            await session.commit()
        async with factory() as session:
            result = await session.execute(select(_PlatformPersistenceProbeRow))
            assert result.scalar_one().to_dict() == {"identifier": 1, "label": "M1"}
    finally:
        await close_engine()
    assert get_engine() is None
    assert get_session_factory() is None


@pytest.mark.asyncio
async def test_ensure_schema_registers_control_plane_workspace_tables(tmp_path) -> None:
    """Gateway 先建库再装配 Repository 时，VideoWorkspace 表仍必须已创建。"""

    await init_engine(
        backend="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'control-plane.db'}",
        sqlite_dir=str(tmp_path),
    )
    engine = get_engine()
    assert engine is not None
    try:
        await ensure_schema(engine)
        async with engine.begin() as connection:
            table_names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert "pixelflow_video_agent_workspaces" in table_names
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_ensure_schema_upgrades_legacy_mem0_writeoutbox_to_manual_review(tmp_path) -> None:
    """旧 SQLite Outbox 必须在启动时升级 CHECK 约束和退避字段，避免人工重放被旧表拒绝。"""

    await init_engine(
        backend="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'legacy-memory.db'}",
        sqlite_dir=str(tmp_path),
    )
    engine = get_engine()
    assert engine is not None
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TABLE pixelflow_long_term_memory_writes (
                    write_key VARCHAR(128) NOT NULL PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    category VARCHAR(32) NOT NULL,
                    content TEXT NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    event_id VARCHAR(128),
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner VARCHAR(128),
                    lease_expires_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT ck_pf_ltm_writes_status CHECK (status IN ('pending', 'processing', 'completed'))
                )
                """
            )
            await connection.execute(
                text(
                    "INSERT INTO pixelflow_long_term_memory_writes "
                    "(write_key, user_id, category, content, status, delivery_attempts, created_at, updated_at) "
                    "VALUES ('legacy-write', 'owner', 'preference', '安全内容', 'pending', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        await ensure_schema(engine)
        async with engine.begin() as connection:
            columns = await connection.run_sync(
                lambda sync: {item["name"] for item in inspect(sync).get_columns("pixelflow_long_term_memory_writes")}
            )
            assert {"retry_not_before", "last_failure_code"}.issubset(columns)
            await connection.execute(
                text(
                    "UPDATE pixelflow_long_term_memory_writes "
                    "SET status = 'manual_review', last_failure_code = 'mem0_event_poll_attempts_exhausted' "
                    "WHERE write_key = 'legacy-write'"
                )
            )
    finally:
        await close_engine()
