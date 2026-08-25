"""Mem0 写入的持久化 Outbox 与单进程恢复 Worker。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from pixelflow.agent_control_plane.persistence.models import PixelFlowLongTermMemoryWriteRow

from .service import LongTermMemoryPort


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    """已领取写入的最小恢复载荷。"""

    write_key: str
    user_id: str
    category: str
    content: str
    event_id: str | None


class SQLWriteOutbox:
    """通过稳定 write_key 去重，并以过期租约支持进程恢复。"""

    def __init__(self, session_factory: async_sessionmaker, *, lease_seconds: int = 30) -> None:
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds

    async def enqueue(self, *, user_id: str, content: str, category: str, write_key: str) -> None:
        async with self._session_factory() as session:
            existing = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if existing is None:
                session.add(PixelFlowLongTermMemoryWriteRow(write_key=write_key, user_id=user_id, category=category, content=content))
                await session.commit()
                return
            if (existing.user_id, existing.category, existing.content) != (user_id, category, content):
                raise ValueError("memory_write_key_conflict")

    async def claim(self, *, worker_id: str, now: datetime) -> MemoryWrite | None:
        async with self._session_factory() as session:
            statement = (
                select(PixelFlowLongTermMemoryWriteRow)
                .where(
                    PixelFlowLongTermMemoryWriteRow.status.in_(("pending", "processing")),
                    or_(
                        PixelFlowLongTermMemoryWriteRow.lease_expires_at.is_(None),
                        PixelFlowLongTermMemoryWriteRow.lease_expires_at <= now,
                    ),
                )
                .order_by(PixelFlowLongTermMemoryWriteRow.created_at)
                .limit(1)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return None
            row.status = "processing"
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            row.delivery_attempts += 1
            await session.commit()
            return MemoryWrite(
                row.write_key,
                row.user_id,
                row.category,
                row.content,
                row.event_id,
            )

    async def save_event_id(self, *, write_key: str, worker_id: str, event_id: str) -> bool:
        """持久化供应商异步事件身份，崩溃恢复时只能轮询同一事件。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return False
            row.event_id = event_id
            row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
            await session.commit()
            return True

    async def release(self, *, write_key: str, worker_id: str) -> None:
        """释放未稳定事件的本轮租约，使下次轮询或新进程继续查询同一 event_id。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()

    async def complete(self, *, write_key: str, worker_id: str, event_id: str | None) -> None:
        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return
            row.status = "completed"
            row.event_id = event_id
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()


class MemoryWriteWorker:
    """串行领取 Mem0 写入，异常时保留租约等待下一实例接管。"""

    def __init__(self, outbox: SQLWriteOutbox, adapter: LongTermMemoryPort, *, worker_id: str) -> None:
        self._outbox = outbox
        self._adapter = adapter
        self._worker_id = worker_id
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        self._stopping = True
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stopping:
            claimed = await self._outbox.claim(worker_id=self._worker_id, now=datetime.now(UTC))
            if claimed is None:
                await asyncio.sleep(0.2)
                continue
            try:
                if claimed.event_id:
                    stable = await self._adapter.get(memory_id=claimed.event_id)
                    if stable is not None:
                        await self._outbox.complete(
                            write_key=claimed.write_key,
                            worker_id=self._worker_id,
                            event_id=claimed.event_id,
                        )
                    else:
                        await self._outbox.release(
                            write_key=claimed.write_key,
                            worker_id=self._worker_id,
                        )
                        await asyncio.sleep(0.2)
                    continue
                event_id = await self._adapter.add(
                    user_id=claimed.user_id,
                    content=claimed.content,
                    category=claimed.category,
                    write_key=claimed.write_key,
                )
            except Exception:
                await self._outbox.release(write_key=claimed.write_key, worker_id=self._worker_id)
                continue
            if event_id:
                await self._outbox.save_event_id(
                    write_key=claimed.write_key,
                    worker_id=self._worker_id,
                    event_id=event_id,
                )
            else:
                await self._outbox.release(write_key=claimed.write_key, worker_id=self._worker_id)
                await asyncio.sleep(0.2)
