from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import ExternalJobStatus, OperationRequest
from pixelflow.agent_runtime.jobs import (
    OPERATION_STATE_TRANSITIONS,
    OperationCoordinator,
    OperationStateConflictError,
    build_operation_idempotency_key,
    build_operation_request,
    ensure_operation_transition,
    hash_operation_request,
)
from pixelflow.agent_runtime.persistence import AGENT_RUNTIME_TABLES
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
)
from pixelflow.agent_runtime.ports import OperationConflictError

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 7, 28, 6, 30, tzinfo=UTC)
OWNER = "user-operation"
CONVERSATION = "conversation-operation"


@asynccontextmanager
async def _repository(
    kind: RepositoryKind,
    database_path: Path,
) -> AsyncIterator[AgentRuntimeRepository]:
    if kind == "memory":
        yield MemoryAgentRuntimeRepository()
        return

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
    try:
        yield SQLAgentRuntimeRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def _request(
    *,
    payload: object | None = None,
    workflow_id: str = "workflow-image",
    stage: str = "image_generate",
    stage_version: int = 2,
    attempt: int = 1,
) -> OperationRequest:
    return build_operation_request(
        workflow_id=workflow_id,
        stage=stage,
        stage_version=stage_version,
        attempt=attempt,
        provider_request=payload
        if payload is not None
        else {
            "model": "image-model",
            "prompt": "生成一张商品主图",
            "ratio": "1:1",
        },
    )


def test_request_hash_uses_canonical_json_and_detects_changes() -> None:
    first = hash_operation_request(
        {
            "prompt": "生成一张商品主图",
            "params": {"ratio": "1:1", "count": 2},
        }
    )
    reordered = hash_operation_request(
        {
            "params": {"count": 2, "ratio": "1:1"},
            "prompt": "生成一张商品主图",
        }
    )
    changed = hash_operation_request(
        {
            "prompt": "生成一张商品主图",
            "params": {"ratio": "16:9", "count": 2},
        }
    )

    assert first == reordered
    assert first != changed
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


@pytest.mark.parametrize(
    "payload",
    [
        {"unsupported": {1, 2}},
        {"invalid": float("nan")},
        {"invalid": float("inf")},
        {"invalid": float("-inf")},
    ],
)
def test_request_hash_rejects_values_outside_json_contract(payload: object) -> None:
    with pytest.raises(ValueError, match="JSON"):
        hash_operation_request(payload)


def test_operation_idempotency_key_contains_all_identity_fields() -> None:
    base = build_operation_idempotency_key("workflow-image", "image_generate", 2, 1)

    assert base.startswith("operation:v1:sha256:")
    assert build_operation_idempotency_key("workflow-video", "image_generate", 2, 1) != base
    assert build_operation_idempotency_key("workflow-image", "video_generate", 2, 1) != base
    assert build_operation_idempotency_key("workflow-image", "image_generate", 3, 1) != base
    assert build_operation_idempotency_key("workflow-image", "image_generate", 2, 2) != base
    assert build_operation_idempotency_key("workflow:part", "stage", 2, 1) != build_operation_idempotency_key("workflow", "part:stage", 2, 1)


@pytest.mark.parametrize(
    ("workflow_id", "stage", "stage_version", "attempt"),
    [
        ("", "image_generate", 1, 1),
        ("workflow-image", " ", 1, 1),
        ("workflow-image", "image_generate", 0, 1),
        ("workflow-image", "image_generate", 1, 0),
        ("workflow-image", "image_generate", True, 1),
        ("workflow-image", "image_generate", 1, False),
    ],
)
def test_operation_idempotency_key_rejects_invalid_identity(
    workflow_id: str,
    stage: str,
    stage_version: int,
    attempt: int,
) -> None:
    with pytest.raises(ValueError):
        build_operation_idempotency_key(
            workflow_id,
            stage,
            stage_version,
            attempt,
        )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExternalJobStatus.CREATED, ExternalJobStatus.CREATED),
        (ExternalJobStatus.CREATED, ExternalJobStatus.POLLING),
        (ExternalJobStatus.CREATED, ExternalJobStatus.SUCCEEDED),
        (ExternalJobStatus.CREATED, ExternalJobStatus.FAILED),
        (ExternalJobStatus.CREATED, ExternalJobStatus.TIMEOUT),
        (ExternalJobStatus.CREATED, ExternalJobStatus.EXPIRED),
        (ExternalJobStatus.POLLING, ExternalJobStatus.POLLING),
        (ExternalJobStatus.POLLING, ExternalJobStatus.SUCCEEDED),
        (ExternalJobStatus.POLLING, ExternalJobStatus.FAILED),
        (ExternalJobStatus.POLLING, ExternalJobStatus.TIMEOUT),
        (ExternalJobStatus.POLLING, ExternalJobStatus.EXPIRED),
        (ExternalJobStatus.SUCCEEDED, ExternalJobStatus.SUCCEEDED),
        (ExternalJobStatus.FAILED, ExternalJobStatus.FAILED),
        (ExternalJobStatus.TIMEOUT, ExternalJobStatus.TIMEOUT),
        (ExternalJobStatus.EXPIRED, ExternalJobStatus.EXPIRED),
    ],
)
def test_operation_state_machine_accepts_defined_transitions(
    current: ExternalJobStatus,
    target: ExternalJobStatus,
) -> None:
    ensure_operation_transition(current, target)


def test_operation_state_machine_covers_every_frozen_status() -> None:
    assert set(OPERATION_STATE_TRANSITIONS) == set(ExternalJobStatus)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExternalJobStatus.POLLING, ExternalJobStatus.CREATED),
        (ExternalJobStatus.SUCCEEDED, ExternalJobStatus.CREATED),
        (ExternalJobStatus.SUCCEEDED, ExternalJobStatus.POLLING),
        (ExternalJobStatus.SUCCEEDED, ExternalJobStatus.FAILED),
        (ExternalJobStatus.FAILED, ExternalJobStatus.CREATED),
        (ExternalJobStatus.FAILED, ExternalJobStatus.SUCCEEDED),
        (ExternalJobStatus.TIMEOUT, ExternalJobStatus.POLLING),
        (ExternalJobStatus.TIMEOUT, ExternalJobStatus.SUCCEEDED),
        (ExternalJobStatus.EXPIRED, ExternalJobStatus.POLLING),
        (ExternalJobStatus.EXPIRED, ExternalJobStatus.FAILED),
    ],
)
def test_operation_state_machine_rejects_reopen_and_terminal_switch(
    current: ExternalJobStatus,
    target: ExternalJobStatus,
) -> None:
    with pytest.raises(OperationStateConflictError):
        ensure_operation_transition(current, target)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_repeated_claim_returns_one_persisted_operation(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(kind, tmp_path / f"{kind}-repeated.db") as repository:
        coordinator = OperationCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            now=lambda: NOW,
        )
        request = _request(
            payload={
                "prompt": "生成一张商品主图",
                "Authorization": "Bearer should-never-be-persisted",
            }
        )

        first = await coordinator.claim(request)
        replayed = await coordinator.claim(request)
        stored = await repository.list_operations(OWNER, CONVERSATION)

        assert replayed == first
        assert first.status is ExternalJobStatus.CREATED
        assert first.provider_job_id is None
        assert first.next_poll_at is None
        assert first.lease_owner is None
        assert first.lease_expires_at is None
        assert stored == [first]
        serialized = json.dumps(stored[0].model_dump(mode="json"), ensure_ascii=False)
        assert "should-never-be-persisted" not in serialized
        assert request.request_hash in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_concurrent_repeated_claim_returns_winning_job(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(kind, tmp_path / f"{kind}-concurrent.db") as repository:
        first_coordinator = OperationCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            now=lambda: NOW,
        )
        second_coordinator = OperationCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            now=lambda: NOW,
        )
        request = _request()

        first, second = await asyncio.gather(
            first_coordinator.claim(request),
            second_coordinator.claim(request),
        )

        assert first == second
        assert len(await repository.list_operations(OWNER, CONVERSATION)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_claim_rejects_same_key_with_different_request_or_scope(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(kind, tmp_path / f"{kind}-conflict.db") as repository:
        coordinator = OperationCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            now=lambda: NOW,
        )
        request = _request()
        await coordinator.claim(request)

        with pytest.raises(OperationConflictError):
            await coordinator.claim(request.model_copy(update={"request_hash": hash_operation_request({"prompt": "生成另一张商品主图"})}))

        for update in (
            {"workflow_id": "workflow-other"},
            {"stage": "video_generate"},
            {"stage_version": 3},
            {"attempt": 2},
        ):
            with pytest.raises(OperationConflictError):
                await coordinator.claim(request.model_copy(update=update))

        other_conversation = OperationCoordinator(
            repository,
            user_id=OWNER,
            conversation_id="conversation-other",
            now=lambda: NOW,
        )
        with pytest.raises(OperationConflictError):
            await other_conversation.claim(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_claim_does_not_reveal_or_reuse_other_owner_operation(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    async with _repository(kind, tmp_path / f"{kind}-owner.db") as repository:
        request = _request()
        owner_coordinator = OperationCoordinator(
            repository,
            user_id=OWNER,
            conversation_id=CONVERSATION,
            now=lambda: NOW,
        )
        created = await owner_coordinator.claim(request)
        other_owner = OperationCoordinator(
            repository,
            user_id="user-other",
            conversation_id=CONVERSATION,
            now=lambda: NOW,
        )

        assert (
            await repository.get_operation_by_idempotency_key(
                "user-other",
                request.idempotency_key,
            )
            is None
        )
        with pytest.raises(OperationConflictError):
            await other_owner.claim(request)
        assert await repository.get_operation(OWNER, created.job_id) == created
