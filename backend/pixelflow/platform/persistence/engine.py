"""管理 PixelFlow 自有异步数据库引擎与会话工厂生命周期。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .base import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _json_serializer(value: object) -> str:
    """保留中文字符的 JSON 序列化，避免数据库内出现不可读转义文本。"""

    return json.dumps(value, ensure_ascii=False)


async def init_engine(
    *,
    backend: str,
    url: str = "",
    echo: bool = False,
    pool_size: int = 5,
    sqlite_dir: str = "",
) -> None:
    """创建 PixelFlow 引擎；memory 模式不创建连接，调用方应显式使用内存 Repository。"""

    global _engine, _session_factory
    if backend == "memory":
        await close_engine()
        return
    if not url:
        raise ValueError("数据库 URL 不能为空")
    await close_engine()
    if backend == "sqlite":
        Path(sqlite_dir or ".").mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(url, echo=echo, json_serializer=_json_serializer)

        @event.listens_for(_engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            """为每个 SQLite 连接启用 WAL、外键与安全同步策略。"""

            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
            finally:
                cursor.close()
    elif backend == "postgres":
        _engine = create_async_engine(
            url,
            echo=echo,
            pool_size=pool_size,
            pool_pre_ping=True,
            json_serializer=_json_serializer,
        )
    else:
        raise ValueError("数据库 backend 只允许 memory、sqlite 或 postgres")
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info("PixelFlow 自有持久化引擎已初始化：backend=%s", backend)


def get_engine() -> AsyncEngine | None:
    """返回当前自有引擎；memory 模式或关闭后返回 None。"""

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """返回当前自有会话工厂；调用方必须处理 None。"""

    return _session_factory


async def close_engine() -> None:
    """关闭自有连接池；不会触碰仍由 DeerFlow 管理的旧引擎。"""

    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def ensure_schema(engine: AsyncEngine) -> None:
    """在既有数据库上幂等创建 PixelFlow 自有 ORM 表，不管理 DeerFlow 平台表。"""

    # 用途：在建表前注册控制面 ORM；影响：Workspace、Run/Event 等模型即使尚未被
    # Gateway 后续装配代码导入，也会进入同一 PixelFlow Base 的 metadata。
    from pixelflow.agent_control_plane.persistence import models as _control_plane_models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await _migrate_video_plan_revision_schema(connection)
        await _migrate_long_term_memory_write_schema(connection)


async def _migrate_video_plan_revision_schema(connection) -> None:
    """为已存在的 Plan 表补 revision，旧记录统一从第一版开始。"""

    table_name = "pixelflow_video_agent_plans"
    if connection.dialect.name == "sqlite":
        rows = await connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
        columns = {str(row[1]) for row in rows.all()}
        if columns and "revision" not in columns:
            await connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        return
    if connection.dialect.name == "postgresql":
        await connection.execute(
            text(
                "ALTER TABLE pixelflow_video_agent_plans "
                "ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1"
            )
        )


async def _migrate_long_term_memory_write_schema(connection) -> None:
    """升级 Mem0 WriteOutbox 的失败状态字段；SQLite 需重建旧 CHECK 约束表。"""

    dialect = connection.dialect.name
    table_name = "pixelflow_long_term_memory_writes"
    if dialect == "sqlite":
        row = await connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": table_name},
        )
        definition = row.scalar_one_or_none()
        if not definition:
            return
        columns = {
            item[1]
            for item in (
                await connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
            ).all()
        }
        if {
            "retry_not_before",
            "last_failure_code",
        }.issubset(columns) and "manual_review" in definition:
            return
        await connection.exec_driver_sql(
            """
            CREATE TABLE pixelflow_long_term_memory_writes_next (
                write_key VARCHAR(128) NOT NULL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                category VARCHAR(32) NOT NULL,
                content TEXT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                event_id VARCHAR(128),
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                retry_not_before DATETIME,
                last_failure_code VARCHAR(64),
                lease_owner VARCHAR(128),
                lease_expires_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT ck_pf_ltm_writes_status
                    CHECK (status IN ('pending', 'processing', 'completed', 'manual_review'))
            )
            """
        )
        await connection.exec_driver_sql(
            """
            INSERT INTO pixelflow_long_term_memory_writes_next (
                write_key, user_id, category, content, status, event_id,
                delivery_attempts, lease_owner, lease_expires_at, created_at, updated_at
            )
            SELECT write_key, user_id, category, content, status, event_id,
                delivery_attempts, lease_owner, lease_expires_at, created_at, updated_at
            FROM pixelflow_long_term_memory_writes
            """
        )
        await connection.exec_driver_sql("DROP TABLE pixelflow_long_term_memory_writes")
        await connection.exec_driver_sql(
            "ALTER TABLE pixelflow_long_term_memory_writes_next RENAME TO pixelflow_long_term_memory_writes"
        )
        await connection.exec_driver_sql(
            "CREATE INDEX ix_pf_ltm_writes_due ON pixelflow_long_term_memory_writes "
            "(status, retry_not_before, lease_expires_at, created_at)"
        )
        logger.info("PixelFlow Mem0 WriteOutbox SQLite schema 已升级")
        return
    if dialect == "postgresql":
        await connection.execute(
            text(
                "ALTER TABLE pixelflow_long_term_memory_writes "
                "ADD COLUMN IF NOT EXISTS retry_not_before TIMESTAMP WITH TIME ZONE"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE pixelflow_long_term_memory_writes "
                "ADD COLUMN IF NOT EXISTS last_failure_code VARCHAR(64)"
            )
        )
        await connection.execute(
            text("ALTER TABLE pixelflow_long_term_memory_writes DROP CONSTRAINT IF EXISTS ck_pf_ltm_writes_status")
        )
        await connection.execute(
            text(
                "ALTER TABLE pixelflow_long_term_memory_writes "
                "ADD CONSTRAINT ck_pf_ltm_writes_status "
                "CHECK (status IN ('pending', 'processing', 'completed', 'manual_review'))"
            )
        )


__all__ = ["close_engine", "ensure_schema", "get_engine", "get_session_factory", "init_engine"]
