"""Persistence abstraction for PixelFlow business tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pixelflow.tasks.model import PixelFlowAssetRow, PixelFlowSessionContextRow, PixelFlowTaskEventRow, PixelFlowTaskRow


def _now() -> datetime:
    return datetime.now(UTC)


def _dt(value: datetime | str | None) -> str:
    return value.isoformat() if isinstance(value, datetime) else (value or "")


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


class PixelFlowTaskStore(Protocol):
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


class SQLPixelFlowTaskStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

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


def _event_to_dict(row: PixelFlowTaskEventRow) -> dict[str, Any]:
    return {"id": row.id, "task_id": row.task_id, "event": row.event, "data": row.data_json or {}, "created_at": _dt(row.created_at)}


class MemoryPixelFlowTaskStore:
    def __init__(self):
        self._tasks: dict[str, PixelFlowTaskRecord] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._assets: dict[str, PixelFlowAssetRecord] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._next_event_id = 1

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
