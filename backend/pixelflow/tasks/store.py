"""PixelFlow 业务任务持久化抽象。

LangGraph checkpoint 保存的是运行时状态；这里的 Store 保存的是前端和业务 API
更容易消费的任务视图、事件流和资产列表。可以把它理解成业务侧 Repository：
``pixelflow_tasks`` 是任务主表，``pixelflow_task_events`` 是进度事件表，
``pixelflow_assets`` 是生成/剪辑产物表。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.utils.time import coerce_iso
from pixelflow.tasks.model import (
    PixelFlowAssetRow,
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
    PixelFlowConversationTraceEventRow,
    PixelFlowSessionContextRow,
    PixelFlowTaskEventRow,
    PixelFlowTaskRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _to_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _dt(value: datetime | str | None) -> str:
    return coerce_iso(value)


def _conversation_context(value: dict[str, Any] | None) -> dict[str, Any]:
    """Drop legacy chat snapshots; persisted messages are the source of truth."""
    if not value:
        return {}
    return {key: item for key, item in value.items() if key != "messages"}


class _UnsetJianyingDraftResumeError:
    pass


_UNSET_JIANYING_DRAFT_RESUME_ERROR = _UnsetJianyingDraftResumeError()
type JianyingDraftResumeErrorPatch = str | None | _UnsetJianyingDraftResumeError
_JIANYING_DRAFT_CONTEXT_KEYS = (
    "pendingJianyingDraftJob",
    "pending_jianying_draft_job",
    "jianyingDraftRecords",
    "jianying_draft_records",
    "jianying_draft_job_resume_error",
)
_CONVERSATION_LOCK_STRIPE_COUNT = 64
_JIANYING_DRAFT_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timeout"})
_JIANYING_DRAFT_ACTIVE_STATUS_RANK = {"queued": 1, "running": 2}


def _jianying_draft_records(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _jianying_draft_pending(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _jianying_draft_job_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    job_id = value.get("job_id")
    return job_id if isinstance(job_id, str) and job_id else None


def _jianying_draft_status(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    return status if isinstance(status, str) and status else None


def _jianying_draft_succeeded_is_valid(value: Any, *, now: datetime) -> bool:
    if _jianying_draft_status(value) != "succeeded" or not isinstance(value, dict):
        return False
    raw_expire_at = value.get("expire_at")
    if not isinstance(raw_expire_at, (datetime, str)):
        return True
    expire_at = _to_datetime(raw_expire_at)
    if expire_at is None:
        return True
    return expire_at > now


def _merge_jianying_draft_record(
    current_record: Any,
    incoming_record: Any,
    *,
    expected_job_id: str,
    current_pending_job_id: str | None,
    now: datetime,
) -> tuple[dict[str, Any] | None, bool]:
    current = dict(current_record) if isinstance(current_record, dict) else None
    incoming = dict(incoming_record) if isinstance(incoming_record, dict) else None
    if incoming is None or _jianying_draft_job_id(incoming) != expected_job_id:
        return current, False
    if current is None:
        return incoming, True

    current_status = _jianying_draft_status(current)
    incoming_status = _jianying_draft_status(incoming)
    current_job_id = _jianying_draft_job_id(current)
    if current_job_id != expected_job_id and current_pending_job_id != expected_job_id:
        return current, False
    if current_status == "succeeded" and _jianying_draft_succeeded_is_valid(current, now=now):
        return current, current_job_id == expected_job_id and incoming_status == "succeeded"
    if incoming_status == "succeeded":
        return incoming, True
    if current_job_id == expected_job_id:
        if current_status in _JIANYING_DRAFT_TERMINAL_STATUSES:
            return current, incoming_status == current_status
        if incoming_status in _JIANYING_DRAFT_TERMINAL_STATUSES:
            return incoming, True
        if _JIANYING_DRAFT_ACTIVE_STATUS_RANK.get(incoming_status or "", 0) >= _JIANYING_DRAFT_ACTIVE_STATUS_RANK.get(
            current_status or "", 0
        ):
            return incoming, True
        return current, False
    if current_pending_job_id == expected_job_id:
        return incoming, True
    return current, False


def _can_set_jianying_draft_pending(
    pending_job: dict[str, Any],
    *,
    expected_job_id: str,
    current_pending_job_id: str | None,
    records: dict[str, Any],
    now: datetime,
) -> bool:
    if _jianying_draft_job_id(pending_job) != expected_job_id:
        return False
    if current_pending_job_id not in {None, expected_job_id}:
        return False
    storyboard_version_id = pending_job.get("storyboard_version_id")
    if not isinstance(storyboard_version_id, str) or not storyboard_version_id:
        return False
    record = records.get(storyboard_version_id)
    record_status = _jianying_draft_status(record)
    if record_status == "succeeded" and _jianying_draft_succeeded_is_valid(record, now=now):
        return False
    return not (
        _jianying_draft_job_id(record) == expected_job_id
        and record_status in _JIANYING_DRAFT_TERMINAL_STATUSES
    )


def _jianying_draft_phase_after_patch(
    current_phase: str,
    requested_phase: str,
    *,
    expected_job_id: str,
    pending_job: dict[str, Any] | None,
    merged_records: dict[str, Any],
    incoming_record_keys: set[str],
    request_authorized: bool,
) -> str:
    if not request_authorized:
        return current_phase
    pending_job_id = _jianying_draft_job_id(pending_job)
    if pending_job_id is not None:
        return "jianying_draft_running" if pending_job_id == expected_job_id else current_phase
    effective_statuses = {
        _jianying_draft_status(merged_records.get(storyboard_version_id))
        for storyboard_version_id in incoming_record_keys
    }
    if "succeeded" in effective_statuses:
        return "jianying_draft_succeeded"
    if "failed" in effective_statuses:
        return "jianying_draft_failed"
    if "timeout" in effective_statuses:
        return "jianying_draft_timeout"
    return requested_phase


def _patch_jianying_draft_context(
    context: dict[str, Any] | None,
    *,
    current_phase: str,
    requested_phase: str,
    expected_job_id: str,
    pending_job: dict[str, Any] | None,
    records: dict[str, Any],
    resume_error: JianyingDraftResumeErrorPatch = _UNSET_JIANYING_DRAFT_RESUME_ERROR,
) -> tuple[dict[str, Any], str]:
    current = _conversation_context(context)
    merged_records = {
        **_jianying_draft_records(current.get("jianying_draft_records")),
        **_jianying_draft_records(current.get("jianyingDraftRecords")),
    }
    current_pending = _jianying_draft_pending(current.get("pending_jianying_draft_job"))
    camel_pending = _jianying_draft_pending(current.get("pendingJianyingDraftJob"))
    if camel_pending is not None:
        current_pending = camel_pending
    current_pending_job_id = _jianying_draft_job_id(current_pending)
    patch_time = _now()
    request_authorized = False
    for storyboard_version_id, incoming_record in records.items():
        merged_record, record_authorized = _merge_jianying_draft_record(
            merged_records.get(storyboard_version_id),
            incoming_record,
            expected_job_id=expected_job_id,
            current_pending_job_id=current_pending_job_id,
            now=patch_time,
        )
        if merged_record is not None:
            merged_records[storyboard_version_id] = merged_record
        request_authorized = request_authorized or record_authorized

    resolved_pending = current_pending
    if pending_job is None:
        if current_pending_job_id == expected_job_id:
            resolved_pending = None
            request_authorized = True
    elif _can_set_jianying_draft_pending(
        pending_job,
        expected_job_id=expected_job_id,
        current_pending_job_id=current_pending_job_id,
        records=merged_records,
        now=patch_time,
    ):
        resolved_pending = dict(pending_job)
        request_authorized = True

    patched = {
        **current,
        "pendingJianyingDraftJob": resolved_pending,
        "pending_jianying_draft_job": resolved_pending,
        "jianyingDraftRecords": merged_records,
        "jianying_draft_records": merged_records,
    }
    if request_authorized and not isinstance(resume_error, _UnsetJianyingDraftResumeError):
        patched["jianying_draft_job_resume_error"] = resume_error
    resolved_phase = _jianying_draft_phase_after_patch(
        current_phase,
        requested_phase,
        expected_job_id=expected_job_id,
        pending_job=resolved_pending,
        merged_records=merged_records,
        incoming_record_keys=set(records),
        request_authorized=request_authorized,
    )
    return patched, resolved_phase


def _replace_context_preserving_jianying_draft_fields(
    current_context: dict[str, Any] | None,
    replacement_context: dict[str, Any] | None,
) -> dict[str, Any]:
    current = _conversation_context(current_context)
    replacement = _conversation_context(replacement_context)
    for key in _JIANYING_DRAFT_CONTEXT_KEYS:
        if key in current:
            replacement[key] = current[key]
        else:
            replacement.pop(key, None)
    return replacement


def _new_conversation_locks() -> tuple[asyncio.Lock, ...]:
    return tuple(asyncio.Lock() for _ in range(_CONVERSATION_LOCK_STRIPE_COUNT))


def _conversation_lock(locks: tuple[asyncio.Lock, ...], conversation_id: str) -> asyncio.Lock:
    return locks[hash(conversation_id) % len(locks)]


@asynccontextmanager
async def _conversation_write_transaction(session: AsyncSession) -> AsyncIterator[None]:
    if session.get_bind().dialect.name == "sqlite":
        # SQLite 不支持行锁，提前获取数据库写锁以覆盖多 Store/多进程并发。
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            yield
        except BaseException:
            await session.rollback()
            raise
        else:
            await session.commit()
        return
    async with session.begin():
        yield


def _parse_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor or "|" not in cursor:
        return None
    raw_created_at, conversation_id = cursor.rsplit("|", 1)
    created_at = _to_datetime(raw_created_at)
    if created_at is None:
        return None
    return created_at, conversation_id


def _conversation_cursor(record: PixelFlowConversationRecord) -> str:
    return f"{record.created_at}|{record.conversation_id}"


@dataclass
class PixelFlowTaskRecord:
    task_id: str
    user_id: str | None
    task_type: str
    status: str
    phase: str
    thread_id: str
    run_id: str | None = None
    product_info: dict[str, Any] = field(default_factory=dict)
    video_params: dict[str, Any] = field(default_factory=dict)
    reference_videos: list[dict[str, Any]] = field(default_factory=list)
    creative_direction: dict[str, Any] = field(default_factory=dict)
    brief: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "task_type": self.task_type,
            "status": self.status,
            "phase": self.phase,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "product_info": self.product_info,
            "video_params": self.video_params,
            "reference_videos": self.reference_videos,
            "creative_direction": self.creative_direction,
            "brief": self.brief,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PixelFlowAssetRecord:
    asset_id: str
    task_id: str
    user_id: str | None
    asset_type: str
    status: str = "created"
    phase: str = ""
    shot_id: str | None = None
    url: str = ""
    local_path: str = ""
    vendor: str = ""
    vendor_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "asset_type": self.asset_type,
            "status": self.status,
            "phase": self.phase,
            "shot_id": self.shot_id,
            "url": self.url,
            "local_path": self.local_path,
            "vendor": self.vendor,
            "vendor_task_id": self.vendor_task_id,
            "metadata": self.metadata,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PixelFlowConversationRecord:
    conversation_id: str
    user_id: str | None
    title: str = ""
    current_task_id: str | None = None
    last_phase: str = "idle"
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "title": self.title,
            "current_task_id": self.current_task_id,
            "last_phase": self.last_phase,
            "context": self.context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PixelFlowConversationMessageRecord:
    message_id: str
    conversation_id: str
    user_id: str | None
    role: str
    content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "role": self.role,
            "content": self.content,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class PixelFlowTaskStore(Protocol):
    """业务任务 Store 接口。

    SQL 实现用于真实持久化；Memory 实现适合本地开发和测试。所有方法都带可选
    ``user_id`` 过滤，避免不同用户读取彼此任务。
    """

    async def create(self, record: PixelFlowTaskRecord) -> PixelFlowTaskRecord: ...
    async def get(self, task_id: str, *, user_id: str | None = None) -> PixelFlowTaskRecord | None: ...
    async def list(self, *, user_id: str | None = None, limit: int = 50) -> list[PixelFlowTaskRecord]: ...
    async def update(self, task_id: str, *, user_id: str | None = None, **fields: Any) -> PixelFlowTaskRecord | None: ...
    async def append_event(self, task_id: str, event: str, data: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]: ...
    async def list_events(self, task_id: str, *, user_id: str | None = None, after_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]: ...
    async def upsert_asset(self, asset: PixelFlowAssetRecord) -> PixelFlowAssetRecord: ...
    async def list_assets(self, task_id: str, *, user_id: str | None = None) -> list[PixelFlowAssetRecord]: ...
    async def get_session_context(self, task_id: str | None = None, *, user_id: str | None = None) -> dict[str, Any] | None: ...
    async def upsert_session_context(self, task_id: str, context: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]: ...
    async def create_conversation(self, record: PixelFlowConversationRecord) -> PixelFlowConversationRecord: ...
    async def get_conversation(self, conversation_id: str, *, user_id: str | None = None) -> PixelFlowConversationRecord | None: ...
    async def list_conversations(
        self, *, user_id: str | None = None, limit: int = 5, cursor: str | None = None
    ) -> tuple[list[PixelFlowConversationRecord], str | None]: ...
    async def update_conversation(
        self, conversation_id: str, *, user_id: str | None = None, **fields: Any
    ) -> PixelFlowConversationRecord | None: ...
    async def patch_jianying_draft_conversation_context(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        expected_job_id: str,
        pending_job: dict[str, Any] | None,
        records: dict[str, Any],
        last_phase: str,
        resume_error: JianyingDraftResumeErrorPatch = _UNSET_JIANYING_DRAFT_RESUME_ERROR,
    ) -> PixelFlowConversationRecord | None: ...
    async def append_conversation_message(self, message: PixelFlowConversationMessageRecord) -> PixelFlowConversationMessageRecord: ...
    async def update_conversation_message(
        self,
        conversation_id: str,
        message_id: str,
        *,
        user_id: str | None = None,
        content: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> PixelFlowConversationMessageRecord | None: ...
    async def list_conversation_messages(
        self, conversation_id: str, *, user_id: str | None = None, limit: int = 200
    ) -> list[PixelFlowConversationMessageRecord]: ...
    async def append_trace_event(self, conversation_id: str, event: str, data: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]: ...
    async def list_trace_events(
        self, conversation_id: str, *, user_id: str | None = None, after_id: int | None = None, limit: int = 500
    ) -> list[dict[str, Any]]: ...


def _row_to_record(row: PixelFlowTaskRow) -> PixelFlowTaskRecord:
    return PixelFlowTaskRecord(
        task_id=row.task_id,
        user_id=row.user_id,
        task_type=row.task_type,
        status=row.status,
        phase=row.phase,
        thread_id=row.thread_id,
        run_id=row.run_id,
        product_info=row.product_info_json or {},
        video_params=row.video_params_json or {},
        reference_videos=row.reference_videos_json or [],
        creative_direction=row.creative_direction_json or {},
        brief=row.brief_json or {},
        result=row.result_json or {},
        error=row.error,
        created_at=_dt(row.created_at),
        updated_at=_dt(row.updated_at),
    )


def _asset_row_to_record(row: PixelFlowAssetRow) -> PixelFlowAssetRecord:
    return PixelFlowAssetRecord(
        asset_id=row.asset_id,
        task_id=row.task_id,
        user_id=row.user_id,
        asset_type=row.asset_type,
        status=row.status,
        phase=row.phase,
        shot_id=row.shot_id,
        url=row.url or "",
        local_path=row.local_path or "",
        vendor=row.vendor or "",
        vendor_task_id=row.vendor_task_id,
        metadata=row.metadata_json or {},
        error=row.error,
        created_at=_dt(row.created_at),
        updated_at=_dt(row.updated_at),
    )


def _conversation_row_to_record(row: PixelFlowConversationRow) -> PixelFlowConversationRecord:
    return PixelFlowConversationRecord(
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        title=row.title or "",
        current_task_id=row.current_task_id,
        last_phase=row.last_phase or "idle",
        context=_conversation_context(row.context_json),
        created_at=_dt(row.created_at),
        updated_at=_dt(row.updated_at),
    )


def _conversation_message_row_to_record(row: PixelFlowConversationMessageRow) -> PixelFlowConversationMessageRecord:
    return PixelFlowConversationMessageRecord(
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        role=row.role,
        content=row.content or "",
        payload=row.payload_json or {},
        created_at=_dt(row.created_at),
    )


class SQLPixelFlowTaskStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory
        self._conversation_locks = _new_conversation_locks()

    async def create(self, record: PixelFlowTaskRecord) -> PixelFlowTaskRecord:
        async with self._sf() as session:
            row = PixelFlowTaskRow(
                task_id=record.task_id,
                user_id=record.user_id,
                task_type=record.task_type,
                status=record.status,
                phase=record.phase,
                thread_id=record.thread_id,
                run_id=record.run_id,
                product_info_json=record.product_info,
                video_params_json=record.video_params,
                reference_videos_json=record.reference_videos,
                creative_direction_json=record.creative_direction,
                brief_json=record.brief,
                result_json=record.result,
                error=record.error,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_record(row)

    async def get(self, task_id: str, *, user_id: str | None = None) -> PixelFlowTaskRecord | None:
        async with self._sf() as session:
            stmt = select(PixelFlowTaskRow).where(PixelFlowTaskRow.task_id == task_id)
            if user_id is not None:
                stmt = stmt.where(PixelFlowTaskRow.user_id == user_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_to_record(row) if row else None

    async def list(self, *, user_id: str | None = None, limit: int = 50) -> list[PixelFlowTaskRecord]:
        async with self._sf() as session:
            stmt = select(PixelFlowTaskRow).order_by(PixelFlowTaskRow.updated_at.desc()).limit(limit)
            if user_id is not None:
                stmt = stmt.where(PixelFlowTaskRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_record(r) for r in rows]

    async def update(self, task_id: str, *, user_id: str | None = None, **fields: Any) -> PixelFlowTaskRecord | None:
        async with self._sf() as session:
            stmt = select(PixelFlowTaskRow).where(PixelFlowTaskRow.task_id == task_id)
            if user_id is not None:
                stmt = stmt.where(PixelFlowTaskRow.user_id == user_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            mapping = {
                "status": "status",
                "phase": "phase",
                "run_id": "run_id",
                "brief": "brief_json",
                "result": "result_json",
                "error": "error",
                "product_info": "product_info_json",
                "video_params": "video_params_json",
                "reference_videos": "reference_videos_json",
                "creative_direction": "creative_direction_json",
            }
            for key, value in fields.items():
                attr = mapping.get(key)
                if attr:
                    setattr(row, attr, value)
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _row_to_record(row)

    async def append_event(self, task_id: str, event: str, data: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        async with self._sf() as session:
            row = PixelFlowTaskEventRow(task_id=task_id, user_id=user_id, event=event, data_json=data)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _event_to_dict(row)

    async def list_events(self, task_id: str, *, user_id: str | None = None, after_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        async with self._sf() as session:
            stmt = select(PixelFlowTaskEventRow).where(PixelFlowTaskEventRow.task_id == task_id).order_by(PixelFlowTaskEventRow.id.asc()).limit(limit)
            if user_id is not None:
                stmt = stmt.where(PixelFlowTaskEventRow.user_id == user_id)
            if after_id is not None:
                stmt = stmt.where(PixelFlowTaskEventRow.id > after_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [_event_to_dict(r) for r in rows]

    async def append_trace_event(self, conversation_id: str, event: str, data: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        async with self._sf() as session:
            row = PixelFlowConversationTraceEventRow(conversation_id=conversation_id, user_id=user_id, event=event, data_json=data)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _trace_event_to_dict(row)

    async def list_trace_events(
        self, conversation_id: str, *, user_id: str | None = None, after_id: int | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        async with self._sf() as session:
            stmt = (
                select(PixelFlowConversationTraceEventRow)
                .where(PixelFlowConversationTraceEventRow.conversation_id == conversation_id)
                .order_by(PixelFlowConversationTraceEventRow.id.asc())
                .limit(limit)
            )
            if user_id is not None:
                stmt = stmt.where(PixelFlowConversationTraceEventRow.user_id == user_id)
            if after_id is not None:
                stmt = stmt.where(PixelFlowConversationTraceEventRow.id > after_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [_trace_event_to_dict(r) for r in rows]

    async def upsert_asset(self, asset: PixelFlowAssetRecord) -> PixelFlowAssetRecord:
        async with self._sf() as session:
            row = await session.get(PixelFlowAssetRow, asset.asset_id)
            values = {
                "task_id": asset.task_id,
                "user_id": asset.user_id,
                "asset_type": asset.asset_type,
                "status": asset.status,
                "phase": asset.phase,
                "shot_id": asset.shot_id,
                "url": asset.url,
                "local_path": asset.local_path,
                "vendor": asset.vendor,
                "vendor_task_id": asset.vendor_task_id,
                "metadata_json": asset.metadata,
                "error": asset.error,
                "updated_at": _now(),
            }
            if row is None:
                session.add(PixelFlowAssetRow(asset_id=asset.asset_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await session.commit()
            row = await session.get(PixelFlowAssetRow, asset.asset_id)
            return _asset_row_to_record(row)

    async def list_assets(self, task_id: str, *, user_id: str | None = None) -> list[PixelFlowAssetRecord]:
        async with self._sf() as session:
            stmt = select(PixelFlowAssetRow).where(PixelFlowAssetRow.task_id == task_id).order_by(PixelFlowAssetRow.created_at.asc())
            if user_id is not None:
                stmt = stmt.where(PixelFlowAssetRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [_asset_row_to_record(r) for r in rows]

    async def get_session_context(self, task_id: str | None = None, *, user_id: str | None = None) -> dict[str, Any] | None:
        async with self._sf() as session:
            stmt = select(PixelFlowSessionContextRow).order_by(PixelFlowSessionContextRow.updated_at.desc()).limit(1)
            if task_id is not None:
                stmt = stmt.where(PixelFlowSessionContextRow.task_id == task_id)
            if user_id is not None:
                stmt = stmt.where(PixelFlowSessionContextRow.user_id == user_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return {"task_id": row.task_id, "user_id": row.user_id, "context": row.context_json or {}, "updated_at": _dt(row.updated_at)}

    async def upsert_session_context(self, task_id: str, context: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        async with self._sf() as session:
            row = await session.get(PixelFlowSessionContextRow, task_id)
            if row is None:
                row = PixelFlowSessionContextRow(task_id=task_id, user_id=user_id, context_json=context)
                session.add(row)
            else:
                row.user_id = user_id
                row.context_json = context
                row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return {"task_id": row.task_id, "user_id": row.user_id, "context": row.context_json or {}, "updated_at": _dt(row.updated_at)}

    async def create_conversation(self, record: PixelFlowConversationRecord) -> PixelFlowConversationRecord:
        async with self._sf() as session:
            created_at = _to_datetime(record.created_at) or _now()
            updated_at = _to_datetime(record.updated_at) or created_at
            row = PixelFlowConversationRow(
                conversation_id=record.conversation_id,
                user_id=record.user_id,
                title=record.title,
                current_task_id=record.current_task_id,
                last_phase=record.last_phase,
                context_json=_conversation_context(record.context),
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _conversation_row_to_record(row)

    async def get_conversation(self, conversation_id: str, *, user_id: str | None = None) -> PixelFlowConversationRecord | None:
        async with self._sf() as session:
            stmt = select(PixelFlowConversationRow).where(PixelFlowConversationRow.conversation_id == conversation_id)
            if user_id is not None:
                stmt = stmt.where(PixelFlowConversationRow.user_id == user_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _conversation_row_to_record(row) if row else None

    async def list_conversations(
        self, *, user_id: str | None = None, limit: int = 5, cursor: str | None = None
    ) -> tuple[list[PixelFlowConversationRecord], str | None]:
        async with self._sf() as session:
            stmt = (
                select(PixelFlowConversationRow)
                .order_by(PixelFlowConversationRow.created_at.desc(), PixelFlowConversationRow.conversation_id.desc())
                .limit(limit + 1)
            )
            if user_id is not None:
                stmt = stmt.where(PixelFlowConversationRow.user_id == user_id)
            parsed = _parse_cursor(cursor)
            if parsed is not None:
                created_at, conversation_id = parsed
                stmt = stmt.where(
                    or_(
                        PixelFlowConversationRow.created_at < created_at,
                        and_(
                            PixelFlowConversationRow.created_at == created_at,
                            PixelFlowConversationRow.conversation_id < conversation_id,
                        ),
                    )
                )
            rows = (await session.execute(stmt)).scalars().all()
            records = [_conversation_row_to_record(r) for r in rows[:limit]]
            next_cursor = _conversation_cursor(records[-1]) if len(rows) > limit and records else None
            return records, next_cursor

    async def update_conversation(
        self, conversation_id: str, *, user_id: str | None = None, **fields: Any
    ) -> PixelFlowConversationRecord | None:
        lock = _conversation_lock(self._conversation_locks, conversation_id)
        async with lock:
            async with self._sf() as session:
                async with _conversation_write_transaction(session):
                    stmt = (
                        select(PixelFlowConversationRow)
                        .where(PixelFlowConversationRow.conversation_id == conversation_id)
                        .with_for_update()
                    )
                    if user_id is not None:
                        stmt = stmt.where(PixelFlowConversationRow.user_id == user_id)
                    row = (await session.execute(stmt)).scalar_one_or_none()
                    if row is None:
                        return None
                    mapping = {
                        "title": "title",
                        "current_task_id": "current_task_id",
                        "last_phase": "last_phase",
                        "context": "context_json",
                    }
                    for key, value in fields.items():
                        attr = mapping.get(key)
                        if attr:
                            if key == "context":
                                value = _replace_context_preserving_jianying_draft_fields(row.context_json, value)
                            setattr(row, attr, value)
                    row.updated_at = _now()
                    await session.flush()
                    return _conversation_row_to_record(row)

    async def patch_jianying_draft_conversation_context(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        expected_job_id: str,
        pending_job: dict[str, Any] | None,
        records: dict[str, Any],
        last_phase: str,
        resume_error: JianyingDraftResumeErrorPatch = _UNSET_JIANYING_DRAFT_RESUME_ERROR,
    ) -> PixelFlowConversationRecord | None:
        lock = _conversation_lock(self._conversation_locks, conversation_id)
        async with lock:
            async with self._sf() as session:
                async with _conversation_write_transaction(session):
                    stmt = (
                        select(PixelFlowConversationRow)
                        .where(PixelFlowConversationRow.conversation_id == conversation_id)
                        .with_for_update()
                    )
                    if user_id is not None:
                        stmt = stmt.where(PixelFlowConversationRow.user_id == user_id)
                    row = (await session.execute(stmt)).scalar_one_or_none()
                    if row is None:
                        return None
                    row.context_json, row.last_phase = _patch_jianying_draft_context(
                        row.context_json,
                        current_phase=row.last_phase,
                        requested_phase=last_phase,
                        expected_job_id=expected_job_id,
                        pending_job=pending_job,
                        records=records,
                        resume_error=resume_error,
                    )
                    row.updated_at = _now()
                    await session.flush()
                    return _conversation_row_to_record(row)

    async def append_conversation_message(self, message: PixelFlowConversationMessageRecord) -> PixelFlowConversationMessageRecord:
        async with self._sf() as session:
            existing = await session.get(PixelFlowConversationMessageRow, message.message_id)
            if existing is not None:
                if existing.conversation_id != message.conversation_id:
                    raise ValueError("conversation message_id collision across conversations")
                return _conversation_message_row_to_record(existing)
            conversation = await session.get(PixelFlowConversationRow, message.conversation_id)
            row = PixelFlowConversationMessageRow(
                message_id=message.message_id,
                conversation_id=message.conversation_id,
                user_id=message.user_id,
                role=message.role,
                content=message.content,
                payload_json=message.payload,
            )
            session.add(row)
            if conversation is not None:
                conversation.updated_at = _now()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.get(PixelFlowConversationMessageRow, message.message_id)
                if existing is not None and existing.conversation_id == message.conversation_id:
                    return _conversation_message_row_to_record(existing)
                raise
            await session.refresh(row)
            return _conversation_message_row_to_record(row)

    async def update_conversation_message(
        self,
        conversation_id: str,
        message_id: str,
        *,
        user_id: str | None = None,
        content: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> PixelFlowConversationMessageRecord | None:
        async with self._sf() as session:
            stmt = select(PixelFlowConversationMessageRow).where(PixelFlowConversationMessageRow.conversation_id == conversation_id)
            if user_id is not None:
                stmt = stmt.where(PixelFlowConversationMessageRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            row = next(
                (
                    item
                    for item in rows
                    if item.message_id == message_id or (item.payload_json or {}).get("client_message_id") == message_id
                ),
                None,
            )
            if row is None:
                return None
            if content is not None:
                row.content = content
            if payload is not None:
                row.payload_json = payload
            conversation = await session.get(PixelFlowConversationRow, conversation_id)
            if conversation is not None:
                conversation.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _conversation_message_row_to_record(row)

    async def list_conversation_messages(
        self, conversation_id: str, *, user_id: str | None = None, limit: int = 200
    ) -> list[PixelFlowConversationMessageRecord]:
        async with self._sf() as session:
            stmt = (
                select(PixelFlowConversationMessageRow)
                .where(PixelFlowConversationMessageRow.conversation_id == conversation_id)
                .order_by(PixelFlowConversationMessageRow.created_at.asc(), PixelFlowConversationMessageRow.message_id.asc())
                .limit(limit)
            )
            if user_id is not None:
                stmt = stmt.where(PixelFlowConversationMessageRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [_conversation_message_row_to_record(r) for r in rows]


def _event_to_dict(row: PixelFlowTaskEventRow) -> dict[str, Any]:
    return {"id": row.id, "task_id": row.task_id, "event": row.event, "data": row.data_json or {}, "created_at": _dt(row.created_at)}


def _trace_event_to_dict(row: PixelFlowConversationTraceEventRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "event": row.event,
        "data": row.data_json or {},
        "created_at": _dt(row.created_at),
    }


class MemoryPixelFlowTaskStore:
    def __init__(self):
        self._tasks: dict[str, PixelFlowTaskRecord] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._assets: dict[str, PixelFlowAssetRecord] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._conversations: dict[str, PixelFlowConversationRecord] = {}
        self._conversation_messages: dict[str, list[PixelFlowConversationMessageRecord]] = {}
        self._trace_events: dict[str, list[dict[str, Any]]] = {}
        self._conversation_locks = _new_conversation_locks()
        self._next_event_id = 1
        self._next_trace_event_id = 1

    async def create(self, record: PixelFlowTaskRecord) -> PixelFlowTaskRecord:
        stamp = _dt(_now())
        record.created_at = record.created_at or stamp
        record.updated_at = record.updated_at or stamp
        self._tasks[record.task_id] = record
        return record

    async def get(self, task_id: str, *, user_id: str | None = None) -> PixelFlowTaskRecord | None:
        record = self._tasks.get(task_id)
        if record and (user_id is None or record.user_id == user_id):
            return record
        return None

    async def list(self, *, user_id: str | None = None, limit: int = 50) -> list[PixelFlowTaskRecord]:
        rows = [r for r in self._tasks.values() if user_id is None or r.user_id == user_id]
        return sorted(rows, key=lambda r: r.updated_at, reverse=True)[:limit]

    async def update(self, task_id: str, *, user_id: str | None = None, **fields: Any) -> PixelFlowTaskRecord | None:
        record = await self.get(task_id, user_id=user_id)
        if record is None:
            return None
        for key, value in fields.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record.updated_at = _dt(_now())
        return record

    async def append_event(self, task_id: str, event: str, data: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        row = {"id": self._next_event_id, "task_id": task_id, "event": event, "data": data, "created_at": _dt(_now())}
        self._next_event_id += 1
        self._events.setdefault(task_id, []).append(row)
        return row

    async def list_events(self, task_id: str, *, user_id: str | None = None, after_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = list(self._events.get(task_id, []))
        if after_id is not None:
            rows = [r for r in rows if r["id"] > after_id]
        return rows[:limit]

    async def append_trace_event(self, conversation_id: str, event: str, data: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        row = {"id": self._next_trace_event_id, "conversation_id": conversation_id, "event": event, "data": data, "created_at": _dt(_now())}
        self._next_trace_event_id += 1
        self._trace_events.setdefault(conversation_id, []).append(row)
        return row

    async def list_trace_events(
        self, conversation_id: str, *, user_id: str | None = None, after_id: int | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = list(self._trace_events.get(conversation_id, []))
        if after_id is not None:
            rows = [r for r in rows if r["id"] > after_id]
        return rows[:limit]

    async def upsert_asset(self, asset: PixelFlowAssetRecord) -> PixelFlowAssetRecord:
        stamp = _dt(_now())
        asset.created_at = asset.created_at or stamp
        asset.updated_at = stamp
        self._assets[asset.asset_id] = asset
        return asset

    async def list_assets(self, task_id: str, *, user_id: str | None = None) -> list[PixelFlowAssetRecord]:
        rows = [r for r in self._assets.values() if r.task_id == task_id and (user_id is None or r.user_id == user_id)]
        return sorted(rows, key=lambda r: r.created_at)

    async def get_session_context(self, task_id: str | None = None, *, user_id: str | None = None) -> dict[str, Any] | None:
        rows = list(self._contexts.values())
        if task_id is not None:
            rows = [r for r in rows if r["task_id"] == task_id]
        if user_id is not None:
            rows = [r for r in rows if r.get("user_id") == user_id]
        if not rows:
            return None
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)[0]

    async def upsert_session_context(self, task_id: str, context: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        stamp = _dt(_now())
        row = self._contexts.get(task_id) or {"task_id": task_id, "user_id": user_id, "created_at": stamp}
        row.update({"user_id": user_id, "context": context, "updated_at": stamp})
        self._contexts[task_id] = row
        return row

    async def create_conversation(self, record: PixelFlowConversationRecord) -> PixelFlowConversationRecord:
        stamp = _dt(_now())
        record.created_at = record.created_at or stamp
        record.updated_at = record.updated_at or stamp
        record.context = _conversation_context(record.context)
        self._conversations[record.conversation_id] = record
        return record

    async def get_conversation(self, conversation_id: str, *, user_id: str | None = None) -> PixelFlowConversationRecord | None:
        record = self._conversations.get(conversation_id)
        if record and (user_id is None or record.user_id == user_id):
            return record
        return None

    async def list_conversations(
        self, *, user_id: str | None = None, limit: int = 5, cursor: str | None = None
    ) -> tuple[list[PixelFlowConversationRecord], str | None]:
        rows = [r for r in self._conversations.values() if user_id is None or r.user_id == user_id]
        rows = sorted(rows, key=lambda r: (r.created_at, r.conversation_id), reverse=True)
        parsed = _parse_cursor(cursor)
        if parsed is not None:
            created_at, conversation_id = parsed
            cursor_key = (_dt(created_at), conversation_id)
            rows = [r for r in rows if (r.created_at, r.conversation_id) < cursor_key]
        page = rows[:limit]
        next_cursor = _conversation_cursor(page[-1]) if len(rows) > limit and page else None
        return page, next_cursor

    async def update_conversation(
        self, conversation_id: str, *, user_id: str | None = None, **fields: Any
    ) -> PixelFlowConversationRecord | None:
        lock = _conversation_lock(self._conversation_locks, conversation_id)
        async with lock:
            record = await self.get_conversation(conversation_id, user_id=user_id)
            if record is None:
                return None
            for key in ("title", "current_task_id", "last_phase", "context"):
                if key in fields:
                    if key == "context":
                        value = _replace_context_preserving_jianying_draft_fields(record.context, fields[key])
                    else:
                        value = fields[key]
                    setattr(record, key, value)
            record.updated_at = _dt(_now())
            return record

    async def patch_jianying_draft_conversation_context(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        expected_job_id: str,
        pending_job: dict[str, Any] | None,
        records: dict[str, Any],
        last_phase: str,
        resume_error: JianyingDraftResumeErrorPatch = _UNSET_JIANYING_DRAFT_RESUME_ERROR,
    ) -> PixelFlowConversationRecord | None:
        lock = _conversation_lock(self._conversation_locks, conversation_id)
        async with lock:
            record = await self.get_conversation(conversation_id, user_id=user_id)
            if record is None:
                return None
            record.context, record.last_phase = _patch_jianying_draft_context(
                record.context,
                current_phase=record.last_phase,
                requested_phase=last_phase,
                expected_job_id=expected_job_id,
                pending_job=pending_job,
                records=records,
                resume_error=resume_error,
            )
            record.updated_at = _dt(_now())
            return record

    async def append_conversation_message(self, message: PixelFlowConversationMessageRecord) -> PixelFlowConversationMessageRecord:
        existing = next(
            (
                item
                for rows in self._conversation_messages.values()
                for item in rows
                if item.message_id == message.message_id
            ),
            None,
        )
        if existing is not None:
            if existing.conversation_id != message.conversation_id:
                raise ValueError("conversation message_id collision across conversations")
            return existing
        message.created_at = message.created_at or _dt(_now())
        self._conversation_messages.setdefault(message.conversation_id, []).append(message)
        conversation = self._conversations.get(message.conversation_id)
        if conversation is not None:
            conversation.updated_at = message.created_at
        return message

    async def update_conversation_message(
        self,
        conversation_id: str,
        message_id: str,
        *,
        user_id: str | None = None,
        content: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> PixelFlowConversationMessageRecord | None:
        rows = self._conversation_messages.get(conversation_id, [])
        record = next(
            (
                item
                for item in rows
                if (user_id is None or item.user_id == user_id)
                and (item.message_id == message_id or item.payload.get("client_message_id") == message_id)
            ),
            None,
        )
        if record is None:
            return None
        if content is not None:
            record.content = content
        if payload is not None:
            record.payload = payload
        conversation = self._conversations.get(conversation_id)
        if conversation is not None:
            conversation.updated_at = _dt(_now())
        return record

    async def list_conversation_messages(
        self, conversation_id: str, *, user_id: str | None = None, limit: int = 200
    ) -> list[PixelFlowConversationMessageRecord]:
        rows = list(self._conversation_messages.get(conversation_id, []))
        if user_id is not None:
            rows = [r for r in rows if r.user_id == user_id]
        return sorted(rows, key=lambda r: (r.created_at, r.message_id))[:limit]
