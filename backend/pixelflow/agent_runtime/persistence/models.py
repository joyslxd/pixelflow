"""Agent Runtime 五类业务记录的 SQLAlchemy 行模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _timestamp_type() -> DateTime:
    """MySQL 使用微秒精度，保证高并发记录仍可稳定排序。"""

    return DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def _now() -> datetime:
    return datetime.now(UTC)


def _payload_sha256_check_expression(column_name: str) -> str:
    """生成跨 SQLite/MySQL 的小写 SHA-256 格式约束。"""

    allowed = ", ".join(f"'{character}'" for character in "0123456789abcdef")
    character_checks = " AND ".join(
        f"SUBSTR({column_name}, {position}, 1) IN ({allowed})"
        for position in range(8, 72)
    )
    return (
        f"LENGTH({column_name}) = 71 AND "
        f"SUBSTR({column_name}, 1, 7) = 'sha256:' AND {character_checks}"
    )


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


class PixelFlowAgentCompactionLockRow(Base):
    """保存 conversation 压缩的短事务租约和 fencing token。"""

    __tablename__ = "pixelflow_agent_compaction_locks"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="idle",
    )
    lease_owner: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    lease_token: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        _timestamp_type(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        _timestamp_type(),
        nullable=False,
        default=_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        _timestamp_type(),
        nullable=False,
        default=_now,
        onupdate=_now,
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('idle', 'active', 'retry_required')",
            name="ck_pf_agent_compaction_locks_state",
        ),
        CheckConstraint(
            "(state = 'idle' AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR (state IN ('active', 'retry_required') AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_pf_agent_compaction_locks_lease_fields",
        ),
        Index(
            "ix_pf_agent_compaction_locks_owner_expiry",
            "user_id",
            "lease_expires_at",
        ),
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


class PixelFlowAgentContextPayloadRow(Base):
    """幂等保存从模型输入外置的完整 tool/artifact 载荷。"""

    __tablename__ = "pixelflow_agent_context_payloads"

    payload_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    original_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _timestamp_type(),
        nullable=False,
        default=_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "conversation_id",
            "source_kind",
            "source_ref",
            "content_hash",
            name="uq_pf_agent_context_payloads_identity",
        ),
        CheckConstraint(
            "source_kind IN ('tool', 'artifact')",
            name="ck_pf_agent_context_payloads_kind",
        ),
        Index(
            "ix_pf_agent_context_payloads_owner_created",
            "user_id",
            "conversation_id",
            "created_at",
        ),
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


class PixelFlowAgentVideoStateRow(Base):
    """保存视频 Workflow 可校验、可恢复的权威状态快照。"""

    __tablename__ = "pixelflow_agent_video_states"

    workflow_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    last_turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_action_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        _timestamp_type(), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        CheckConstraint(
            _payload_sha256_check_expression("payload_sha256"),
            name="ck_pf_agent_video_states_payload_sha256",
        ),
        CheckConstraint(
            "workflow_version >= 1",
            name="ck_pf_agent_video_states_workflow_version",
        ),
        Index(
            "ix_pf_agent_video_states_owner_conversation",
            "user_id",
            "conversation_id",
        ),
    )


class PixelFlowAgentTurnExecutionRow(Base):
    """保存 Turn 执行尝试、恢复排期和带 fencing token 的租约。"""

    __tablename__ = "pixelflow_agent_turn_executions"

    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)
    last_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        _timestamp_type(), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        CheckConstraint(
            "attempt >= 0",
            name="ck_pf_agent_turn_executions_attempt",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_pf_agent_turn_executions_lease_fields",
        ),
        Index(
            "ix_pf_agent_turn_executions_owner_conversation",
            "user_id",
            "conversation_id",
        ),
        Index(
            "ix_pf_agent_turn_executions_recovery",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )


class PixelFlowAgentProjectionMessageRow(Base):
    """保存 Supervisor 产生的助手或系统消息权威投影。"""

    __tablename__ = "pixelflow_agent_projection_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        _timestamp_type(), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('assistant', 'system')",
            name="ck_pf_agent_projection_messages_role",
        ),
        Index(
            "ix_pf_agent_projection_messages_owner_conversation_created",
            "user_id",
            "conversation_id",
            "created_at",
        ),
    )


class PixelFlowAgentInterruptRow(Base):
    """保存待人工响应的 Graph interrupt 及其幂等响应。"""

    __tablename__ = "pixelflow_agent_interrupts"

    interrupt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(_timestamp_type(), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'responded', 'closed')",
            name="ck_pf_agent_interrupts_status",
        ),
        CheckConstraint(
            "(response_id IS NULL AND response_json IS NULL) "
            "OR (response_id IS NOT NULL AND response_json IS NOT NULL)",
            name="ck_pf_agent_interrupts_response_fields",
        ),
        Index(
            "ix_pf_agent_interrupts_owner_conversation_status",
            "user_id",
            "conversation_id",
            "status",
        ),
    )


class PixelFlowAgentConversationStateRow(Base):
    """保存会话当前活动 Workflow 的权威投影。"""

    __tablename__ = "pixelflow_agent_conversation_states"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    active_workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_timestamp_type(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        _timestamp_type(), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        Index(
            "ix_pf_agent_conversation_states_owner",
            "user_id",
            "conversation_id",
        ),
    )


AGENT_RUNTIME_TABLES = (
    PixelFlowAgentWorkflowRow.__table__,
    PixelFlowAgentTurnRow.__table__,
    PixelFlowAgentCompactionLockRow.__table__,
    PixelFlowAgentContextSummaryRow.__table__,
    PixelFlowAgentEventRow.__table__,
    PixelFlowAgentOperationRow.__table__,
)

# 支撑表不改变 M01 冻结的业务投影合同，但必须随 Runtime 一起建库。
AGENT_RUNTIME_SUPPORT_TABLES = (
    PixelFlowAgentContextPayloadRow.__table__,
    PixelFlowAgentVideoStateRow.__table__,
    PixelFlowAgentTurnExecutionRow.__table__,
    PixelFlowAgentProjectionMessageRow.__table__,
    PixelFlowAgentInterruptRow.__table__,
    PixelFlowAgentConversationStateRow.__table__,
)
