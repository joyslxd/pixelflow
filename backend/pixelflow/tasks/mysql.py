"""PixelFlow 业务任务 MySQL 初始化。

PixelFlow 业务数据可以独立放在 MySQL 中，不必和 DeerFlow runtime/checkpointer
数据库共用同一套连接。通过以下环境变量配置：

    PIXELFLOW_MYSQL_URL=mysql+asyncmy://user:password@host:3306/database?charset=utf8mb4
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from pixelflow.tasks.model import (
    PixelFlowAssetRow,
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
    PixelFlowTaskEventRow,
    PixelFlowTaskRow,
)
from pixelflow.tasks.store import SQLPixelFlowTaskStore

logger = logging.getLogger(__name__)

PIXELFLOW_TASK_TABLES = [
    PixelFlowTaskRow.__table__,
    PixelFlowTaskEventRow.__table__,
    PixelFlowAssetRow.__table__,
    PixelFlowConversationRow.__table__,
    PixelFlowConversationMessageRow.__table__,
]


_MICROSECOND_COLUMNS = {
    "pixelflow_tasks": ("created_at", "updated_at"),
    "pixelflow_task_events": ("created_at",),
    "pixelflow_assets": ("created_at", "updated_at"),
    "pixelflow_conversations": ("created_at", "updated_at"),
    "pixelflow_conversation_messages": ("created_at",),
}


async def _mysql_datetime_precision(conn, *, table: str, column: str) -> int | None:
    result = await conn.execute(
        text(
            """
            SELECT DATETIME_PRECISION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table, "column_name": column},
    )
    row = result.first()
    if row is None:
        return None
    precision = row[0]
    return int(precision) if precision is not None else None


async def _ensure_mysql_microsecond_timestamps(conn) -> None:
    if conn.dialect.name not in {"mysql", "mariadb"}:
        return
    for table, columns in _MICROSECOND_COLUMNS.items():
        clauses: list[str] = []
        for column in columns:
            precision = await _mysql_datetime_precision(conn, table=table, column=column)
            if precision != 6:
                clauses.append(f"MODIFY {column} DATETIME(6) NOT NULL")
        if not clauses:
            continue
        try:
            await conn.execute(text(f"ALTER TABLE {table} {', '.join(clauses)}"))
        except Exception:
            logger.warning("Failed to ensure microsecond timestamps for %s", table, exc_info=True)

    result = await conn.execute(text("SHOW INDEX FROM pixelflow_conversations WHERE Key_name = :name"), {"name": "ix_pixelflow_conversations_user_created"})
    if result.first() is None:
        try:
            await conn.execute(
                text(
                    "CREATE INDEX ix_pixelflow_conversations_user_created "
                    "ON pixelflow_conversations (user_id, created_at, conversation_id)"
                )
            )
        except Exception:
            logger.warning("Failed to create pixelflow conversation created_at index", exc_info=True)


async def make_mysql_task_store(url: str, *, echo: bool = False, pool_size: int = 5) -> tuple[SQLPixelFlowTaskStore, AsyncEngine]:
    if not url.startswith("mysql+"):
        url = url.replace("mysql://", "mysql+asyncmy://", 1)
    engine = create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        pool_pre_ping=True,
        json_serializer=lambda obj: __import__("json").dumps(obj, ensure_ascii=False),
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: PixelFlowTaskRow.metadata.create_all(
                sync_conn,
                tables=PIXELFLOW_TASK_TABLES,
            )
        )
        await _ensure_mysql_microsecond_timestamps(conn)
    logger.info("PixelFlow MySQL task tables are ready")
    return SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False)), engine
