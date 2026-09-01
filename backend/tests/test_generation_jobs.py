from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.generation_jobs.contracts import (
    GenerationJobKind,
    GenerationJobRecord,
    GenerationJobStatus,
)
from pixelflow.generation_jobs.repository import MemoryGenerationJobRepository, SQLGenerationJobRepository
from pixelflow.platform.persistence import ensure_schema


def _job(
    *,
    job_id: str,
    idempotency_key: str,
    status: GenerationJobStatus = GenerationJobStatus.QUEUED,
    next_poll_at: datetime | None = None,
) -> GenerationJobRecord:
    now = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    return GenerationJobRecord(
        generation_job_id=job_id,
        user_id="user-1",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        kind=GenerationJobKind.IMAGE,
        item_id=job_id.replace("generation-job-", "asset-"),
        variant_index=1,
        status=status,
        request_json={"prompt": "测试图片"},
        request_hash="sha256:" + "a" * 64,
        idempotency_key=idempotency_key,
        provider_id="content-app-image",
        provider_job_id=None,
        result_json=None,
        failure_reason_code=None,
        next_poll_at=next_poll_at,
        lease_owner=None,
        lease_expires_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_generation_job_repository_is_idempotent_by_idempotency_key() -> None:
    repository = MemoryGenerationJobRepository()
    candidate = _job(job_id="generation-job-1", idempotency_key="generation:v1:one")

    created = await repository.create_or_read(candidate)
    replayed = await repository.create_or_read(candidate.model_copy(update={"generation_job_id": "generation-job-other"}))

    assert created.generation_job_id == "generation-job-1"
    assert replayed == created


@pytest.mark.asyncio
async def test_generation_job_repository_claims_at_most_six_start_jobs() -> None:
    repository = MemoryGenerationJobRepository()
    for index in range(7):
        await repository.create_or_read(
            _job(
                job_id=f"generation-job-{index}",
                idempotency_key=f"generation:v1:{index}",
            )
        )

    claimed = await repository.claim_start_jobs(
        worker_id="generation-worker-1",
        now=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        lease_duration=timedelta(seconds=30),
        limit=6,
    )

    assert len(claimed) == 6
    assert all(job.status is GenerationJobStatus.STARTING for job in claimed)
    assert all(job.lease_owner == "generation-worker-1" for job in claimed)


@pytest.mark.asyncio
async def test_generation_job_repository_reclaims_expired_start_lease() -> None:
    repository = MemoryGenerationJobRepository()
    await repository.create_or_read(
        _job(job_id="generation-job-1", idempotency_key="generation:v1:one")
    )
    await repository.claim_start_jobs(
        worker_id="old-worker",
        now=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        lease_duration=timedelta(seconds=30),
        limit=6,
    )

    reclaimed = await repository.claim_start_jobs(
        worker_id="new-worker",
        now=datetime(2026, 9, 1, 0, 1, tzinfo=UTC),
        lease_duration=timedelta(seconds=30),
        limit=6,
    )

    assert [job.generation_job_id for job in reclaimed] == ["generation-job-1"]
    assert reclaimed[0].lease_owner == "new-worker"


@pytest.mark.asyncio
async def test_generation_job_repository_binds_provider_job_and_claims_only_due_poll() -> None:
    repository = MemoryGenerationJobRepository()
    created = await repository.create_or_read(
        _job(job_id="generation-job-1", idempotency_key="generation:v1:one")
    )
    claimed = (
        await repository.claim_start_jobs(
            worker_id="generation-worker-1",
            now=created.created_at,
            lease_duration=timedelta(seconds=30),
            limit=6,
        )
    )[0]
    polling = await repository.bind_provider_job(
        generation_job_id=claimed.generation_job_id,
        provider_job_id="provider-job-1",
        worker_id="generation-worker-1",
        now=created.created_at,
        next_poll_at=created.created_at + timedelta(seconds=2),
    )

    assert polling.status is GenerationJobStatus.POLLING
    assert polling.provider_job_id == "provider-job-1"
    assert polling.lease_owner is None
    assert await repository.claim_poll_jobs(
        worker_id="poll-worker",
        now=created.created_at + timedelta(seconds=1),
        lease_duration=timedelta(seconds=30),
        limit=6,
    ) == ()

    due = await repository.claim_poll_jobs(
        worker_id="poll-worker",
        now=created.created_at + timedelta(seconds=2),
        lease_duration=timedelta(seconds=30),
        limit=6,
    )
    assert [job.provider_job_id for job in due] == ["provider-job-1"]


@pytest.mark.asyncio
async def test_indeterminate_generation_job_is_not_claimed_again() -> None:
    repository = MemoryGenerationJobRepository()
    created = await repository.create_or_read(
        _job(job_id="generation-job-1", idempotency_key="generation:v1:one")
    )
    claimed = (
        await repository.claim_start_jobs(
            worker_id="generation-worker-1",
            now=created.created_at,
            lease_duration=timedelta(seconds=30),
            limit=6,
        )
    )[0]
    failed = await repository.complete(
        generation_job_id=claimed.generation_job_id,
        worker_id="generation-worker-1",
        status=GenerationJobStatus.INDETERMINATE,
        now=created.created_at,
        failure_reason_code="provider_job_id_missing",
    )

    assert failed.status is GenerationJobStatus.INDETERMINATE
    assert failed.failure_reason_code == "provider_job_id_missing"
    assert await repository.claim_start_jobs(
        worker_id="another-worker",
        now=created.created_at + timedelta(minutes=5),
        lease_duration=timedelta(seconds=30),
        limit=6,
    ) == ()


@pytest.mark.asyncio
async def test_sql_generation_job_repository_persists_and_reclaims_poll_job(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'generation-jobs.db'}")
    try:
        await ensure_schema(engine)
        repository = SQLGenerationJobRepository(async_sessionmaker(engine, expire_on_commit=False))
        candidate = _job(job_id="generation-job-sql", idempotency_key="generation:v1:sql")

        created = await repository.create_or_read(candidate)
        replayed = await repository.create_or_read(candidate.model_copy(update={"generation_job_id": "generation-job-other"}))
        claimed = (
            await repository.claim_start_jobs(
                worker_id="sql-worker",
                now=created.created_at,
                lease_duration=timedelta(seconds=30),
                limit=6,
            )
        )[0]
        polling = await repository.bind_provider_job(
            generation_job_id=claimed.generation_job_id,
            provider_job_id="provider-sql-1",
            worker_id="sql-worker",
            now=created.created_at,
            next_poll_at=created.created_at,
        )

        assert replayed.generation_job_id == created.generation_job_id
        assert polling.status is GenerationJobStatus.POLLING
        assert (
            await repository.claim_poll_jobs(
                worker_id="sql-poll-worker",
                now=created.created_at,
                lease_duration=timedelta(seconds=30),
                limit=6,
            )
        )[0].provider_job_id == "provider-sql-1"
    finally:
        await engine.dispose()
