"""PixelFlow 业务任务 MySQL 初始化。

PixelFlow 业务数据可以独立放在 MySQL 中，不必和 DeerFlow runtime/checkpointer
数据库共用同一套连接。通过以下环境变量配置：

    PIXELFLOW_MYSQL_URL=mysql+asyncmy://user:password@host:3306/database?charset=utf8mb4
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from pixelflow.tasks.model import PixelFlowAssetRow, PixelFlowTaskEventRow, PixelFlowTaskRow
from pixelflow.tasks.store import SQLPixelFlowTaskStore

logger = logging.getLogger(__name__)


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
                tables=[PixelFlowTaskRow.__table__, PixelFlowTaskEventRow.__table__, PixelFlowAssetRow.__table__],
            )
        )
    logger.info("PixelFlow MySQL task tables are ready")
    return SQLPixelFlowTaskStore(async_sessionmaker(engine, expire_on_commit=False)), engine
