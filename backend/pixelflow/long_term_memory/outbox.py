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
    delivery_attempts: int


class SQLWriteOutbox:
    """通过稳定 write_key 去重，并以租约、退避和人工重放支持进程恢复。"""

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
                    or_(
                        (
                            (PixelFlowLongTermMemoryWriteRow.status == "pending")
                            & or_(
                                PixelFlowLongTermMemoryWriteRow.retry_not_before.is_(None),
                                PixelFlowLongTermMemoryWriteRow.retry_not_before <= now,
                            )
                        ),
                        (
                            (PixelFlowLongTermMemoryWriteRow.status == "processing")
                            & (PixelFlowLongTermMemoryWriteRow.lease_expires_at <= now)
                        ),
                    ),
                )
                .order_by(
                    PixelFlowLongTermMemoryWriteRow.retry_not_before,
                    PixelFlowLongTermMemoryWriteRow.created_at,
                )
                .limit(1)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return None
            row.status = "processing"
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            row.delivery_attempts += 1
            row.retry_not_before = None
            await session.commit()
            return MemoryWrite(
                row.write_key,
                row.user_id,
                row.category,
                row.content,
                row.event_id,
                row.delivery_attempts,
            )

    async def save_event_id(self, *, write_key: str, worker_id: str, event_id: str) -> bool:
        """持久化供应商异步事件身份，崩溃恢复时只能轮询同一事件。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return False
            row.event_id = event_id
            row.last_failure_code = None
            row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
            await session.commit()
            return True

    async def retry(
        self,
        *,
        write_key: str,
        worker_id: str,
        retry_after_seconds: float,
        failure_code: str,
    ) -> None:
        """按安全失败码释放租约，并在固定退避窗口后允许同一事件再次轮询。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return
            row.status = "pending"
            row.lease_owner = None
            row.lease_expires_at = None
            row.retry_not_before = datetime.now(UTC) + timedelta(seconds=max(0, retry_after_seconds))
            row.last_failure_code = failure_code[:64]
            await session.commit()

    async def move_to_manual_review(
        self,
        *,
        write_key: str,
        worker_id: str,
        failure_code: str,
    ) -> None:
        """未知提交边界或耗尽轮询次数时停止自动动作，等待人工确认后重放。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return
            row.status = "manual_review"
            row.lease_owner = None
            row.lease_expires_at = None
            row.retry_not_before = None
            row.last_failure_code = failure_code[:64]
            await session.commit()

    async def requeue_manual_review(self, *, user_id: str, write_key: str) -> bool:
        """仅由 owner 显式确认后重放人工审核记录；没有 event_id 时才允许再次提交 add。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.user_id != user_id or row.status != "manual_review":
                return False
            row.status = "pending"
            row.delivery_attempts = 0 if row.event_id is None else 1
            row.retry_not_before = None
            row.last_failure_code = None
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()
            return True

    async def complete(self, *, write_key: str, worker_id: str, event_id: str | None) -> None:
        async with self._session_factory() as session:
            row = await session.get(PixelFlowLongTermMemoryWriteRow, write_key)
            if row is None or row.lease_owner != worker_id:
                return
            row.status = "completed"
            row.event_id = event_id
            row.retry_not_before = None
            row.last_failure_code = None
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()


class MemoryWriteWorker:
    """串行领取 Mem0 写入；只轮询已确认 event，未知提交边界转人工审核。"""

    def __init__(
        self,
        outbox: SQLWriteOutbox,
        adapter: LongTermMemoryPort,
        *,
        worker_id: str,
        retry_initial_seconds: float = 2,
        retry_max_seconds: float = 120,
        max_event_poll_attempts: int = 6,
    ) -> None:
        self._outbox = outbox
        self._adapter = adapter
        self._worker_id = worker_id
        self._retry_initial_seconds = max(0, retry_initial_seconds)
        self._retry_max_seconds = max(self._retry_initial_seconds, retry_max_seconds)
        self._max_event_poll_attempts = max(1, max_event_poll_attempts)
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        """启动单进程领取循环；重复启动不创建第二个投递者。"""

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        self._stopping = True
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stopping:
            if not await self.run_once():
                await asyncio.sleep(0.2)

    async def run_once(self) -> bool:
        """处理一个已持久化写入；测试和重启恢复都复用相同路径。"""

        claimed = await self._outbox.claim(worker_id=self._worker_id, now=datetime.now(UTC))
        if claimed is None:
            return False
        try:
            if claimed.event_id:
                stable = await self._adapter.get_event(
                    event_id=claimed.event_id,
                    user_id=claimed.user_id,
                    content=claimed.content,
                    write_key=claimed.write_key,
                )
                if stable is not None:
                    await self._outbox.complete(
                        write_key=claimed.write_key,
                        worker_id=self._worker_id,
                        event_id=claimed.event_id,
                    )
                else:
                    await self._retry_or_manual_review(
                        claimed,
                        failure_code="mem0_event_pending",
                    )
                return True
            if claimed.delivery_attempts > 1:
                await self._outbox.move_to_manual_review(
                    write_key=claimed.write_key,
                    worker_id=self._worker_id,
                    failure_code="mem0_add_submit_boundary_unknown",
                )
                return True
            event_id = await self._adapter.add(
                user_id=claimed.user_id,
                content=claimed.content,
                category=claimed.category,
                write_key=claimed.write_key,
            )
        except Exception:  # noqa: BLE001 - 未知 add 提交边界绝不能自动重复写入。
            await self._outbox.move_to_manual_review(
                write_key=claimed.write_key,
                worker_id=self._worker_id,
                failure_code="mem0_add_submit_boundary_unknown",
            )
            return True
        if event_id:
            saved = await self._outbox.save_event_id(
                write_key=claimed.write_key,
                worker_id=self._worker_id,
                event_id=event_id,
            )
            if saved:
                await self._outbox.retry(
                    write_key=claimed.write_key,
                    worker_id=self._worker_id,
                    retry_after_seconds=self._retry_delay_seconds(claimed.delivery_attempts),
                    failure_code="mem0_event_pending",
                )
        else:
            await self._outbox.move_to_manual_review(
                write_key=claimed.write_key,
                worker_id=self._worker_id,
                failure_code="mem0_add_event_id_missing",
            )
        return True

    async def _retry_or_manual_review(self, claimed: MemoryWrite, *, failure_code: str) -> None:
        """已知 event 的轮询达到上限后停止，未达到时按指数退避再次检查。"""

        if claimed.delivery_attempts >= self._max_event_poll_attempts:
            await self._outbox.move_to_manual_review(
                write_key=claimed.write_key,
                worker_id=self._worker_id,
                failure_code="mem0_event_poll_attempts_exhausted",
            )
            return
        await self._outbox.retry(
            write_key=claimed.write_key,
            worker_id=self._worker_id,
            retry_after_seconds=self._retry_delay_seconds(claimed.delivery_attempts),
            failure_code=failure_code,
        )

    def _retry_delay_seconds(self, delivery_attempts: int) -> float:
        """对同一 event 使用有上限的指数退避，避免 PENDING 事件占满 Gateway Worker。"""

        exponent = max(0, delivery_attempts - 1)
        return min(self._retry_max_seconds, self._retry_initial_seconds * (2**exponent))
