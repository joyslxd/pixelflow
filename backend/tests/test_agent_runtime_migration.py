"""Agent Runtime 行模型与 additive migration 结构合同。"""

import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = BACKEND_ROOT / "packages" / "harness" / "deerflow" / "persistence" / "migrations"

EXPECTED_TABLE_COLUMNS = {
    "pixelflow_agent_workflows": {
        "workflow_id",
        "conversation_id",
        "user_id",
        "kind",
        "status",
        "current_stage",
        "stage_version",
        "creation_contract_snapshot_json",
        "pending_external_job_json",
        "latest_artifact_refs_json",
        "context_version",
        "created_at",
        "updated_at",
    },
    "pixelflow_agent_turns": {
        "inbox_sequence",
        "turn_id",
        "conversation_id",
        "user_id",
        "client_input_id",
        "status",
        "target_workflow_id",
        "decision_json",
        "expected_context_version",
        "created_at",
        "updated_at",
    },
    "pixelflow_agent_compaction_locks": {
        "conversation_id",
        "user_id",
        "state",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "created_at",
        "updated_at",
    },
    "pixelflow_agent_context_summaries": {
        "summary_id",
        "conversation_id",
        "user_id",
        "version",
        "previous_summary_id",
        "content_hash",
        "user_goals_json",
        "confirmed_decisions_json",
        "negative_constraints_json",
        "workflow_states_json",
        "unresolved_questions_json",
        "artifact_evidence_refs_json",
        "covered_message_ids_json",
        "covered_sequence_start",
        "covered_sequence_end",
        "compression_model",
        "created_at",
    },
    "pixelflow_agent_events": {
        "outbox_id",
        "schema_version",
        "event_id",
        "sequence",
        "cursor",
        "conversation_id",
        "user_id",
        "run_id",
        "occurred_at",
        "event_type",
        "payload_json",
        "delivery_status",
        "delivery_attempts",
        "lease_owner",
        "lease_expires_at",
        "published_at",
    },
    "pixelflow_agent_operations": {
        "job_id",
        "provider_job_id",
        "workflow_id",
        "conversation_id",
        "user_id",
        "stage",
        "stage_version",
        "status",
        "attempt",
        "request_hash",
        "idempotency_key",
        "next_poll_at",
        "lease_owner",
        "lease_expires_at",
        "created_at",
        "updated_at",
    },
}

EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS = {
    "pixelflow_agent_video_states": {
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
    "pixelflow_agent_turn_executions": {
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
    "pixelflow_agent_projection_messages": {
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
    "pixelflow_agent_interrupts": {
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
    "pixelflow_agent_conversation_states": {
        "conversation_id",
        "user_id",
        "active_workflow_id",
        "created_at",
        "updated_at",
    },
}

EXPECTED_RUNTIME_SUPPORT_TABLE_NAMES = {"pixelflow_agent_context_payloads"} | set(
    EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS
)

EXPECTED_MIGRATION_TABLE_COLUMNS = dict(EXPECTED_TABLE_COLUMNS)
EXPECTED_MIGRATION_TABLE_COLUMNS.update(EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS)

EXPECTED_PRIMARY_KEYS = {
    "pixelflow_agent_workflows": ("workflow_id",),
    "pixelflow_agent_turns": ("inbox_sequence",),
    "pixelflow_agent_compaction_locks": ("conversation_id",),
    "pixelflow_agent_context_summaries": ("summary_id",),
    "pixelflow_agent_events": ("outbox_id",),
    "pixelflow_agent_operations": ("job_id",),
    "pixelflow_agent_video_states": ("workflow_id",),
    "pixelflow_agent_turn_executions": ("turn_id",),
    "pixelflow_agent_projection_messages": ("message_id",),
    "pixelflow_agent_interrupts": ("interrupt_id",),
    "pixelflow_agent_conversation_states": ("conversation_id",),
}

EXPECTED_AUTOINCREMENT_COLUMNS = {
    "pixelflow_agent_turns": {"inbox_sequence"},
    "pixelflow_agent_events": {"outbox_id"},
}

EXPECTED_NULLABLE_COLUMNS = {
    "pixelflow_agent_workflows": {"pending_external_job_json"},
    "pixelflow_agent_turns": {"target_workflow_id", "decision_json"},
    "pixelflow_agent_compaction_locks": {
        "lease_owner",
        "lease_token",
        "lease_expires_at",
    },
    "pixelflow_agent_context_summaries": {
        "previous_summary_id",
        "covered_sequence_start",
        "covered_sequence_end",
    },
    "pixelflow_agent_events": {"lease_owner", "lease_expires_at", "published_at"},
    "pixelflow_agent_operations": {
        "provider_job_id",
        "next_poll_at",
        "lease_owner",
        "lease_expires_at",
    },
    "pixelflow_agent_video_states": {"last_turn_id", "last_action_key"},
    "pixelflow_agent_turn_executions": {
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "next_attempt_at",
        "last_reason_code",
    },
    "pixelflow_agent_projection_messages": {"run_id"},
    "pixelflow_agent_interrupts": {"response_id", "response_json", "closed_at"},
    "pixelflow_agent_conversation_states": {"active_workflow_id"},
}

EXPECTED_COLUMN_TYPE_FAMILIES = {
    "pixelflow_agent_workflows": {
        "workflow_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "kind": "string",
        "status": "string",
        "current_stage": "string",
        "stage_version": "integer",
        "creation_contract_snapshot_json": "json",
        "pending_external_job_json": "json",
        "latest_artifact_refs_json": "json",
        "context_version": "integer",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_turns": {
        "inbox_sequence": "integer",
        "turn_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "client_input_id": "string",
        "status": "string",
        "target_workflow_id": "string",
        "decision_json": "json",
        "expected_context_version": "integer",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_compaction_locks": {
        "conversation_id": "string",
        "user_id": "string",
        "state": "string",
        "lease_owner": "string",
        "lease_token": "string",
        "lease_expires_at": "datetime",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_context_summaries": {
        "summary_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "version": "integer",
        "previous_summary_id": "string",
        "content_hash": "string",
        "user_goals_json": "json",
        "confirmed_decisions_json": "json",
        "negative_constraints_json": "json",
        "workflow_states_json": "json",
        "unresolved_questions_json": "json",
        "artifact_evidence_refs_json": "json",
        "covered_message_ids_json": "json",
        "covered_sequence_start": "integer",
        "covered_sequence_end": "integer",
        "compression_model": "string",
        "created_at": "datetime",
    },
    "pixelflow_agent_events": {
        "outbox_id": "integer",
        "schema_version": "integer",
        "event_id": "string",
        "sequence": "integer",
        "cursor": "string",
        "conversation_id": "string",
        "user_id": "string",
        "run_id": "string",
        "occurred_at": "datetime",
        "event_type": "string",
        "payload_json": "json",
        "delivery_status": "string",
        "delivery_attempts": "integer",
        "lease_owner": "string",
        "lease_expires_at": "datetime",
        "published_at": "datetime",
    },
    "pixelflow_agent_operations": {
        "job_id": "string",
        "provider_job_id": "string",
        "workflow_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "stage": "string",
        "stage_version": "integer",
        "status": "string",
        "attempt": "integer",
        "request_hash": "string",
        "idempotency_key": "string",
        "next_poll_at": "datetime",
        "lease_owner": "string",
        "lease_expires_at": "datetime",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_video_states": {
        "workflow_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "schema_version": "integer",
        "state_kind": "string",
        "workflow_version": "integer",
        "context_version": "integer",
        "payload_json": "json",
        "payload_sha256": "string",
        "last_turn_id": "string",
        "last_action_key": "string",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_turn_executions": {
        "turn_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "attempt": "integer",
        "lease_owner": "string",
        "lease_token": "string",
        "lease_expires_at": "datetime",
        "next_attempt_at": "datetime",
        "last_reason_code": "string",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_projection_messages": {
        "message_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "run_id": "string",
        "role": "string",
        "content": "string",
        "payload_json": "json",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "pixelflow_agent_interrupts": {
        "interrupt_id": "string",
        "conversation_id": "string",
        "user_id": "string",
        "workflow_id": "string",
        "turn_id": "string",
        "thread_id": "string",
        "checkpoint_ns": "string",
        "kind": "string",
        "reason_code": "string",
        "status": "string",
        "payload_json": "json",
        "response_id": "string",
        "response_json": "json",
        "opened_at": "datetime",
        "closed_at": "datetime",
    },
    "pixelflow_agent_conversation_states": {
        "conversation_id": "string",
        "user_id": "string",
        "active_workflow_id": "string",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
}

EXPECTED_UNIQUE_COLUMN_SETS = {
    "pixelflow_agent_workflows": set(),
    "pixelflow_agent_turns": {
        frozenset({"turn_id"}),
        frozenset({"conversation_id", "client_input_id"}),
    },
    "pixelflow_agent_compaction_locks": set(),
    "pixelflow_agent_context_summaries": {
        frozenset({"conversation_id", "version"}),
    },
    "pixelflow_agent_events": {
        frozenset({"event_id"}),
        frozenset({"conversation_id", "cursor"}),
        frozenset({"conversation_id", "sequence"}),
    },
    "pixelflow_agent_operations": {
        frozenset({"idempotency_key"}),
        frozenset({"workflow_id", "stage", "stage_version", "attempt"}),
    },
    "pixelflow_agent_video_states": set(),
    "pixelflow_agent_turn_executions": set(),
    "pixelflow_agent_projection_messages": set(),
    "pixelflow_agent_interrupts": set(),
    "pixelflow_agent_conversation_states": set(),
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "pixelflow_agent_turns": {"uq_pf_agent_turns_conversation_client_input"},
    "pixelflow_agent_context_summaries": {"uq_pf_agent_summaries_conversation_version"},
    "pixelflow_agent_events": {
        "uq_pf_agent_events_conversation_cursor",
        "uq_pf_agent_events_conversation_sequence",
    },
    "pixelflow_agent_operations": {
        "uq_pf_agent_operations_idempotency_key",
        "uq_pf_agent_operations_workflow_stage_attempt",
    },
}

EXPECTED_CHECK_CONSTRAINTS = {
    "pixelflow_agent_compaction_locks": {
        "ck_pf_agent_compaction_locks_lease_fields",
        "ck_pf_agent_compaction_locks_state",
    },
    "pixelflow_agent_video_states": {
        "ck_pf_agent_video_states_payload_sha256",
        "ck_pf_agent_video_states_workflow_version",
    },
    "pixelflow_agent_turn_executions": {
        "ck_pf_agent_turn_executions_attempt",
        "ck_pf_agent_turn_executions_lease_fields",
    },
    "pixelflow_agent_projection_messages": {
        "ck_pf_agent_projection_messages_role",
    },
    "pixelflow_agent_interrupts": {
        "ck_pf_agent_interrupts_response_fields",
        "ck_pf_agent_interrupts_status",
    },
}

EXPECTED_INDEXES = {
    "pixelflow_agent_workflows": {
        "ix_pf_agent_workflows_conversation_updated",
        "ix_pf_agent_workflows_owner_conversation",
    },
    "pixelflow_agent_turns": {"ix_pf_agent_turns_owner_queue"},
    "pixelflow_agent_compaction_locks": {
        "ix_pf_agent_compaction_locks_owner_expiry",
    },
    "pixelflow_agent_context_summaries": {"ix_pf_agent_summaries_owner_version"},
    "pixelflow_agent_events": {
        "ix_pf_agent_events_delivery",
        "ix_pf_agent_events_owner_sequence",
    },
    "pixelflow_agent_operations": {
        "ix_pf_agent_operations_owner_workflow",
        "ix_pf_agent_operations_poll",
    },
    "pixelflow_agent_video_states": {
        "ix_pf_agent_video_states_owner_conversation",
    },
    "pixelflow_agent_turn_executions": {
        "ix_pf_agent_turn_executions_owner_conversation",
        "ix_pf_agent_turn_executions_recovery",
    },
    "pixelflow_agent_projection_messages": {
        "ix_pf_agent_projection_messages_owner_conversation_created",
    },
    "pixelflow_agent_interrupts": {
        "ix_pf_agent_interrupts_owner_conversation_status",
    },
    "pixelflow_agent_conversation_states": {
        "ix_pf_agent_conversation_states_owner",
    },
}

EXPECTED_VIDEO_LIVE_OWNER_INDEXES = {
    "pixelflow_agent_video_states": "ix_pf_agent_video_states_owner_conversation",
    "pixelflow_agent_turn_executions": (
        "ix_pf_agent_turn_executions_owner_conversation"
    ),
    "pixelflow_agent_projection_messages": (
        "ix_pf_agent_projection_messages_owner_conversation_created"
    ),
    "pixelflow_agent_interrupts": (
        "ix_pf_agent_interrupts_owner_conversation_status"
    ),
    "pixelflow_agent_conversation_states": "ix_pf_agent_conversation_states_owner",
}

EXPECTED_VIDEO_LIVE_INDEX_COLUMNS = {
    "pixelflow_agent_video_states": {
        "ix_pf_agent_video_states_owner_conversation": ("user_id", "conversation_id"),
    },
    "pixelflow_agent_turn_executions": {
        "ix_pf_agent_turn_executions_owner_conversation": (
            "user_id",
            "conversation_id",
        ),
        "ix_pf_agent_turn_executions_recovery": (
            "next_attempt_at",
            "lease_expires_at",
        ),
    },
    "pixelflow_agent_projection_messages": {
        "ix_pf_agent_projection_messages_owner_conversation_created": (
            "user_id",
            "conversation_id",
            "created_at",
        ),
    },
    "pixelflow_agent_interrupts": {
        "ix_pf_agent_interrupts_owner_conversation_status": (
            "user_id",
            "conversation_id",
            "status",
        ),
    },
    "pixelflow_agent_conversation_states": {
        "ix_pf_agent_conversation_states_owner": ("user_id", "conversation_id"),
    },
}


def _migration_config(database_path: Path) -> Config:
    config = Config(str(MIGRATION_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    return config


def test_agent_runtime_migration_keeps_existing_application_loggers_enabled(tmp_path: Path) -> None:
    """执行 migration 时不得禁用测试收集阶段已创建的业务 logger。"""

    logger = logging.getLogger("pixelflow.m13.logging-sentinel")
    original_disabled = logger.disabled
    logger.disabled = False
    try:
        command.upgrade(_migration_config(tmp_path / "logging-sentinel.db"), "head")

        assert logger.disabled is False
    finally:
        logger.disabled = original_disabled


def test_conversation_orchestration_migration_preserves_legacy_rows(tmp_path: Path) -> None:
    """旧对话升级后固定归旧 v2，回滚只移除 M13.1 自有字段。"""

    database_path = tmp_path / "conversation-orchestration.db"
    engine = create_engine(_sync_database_url(database_path))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE pixelflow_conversations (conversation_id VARCHAR(64) PRIMARY KEY, title VARCHAR(255) NOT NULL)"))
        connection.execute(text("INSERT INTO pixelflow_conversations (conversation_id, title) VALUES ('legacy-1', '保留旧对话')"))
    engine.dispose()

    config = _migration_config(database_path)
    command.upgrade(config, "head")

    engine = create_engine(_sync_database_url(database_path))
    inspector = inspect(engine)
    assert "pixelflow_agent_context_payloads" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("pixelflow_conversations")}
    assert {"orchestration_mode", "orchestration_version"}.issubset(columns)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT title, orchestration_mode, orchestration_version FROM pixelflow_conversations WHERE conversation_id='legacy-1'")).one()
        assert tuple(row) == ("保留旧对话", "frontend_v2", 1)
    engine.dispose()

    command.downgrade(config, "20260725_03")

    engine = create_engine(_sync_database_url(database_path))
    inspector = inspect(engine)
    assert "pixelflow_agent_context_payloads" not in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("pixelflow_conversations")}
    assert "orchestration_mode" not in columns
    assert "orchestration_version" not in columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM pixelflow_conversations WHERE conversation_id='legacy-1'")).scalar_one() == "保留旧对话"
    engine.dispose()


def _sync_database_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.as_posix()}"


def _type_family(column_type) -> str:
    if isinstance(column_type, JSON):
        return "json"
    if isinstance(column_type, DateTime):
        return "datetime"
    if isinstance(column_type, Integer):
        return "integer"
    if isinstance(column_type, String):
        return "string"
    raise AssertionError(f"未声明的字段类型：{column_type!r}")


def test_agent_runtime_models_register_frozen_tables_and_mysql_bootstrap():
    """ORM metadata 与 PixelFlow 独立 MySQL 初始化必须使用同一组新表。"""

    from pixelflow.agent_runtime.persistence.models import (
        AGENT_RUNTIME_SUPPORT_TABLES,
        AGENT_RUNTIME_TABLES,
    )
    from pixelflow.tasks.mysql import PIXELFLOW_TASK_TABLES

    runtime_tables = {table.name: table for table in AGENT_RUNTIME_TABLES}
    support_tables = {table.name: table for table in AGENT_RUNTIME_SUPPORT_TABLES}
    assert set(runtime_tables) == set(EXPECTED_TABLE_COLUMNS)
    assert set(support_tables) == EXPECTED_RUNTIME_SUPPORT_TABLE_NAMES
    assert {table.name for table in PIXELFLOW_TASK_TABLES}.issuperset(
        runtime_tables | support_tables
    )
    inspected_tables = runtime_tables | {
        table_name: support_tables[table_name]
        for table_name in EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS
    }
    for table_name, expected_columns in EXPECTED_MIGRATION_TABLE_COLUMNS.items():
        table = inspected_tables[table_name]
        assert {column.name for column in table.columns} == expected_columns
        assert set(EXPECTED_COLUMN_TYPE_FAMILIES[table_name]) == expected_columns
        assert tuple(column.name for column in table.primary_key.columns) == EXPECTED_PRIMARY_KEYS[table_name]
        for column in table.columns:
            assert _type_family(column.type) == EXPECTED_COLUMN_TYPE_FAMILIES[table_name][column.name]
            assert column.nullable is (column.name in EXPECTED_NULLABLE_COLUMNS[table_name])
        assert {column.name for column in table.columns if column.autoincrement is True} == EXPECTED_AUTOINCREMENT_COLUMNS.get(table_name, set())
        unique_constraints = {frozenset(column.name for column in constraint.columns) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
        assert unique_constraints == EXPECTED_UNIQUE_COLUMN_SETS[table_name]
        unique_names = {constraint.name for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
        assert unique_names.issuperset(EXPECTED_UNIQUE_CONSTRAINTS.get(table_name, set()))
        check_names = {constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
        assert check_names == EXPECTED_CHECK_CONSTRAINTS.get(table_name, set())
        assert {index.name for index in table.indexes} == EXPECTED_INDEXES.get(table_name, set())
        if table_name in EXPECTED_VIDEO_LIVE_INDEX_COLUMNS:
            assert {
                index.name: tuple(column.name for column in index.columns)
                for index in table.indexes
            } == EXPECTED_VIDEO_LIVE_INDEX_COLUMNS[table_name]


def test_agent_runtime_migration_upgrade_and_downgrade_are_additive(tmp_path):
    """升级和回滚只处理新表，不能删除或改写旧业务数据。"""

    database_path = tmp_path / "agent-runtime-migration.db"
    sync_url = _sync_database_url(database_path)
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_sentinel (id INTEGER PRIMARY KEY, value VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO legacy_sentinel (id, value) VALUES (1, 'keep-me')"))
    engine.dispose()

    config = _migration_config(database_path)
    command.upgrade(config, "head")

    engine = create_engine(sync_url)
    inspector = inspect(engine)
    assert set(EXPECTED_MIGRATION_TABLE_COLUMNS).issubset(inspector.get_table_names())
    for table_name, expected_columns in EXPECTED_MIGRATION_TABLE_COLUMNS.items():
        reflected_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert set(reflected_columns) == expected_columns
        assert set(EXPECTED_COLUMN_TYPE_FAMILIES[table_name]) == expected_columns
        assert tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]) == EXPECTED_PRIMARY_KEYS[table_name]
        for column_name, column in reflected_columns.items():
            assert _type_family(column["type"]) == EXPECTED_COLUMN_TYPE_FAMILIES[table_name][column_name]
            assert column["nullable"] is (column_name in EXPECTED_NULLABLE_COLUMNS[table_name])
        unique_column_sets = {frozenset(item["column_names"]) for item in inspector.get_unique_constraints(table_name)}
        assert unique_column_sets == EXPECTED_UNIQUE_COLUMN_SETS[table_name]
        unique_names = {item["name"] for item in inspector.get_unique_constraints(table_name)}
        assert unique_names.issuperset(EXPECTED_UNIQUE_CONSTRAINTS.get(table_name, set()))
        check_names = {item["name"] for item in inspector.get_check_constraints(table_name)}
        assert check_names == EXPECTED_CHECK_CONSTRAINTS.get(table_name, set())
        index_names = {item["name"] for item in inspector.get_indexes(table_name)}
        assert index_names == EXPECTED_INDEXES.get(table_name, set())
        if table_name in EXPECTED_VIDEO_LIVE_INDEX_COLUMNS:
            assert {
                item["name"]: tuple(item["column_names"])
                for item in inspector.get_indexes(table_name)
            } == EXPECTED_VIDEO_LIVE_INDEX_COLUMNS[table_name]
    with engine.begin() as connection:
        for suffix in ("a", "b"):
            connection.execute(
                text(
                    "INSERT INTO pixelflow_agent_turns ("
                    "turn_id, conversation_id, user_id, client_input_id, status, "
                    "expected_context_version, created_at, updated_at"
                    ") VALUES ("
                    ":turn_id, 'conversation-1', 'user-1', :client_input_id, 'accepted', "
                    "0, '2026-07-24T00:00:00Z', '2026-07-24T00:00:00Z'"
                    ")"
                ),
                {"turn_id": f"turn-{suffix}", "client_input_id": f"00000000-0000-0000-0000-00000000000{suffix}"},
            )
            connection.execute(
                text(
                    "INSERT INTO pixelflow_agent_events ("
                    "schema_version, event_id, sequence, cursor, conversation_id, "
                    "user_id, run_id, occurred_at, event_type, payload_json, "
                    "delivery_status, delivery_attempts"
                    ") VALUES ("
                    "1, :event_id, :sequence, :cursor, 'conversation-1', "
                    "'user-1', 'run-1', '2026-07-24T00:00:00Z', "
                    "'run.state_changed', '{}', 'pending', 0"
                    ")"
                ),
                {"event_id": f"event-{suffix}", "sequence": 1 if suffix == "a" else 2, "cursor": f"cursor-{suffix}"},
            )
        turn_sequences = connection.execute(text("SELECT inbox_sequence FROM pixelflow_agent_turns ORDER BY inbox_sequence")).scalars().all()
        event_ids = connection.execute(text("SELECT outbox_id FROM pixelflow_agent_events ORDER BY outbox_id")).scalars().all()
        assert turn_sequences == [1, 2]
        assert event_ids == [1, 2]
        assert connection.execute(text("SELECT value FROM legacy_sentinel WHERE id = 1")).scalar_one() == "keep-me"
    engine.dispose()

    command.downgrade(config, "base")

    engine = create_engine(sync_url)
    inspector = inspect(engine)
    remaining_tables = set(inspector.get_table_names())
    assert not remaining_tables.intersection(EXPECTED_MIGRATION_TABLE_COLUMNS)
    assert "legacy_sentinel" in remaining_tables
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM legacy_sentinel WHERE id = 1")).scalar_one() == "keep-me"
    engine.dispose()


def test_video_live_migration_downgrade_preserves_existing_runtime_rows(tmp_path: Path) -> None:
    """只回滚本版本五表，并保留 sentinel 与既有 Runtime 数据。"""

    database_path = tmp_path / "video-live-downgrade.db"
    config = _migration_config(database_path)
    command.upgrade(config, "20260725_04")

    engine = create_engine(_sync_database_url(database_path))
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE legacy_sentinel "
                "(id INTEGER PRIMARY KEY, value VARCHAR(32) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO legacy_sentinel (id, value) VALUES (1, 'keep-me')")
        )
        connection.execute(
            text(
                "INSERT INTO pixelflow_agent_workflows ("
                "workflow_id, conversation_id, user_id, kind, status, current_stage, "
                "stage_version, creation_contract_snapshot_json, "
                "latest_artifact_refs_json, context_version, created_at, updated_at"
                ") VALUES ("
                "'workflow-legacy', 'conversation-1', 'user-1', 'video', 'running', "
                "'planning', 1, '{}', '[]', 1, "
                "'2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z'"
                ")"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    command.downgrade(config, "20260725_04")

    engine = create_engine(_sync_database_url(database_path))
    inspector = inspect(engine)
    remaining_tables = set(inspector.get_table_names())
    assert not remaining_tables.intersection(EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS)
    assert set(EXPECTED_TABLE_COLUMNS).issubset(remaining_tables)
    assert "pixelflow_agent_context_payloads" in remaining_tables
    assert "legacy_sentinel" in remaining_tables
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT value FROM legacy_sentinel WHERE id = 1")
        ).scalar_one() == "keep-me"
        assert connection.execute(
            text(
                "SELECT status FROM pixelflow_agent_workflows "
                "WHERE workflow_id = 'workflow-legacy'"
            )
        ).scalar_one() == "running"
    engine.dispose()


@pytest.mark.parametrize("table_name", EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS)
def test_video_live_migration_rejects_incomplete_same_name_table(
    tmp_path: Path,
    table_name: str,
) -> None:
    """同名残表缺少完整字段或 owner 标记时必须失败关闭。"""

    database_path = tmp_path / f"incomplete-{table_name}.db"
    config = _migration_config(database_path)
    command.upgrade(config, "20260725_04")
    engine = create_engine(_sync_database_url(database_path))
    with engine.begin() as connection:
        connection.execute(
            text(f"CREATE TABLE {table_name} (legacy_id VARCHAR(64) PRIMARY KEY)")
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match=table_name):
        command.upgrade(config, "head")


@pytest.mark.parametrize("table_name", EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS)
def test_video_live_migration_rejects_spoofed_owner_index(
    tmp_path: Path,
    table_name: str,
) -> None:
    """owner 索引名称正确但列错误时仍须失败关闭。"""

    from pixelflow.agent_runtime.persistence.models import AGENT_RUNTIME_SUPPORT_TABLES

    database_path = tmp_path / f"spoofed-owner-{table_name}.db"
    config = _migration_config(database_path)
    command.upgrade(config, "20260725_04")
    engine = create_engine(_sync_database_url(database_path))
    support_tables = {table.name: table for table in AGENT_RUNTIME_SUPPORT_TABLES}
    support_tables[table_name].metadata.create_all(
        engine,
        tables=[support_tables[table_name]],
    )
    owner_index = EXPECTED_VIDEO_LIVE_OWNER_INDEXES[table_name]
    primary_key = EXPECTED_PRIMARY_KEYS[table_name][0]
    with engine.begin() as connection:
        connection.execute(text(f'DROP INDEX "{owner_index}"'))
        connection.execute(
            text(f'CREATE INDEX "{owner_index}" ON "{table_name}" ("{primary_key}")')
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match=table_name):
        command.upgrade(config, "head")


def test_video_live_migration_downgrade_protects_unrecognized_legacy_tables(
    tmp_path: Path,
) -> None:
    """owner 索引列不匹配的同名旧表不能在回滚时被删除。"""

    database_path = tmp_path / "unowned-video-live-tables.db"
    config = _migration_config(database_path)
    engine = create_engine(_sync_database_url(database_path))
    with engine.begin() as connection:
        for table_name in EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS:
            connection.execute(
                text(f"CREATE TABLE {table_name} (legacy_id VARCHAR(64) PRIMARY KEY)")
            )
            connection.execute(
                text(f"INSERT INTO {table_name} (legacy_id) VALUES ('keep-me')")
            )
            owner_index = EXPECTED_VIDEO_LIVE_OWNER_INDEXES[table_name]
            connection.execute(
                text(
                    f'CREATE INDEX "{owner_index}" '
                    f'ON "{table_name}" ("legacy_id")'
                )
            )
    engine.dispose()
    command.stamp(config, "20260731_05")

    command.downgrade(config, "20260725_04")

    engine = create_engine(_sync_database_url(database_path))
    inspector = inspect(engine)
    assert set(EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS).issubset(inspector.get_table_names())
    with engine.connect() as connection:
        for table_name in EXPECTED_VIDEO_LIVE_SUPPORT_COLUMNS:
            assert connection.execute(
                text(f"SELECT legacy_id FROM {table_name}")
            ).scalar_one() == "keep-me"
    engine.dispose()


def test_video_live_migration_enforces_state_and_lease_constraints(tmp_path: Path) -> None:
    """迁移必须真实拒绝非法摘要、租约、角色、状态和响应组合。"""

    database_path = tmp_path / "video-live-checks.db"
    config = _migration_config(database_path)
    command.upgrade(config, "head")
    engine = create_engine(_sync_database_url(database_path))

    invalid_statements = (
        "INSERT INTO pixelflow_agent_video_states ("
        "workflow_id, conversation_id, user_id, schema_version, state_kind, "
        "workflow_version, context_version, payload_json, payload_sha256, created_at, updated_at"
        ") VALUES ('workflow-bad-hash', 'conversation-1', 'user-1', 1, 'planning', "
        "1, 1, '{}', 'sha256:ABC', '2026-07-31', '2026-07-31')",
        "INSERT INTO pixelflow_agent_video_states ("
        "workflow_id, conversation_id, user_id, schema_version, state_kind, "
        "workflow_version, context_version, payload_json, payload_sha256, created_at, updated_at"
        ") VALUES ('workflow-bad-version', 'conversation-1', 'user-1', 1, 'planning', "
        "0, 1, '{}', 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
        "'2026-07-31', '2026-07-31')",
        "INSERT INTO pixelflow_agent_turn_executions ("
        "turn_id, conversation_id, user_id, attempt, lease_owner, created_at, updated_at"
        ") VALUES ('turn-partial-lease', 'conversation-1', 'user-1', 0, 'worker-1', "
        "'2026-07-31', '2026-07-31')",
        "INSERT INTO pixelflow_agent_turn_executions ("
        "turn_id, conversation_id, user_id, attempt, created_at, updated_at"
        ") VALUES ('turn-bad-attempt', 'conversation-1', 'user-1', -1, "
        "'2026-07-31', '2026-07-31')",
        "INSERT INTO pixelflow_agent_projection_messages ("
        "message_id, conversation_id, user_id, role, content, payload_json, created_at, updated_at"
        ") VALUES ('message-bad-role', 'conversation-1', 'user-1', 'user', 'x', '{}', "
        "'2026-07-31', '2026-07-31')",
        "INSERT INTO pixelflow_agent_interrupts ("
        "interrupt_id, conversation_id, user_id, workflow_id, turn_id, thread_id, "
        "checkpoint_ns, kind, reason_code, status, payload_json, opened_at"
        ") VALUES ('interrupt-bad-status', 'conversation-1', 'user-1', 'workflow-1', "
        "'turn-1', 'thread-1', 'video', 'review', 'awaiting_review', 'unknown', '{}', "
        "'2026-07-31')",
        "INSERT INTO pixelflow_agent_interrupts ("
        "interrupt_id, conversation_id, user_id, workflow_id, turn_id, thread_id, "
        "checkpoint_ns, kind, reason_code, status, payload_json, response_id, opened_at"
        ") VALUES ('interrupt-partial-response', 'conversation-1', 'user-1', 'workflow-1', "
        "'turn-1', 'thread-1', 'video', 'review', 'awaiting_review', 'responded', '{}', "
        "'response-1', '2026-07-31')",
    )
    for statement in invalid_statements:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(statement))
    engine.dispose()
