"""管理 PixelFlow 自有异步数据库引擎与会话工厂生命周期。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import event
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

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


__all__ = ["close_engine", "ensure_schema", "get_engine", "get_session_factory", "init_engine"]
