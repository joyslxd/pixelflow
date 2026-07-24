"""M04.4 conversation 压缩租约与 Turn 队列恢复测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.context import (
    ContextCompactionRequest,
    ContextCompactionResult,
    ConversationCompactionRuntime,
)
from pixelflow.agent_runtime.contracts import ContextBudgetReport, TurnRecord, TurnStatus
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_TABLES,
    CompactionLeaseConflictError,
    CompactionQueueRepository,
    MemoryCompactionQueueRepository,
    SQLCompactionQueueRepository,
)
from pixelflow.agent_runtime.persistence.models import (
    PixelFlowAgentCompactionLockRow,
    PixelFlowAgentTurnRow,
)
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    SQLAgentRuntimeRepository,
)

RepositoryKind = Literal["memory", "sql"]
OWNER_A = "user-a"
OWNER_B = "user-b"
CONVERSATION_A = "conversation-a"
CONVERSATION_B = "conversation-b"
NOW = datetime(2026, 7, 25, 0, 30, tzinfo=UTC)


def _turn(
    index: int,
    *,
    conversation_id: str = CONVERSATION_A,
    status: TurnStatus = TurnStatus.ACCEPTED,
) -> TurnRecord:
    return TurnRecord(
        turn_id=f"turn-{conversation_id}-{index}",
        conversation_id=conversation_id,
        client_input_id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
        status=status,
        target_workflow_id=None,
        decision=None,
        expected_context_version=1,
        created_at=NOW + timedelta(seconds=index),
    )


def _budget(
    *,
    estimated_input_tokens: int,
    compaction_level: int,
) -> ContextBudgetReport:
    return ContextBudgetReport(
        estimated_input_tokens=estimated_input_tokens,
        effective_context_tokens=120,
        usable_input_tokens=100,
        max_output_tokens=10,
        safety_reserve_tokens=10,
        utilization=estimated_input_tokens / 100,
        compaction_level=compaction_level,
    )


def _request() -> ContextCompactionRequest:
    return ContextCompactionRequest(
        conversation_id=CONVERSATION_A,
        budget_report=_budget(
            estimated_input_tokens=10,
            compaction_level=0,
        ),
    )


def _completed_result() -> ContextCompactionResult:
    budget = _budget(
        estimated_input_tokens=10,
        compaction_level=0,
    )
    return ContextCompactionResult(
        status="not_required",
        initial_budget_report=budget,
        final_budget_report=budget,
        target_input_tokens=44,
        attempts=(),
        model_invocation_allowed=True,
    )


def _paused_result() -> ContextCompactionResult:
    budget = _budget(
        estimated_input_tokens=95,
        compaction_level=4,
    )
    return ContextCompactionResult(
        status="paused",
        initial_budget_report=budget,
        final_budget_report=budget,
        target_input_tokens=44,
        attempts=(),
        model_invocation_allowed=False,
        pause_reason="hard_gate_compaction_failed",
    )


@asynccontextmanager
async def _repository(
    kind: RepositoryKind,
) -> AsyncIterator[CompactionQueueRepository]:
    if kind == "memory":
        yield MemoryCompactionQueueRepository()
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
        yield SQLCompactionQueueRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


@asynccontextmanager
async def _file_sql_repositories(
    database_path: Path,
) -> AsyncIterator[tuple[SQLCompactionQueueRepository, SQLCompactionQueueRepository]]:
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
            SQLCompactionQueueRepository(async_sessionmaker(engine_a, expire_on_commit=False)),
            SQLCompactionQueueRepository(async_sessionmaker(engine_b, expire_on_commit=False)),
        )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


class _BlockingCoordinator:
    def __init__(
        self,
        result: ContextCompactionResult | Exception,
    ) -> None:
        self.result = result
        self.started = asyncio.Event()
        self.resume = asyncio.Event()
        self.requests: list[ContextCompactionRequest] = []

    async def coordinate(
        self,
        request: ContextCompactionRequest,
    ) -> ContextCompactionResult:
        self.requests.append(request)
        self.started.set()
        await self.resume.wait()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_compaction_lease_is_conversation_scoped_and_exclusive(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        replayed = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        blocked = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(minutes=6),
        )
        other_conversation = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_B,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(minutes=6),
        )

        assert first is not None
        assert replayed is None
        assert blocked is None
        assert other_conversation is not None
        assert other_conversation.conversation_id == CONVERSATION_B
        assert (
            await repository.get_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
            )
            == first
        )
        assert (
            await repository.get_compaction_lease(
                OWNER_B,
                CONVERSATION_A,
            )
            is None
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_acquire_queues_existing_accepted_turn_and_rejects_processing(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        accepted = await repository.enqueue_turn(
            OWNER_A,
            _turn(1),
        )
        lease = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

        assert lease is not None
        stored = await repository.get_turn(OWNER_A, accepted.turn_id)
        assert stored is not None
        assert stored.status is TurnStatus.QUEUED

    async with _repository(kind) as repository:
        await repository.enqueue_turn(
            OWNER_A,
            _turn(1, status=TurnStatus.PROCESSING),
        )

        assert (
            await repository.acquire_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(minutes=5),
            )
            is None
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_inputs_stay_queued_in_order_and_retry_does_not_duplicate(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        lease = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        assert lease is not None

        stored = [
            await repository.enqueue_turn_for_execution(
                OWNER_A,
                _turn(index),
                now=NOW + timedelta(seconds=index),
            )
            for index in (1, 2, 3)
        ]
        replayed = await repository.enqueue_turn_for_execution(
            OWNER_A,
            _turn(2),
            now=NOW + timedelta(seconds=4),
        )

        assert [turn.status for turn in stored] == [
            TurnStatus.QUEUED,
            TurnStatus.QUEUED,
            TurnStatus.QUEUED,
        ]
        assert replayed == stored[1]
        persisted = await repository.list_turns(
            OWNER_A,
            CONVERSATION_A,
        )
        assert [turn.turn_id for turn in persisted] == [turn.turn_id for turn in stored]
        assert await repository.claim_next_turn(OWNER_A, CONVERSATION_A) is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_success_releases_lease_and_claims_only_first_queued_turn(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        lease = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        assert lease is not None
        for index in (1, 2, 3):
            await repository.enqueue_turn_for_execution(
                OWNER_A,
                _turn(index),
                now=NOW + timedelta(seconds=index),
            )

        claimed = await repository.finish_compaction(
            OWNER_A,
            CONVERSATION_A,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            now=NOW + timedelta(minutes=1),
            claim_next=True,
        )

        assert claimed is not None
        assert claimed.turn_id == _turn(1).turn_id
        assert claimed.status is TurnStatus.PROCESSING
        assert (
            await repository.get_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
            )
            is None
        )
        assert [
            turn.status
            for turn in await repository.list_turns(
                OWNER_A,
                CONVERSATION_A,
            )
        ] == [
            TurnStatus.PROCESSING,
            TurnStatus.QUEUED,
            TurnStatus.QUEUED,
        ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_failed_compaction_keeps_queue_for_later_recovery(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first_lease = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        assert first_lease is not None
        for index in (1, 2, 3):
            await repository.enqueue_turn_for_execution(
                OWNER_A,
                _turn(index),
                now=NOW + timedelta(seconds=index),
            )

        assert (
            await repository.finish_compaction(
                OWNER_A,
                CONVERSATION_A,
                lease_owner=first_lease.lease_owner,
                lease_token=first_lease.lease_token,
                now=NOW + timedelta(minutes=1),
                claim_next=False,
            )
            is None
        )
        assert [
            turn.status
            for turn in await repository.list_turns(
                OWNER_A,
                CONVERSATION_A,
            )
        ] == [TurnStatus.QUEUED] * 3
        assert await repository.claim_next_turn(OWNER_A, CONVERSATION_A) is None

        recovered_lease = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-b",
            now=NOW + timedelta(minutes=2),
            lease_expires_at=NOW + timedelta(minutes=7),
        )
        assert recovered_lease is not None
        recovered = await repository.finish_compaction(
            OWNER_A,
            CONVERSATION_A,
            lease_owner=recovered_lease.lease_owner,
            lease_token=recovered_lease.lease_token,
            now=NOW + timedelta(minutes=3),
            claim_next=True,
        )

        assert recovered is not None
        assert recovered.turn_id == _turn(1).turn_id
        assert (
            len(
                await repository.list_turns(
                    OWNER_A,
                    CONVERSATION_A,
                )
            )
            == 3
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_expired_lease_can_be_taken_over_but_stale_worker_is_fenced(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        stale = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=1),
        )
        assert stale is not None
        await repository.enqueue_turn_for_execution(
            OWNER_A,
            _turn(1),
            now=NOW + timedelta(seconds=1),
        )

        active = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-b",
            now=NOW + timedelta(minutes=2),
            lease_expires_at=NOW + timedelta(minutes=7),
        )
        assert active is not None
        assert active.lease_token != stale.lease_token

        with pytest.raises(CompactionLeaseConflictError):
            await repository.finish_compaction(
                OWNER_A,
                CONVERSATION_A,
                lease_owner=stale.lease_owner,
                lease_token=stale.lease_token,
                now=NOW + timedelta(minutes=3),
                claim_next=True,
            )

        assert (
            await repository.get_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
            )
            == active
        )
        assert (
            await repository.get_turn(
                OWNER_A,
                _turn(1).turn_id,
            )
        ).status is TurnStatus.QUEUED


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_compaction_lease_validation_and_owner_conflict_fail_closed(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        with pytest.raises(ValueError):
            await repository.acquire_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW,
            )

        lease = await repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        assert lease is not None
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.acquire_compaction_lease(
                OWNER_B,
                CONVERSATION_A,
                lease_owner="worker-b",
                now=NOW + timedelta(seconds=1),
                lease_expires_at=NOW + timedelta(minutes=6),
            )
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.enqueue_turn_for_execution(
                OWNER_B,
                _turn(1),
                now=NOW + timedelta(seconds=1),
            )


@pytest.mark.asyncio
async def test_sql_concurrent_lease_acquisition_has_one_winner(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(tmp_path / "compaction-lock.db") as (repository_a, repository_b):
        leases = await asyncio.gather(
            repository_a.acquire_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
                lease_owner="worker-a",
                now=NOW,
                lease_expires_at=NOW + timedelta(minutes=5),
            ),
            repository_b.acquire_compaction_lease(
                OWNER_A,
                CONVERSATION_A,
                lease_owner="worker-b",
                now=NOW,
                lease_expires_at=NOW + timedelta(minutes=5),
            ),
        )

        assert len([lease for lease in leases if lease is not None]) == 1


@pytest.mark.asyncio
async def test_sql_queue_recovers_across_repository_instances_without_resend(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(tmp_path / "compaction-recovery.db") as (repository_a, repository_b):
        stale = await repository_a.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=1),
        )
        assert stale is not None
        for index in (1, 2, 3):
            await repository_a.enqueue_turn_for_execution(
                OWNER_A,
                _turn(index),
                now=NOW + timedelta(seconds=index),
            )

        recovered = await repository_b.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-b",
            now=NOW + timedelta(minutes=2),
            lease_expires_at=NOW + timedelta(minutes=7),
        )
        assert recovered is not None
        claimed = await repository_b.finish_compaction(
            OWNER_A,
            CONVERSATION_A,
            lease_owner=recovered.lease_owner,
            lease_token=recovered.lease_token,
            now=NOW + timedelta(minutes=3),
            claim_next=True,
        )

        assert claimed is not None
        assert claimed.turn_id == _turn(1).turn_id
        assert [
            turn.status
            for turn in await repository_b.list_turns(
                OWNER_A,
                CONVERSATION_A,
            )
        ] == [
            TurnStatus.PROCESSING,
            TurnStatus.QUEUED,
            TurnStatus.QUEUED,
        ]


@pytest.mark.asyncio
async def test_sql_common_repository_cannot_bypass_active_compaction(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'common-repository.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    compaction_repository = SQLCompactionQueueRepository(session_factory)
    common_repository = SQLAgentRuntimeRepository(session_factory)
    try:
        lease = await compaction_repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        assert lease is not None

        enqueued = await common_repository.enqueue_turn(
            OWNER_A,
            _turn(1),
        )
        created = await common_repository.create_turn(
            OWNER_A,
            _turn(2),
        )

        assert enqueued.status is TurnStatus.QUEUED
        assert created.status is TurnStatus.QUEUED
        assert (
            await common_repository.claim_next_turn(
                OWNER_A,
                CONVERSATION_A,
            )
            is None
        )
        assert (
            await compaction_repository.finish_compaction(
                OWNER_A,
                CONVERSATION_A,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                now=NOW + timedelta(minutes=1),
                claim_next=False,
            )
            is None
        )
        after_failure = await common_repository.enqueue_turn(
            OWNER_A,
            _turn(3),
        )

        assert after_failure.status is TurnStatus.QUEUED
        assert (
            await common_repository.claim_next_turn(
                OWNER_A,
                CONVERSATION_A,
            )
            is None
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_acquire_and_enqueue_share_stable_lock_across_instances(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(tmp_path / "acquire-enqueue-race.db") as (repository_a, repository_b):
        for index in range(1, 11):
            conversation = f"conversation-race-{index}"
            lease, _ = await asyncio.gather(
                repository_a.acquire_compaction_lease(
                    OWNER_A,
                    conversation,
                    lease_owner="worker-a",
                    now=NOW,
                    lease_expires_at=NOW + timedelta(minutes=5),
                ),
                repository_b.enqueue_turn_for_execution(
                    OWNER_A,
                    _turn(index, conversation_id=conversation),
                    now=NOW + timedelta(seconds=index),
                ),
            )

            assert lease is not None
            stored = await repository_b.list_turns(
                OWNER_A,
                conversation,
            )
            assert [turn.status for turn in stored] == [TurnStatus.QUEUED]
            assert (
                await repository_b.claim_next_turn(
                    OWNER_A,
                    conversation,
                )
                is None
            )


@pytest.mark.asyncio
async def test_sql_acquire_and_common_claim_never_both_win(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(tmp_path / "acquire-claim-race.db") as (repository_a, repository_b):
        common_repository = SQLAgentRuntimeRepository(repository_b._session_factory)
        for index in range(1, 11):
            conversation = f"conversation-claim-race-{index}"
            await common_repository.enqueue_turn(
                OWNER_A,
                _turn(index, conversation_id=conversation),
            )

            lease, claimed = await asyncio.gather(
                repository_a.acquire_compaction_lease(
                    OWNER_A,
                    conversation,
                    lease_owner="worker-a",
                    now=NOW,
                    lease_expires_at=NOW + timedelta(minutes=5),
                ),
                common_repository.claim_next_turn(
                    OWNER_A,
                    conversation,
                ),
            )

            assert not (lease is not None and claimed is not None)
            persisted = await repository_a.list_turns(
                OWNER_A,
                conversation,
            )
            if lease is not None:
                assert claimed is None
                assert persisted[0].status is TurnStatus.QUEUED
            else:
                assert claimed is not None
                assert persisted[0].status is TurnStatus.PROCESSING


@pytest.mark.asyncio
async def test_sql_common_turn_path_creates_stable_conversation_lock_root(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'stable-root.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    common_repository = SQLAgentRuntimeRepository(session_factory)
    try:
        stored = await common_repository.enqueue_turn(
            OWNER_A,
            _turn(1),
        )
        async with session_factory() as session:
            coordination_row = (await session.scalars(select(PixelFlowAgentCompactionLockRow).where(PixelFlowAgentCompactionLockRow.conversation_id == CONVERSATION_A))).one_or_none()

        assert stored.status is TurnStatus.ACCEPTED
        assert coordination_row is not None
        assert coordination_row.user_id == OWNER_A
        assert coordination_row.state == "idle"
        assert coordination_row.lease_owner is None
        assert coordination_row.lease_token is None
        assert coordination_row.lease_expires_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_empty_claim_cannot_take_conversation_ownership(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'empty-claim.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    common_repository = SQLAgentRuntimeRepository(session_factory)
    compaction_repository = SQLCompactionQueueRepository(session_factory)
    try:
        assert (
            await common_repository.claim_next_turn(
                OWNER_B,
                CONVERSATION_A,
            )
            is None
        )
        lease = await compaction_repository.acquire_compaction_lease(
            OWNER_A,
            CONVERSATION_A,
            lease_owner="worker-a",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

        assert lease is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_legacy_turn_owner_cannot_be_replaced_when_root_is_created(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'legacy-owner.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=AGENT_RUNTIME_TABLES,
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    common_repository = SQLAgentRuntimeRepository(session_factory)
    legacy_turn = _turn(1)
    async with session_factory() as session:
        session.add(
            PixelFlowAgentTurnRow(
                turn_id=legacy_turn.turn_id,
                conversation_id=legacy_turn.conversation_id,
                user_id=OWNER_A,
                client_input_id=str(legacy_turn.client_input_id),
                status=legacy_turn.status.value,
                target_workflow_id=None,
                decision_json=None,
                expected_context_version=(legacy_turn.expected_context_version),
                created_at=legacy_turn.created_at,
                updated_at=legacy_turn.created_at,
            )
        )
        await session.commit()
    try:
        with pytest.raises(AgentRuntimeRecordConflictError):
            await common_repository.enqueue_turn(
                OWNER_B,
                _turn(2),
            )
        with pytest.raises(AgentRuntimeRecordConflictError):
            await common_repository.create_turn(
                OWNER_B,
                _turn(3),
            )

        claimed = await common_repository.claim_next_turn(
            OWNER_A,
            CONVERSATION_A,
        )
        assert claimed is not None
        assert claimed.turn_id == legacy_turn.turn_id
        assert claimed.status is TurnStatus.PROCESSING
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_accepts_inputs_during_compaction_and_claims_in_order() -> None:
    coordinator = _BlockingCoordinator(_completed_result())
    repository = MemoryCompactionQueueRepository()
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )

    compaction_task = asyncio.create_task(runtime.compact(OWNER_A, _request()))
    await coordinator.started.wait()
    queued = [await runtime.enqueue_turn(OWNER_A, _turn(index)) for index in (1, 2, 3)]
    coordinator.resume.set()
    result = await compaction_task

    assert [turn.status for turn in queued] == [TurnStatus.QUEUED] * 3
    assert result.status == "completed"
    assert result.compaction_result == _completed_result()
    assert result.next_turn is not None
    assert result.next_turn.turn_id == _turn(1).turn_id
    assert len(coordinator.requests) == 1


@pytest.mark.asyncio
async def test_runtime_rejects_parallel_compaction_even_for_same_worker() -> None:
    coordinator = _BlockingCoordinator(_completed_result())
    repository = MemoryCompactionQueueRepository()
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )

    first_task = asyncio.create_task(runtime.compact(OWNER_A, _request()))
    await coordinator.started.wait()
    parallel = await runtime.compact(OWNER_A, _request())
    coordinator.resume.set()
    completed = await first_task

    assert parallel.status == "already_running"
    assert completed.status == "completed"
    assert len(coordinator.requests) == 1


@pytest.mark.parametrize(
    ("coordinator_result", "expected_status"),
    [
        (_paused_result(), "paused"),
        (RuntimeError("摘要服务暂时不可用"), "failed"),
    ],
)
@pytest.mark.asyncio
async def test_runtime_failure_or_pause_preserves_inputs_without_resend(
    coordinator_result: ContextCompactionResult | Exception,
    expected_status: str,
) -> None:
    coordinator = _BlockingCoordinator(coordinator_result)
    repository = MemoryCompactionQueueRepository()
    runtime = ConversationCompactionRuntime(
        coordinator=coordinator,
        repository=repository,
        lease_owner="worker-a",
        lease_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )

    compaction_task = asyncio.create_task(runtime.compact(OWNER_A, _request()))
    await coordinator.started.wait()
    first = await runtime.enqueue_turn(OWNER_A, _turn(1))
    replayed = await runtime.enqueue_turn(OWNER_A, _turn(1))
    coordinator.resume.set()

    if expected_status == "failed":
        with pytest.raises(RuntimeError, match="摘要服务暂时不可用"):
            await compaction_task
    else:
        result = await compaction_task
        assert result.status == expected_status
        assert result.next_turn is None

    assert first == replayed
    assert first.status is TurnStatus.QUEUED
    recovery_marker = await repository.get_compaction_lease(
        OWNER_A,
        CONVERSATION_A,
    )
    assert recovery_marker is not None
    assert recovery_marker.lease_expires_at <= NOW
    queued_after_failure = await runtime.enqueue_turn(
        OWNER_A,
        _turn(2),
    )
    assert queued_after_failure.status is TurnStatus.QUEUED
    assert await repository.claim_next_turn(OWNER_A, CONVERSATION_A) is None
    persisted = await repository.list_turns(
        OWNER_A,
        CONVERSATION_A,
    )
    assert len(persisted) == 2
    assert persisted[0].status is TurnStatus.QUEUED
