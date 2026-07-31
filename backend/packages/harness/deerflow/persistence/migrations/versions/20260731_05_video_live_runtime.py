"""新增视频 live Runtime 的状态、执行租约、消息和 interrupt 支撑表。

迁移版本：20260731_05
前置版本：20260725_04
创建日期：2026-07-31
"""

import re
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

_TABLE_COLUMN_CONTRACTS = {
    _VIDEO_STATE_TABLE: {
        "workflow_id": ("string", 64, False),
        "conversation_id": ("string", 64, False),
        "user_id": ("string", 64, False),
        "schema_version": ("integer", None, False),
        "state_kind": ("string", 64, False),
        "workflow_version": ("integer", None, False),
        "context_version": ("integer", None, False),
        "payload_json": ("json", None, False),
        "payload_sha256": ("string", 71, False),
        "last_turn_id": ("string", 64, True),
        "last_action_key": ("string", 255, True),
        "created_at": ("datetime", None, False),
        "updated_at": ("datetime", None, False),
    },
    _TURN_EXECUTION_TABLE: {
        "turn_id": ("string", 64, False),
        "conversation_id": ("string", 64, False),
        "user_id": ("string", 64, False),
        "attempt": ("integer", None, False),
        "lease_owner": ("string", 128, True),
        "lease_token": ("string", 36, True),
        "lease_expires_at": ("datetime", None, True),
        "next_attempt_at": ("datetime", None, True),
        "last_reason_code": ("string", 64, True),
        "created_at": ("datetime", None, False),
        "updated_at": ("datetime", None, False),
    },
    _PROJECTION_MESSAGE_TABLE: {
        "message_id": ("string", 64, False),
        "conversation_id": ("string", 64, False),
        "user_id": ("string", 64, False),
        "run_id": ("string", 64, True),
        "role": ("string", 16, False),
        "content": ("text", None, False),
        "payload_json": ("json", None, False),
        "created_at": ("datetime", None, False),
        "updated_at": ("datetime", None, False),
    },
    _INTERRUPT_TABLE: {
        "interrupt_id": ("string", 64, False),
        "conversation_id": ("string", 64, False),
        "user_id": ("string", 64, False),
        "workflow_id": ("string", 64, False),
        "turn_id": ("string", 64, False),
        "thread_id": ("string", 128, False),
        "checkpoint_ns": ("string", 128, False),
        "kind": ("string", 64, False),
        "reason_code": ("string", 64, False),
        "status": ("string", 16, False),
        "payload_json": ("json", None, False),
        "response_id": ("string", 64, True),
        "response_json": ("json", None, True),
        "opened_at": ("datetime", None, False),
        "closed_at": ("datetime", None, True),
    },
    _CONVERSATION_STATE_TABLE: {
        "conversation_id": ("string", 64, False),
        "user_id": ("string", 64, False),
        "active_workflow_id": ("string", 64, True),
        "created_at": ("datetime", None, False),
        "updated_at": ("datetime", None, False),
    },
}

_TABLE_PRIMARY_KEYS = {
    _VIDEO_STATE_TABLE: ("workflow_id",),
    _TURN_EXECUTION_TABLE: ("turn_id",),
    _PROJECTION_MESSAGE_TABLE: ("message_id",),
    _INTERRUPT_TABLE: ("interrupt_id",),
    _CONVERSATION_STATE_TABLE: ("conversation_id",),
}

_TABLE_CHECK_NAMES = {
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

_BUSINESS_INDEX_COLUMNS = {
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

_MIGRATION_MARKERS = {
    _VIDEO_STATE_TABLE: (
        "ix_pf_agent_video_states_revision_20260731_05",
        ("workflow_id",),
    ),
    _TURN_EXECUTION_TABLE: (
        "ix_pf_agent_turn_executions_revision_20260731_05",
        ("turn_id",),
    ),
    _PROJECTION_MESSAGE_TABLE: (
        "ix_pf_agent_projection_messages_revision_20260731_05",
        ("message_id",),
    ),
    _INTERRUPT_TABLE: (
        "ix_pf_agent_interrupts_revision_20260731_05",
        ("interrupt_id",),
    ),
    _CONVERSATION_STATE_TABLE: (
        "ix_pf_agent_conversation_states_revision_20260731_05",
        ("conversation_id",),
    ),
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


_TABLE_CHECK_SQL = {
    _VIDEO_STATE_TABLE: {
        "ck_pf_agent_video_states_payload_sha256": (
            _payload_sha256_check_expression("payload_sha256")
        ),
        "ck_pf_agent_video_states_workflow_version": "workflow_version >= 1",
    },
    _TURN_EXECUTION_TABLE: {
        "ck_pf_agent_turn_executions_attempt": "attempt >= 0",
        "ck_pf_agent_turn_executions_lease_fields": (
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)"
        ),
    },
    _PROJECTION_MESSAGE_TABLE: {
        "ck_pf_agent_projection_messages_role": "role IN ('assistant', 'system')",
    },
    _INTERRUPT_TABLE: {
        "ck_pf_agent_interrupts_status": (
            "status IN ('open', 'responded', 'closed')"
        ),
        "ck_pf_agent_interrupts_response_fields": (
            "(response_id IS NULL AND response_json IS NULL) "
            "OR (response_id IS NOT NULL AND response_json IS NOT NULL)"
        ),
    },
    _CONVERSATION_STATE_TABLE: {},
}


def _normalize_check_sql(sqltext: object) -> str:
    """归一化 SQLite/MySQL 反射出的 CHECK 文本后再比较语义令牌。"""

    normalized = str(sqltext).lower().replace("`", "").replace('"', "")
    normalized = re.sub(r"_[a-z0-9]+\s*(?=')", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.replace("(", "").replace(")", "")


def _column_type_contract(column_type: object) -> tuple[str, int | None]:
    """把方言类型归一化为类型族和字符串长度。"""

    if isinstance(column_type, sa.JSON):
        return ("json", None)
    if isinstance(column_type, sa.Text):
        return ("text", None)
    if isinstance(column_type, sa.String):
        return ("string", column_type.length)
    if isinstance(column_type, sa.DateTime):
        return ("datetime", None)
    if isinstance(column_type, sa.Integer):
        return ("integer", None)
    return (type(column_type).__name__.lower(), None)


def _reflected_indexes(table_name: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    """读取索引列序和唯一属性，供 marker 与业务索引共同校验。"""

    inspector = sa.inspect(op.get_bind())
    return {
        str(index["name"]): (
            tuple(str(column) for column in index["column_names"]),
            bool(index.get("unique", False)),
        )
        for index in inspector.get_indexes(table_name)
        if index["name"] is not None
    }


def _expected_indexes(table_name: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    """合并业务索引与 revision 私有 marker 合同。"""

    expected = {
        name: (columns, False)
        for name, columns in _BUSINESS_INDEX_COLUMNS[table_name].items()
    }
    marker_name, marker_columns = _MIGRATION_MARKERS[table_name]
    expected[marker_name] = (marker_columns, False)
    return expected


def _marker_state(table_name: str) -> str:
    """区分不存在、完整和伪造的 revision 私有 marker。"""

    marker_name, marker_columns = _MIGRATION_MARKERS[table_name]
    marker = _reflected_indexes(table_name).get(marker_name)
    if marker is None:
        return "absent"
    if marker != (marker_columns, False):
        return "invalid"
    return "valid"


def _validate_existing_table(table_name: str) -> None:
    """校验完整方言归一化 schema 指纹，禁止接管或删除残表。"""

    inspector = sa.inspect(op.get_bind())
    reflected_columns = {
        str(column["name"]): column
        for column in inspector.get_columns(table_name)
    }
    expected_columns = _TABLE_COLUMN_CONTRACTS[table_name]
    if set(reflected_columns) != set(expected_columns):
        raise RuntimeError(f"{table_name} schema contract column names mismatch")
    dialect_name = op.get_bind().dialect.name
    for column_name, (family, length, nullable) in expected_columns.items():
        column = reflected_columns[column_name]
        reflected_family, reflected_length = _column_type_contract(column["type"])
        reflected_contract = (
            reflected_family,
            reflected_length,
            bool(column["nullable"]),
        )
        if reflected_contract != (family, length, nullable):
            raise RuntimeError(
                f"{table_name} schema contract column contract mismatch: {column_name}"
            )
        if (
            family == "datetime"
            and dialect_name in {"mysql", "mariadb"}
            and getattr(column["type"], "fsp", None) != 6
        ):
            raise RuntimeError(
                f"{table_name} schema contract datetime precision mismatch: {column_name}"
            )
    primary_key = tuple(inspector.get_pk_constraint(table_name)["constrained_columns"])
    if primary_key != _TABLE_PRIMARY_KEYS[table_name]:
        raise RuntimeError(f"{table_name} schema contract primary key mismatch")
    checks = {
        str(constraint["name"]): _normalize_check_sql(constraint["sqltext"])
        for constraint in inspector.get_check_constraints(table_name)
        if constraint["name"] is not None
    }
    expected_checks = {
        name: _normalize_check_sql(sqltext)
        for name, sqltext in _TABLE_CHECK_SQL[table_name].items()
    }
    if set(checks) != _TABLE_CHECK_NAMES[table_name]:
        raise RuntimeError(f"{table_name} schema contract check constraint names mismatch")
    if checks != expected_checks:
        raise RuntimeError(
            f"{table_name} schema contract check constraint sqltext mismatch"
        )
    if _reflected_indexes(table_name) != _expected_indexes(table_name):
        raise RuntimeError(f"{table_name} schema contract index contract mismatch")


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


def _create_migration_marker(table_name: str) -> None:
    """仅由本 revision 创建与业务查询无关的私有 marker 索引。"""

    marker_name, marker_columns = _MIGRATION_MARKERS[table_name]
    op.create_index(
        marker_name,
        table_name,
        list(marker_columns),
        unique=False,
    )


def upgrade() -> None:
    """逐表在线校验，仅创建不存在的视频 live Runtime 支撑表。"""

    if op.get_context().as_sql:
        raise RuntimeError("video live Runtime migration requires online schema inspection")
    for table_name, create_table in _TABLE_CREATORS:
        inspector = sa.inspect(op.get_bind())
        if inspector.has_table(table_name):
            marker_state = _marker_state(table_name)
            if marker_state == "absent":
                raise RuntimeError(
                    f"{table_name} exists without revision 20260731_05 marker"
                )
            if marker_state == "invalid":
                raise RuntimeError(
                    f"{table_name} has an invalid revision 20260731_05 marker"
                )
            _validate_existing_table(table_name)
            continue
        create_table()
        _create_migration_marker(table_name)


def downgrade() -> None:
    """预检完整 schema 后仅删除带 revision 私有 marker 的支撑表。"""

    if op.get_context().as_sql:
        raise RuntimeError("video live Runtime migration requires online schema inspection")
    owned_tables: list[str] = []
    for table_name, _create_table in reversed(_TABLE_CREATORS):
        inspector = sa.inspect(op.get_bind())
        if not inspector.has_table(table_name):
            continue
        marker_state = _marker_state(table_name)
        if marker_state == "absent":
            continue
        if marker_state == "invalid":
            raise RuntimeError(
                f"{table_name} has an invalid revision 20260731_05 marker"
            )
        _validate_existing_table(table_name)
        owned_tables.append(table_name)
    for table_name in owned_tables:
        op.drop_table(table_name)
