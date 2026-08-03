"""验证 Gateway 使用的权威 Context 快照数据源。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.context import (
    ContextVersionConflictError,
    RepositoryContextSnapshotSource,
)
from pixelflow.agent_runtime.contracts import (
    AgentEvent,
    AgentEventType,
    ContextSummary,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.identity import conversation_message_id
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryVideoRuntimeRepository,
    SQLVideoRuntimeRepository,
    VideoRuntimeRepository,
    make_turn_registration_store,
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


class _CoordinatedContextRepository:
    """只包装公开端口，在旧多次读取缝隙中稳定插入真实写入。"""

    def __init__(self, delegate: VideoRuntimeRepository) -> None:
        self._delegate = delegate
        self.read_captured = asyncio.Event()
        self.write_finished = asyncio.Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def export_safe_snapshot(self, user_id: str, conversation_id: str):
        snapshot = await self._delegate.export_safe_snapshot(user_id, conversation_id)
        self.read_captured.set()
        await self.write_finished.wait()
        return snapshot

    async def list_summaries(self, user_id: str, conversation_id: str):
        return await self._delegate.list_summaries(user_id, conversation_id)

    async def read_versioned_context_snapshot(
        self,
        user_id: str,
        conversation_id: str,
        *,
        expected_context_version: int,
    ):
        snapshot = await self._delegate.read_versioned_context_snapshot(
            user_id,
            conversation_id,
            expected_context_version=expected_context_version,
        )
        self.read_captured.set()
        await self.write_finished.wait()
        return snapshot


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


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_rejects_future_expected_context_version(
    kind: RepositoryKind,
) -> None:
    """尚未登记的未来版本不能被伪造成可读取快照。"""

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
                expected_context_version=4,
            )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_rejects_partial_registration_event_coverage(
    kind: RepositoryKind,
) -> None:
    """只覆盖部分用户消息的登记事件不能参与版本裁剪。"""

    async with _runtime(kind) as (repository, task_store):
        await task_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=CONVERSATION,
                user_id=OWNER,
                orchestration_mode="supervisor_v1",
                context={
                    AGENT_RUNTIME_CONTEXT_KEY: {
                        "mode": "primary",
                        "context_version": 2,
                    }
                },
            )
        )
        messages = [
            PixelFlowConversationMessageRecord(
                message_id=f"message-partial-{index}",
                conversation_id=CONVERSATION,
                user_id=OWNER,
                role="user",
                content=f"第 {index} 条输入",
                created_at=NOW.isoformat(),
            )
            for index in (1, 2)
        ]
        for message in messages:
            await task_store.append_conversation_message(message)
        await repository.create_event(
            OWNER,
            AgentEvent(
                event_id="event-partial-1",
                sequence=1,
                cursor="cursor-partial-1",
                conversation_id=CONVERSATION,
                run_id="turn-partial-1",
                occurred_at=NOW,
                type=AgentEventType.MESSAGE_UPSERTED,
                payload={"message": messages[0].to_dict()},
            ),
        )

        with pytest.raises(
            ContextVersionConflictError,
            match="上下文登记事件不完整",
        ):
            await RepositoryContextSnapshotSource(
                task_store=task_store,
                repository=repository,
            ).load_context_snapshot(
                user_id=OWNER,
                conversation_id=CONVERSATION,
                expected_context_version=1,
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


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_reads_registered_turn_pre_input_snapshot(
    kind: RepositoryKind,
) -> None:
    """真实登记加一后，原 Turn 读取输入前历史，当前输入由请求单独携带。"""

    async with _runtime(kind) as (repository, task_store):
        await task_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=CONVERSATION,
                user_id=OWNER,
                orchestration_mode="supervisor_v1",
                context={
                    AGENT_RUNTIME_CONTEXT_KEY: {
                        "mode": "primary",
                        "context_version": 0,
                    }
                },
            )
        )
        client_input_id = UUID("40000000-0000-4000-8000-000000000011")
        message_id = conversation_message_id(CONVERSATION, client_input_id)
        registration_store = make_turn_registration_store(
            repository=repository,
            task_store=task_store,
            video_repository=repository,
        )
        registration = await registration_store.register(
            user_id=OWNER,
            conversation_id=CONVERSATION,
            message=PixelFlowConversationMessageRecord(
                message_id=message_id,
                conversation_id=CONVERSATION,
                user_id=OWNER,
                role="user",
                content="请生成商品视频",
                payload={
                    "client_message_id": str(client_input_id),
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [],
                    "explicit_action": None,
                },
                created_at=NOW.isoformat(),
            ),
            turn=TurnRecord(
                turn_id="turn-registered",
                conversation_id=CONVERSATION,
                client_input_id=client_input_id,
                status=TurnStatus.ACCEPTED,
                expected_context_version=0,
                created_at=NOW,
            ),
            expected_context_version=0,
            occurred_at=NOW,
        )
        conversation = await task_store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )
        assert conversation is not None
        assert registration.turn.expected_context_version == 0
        assert registration.context_version == 1
        assert conversation.context[AGENT_RUNTIME_CONTEXT_KEY]["context_version"] == 1

        snapshot = await RepositoryContextSnapshotSource(
            task_store=task_store,
            repository=repository,
        ).load_context_snapshot(
            user_id=OWNER,
            conversation_id=CONVERSATION,
            expected_context_version=registration.turn.expected_context_version,
        )

    assert snapshot.context_version == 0
    assert snapshot.messages == ()


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_excludes_current_registered_turn_message(
    kind: RepositoryKind,
) -> None:
    """第二个 Turn 的输入前快照只含第一条消息，不得混入当前输入。"""

    async with _runtime(kind) as (repository, task_store):
        await task_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=CONVERSATION,
                user_id=OWNER,
                orchestration_mode="supervisor_v1",
                context={
                    AGENT_RUNTIME_CONTEXT_KEY: {
                        "mode": "primary",
                        "context_version": 0,
                    }
                },
            )
        )
        registration_store = make_turn_registration_store(
            repository=repository,
            task_store=task_store,
            video_repository=repository,
        )
        first_input_id = UUID("40000000-0000-4000-8000-000000000021")
        second_input_id = UUID("40000000-0000-4000-8000-000000000022")

        async def register_turn(*, turn_id: str, client_input_id: UUID, expected: int) -> object:
            return await registration_store.register(
                user_id=OWNER,
                conversation_id=CONVERSATION,
                message=PixelFlowConversationMessageRecord(
                    message_id=conversation_message_id(CONVERSATION, client_input_id),
                    conversation_id=CONVERSATION,
                    user_id=OWNER,
                    role="user",
                    content=turn_id,
                    payload={"client_message_id": str(client_input_id)},
                    created_at=NOW.isoformat(),
                ),
                turn=TurnRecord(
                    turn_id=turn_id,
                    conversation_id=CONVERSATION,
                    client_input_id=client_input_id,
                    status=TurnStatus.ACCEPTED,
                    expected_context_version=expected,
                    created_at=NOW,
                ),
                expected_context_version=expected,
                occurred_at=NOW,
            )

        await register_turn(
            turn_id="turn-prior",
            client_input_id=first_input_id,
            expected=0,
        )
        current_registration = await register_turn(
            turn_id="turn-current",
            client_input_id=second_input_id,
            expected=1,
        )
        snapshot = await RepositoryContextSnapshotSource(
            task_store=task_store,
            repository=repository,
        ).load_context_snapshot(
            user_id=OWNER,
            conversation_id=CONVERSATION,
            expected_context_version=current_registration.turn.expected_context_version,
        )

    assert snapshot.context_version == 1
    assert [item.payload["message_id"] for item in snapshot.messages] == [
        conversation_message_id(CONVERSATION, first_input_id)
    ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_repository_source_never_returns_torn_workflow_summary_snapshot(
    kind: RepositoryKind,
) -> None:
    """并发写入只能落在一次读取前后，不得返回旧 Workflow 与新摘要。"""

    async with _runtime(kind) as (repository, task_store):
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
        coordinated = _CoordinatedContextRepository(repository)
        source = RepositoryContextSnapshotSource(
            task_store=task_store,
            repository=coordinated,
        )
        concurrent_workflow_id = "workflow-after-read"

        async def write_workflow_and_summary() -> None:
            await coordinated.read_captured.wait()
            try:
                await repository.create_workflow(
                    OWNER,
                    WorkflowRecord(
                        workflow_id=concurrent_workflow_id,
                        conversation_id=CONVERSATION,
                        kind=WorkflowKind.VIDEO,
                        status=WorkflowStatus.RUNNING,
                        current_stage="intake",
                        stage_version=1,
                        context_version=3,
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                )
                await repository.create_summary(
                    OWNER,
                    ContextSummary(
                        summary_id="summary-after-read",
                        conversation_id=CONVERSATION,
                        version=1,
                        content_hash="sha256:summary-after-read",
                        workflow_states={
                            concurrent_workflow_id: "running:intake:v1"
                        },
                        compression_model="deterministic-test-v1",
                        created_at=NOW,
                    ),
                )
            finally:
                coordinated.write_finished.set()

        snapshot, _written = await asyncio.gather(
            source.load_context_snapshot(
                user_id=OWNER,
                conversation_id=CONVERSATION,
                expected_context_version=3,
            ),
            write_workflow_and_summary(),
        )

    workflow_ids = {item.workflow_id for item in snapshot.workflows}
    summary_workflow_ids = {
        workflow_id
        for summary in snapshot.conversation_summaries
        for workflow_id in summary.workflow_states
    }
    assert summary_workflow_ids.issubset(workflow_ids)
    assert (concurrent_workflow_id in workflow_ids) == (
        concurrent_workflow_id in summary_workflow_ids
    )
