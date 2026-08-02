"""M06.4 Operation 终态事件、Workflow 恢复与 crash window 合同。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.context import RepositoryCompactionEventOutbox
from pixelflow.agent_runtime.contracts import (
    AgentEvent,
    AgentEventType,
    ExternalJobStatus,
)
from pixelflow.agent_runtime.jobs import (
    OperationCompletionConflictError,
    OperationCompletionCoordinator,
    OperationCompletionDispatcher,
    OperationCompletionDispatchError,
    OperationLeaseCoordinator,
    ProviderJobOutcome,
    ProviderJobSnapshot,
    build_operation_completion_event_id,
    build_operation_idempotency_key,
)
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    OperationRecord,
    OperationTerminalEventRecord,
    SQLAgentRuntimeRepository,
)

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
OWNER = "user-operation-completion"
CONVERSATION = "conversation-operation-completion"
JOB_ID = "operation-completion"
PROVIDER_JOB_ID = "provider-job-completion"


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


@asynccontextmanager
async def _file_sql_repositories(
    database_path: Path,
) -> AsyncIterator[tuple[SQLAgentRuntimeRepository, SQLAgentRuntimeRepository]]:
    engine_a = await _create_sql_engine(database_path)
    engine_b = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    try:
        yield (
            SQLAgentRuntimeRepository(async_sessionmaker(engine_a, expire_on_commit=False)),
            SQLAgentRuntimeRepository(async_sessionmaker(engine_b, expire_on_commit=False)),
        )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


def _operation(
    *,
    job_id: str = JOB_ID,
    conversation_id: str = CONVERSATION,
    provider_job_id: str = PROVIDER_JOB_ID,
) -> OperationRecord:
    workflow_id = f"workflow-{job_id}"
    return OperationRecord(
        job_id=job_id,
        provider_job_id=provider_job_id,
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        stage="image_generate",
        stage_version=2,
        status=ExternalJobStatus.POLLING,
        attempt=1,
        request_hash=f"sha256:{'a' * 64}",
        idempotency_key=build_operation_idempotency_key(
            workflow_id,
            "image_generate",
            2,
            1,
        ),
        next_poll_at=NOW,
        lease_owner=None,
        lease_expires_at=None,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )


def _snapshot(
    outcome: ProviderJobOutcome = ProviderJobOutcome.SUCCEEDED,
    *,
    provider_job_id: str = PROVIDER_JOB_ID,
) -> ProviderJobSnapshot:
    contracts = {
        ProviderJobOutcome.POLLING: (
            "provider_polling",
            "供应商任务处理中。",
            {"progress": 50},
        ),
        ProviderJobOutcome.SUCCEEDED: (
            "provider_succeeded",
            "供应商任务已完成。",
            {
                "artifact_refs": ["artifact-image-1"],
                "preview_url": "https://cdn.example.com/output.png",
            },
        ),
        ProviderJobOutcome.FAILED: (
            "provider_business_failed",
            "供应商任务执行失败。",
            None,
        ),
        ProviderJobOutcome.PAUSED_QUOTA: (
            "provider_quota_insufficient",
            "额度不足，当前任务已暂停，可在充值后继续。",
            None,
        ),
        ProviderJobOutcome.TIMEOUT: (
            "provider_timeout",
            "供应商任务等待超时。",
            None,
        ),
    }
    reason_code, message, result = contracts[outcome]
    return ProviderJobSnapshot(
        provider_job_id=provider_job_id,
        outcome=outcome,
        result=result,
        reason_code=reason_code,
        message=message,
    )


async def _leased_operation(
    repository: AgentRuntimeRepository,
    *,
    owner: str = OWNER,
    conversation_id: str = CONVERSATION,
    operation: OperationRecord | None = None,
    lease_owner: str = "poller-a",
) -> OperationRecord:
    record = operation or _operation(conversation_id=conversation_id)
    await repository.create_operation(owner, record)
    claimed = await OperationLeaseCoordinator(
        repository,
        user_id=owner,
        conversation_id=conversation_id,
    ).claim(
        record.job_id,
        lease_owner=lease_owner,
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    assert claimed is not None
    return claimed


def _coordinator(
    repository: AgentRuntimeRepository,
    *,
    user_id: str = OWNER,
    conversation_id: str = CONVERSATION,
) -> OperationCompletionCoordinator:
    return OperationCompletionCoordinator(
        repository,
        user_id=user_id,
        conversation_id=conversation_id,
    )


class _CheckpointingGraphResumer:
    """用完成事件 ID 模拟 Workflow checkpoint 的幂等恢复。"""

    def __init__(self, *, fail_after_first_checkpoint: bool = False) -> None:
        self.calls: list[tuple[object, AgentEvent, str]] = []
        self.checkpointed_event_ids: set[str] = set()
        self.applied_count = 0
        self._fail_after_first_checkpoint = fail_after_first_checkpoint

    async def resume_external_job(
        self,
        namespace: object,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls.append((namespace, completion_event, idempotency_key))
        if idempotency_key not in self.checkpointed_event_ids:
            self.checkpointed_event_ids.add(idempotency_key)
            self.applied_count += 1
        if self._fail_after_first_checkpoint:
            self._fail_after_first_checkpoint = False
            raise RuntimeError("模拟 Workflow checkpoint 后进程退出")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_terminal_snapshot_atomically_updates_operation_and_outbox(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-terminal.db",
    ) as repository:
        await _leased_operation(repository)

        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )
        replayed = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=2),
        )

        assert completion == replayed
        assert completion.operation.status is ExternalJobStatus.SUCCEEDED
        assert completion.operation.provider_job_id == PROVIDER_JOB_ID
        assert completion.operation.next_poll_at is None
        assert completion.operation.lease_owner is None
        assert completion.operation.lease_expires_at is None
        assert completion.event.type is AgentEventType.EXTERNAL_JOB_STATE_CHANGED
        assert completion.event.event_id == build_operation_completion_event_id(JOB_ID)
        assert completion.event.sequence == 1
        assert completion.event.payload == {
            "job_id": JOB_ID,
            "provider_job_id": PROVIDER_JOB_ID,
            "workflow_id": f"workflow-{JOB_ID}",
            "stage": "image_generate",
            "stage_version": 2,
            "attempt": 1,
            "status": "succeeded",
            "reason_code": "provider_succeeded",
            "message": "供应商任务已完成。",
            "result": {
                "artifact_refs": ["artifact-image-1"],
                "preview_url": "https://cdn.example.com/output.png",
            },
        }
        assert "request_hash" not in completion.event.payload
        assert "idempotency_key" not in completion.event.payload
        assert await repository.get_operation(OWNER, JOB_ID) == completion.operation
        assert await repository.list_events(OWNER, CONVERSATION) == [completion.event]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_wrong_owner_cannot_poison_conversation_before_terminal_event(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-terminal-owner-safe.db",
    ) as repository:
        leased = await _leased_operation(repository)
        terminal_event = OperationTerminalEventRecord(
            event_id="evt_job_done_owner_safe",
            cursor="cursor-job-done-owner-safe",
            run_id="run-job-done-owner-safe",
            occurred_at=NOW + timedelta(seconds=1),
            payload={"job_id": JOB_ID, "status": "succeeded"},
        )

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.finalize_operation_terminal(
                "user-operation-completion-other",
                CONVERSATION,
                JOB_ID,
                provider_job_id=PROVIDER_JOB_ID,
                terminal_status=ExternalJobStatus.SUCCEEDED,
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=1),
                event=terminal_event,
            )

        assert await repository.get_operation(OWNER, JOB_ID) == leased
        assert await repository.list_events(OWNER, CONVERSATION) == []
        assert await repository.list_events(
            "user-operation-completion-other",
            CONVERSATION,
        ) == []

        operation, completion_event = await repository.finalize_operation_terminal(
            OWNER,
            CONVERSATION,
            JOB_ID,
            provider_job_id=PROVIDER_JOB_ID,
            terminal_status=ExternalJobStatus.SUCCEEDED,
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
            event=terminal_event,
        )
        assert operation.status is ExternalJobStatus.SUCCEEDED
        assert completion_event.sequence == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (ProviderJobOutcome.FAILED, ExternalJobStatus.FAILED),
        (ProviderJobOutcome.TIMEOUT, ExternalJobStatus.TIMEOUT),
    ],
)
async def test_failed_and_timeout_snapshots_use_declared_terminal_status(
    kind: RepositoryKind,
    outcome: ProviderJobOutcome,
    expected_status: ExternalJobStatus,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-{outcome.value}.db",
    ) as repository:
        await _leased_operation(repository)

        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(outcome),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )

        assert completion.operation.status is expected_status
        assert completion.event.payload["status"] == expected_status.value
        assert completion.event.payload["result"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(ProviderJobOutcome.POLLING),
        _snapshot(ProviderJobOutcome.PAUSED_QUOTA),
        _snapshot(provider_job_id="provider-job-other"),
    ],
    ids=["polling", "paused-quota", "provider-id-mismatch"],
)
async def test_non_terminal_or_mismatched_snapshot_leaves_no_half_state(
    kind: RepositoryKind,
    snapshot: ProviderJobSnapshot,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-{snapshot.outcome.value}.db",
    ) as repository:
        leased = await _leased_operation(repository)

        with pytest.raises(OperationCompletionConflictError):
            await _coordinator(repository).record_terminal(
                JOB_ID,
                snapshot,
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=1),
            )

        assert await repository.get_operation(OWNER, JOB_ID) == leased
        assert await repository.list_events(OWNER, CONVERSATION) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("user_id", "conversation_id", "lease_owner", "now"),
    [
        ("user-other", CONVERSATION, "poller-a", NOW + timedelta(seconds=1)),
        (OWNER, "conversation-other", "poller-a", NOW + timedelta(seconds=1)),
        (OWNER, CONVERSATION, "poller-other", NOW + timedelta(seconds=1)),
        (OWNER, CONVERSATION, "poller-a", NOW + timedelta(seconds=30)),
    ],
    ids=["owner", "conversation", "worker", "expired"],
)
async def test_terminal_record_requires_current_scoped_poll_lease(
    kind: RepositoryKind,
    user_id: str,
    conversation_id: str,
    lease_owner: str,
    now: datetime,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-{user_id}-{conversation_id}-{lease_owner}.db",
    ) as repository:
        leased = await _leased_operation(repository)

        with pytest.raises(OperationCompletionConflictError):
            await _coordinator(
                repository,
                user_id=user_id,
                conversation_id=conversation_id,
            ).record_terminal(
                JOB_ID,
                _snapshot(),
                lease_owner=lease_owner,
                now=now,
            )

        assert await repository.get_operation(OWNER, JOB_ID) == leased
        assert await repository.list_events(OWNER, CONVERSATION) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_conflicting_terminal_replay_keeps_original_event(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-terminal-conflict.db",
    ) as repository:
        await _leased_operation(repository)
        succeeded = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )

        with pytest.raises(OperationCompletionConflictError):
            await _coordinator(repository).record_terminal(
                JOB_ID,
                _snapshot(ProviderJobOutcome.FAILED),
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=2),
            )

        assert await repository.get_operation(OWNER, JOB_ID) == succeeded.operation
        assert await repository.list_events(OWNER, CONVERSATION) == [succeeded.event]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_event_identity_collision_rolls_back_terminal_update(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-event-collision.db",
    ) as repository:
        leased = await _leased_operation(repository)
        collision = AgentEvent(
            event_id=build_operation_completion_event_id(JOB_ID),
            sequence=1,
            cursor="cursor-collision",
            conversation_id=CONVERSATION,
            run_id="run-collision",
            occurred_at=NOW,
            type=AgentEventType.ERROR_RAISED,
            payload={"reason_code": "collision"},
        )
        await repository.create_event(OWNER, collision)

        with pytest.raises(OperationCompletionConflictError):
            await _coordinator(repository).record_terminal(
                JOB_ID,
                _snapshot(),
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=1),
            )

        assert await repository.get_operation(OWNER, JOB_ID) == leased
        assert await repository.list_events(OWNER, CONVERSATION) == [collision]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_completion_event_keeps_contiguous_sequence_during_other_append(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-sequence.db",
    ) as repository:
        await _leased_operation(repository)
        outbox = RepositoryCompactionEventOutbox(repository=repository)

        other_result, completion_result = await asyncio.gather(
            outbox.append(
                OWNER,
                conversation_id=CONVERSATION,
                run_id="run-other",
                event_type=AgentEventType.INPUT_STATE_CHANGED,
                payload={"turn_id": "turn-other", "status": "queued"},
                occurred_at=NOW + timedelta(seconds=1),
            ),
            _coordinator(repository).record_terminal(
                JOB_ID,
                _snapshot(),
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=1),
            ),
        )

        events = await repository.list_events(OWNER, CONVERSATION)
        assert [event.sequence for event in events] == [1, 2]
        assert {event.event_id for event in events} == {
            other_result.event_id,
            completion_result.event.event_id,
        }


@pytest.mark.asyncio
async def test_two_sql_engines_finalize_one_terminal_event(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(tmp_path / "sql-cross-engine-terminal.db") as (repository_a, repository_b):
        await _leased_operation(repository_a)

        results = await asyncio.gather(
            _coordinator(repository_a).record_terminal(
                JOB_ID,
                _snapshot(),
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=1),
            ),
            _coordinator(repository_b).record_terminal(
                JOB_ID,
                _snapshot(),
                lease_owner="poller-a",
                now=NOW + timedelta(seconds=1),
            ),
        )

        assert results[0] == results[1]
        assert len(await repository_a.list_events(OWNER, CONVERSATION)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_crash_before_graph_resume_recovers_persisted_completion_only(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-crash-before-resume.db",
    ) as repository:
        await _leased_operation(repository)
        unrelated = AgentEvent(
            event_id="event-unrelated",
            sequence=1,
            cursor="cursor-unrelated",
            conversation_id=CONVERSATION,
            run_id="run-unrelated",
            occurred_at=NOW,
            type=AgentEventType.INPUT_STATE_CHANGED,
            payload={"turn_id": "turn-unrelated", "status": "queued"},
        )
        await repository.create_event(OWNER, unrelated)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )

        # 这里模拟进程在完成事务后、调用 Workflow Graph 前退出。
        resumer = _CheckpointingGraphResumer()
        dispatcher = OperationCompletionDispatcher(
            repository,
            resumer=resumer,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW + timedelta(seconds=3),
        )
        resumed = await dispatcher.dispatch(
            JOB_ID,
            lease_owner="resume-worker-a",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=32),
        )
        replayed = await dispatcher.dispatch(
            JOB_ID,
            lease_owner="resume-worker-b",
            now=NOW + timedelta(seconds=3),
            lease_expires_at=NOW + timedelta(seconds=33),
        )

        assert resumed == completion.event
        assert replayed is None
        assert resumer.applied_count == 1
        namespace, event, idempotency_key = resumer.calls[0]
        assert namespace.thread_id == (f"pf:conversation:{CONVERSATION}:workflow:workflow-{JOB_ID}:v1")
        assert event == completion.event
        assert idempotency_key == completion.event.event_id
        assert (
            await repository.claim_next_event(
                OWNER,
                CONVERSATION,
                lease_owner="generic-event-worker",
                now=NOW + timedelta(seconds=3),
                lease_expires_at=NOW + timedelta(seconds=33),
            )
            is not None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_generic_outbox_claim_cannot_steal_workflow_completion(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-generic-claim.db",
    ) as repository:
        await _leased_operation(repository)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )

        generic_claim = await repository.claim_next_event(
            OWNER,
            CONVERSATION,
            lease_owner="generic-event-worker",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=32),
        )
        resumer = _CheckpointingGraphResumer()
        resumed = await OperationCompletionDispatcher(
            repository,
            resumer=resumer,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW + timedelta(seconds=3),
        ).dispatch(
            JOB_ID,
            lease_owner="resume-worker",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=32),
        )

        assert generic_claim is None
        assert resumed == completion.event
        assert resumer.applied_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_generic_outbox_claim_does_not_skip_completion_at_queue_head(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-generic-head-order.db",
    ) as repository:
        await _leased_operation(repository)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )
        ordinary_event = await RepositoryCompactionEventOutbox(repository=repository).append(
            OWNER,
            conversation_id=CONVERSATION,
            run_id="run-after-completion",
            event_type=AgentEventType.INPUT_STATE_CHANGED,
            payload={"turn_id": "turn-after-completion", "status": "queued"},
            occurred_at=NOW + timedelta(seconds=2),
        )

        blocked = await repository.claim_next_event(
            OWNER,
            CONVERSATION,
            lease_owner="generic-event-worker",
            now=NOW + timedelta(seconds=3),
            lease_expires_at=NOW + timedelta(seconds=33),
        )
        resumed = await OperationCompletionDispatcher(
            repository,
            resumer=_CheckpointingGraphResumer(),
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW + timedelta(seconds=4),
        ).dispatch(
            JOB_ID,
            lease_owner="resume-worker",
            now=NOW + timedelta(seconds=3),
            lease_expires_at=NOW + timedelta(seconds=33),
        )
        next_claim = await repository.claim_next_event(
            OWNER,
            CONVERSATION,
            lease_owner="generic-event-worker",
            now=NOW + timedelta(seconds=4),
            lease_expires_at=NOW + timedelta(seconds=34),
        )

        assert blocked is None
        assert resumed == completion.event
        assert next_claim is not None
        assert next_claim.event == ordinary_event


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_completion_result_and_graph_input_are_deeply_read_only(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-immutable-completion.db",
    ) as repository:
        await _leased_operation(repository)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )

        completion_json = completion.model_dump(mode="json")
        assert json.loads(completion.model_dump_json()) == completion_json
        assert completion_json["event"]["payload"]["result"]["artifact_refs"] == ["artifact-image-1"]

        with pytest.raises(ValidationError):
            completion.operation.status = ExternalJobStatus.FAILED
        with pytest.raises(ValidationError):
            completion.event.type = AgentEventType.ERROR_RAISED
        with pytest.raises(TypeError):
            completion.event.payload["status"] = ExternalJobStatus.FAILED.value
        with pytest.raises(AttributeError):
            completion.event.payload["result"]["artifact_refs"].append("artifact-image-2")

        class _MutationCheckingResumer:
            def __init__(self) -> None:
                self.checked = False

            async def resume_external_job(
                self,
                namespace: object,
                *,
                completion_event: AgentEvent,
                idempotency_key: str,
            ) -> None:
                del namespace, idempotency_key
                with pytest.raises(ValidationError):
                    completion_event.type = AgentEventType.ERROR_RAISED
                with pytest.raises(TypeError):
                    completion_event.payload["status"] = ExternalJobStatus.FAILED.value
                with pytest.raises(AttributeError):
                    completion_event.payload["result"]["artifact_refs"].append("artifact-image-2")
                self.checked = True

        resumer = _MutationCheckingResumer()
        resumed = await OperationCompletionDispatcher(
            repository,
            resumer=resumer,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW + timedelta(seconds=3),
        ).dispatch(
            JOB_ID,
            lease_owner="resume-worker",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=32),
        )

        assert resumed == completion.event
        assert resumer.checked is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_resume_claim_is_single_worker_and_replays_same_id_after_crash(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-resume-replay.db",
    ) as repository:
        await _leased_operation(repository)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )
        resumer = _CheckpointingGraphResumer(fail_after_first_checkpoint=True)
        clock = [NOW + timedelta(seconds=2)]
        dispatcher = OperationCompletionDispatcher(
            repository,
            resumer=resumer,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: clock[0],
        )

        with pytest.raises(
            OperationCompletionDispatchError,
            match="Workflow Graph 恢复失败",
        ):
            await dispatcher.dispatch(
                JOB_ID,
                lease_owner="resume-worker-a",
                now=NOW + timedelta(seconds=2),
                lease_expires_at=NOW + timedelta(seconds=12),
            )
        blocked = await dispatcher.dispatch(
            JOB_ID,
            lease_owner="resume-worker-b",
            now=NOW + timedelta(seconds=3),
            lease_expires_at=NOW + timedelta(seconds=13),
        )
        clock[0] = NOW + timedelta(seconds=13)
        recovered = await dispatcher.dispatch(
            JOB_ID,
            lease_owner="resume-worker-b",
            now=NOW + timedelta(seconds=12),
            lease_expires_at=NOW + timedelta(seconds=22),
        )

        assert blocked is None
        assert recovered == completion.event
        assert [call[2] for call in resumer.calls] == [
            completion.event.event_id,
            completion.event.event_id,
        ]
        assert resumer.applied_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_graph_resume_cannot_ack_an_expired_delivery_lease(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-resume-expired.db",
    ) as repository:
        await _leased_operation(repository)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )
        clock = [NOW + timedelta(seconds=2)]

        class _LeaseExpiringResumer(_CheckpointingGraphResumer):
            async def resume_external_job(
                self,
                namespace: object,
                *,
                completion_event: AgentEvent,
                idempotency_key: str,
            ) -> None:
                await super().resume_external_job(
                    namespace,
                    completion_event=completion_event,
                    idempotency_key=idempotency_key,
                )
                clock[0] = NOW + timedelta(seconds=12)

        resumer = _LeaseExpiringResumer()
        dispatcher = OperationCompletionDispatcher(
            repository,
            resumer=resumer,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: clock[0],
        )

        with pytest.raises(
            OperationCompletionConflictError,
            match="投递租约",
        ):
            await dispatcher.dispatch(
                JOB_ID,
                lease_owner="resume-worker-a",
                now=NOW + timedelta(seconds=2),
                lease_expires_at=NOW + timedelta(seconds=12),
            )
        recovered = await dispatcher.dispatch(
            JOB_ID,
            lease_owner="resume-worker-b",
            now=NOW + timedelta(seconds=12),
            lease_expires_at=NOW + timedelta(seconds=22),
        )

        assert recovered == completion.event
        assert [call[2] for call in resumer.calls] == [
            completion.event.event_id,
            completion.event.event_id,
        ]
        assert resumer.applied_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_concurrent_resume_dispatch_calls_graph_once(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(
        kind,
        tmp_path / f"{kind}-resume-race.db",
    ) as repository:
        await _leased_operation(repository)
        completion = await _coordinator(repository).record_terminal(
            JOB_ID,
            _snapshot(),
            lease_owner="poller-a",
            now=NOW + timedelta(seconds=1),
        )
        resumer = _CheckpointingGraphResumer()
        dispatcher = OperationCompletionDispatcher(
            repository,
            resumer=resumer,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            clock=lambda: NOW + timedelta(seconds=3),
        )

        results = await asyncio.gather(
            dispatcher.dispatch(
                JOB_ID,
                lease_owner="resume-worker-a",
                now=NOW + timedelta(seconds=2),
                lease_expires_at=NOW + timedelta(seconds=32),
            ),
            dispatcher.dispatch(
                JOB_ID,
                lease_owner="resume-worker-b",
                now=NOW + timedelta(seconds=2),
                lease_expires_at=NOW + timedelta(seconds=32),
            ),
        )

        assert [result for result in results if result is not None] == [completion.event]
        assert len(resumer.calls) == 1
