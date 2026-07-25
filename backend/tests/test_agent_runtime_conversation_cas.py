"""M01.3 对话 revision、CAS 与服务端保留命名空间合同。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from deerflow.persistence.base import Base
from pixelflow.tasks import (
    ConversationRevisionConflictError,
    MemoryPixelFlowTaskStore,
    PixelFlowConversationRecord,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.mysql import _ensure_mysql_conversation_revision
from tests._router_auth_helpers import make_authed_test_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = BACKEND_ROOT / "packages" / "harness" / "deerflow" / "persistence" / "migrations"


def _stable_user() -> User:
    return User(
        email="m01-cas@example.com",
        password_hash="x",
        system_role="user",
        id=UUID("00000000-0000-0000-0000-000000000123"),
    )


def _migration_config(database_path: Path) -> Config:
    config = Config(str(MIGRATION_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    return config


def _jianying_pending(conversation_id: str) -> dict[str, str]:
    return {
        "job_id": "job-1",
        "conversation_id": conversation_id,
        "storyboard_version_id": "storyboard-1",
    }


def _jianying_records() -> dict[str, dict[str, str]]:
    return {
        "storyboard-1": {
            "status": "running",
            "job_id": "job-1",
            "storyboard_version_id": "storyboard-1",
        }
    }


@pytest.mark.asyncio
async def test_memory_conversation_cas_protects_server_namespace_and_jianying_state():
    store = MemoryPixelFlowTaskStore()
    created = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-memory-cas",
            user_id="user-1",
            context={"client_state": "v1"},
        )
    )
    assert created.revision == 1

    runtime_updated = await store.patch_agent_runtime_conversation_context(
        created.conversation_id,
        user_id="user-1",
        expected_revision=1,
        runtime_patch={"context_version": 7, "compression": {"status": "idle"}},
    )
    assert runtime_updated is not None
    assert runtime_updated.revision == 2
    assert runtime_updated.context["__agent_runtime"] == {
        "context_version": 7,
        "compression": {"status": "idle"},
    }

    with pytest.raises(ConversationRevisionConflictError) as exc_info:
        await store.patch_agent_runtime_conversation_context(
            created.conversation_id,
            user_id="user-1",
            expected_revision=1,
            runtime_patch={"context_version": 8},
        )
    assert exc_info.value.expected_revision == 1
    assert exc_info.value.current_revision == 2

    jianying_updated = await store.patch_jianying_draft_conversation_context(
        created.conversation_id,
        user_id="user-1",
        expected_job_id="job-1",
        pending_job=_jianying_pending(created.conversation_id),
        records=_jianying_records(),
        last_phase="jianying_draft_running",
    )
    assert jianying_updated is not None
    assert jianying_updated.revision == 3

    frontend_updated = await store.update_conversation(
        created.conversation_id,
        user_id="user-1",
        expected_revision=3,
        context={
            "client_state": "v2",
            "__agent_runtime": {"context_version": -1},
            "pendingJianyingDraftJob": None,
            "pending_jianying_draft_job": None,
            "jianyingDraftRecords": {},
            "jianying_draft_records": {},
        },
    )
    assert frontend_updated is not None
    assert frontend_updated.revision == 4
    assert frontend_updated.context["client_state"] == "v2"
    assert frontend_updated.context["__agent_runtime"]["context_version"] == 7
    assert frontend_updated.context["pendingJianyingDraftJob"]["job_id"] == "job-1"
    assert frontend_updated.context["jianyingDraftRecords"]["storyboard-1"]["status"] == "running"

    assert (
        await store.patch_agent_runtime_conversation_context(
            created.conversation_id,
            user_id="other-user",
            expected_revision=4,
            runtime_patch={"forbidden": True},
        )
        is None
    )


@pytest.mark.asyncio
async def test_memory_conversation_snapshots_cannot_bypass_cas_by_mutation():
    store = MemoryPixelFlowTaskStore()
    source = PixelFlowConversationRecord(
        conversation_id="conversation-memory-snapshot",
        user_id="user-1",
        context={"client_state": {"value": "stored"}},
    )
    created = await store.create_conversation(source)

    source.revision = 99
    source.context["client_state"]["value"] = "source-mutated"
    created.revision = 88
    created.context["client_state"]["value"] = "return-mutated"

    restored = await store.get_conversation(source.conversation_id, user_id="user-1")
    assert restored is not None
    assert restored.revision == 1
    assert restored.context == {"client_state": {"value": "stored"}}

    restored.revision = 77
    restored.context["client_state"]["value"] = "get-mutated"
    listed, _ = await store.list_conversations(user_id="user-1")
    assert listed[0].revision == 1
    assert listed[0].context == {"client_state": {"value": "stored"}}

    listed[0].revision = 66
    listed[0].context["client_state"]["value"] = "list-mutated"
    unchanged = await store.update_conversation(
        source.conversation_id,
        user_id="user-1",
        expected_revision=1,
        context={"client_state": {"value": "stored"}},
    )
    assert unchanged is not None
    assert unchanged.revision == 1

    runtime_updated = await store.patch_agent_runtime_conversation_context(
        source.conversation_id,
        user_id="user-1",
        expected_revision=1,
        runtime_patch={"context_version": 1},
    )
    assert runtime_updated is not None
    assert runtime_updated.revision == 2
    runtime_updated.context["__agent_runtime"]["context_version"] = -1

    final = await store.get_conversation(source.conversation_id, user_id="user-1")
    assert final is not None
    assert final.revision == 2
    assert final.context["__agent_runtime"] == {"context_version": 1}

    replacement = {
        "client_state": {
            "value": "updated",
            "nested": ["keep"],
        }
    }
    ordinary_updated = await store.update_conversation(
        source.conversation_id,
        user_id="user-1",
        expected_revision=2,
        context=replacement,
    )
    assert ordinary_updated is not None
    assert ordinary_updated.revision == 3
    replacement["client_state"]["value"] = "input-mutated"
    replacement["client_state"]["nested"].append("forbidden")

    pending = _jianying_pending(source.conversation_id)
    pending["request"] = {"nested": ["keep"]}
    records = _jianying_records()
    records["storyboard-1"]["result"] = {"nested": ["keep"]}
    jianying_updated = await store.patch_jianying_draft_conversation_context(
        source.conversation_id,
        user_id="user-1",
        expected_job_id="job-1",
        pending_job=pending,
        records=records,
        last_phase="jianying_draft_running",
    )
    assert jianying_updated is not None
    assert jianying_updated.revision == 4
    pending["request"]["nested"].append("forbidden")
    records["storyboard-1"]["result"]["nested"].append("forbidden")

    isolated = await store.get_conversation(source.conversation_id, user_id="user-1")
    assert isolated is not None
    assert isolated.revision == 4
    assert isolated.context["client_state"] == {
        "value": "updated",
        "nested": ["keep"],
    }
    assert isolated.context["pendingJianyingDraftJob"]["request"] == {
        "nested": ["keep"],
    }
    assert isolated.context["jianyingDraftRecords"]["storyboard-1"]["result"] == {
        "nested": ["keep"],
    }


@pytest.mark.asyncio
async def test_two_sql_stores_allow_only_one_writer_for_same_expected_revision(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conversation-cas.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        first_store = SQLPixelFlowTaskStore(session_factory)
        second_store = SQLPixelFlowTaskStore(session_factory)
        created = await first_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="conversation-sql-cas",
                user_id="user-1",
            )
        )
        assert created.revision == 1
        assert (
            await second_store.patch_agent_runtime_conversation_context(
                created.conversation_id,
                user_id="other-user",
                expected_revision=999,
                runtime_patch={"forbidden": True},
            )
            is None
        )

        results = await asyncio.gather(
            first_store.patch_agent_runtime_conversation_context(
                created.conversation_id,
                user_id="user-1",
                expected_revision=1,
                runtime_patch={"writer": "first"},
            ),
            second_store.patch_agent_runtime_conversation_context(
                created.conversation_id,
                user_id="user-1",
                expected_revision=1,
                runtime_patch={"writer": "second"},
            ),
            return_exceptions=True,
        )

        successful = [item for item in results if isinstance(item, PixelFlowConversationRecord)]
        conflicts = [item for item in results if isinstance(item, ConversationRevisionConflictError)]
        assert len(successful) == 1
        assert len(conflicts) == 1
        assert successful[0].revision == 2
        assert conflicts[0].expected_revision == 1
        assert conflicts[0].current_revision == 2

        restored = await first_store.get_conversation(created.conversation_id, user_id="user-1")
        assert restored is not None
        assert restored.revision == 2
        assert restored.context["__agent_runtime"]["writer"] in {"first", "second"}

        unchanged = await first_store.update_conversation(
            created.conversation_id,
            user_id="user-1",
            expected_revision=2,
            context=restored.context,
        )
        assert unchanged is not None
        assert unchanged.revision == 2

        same_runtime = await first_store.patch_agent_runtime_conversation_context(
            created.conversation_id,
            user_id="user-1",
            expected_revision=2,
            runtime_patch={
                "writer": restored.context["__agent_runtime"]["writer"],
            },
        )
        assert same_runtime is not None
        assert same_runtime.revision == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_full_context_save_does_not_overwrite_runtime_or_concurrent_jianying_patch(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conversation-reserved-context.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime_store = SQLPixelFlowTaskStore(session_factory)
        frontend_store = SQLPixelFlowTaskStore(session_factory)
        created = await runtime_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id="conversation-sql-reserved",
                user_id="user-1",
                context={"client_state": "old"},
            )
        )
        await runtime_store.patch_agent_runtime_conversation_context(
            created.conversation_id,
            user_id="user-1",
            expected_revision=1,
            runtime_patch={"context_version": 3},
        )

        await asyncio.gather(
            frontend_store.update_conversation(
                created.conversation_id,
                user_id="user-1",
                context={
                    "client_state": "new",
                    "__agent_runtime": {"context_version": -1},
                    "pendingJianyingDraftJob": None,
                    "jianyingDraftRecords": {},
                },
            ),
            runtime_store.patch_jianying_draft_conversation_context(
                created.conversation_id,
                user_id="user-1",
                expected_job_id="job-1",
                pending_job=_jianying_pending(created.conversation_id),
                records=_jianying_records(),
                last_phase="jianying_draft_running",
            ),
        )

        restored = await runtime_store.get_conversation(created.conversation_id, user_id="user-1")
        assert restored is not None
        assert restored.revision == 4
        assert restored.context["client_state"] == "new"
        assert restored.context["__agent_runtime"] == {"context_version": 3}
        assert restored.context["pendingJianyingDraftJob"]["job_id"] == "job-1"
        assert restored.context["jianyingDraftRecords"]["storyboard-1"]["status"] == "running"
    finally:
        await engine.dispose()


def test_conversation_revision_migration_backfills_existing_rows_and_is_reversible(tmp_path):
    database_path = tmp_path / "conversation-revision-migration.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE pixelflow_conversations ("
                "conversation_id VARCHAR(64) PRIMARY KEY"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO pixelflow_conversations (conversation_id) "
                "VALUES ('legacy-conversation')"
            )
        )
    engine.dispose()

    config = _migration_config(database_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("pixelflow_conversations")} == {
        "conversation_id",
        "orchestration_mode",
        "orchestration_version",
        "revision",
    }
    assert "ix_pf_conversation_revision_m01_3" in {
        item["name"]
        for item in inspector.get_indexes("pixelflow_conversations")
    }
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT revision, orchestration_mode, orchestration_version "
                "FROM pixelflow_conversations "
                "WHERE conversation_id = 'legacy-conversation'"
            )
        ).one() == (1, "frontend_v2", 1)
    engine.dispose()

    command.downgrade(config, "20260724_01")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("pixelflow_conversations")} == {
        "conversation_id",
    }
    engine.dispose()


def test_conversation_revision_migration_does_not_claim_preexisting_column(tmp_path):
    database_path = tmp_path / "conversation-preexisting-revision.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE pixelflow_conversations ("
                "conversation_id VARCHAR(64) PRIMARY KEY, "
                "revision INTEGER NOT NULL DEFAULT 1"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO pixelflow_conversations "
                "(conversation_id, revision) VALUES ('preexisting', 9)"
            )
        )
    engine.dispose()

    config = _migration_config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, "20260724_01")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("pixelflow_conversations")} == {
        "conversation_id",
        "revision",
    }
    assert "ix_pf_conversation_revision_m01_3" not in {
        item["name"]
        for item in inspector.get_indexes("pixelflow_conversations")
    }
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT revision FROM pixelflow_conversations "
                "WHERE conversation_id = 'preexisting'"
            )
        ).scalar_one() == 9
    engine.dispose()


def test_conversation_revision_migration_refuses_unsafe_offline_sql(tmp_path):
    config = _migration_config(tmp_path / "offline-conversation-revision.db")
    with pytest.raises(
        RuntimeError,
        match="conversation revision migration requires online schema inspection",
    ):
        command.upgrade(config, "head", sql=True)


def test_conversation_router_exposes_revision_and_maps_stale_update_to_conflict():
    from app.gateway.routers import pixelflow_conversations

    store = MemoryPixelFlowTaskStore()
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = store
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        created_response = client.post(
            "/agent/conversations",
            json={
                "title": "CAS 对话",
                "context": {
                    "client_state": "v1",
                    "__agent_runtime": {"forbidden": True},
                },
            },
        )
        assert created_response.status_code == 200
        created = created_response.json()
        assert created["revision"] == 1
        assert "__agent_runtime" not in created["context"]

        asyncio.run(
            store.patch_agent_runtime_conversation_context(
                created["conversation_id"],
                user_id=str(_stable_user().id),
                expected_revision=1,
                runtime_patch={"context_version": 1},
            )
        )

        updated_response = client.put(
            f"/agent/conversations/{created['conversation_id']}",
            json={
                "expected_revision": 2,
                "context": {
                    "client_state": "v2",
                    "__agent_runtime": {"context_version": -1},
                },
            },
        )
        assert updated_response.status_code == 200
        updated = updated_response.json()
        assert updated["revision"] == 3
        assert updated["context"]["client_state"] == "v2"
        assert updated["context"]["__agent_runtime"] == {"context_version": 1}

        conflict_response = client.put(
            f"/agent/conversations/{created['conversation_id']}",
            json={
                "expected_revision": 2,
                "context": {"client_state": "stale"},
            },
        )
        assert conflict_response.status_code == 409
        assert conflict_response.json()["detail"] == {
            "code": "conversation_revision_conflict",
            "expected_revision": 2,
            "current_revision": 3,
        }

        restored = client.get(
            f"/agent/conversations/{created['conversation_id']}"
        ).json()["conversation"]
        assert restored["revision"] == 3
        assert restored["context"]["client_state"] == "v2"
        assert restored["context"]["__agent_runtime"] == {"context_version": 1}

        asyncio.run(
            store.create_conversation(
                PixelFlowConversationRecord(
                    conversation_id="foreign-conversation",
                    user_id="other-user",
                    revision=7,
                )
            )
        )
        foreign_response = client.put(
            "/agent/conversations/foreign-conversation",
            json={
                "expected_revision": 1,
                "context": {"forbidden": True},
            },
        )
        assert foreign_response.status_code == 404


class _MySQLRevisionResult:
    def __init__(self, row: tuple[str] | None):
        self._row = row

    def first(self) -> tuple[str] | None:
        return self._row


class _MySQLRevisionConnection:
    class _Dialect:
        name = "mysql"

    dialect = _Dialect()

    def __init__(self, *, revision_exists: bool):
        self.revision_exists = revision_exists
        self.statements: list[str] = []

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            row = ("revision",) if self.revision_exists else None
            return _MySQLRevisionResult(row)
        return _MySQLRevisionResult(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("revision_exists", [False, True])
async def test_mysql_conversation_revision_bootstrap_adds_only_missing_column(
    revision_exists: bool,
):
    connection = _MySQLRevisionConnection(revision_exists=revision_exists)

    await _ensure_mysql_conversation_revision(connection)

    alter_statements = [
        statement
        for statement in connection.statements
        if "ALTER TABLE pixelflow_conversations" in statement
    ]
    assert len(alter_statements) == (0 if revision_exists else 1)
    if alter_statements:
        assert "revision INTEGER NOT NULL DEFAULT 1" in alter_statements[0]
