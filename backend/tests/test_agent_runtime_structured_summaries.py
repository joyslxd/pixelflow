from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.context.summaries import (
    StructuredSummaryRepository,
    SummaryEvidenceSnapshot,
    SummaryEvidenceValidationError,
    SummaryMessageEvidence,
    SummaryVersionConflictError,
)
from pixelflow.agent_runtime.contracts import ContextSummary
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_TABLES,
    AgentRuntimeRepository,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
)

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
RepositoryKind = Literal["memory", "sql"]
OWNER = "user-1"
CONVERSATION_ID = "conv-1"


def _summary_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary_id": "summary-1",
        "conversation_id": "conv-1",
        "version": 1,
        "previous_summary_id": None,
        "content_hash": "sha256:summary-1",
        "user_goals": ["生成商品图"],
        "confirmed_decisions": ["使用 1:1"],
        "negative_constraints": ["不要水印"],
        "workflow_states": {"wf-1": "running"},
        "unresolved_questions": ["是否需要透明背景"],
        "artifact_evidence_refs": ["artifact-1"],
        "covered_message_ids": ["message-1", "message-2"],
        "covered_sequence_start": 1,
        "covered_sequence_end": 2,
        "compression_model": "fake-summary-model",
        "created_at": NOW,
    }
    payload.update(updates)
    return payload


def _summary(
    summary_id: str,
    *,
    version: int,
    previous_summary_id: str | None,
    covered_message_ids: list[str],
    covered_sequence_start: int | None,
    covered_sequence_end: int | None,
    artifact_evidence_refs: list[str] | None = None,
) -> ContextSummary:
    return ContextSummary.model_validate(
        _summary_payload(
            summary_id=summary_id,
            version=version,
            previous_summary_id=previous_summary_id,
            content_hash=f"sha256:{summary_id}",
            covered_message_ids=covered_message_ids,
            covered_sequence_start=covered_sequence_start,
            covered_sequence_end=covered_sequence_end,
            artifact_evidence_refs=artifact_evidence_refs or ["artifact-1"],
        )
    )


def _evidence_snapshot(
    *,
    user_id: str = OWNER,
    conversation_id: str = CONVERSATION_ID,
    artifact_refs: tuple[str, ...] = ("artifact-1", "artifact-2"),
) -> SummaryEvidenceSnapshot:
    return SummaryEvidenceSnapshot(
        user_id=user_id,
        conversation_id=conversation_id,
        messages=(
            SummaryMessageEvidence(message_id="message-1", sequence=1),
            SummaryMessageEvidence(message_id="message-2", sequence=2),
            SummaryMessageEvidence(message_id="message-3", sequence=3),
        ),
        artifact_refs=artifact_refs,
    )


class _EvidenceSource:
    def __init__(
        self,
        snapshot: SummaryEvidenceSnapshot,
        *,
        on_load: Any = None,
    ) -> None:
        self.snapshot = snapshot
        self.on_load = on_load
        self.calls: list[tuple[str, str]] = []

    async def load_summary_evidence(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> SummaryEvidenceSnapshot:
        self.calls.append((user_id, conversation_id))
        if self.on_load is not None:
            self.on_load()
        return self.snapshot


@asynccontextmanager
async def _base_repository(
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
        yield SQLAgentRuntimeRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def test_context_summary_schema_round_trips_a_valid_version_chain_entry() -> None:
    summary = ContextSummary.model_validate(
        _summary_payload(
            summary_id="summary-2",
            version=2,
            previous_summary_id="summary-1",
        )
    )

    assert ContextSummary.model_validate_json(summary.model_dump_json()) == summary


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"version": 2, "previous_summary_id": None}, "第二版及后续摘要必须声明前一版"),
        ({"version": 1, "previous_summary_id": "summary-0"}, "第一版摘要不能声明前一版"),
        ({"previous_summary_id": "summary-1"}, "摘要不能把自己声明为前一版"),
        ({"covered_sequence_start": None}, "覆盖范围起止必须同时存在"),
        ({"covered_sequence_end": None}, "覆盖范围起止必须同时存在"),
        (
            {
                "covered_message_ids": ["message-2", "message-1"],
                "covered_sequence_start": 2,
                "covered_sequence_end": 1,
            },
            "覆盖范围起点不能大于终点",
        ),
        (
            {
                "covered_message_ids": ["message-2", "message-3"],
                "covered_sequence_start": 2,
                "covered_sequence_end": 3,
            },
            "覆盖范围必须从 sequence 1 开始",
        ),
        ({"covered_message_ids": ["message-1"]}, "覆盖范围必须与消息 ID 数量一致"),
        (
            {
                "covered_message_ids": ["message-1"],
                "covered_sequence_start": None,
                "covered_sequence_end": None,
            },
            "没有覆盖范围时不能声明消息 ID",
        ),
        (
            {"covered_message_ids": ["message-1", "message-1"]},
            "covered_message_ids 不能重复",
        ),
        (
            {"artifact_evidence_refs": ["artifact-1", "artifact-1"]},
            "artifact_evidence_refs 不能重复",
        ),
        (
            {"artifact_evidence_refs": [" "]},
            "artifact_evidence_refs 不能包含空引用",
        ),
    ],
)
def test_context_summary_schema_rejects_invalid_chain_coverage_or_evidence(
    updates: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ContextSummary.model_validate(_summary_payload(**updates))


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_round_trips_a_verified_chain(
    kind: RepositoryKind,
) -> None:
    source = _EvidenceSource(_evidence_snapshot())
    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=source,
        )
        first = _summary(
            "summary-1",
            version=1,
            previous_summary_id=None,
            covered_message_ids=["message-1", "message-2"],
            covered_sequence_start=1,
            covered_sequence_end=2,
        )
        second = _summary(
            "summary-2",
            version=2,
            previous_summary_id="summary-1",
            covered_message_ids=["message-1", "message-2", "message-3"],
            covered_sequence_start=1,
            covered_sequence_end=3,
            artifact_evidence_refs=["artifact-1", "artifact-2"],
        )

        assert await repository.save(OWNER, first) == first
        assert await repository.save(OWNER, second) == second
        assert await repository.get(OWNER, "summary-2") == second
        assert await repository.list(OWNER, CONVERSATION_ID) == [first, second]
        assert await repository.get("user-2", "summary-2") is None
        assert await repository.list("user-2", CONVERSATION_ID) == []

    assert source.calls == [
        (OWNER, CONVERSATION_ID),
        (OWNER, CONVERSATION_ID),
    ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_rejects_non_contiguous_versions(
    kind: RepositoryKind,
) -> None:
    source = _EvidenceSource(_evidence_snapshot())
    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=source,
        )
        with pytest.raises(
            SummaryVersionConflictError,
            match="第一条摘要必须是版本 1",
        ):
            await repository.save(
                OWNER,
                _summary(
                    "summary-2",
                    version=2,
                    previous_summary_id="summary-1",
                    covered_message_ids=["message-1", "message-2"],
                    covered_sequence_start=1,
                    covered_sequence_end=2,
                ),
            )

        first = _summary(
            "summary-1",
            version=1,
            previous_summary_id=None,
            covered_message_ids=["message-1", "message-2"],
            covered_sequence_start=1,
            covered_sequence_end=2,
        )
        await repository.save(OWNER, first)

        invalid_versions = [
            (
                _summary(
                    "summary-3",
                    version=3,
                    previous_summary_id="summary-1",
                    covered_message_ids=["message-1", "message-2", "message-3"],
                    covered_sequence_start=1,
                    covered_sequence_end=3,
                ),
                "摘要版本必须连续",
            ),
            (
                _summary(
                    "summary-2-wrong-parent",
                    version=2,
                    previous_summary_id="summary-other",
                    covered_message_ids=["message-1", "message-2", "message-3"],
                    covered_sequence_start=1,
                    covered_sequence_end=3,
                ),
                "previous_summary_id 必须指向最新摘要",
            ),
            (
                _summary(
                    "summary-2-regressed",
                    version=2,
                    previous_summary_id="summary-1",
                    covered_message_ids=["message-1"],
                    covered_sequence_start=1,
                    covered_sequence_end=1,
                ),
                "消息覆盖范围不能回退",
            ),
        ]
        for invalid, message in invalid_versions:
            with pytest.raises(SummaryVersionConflictError, match=message):
                await repository.save(OWNER, invalid)

        assert await repository.list(OWNER, CONVERSATION_ID) == [first]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_rejects_invalid_evidence(
    kind: RepositoryKind,
) -> None:
    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=_EvidenceSource(_evidence_snapshot()),
        )
        invalid_evidence = [
            (
                _summary(
                    "summary-missing-message",
                    version=1,
                    previous_summary_id=None,
                    covered_message_ids=["message-1", "message-missing"],
                    covered_sequence_start=1,
                    covered_sequence_end=2,
                ),
                "消息证据必须按 sequence 完整匹配覆盖范围",
            ),
            (
                _summary(
                    "summary-reordered-message",
                    version=1,
                    previous_summary_id=None,
                    covered_message_ids=["message-2", "message-1"],
                    covered_sequence_start=1,
                    covered_sequence_end=2,
                ),
                "消息证据必须按 sequence 完整匹配覆盖范围",
            ),
            (
                _summary(
                    "summary-missing-artifact",
                    version=1,
                    previous_summary_id=None,
                    covered_message_ids=["message-1", "message-2"],
                    covered_sequence_start=1,
                    covered_sequence_end=2,
                    artifact_evidence_refs=["artifact-missing"],
                ),
                "Artifact 证据不存在",
            ),
        ]
        for invalid, message in invalid_evidence:
            with pytest.raises(SummaryEvidenceValidationError, match=message):
                await repository.save(OWNER, invalid)

        assert await repository.list(OWNER, CONVERSATION_ID) == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_rejects_non_prefix_coverage(
    kind: RepositoryKind,
) -> None:
    summary = _summary(
        "summary-1",
        version=1,
        previous_summary_id=None,
        covered_message_ids=["message-1", "message-2"],
        covered_sequence_start=1,
        covered_sequence_end=2,
    ).model_copy(
        update={
            "covered_message_ids": ["message-2", "message-3"],
            "covered_sequence_start": 2,
            "covered_sequence_end": 3,
        }
    )

    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=_EvidenceSource(_evidence_snapshot()),
        )
        with pytest.raises(
            ValidationError,
            match="覆盖范围必须从 sequence 1 开始",
        ):
            await repository.save(OWNER, summary)

        assert await repository.list(OWNER, CONVERSATION_ID) == []


@pytest.mark.parametrize(
    "snapshot",
    [
        _evidence_snapshot(user_id="user-2"),
        _evidence_snapshot(conversation_id="conv-2"),
    ],
)
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_rejects_foreign_evidence_snapshots(
    kind: RepositoryKind,
    snapshot: SummaryEvidenceSnapshot,
) -> None:
    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=_EvidenceSource(snapshot),
        )
        with pytest.raises(
            SummaryEvidenceValidationError,
            match="证据快照不属于当前用户和会话",
        ):
            await repository.save(
                OWNER,
                _summary(
                    "summary-1",
                    version=1,
                    previous_summary_id=None,
                    covered_message_ids=["message-1", "message-2"],
                    covered_sequence_start=1,
                    covered_sequence_end=2,
                ),
            )
        assert await repository.list(OWNER, CONVERSATION_ID) == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_freezes_input_before_await(
    kind: RepositoryKind,
) -> None:
    summary = _summary(
        "summary-1",
        version=1,
        previous_summary_id=None,
        covered_message_ids=["message-1", "message-2"],
        covered_sequence_start=1,
        covered_sequence_end=2,
    )

    def mutate_input() -> None:
        summary.user_goals[0] = "等待期间被改写"
        summary.artifact_evidence_refs[0] = "artifact-missing"

    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=_EvidenceSource(
                _evidence_snapshot(),
                on_load=mutate_input,
            ),
        )
        stored = await repository.save(OWNER, summary)

        assert stored.user_goals == ["生成商品图"]
        assert stored.artifact_evidence_refs == ["artifact-1"]
        assert await repository.get(OWNER, "summary-1") == stored


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_structured_summary_repository_never_mutates_source_evidence(
    kind: RepositoryKind,
) -> None:
    snapshot = _evidence_snapshot()
    original_snapshot = snapshot.model_dump(mode="python")

    async with _base_repository(kind) as base_repository:
        repository = StructuredSummaryRepository(
            repository=base_repository,
            evidence_source=_EvidenceSource(snapshot),
        )
        await repository.save(
            OWNER,
            _summary(
                "summary-1",
                version=1,
                previous_summary_id=None,
                covered_message_ids=["message-1", "message-2"],
                covered_sequence_start=1,
                covered_sequence_end=2,
            ),
        )
        assert snapshot.model_dump(mode="python") == original_snapshot

        with pytest.raises(
            SummaryEvidenceValidationError,
            match="Artifact 证据不存在",
        ):
            await repository.save(
                OWNER,
                _summary(
                    "summary-2",
                    version=2,
                    previous_summary_id="summary-1",
                    covered_message_ids=["message-1", "message-2", "message-3"],
                    covered_sequence_start=1,
                    covered_sequence_end=3,
                    artifact_evidence_refs=["artifact-missing"],
                ),
            )
        assert snapshot.model_dump(mode="python") == original_snapshot
