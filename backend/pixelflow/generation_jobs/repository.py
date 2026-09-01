"""GenerationJob 的幂等创建、租约领取和终态持久化。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pixelflow.agent_control_plane.persistence.models import PixelFlowGenerationJobRow
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRecordConflictError

from .contracts import GenerationJobRecord, GenerationJobStatus


class GenerationJobRepository(Protocol):
    """Gateway GenerationJob Service 依赖的持久化 Port。"""

    async def create_or_read(self, candidate: GenerationJobRecord) -> GenerationJobRecord: ...

    async def claim_start_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[GenerationJobRecord, ...]: ...

    async def bind_provider_job(
        self,
        *,
        generation_job_id: str,
        provider_job_id: str,
        worker_id: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> GenerationJobRecord: ...

    async def claim_poll_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[GenerationJobRecord, ...]: ...

    async def reschedule_poll(
        self,
        *,
        generation_job_id: str,
        worker_id: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> GenerationJobRecord: ...

    async def complete(
        self,
        *,
        generation_job_id: str,
        worker_id: str,
        status: GenerationJobStatus,
        now: datetime,
        result_json: dict[str, object] | None = None,
        failure_reason_code: str | None = None,
    ) -> GenerationJobRecord: ...


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MemoryGenerationJobRepository:
    """测试与本地 memory 模式使用的 GenerationJob Repository。"""

    def __init__(self) -> None:
        self._records: dict[str, GenerationJobRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, generation_job_id: str) -> GenerationJobRecord | None:
        async with self._lock:
            item = self._records.get(generation_job_id)
            return None if item is None else deepcopy(item)

    async def list_all(self) -> tuple[GenerationJobRecord, ...]:
        async with self._lock:
            return tuple(deepcopy(item) for item in self._records.values())

    async def reschedule_poll(
        self,
        *,
        generation_job_id: str,
        worker_id: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> GenerationJobRecord:
        observed_at = _utc(now)
        async with self._lock:
            item = self._get(generation_job_id)
            if not _lease_owned(item, worker_id, observed_at) or item.status is not GenerationJobStatus.POLLING:
                raise AgentRuntimeRecordConflictError("GenerationJob poll lease 无效")
            updated = item.model_copy(
                update={
                    "next_poll_at": _utc(next_poll_at),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": observed_at,
                }
            )
            self._records[generation_job_id] = updated
            return deepcopy(updated)

    async def create_or_read(self, candidate: GenerationJobRecord) -> GenerationJobRecord:
        async with self._lock:
            existing = next(
                (item for item in self._records.values() if item.idempotency_key == candidate.idempotency_key),
                None,
            )
            if existing is not None:
                if not _same_identity(existing, candidate):
                    raise AgentRuntimeRecordConflictError("GenerationJob 幂等键被不同请求占用")
                return deepcopy(existing)
            self._records[candidate.generation_job_id] = candidate
            return deepcopy(candidate)

    async def claim_start_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[GenerationJobRecord, ...]:
        _validate_claim_inputs(worker_id, lease_duration, limit)
        observed_at = _utc(now)
        async with self._lock:
            candidates = [
                item
                for item in self._records.values()
                if item.status is GenerationJobStatus.QUEUED
                or (
                    item.status is GenerationJobStatus.STARTING
                    and item.lease_expires_at is not None
                    and _utc(item.lease_expires_at) <= observed_at
                )
            ][:limit]
            claimed: list[GenerationJobRecord] = []
            for item in candidates:
                updated = item.model_copy(
                    update={
                        "status": GenerationJobStatus.STARTING,
                        "lease_owner": worker_id,
                        "lease_expires_at": observed_at + lease_duration,
                        "updated_at": observed_at,
                    }
                )
                self._records[item.generation_job_id] = updated
                claimed.append(updated)
            return tuple(deepcopy(item) for item in claimed)

    async def bind_provider_job(
        self,
        *,
        generation_job_id: str,
        provider_job_id: str,
        worker_id: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> GenerationJobRecord:
        observed_at = _utc(now)
        async with self._lock:
            item = self._get(generation_job_id)
            if not _lease_owned(item, worker_id, observed_at) or item.status is not GenerationJobStatus.STARTING:
                raise AgentRuntimeRecordConflictError("GenerationJob start lease 无效")
            updated = item.model_copy(
                update={
                    "status": GenerationJobStatus.POLLING,
                    "provider_job_id": provider_job_id,
                    "next_poll_at": _utc(next_poll_at),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": observed_at,
                }
            )
            self._records[generation_job_id] = updated
            return deepcopy(updated)

    async def claim_poll_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[GenerationJobRecord, ...]:
        _validate_claim_inputs(worker_id, lease_duration, limit)
        observed_at = _utc(now)
        async with self._lock:
            candidates = [
                item
                for item in self._records.values()
                if item.status is GenerationJobStatus.POLLING
                and item.next_poll_at is not None
                and _utc(item.next_poll_at) <= observed_at
                and (
                    item.lease_expires_at is None
                    or _utc(item.lease_expires_at) <= observed_at
                )
            ][:limit]
            claimed: list[GenerationJobRecord] = []
            for item in candidates:
                updated = item.model_copy(
                    update={
                        "lease_owner": worker_id,
                        "lease_expires_at": observed_at + lease_duration,
                        "updated_at": observed_at,
                    }
                )
                self._records[item.generation_job_id] = updated
                claimed.append(updated)
            return tuple(deepcopy(item) for item in claimed)

    async def complete(
        self,
        *,
        generation_job_id: str,
        worker_id: str,
        status: GenerationJobStatus,
        now: datetime,
        result_json: dict[str, object] | None = None,
        failure_reason_code: str | None = None,
    ) -> GenerationJobRecord:
        observed_at = _utc(now)
        async with self._lock:
            item = self._get(generation_job_id)
            if not _lease_owned(item, worker_id, observed_at):
                raise AgentRuntimeRecordConflictError("GenerationJob lease 无效")
            updated = item.model_copy(
                update={
                    "status": status,
                    "result_json": result_json,
                    "failure_reason_code": failure_reason_code,
                    "next_poll_at": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": observed_at,
                }
            )
            self._records[generation_job_id] = updated
            return deepcopy(updated)

    def _get(self, generation_job_id: str) -> GenerationJobRecord:
        item = self._records.get(generation_job_id)
        if item is None:
            raise LookupError("GenerationJob 不存在")
        return item


class SQLGenerationJobRepository:
    """SQLite/PostgreSQL GenerationJob Repository，所有领取都在事务内完成。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_read(self, candidate: GenerationJobRecord) -> GenerationJobRecord:
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.scalar(
                        select(PixelFlowGenerationJobRow)
                        .where(PixelFlowGenerationJobRow.idempotency_key == candidate.idempotency_key)
                        .with_for_update()
                    )
                    if row is None:
                        session.add(_row_from_record(candidate))
                        await session.flush()
                        row = await session.scalar(
                            select(PixelFlowGenerationJobRow)
                            .where(PixelFlowGenerationJobRow.generation_job_id == candidate.generation_job_id)
                        )
                    existing = _record_from_row(row)
                    if not _same_identity(existing, candidate):
                        raise AgentRuntimeRecordConflictError("GenerationJob 幂等键被不同请求占用")
                    return existing
            except IntegrityError:
                pass
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PixelFlowGenerationJobRow).where(
                    PixelFlowGenerationJobRow.idempotency_key == candidate.idempotency_key
                )
            )
            existing = _record_from_row(row)
            if not _same_identity(existing, candidate):
                raise AgentRuntimeRecordConflictError("GenerationJob 幂等键被不同请求占用")
            return existing

    async def claim_start_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[GenerationJobRecord, ...]:
        _validate_claim_inputs(worker_id, lease_duration, limit)
        observed_at = _utc(now)
        async with self._session_factory() as session:
            async with session.begin():
                rows = list(
                    (
                        await session.scalars(
                            select(PixelFlowGenerationJobRow)
                            .where(
                                or_(
                                    PixelFlowGenerationJobRow.status == GenerationJobStatus.QUEUED.value,
                                    (
                                        (PixelFlowGenerationJobRow.status == GenerationJobStatus.STARTING.value)
                                        & PixelFlowGenerationJobRow.lease_expires_at.is_not(None)
                                        & (PixelFlowGenerationJobRow.lease_expires_at <= observed_at)
                                    ),
                                )
                            )
                            .order_by(PixelFlowGenerationJobRow.created_at, PixelFlowGenerationJobRow.generation_job_id)
                            .limit(limit)
                            .with_for_update()
                        )
                    ).all()
                )
                claimed: list[GenerationJobRecord] = []
                for row in rows:
                    row.status = GenerationJobStatus.STARTING.value
                    row.lease_owner = worker_id
                    row.lease_expires_at = observed_at + lease_duration
                    row.updated_at = observed_at
                    claimed.append(_record_from_row(row))
                return tuple(claimed)

    async def bind_provider_job(
        self,
        *,
        generation_job_id: str,
        provider_job_id: str,
        worker_id: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> GenerationJobRecord:
        observed_at = _utc(now)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(PixelFlowGenerationJobRow)
                    .where(PixelFlowGenerationJobRow.generation_job_id == generation_job_id)
                    .with_for_update()
                )
                current = _record_from_row(row)
                if not _lease_owned(current, worker_id, observed_at) or current.status is not GenerationJobStatus.STARTING:
                    raise AgentRuntimeRecordConflictError("GenerationJob start lease 无效")
                row.status = GenerationJobStatus.POLLING.value
                row.provider_job_id = provider_job_id
                row.next_poll_at = _utc(next_poll_at)
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = observed_at
                await session.flush()
                return _record_from_row(row)

    async def reschedule_poll(
        self,
        *,
        generation_job_id: str,
        worker_id: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> GenerationJobRecord:
        observed_at = _utc(now)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(PixelFlowGenerationJobRow)
                    .where(PixelFlowGenerationJobRow.generation_job_id == generation_job_id)
                    .with_for_update()
                )
                current = _record_from_row(row)
                if not _lease_owned(current, worker_id, observed_at) or current.status is not GenerationJobStatus.POLLING:
                    raise AgentRuntimeRecordConflictError("GenerationJob poll lease 无效")
                row.next_poll_at = _utc(next_poll_at)
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = observed_at
                await session.flush()
                return _record_from_row(row)

    async def claim_poll_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[GenerationJobRecord, ...]:
        _validate_claim_inputs(worker_id, lease_duration, limit)
        observed_at = _utc(now)
        async with self._session_factory() as session:
            async with session.begin():
                rows = list(
                    (
                        await session.scalars(
                            select(PixelFlowGenerationJobRow)
                            .where(
                                PixelFlowGenerationJobRow.status == GenerationJobStatus.POLLING.value,
                                PixelFlowGenerationJobRow.provider_job_id.is_not(None),
                                PixelFlowGenerationJobRow.next_poll_at.is_not(None),
                                PixelFlowGenerationJobRow.next_poll_at <= observed_at,
                                or_(
                                    PixelFlowGenerationJobRow.lease_expires_at.is_(None),
                                    PixelFlowGenerationJobRow.lease_expires_at <= observed_at,
                                ),
                            )
                            .order_by(PixelFlowGenerationJobRow.next_poll_at, PixelFlowGenerationJobRow.created_at)
                            .limit(limit)
                            .with_for_update()
                        )
                    ).all()
                )
                claimed: list[GenerationJobRecord] = []
                for row in rows:
                    row.lease_owner = worker_id
                    row.lease_expires_at = observed_at + lease_duration
                    row.updated_at = observed_at
                    claimed.append(_record_from_row(row))
                return tuple(claimed)

    async def complete(
        self,
        *,
        generation_job_id: str,
        worker_id: str,
        status: GenerationJobStatus,
        now: datetime,
        result_json: dict[str, object] | None = None,
        failure_reason_code: str | None = None,
    ) -> GenerationJobRecord:
        observed_at = _utc(now)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(PixelFlowGenerationJobRow)
                    .where(PixelFlowGenerationJobRow.generation_job_id == generation_job_id)
                    .with_for_update()
                )
                current = _record_from_row(row)
                if not _lease_owned(current, worker_id, observed_at):
                    raise AgentRuntimeRecordConflictError("GenerationJob lease 无效")
                row.status = status.value
                row.result_json = deepcopy(result_json)
                row.failure_reason_code = failure_reason_code
                row.next_poll_at = None
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = observed_at
                await session.flush()
                return _record_from_row(row)


def _row_from_record(record: GenerationJobRecord) -> PixelFlowGenerationJobRow:
    values = record.model_dump(mode="python")
    values["kind"] = record.kind.value
    values["status"] = record.status.value
    return PixelFlowGenerationJobRow(**values)


def _record_from_row(row: PixelFlowGenerationJobRow | None) -> GenerationJobRecord:
    if row is None:
        raise AgentRuntimeRecordConflictError("GenerationJob 持久化记录不可见")
    values: dict[str, Any] = {
        "generation_job_id": row.generation_job_id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "workspace_id": row.workspace_id,
        "kind": row.kind,
        "item_id": row.item_id,
        "variant_index": row.variant_index,
        "status": row.status,
        "request_json": row.request_json or {},
        "request_hash": row.request_hash,
        "idempotency_key": row.idempotency_key,
        "provider_id": row.provider_id,
        "provider_job_id": row.provider_job_id,
        "result_json": row.result_json,
        "failure_reason_code": row.failure_reason_code,
        "next_poll_at": row.next_poll_at,
        "lease_owner": row.lease_owner,
        "lease_expires_at": row.lease_expires_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    return GenerationJobRecord.model_validate(values)


def _same_identity(left: GenerationJobRecord, right: GenerationJobRecord) -> bool:
    return (
        left.user_id == right.user_id
        and left.conversation_id == right.conversation_id
        and left.workspace_id == right.workspace_id
        and left.kind is right.kind
        and left.item_id == right.item_id
        and left.variant_index == right.variant_index
        and left.request_hash == right.request_hash
        and left.provider_id == right.provider_id
    )


def _lease_owned(item: GenerationJobRecord, worker_id: str, now: datetime) -> bool:
    return (
        item.lease_owner == worker_id
        and item.lease_expires_at is not None
        and _utc(item.lease_expires_at) > now
    )


def _validate_claim_inputs(worker_id: str, lease_duration: timedelta, limit: int) -> None:
    if not worker_id.strip() or lease_duration <= timedelta(0) or limit < 1 or limit > 6:
        raise ValueError("GenerationJob Worker 参数无效")


__all__ = ["GenerationJobRepository", "MemoryGenerationJobRepository", "SQLGenerationJobRepository"]
