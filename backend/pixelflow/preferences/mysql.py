"""PixelFlow preference MySQL bootstrap."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from pixelflow.preferences.model import PixelFlowUserPreferenceRow
from pixelflow.preferences.store import SQLUserPreferenceStore

logger = logging.getLogger(__name__)


async def make_mysql_preference_store(url: str, *, echo: bool = False, pool_size: int = 5) -> tuple[SQLUserPreferenceStore, AsyncEngine]:
    if not url.startswith("mysql+"):
        url = url.replace("mysql://", "mysql+asyncmy://", 1)
    engine = create_async_engine(url, echo=echo, pool_size=pool_size, pool_pre_ping=True, json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False))
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: PixelFlowUserPreferenceRow.metadata.create_all(sync_conn, tables=[PixelFlowUserPreferenceRow.__table__]))
    logger.info("PixelFlow MySQL preference table is ready")
    return SQLUserPreferenceStore(async_sessionmaker(engine, expire_on_commit=False)), engine
