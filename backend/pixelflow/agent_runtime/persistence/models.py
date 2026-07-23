"""Agent Runtime 五类业务记录的 SQLAlchemy 行模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _timestamp_type() -> DateTime:
    """MySQL 使用微秒精度，保证高并发记录仍可稳定排序。"""

    return DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def _now() -> datetime:
    return datetime.now(UTC)


class PixelFlowAgentWorkflowRow(Base):
    """保存可查询、可恢复的 Workflow 业务投影。"""

    __tablename__ = "pixelflow_agent_workflows"

    workflow_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_version: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_contract_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pending_external_job_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latest_artifact_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_pf_agent_workflows_owner_conversation", "user_id", "conversation_id"),
        Index("ix_pf_agent_workflows_conversation_updated", "conversation_id", "updated_at"),
    )


class PixelFlowAgentTurnRow(Base):
    """保存用户输入队列顺序与幂等 Turn 投影。"""

    __tablename__ = "pixelflow_agent_turns"

    inbox_sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_input_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    target_workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expected_context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("conversation_id", "client_input_id", name="uq_pf_agent_turns_conversation_client_input"),
        Index("ix_pf_agent_turns_owner_queue", "user_id", "conversation_id", "status", "inbox_sequence"),
    )


class PixelFlowAgentContextSummaryRow(Base):
    """保存不覆盖原始消息的版本化结构摘要。"""

    __tablename__ = "pixelflow_agent_context_summaries"

    summary_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_summary_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_goals_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confirmed_decisions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    negative_constraints_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    workflow_states_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    unresolved_questions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    artifact_evidence_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    covered_message_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    covered_sequence_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    covered_sequence_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compression_model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("conversation_id", "version", name="uq_pf_agent_summaries_conversation_version"),
        Index("ix_pf_agent_summaries_owner_version", "user_id", "conversation_id", "version"),
    )


class PixelFlowAgentEventRow(Base):
    """保存先落库、后投递的 conversation 事件 Outbox。"""

    __tablename__ = "pixelflow_agent_events"

    outbox_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    delivery_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_pf_agent_events_conversation_sequence"),
        UniqueConstraint("conversation_id", "cursor", name="uq_pf_agent_events_conversation_cursor"),
        Index("ix_pf_agent_events_owner_sequence", "user_id", "conversation_id", "sequence"),
        Index("ix_pf_agent_events_delivery", "delivery_status", "lease_expires_at", "outbox_id"),
    )


class PixelFlowAgentOperationRow(Base):
    """保存外部任务 claim、轮询引用和数据库 lease。"""

    __tablename__ = "pixelflow_agent_operations"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    next_poll_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pf_agent_operations_idempotency_key"),
        UniqueConstraint(
            "workflow_id",
            "stage",
            "stage_version",
            "attempt",
            name="uq_pf_agent_operations_workflow_stage_attempt",
        ),
        Index("ix_pf_agent_operations_owner_workflow", "user_id", "conversation_id", "workflow_id"),
        Index("ix_pf_agent_operations_poll", "status", "next_poll_at", "lease_expires_at"),
    )


AGENT_RUNTIME_TABLES = (
    PixelFlowAgentWorkflowRow.__table__,
    PixelFlowAgentTurnRow.__table__,
    PixelFlowAgentContextSummaryRow.__table__,
    PixelFlowAgentEventRow.__table__,
    PixelFlowAgentOperationRow.__table__,
)
