"""验证 Gateway 使用的权威 Context 快照数据源。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.context import (
    ContextVersionConflictError,
    RepositoryContextSnapshotSource,
)
from pixelflow.agent_runtime.contracts import (
    ContextSummary,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryVideoRuntimeRepository,
    SQLVideoRuntimeRepository,
    VideoRuntimeRepository,
)
from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
    PixelFlowTaskStore,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
OWNER = "user-1"
CONVERSATION = "conversation-1"
WORKFLOW = "workflow-video-1"


@asynccontextmanager
async def _runtime(
    kind: RepositoryKind,
) -> AsyncIterator[tuple[VideoRuntimeRepository, PixelFlowTaskStore]]:
    if kind == "memory":
        task_store = MemoryPixelFlowTaskStore()
        yield MemoryVideoRuntimeRepository(task_store=task_store), task_store
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=(
                    AGENT_RUNTIME_TABLES
                    + AGENT_RUNTIME_SUPPORT_TABLES
                    + (
                        PixelFlowConversationRow.__table__,
                        PixelFlowConversationMessageRow.__table__,
                    )
                ),
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    task_store = SQLPixelFlowTaskStore(session_factory)
    try:
        yield (
            SQLVideoRuntimeRepository(
                session_factory,
                task_store=task_store,
            ),
            task_store,
        )
    finally:
        await engine.dispose()


async def _seed_authoritative_context(
    repository: VideoRuntimeRepository,
    task_store: PixelFlowTaskStore,
) -> None:
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=CONVERSATION,
            user_id=OWNER,
            orchestration_mode="supervisor_v1",
            context={
                AGENT_RUNTIME_CONTEXT_KEY: {
                    "mode": "primary",
                    "context_version": 3,
                }
            },
        )
    )
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="message-user-1",
            conversation_id=CONVERSATION,
            user_id=OWNER,
            role="user",
            content="请生成商品视频",
            payload={"artifact_refs": ["artifact:message"]},
            created_at=NOW.isoformat(),
        )
    )
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="message-legacy-assistant",
            conversation_id=CONVERSATION,
            user_id=OWNER,
            role="assistant",
            content="旧流程消息不属于 live 权威投影",
            created_at=NOW.isoformat(),
        )
    )
    await repository.create_workflow(
        OWNER,
        WorkflowRecord(
            workflow_id=WORKFLOW,
            conversation_id=CONVERSATION,
            kind=WorkflowKind.VIDEO,
            status=WorkflowStatus.RUNNING,
            current_stage="intake",
            stage_version=1,
            latest_artifact_refs=["artifact:workflow"],
            context_version=3,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    await repository.create_summary(
        OWNER,
        ContextSummary(
            summary_id="summary-1",
            conversation_id=CONVERSATION,
            version=1,
            content_hash="sha256:summary-1",
            workflow_states={WORKFLOW: "running:intake:v1"},
            artifact_evidence_refs=["artifact:summary"],
            compression_model="deterministic-test-v1",
            created_at=NOW,
        ),
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_builds_same_authoritative_snapshot(
    kind: RepositoryKind,
) -> None:
    async with _runtime(kind) as (repository, task_store):
        await _seed_authoritative_context(repository, task_store)
        source = RepositoryContextSnapshotSource(
            task_store=task_store,
            repository=repository,
        )

        snapshot = await source.load_context_snapshot(
            user_id=OWNER,
            conversation_id=CONVERSATION,
            expected_context_version=3,
        )

    assert snapshot.user_id == OWNER
    assert snapshot.context_version == 3
    assert [item.workflow_id for item in snapshot.workflows] == [WORKFLOW]
    assert [item.payload["message_id"] for item in snapshot.messages] == [
        "message-user-1"
    ]
    assert [item.summary_id for item in snapshot.conversation_summaries] == [
        "summary-1"
    ]
    assert [item.workflow_id for item in snapshot.workflow_summaries] == [WORKFLOW]
    assert [item.artifact_ref for item in snapshot.artifact_evidence] == [
        "artifact:message",
        "artifact:workflow",
        "artifact:summary",
    ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_rejects_stale_expected_context_version(
    kind: RepositoryKind,
) -> None:
    async with _runtime(kind) as (repository, task_store):
        await _seed_authoritative_context(repository, task_store)
        source = RepositoryContextSnapshotSource(
            task_store=task_store,
            repository=repository,
        )

        with pytest.raises(ContextVersionConflictError):
            await source.load_context_snapshot(
                user_id=OWNER,
                conversation_id=CONVERSATION,
                expected_context_version=2,
            )


@pytest.mark.asyncio
async def test_repository_source_rejects_cross_owner_reads() -> None:
    async with _runtime("memory") as (repository, task_store):
        await _seed_authoritative_context(repository, task_store)
        source = RepositoryContextSnapshotSource(
            task_store=task_store,
            repository=repository,
        )

        with pytest.raises(LookupError, match="对话不存在或不属于当前用户"):
            await source.load_context_snapshot(
                user_id="other-user",
                conversation_id=CONVERSATION,
                expected_context_version=3,
            )
