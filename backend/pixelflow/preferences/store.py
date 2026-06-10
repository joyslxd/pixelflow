"""Structured preference storage for PixelFlow P0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pixelflow.preferences.model import PixelFlowUserPreferenceRow

MAX_RECENT_FEEDBACK = 20


def _now() -> datetime:
    return datetime.now(UTC)


def _dt(value: datetime | str | None) -> str:
    return value.isoformat() if isinstance(value, datetime) else (value or "")


@dataclass
class UserPreferenceRecord:
    user_id: str
    style_preferences: dict[str, Any] = field(default_factory=dict)
    negative_rules: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    recent_feedback: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "style_preferences": self.style_preferences,
            "negative_rules": self.negative_rules,
            "defaults": self.defaults,
            "recent_feedback": self.recent_feedback,
            "updated_at": self.updated_at,
            "semantic_memory": {"enabled": False, "provider": "mem0", "status": "reserved_for_p1"},
        }


class PreferencePatch(dict):
    """Plain dict marker for preference update payloads."""


class UserPreferenceStore(Protocol):
    async def get(self, user_id: str) -> UserPreferenceRecord: ...
    async def update(self, user_id: str, patch: dict[str, Any]) -> UserPreferenceRecord: ...
    async def append_feedback(self, user_id: str, feedback: str, *, task_id: str | None = None, metadata: dict[str, Any] | None = None) -> UserPreferenceRecord: ...


def _row_to_record(row: PixelFlowUserPreferenceRow) -> UserPreferenceRecord:
    return UserPreferenceRecord(
        user_id=row.user_id,
        style_preferences=row.style_json or {},
        negative_rules=row.negatives_json or [],
        defaults=row.defaults_json or {},
        recent_feedback=row.recent_feedback_json or [],
        updated_at=_dt(row.updated_at),
    )


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    out = list(existing)
    seen = {x.strip() for x in out if x.strip()}
    for item in incoming:
        value = str(item).strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


class SQLUserPreferenceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def get(self, user_id: str) -> UserPreferenceRecord:
        async with self._sf() as session:
            row = await session.get(PixelFlowUserPreferenceRow, user_id)
            if row is None:
                row = PixelFlowUserPreferenceRow(user_id=user_id, style_json={}, negatives_json=[], defaults_json={}, recent_feedback_json=[])
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return _row_to_record(row)

    async def update(self, user_id: str, patch: dict[str, Any]) -> UserPreferenceRecord:
        async with self._sf() as session:
            row = await session.get(PixelFlowUserPreferenceRow, user_id)
            if row is None:
                row = PixelFlowUserPreferenceRow(user_id=user_id, style_json={}, negatives_json=[], defaults_json={}, recent_feedback_json=[])
                session.add(row)
            row.style_json = {**(row.style_json or {}), **(patch.get("style_preferences") or {})}
            row.defaults_json = {**(row.defaults_json or {}), **(patch.get("defaults") or {})}
            row.negatives_json = _merge_unique(row.negatives_json or [], patch.get("negative_rules") or [])
            if patch.get("recent_feedback"):
                row.recent_feedback_json = [*patch["recent_feedback"], *(row.recent_feedback_json or [])][:MAX_RECENT_FEEDBACK]
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _row_to_record(row)

    async def append_feedback(self, user_id: str, feedback: str, *, task_id: str | None = None, metadata: dict[str, Any] | None = None) -> UserPreferenceRecord:
        text = feedback.strip()
        if not text:
            return await self.get(user_id)
        item = {"content": text, "task_id": task_id, "metadata": metadata or {}, "created_at": _dt(_now())}
        return await self.update(user_id, {"recent_feedback": [item]})


class MemoryUserPreferenceStore:
    def __init__(self):
        self._rows: dict[str, UserPreferenceRecord] = {}

    async def get(self, user_id: str) -> UserPreferenceRecord:
        if user_id not in self._rows:
            self._rows[user_id] = UserPreferenceRecord(user_id=user_id, updated_at=_dt(_now()))
        return self._rows[user_id]

    async def update(self, user_id: str, patch: dict[str, Any]) -> UserPreferenceRecord:
        row = await self.get(user_id)
        row.style_preferences = {**row.style_preferences, **(patch.get("style_preferences") or {})}
        row.defaults = {**row.defaults, **(patch.get("defaults") or {})}
        row.negative_rules = _merge_unique(row.negative_rules, patch.get("negative_rules") or [])
        if patch.get("recent_feedback"):
            row.recent_feedback = [*patch["recent_feedback"], *row.recent_feedback][:MAX_RECENT_FEEDBACK]
        row.updated_at = _dt(_now())
        return row

    async def append_feedback(self, user_id: str, feedback: str, *, task_id: str | None = None, metadata: dict[str, Any] | None = None) -> UserPreferenceRecord:
        text = feedback.strip()
        if not text:
            return await self.get(user_id)
        item = {"content": text, "task_id": task_id, "metadata": metadata or {}, "created_at": _dt(_now())}
        return await self.update(user_id, {"recent_feedback": [item]})
