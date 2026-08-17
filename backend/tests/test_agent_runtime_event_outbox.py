from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import (
    AgentEvent,
    AgentEventType,
    ExternalJobStatus,
)
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    OperationRecord,
    SQLAgentRuntimeRepository,
)

RepositoryKind = Literal["memory", "sql"]
OWNER_A = "user-a"
OWNER_B = "user-b"
CONVERSATION_ID = "conversation-a"
NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def _event(
    event_id: str,
    sequence: int,
    *,
    cursor: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor or f"cursor-{sequence}",
        conversation_id=CONVERSATION_ID,
        run_id="run-1",
        occurred_at=NOW + timedelta(seconds=sequence),
        type=AgentEventType.WORKFLOW_PROGRESSED,
        payload={"sequence": sequence},
    )


@asynccontextmanager
async def _repository(
    kind: RepositoryKind,
) -> AsyncIterator[AgentRuntimeRepository]:
    if kind == "memory":
        yield MemoryAgentRuntimeRepository()
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    try:
        yield SQLAgentRuntimeRepository(
            async_sessionmaker(engine, expire_on_commit=False)
        )
    finally:
        await engine.dispose()


@asynccontextmanager
async def _file_sql_repositories(
    database_path: Path,
) -> AsyncIterator[
    tuple[SQLAgentRuntimeRepository, SQLAgentRuntimeRepository]
]:
    engine_a = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    engine_b = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine_a.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    try:
        yield (
            SQLAgentRuntimeRepository(
                async_sessionmaker(engine_a, expire_on_commit=False)
            ),
            SQLAgentRuntimeRepository(
                async_sessionmaker(engine_b, expire_on_commit=False)
            ),
        )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_event_sequence_and_cursor_query_are_contiguous_and_owner_scoped(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first = _event("event-1", 1)
        second = _event("event-2", 2)
        await repository.create_event(OWNER_A, first)

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.create_event(
                OWNER_A,
                _event("event-gap", 3),
            )

        await repository.create_event(OWNER_A, second)

        assert await repository.list_events_after_cursor(
            OWNER_A,
            CONVERSATION_ID,
            cursor=None,
            limit=1,
        ) == [first]
        assert await repository.list_events_after_cursor(
            OWNER_A,
            CONVERSATION_ID,
            cursor=first.cursor,
        ) == [second]
        assert await repository.list_events_after_cursor(
            OWNER_A,
            CONVERSATION_ID,
            cursor=second.cursor,
        ) == []
        assert (
            await repository.list_events_after_cursor(
                OWNER_A,
                CONVERSATION_ID,
                cursor="unknown-cursor",
            )
            is None
        )
        assert (
            await repository.list_events_after_cursor(
                OWNER_B,
                CONVERSATION_ID,
                cursor=first.cursor,
            )
            is None
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_create_event_heals_stale_sequence_from_concurrent_writers(
    kind: RepositoryKind,
) -> None:
    """锁外预读的 sequence 过期时，create_event 锁内自愈，避免 tool/确认事件全丢。"""

    async with _repository(kind) as repository:
        await repository.create_event(OWNER_A, _event("event-1", 1))
        await repository.create_event(OWNER_A, _event("event-2", 2))
        healed = await repository.create_event(
            OWNER_A,
            _event("event-stale", 2, cursor="cursor-stale"),
        )
        assert healed.sequence == 3
        events = await repository.list_events(OWNER_A, CONVERSATION_ID)
        assert [item.sequence for item in events] == [1, 2, 3]
        assert events[-1].event_id == "event-stale"


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_concurrent_create_event_does_not_drop_on_sequence_race(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        await repository.create_event(OWNER_A, _event("event-seed", 1))

        async def _append(index: int) -> AgentEvent:
            # 故意全部声称 sequence=2，模拟并发 TOCTOU。
            return await repository.create_event(
                OWNER_A,
                _event(f"event-race-{index}", 2, cursor=f"cursor-race-{index}"),
            )

        results = await asyncio.gather(*[_append(index) for index in range(5)])
        sequences = sorted(item.sequence for item in results)
        assert sequences == [2, 3, 4, 5, 6]
        listed = await repository.list_events(OWNER_A, CONVERSATION_ID)
        assert [item.sequence for item in listed] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_event_cursor_query_rejects_invalid_limit(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        for limit in (0, 1001):
            with pytest.raises(ValueError):
                await repository.list_events_after_cursor(
                    OWNER_A,
                    CONVERSATION_ID,
                    cursor=None,
                    limit=limit,
                )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_event_claim_blocks_duplicate_and_reclaims_expired_lease(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first = _event("event-1", 1)
        second = _event("event-2", 2)
        await repository.create_event(OWNER_A, first)
        await repository.create_event(OWNER_A, second)

        first_claim = await repository.claim_next_event(
            OWNER_A,
            CONVERSATION_ID,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        duplicate_claim = await repository.claim_next_event(
            OWNER_A,
            CONVERSATION_ID,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=31),
        )
        reclaimed = await repository.claim_next_event(
            OWNER_A,
            CONVERSATION_ID,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=31),
            lease_expires_at=NOW + timedelta(seconds=61),
        )

        assert first_claim is not None
        assert first_claim.event == first
        assert first_claim.delivery_attempts == 1
        assert duplicate_claim is None
        assert reclaimed is not None
        assert reclaimed.event == first
        assert reclaimed.delivery_attempts == 2

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.complete_event_delivery(
                OWNER_A,
                first.event_id,
                lease_owner="worker-a",
                published_at=NOW + timedelta(seconds=32),
            )

        published = await repository.complete_event_delivery(
            OWNER_A,
            first.event_id,
            lease_owner="worker-b",
            published_at=NOW + timedelta(seconds=32),
        )
        replayed_completion = await repository.complete_event_delivery(
            OWNER_A,
            first.event_id,
            lease_owner="worker-b",
            published_at=NOW + timedelta(seconds=33),
        )
        next_claim = await repository.claim_next_event(
            OWNER_A,
            CONVERSATION_ID,
            lease_owner="worker-c",
            now=NOW + timedelta(seconds=33),
            lease_expires_at=NOW + timedelta(seconds=63),
        )

        assert published == first
        assert replayed_completion == first
        assert next_claim is not None
        assert next_claim.event == second
        assert next_claim.delivery_attempts == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_operation_internal_quota_event_blocks_generic_outbox_head(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        quota_event = AgentEvent(
            event_id="evt_job_quota_job-1_1_paused",
            sequence=1,
            cursor="cursor-job-quota-job-1-1-paused",
            conversation_id=CONVERSATION_ID,
            run_id="job-1",
            occurred_at=NOW,
            type=AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED,
            payload={
                "job_id": "job-1",
                "quota_pause_revision": 1,
                "quota_state": "paused",
            },
        )
        await repository.create_event(OWNER_A, quota_event)
        await repository.create_event(OWNER_A, _event("event-after-quota", 2))

        assert await repository.claim_next_event(
            OWNER_A,
            CONVERSATION_ID,
            lease_owner="generic-worker",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        ) is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_quota_prefix_matching_is_literal_across_due_and_outbox_channels(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        operation = OperationRecord(
            job_id="job-quota-literal-prefix",
            provider_job_id="provider-quota-literal-prefix",
            workflow_id="workflow-quota-literal-prefix",
            conversation_id=CONVERSATION_ID,
            stage="scene_generation",
            stage_version=1,
            status=ExternalJobStatus.POLLING,
            attempt=1,
            request_hash="sha256:" + "1" * 64,
            idempotency_key="operation-quota-literal-prefix",
            quota_pause_revision=1,
            next_poll_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        fake_prefix_event = AgentEvent(
            event_id="evtXjobXquotaXjob-1_1_paused",
            sequence=1,
            cursor="cursor-fake-quota-prefix",
            conversation_id=CONVERSATION_ID,
            run_id=operation.job_id,
            occurred_at=NOW,
            type=AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED,
            payload={
                "job_id": operation.job_id,
                "quota_pause_revision": 1,
                "quota_state": "paused",
            },
        )
        await repository.create_operation(OWNER_A, operation)
        await repository.create_event(OWNER_A, fake_prefix_event)

        due = await repository.list_due_operations(now=NOW, limit=100)
        assert [item.operation.job_id for item in due] == [operation.job_id]
        assert await repository.list_pending_operation_quota_events(
            now=NOW,
            limit=100,
        ) == []
        generic_claim = await repository.claim_next_event(
            OWNER_A,
            CONVERSATION_ID,
            lease_owner="generic-fake-prefix",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        assert generic_claim is not None
        assert generic_claim.event == fake_prefix_event


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_event_claim_and_completion_hide_other_owner(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        event = _event("event-1", 1)
        await repository.create_event(OWNER_A, event)

        assert (
            await repository.claim_next_event(
                OWNER_B,
                CONVERSATION_ID,
                lease_owner="worker-b",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            )
            is None
        )
        assert (
            await repository.complete_event_delivery(
                OWNER_B,
                event.event_id,
                lease_owner="worker-b",
                published_at=NOW,
            )
            is None
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_concurrent_event_append_heals_stale_sequence_contiguously(
    kind: RepositoryKind,
) -> None:
    """两路并发都声称 sequence=2 时，锁内自愈为 2、3，不得整条丢掉。"""

    async with _repository(kind) as repository:
        first = _event("event-1", 1)
        await repository.create_event(OWNER_A, first)

        results = await asyncio.gather(
            repository.create_event(
                OWNER_A,
                _event("event-2-a", 2, cursor="cursor-2-a"),
            ),
            repository.create_event(
                OWNER_A,
                _event("event-2-b", 2, cursor="cursor-2-b"),
            ),
            return_exceptions=True,
        )

        created = [
            result
            for result in results
            if isinstance(result, AgentEvent)
        ]
        conflicts = [
            result
            for result in results
            if isinstance(result, AgentRuntimeRecordConflictError)
        ]
        assert len(created) == 2
        assert conflicts == []
        assert sorted(item.sequence for item in created) == [2, 3]
        listed = await repository.list_events(OWNER_A, CONVERSATION_ID)
        assert [item.sequence for item in listed] == [1, 2, 3]
        assert listed[0] == first


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_concurrent_event_claim_returns_one_delivery(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        event = _event("event-1", 1)
        await repository.create_event(OWNER_A, event)

        claims = await asyncio.gather(
            repository.claim_next_event(
                OWNER_A,
                CONVERSATION_ID,
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            ),
            repository.claim_next_event(
                OWNER_A,
                CONVERSATION_ID,
                lease_owner="worker-b",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            ),
        )

        assert [claim.event for claim in claims if claim is not None] == [event]


@pytest.mark.asyncio
async def test_sql_cross_engine_claim_returns_one_delivery(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(
        tmp_path / "event-claim.db"
    ) as (repository_a, repository_b):
        event = _event("event-1", 1)
        await repository_a.create_event(OWNER_A, event)

        claims = await asyncio.gather(
            repository_a.claim_next_event(
                OWNER_A,
                CONVERSATION_ID,
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            ),
            repository_b.claim_next_event(
                OWNER_A,
                CONVERSATION_ID,
                lease_owner="worker-b",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            ),
        )

        assert [claim.event for claim in claims if claim is not None] == [event]
