"""M06.2 External Job Operation 租约与过期接管合同。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import ExternalJobStatus
from pixelflow.agent_runtime.jobs import (
    OperationLeaseCoordinator,
    build_operation_idempotency_key,
)
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    OperationRecord,
    SQLAgentRuntimeRepository,
)

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
OWNER = "user-operation-lease"
CONVERSATION = "conversation-operation-lease"


async def _create_sql_engine(database_path: Path) -> AsyncEngine:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    return engine


@asynccontextmanager
async def _repository(
    kind: RepositoryKind,
    database_path: Path,
) -> AsyncIterator[AgentRuntimeRepository]:
    if kind == "memory":
        yield MemoryAgentRuntimeRepository()
        return

    engine = await _create_sql_engine(database_path)
    try:
        yield SQLAgentRuntimeRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def _operation(
    job_id: str = "operation-lease",
    *,
    status: ExternalJobStatus = ExternalJobStatus.POLLING,
    provider_job_id: str | None = "provider-job-1",
    next_poll_at: datetime | None = NOW,
) -> OperationRecord:
    return OperationRecord(
        job_id=job_id,
        provider_job_id=provider_job_id,
        workflow_id=f"workflow-{job_id}",
        conversation_id=CONVERSATION,
        stage="image_generate",
        stage_version=1,
        status=status,
        attempt=1,
        request_hash=f"sha256:{'1' * 64}",
        idempotency_key=build_operation_idempotency_key(
            f"workflow-{job_id}",
            "image_generate",
            1,
            1,
        ),
        next_poll_at=next_poll_at,
        lease_owner=None,
        lease_expires_at=None,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )


def _coordinator(
    repository: AgentRuntimeRepository,
    *,
    user_id: str = OWNER,
    conversation_id: str = CONVERSATION,
) -> OperationLeaseCoordinator:
    return OperationLeaseCoordinator(
        repository,
        user_id=user_id,
        conversation_id=conversation_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_due_polling_operation_can_be_claimed_and_replayed_by_same_worker(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(kind, tmp_path / f"{kind}-due.db") as repository:
        await repository.create_operation(OWNER, _operation())
        coordinator = _coordinator(repository)

        claimed = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        replayed = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=60),
        )
        competing = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=60),
        )

        assert claimed is not None
        assert claimed.lease_owner == "worker-a"
        assert claimed.lease_expires_at == NOW + timedelta(seconds=30)
        assert claimed.next_poll_at == NOW
        assert claimed.updated_at == NOW
        assert replayed == claimed
        assert competing is None
        assert await repository.get_operation(OWNER, "operation-lease") == claimed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("status", "provider_job_id", "next_poll_at"),
    [
        (ExternalJobStatus.CREATED, None, NOW),
        (ExternalJobStatus.SUCCEEDED, "provider-job-1", NOW),
        (ExternalJobStatus.FAILED, "provider-job-1", NOW),
        (ExternalJobStatus.TIMEOUT, "provider-job-1", NOW),
        (ExternalJobStatus.EXPIRED, "provider-job-1", NOW),
        (ExternalJobStatus.POLLING, None, NOW),
        (ExternalJobStatus.POLLING, "provider-job-1", None),
        (
            ExternalJobStatus.POLLING,
            "provider-job-1",
            NOW + timedelta(seconds=1),
        ),
    ],
)
async def test_operation_claim_requires_due_polling_provider_job(
    kind: RepositoryKind,
    status: ExternalJobStatus,
    provider_job_id: str | None,
    next_poll_at: datetime | None,
    tmp_path: Path,
) -> None:
    case_name = f"{status.value}-provider-{provider_job_id is not None}-poll-{next_poll_at is not None}-future-{next_poll_at is not None and next_poll_at > NOW}"
    async with _repository(
        kind,
        tmp_path / f"{kind}-{case_name}.db",
    ) as repository:
        await repository.create_operation(
            OWNER,
            _operation(
                status=status,
                provider_job_id=provider_job_id,
                next_poll_at=next_poll_at,
            ),
        )

        claimed = await _coordinator(repository).claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )

        assert claimed is None
        stored = await repository.get_operation(OWNER, "operation-lease")
        assert stored is not None
        assert stored.lease_owner is None
        assert stored.lease_expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_operation_claim_isolated_by_owner_and_conversation(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-scope.db",
    ) as repository:
        await repository.create_operation(OWNER, _operation())

        assert (
            await _coordinator(repository, user_id="user-other").claim(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            )
            is None
        )
        assert (
            await _coordinator(
                repository,
                conversation_id="conversation-other",
            ).claim(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            )
            is None
        )
        stored = await repository.get_operation(OWNER, "operation-lease")
        assert stored is not None
        assert stored.lease_owner is None


@pytest.mark.asyncio
async def test_memory_workers_compete_for_one_operation_lease() -> None:
    repository = MemoryAgentRuntimeRepository()
    await repository.create_operation(OWNER, _operation())

    results = await asyncio.gather(
        _coordinator(repository).claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        ),
        _coordinator(repository).claim(
            "operation-lease",
            lease_owner="worker-b",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        ),
    )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].lease_owner in {"worker-a", "worker-b"}
    assert await repository.get_operation(OWNER, "operation-lease") == winners[0]


@pytest.mark.asyncio
async def test_sql_workers_with_independent_engines_compete_for_one_lease(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sql-independent-workers.db"
    first_engine = await _create_sql_engine(database_path)
    second_engine = await _create_sql_engine(database_path)
    first_repository = SQLAgentRuntimeRepository(async_sessionmaker(first_engine, expire_on_commit=False))
    second_repository = SQLAgentRuntimeRepository(async_sessionmaker(second_engine, expire_on_commit=False))
    try:
        await first_repository.create_operation(OWNER, _operation())

        results = await asyncio.gather(
            _coordinator(first_repository).claim(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            ),
            _coordinator(second_repository).claim(
                "operation-lease",
                lease_owner="worker-b",
                now=NOW,
                lease_expires_at=NOW + timedelta(seconds=30),
            ),
        )

        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        assert winners[0].lease_owner in {"worker-a", "worker-b"}
        assert await first_repository.get_operation(OWNER, "operation-lease") == winners[0]
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_heartbeat_only_extends_current_active_lease(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-heartbeat.db",
    ) as repository:
        await repository.create_operation(OWNER, _operation())
        coordinator = _coordinator(repository)
        claimed = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        assert claimed is not None

        unchanged = await coordinator.heartbeat(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=5),
            lease_expires_at=NOW + timedelta(seconds=20),
        )
        wrong_worker = await coordinator.heartbeat(
            "operation-lease",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=5),
            lease_expires_at=NOW + timedelta(seconds=60),
        )
        extended = await coordinator.heartbeat(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=5),
            lease_expires_at=NOW + timedelta(seconds=60),
        )

        assert unchanged is None
        assert wrong_worker is None
        assert extended is not None
        assert extended.lease_owner == "worker-a"
        assert extended.lease_expires_at == NOW + timedelta(seconds=60)
        assert extended.updated_at == NOW + timedelta(seconds=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_schedule_next_poll_releases_lease_until_due_time(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-schedule.db",
    ) as repository:
        await repository.create_operation(OWNER, _operation())
        coordinator = _coordinator(repository)
        claimed = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        assert claimed is not None

        scheduled = await coordinator.schedule_next_poll(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=5),
            next_poll_at=NOW + timedelta(seconds=20),
        )
        early = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=19),
            lease_expires_at=NOW + timedelta(seconds=49),
        )
        due = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=20),
            lease_expires_at=NOW + timedelta(seconds=50),
        )

        assert scheduled is not None
        assert scheduled.next_poll_at == NOW + timedelta(seconds=20)
        assert scheduled.lease_owner is None
        assert scheduled.lease_expires_at is None
        assert scheduled.updated_at == NOW + timedelta(seconds=5)
        assert early is None
        assert due is not None
        assert due.lease_owner == "worker-b"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_expired_lease_is_taken_over_and_old_worker_loses_write_right(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-takeover.db",
    ) as repository:
        await repository.create_operation(OWNER, _operation())
        coordinator = _coordinator(repository)
        first = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=10),
        )
        assert first is not None

        takeover = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=10),
            lease_expires_at=NOW + timedelta(seconds=40),
        )
        stale_heartbeat = await coordinator.heartbeat(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=11),
            lease_expires_at=NOW + timedelta(seconds=50),
        )
        stale_schedule = await coordinator.schedule_next_poll(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=11),
            next_poll_at=NOW + timedelta(seconds=20),
        )

        assert takeover is not None
        assert takeover.lease_owner == "worker-b"
        assert takeover.lease_expires_at == NOW + timedelta(seconds=40)
        assert stale_heartbeat is None
        assert stale_schedule is None
        assert await repository.get_operation(OWNER, "operation-lease") == takeover


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_heartbeat_and_schedule_reject_expired_current_lease(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-expired-write.db",
    ) as repository:
        await repository.create_operation(OWNER, _operation())
        coordinator = _coordinator(repository)
        claimed = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=10),
        )
        assert claimed is not None

        assert (
            await coordinator.heartbeat(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW + timedelta(seconds=10),
                lease_expires_at=NOW + timedelta(seconds=30),
            )
            is None
        )
        assert (
            await coordinator.schedule_next_poll(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW + timedelta(seconds=10),
                next_poll_at=NOW + timedelta(seconds=20),
            )
            is None
        )
        assert await repository.get_operation(OWNER, "operation-lease") == claimed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_lease_operations_reject_invalid_time_boundaries(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-invalid-time.db",
    ) as repository:
        await repository.create_operation(OWNER, _operation())
        coordinator = _coordinator(repository)

        with pytest.raises(ValueError):
            await coordinator.claim(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW,
            )
        with pytest.raises(ValueError):
            await coordinator.claim(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW.replace(tzinfo=None),
                lease_expires_at=NOW + timedelta(seconds=30),
            )

        claimed = await coordinator.claim(
            "operation-lease",
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        assert claimed is not None
        with pytest.raises(ValueError):
            await coordinator.schedule_next_poll(
                "operation-lease",
                lease_owner="worker-a",
                now=NOW + timedelta(seconds=5),
                next_poll_at=NOW + timedelta(seconds=5),
            )
