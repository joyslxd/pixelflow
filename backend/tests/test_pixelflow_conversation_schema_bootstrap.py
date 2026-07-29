"""验证旧 SQLite 对话库在网关启动时可以幂等补齐 M13.1 字段。"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.tasks import (
    PixelFlowConversationRecord,
    SQLPixelFlowTaskStore,
    ensure_sql_conversation_schema,
)


@pytest.mark.asyncio
async def test_bootstrap_repairs_legacy_conversation_table(tmp_path: Path) -> None:
    """旧表缺少 revision 和编排字段时，新建与分页都应继续可用。"""

    database_path = tmp_path / "legacy-conversations.db"
    sync_engine = create_engine(f"sqlite:///{database_path}")
    with sync_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE pixelflow_conversations ("
                "conversation_id VARCHAR(64) PRIMARY KEY, "
                "user_id VARCHAR(64), title VARCHAR(200), "
                "current_task_id VARCHAR(64), last_phase VARCHAR(32), "
                "context_json JSON, created_at DATETIME, updated_at DATETIME"
                ")"
            )
        )
    sync_engine.dispose()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    await ensure_sql_conversation_schema(async_engine)
    async with async_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                str(column["name"])
                for column in inspect(sync_connection).get_columns("pixelflow_conversations")
            }
        )
    assert {"revision", "orchestration_mode", "orchestration_version"}.issubset(columns)

    store = SQLPixelFlowTaskStore(
        async_sessionmaker(async_engine, expire_on_commit=False)
    )
    created = await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="legacy-repaired-1",
            user_id="test-user",
            title="修复后的对话",
        )
    )
    listed, cursor = await store.list_conversations(user_id="test-user", limit=5)

    assert created.orchestration_mode == "frontend_v2"
    assert created.orchestration_version == 1
    assert created.revision == 1
    assert [item.conversation_id for item in listed] == ["legacy-repaired-1"]
    assert cursor is None
    await async_engine.dispose()
