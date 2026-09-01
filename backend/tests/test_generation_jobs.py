from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.generation_jobs.contracts import (
    GenerationJobKind,
    GenerationJobRecord,
    GenerationJobStatus,
)
from pixelflow.generation_jobs.credentials import TransientGenerationJobCredentialStore
from pixelflow.generation_jobs.repository import MemoryGenerationJobRepository, SQLGenerationJobRepository
from pixelflow.generation_jobs.service import GenerationJobService
from pixelflow.generation_jobs.worker import GenerationJobWorker
from pixelflow.operations.jobs.providers import ProviderJobOutcome, ProviderJobSnapshot
from pixelflow.platform.persistence import ensure_schema
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace import MemoryVideoAgentRepository


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


class _FakeProvider:
    provider_id = "fake-image"
    profile_version = "test-v1"

    def prepare_operation_request(self, request):
        return {**dict(request), "provider_id": self.provider_id, "provider_profile_version": self.profile_version}

    async def start(self, request, *, authorization, idempotency_key):
        del request, authorization, idempotency_key
        return ProviderJobSnapshot(
            provider_job_id="provider-image-1",
            outcome=ProviderJobOutcome.POLLING,
            reason_code="provider_polling",
            message="供应商任务处理中。",
        )

    async def status(self, provider_job_id, *, user_id, conversation_id):
        del user_id, conversation_id
        return ProviderJobSnapshot(
            provider_job_id=provider_job_id,
            outcome=ProviderJobOutcome.SUCCEEDED,
            result={
                "image_url": "https://cdn.example/image-1.png",
                "artifact_ref": "artifact:image:image-1",
            },
            reason_code="provider_succeeded",
            message="供应商任务已完成。",
        )


def _context(*, credential: bool = True) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            revision=3,
            payload={
                "creation_contract": {"aspect_ratio": "9:16"},
                "asset_registry": [
                    {
                        "asset_id": "asset-host",
                        "origin": "planned_generation",
                        "state": "planned",
                        "generation_prompt": "现代厨房中的年轻女主人",
                    }
                ],
            },
        ),
        run_id="hrun_test",
        tool_call_id="tool-call-test",
        credential=TransientVideoAgentCredential("secret-not-persisted") if credential else None,
    )


@pytest.mark.asyncio
async def test_generation_job_service_submits_image_job_without_batch() -> None:
    repository = MemoryGenerationJobRepository()
    credentials = TransientGenerationJobCredentialStore()
    service = GenerationJobService(
        repository=repository,
        image_provider=_FakeProvider(),
        video_provider=None,
        credential_store=credentials,
    )

    submissions = await service.submit_images(
        _context(),
        assets=(_context().workspace.payload["asset_registry"][0],),
        attempt=1,
    )

    assert len(submissions) == 1
    assert submissions[0].job_id.startswith("generation-job-")
    assert submissions[0].status == "queued"
    record = await repository.get(submissions[0].job_id)
    assert record is not None
    assert record.item_id == "asset-host"
    assert "secret-not-persisted" not in repr(record)
    assert await credentials.get(generation_job_id=record.generation_job_id) is not None


@pytest.mark.asyncio
async def test_generation_job_service_replays_same_image_job() -> None:
    repository = MemoryGenerationJobRepository()
    service = GenerationJobService(
        repository=repository,
        image_provider=_FakeProvider(),
        credential_store=TransientGenerationJobCredentialStore(),
    )
    context = _context()
    asset = context.workspace.payload["asset_registry"][0]

    first = await service.submit_images(context, assets=(asset,), attempt=1)
    second = await service.submit_images(context, assets=(asset,), attempt=1)

    assert second[0].job_id == first[0].job_id
    assert len(await repository.list_all()) == 1


@pytest.mark.asyncio
async def test_generation_job_service_requires_confirmation_credential() -> None:
    service = GenerationJobService(
        repository=MemoryGenerationJobRepository(),
        image_provider=_FakeProvider(),
        credential_store=TransientGenerationJobCredentialStore(),
    )

    with pytest.raises(ValueError, match="临时授权"):
        await service.submit_images(
            _context(credential=False),
            assets=(_context().workspace.payload["asset_registry"][0],),
            attempt=1,
        )


@pytest.mark.asyncio
async def test_generation_job_worker_starts_polls_and_projects_image_result() -> None:
    repository = MemoryGenerationJobRepository()
    credentials = TransientGenerationJobCredentialStore()
    context = _context()
    workspace_repository = MemoryVideoAgentRepository()
    await workspace_repository.create_workspace(context.user_id, context.workspace)
    service = GenerationJobService(
        repository=repository,
        image_provider=_FakeProvider(),
        credential_store=credentials,
    )
    submission = (await service.submit_images(
        context,
        assets=(context.workspace.payload["asset_registry"][0],),
        attempt=1,
    ))[0]
    now = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    worker = GenerationJobWorker(
        repository=repository,
        workspace_repository=workspace_repository,
        credential_store=credentials,
        image_provider=_FakeProvider(),
        worker_id="generation-worker-test",
        clock=lambda: now,
    )

    assert await worker.run_once() == 1
    started = await repository.get(submission.job_id)
    assert started is not None
    assert started.status is GenerationJobStatus.POLLING
    assert started.provider_job_id == "provider-image-1"

    now = now + timedelta(seconds=3)
    assert await worker.run_once() == 1
    completed = await repository.get(submission.job_id)
    assert completed is not None
    assert completed.status is GenerationJobStatus.SUCCEEDED
    updated_workspace = await workspace_repository.get_workspace(
        context.user_id,
        context.workspace.workspace_id,
    )
    assert updated_workspace is not None
    asset = updated_workspace.payload["asset_registry"][0]
    assert asset["state"] == "ready"
    assert asset["provider_artifact_ref"] == "artifact:image:image-1"
    assert asset["image_url"] == "https://cdn.example/image-1.png"


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
