"""新增视频 live Runtime 的状态、执行租约、消息和 interrupt 支撑表。

迁移版本：20260731_05
前置版本：20260725_04
创建日期：2026-07-31
"""

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260731_05"
down_revision: str | None = "20260725_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIDEO_STATE_TABLE = "pixelflow_agent_video_states"
_TURN_EXECUTION_TABLE = "pixelflow_agent_turn_executions"
_PROJECTION_MESSAGE_TABLE = "pixelflow_agent_projection_messages"
_INTERRUPT_TABLE = "pixelflow_agent_interrupts"
_CONVERSATION_STATE_TABLE = "pixelflow_agent_conversation_states"

_TABLE_COLUMNS = {
    _VIDEO_STATE_TABLE: {
        "workflow_id",
        "conversation_id",
        "user_id",
        "schema_version",
        "state_kind",
        "workflow_version",
        "context_version",
        "payload_json",
        "payload_sha256",
        "last_turn_id",
        "last_action_key",
        "created_at",
        "updated_at",
    },
    _TURN_EXECUTION_TABLE: {
        "turn_id",
        "conversation_id",
        "user_id",
        "attempt",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "next_attempt_at",
        "last_reason_code",
        "created_at",
        "updated_at",
    },
    _PROJECTION_MESSAGE_TABLE: {
        "message_id",
        "conversation_id",
        "user_id",
        "run_id",
        "role",
        "content",
        "payload_json",
        "created_at",
        "updated_at",
    },
    _INTERRUPT_TABLE: {
        "interrupt_id",
        "conversation_id",
        "user_id",
        "workflow_id",
        "turn_id",
        "thread_id",
        "checkpoint_ns",
        "kind",
        "reason_code",
        "status",
        "payload_json",
        "response_id",
        "response_json",
        "opened_at",
        "closed_at",
    },
    _CONVERSATION_STATE_TABLE: {
        "conversation_id",
        "user_id",
        "active_workflow_id",
        "created_at",
        "updated_at",
    },
}

_TABLE_PRIMARY_KEYS = {
    _VIDEO_STATE_TABLE: ("workflow_id",),
    _TURN_EXECUTION_TABLE: ("turn_id",),
    _PROJECTION_MESSAGE_TABLE: ("message_id",),
    _INTERRUPT_TABLE: ("interrupt_id",),
    _CONVERSATION_STATE_TABLE: ("conversation_id",),
}

_TABLE_CHECKS = {
    _VIDEO_STATE_TABLE: {
        "ck_pf_agent_video_states_payload_sha256",
        "ck_pf_agent_video_states_workflow_version",
    },
    _TURN_EXECUTION_TABLE: {
        "ck_pf_agent_turn_executions_attempt",
        "ck_pf_agent_turn_executions_lease_fields",
    },
    _PROJECTION_MESSAGE_TABLE: {"ck_pf_agent_projection_messages_role"},
    _INTERRUPT_TABLE: {
        "ck_pf_agent_interrupts_response_fields",
        "ck_pf_agent_interrupts_status",
    },
    _CONVERSATION_STATE_TABLE: set(),
}

_TABLE_INDEX_COLUMNS = {
    _VIDEO_STATE_TABLE: {
        "ix_pf_agent_video_states_owner_conversation": ("user_id", "conversation_id"),
    },
    _TURN_EXECUTION_TABLE: {
        "ix_pf_agent_turn_executions_owner_conversation": (
            "user_id",
            "conversation_id",
        ),
        "ix_pf_agent_turn_executions_recovery": (
            "next_attempt_at",
            "lease_expires_at",
        ),
    },
    _PROJECTION_MESSAGE_TABLE: {
        "ix_pf_agent_projection_messages_owner_conversation_created": (
            "user_id",
            "conversation_id",
            "created_at",
        ),
    },
    _INTERRUPT_TABLE: {
        "ix_pf_agent_interrupts_owner_conversation_status": (
            "user_id",
            "conversation_id",
            "status",
        ),
    },
    _CONVERSATION_STATE_TABLE: {
        "ix_pf_agent_conversation_states_owner": ("user_id", "conversation_id"),
    },
}

_OWNERSHIP_INDEXES = {
    _VIDEO_STATE_TABLE: "ix_pf_agent_video_states_owner_conversation",
    _TURN_EXECUTION_TABLE: "ix_pf_agent_turn_executions_owner_conversation",
    _PROJECTION_MESSAGE_TABLE: (
        "ix_pf_agent_projection_messages_owner_conversation_created"
    ),
    _INTERRUPT_TABLE: "ix_pf_agent_interrupts_owner_conversation_status",
    _CONVERSATION_STATE_TABLE: "ix_pf_agent_conversation_states_owner",
}


def _timestamp_type() -> sa.DateTime:
    """MySQL 使用微秒精度，其他数据库保留时区类型声明。"""

    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


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


def _validate_existing_table(table_name: str) -> None:
    """同名表必须完整匹配本迁移合同，禁止接管残表。"""

    inspector = sa.inspect(op.get_bind())
    columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
    primary_key = tuple(inspector.get_pk_constraint(table_name)["constrained_columns"])
    checks = {
        str(constraint["name"])
        for constraint in inspector.get_check_constraints(table_name)
        if constraint["name"] is not None
    }
    indexes = {
        str(index["name"]): (
            tuple(str(column) for column in index["column_names"]),
            bool(index["unique"]),
        )
        for index in inspector.get_indexes(table_name)
        if index["name"] is not None
    }
    if columns != _TABLE_COLUMNS[table_name]:
        raise RuntimeError(f"{table_name} exists with incomplete columns")
    if primary_key != _TABLE_PRIMARY_KEYS[table_name]:
        raise RuntimeError(f"{table_name} exists with an incompatible primary key")
    if checks != _TABLE_CHECKS[table_name]:
        raise RuntimeError(f"{table_name} exists with incomplete check constraints")
    expected_indexes = {
        name: (columns, False)
        for name, columns in _TABLE_INDEX_COLUMNS[table_name].items()
    }
    if indexes != expected_indexes:
        raise RuntimeError(f"{table_name} exists with incomplete migration indexes")


def _has_ownership_index(table_name: str) -> bool:
    """仅以本迁移专属 owner 索引识别允许回滚的表。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    ownership_index = _OWNERSHIP_INDEXES[table_name]
    expected_columns = _TABLE_INDEX_COLUMNS[table_name][ownership_index]
    return any(
        index["name"] == ownership_index
        and tuple(index["column_names"]) == expected_columns
        and not bool(index["unique"])
        for index in inspector.get_indexes(table_name)
    )


def _create_video_state_table() -> None:
    """创建视频 Workflow 权威状态表。"""

    op.create_table(
        _VIDEO_STATE_TABLE,
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state_kind", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=71), nullable=False),
        sa.Column("last_turn_id", sa.String(length=64), nullable=True),
        sa.Column("last_action_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.CheckConstraint(
            _payload_sha256_check_expression("payload_sha256"),
            name="ck_pf_agent_video_states_payload_sha256",
        ),
        sa.CheckConstraint(
            "workflow_version >= 1",
            name="ck_pf_agent_video_states_workflow_version",
        ),
        sa.PrimaryKeyConstraint("workflow_id"),
    )
    op.create_index(
        "ix_pf_agent_video_states_owner_conversation",
        _VIDEO_STATE_TABLE,
        ["user_id", "conversation_id"],
        unique=False,
    )


def _create_turn_execution_table() -> None:
    """创建 Turn 执行租约和恢复排期表。"""

    op.create_table(
        _TURN_EXECUTION_TABLE,
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", _timestamp_type(), nullable=True),
        sa.Column("next_attempt_at", _timestamp_type(), nullable=True),
        sa.Column("last_reason_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.CheckConstraint(
            "attempt >= 0",
            name="ck_pf_agent_turn_executions_attempt",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_pf_agent_turn_executions_lease_fields",
        ),
        sa.PrimaryKeyConstraint("turn_id"),
    )
    op.create_index(
        "ix_pf_agent_turn_executions_owner_conversation",
        _TURN_EXECUTION_TABLE,
        ["user_id", "conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_pf_agent_turn_executions_recovery",
        _TURN_EXECUTION_TABLE,
        ["next_attempt_at", "lease_expires_at"],
        unique=False,
    )


def _create_projection_message_table() -> None:
    """创建助手和系统消息权威投影表。"""

    op.create_table(
        _PROJECTION_MESSAGE_TABLE,
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.CheckConstraint(
            "role IN ('assistant', 'system')",
            name="ck_pf_agent_projection_messages_role",
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_pf_agent_projection_messages_owner_conversation_created",
        _PROJECTION_MESSAGE_TABLE,
        ["user_id", "conversation_id", "created_at"],
        unique=False,
    )


def _create_interrupt_table() -> None:
    """创建人工确认 interrupt 与响应表。"""

    op.create_table(
        _INTERRUPT_TABLE,
        sa.Column("interrupt_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("response_id", sa.String(length=64), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("opened_at", _timestamp_type(), nullable=False),
        sa.Column("closed_at", _timestamp_type(), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'responded', 'closed')",
            name="ck_pf_agent_interrupts_status",
        ),
        sa.CheckConstraint(
            "(response_id IS NULL AND response_json IS NULL) "
            "OR (response_id IS NOT NULL AND response_json IS NOT NULL)",
            name="ck_pf_agent_interrupts_response_fields",
        ),
        sa.PrimaryKeyConstraint("interrupt_id"),
    )
    op.create_index(
        "ix_pf_agent_interrupts_owner_conversation_status",
        _INTERRUPT_TABLE,
        ["user_id", "conversation_id", "status"],
        unique=False,
    )


def _create_conversation_state_table() -> None:
    """创建会话当前活动 Workflow 的锁对象表。"""

    op.create_table(
        _CONVERSATION_STATE_TABLE,
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("active_workflow_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", _timestamp_type(), nullable=False),
        sa.Column("updated_at", _timestamp_type(), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_pf_agent_conversation_states_owner",
        _CONVERSATION_STATE_TABLE,
        ["user_id", "conversation_id"],
        unique=False,
    )


_TABLE_CREATORS: tuple[tuple[str, Callable[[], None]], ...] = (
    (_VIDEO_STATE_TABLE, _create_video_state_table),
    (_TURN_EXECUTION_TABLE, _create_turn_execution_table),
    (_PROJECTION_MESSAGE_TABLE, _create_projection_message_table),
    (_INTERRUPT_TABLE, _create_interrupt_table),
    (_CONVERSATION_STATE_TABLE, _create_conversation_state_table),
)


def upgrade() -> None:
    """逐表在线校验，仅创建不存在的视频 live Runtime 支撑表。"""

    if op.get_context().as_sql:
        raise RuntimeError("video live Runtime migration requires online schema inspection")
    for table_name, create_table in _TABLE_CREATORS:
        inspector = sa.inspect(op.get_bind())
        if inspector.has_table(table_name):
            _validate_existing_table(table_name)
            continue
        create_table()


def downgrade() -> None:
    """仅删除带本迁移 owner 索引的五张支撑表。"""

    if op.get_context().as_sql:
        raise RuntimeError("video live Runtime migration requires online schema inspection")
    for table_name, _create_table in reversed(_TABLE_CREATORS):
        if _has_ownership_index(table_name):
            op.drop_table(table_name)
