"""ORM rows for PixelFlow business tasks and progress events."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class PixelFlowTaskRow(Base):
    __tablename__ = "pixelflow_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    task_type: Mapped[str] = mapped_column(String(32), default="ecom_video")
    status: Mapped[str] = mapped_column(String(24), default="created", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="intake", index=True)

    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)

    product_info_json: Mapped[dict] = mapped_column(JSON, default=dict)
    video_params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    reference_videos_json: Mapped[list] = mapped_column(JSON, default=list)
    creative_direction_json: Mapped[dict] = mapped_column(JSON, default=dict)

    brief_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_pixelflow_tasks_user_updated", "user_id", "updated_at"),)


class PixelFlowTaskEventRow(Base):
    __tablename__ = "pixelflow_task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (Index("ix_pixelflow_events_task_id_id", "task_id", "id"),)


class PixelFlowSessionContextRow(Base):
    __tablename__ = "pixelflow_session_contexts"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), index=True)

    __table_args__ = (Index("ix_pixelflow_session_user_updated", "user_id", "updated_at"),)


class PixelFlowAssetRow(Base):
    __tablename__ = "pixelflow_assets"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="created", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="")
    shot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    local_path: Mapped[str] = mapped_column(Text, default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    vendor_task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_pixelflow_assets_task_type", "task_id", "asset_type"),)
