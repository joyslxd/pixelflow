"""PixelFlow 业务任务、进度事件和资产表的 ORM 行模型。

这些 SQLAlchemy model 对应数据库表结构，字段名需要稳定，因为 Store、API 返回
和前端展示都会间接依赖这些列。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _timestamp_type() -> DateTime:
    """Use microsecond precision on MySQL so rapid conversation creation keeps order."""
    return DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


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

    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_pixelflow_tasks_user_updated", "user_id", "updated_at"),)


class PixelFlowTaskEventRow(Base):
    __tablename__ = "pixelflow_task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (Index("ix_pixelflow_events_task_id_id", "task_id", "id"),)


class PixelFlowConversationTraceEventRow(Base):
    """内部调试用：v2 对话工作流（intake/creative/image/video/ppt）的 trace 事件。

    跟 ``PixelFlowTaskEventRow`` 结构一致，但按 ``conversation_id`` 索引，
    因为当前 v2 工作流不经过旧 LangGraph 任务流的 ``task_id``/``run_id``。
    """

    __tablename__ = "pixelflow_conversation_trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (Index("ix_pixelflow_trace_conversation_id_id", "conversation_id", "id"),)


class PixelFlowSessionContextRow(Base):
    __tablename__ = "pixelflow_session_contexts"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), index=True)

    __table_args__ = (Index("ix_pixelflow_session_user_updated", "user_id", "updated_at"),)


class PixelFlowConversationRow(Base):
    __tablename__ = "pixelflow_conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    orchestration_mode: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="frontend_v2",
        server_default="frontend_v2",
    )
    orchestration_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    current_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    last_phase: Mapped[str] = mapped_column(String(32), default="idle", index=True)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), index=True)

    __table_args__ = (
        Index("ix_pixelflow_conversations_user_updated", "user_id", "updated_at", "conversation_id"),
        Index("ix_pixelflow_conversations_user_created", "user_id", "created_at", "conversation_id"),
    )


class PixelFlowConversationMessageRow(Base):
    __tablename__ = "pixelflow_conversation_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (Index("ix_pixelflow_conversation_messages_conversation_created", "conversation_id", "created_at"),)


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
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_pixelflow_assets_task_type", "task_id", "asset_type"),)
