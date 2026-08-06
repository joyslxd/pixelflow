"""M06.5 Operation 启动竞争、重启、404 与人工恢复合同。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import (
    AgentEvent,
    AgentEventType,
    ExternalJobStatus,
)
from pixelflow.agent_runtime.jobs import (
    MappingProviderJobAdapterResolver,
    OperationManualRecoveryAction,
    OperationQuotaCoordinator,
    OperationRecoveryRuntime,
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    ProviderJobOutcome,
    build_operation_idempotency_key,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    OperationRecord,
    SQLAgentRuntimeRepository,
)
from pixelflow.agent_runtime.ports import OperationConflictError

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
OWNER = "user-operation-recovery"
CONVERSATION = "conversation-operation-recovery"
WORKFLOW = "workflow-operation-recovery"
JOB_ID = "operation-recovery"
PROVIDER_JOB_ID = "provider-job-recovery"
AUTHORIZATION = "Bearer m06-secret-must-not-persist"
PROVIDER_REQUEST = {"prompt": "生成商品主图", "count": 1}


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
async def _repositories(
    kind: RepositoryKind,
    database_path: Path,
) -> AsyncIterator[
    tuple[
        AgentRuntimeRepository,
        AgentRuntimeRepository,
    ]
]:
    if kind == "memory":
        repository = MemoryAgentRuntimeRepository()
        yield repository, repository
        return

    first_engine = await _create_sql_engine(database_path)
    second_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    try:
        yield (
            SQLAgentRuntimeRepository(async_sessionmaker(first_engine, expire_on_commit=False)),
            SQLAgentRuntimeRepository(async_sessionmaker(second_engine, expire_on_commit=False)),
        )
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


def _polling_operation(
    *,
    job_id: str = JOB_ID,
    provider_job_id: str = PROVIDER_JOB_ID,
    workflow_id: str = WORKFLOW,
    next_poll_at: datetime = NOW,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> OperationRecord:
    return OperationRecord(
        job_id=job_id,
        provider_job_id=provider_job_id,
        workflow_id=workflow_id,
        conversation_id=CONVERSATION,
        stage="image_generate",
        stage_version=1,
        status=ExternalJobStatus.POLLING,
        attempt=1,
        request_hash=f"sha256:{'a' * 64}",
        idempotency_key=build_operation_idempotency_key(
            workflow_id,
            "image_generate",
            1,
            1,
        ),
        next_poll_at=next_poll_at,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )


class _HttpStatusError(RuntimeError):
    """只向 Adapter 暴露 HTTP status，不携带响应正文。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("测试异常正文不得进入持久化状态")


class _ScriptedExistingJobService:
    """记录调用并按顺序返回确定性 start/status 结果。"""

    def __init__(
        self,
        *,
        start_response: object | None = None,
        status_results: list[object] | None = None,
    ) -> None:
        self.start_response = start_response or {
            "job_id": PROVIDER_JOB_ID,
            "status": "running",
            "result": {"progress": 0},
        }
        self.status_results = list(status_results or [])
        self.start_calls: list[dict[str, object]] = []
        self.status_calls: list[str] = []

    async def start(
        self,
        request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        self.start_calls.append(
            {
                "request": dict(request),
                "authorization": authorization,
                "idempotency_key": idempotency_key,
            }
        )
        await asyncio.sleep(0)
        if isinstance(self.start_response, BaseException):
            raise self.start_response
        return self.start_response

    async def status(self, provider_job_id: str) -> object:
        self.status_calls.append(provider_job_id)
        if not self.status_results:
            raise AssertionError("测试未配置下一条 Provider status 结果")
        result = self.status_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _ScopedExistingJobService(_ScriptedExistingJobService):
    """验证恢复Runtime只把Repository中的所有者作用域交给显式Service。"""

    def __init__(self) -> None:
        super().__init__()
        self.scoped_status_calls: list[tuple[str, str, str]] = []

    async def status_scoped(
        self,
        provider_job_id: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> object:
        self.scoped_status_calls.append(
            (provider_job_id, user_id, conversation_id)
        )
        return {
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {"artifact_ref": "artifact:scoped-status-result"},
        }


class _RecordingGraphResumer:
    """按事件 ID 记录 Workflow 恢复，不执行真实业务图。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, AgentEvent, str]] = []

    async def resume_external_job(
        self,
        namespace: object,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls.append((namespace, completion_event, idempotency_key))


class _RecordingQuotaGraph:
    """记录 quota 事件的 Workflow 恢复边界，不执行真实业务图。"""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[tuple[object, AgentEvent, str]] = []
        self._fail_first = fail_first

    async def resume_external_job_quota(
        self,
        namespace: object,
        *,
        quota_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls.append((namespace, quota_event, idempotency_key))
        if self._fail_first:
            self._fail_first = False
            raise RuntimeError("模拟 quota checkpoint 失败")


class _OrderedRepository:
    """仅记录恢复扫描三个公开 Repository 边界的顺序。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_pending_operation_quota_events(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[object]:
        del now, limit
        self.calls.append("list_pending_operation_quota_events")
        return []

    async def list_pending_operation_completions(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[object]:
        del now, limit
        self.calls.append("list_pending_operation_completions")
        return []

    async def list_due_operations(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[object]:
        del now, limit
        self.calls.append("list_due_operations")
        return []


def _resolver(
    service: _ScriptedExistingJobService,
) -> MappingProviderJobAdapterResolver:
    return MappingProviderJobAdapterResolver({"image_generate": ProviderJobAdapter(service)})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_concurrent_start_calls_provider_once_and_never_persists_authorization(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / f"{kind}-start-once.db"
    service = _ScriptedExistingJobService()
    request = build_operation_request(
        workflow_id=WORKFLOW,
        stage="image_generate",
        stage_version=1,
        attempt=1,
        provider_request=PROVIDER_REQUEST,
    )

    async with _repositories(kind, database_path) as (
        first_repository,
        second_repository,
    ):
        first = OperationStartCoordinator(
            first_repository,
            adapter=ProviderJobAdapter(service),
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW,
            job_id_factory=lambda: "operation-start-a",
            lease_duration=timedelta(seconds=30),
            first_poll_delay=timedelta(seconds=5),
        )
        second = OperationStartCoordinator(
            second_repository,
            adapter=ProviderJobAdapter(service),
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW,
            job_id_factory=lambda: "operation-start-b",
            lease_duration=timedelta(seconds=30),
            first_poll_delay=timedelta(seconds=5),
        )

        results = await asyncio.gather(
            first.start(
                request,
                provider_request=PROVIDER_REQUEST,
                authorization=AUTHORIZATION,
                lease_owner="starter-a",
            ),
            second.start(
                request,
                provider_request=PROVIDER_REQUEST,
                authorization=AUTHORIZATION,
                lease_owner="starter-b",
            ),
        )
        stored = await first_repository.get_operation(
            OWNER,
            results[0].job_id,
        )

        assert len(service.start_calls) == 1
        assert {result.job_id for result in results} == {stored.job_id}
        assert stored.status is ExternalJobStatus.POLLING
        assert stored.provider_job_id == PROVIDER_JOB_ID
        assert stored.next_poll_at == NOW + timedelta(seconds=5)
        assert stored.lease_owner is None
        assert stored.lease_expires_at is None
        serialized = stored.model_dump_json()
        assert AUTHORIZATION not in serialized
        assert "生成商品主图" not in serialized

    if kind == "sql":
        database_bytes = database_path.read_bytes()
        assert AUTHORIZATION.encode() not in database_bytes
        assert "生成商品主图".encode() not in database_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_synchronous_start_result_atomically_persists_terminal_event(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    service = _ScriptedExistingJobService(
        start_response={
            "job_id": "provider-merge-sync-1",
            "status": "succeeded",
            "result": {"video_url": "https://cdn.example.invalid/merged.mp4"},
        }
    )
    request = build_operation_request(
        workflow_id=WORKFLOW,
        stage="image_generate",
        stage_version=1,
        attempt=1,
        provider_request=PROVIDER_REQUEST,
    )
    async with _repositories(
        kind,
        tmp_path / f"{kind}-sync-start-terminal.db",
    ) as (repository, _):
        completed = await OperationStartCoordinator(
            repository,
            adapter=ProviderJobAdapter(service),
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW,
            job_id_factory=lambda: JOB_ID,
        ).start(
            request,
            provider_request=PROVIDER_REQUEST,
            authorization=AUTHORIZATION,
            lease_owner="starter-sync-terminal",
        )
        pending = await repository.list_pending_operation_completions(
            now=NOW,
            limit=10,
        )
        events = await repository.list_events(OWNER, CONVERSATION)

    assert completed.status is ExternalJobStatus.SUCCEEDED
    assert completed.provider_job_id == "provider-merge-sync-1"
    assert completed.next_poll_at is None
    assert completed.lease_owner is None
    assert len(pending) == 1
    assert pending[0].operation == completed
    assert events[-1].payload["result"]["video_url"].endswith("merged.mp4")
    assert service.status_calls == []


@pytest.mark.asyncio
async def test_recovery_runtime_passes_repository_owner_to_scoped_status_service() -> None:
    service = _ScopedExistingJobService()
    repository = MemoryAgentRuntimeRepository()
    request = build_operation_request(
        workflow_id=WORKFLOW,
        stage="image_generate",
        stage_version=1,
        attempt=1,
        provider_request=PROVIDER_REQUEST,
    )
    await OperationStartCoordinator(
        repository,
        adapter=ProviderJobAdapter(service),
        user_id=OWNER,
        conversation_id=CONVERSATION,
        clock=lambda: NOW,
        job_id_factory=lambda: JOB_ID,
        first_poll_delay=timedelta(seconds=2),
    ).start(
        request,
        provider_request=PROVIDER_REQUEST,
        authorization=AUTHORIZATION,
        lease_owner="scoped-status-starter",
    )
    await OperationRecoveryRuntime(
        repository,
        resolver=_resolver(service),
        resumer=_RecordingGraphResumer(),
        worker_id="scoped-status-worker",
        clock=lambda: NOW + timedelta(seconds=3),
    ).run_once()

    assert service.scoped_status_calls == [
        (PROVIDER_JOB_ID, OWNER, CONVERSATION)
    ]
    assert service.status_calls == []


@pytest.mark.asyncio
async def test_provider_status_404_maps_to_expired_without_response_leak() -> None:
    service = _ScriptedExistingJobService(
        status_results=[_HttpStatusError(404)],
    )

    snapshot = await ProviderJobAdapter(service).status(PROVIDER_JOB_ID)

    assert snapshot.outcome is ProviderJobOutcome.EXPIRED
    assert snapshot.provider_job_id == PROVIDER_JOB_ID
    assert snapshot.reason_code == "provider_job_expired"
    assert snapshot.message == "供应商原任务已过期，需要用户手动重新发起。"
    assert snapshot.result is None
    assert "测试异常正文" not in snapshot.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_start_quota_pause_returns_safe_recoverable_error(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    service = _ScriptedExistingJobService(
        start_response=_HttpStatusError(402),
    )
    request = build_operation_request(
        workflow_id=WORKFLOW,
        stage="image_generate",
        stage_version=1,
        attempt=1,
        provider_request=PROVIDER_REQUEST,
    )

    async with _repositories(
        kind,
        tmp_path / f"{kind}-start-quota.db",
    ) as (repository, _):
        coordinator = OperationStartCoordinator(
            repository,
            adapter=ProviderJobAdapter(service),
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW,
            job_id_factory=lambda: JOB_ID,
        )

        with pytest.raises(OperationStartQuotaPausedError) as error:
            await coordinator.start(
                request,
                provider_request=PROVIDER_REQUEST,
                authorization=AUTHORIZATION,
                lease_owner="starter-quota",
            )

        stored = await repository.get_operation(OWNER, JOB_ID)
        assert error.value.reason_code == "provider_quota_insufficient"
        assert error.value.message == "额度不足，当前任务尚未启动，可在充值后重试。"
        assert error.value.operation == stored
        assert stored.status is ExternalJobStatus.CREATED
        assert stored.provider_job_id is None
        assert stored.lease_owner is None
        assert stored.lease_expires_at is None
        assert AUTHORIZATION not in error.value.operation.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_status_402_persists_pause_event_before_graph_and_replays_same_identity(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    service = _ScriptedExistingJobService(
        status_results=[{"job_id": PROVIDER_JOB_ID, "status": "quota_paused"}]
    )
    resumer = _RecordingGraphResumer()
    quota_graph = _RecordingQuotaGraph()

    async with _repositories(
        kind,
        tmp_path / f"{kind}-quota-resume.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=resumer,
            quota_resumer=quota_graph,
            worker_id="runtime-quota",
            clock=lambda: NOW,
            lease_duration=timedelta(seconds=30),
            poll_interval=timedelta(seconds=5),
            scan_interval=timedelta(seconds=1),
        )

        await runtime.run_once()
        paused = await repository.get_operation(OWNER, JOB_ID)

        assert paused.status is ExternalJobStatus.POLLING
        assert paused.provider_job_id == PROVIDER_JOB_ID
        assert paused.next_poll_at is None
        assert paused.lease_owner is None
        assert paused.lease_expires_at is None
        assert paused.quota_pause_revision == 1
        assert len(quota_graph.calls) == 1
        first_event = quota_graph.calls[0][1]
        assert first_event.payload["quota_state"] == "paused"
        assert first_event.payload["quota_pause_revision"] == 1
        assert quota_graph.calls[0][2] == first_event.event_id

        await runtime.run_once()

        assert len(quota_graph.calls) == 1
        assert service.status_calls == [PROVIDER_JOB_ID]
        assert service.start_calls == []
        assert resumer.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_paused_operation_rejects_unauthorized_manual_recovery(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """防止旧 recover_manually 绕过 quota resume 授权事件直接续跑 Provider。"""

    service = _ScriptedExistingJobService(
        status_results=[{"job_id": PROVIDER_JOB_ID, "status": "quota_paused"}]
    )
    async with _repositories(
        kind,
        tmp_path / f"{kind}-quota-manual-closed.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=_RecordingGraphResumer(),
            quota_resumer=_RecordingQuotaGraph(),
            worker_id="runtime-quota-manual-closed",
            clock=lambda: NOW,
        )
        await runtime.run_once()

        with pytest.raises(
            OperationConflictError,
            match="quota_resume_requires_authorized_handler",
        ):
            await runtime.recover_manually(OWNER, CONVERSATION, JOB_ID)

        paused = await repository.get_operation(OWNER, JOB_ID)
        assert paused.next_poll_at is None
        assert service.status_calls == [PROVIDER_JOB_ID]
        assert service.start_calls == []


@pytest.mark.asyncio
async def test_recovery_scan_order_is_quota_then_completion_then_due() -> None:
    """防止到期轮询越过 quota 或 completion Outbox，导致重复副作用。"""

    repository = _OrderedRepository()
    service = _ScriptedExistingJobService()
    runtime = OperationRecoveryRuntime(
        repository,
        resolver=_resolver(service),
        resumer=_RecordingGraphResumer(),
        quota_resumer=_RecordingQuotaGraph(),
        worker_id="runtime-scan-order",
        clock=lambda: NOW,
    )

    await runtime.run_once()

    assert repository.calls == [
        "list_pending_operation_quota_events",
        "list_pending_operation_completions",
        "list_due_operations",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_missing_quota_resumer_keeps_event_pending_and_stops_polling(
    kind: RepositoryKind,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """防止未装配 quota Graph 时丢弃事件或继续查询原 Provider job。"""

    caplog.set_level("WARNING")
    service = _ScriptedExistingJobService(
        status_results=[{"job_id": PROVIDER_JOB_ID, "status": "quota_paused"}]
    )
    async with _repositories(
        kind,
        tmp_path / f"{kind}-quota-resumer-missing.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=_RecordingGraphResumer(),
            worker_id="runtime-quota-resumer-missing",
            clock=lambda: NOW,
        )

        await runtime.run_once()
        await runtime.run_once()

        pending = await repository.list_pending_operation_quota_events(
            now=NOW,
            limit=10,
        )
        assert len(pending) == 1
        assert pending[0].event.payload["quota_state"] == "paused"
        assert service.status_calls == [PROVIDER_JOB_ID]
        assert service.start_calls == []
        assert "phase=quota_dispatch error_type=OperationConflictError" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_second_status_402_increments_revision_and_rejects_old_resume(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """防止第二次 402 复用旧事件身份，或旧 revision 授权穿透新暂停。"""

    clock = [NOW]
    service = _ScriptedExistingJobService(
        status_results=[
            {"job_id": PROVIDER_JOB_ID, "status": "quota_paused"},
            {"job_id": PROVIDER_JOB_ID, "status": "quota_paused"},
        ]
    )
    quota_graph = _RecordingQuotaGraph()
    async with _repositories(
        kind,
        tmp_path / f"{kind}-quota-revision-two.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=_RecordingGraphResumer(),
            quota_resumer=quota_graph,
            worker_id="runtime-quota-revision",
            clock=lambda: clock[0],
            lease_duration=timedelta(seconds=30),
        )
        await runtime.run_once()

        clock[0] = NOW + timedelta(seconds=1)
        coordinator = OperationQuotaCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
        )
        authorized = await coordinator.authorize_resume(
            JOB_ID,
            workflow_id=WORKFLOW,
            expected_revision=1,
            delivery_lease_owner="quota-user-action",
            now=clock[0],
            delivery_lease_expires_at=clock[0] + timedelta(seconds=30),
        )
        await repository.complete_event_delivery(
            OWNER,
            authorized.claim.event.event_id,
            lease_owner="quota-user-action",
            published_at=clock[0],
        )
        await runtime.run_once()

        paused_again = await repository.get_operation(OWNER, JOB_ID)
        assert paused_again.quota_pause_revision == 2
        assert [call[1].payload["quota_pause_revision"] for call in quota_graph.calls] == [1, 2]
        assert quota_graph.calls[0][1].event_id != quota_graph.calls[1][1].event_id
        with pytest.raises(OperationConflictError, match="quota revision"):
            await coordinator.authorize_resume(
                JOB_ID,
                workflow_id=WORKFLOW,
                expected_revision=1,
                delivery_lease_owner="stale-quota-user-action",
                now=clock[0],
                delivery_lease_expires_at=clock[0] + timedelta(seconds=30),
            )
        assert service.status_calls == [PROVIDER_JOB_ID, PROVIDER_JOB_ID]
        assert service.start_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_graph_failure_keeps_pause_event_for_expired_lease_takeover(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """防止 Graph 失败后误确认 pause 事件，或有效租约期内被其他 worker 重放。"""

    clock = [NOW]
    service = _ScriptedExistingJobService(
        status_results=[{"job_id": PROVIDER_JOB_ID, "status": "quota_paused"}]
    )
    first_graph = _RecordingQuotaGraph(fail_first=True)
    second_graph = _RecordingQuotaGraph()
    async with _repositories(
        kind,
        tmp_path / f"{kind}-quota-lease-takeover.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        first_runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=_RecordingGraphResumer(),
            quota_resumer=first_graph,
            worker_id="runtime-quota-first",
            clock=lambda: clock[0],
            lease_duration=timedelta(seconds=10),
        )
        await first_runtime.run_once()
        event_id = first_graph.calls[0][1].event_id

        second_runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=_RecordingGraphResumer(),
            quota_resumer=second_graph,
            worker_id="runtime-quota-second",
            clock=lambda: clock[0],
            lease_duration=timedelta(seconds=10),
        )
        clock[0] = NOW + timedelta(seconds=5)
        await second_runtime.run_once()
        assert second_graph.calls == []

        clock[0] = NOW + timedelta(seconds=11)
        await second_runtime.run_once()
        pending = await repository.list_pending_operation_quota_events(
            now=clock[0],
            limit=10,
        )

        assert len(second_graph.calls) == 1
        assert second_graph.calls[0][1].event_id == event_id
        assert pending == []
        assert service.status_calls == [PROVIDER_JOB_ID]
        assert service.start_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_resume_event_blocks_polling_until_delivery_lease_takeover(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """防止已授权 resume 事件尚未投递时提前轮询，并验证租约过期后可独立接管。"""

    clock = [NOW]
    service = _ScriptedExistingJobService(
        status_results=[
            {"job_id": PROVIDER_JOB_ID, "status": "quota_paused"},
            {
                "job_id": PROVIDER_JOB_ID,
                "status": "succeeded",
                "result": {"artifact_refs": ["artifact-after-quota"]},
            },
        ]
    )
    quota_graph = _RecordingQuotaGraph()
    completion_graph = _RecordingGraphResumer()
    async with _repositories(
        kind,
        tmp_path / f"{kind}-resume-event-blocks-polling.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=completion_graph,
            quota_resumer=quota_graph,
            worker_id="runtime-resume-event",
            clock=lambda: clock[0],
            lease_duration=timedelta(seconds=10),
        )
        await runtime.run_once()

        clock[0] = NOW + timedelta(seconds=1)
        authorized = await OperationQuotaCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
        ).authorize_resume(
            JOB_ID,
            workflow_id=WORKFLOW,
            expected_revision=1,
            delivery_lease_owner="quota-user-action",
            now=clock[0],
            delivery_lease_expires_at=clock[0] + timedelta(seconds=10),
        )

        clock[0] = NOW + timedelta(seconds=5)
        await runtime.run_once()
        assert service.status_calls == [PROVIDER_JOB_ID]
        assert len(quota_graph.calls) == 1

        clock[0] = NOW + timedelta(seconds=12)
        await runtime.run_once()
        completed = await repository.get_operation(OWNER, JOB_ID)

        assert quota_graph.calls[-1][1].event_id == authorized.claim.event.event_id
        assert quota_graph.calls[-1][1].payload["quota_state"] == "resumed"
        assert service.status_calls == [PROVIDER_JOB_ID, PROVIDER_JOB_ID]
        assert service.start_calls == []
        assert completed.status is ExternalJobStatus.SUCCEEDED
        assert len(completion_graph.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_expired_job_requires_new_attempt_and_never_restarts_provider(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    service = _ScriptedExistingJobService(
        status_results=[_HttpStatusError(404)],
    )
    resumer = _RecordingGraphResumer()

    async with _repositories(
        kind,
        tmp_path / f"{kind}-expired.db",
    ) as (repository, _):
        await repository.create_operation(OWNER, _polling_operation())
        runtime = OperationRecoveryRuntime(
            repository,
            resolver=_resolver(service),
            resumer=resumer,
            worker_id="runtime-expired",
            clock=lambda: NOW,
            lease_duration=timedelta(seconds=30),
            poll_interval=timedelta(seconds=5),
            scan_interval=timedelta(seconds=1),
        )

        await runtime.run_once()
        expired = await repository.get_operation(OWNER, JOB_ID)
        first_events = await repository.list_events(OWNER, CONVERSATION)
        manual = await runtime.recover_manually(
            OWNER,
            CONVERSATION,
            JOB_ID,
        )
        await runtime.run_once()

        assert expired.status is ExternalJobStatus.EXPIRED
        assert expired.provider_job_id == PROVIDER_JOB_ID
        assert manual.action is OperationManualRecoveryAction.NEW_ATTEMPT_REQUIRED
        assert manual.operation == expired
        assert len(first_events) == 1
        assert first_events[0].payload["status"] == "expired"
        assert first_events[0].payload["reason_code"] == "provider_job_expired"
        assert service.status_calls == [PROVIDER_JOB_ID]
        assert service.start_calls == []
        assert len(resumer.calls) == 1


class _FailOnceGraphResumer(_RecordingGraphResumer):
    """第一次恢复失败，后续候选仍应继续处理。"""

    async def resume_external_job(
        self,
        namespace: object,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls.append((namespace, completion_event, idempotency_key))
        if len(self.calls) == 1:
            raise RuntimeError("不得让单个 Graph 失败终止后台循环")


@pytest.mark.asyncio
async def test_bad_candidate_does_not_stop_batch_or_background_runtime() -> None:
    first_job = "operation-bad-candidate"
    second_job = "operation-good-candidate"
    first_provider = "provider-bad-candidate"
    second_provider = "provider-good-candidate"
    service = _ScriptedExistingJobService(
        status_results=[
            {
                "job_id": first_provider,
                "status": "succeeded",
                "result": {"artifact_refs": ["artifact-first"]},
            },
            {
                "job_id": second_provider,
                "status": "succeeded",
                "result": {"artifact_refs": ["artifact-second"]},
            },
        ]
    )
    repository = MemoryAgentRuntimeRepository()
    await repository.create_operation(
        OWNER,
        _polling_operation(
            job_id=first_job,
            provider_job_id=first_provider,
            workflow_id="workflow-bad-candidate",
        ),
    )
    await repository.create_operation(
        OWNER,
        _polling_operation(
            job_id=second_job,
            provider_job_id=second_provider,
            workflow_id="workflow-good-candidate",
        ),
    )
    resumer = _FailOnceGraphResumer()
    runtime = OperationRecoveryRuntime(
        repository,
        resolver=_resolver(service),
        resumer=resumer,
        worker_id="runtime-candidate-isolation",
        clock=lambda: NOW,
        lease_duration=timedelta(seconds=30),
        poll_interval=timedelta(seconds=5),
        scan_interval=timedelta(milliseconds=10),
    )

    await runtime.start()
    async with asyncio.timeout(3):
        while len(service.status_calls) < 2:
            await asyncio.sleep(0.01)
    await runtime.aclose()

    first = await repository.get_operation(OWNER, first_job)
    second = await repository.get_operation(OWNER, second_job)
    assert first.status is ExternalJobStatus.SUCCEEDED
    assert second.status is ExternalJobStatus.SUCCEEDED
    assert service.status_calls == [first_provider, second_provider]
    assert len(resumer.calls) == 2


class _AdvancingClockService(_ScriptedExistingJobService):
    """模拟 status 返回时已经跨过当前数据库租约。"""

    def __init__(self, clock: list[datetime]) -> None:
        super().__init__()
        self._clock = clock

    async def status(self, provider_job_id: str) -> object:
        self.status_calls.append(provider_job_id)
        self._clock[0] = NOW + timedelta(seconds=11)
        return {
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {"artifact_refs": ["artifact-after-lease"]},
        }


@pytest.mark.asyncio
async def test_slow_status_cannot_commit_after_poll_lease_expired() -> None:
    clock = [NOW]
    service = _AdvancingClockService(clock)
    repository = MemoryAgentRuntimeRepository()
    await repository.create_operation(OWNER, _polling_operation())
    first_resumer = _RecordingGraphResumer()
    first_runtime = OperationRecoveryRuntime(
        repository,
        resolver=_resolver(service),
        resumer=first_resumer,
        worker_id="runtime-slow-before-expiry",
        clock=lambda: clock[0],
        lease_duration=timedelta(seconds=10),
        poll_interval=timedelta(seconds=5),
        scan_interval=timedelta(seconds=1),
    )

    await first_runtime.run_once()
    after_slow_status = await repository.get_operation(OWNER, JOB_ID)

    assert after_slow_status.status is ExternalJobStatus.POLLING
    assert after_slow_status.lease_owner == "runtime-slow-before-expiry"
    assert first_resumer.calls == []

    second_resumer = _RecordingGraphResumer()
    second_runtime = OperationRecoveryRuntime(
        repository,
        resolver=_resolver(service),
        resumer=second_resumer,
        worker_id="runtime-slow-after-expiry",
        clock=lambda: clock[0],
        lease_duration=timedelta(seconds=10),
        poll_interval=timedelta(seconds=5),
        scan_interval=timedelta(seconds=1),
    )
    await second_runtime.run_once()
    completed = await repository.get_operation(OWNER, JOB_ID)

    assert completed.status is ExternalJobStatus.SUCCEEDED
    assert service.status_calls == [PROVIDER_JOB_ID, PROVIDER_JOB_ID]
    assert len(second_resumer.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_invalid_completion_prefix_cannot_starve_valid_candidate(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repositories(
        kind,
        tmp_path / f"{kind}-completion-starvation.db",
    ) as (repository, _):
        await repository.create_event(
            OWNER,
            AgentEvent(
                event_id="event-invalid-completion",
                sequence=1,
                cursor="cursor-invalid-completion",
                conversation_id=CONVERSATION,
                run_id="run-invalid-completion",
                occurred_at=NOW,
                type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
                payload={"job_id": "operation-missing", "status": "succeeded"},
            ),
        )
        terminal = _polling_operation().model_copy(
            update={
                "status": ExternalJobStatus.SUCCEEDED,
                "next_poll_at": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": NOW,
            }
        )
        await repository.create_operation(OWNER, terminal)
        await repository.create_event(
            OWNER,
            AgentEvent(
                event_id="event-valid-completion",
                sequence=2,
                cursor="cursor-valid-completion",
                conversation_id=CONVERSATION,
                run_id="run-valid-completion",
                occurred_at=NOW,
                type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
                payload={"job_id": JOB_ID, "status": "succeeded"},
            ),
        )

        candidates = await repository.list_pending_operation_completions(
            now=NOW,
            limit=1,
        )

        assert [candidate.operation.job_id for candidate in candidates] == [JOB_ID]


class _BlockingThenSuccessfulService(_ScriptedExistingJobService):
    """首轮 status 等待取消，第二轮模拟重启后查询成功。"""

    def __init__(self) -> None:
        super().__init__()
        self.first_status_started = asyncio.Event()
        self._call_count = 0

    async def status(self, provider_job_id: str) -> object:
        self.status_calls.append(provider_job_id)
        self._call_count += 1
        if self._call_count == 1:
            self.first_status_started.set()
            await asyncio.Future()
        return {
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {"artifact_refs": ["artifact-restarted"]},
        }


@pytest.mark.asyncio
async def test_shutdown_then_sql_restart_continues_original_job_after_lease_expiry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "operation-restart.db"
    first_engine = await _create_sql_engine(database_path)
    service = _BlockingThenSuccessfulService()
    first_resumer = _RecordingGraphResumer()
    first_repository = SQLAgentRuntimeRepository(async_sessionmaker(first_engine, expire_on_commit=False))
    await first_repository.create_operation(OWNER, _polling_operation())
    first_runtime = OperationRecoveryRuntime(
        first_repository,
        resolver=_resolver(service),
        resumer=first_resumer,
        worker_id="runtime-before-restart",
        clock=lambda: NOW,
        lease_duration=timedelta(seconds=10),
        poll_interval=timedelta(seconds=5),
        scan_interval=timedelta(seconds=1),
    )

    await first_runtime.start()
    await asyncio.wait_for(service.first_status_started.wait(), timeout=3)
    await first_runtime.aclose()
    await first_runtime.aclose()
    leased = await first_repository.get_operation(OWNER, JOB_ID)
    await first_engine.dispose()

    assert leased.status is ExternalJobStatus.POLLING
    assert leased.lease_owner == "runtime-before-restart"
    assert leased.lease_expires_at == NOW + timedelta(seconds=10)
    assert first_resumer.calls == []

    second_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    try:
        second_repository = SQLAgentRuntimeRepository(async_sessionmaker(second_engine, expire_on_commit=False))
        second_resumer = _RecordingGraphResumer()
        restarted_at = NOW + timedelta(seconds=11)
        second_runtime = OperationRecoveryRuntime(
            second_repository,
            resolver=_resolver(service),
            resumer=second_resumer,
            worker_id="runtime-after-restart",
            clock=lambda: restarted_at,
            lease_duration=timedelta(seconds=10),
            poll_interval=timedelta(seconds=5),
            scan_interval=timedelta(seconds=1),
        )

        await second_runtime.run_once()
        completed = await second_repository.get_operation(OWNER, JOB_ID)
        await second_runtime.aclose()
        calls_after_close = list(service.status_calls)
        await asyncio.sleep(0)

        assert completed.status is ExternalJobStatus.SUCCEEDED
        assert completed.provider_job_id == PROVIDER_JOB_ID
        assert service.status_calls == [
            PROVIDER_JOB_ID,
            PROVIDER_JOB_ID,
        ]
        assert service.start_calls == []
        assert len(second_resumer.calls) == 1
        assert service.status_calls == calls_after_close
    finally:
        await second_engine.dispose()
