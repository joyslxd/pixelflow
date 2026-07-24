from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import TurnRecord, TurnStatus
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
)

RepositoryKind = Literal["memory", "sql"]
OWNER_A = "user-a"
OWNER_B = "user-b"
CONVERSATION_A = "conversation-a"
CONVERSATION_B = "conversation-b"
NOW = datetime(2026, 7, 24, 9, 15, tzinfo=UTC)


def _turn(
    turn_id: str,
    client_input_id: str,
    *,
    conversation_id: str = CONVERSATION_A,
    status: TurnStatus = TurnStatus.ACCEPTED,
    expected_context_version: int = 1,
) -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        conversation_id=conversation_id,
        client_input_id=UUID(client_input_id),
        status=status,
        target_workflow_id=None,
        decision=None,
        expected_context_version=expected_context_version,
        created_at=NOW,
    )


@asynccontextmanager
async def _repository(kind: RepositoryKind) -> AsyncIterator[AgentRuntimeRepository]:
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
async def test_enqueue_turn_reuses_same_client_input_without_duplicate(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first = _turn(
            "turn-first",
            "00000000-0000-0000-0000-000000000001",
        )
        retry = _turn(
            "turn-retry",
            "00000000-0000-0000-0000-000000000001",
            status=TurnStatus.QUEUED,
            expected_context_version=99,
        )

        created = await repository.enqueue_turn(OWNER_A, first)
        replayed = await repository.enqueue_turn(OWNER_A, retry)

        assert created == first
        assert replayed == first
        assert await repository.list_turns(OWNER_A, CONVERSATION_A) == [first]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_enqueue_turn_reuses_same_client_input_under_concurrency(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first, second = await asyncio.gather(
            repository.enqueue_turn(
                OWNER_A,
                _turn(
                    "turn-race-a",
                    "00000000-0000-0000-0000-000000000001",
                ),
            ),
            repository.enqueue_turn(
                OWNER_A,
                _turn(
                    "turn-race-b",
                    "00000000-0000-0000-0000-000000000001",
                ),
            ),
        )

        assert first == second
        assert first.turn_id in {"turn-race-a", "turn-race-b"}
        assert await repository.list_turns(
            OWNER_A,
            CONVERSATION_A,
        ) == [first]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_enqueue_turn_keeps_global_identity_and_owner_fail_closed(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        first = _turn(
            "turn-shared",
            "00000000-0000-0000-0000-000000000001",
        )
        await repository.enqueue_turn(OWNER_A, first)

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.enqueue_turn(
                OWNER_A,
                _turn(
                    "turn-shared",
                    "00000000-0000-0000-0000-000000000002",
                ),
            )

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.enqueue_turn(
                OWNER_B,
                _turn(
                    "turn-other-owner",
                    "00000000-0000-0000-0000-000000000001",
                ),
            )

        assert (
            await repository.get_turn_by_client_input_id(
                OWNER_B,
                CONVERSATION_A,
                first.client_input_id,
            )
            is None
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_claim_next_turn_uses_inbox_order_and_blocks_same_conversation(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        queued_first = _turn(
            "turn-queued-first",
            "00000000-0000-0000-0000-000000000001",
            status=TurnStatus.QUEUED,
        )
        accepted_second = _turn(
            "turn-accepted-second",
            "00000000-0000-0000-0000-000000000002",
        )
        await repository.enqueue_turn(OWNER_A, queued_first)
        await repository.enqueue_turn(OWNER_A, accepted_second)

        claimed = await repository.claim_next_turn(OWNER_A, CONVERSATION_A)
        blocked = await repository.claim_next_turn(OWNER_A, CONVERSATION_A)

        assert claimed is not None
        assert claimed.turn_id == queued_first.turn_id
        assert claimed.status is TurnStatus.PROCESSING
        assert blocked is None
        stored = await repository.list_turns(OWNER_A, CONVERSATION_A)
        assert [item.status for item in stored] == [
            TurnStatus.PROCESSING,
            TurnStatus.ACCEPTED,
        ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_claim_next_turn_skips_terminal_and_isolates_conversations(
    kind: RepositoryKind,
) -> None:
    async with _repository(kind) as repository:
        await repository.enqueue_turn(
            OWNER_A,
            _turn(
                "turn-completed",
                "00000000-0000-0000-0000-000000000001",
                status=TurnStatus.COMPLETED,
            ),
        )
        expected_a = _turn(
            "turn-a",
            "00000000-0000-0000-0000-000000000002",
        )
        expected_b = _turn(
            "turn-b",
            "00000000-0000-0000-0000-000000000003",
            conversation_id=CONVERSATION_B,
        )
        await repository.enqueue_turn(OWNER_A, expected_a)
        await repository.enqueue_turn(OWNER_A, expected_b)

        claimed_a, claimed_b = await asyncio.gather(
            repository.claim_next_turn(OWNER_A, CONVERSATION_A),
            repository.claim_next_turn(OWNER_A, CONVERSATION_B),
        )

        assert claimed_a is not None
        assert claimed_a.turn_id == expected_a.turn_id
        assert claimed_a.status is TurnStatus.PROCESSING
        assert claimed_b is not None
        assert claimed_b.turn_id == expected_b.turn_id
        assert claimed_b.status is TurnStatus.PROCESSING
        assert await repository.claim_next_turn(OWNER_B, CONVERSATION_A) is None


@pytest.mark.asyncio
async def test_sql_concurrent_enqueue_returns_one_persisted_turn(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(
        tmp_path / "turn-enqueue.db"
    ) as (repository_a, repository_b):
        first, second = await asyncio.gather(
            repository_a.enqueue_turn(
                OWNER_A,
                _turn(
                    "turn-race-a",
                    "00000000-0000-0000-0000-000000000001",
                ),
            ),
            repository_b.enqueue_turn(
                OWNER_A,
                _turn(
                    "turn-race-b",
                    "00000000-0000-0000-0000-000000000001",
                ),
            ),
        )

        assert first == second
        stored = await repository_a.list_turns(OWNER_A, CONVERSATION_A)
        assert stored == [first]


@pytest.mark.asyncio
async def test_sql_concurrent_claim_allows_one_processing_turn(
    tmp_path: Path,
) -> None:
    async with _file_sql_repositories(
        tmp_path / "turn-claim.db"
    ) as (repository_a, repository_b):
        await repository_a.enqueue_turn(
            OWNER_A,
            _turn(
                "turn-first",
                "00000000-0000-0000-0000-000000000001",
            ),
        )
        await repository_a.enqueue_turn(
            OWNER_A,
            _turn(
                "turn-second",
                "00000000-0000-0000-0000-000000000002",
            ),
        )

        claims = await asyncio.gather(
            repository_a.claim_next_turn(OWNER_A, CONVERSATION_A),
            repository_b.claim_next_turn(OWNER_A, CONVERSATION_A),
        )

        assert [claim.turn_id for claim in claims if claim is not None] == [
            "turn-first"
        ]
        stored = await repository_a.list_turns(OWNER_A, CONVERSATION_A)
        assert [item.status for item in stored] == [
            TurnStatus.PROCESSING,
            TurnStatus.ACCEPTED,
        ]
