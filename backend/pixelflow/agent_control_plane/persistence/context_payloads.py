"""为 Context 外置载荷提供 Memory/SQL 幂等持久化。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import PixelFlowAgentContextPayloadRow
from .repositories import (
    AgentRuntimeRecordConflictError,
    _repository_write_transaction,
)

if TYPE_CHECKING:
    from ..context.externalizer import ContextPayloadRecord


def _payload_id(record: ContextPayloadRecord) -> str:
    """从完整幂等复合键生成不暴露业务内容的稳定主键。"""

    encoded = json.dumps(
        list(record.storage_identity),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _external_ref(payload_id: str) -> str:
    return f"context-payload:{payload_id}"


def _same_record(
    row: PixelFlowAgentContextPayloadRow,
    record: ContextPayloadRecord,
) -> bool:
    return (
        row.user_id,
        row.conversation_id,
        row.source_kind,
        row.source_ref,
        row.content_hash,
        row.original_bytes,
        row.payload_json,
    ) == (
        record.user_id,
        record.conversation_id,
        record.source_kind,
        record.source_ref,
        record.content_hash,
        record.original_bytes,
        record.payload,
    )


class MemoryContextPayloadStore:
    """进程内开发模式也按生产复合键执行幂等 upsert。"""

    def __init__(self) -> None:
        self._records: dict[str, ContextPayloadRecord] = {}
        self._lock = asyncio.Lock()

    async def save_context_payload(
        self,
        record: ContextPayloadRecord,
    ) -> str:
        payload_id = _payload_id(record)
        async with self._lock:
            existing = self._records.get(payload_id)
            if existing is not None and existing.model_dump(mode="python") != record.model_dump(mode="python"):
                raise AgentRuntimeRecordConflictError(
                    "Context 载荷主键与已保存内容冲突",
                )
            self._records[payload_id] = deepcopy(record)
        return _external_ref(payload_id)

    async def get_context_payload(
        self,
        payload_id: str,
    ) -> ContextPayloadRecord | None:
        """只供恢复校验和非付费测试读取完整载荷。"""

        async with self._lock:
            record = self._records.get(payload_id)
            return None if record is None else deepcopy(record)


class SQLContextPayloadStore:
    """在同一 PixelFlow 数据库中保存可跨进程恢复的完整载荷。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory
        self._sqlite_write_lock = asyncio.Lock()

    async def save_context_payload(
        self,
        record: ContextPayloadRecord,
    ) -> str:
        payload_id = _payload_id(record)
        async with self._session_factory() as session:
            async with _repository_write_transaction(
                session,
                self._sqlite_write_lock,
            ):
                statement = (
                    select(PixelFlowAgentContextPayloadRow)
                    .where(
                        PixelFlowAgentContextPayloadRow.payload_id == payload_id,
                    )
                    .with_for_update()
                )
                existing = (await session.scalars(statement)).one_or_none()
                if existing is None:
                    session.add(
                        PixelFlowAgentContextPayloadRow(
                            payload_id=payload_id,
                            conversation_id=record.conversation_id,
                            user_id=record.user_id,
                            source_kind=record.source_kind,
                            source_ref=record.source_ref,
                            content_hash=record.content_hash,
                            original_bytes=record.original_bytes,
                            payload_json=deepcopy(record.payload),
                        ),
                    )
                    await session.flush()
                elif not _same_record(existing, record):
                    raise AgentRuntimeRecordConflictError(
                        "Context 载荷主键与已保存内容冲突",
                    )
        return _external_ref(payload_id)

    async def get_context_payload(
        self,
        payload_id: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> ContextPayloadRecord | None:
        """按 owner 和 conversation 读取外置载荷，拒绝跨用户引用。"""

        from ..context.externalizer import ContextPayloadRecord

        statement = select(PixelFlowAgentContextPayloadRow).where(
            PixelFlowAgentContextPayloadRow.payload_id == payload_id,
            PixelFlowAgentContextPayloadRow.user_id == user_id,
            PixelFlowAgentContextPayloadRow.conversation_id == conversation_id,
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        if row is None:
            return None
        return ContextPayloadRecord(
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            source_kind=row.source_kind,
            source_ref=row.source_ref,
            content_hash=row.content_hash,
            original_bytes=row.original_bytes,
            payload=deepcopy(row.payload_json),
        )
