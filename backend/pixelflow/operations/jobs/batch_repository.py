"""M5 OperationBatch SQL 与内存 Repository，负责批次槽位与终态聚合。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pixelflow.agent_control_plane.persistence.models import (
    PixelFlowOperationBatchChildRow,
    PixelFlowOperationBatchOutboxRow,
    PixelFlowOperationBatchRow,
)
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRecordConflictError

from .batch import OperationBatchPlan, build_operation_batch_completion_event_id

ChildStatus = Literal["queued", "starting", "polling", "succeeded", "failed", "timeout", "expired"]
_TERMINAL = frozenset({"succeeded", "failed", "timeout", "expired"})


@dataclass(frozen=True, slots=True)
class OperationBatchChildRecord:
    operation_idempotency_key: str
    scene_id: str
    variant_index: int
    status: ChildStatus
    job_id: str | None


@dataclass(frozen=True, slots=True)
class OperationBatchRecord:
    batch_id: str
    user_id: str
    conversation_id: str
    workspace_id: str
    run_id: str | None
    tool_call_id: str | None
    attempt: int | None
    source_workspace_revision: int | None
    idempotency_key: str
    status: str
    completion_event_id: str | None
    children: tuple[OperationBatchChildRecord, ...]


@dataclass(frozen=True, slots=True)
class OperationBatchOutboxRecord:
    completion_event_id: str
    batch_id: str
    user_id: str
    conversation_id: str
    workspace_id: str
    resume_run_id: str | None


class OperationBatchRepository(Protocol):
    async def create_or_read(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        plan: OperationBatchPlan,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        attempt: int | None = None,
        source_workspace_revision: int | None = None,
    ) -> OperationBatchRecord: ...
    async def claim_children(self, *, batch_id: str, max_concurrent: int) -> tuple[OperationBatchChildRecord, ...]: ...
    async def list_dispatchable_batches(self, *, limit: int) -> tuple[OperationBatchRecord, ...]: ...

    async def mark_child_polling(self, *, batch_id: str, child_key: str, job_id: str) -> OperationBatchRecord: ...
    async def mark_child_terminal(self, *, batch_id: str, child_key: str, status: ChildStatus, job_id: str) -> OperationBatchRecord: ...

    async def get_batch_for_child_job(self, *, user_id: str, conversation_id: str, job_id: str) -> OperationBatchRecord | None: ...
    async def get_batch(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        batch_id: str,
    ) -> OperationBatchRecord | None: ...


class MemoryOperationBatchRepository:
    """测试用内存实现，与 SQL 的领取和终态规则保持一致。"""

    def __init__(self) -> None:
        self._records: dict[str, OperationBatchRecord] = {}
        self._lock = asyncio.Lock()

    async def create_or_read(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        plan: OperationBatchPlan,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        attempt: int | None = None,
        source_workspace_revision: int | None = None,
    ) -> OperationBatchRecord:
        candidate = OperationBatchRecord(
            plan.batch_id,
            user_id,
            conversation_id,
            workspace_id,
            run_id,
            tool_call_id,
            attempt,
            source_workspace_revision,
            plan.batch_idempotency_key,
            "queued",
            None,
            tuple(OperationBatchChildRecord(child.operation_idempotency_key, child.scene_id, child.variant_index, "queued", None) for child in plan.children),
        )
        async with self._lock:
            existing = self._records.get(plan.batch_idempotency_key)
            if existing is None:
                self._records[plan.batch_idempotency_key] = candidate
                return candidate
            if (
                existing.user_id != candidate.user_id
                or
                existing.batch_id != candidate.batch_id
                or existing.workspace_id != candidate.workspace_id
                or existing.run_id != candidate.run_id
                or existing.tool_call_id != candidate.tool_call_id
                or existing.attempt != candidate.attempt
                or existing.source_workspace_revision != candidate.source_workspace_revision
                or tuple(
                    (child.operation_idempotency_key, child.scene_id, child.variant_index)
                    for child in existing.children
                )
                != tuple(
                    (child.operation_idempotency_key, child.scene_id, child.variant_index)
                    for child in candidate.children
                )
            ):
                raise AgentRuntimeRecordConflictError("OperationBatch 幂等键被不同请求占用")
            return existing

    async def claim_children(self, *, batch_id: str, max_concurrent: int) -> tuple[OperationBatchChildRecord, ...]:
        if max_concurrent < 1:
            raise ValueError("批次并发槽位必须为正整数")
        async with self._lock:
            record = self._by_id(batch_id)
            # starting 无批次级 lease：重启或进程崩溃后可以安全重领。真正的
            # Provider 去重由同一 child key 对应的 M06 Operation start lease 保证。
            active = sum(child.status == "polling" for child in record.children)
            slots = max(0, max_concurrent - active)
            claimed: list[OperationBatchChildRecord] = []
            children: list[OperationBatchChildRecord] = []
            for child in record.children:
                if child.status in {"queued", "starting"} and len(claimed) < slots:
                    child = OperationBatchChildRecord(child.operation_idempotency_key, child.scene_id, child.variant_index, "starting", child.job_id)
                    claimed.append(child)
                children.append(child)
            self._replace(record, children=tuple(children), status="running" if claimed else record.status)
            return tuple(claimed)

    async def list_dispatchable_batches(self, *, limit: int) -> tuple[OperationBatchRecord, ...]:
        if limit < 1:
            raise ValueError("批次扫描上限必须为正整数")
        async with self._lock:
            return tuple(
                record
                for record in self._records.values()
                if record.status in {"queued", "running"}
                and any(child.status in {"queued", "starting"} for child in record.children)
            )[:limit]

    async def mark_child_terminal(self, *, batch_id: str, child_key: str, status: ChildStatus, job_id: str) -> OperationBatchRecord:
        if status not in _TERMINAL:
            raise ValueError("子 Operation 必须以终态聚合")
        async with self._lock:
            record = self._by_id(batch_id)
            existing = next((child for child in record.children if child.operation_idempotency_key == child_key), None)
            if existing is None:
                raise LookupError("批次子 Operation 不存在")
            if existing.status in _TERMINAL:
                placeholder = existing.status == "failed" and existing.job_id == child_key
                if not placeholder:
                    if existing.status != status or existing.job_id != job_id:
                        raise AgentRuntimeRecordConflictError("子 Operation 终态漂移")
                    return record
            children = tuple(OperationBatchChildRecord(child.operation_idempotency_key, child.scene_id, child.variant_index, status, job_id) if child.operation_idempotency_key == child_key else child for child in record.children)
            completed = all(child.status in _TERMINAL for child in children)
            return self._replace(record, children=children, status="completed" if completed else "running", completion_event_id=build_operation_batch_completion_event_id(record.batch_id) if completed else None)

    async def mark_child_polling(
        self,
        *,
        batch_id: str,
        child_key: str,
        job_id: str,
    ) -> OperationBatchRecord:
        """把已获 M06 start lease 的子项绑定到权威 Operation Job。"""

        if not job_id.strip():
            raise ValueError("子 Operation job_id 不能为空")
        async with self._lock:
            record = self._by_id(batch_id)
            existing = next(
                (
                    child
                    for child in record.children
                    if child.operation_idempotency_key == child_key
                ),
                None,
            )
            if existing is None:
                raise LookupError("批次子 Operation 不存在")
            if existing.status in _TERMINAL and not (
                existing.status == "failed" and existing.job_id == child_key
            ):
                raise AgentRuntimeRecordConflictError("终态子 Operation 不能重新轮询")
            if existing.job_id is not None and existing.job_id != job_id:
                raise AgentRuntimeRecordConflictError("子 Operation 绑定了不同 Job")
            children = tuple(
                OperationBatchChildRecord(
                    child.operation_idempotency_key,
                    child.scene_id,
                    child.variant_index,
                    "polling" if child.operation_idempotency_key == child_key else child.status,
                    job_id if child.operation_idempotency_key == child_key else child.job_id,
                )
                for child in record.children
            )
            return self._replace(record, children=children, status="running")

    def _by_id(self, batch_id: str) -> OperationBatchRecord:
        for record in self._records.values():
            if record.batch_id == batch_id:
                return record
        raise LookupError("OperationBatch 不存在")

    async def get_batch_for_child_job(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ) -> OperationBatchRecord | None:
        """按 M06 Job 回读所属批次，终态回调不得猜测批次或工作区。"""

        async with self._lock:
            for record in self._records.values():
                if (
                    record.user_id == user_id
                    and record.conversation_id == conversation_id
                    and any(child.job_id == job_id for child in record.children)
                ):
                    return record
            return None

    async def get_batch(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        batch_id: str,
    ) -> OperationBatchRecord | None:
        """按权威 owner、会话和工作区读取批次，拒绝跨 Workspace 枚举。"""

        async with self._lock:
            for record in self._records.values():
                if (
                    record.batch_id == batch_id
                    and record.user_id == user_id
                    and record.conversation_id == conversation_id
                    and record.workspace_id == workspace_id
                ):
                    return record
            return None

    def _replace(self, record: OperationBatchRecord, **changes: object) -> OperationBatchRecord:
        updated = replace(record, **changes)
        self._records[updated.idempotency_key] = updated
        return updated


class SQLOperationBatchRepository:
    """SQL 实现以事务领取子项；子项终态只更新批次统计及唯一完成事件。"""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_or_read(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        plan: OperationBatchPlan,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        attempt: int | None = None,
        source_workspace_revision: int | None = None,
    ) -> OperationBatchRecord:
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.get(PixelFlowOperationBatchRow, plan.batch_id)
                    if row is None:
                        session.add(
                            PixelFlowOperationBatchRow(
                                batch_id=plan.batch_id,
                                user_id=user_id,
                                conversation_id=conversation_id,
                                workspace_id=workspace_id,
                                run_id=run_id,
                                tool_call_id=tool_call_id,
                                attempt=attempt,
                                source_workspace_revision=source_workspace_revision,
                                idempotency_key=plan.batch_idempotency_key,
                            )
                        )
                        session.add_all(
                            [PixelFlowOperationBatchChildRow(batch_id=plan.batch_id, operation_idempotency_key=child.operation_idempotency_key, scene_id=child.scene_id, variant_index=child.variant_index) for child in plan.children]
                        )
            except IntegrityError:
                pass
        async with self._session_factory() as session:
            row = await session.scalar(select(PixelFlowOperationBatchRow).where(PixelFlowOperationBatchRow.idempotency_key == plan.batch_idempotency_key))
            if row is None:
                raise AgentRuntimeRecordConflictError("OperationBatch 创建结果不可见")
            return await self._read(session, row, workspace_id, plan, run_id=run_id, tool_call_id=tool_call_id, attempt=attempt, source_workspace_revision=source_workspace_revision)

    async def claim_children(self, *, batch_id: str, max_concurrent: int) -> tuple[OperationBatchChildRecord, ...]:
        if max_concurrent < 1:
            raise ValueError("批次并发槽位必须为正整数")
        async with self._session_factory() as session:
            async with session.begin():
                batch = await session.get(PixelFlowOperationBatchRow, batch_id, with_for_update=True)
                if batch is None:
                    raise LookupError("OperationBatch 不存在")
                rows = list(
                    (
                        await session.scalars(
                            select(PixelFlowOperationBatchChildRow)
                            .where(PixelFlowOperationBatchChildRow.batch_id == batch_id)
                            .order_by(PixelFlowOperationBatchChildRow.scene_id, PixelFlowOperationBatchChildRow.variant_index)
                            .with_for_update()
                        )
                    ).all()
                )
                slots = max(0, max_concurrent - sum(row.status == "polling" for row in rows))
                claimed = [row for row in rows if row.status in {"queued", "starting"}][:slots]
                for row in claimed:
                    row.status = "starting"
                    row.started_at = datetime.now(UTC)
                if claimed:
                    batch.status = "running"
                return tuple(_child(row) for row in claimed)

    async def list_dispatchable_batches(self, *, limit: int) -> tuple[OperationBatchRecord, ...]:
        if limit < 1:
            raise ValueError("批次扫描上限必须为正整数")
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(PixelFlowOperationBatchRow)
                        .where(PixelFlowOperationBatchRow.status.in_(("queued", "running")))
                        .order_by(PixelFlowOperationBatchRow.created_at)
                        .limit(limit)
                    )
                ).all()
            )
            result: list[OperationBatchRecord] = []
            for row in rows:
                children = list(
                    (
                        await session.scalars(
                            select(PixelFlowOperationBatchChildRow).where(
                                PixelFlowOperationBatchChildRow.batch_id == row.batch_id
                            )
                        )
                    ).all()
                )
                if any(child.status in {"queued", "starting"} for child in children):
                    result.append(_record(row, children))
            return tuple(result)

    async def mark_child_terminal(self, *, batch_id: str, child_key: str, status: ChildStatus, job_id: str) -> OperationBatchRecord:
        if status not in _TERMINAL:
            raise ValueError("子 Operation 必须以终态聚合")
        async with self._session_factory() as session:
            async with session.begin():
                batch = await session.get(PixelFlowOperationBatchRow, batch_id, with_for_update=True)
                child = await session.get(PixelFlowOperationBatchChildRow, {"batch_id": batch_id, "operation_idempotency_key": child_key}, with_for_update=True)
                if batch is None or child is None:
                    raise LookupError("OperationBatch 或子项不存在")
                placeholder = child.status == "failed" and child.job_id == child_key
                if child.status in _TERMINAL and not placeholder and (child.status != status or child.job_id != job_id):
                    raise AgentRuntimeRecordConflictError("子 Operation 终态漂移")
                child.status, child.job_id = status, job_id
                child.started_at = None
                rows = list((await session.scalars(select(PixelFlowOperationBatchChildRow).where(PixelFlowOperationBatchChildRow.batch_id == batch_id).with_for_update())).all())
                if all(row.status in _TERMINAL for row in rows):
                    batch.status = "completed"
                    if batch.completion_event_id is None:
                        batch.completion_event_id = build_operation_batch_completion_event_id(batch_id)
                        session.add(
                            PixelFlowOperationBatchOutboxRow(
                                completion_event_id=batch.completion_event_id,
                                batch_id=batch.batch_id,
                                user_id=batch.user_id,
                                conversation_id=batch.conversation_id,
                                workspace_id=batch.workspace_id,
                            ),
                        )
                return _record(batch, rows)

    async def mark_child_polling(
        self,
        *,
        batch_id: str,
        child_key: str,
        job_id: str,
    ) -> OperationBatchRecord:
        """原子绑定子项 Job；重放必须回读相同绑定。"""

        if not job_id.strip():
            raise ValueError("子 Operation job_id 不能为空")
        async with self._session_factory() as session:
            async with session.begin():
                batch = await session.get(
                    PixelFlowOperationBatchRow,
                    batch_id,
                    with_for_update=True,
                )
                child = await session.get(
                    PixelFlowOperationBatchChildRow,
                    {
                        "batch_id": batch_id,
                        "operation_idempotency_key": child_key,
                    },
                    with_for_update=True,
                )
                if batch is None or child is None:
                    raise LookupError("OperationBatch 或子项不存在")
                if child.status in _TERMINAL and not (
                    child.status == "failed" and child.job_id == child_key
                ):
                    raise AgentRuntimeRecordConflictError("终态子 Operation 不能重新轮询")
                if child.job_id is not None and child.job_id != job_id:
                    raise AgentRuntimeRecordConflictError("子 Operation 绑定了不同 Job")
                child.status = "polling"
                child.job_id = job_id
                child.started_at = None
                batch.status = "running"
                rows = list(
                    (
                        await session.scalars(
                            select(PixelFlowOperationBatchChildRow).where(
                                PixelFlowOperationBatchChildRow.batch_id == batch_id
                            )
                        )
                    ).all()
                )
                return _record(batch, rows)

    async def get_batch_for_child_job(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ) -> OperationBatchRecord | None:
        """按 Job 精确回读批次，避免单子项完成误触发其他 Run。"""

        async with self._session_factory() as session:
            row = await session.scalar(
                select(PixelFlowOperationBatchChildRow)
                .join(
                    PixelFlowOperationBatchRow,
                    PixelFlowOperationBatchChildRow.batch_id
                    == PixelFlowOperationBatchRow.batch_id,
                )
                .where(
                    or_(
                        PixelFlowOperationBatchChildRow.job_id == job_id,
                        PixelFlowOperationBatchChildRow.operation_idempotency_key == job_id,
                    ),
                    PixelFlowOperationBatchRow.user_id == user_id,
                    PixelFlowOperationBatchRow.conversation_id == conversation_id,
                )
            )
            if row is None:
                return None
            batch = await session.get(PixelFlowOperationBatchRow, row.batch_id)
            if batch is None:
                raise AgentRuntimeRecordConflictError("OperationBatch 子项缺少父批次")
            children = list(
                (
                    await session.scalars(
                        select(PixelFlowOperationBatchChildRow).where(
                            PixelFlowOperationBatchChildRow.batch_id == batch.batch_id
                        )
                    )
                ).all()
            )
            return _record(batch, children)

    async def get_batch(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workspace_id: str,
        batch_id: str,
    ) -> OperationBatchRecord | None:
        """按已验证的业务归属读取单一批次，不向模型开放列表扫描。"""

        async with self._session_factory() as session:
            batch = await session.scalar(
                select(PixelFlowOperationBatchRow).where(
                    PixelFlowOperationBatchRow.batch_id == batch_id,
                    PixelFlowOperationBatchRow.user_id == user_id,
                    PixelFlowOperationBatchRow.conversation_id == conversation_id,
                    PixelFlowOperationBatchRow.workspace_id == workspace_id,
                )
            )
            if batch is None:
                return None
            children = list(
                (
                    await session.scalars(
                        select(PixelFlowOperationBatchChildRow).where(
                            PixelFlowOperationBatchChildRow.batch_id == batch.batch_id
                        )
                    )
                ).all()
            )
            return _record(batch, children)

    async def claim_completion(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> OperationBatchOutboxRecord | None:
        """领取一个待投递的批次完成事件，过期租约允许恢复 Worker 接手。"""

        if not worker_id.strip() or lease_duration <= timedelta(0):
            raise ValueError("批次 Outbox 租约参数无效")
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(PixelFlowOperationBatchOutboxRow)
                    .where(
                        (PixelFlowOperationBatchOutboxRow.status == "pending") | ((PixelFlowOperationBatchOutboxRow.status == "delivering") & (PixelFlowOperationBatchOutboxRow.lease_expires_at <= now)),
                    )
                    .order_by(PixelFlowOperationBatchOutboxRow.created_at)
                    .with_for_update(),
                )
                if row is None:
                    return None
                row.status = "delivering"
                row.lease_owner = worker_id
                row.lease_expires_at = now + lease_duration
                return _outbox(row)

    async def acknowledge_completion(
        self,
        *,
        completion_event_id: str,
        worker_id: str,
        resume_run_id: str,
        now: datetime,
    ) -> OperationBatchOutboxRecord:
        """将已创建的唯一 operation_resume Run 绑定回事件，重复确认只回读。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowOperationBatchOutboxRow, completion_event_id, with_for_update=True)
                if row is None:
                    raise LookupError("OperationBatch Outbox 不存在")
                if row.status == "delivered":
                    if row.resume_run_id != resume_run_id:
                        raise AgentRuntimeRecordConflictError("批次完成事件绑定了不同恢复 Run")
                    return _outbox(row)
                if row.status != "delivering" or row.lease_owner != worker_id:
                    raise AgentRuntimeRecordConflictError("批次完成事件投递租约无效")
                row.status = "delivered"
                row.resume_run_id = resume_run_id
                row.delivered_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                return _outbox(row)

    async def _read(
        self,
        session: AsyncSession,
        row: PixelFlowOperationBatchRow,
        workspace_id: str,
        plan: OperationBatchPlan,
        *,
        run_id: str | None,
        tool_call_id: str | None,
        attempt: int | None,
        source_workspace_revision: int | None,
    ) -> OperationBatchRecord:
        children = list((await session.scalars(select(PixelFlowOperationBatchChildRow).where(PixelFlowOperationBatchChildRow.batch_id == row.batch_id))).all())
        result = _record(row, children)
        if (
            result.batch_id != plan.batch_id
            or result.workspace_id != workspace_id
            or result.run_id != run_id
            or result.tool_call_id != tool_call_id
            or result.attempt != attempt
            or result.source_workspace_revision != source_workspace_revision
            or result.idempotency_key != plan.batch_idempotency_key
            or {
                child.operation_idempotency_key for child in result.children
            }
            != {
                child.operation_idempotency_key for child in plan.children
            }
        ):
            raise AgentRuntimeRecordConflictError("OperationBatch 身份或子项发生漂移")
        return result


def _child(row: PixelFlowOperationBatchChildRow) -> OperationBatchChildRecord:
    return OperationBatchChildRecord(row.operation_idempotency_key, row.scene_id, row.variant_index, row.status, row.job_id)  # type: ignore[arg-type]


def _record(row: PixelFlowOperationBatchRow, children: list[PixelFlowOperationBatchChildRow]) -> OperationBatchRecord:
    return OperationBatchRecord(
        row.batch_id,
        row.user_id,
        row.conversation_id,
        row.workspace_id,
        row.run_id,
        row.tool_call_id,
        row.attempt,
        row.source_workspace_revision,
        row.idempotency_key,
        row.status,
        row.completion_event_id,
        tuple(
            sorted(
                (_child(child) for child in children),
                key=lambda child: child.operation_idempotency_key,
            )
        ),
    )


def _outbox(row: PixelFlowOperationBatchOutboxRow) -> OperationBatchOutboxRecord:
    return OperationBatchOutboxRecord(
        row.completion_event_id,
        row.batch_id,
        row.user_id,
        row.conversation_id,
        row.workspace_id,
        row.resume_run_id,
    )
