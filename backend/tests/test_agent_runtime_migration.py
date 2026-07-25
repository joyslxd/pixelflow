"""Agent Runtime 行模型与 additive migration 结构合同。"""

from pathlib import Path

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

EXPECTED_PRIMARY_KEYS = {
    "pixelflow_agent_workflows": ("workflow_id",),
    "pixelflow_agent_turns": ("inbox_sequence",),
    "pixelflow_agent_compaction_locks": ("conversation_id",),
    "pixelflow_agent_context_summaries": ("summary_id",),
    "pixelflow_agent_events": ("outbox_id",),
    "pixelflow_agent_operations": ("job_id",),
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
}


def _migration_config(database_path: Path) -> Config:
    config = Config(str(MIGRATION_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    return config


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

    from pixelflow.agent_runtime.persistence.models import AGENT_RUNTIME_TABLES
    from pixelflow.tasks.mysql import PIXELFLOW_TASK_TABLES

    runtime_tables = {table.name: table for table in AGENT_RUNTIME_TABLES}
    assert set(runtime_tables) == set(EXPECTED_TABLE_COLUMNS)
    assert {table.name for table in PIXELFLOW_TASK_TABLES}.issuperset(runtime_tables)
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        table = runtime_tables[table_name]
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
    assert set(EXPECTED_TABLE_COLUMNS).issubset(inspector.get_table_names())
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
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
    assert not remaining_tables.intersection(EXPECTED_TABLE_COLUMNS)
    assert "legacy_sentinel" in remaining_tables
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM legacy_sentinel WHERE id = 1")).scalar_one() == "keep-me"
    engine.dispose()
