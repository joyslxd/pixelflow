"""新增 Agent Runtime 业务投影、队列与 Outbox 表。

迁移版本：20260724_01
前置版本：无
创建日期：2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260724_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_type() -> sa.DateTime:
    """MySQL 使用微秒精度，其他数据库保留时区类型声明。"""

    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    """只新增 Agent Runtime 五张表，不改动任何旧业务表。"""

    op.create_table(
        "pixelflow_agent_workflows",
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=False),
        sa.Column("stage_version", sa.Integer(), nullable=False),
        sa.Column("creation_contract_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("pending_external_job_json", sa.JSON(), nullable=True),
        sa.Column("latest_artifact_refs_json", sa.JSON(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.PrimaryKeyConstraint("workflow_id"),
    )
    op.create_index(
        "ix_pf_agent_workflows_owner_conversation",
        "pixelflow_agent_workflows",
        ["user_id", "conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_pf_agent_workflows_conversation_updated",
        "pixelflow_agent_workflows",
        ["conversation_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "pixelflow_agent_turns",
        sa.Column("inbox_sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("client_input_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("target_workflow_id", sa.String(length=64), nullable=True),
        sa.Column("decision_json", sa.JSON(), nullable=True),
        sa.Column("expected_context_version", sa.Integer(), nullable=False),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.PrimaryKeyConstraint("inbox_sequence"),
        sa.UniqueConstraint("turn_id"),
        sa.UniqueConstraint(
            "conversation_id",
            "client_input_id",
            name="uq_pf_agent_turns_conversation_client_input",
        ),
    )
    op.create_index(
        "ix_pf_agent_turns_owner_queue",
        "pixelflow_agent_turns",
        ["user_id", "conversation_id", "status", "inbox_sequence"],
        unique=False,
    )

    op.create_table(
        "pixelflow_agent_context_summaries",
        sa.Column("summary_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_summary_id", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("user_goals_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_decisions_json", sa.JSON(), nullable=False),
        sa.Column("negative_constraints_json", sa.JSON(), nullable=False),
        sa.Column("workflow_states_json", sa.JSON(), nullable=False),
        sa.Column("unresolved_questions_json", sa.JSON(), nullable=False),
        sa.Column("artifact_evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("covered_message_ids_json", sa.JSON(), nullable=False),
        sa.Column("covered_sequence_start", sa.Integer(), nullable=True),
        sa.Column("covered_sequence_end", sa.Integer(), nullable=True),
        sa.Column("compression_model", sa.String(length=128), nullable=False),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.PrimaryKeyConstraint("summary_id"),
        sa.UniqueConstraint(
            "conversation_id",
            "version",
            name="uq_pf_agent_summaries_conversation_version",
        ),
    )
    op.create_index(
        "ix_pf_agent_summaries_owner_version",
        "pixelflow_agent_context_summaries",
        ["user_id", "conversation_id", "version"],
        unique=False,
    )

    op.create_table(
        "pixelflow_agent_events",
        sa.Column("outbox_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", _timestamp_type(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("delivery_status", sa.String(length=24), nullable=False),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", _timestamp_type(), nullable=True),
        sa.Column("published_at", _timestamp_type(), nullable=True),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint(
            "conversation_id",
            "cursor",
            name="uq_pf_agent_events_conversation_cursor",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_pf_agent_events_conversation_sequence",
        ),
    )
    op.create_index(
        "ix_pf_agent_events_owner_sequence",
        "pixelflow_agent_events",
        ["user_id", "conversation_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_pf_agent_events_delivery",
        "pixelflow_agent_events",
        ["delivery_status", "lease_expires_at", "outbox_id"],
        unique=False,
    )

    op.create_table(
        "pixelflow_agent_operations",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("provider_job_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("stage_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("next_poll_at", _timestamp_type(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", _timestamp_type(), nullable=True),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_pf_agent_operations_idempotency_key"),
        sa.UniqueConstraint(
            "workflow_id",
            "stage",
            "stage_version",
            "attempt",
            name="uq_pf_agent_operations_workflow_stage_attempt",
        ),
    )
    op.create_index(
        "ix_pf_agent_operations_owner_workflow",
        "pixelflow_agent_operations",
        ["user_id", "conversation_id", "workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_pf_agent_operations_poll",
        "pixelflow_agent_operations",
        ["status", "next_poll_at", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """按创建逆序只删除本迁移拥有的表和索引。"""

    op.drop_index("ix_pf_agent_operations_poll", table_name="pixelflow_agent_operations")
    op.drop_index("ix_pf_agent_operations_owner_workflow", table_name="pixelflow_agent_operations")
    op.drop_table("pixelflow_agent_operations")

    op.drop_index("ix_pf_agent_events_delivery", table_name="pixelflow_agent_events")
    op.drop_index("ix_pf_agent_events_owner_sequence", table_name="pixelflow_agent_events")
    op.drop_table("pixelflow_agent_events")

    op.drop_index(
        "ix_pf_agent_summaries_owner_version",
        table_name="pixelflow_agent_context_summaries",
    )
    op.drop_table("pixelflow_agent_context_summaries")

    op.drop_index("ix_pf_agent_turns_owner_queue", table_name="pixelflow_agent_turns")
    op.drop_table("pixelflow_agent_turns")

    op.drop_index(
        "ix_pf_agent_workflows_conversation_updated",
        table_name="pixelflow_agent_workflows",
    )
    op.drop_index(
        "ix_pf_agent_workflows_owner_conversation",
        table_name="pixelflow_agent_workflows",
    )
    op.drop_table("pixelflow_agent_workflows")
