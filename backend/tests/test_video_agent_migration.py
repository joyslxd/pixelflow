from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = BACKEND_ROOT / "packages" / "harness" / "deerflow" / "persistence" / "migrations"


def migration_config(database_path: Path) -> Config:
    config = Config(str(MIGRATION_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    return config


def sync_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.as_posix()}"


def test_video_agent_migration_is_additive_and_preserves_existing_workflow(tmp_path: Path) -> None:
    database_path = tmp_path / "video-agent.db"
    config = migration_config(database_path)
    command.upgrade(config, "20260802_07")

    engine = create_engine(sync_url(database_path))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pixelflow_agent_workflows ("
                    "workflow_id, conversation_id, user_id, kind, status, current_stage, "
                    "stage_version, creation_contract_snapshot_json, pending_external_job_json, "
                    "latest_artifact_refs_json, context_version, created_at, updated_at"
                    ") VALUES ("
                    "'workflow-v1', 'conversation-v1', 'user-v1', 'video', 'running', 'planning', "
                    "1, '{}', NULL, '[]', 1, '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z'"
                    ")"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(sync_url(database_path))
    try:
        inspector = inspect(engine)
        assert {
            "pixelflow_video_agent_workspaces",
            "pixelflow_video_agent_plans",
            "pixelflow_video_agent_plan_steps",
        }.issubset(inspector.get_table_names())
        step_columns = {
            column["name"]
            for column in inspector.get_columns("pixelflow_video_agent_plan_steps")
        }
        assert {"arguments_json", "confirmation_required"}.issubset(step_columns)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT current_stage FROM pixelflow_agent_workflows WHERE workflow_id = 'workflow-v1'")
            ).scalar_one() == "planning"
    finally:
        engine.dispose()
