"""Agent Runtime ContextEnvelope 组装器测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.context.profiles import ModelContextProfile
from pixelflow.agent_runtime.contracts import (
    ContextRequest,
    ContextSummary,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)

NOW = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


class _SnapshotSource:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, str]] = []

    async def load_context_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> object:
        self.calls.append((user_id, conversation_id))
        return self.snapshot


class _MemorySearch:
    def __init__(
        self,
        items: list[dict[str, object]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.items = items or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.items


def _workflow(
    workflow_id: str,
    *,
    kind: WorkflowKind,
    updated_offset: int,
    artifacts: list[str] | None = None,
    conversation_id: str = "conv-1",
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        kind=kind,
        status=WorkflowStatus.RUNNING,
        current_stage="review",
        stage_version=1,
        creation_contract_snapshot={"goal": workflow_id},
        latest_artifact_refs=artifacts or [],
        context_version=7,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW + timedelta(minutes=updated_offset),
    )


def _summary(
    summary_id: str,
    *,
    version: int,
    unresolved: list[str] | None = None,
    artifacts: list[str] | None = None,
    covered_end: int | None = None,
    conversation_id: str = "conv-1",
) -> ContextSummary:
    return ContextSummary(
        summary_id=summary_id,
        conversation_id=conversation_id,
        version=version,
        content_hash=f"sha256:{summary_id}",
        user_goals=["制作商品素材"],
        unresolved_questions=unresolved or [],
        artifact_evidence_refs=artifacts or [],
        covered_message_ids=[],
        covered_sequence_start=1 if covered_end is not None else None,
        covered_sequence_end=covered_end,
        compression_model="summary-model",
        created_at=NOW + timedelta(minutes=version),
    )


def _unverified_profile() -> ModelContextProfile:
    return ModelContextProfile(
        model_name="large-but-unverified",
        max_context_tokens=512 * 1024,
        max_output_tokens=64 * 1024,
        tokenizer_strategy="provider_usage",
    )


def _request(**updates: object) -> ContextRequest:
    payload: dict[str, object] = {
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "current_input": "  把第三张图改成白色背景，保留这些空格  ",
        "target_workflow_id": "wf-image",
        "artifact_refs": ["artifact:explicit", "artifact:explicit"],
        "expected_context_version": 7,
    }
    payload.update(updates)
    return ContextRequest.model_validate(payload)


def _assembler(
    snapshot: object,
    *,
    memory_search: _MemorySearch | None = None,
    recent_message_limit: int = 2,
    estimated_tokens: int = 18_000,
):
    from pixelflow.agent_runtime.context.assembler import ContextAssembler

    estimates: list[dict[str, object]] = []

    def estimate(payload: dict[str, object]) -> int:
        estimates.append(payload)
        return estimated_tokens

    assembler = ContextAssembler(
        source=_SnapshotSource(snapshot),
        memory_search=memory_search,
        model_name="large-but-unverified",
        model_profiles={"large-but-unverified": _unverified_profile()},
        budget_node="supervisor",
        token_estimator=estimate,
        clock=lambda: NOW,
        recent_message_limit=recent_message_limit,
    )
    return assembler, estimates


@pytest.mark.asyncio
async def test_assembler_selects_relevant_context_in_deterministic_order() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ArtifactEvidenceRecord,
        ContextAssemblySnapshot,
        ContextMessageRecord,
        WorkflowSummaryRecord,
    )

    image = _workflow(
        "wf-image",
        kind=WorkflowKind.IMAGE,
        updated_offset=10,
        artifacts=["artifact:target-latest", "artifact:missing"],
    )
    video = _workflow("wf-video", kind=WorkflowKind.VIDEO, updated_offset=30)
    ppt = _workflow("wf-ppt", kind=WorkflowKind.PPT, updated_offset=20)
    conversation_summary = _summary(
        "conversation-v2",
        version=2,
        unresolved=["还需确认尺寸"],
        artifacts=["artifact:conversation-summary"],
        covered_end=2,
    )
    target_summary = _summary(
        "image-v3",
        version=3,
        unresolved=["还需确认尺寸", "还需确认颜色"],
        artifacts=["artifact:target-summary"],
    )
    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        active_workflow_id="wf-video",
        workflows=[video, image, ppt],
        messages=[
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=5,
                payload={"message_id": "msg-5", "role": "assistant", "content": "第五条"},
            ),
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=1,
                payload={"message_id": "msg-1", "role": "user", "content": "已摘要"},
            ),
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=4,
                payload={"message_id": "msg-4", "role": "user", "content": "第四条"},
            ),
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=3,
                payload={"message_id": "msg-3", "role": "assistant", "content": "第三条"},
            ),
        ],
        conversation_summaries=[
            conversation_summary,
            _summary("conversation-v1", version=1, covered_end=1),
        ],
        workflow_summaries=[
            WorkflowSummaryRecord(
                workflow_id="wf-video",
                summary=_summary("video-v1", version=1),
            ),
            WorkflowSummaryRecord(
                workflow_id="wf-video",
                summary=_summary("video-v2", version=2),
            ),
            WorkflowSummaryRecord(
                workflow_id="wf-ppt",
                summary=_summary("ppt-v1", version=1),
            ),
            WorkflowSummaryRecord(workflow_id="wf-image", summary=target_summary),
        ],
        artifact_evidence=[
            ArtifactEvidenceRecord(
                conversation_id="conv-1",
                artifact_ref="artifact:target-summary",
            ),
            ArtifactEvidenceRecord(
                conversation_id="conv-1",
                artifact_ref="artifact:explicit",
            ),
            ArtifactEvidenceRecord(
                conversation_id="conv-1",
                artifact_ref="artifact:target-latest",
            ),
            ArtifactEvidenceRecord(
                conversation_id="conv-1",
                artifact_ref="artifact:conversation-summary",
            ),
        ],
    )
    memory_search = _MemorySearch(
        [
            {"memory_id": "mem-2", "content": "偏好白色背景", "score": 0.9},
            {"memory_id": "mem-1", "content": "商品文字不可修改", "score": 0.8},
        ]
    )
    assembler, estimates = _assembler(snapshot, memory_search=memory_search)

    envelope = await assembler.assemble(_request())

    assert envelope.current_input == "把第三张图改成白色背景，保留这些空格"
    assert envelope.active_or_target_workflow == image
    assert [item["message_id"] for item in envelope.recent_messages] == [
        "msg-4",
        "msg-5",
    ]
    assert envelope.conversation_summary == conversation_summary
    assert [item.summary_id for item in envelope.related_workflow_summaries] == [
        "video-v2",
        "ppt-v1",
    ]
    assert [item["memory_id"] for item in envelope.relevant_long_term_memories] == [
        "mem-2",
        "mem-1",
    ]
    assert envelope.artifact_evidence_refs == [
        "artifact:explicit",
        "artifact:target-latest",
        "artifact:conversation-summary",
        "artifact:target-summary",
    ]
    assert envelope.unresolved_questions == ["还需确认尺寸", "还需确认颜色"]
    assert envelope.budget_report.estimated_input_tokens == 18_000
    assert envelope.budget_report.effective_context_tokens == 128 * 1024
    assert envelope.budget_report.max_output_tokens == 8 * 1024
    assert envelope.budget_report.usable_input_tokens == 88 * 1024
    assert len(estimates) == 1
    assert "budget_report" not in estimates[0]
    assert estimates[0]["current_input"] == envelope.current_input
    assert memory_search.calls == [
        {
            "user_id": "user-1",
            "query": envelope.current_input,
            "categories": ["preference", "brand", "skill", "experience"],
            "source_agent": None,
            "limit": 5,
        }
    ]


@pytest.mark.asyncio
async def test_assembler_uses_active_workflow_when_target_is_absent() -> None:
    from pixelflow.agent_runtime.context.assembler import ContextAssemblySnapshot

    image = _workflow("wf-image", kind=WorkflowKind.IMAGE, updated_offset=10)
    video = _workflow("wf-video", kind=WorkflowKind.VIDEO, updated_offset=20)
    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        active_workflow_id="wf-video",
        workflows=[image, video],
    )
    assembler, _ = _assembler(snapshot)

    envelope = await assembler.assemble(_request(target_workflow_id=None, artifact_refs=[]))

    assert envelope.active_or_target_workflow == video


@pytest.mark.asyncio
async def test_assembler_fails_closed_for_foreign_owner_or_stale_version() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ContextAssemblySnapshot,
        ContextVersionConflictError,
    )

    foreign = ContextAssemblySnapshot(
        user_id="user-2",
        conversation_id="conv-1",
        context_version=7,
    )
    memory_search = _MemorySearch([{"content": "不应读取"}])
    assembler, _ = _assembler(foreign, memory_search=memory_search)

    with pytest.raises(KeyError, match="user-1"):
        await assembler.assemble(_request(artifact_refs=[], target_workflow_id=None))
    assert memory_search.calls == []

    owned = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=8,
    )
    assembler, _ = _assembler(owned)
    with pytest.raises(ContextVersionConflictError, match="expected=7.*actual=8"):
        await assembler.assemble(_request(artifact_refs=[], target_workflow_id=None))


def test_snapshot_rejects_cross_conversation_records() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ArtifactEvidenceRecord,
        ContextAssemblySnapshot,
        ContextMessageRecord,
        WorkflowSummaryRecord,
    )

    foreign_workflow = _workflow(
        "wf-foreign",
        kind=WorkflowKind.IMAGE,
        updated_offset=0,
        conversation_id="conv-2",
    )
    foreign_summary = _summary(
        "summary-foreign",
        version=1,
        conversation_id="conv-2",
    )

    with pytest.raises(ValidationError, match="workflows"):
        ContextAssemblySnapshot(
            user_id="user-1",
            conversation_id="conv-1",
            context_version=7,
            workflows=[foreign_workflow],
        )
    with pytest.raises(ValidationError, match="messages"):
        ContextAssemblySnapshot(
            user_id="user-1",
            conversation_id="conv-1",
            context_version=7,
            messages=[
                ContextMessageRecord(
                    conversation_id="conv-2",
                    sequence=1,
                    payload={"message_id": "foreign"},
                )
            ],
        )
    with pytest.raises(ValidationError, match="workflow_summaries"):
        ContextAssemblySnapshot(
            user_id="user-1",
            conversation_id="conv-1",
            context_version=7,
            workflow_summaries=[
                WorkflowSummaryRecord(
                    workflow_id="wf-foreign",
                    summary=foreign_summary,
                )
            ],
        )
    with pytest.raises(ValidationError, match="artifact_evidence"):
        ContextAssemblySnapshot(
            user_id="user-1",
            conversation_id="conv-1",
            context_version=7,
            artifact_evidence=[
                ArtifactEvidenceRecord(
                    conversation_id="conv-2",
                    artifact_ref="artifact:foreign",
                )
            ],
        )


@pytest.mark.asyncio
async def test_assembler_hides_missing_target_and_explicit_artifact() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ArtifactEvidenceRecord,
        ContextAssemblySnapshot,
    )

    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        workflows=[_workflow("wf-image", kind=WorkflowKind.IMAGE, updated_offset=0)],
        artifact_evidence=[
            ArtifactEvidenceRecord(
                conversation_id="conv-1",
                artifact_ref="artifact:owned",
            )
        ],
    )
    memory_search = _MemorySearch()
    assembler, _ = _assembler(snapshot, memory_search=memory_search)

    with pytest.raises(KeyError, match="wf-missing"):
        await assembler.assemble(_request(target_workflow_id="wf-missing", artifact_refs=[]))
    with pytest.raises(KeyError, match="artifact:missing"):
        await assembler.assemble(
            _request(
                target_workflow_id="wf-image",
                artifact_refs=["artifact:missing"],
            )
        )
    assert memory_search.calls == []


@pytest.mark.asyncio
async def test_powermem_failure_is_fail_open_and_does_not_change_order() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ContextAssemblySnapshot,
        ContextMessageRecord,
    )

    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        messages=[
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=2,
                payload={"message_id": "msg-2"},
            ),
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=1,
                payload={"message_id": "msg-1"},
            ),
        ],
    )
    assembler, _ = _assembler(
        snapshot,
        memory_search=_MemorySearch(error=RuntimeError("token=secret-value")),
    )

    envelope = await assembler.assemble(_request(target_workflow_id=None, artifact_refs=[]))

    assert envelope.relevant_long_term_memories == []
    assert [item["message_id"] for item in envelope.recent_messages] == [
        "msg-1",
        "msg-2",
    ]


@pytest.mark.asyncio
async def test_assembler_returns_deeply_isolated_envelopes() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ContextAssemblySnapshot,
        ContextMessageRecord,
    )

    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        messages=[
            ContextMessageRecord(
                conversation_id="conv-1",
                sequence=1,
                payload={
                    "message_id": "msg-1",
                    "content": {"parts": ["原始内容"]},
                },
            )
        ],
    )
    assembler, _ = _assembler(snapshot, recent_message_limit=5)
    request = _request(target_workflow_id=None, artifact_refs=[])

    first = await assembler.assemble(request)
    first.recent_messages[0]["content"]["parts"].append("污染")
    second = await assembler.assemble(request)

    assert second.recent_messages[0]["content"] == {"parts": ["原始内容"]}


@pytest.mark.asyncio
async def test_assembler_freezes_request_and_snapshot_before_powermem_await() -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ContextAssemblySnapshot,
        WorkflowSummaryRecord,
    )

    image = _workflow("wf-image", kind=WorkflowKind.IMAGE, updated_offset=10)
    image.current_stage = "before"
    image.creation_contract_snapshot["goal"] = "before"
    video = _workflow("wf-video", kind=WorkflowKind.VIDEO, updated_offset=20)
    conversation_summary = _summary("conversation", version=1)
    conversation_summary.user_goals = ["before"]
    related_summary = _summary("video", version=1)
    related_summary.confirmed_decisions = ["before"]
    snapshot = ContextAssemblySnapshot(
        user_id="user-1",
        conversation_id="conv-1",
        context_version=7,
        active_workflow_id="wf-image",
        workflows=[image, video],
        conversation_summaries=[conversation_summary],
        workflow_summaries=[
            WorkflowSummaryRecord(
                workflow_id="wf-video",
                summary=related_summary,
            )
        ],
    )
    request = _request(
        current_input="调用开始时的输入",
        artifact_refs=[],
    )

    class _MutatingMemorySearch:
        async def search(self, **kwargs: object) -> list[dict[str, object]]:
            request.current_input = "等待期间被改写的输入"
            image.current_stage = "after"
            image.creation_contract_snapshot["goal"] = "after"
            conversation_summary.user_goals[0] = "after"
            related_summary.confirmed_decisions[0] = "after"
            return []

    assembler, _ = _assembler(
        snapshot,
        memory_search=_MutatingMemorySearch(),
    )

    envelope = await assembler.assemble(request)

    assert envelope.current_input == "调用开始时的输入"
    assert envelope.active_or_target_workflow is not None
    assert envelope.active_or_target_workflow.current_stage == "before"
    assert envelope.active_or_target_workflow.creation_contract_snapshot == {"goal": "before"}
    assert envelope.conversation_summary is not None
    assert envelope.conversation_summary.user_goals == ["before"]
    assert envelope.related_workflow_summaries[0].confirmed_decisions == ["before"]


@pytest.mark.parametrize("recent_message_limit", [0, -1, True])
def test_assembler_rejects_invalid_recent_message_limit(
    recent_message_limit: object,
) -> None:
    from pixelflow.agent_runtime.context.assembler import (
        ContextAssembler,
        ContextAssemblySnapshot,
    )

    with pytest.raises(ValueError, match="recent_message_limit"):
        ContextAssembler(
            source=_SnapshotSource(
                ContextAssemblySnapshot(
                    user_id="user-1",
                    conversation_id="conv-1",
                    context_version=7,
                )
            ),
            model_name="large-but-unverified",
            model_profiles={"large-but-unverified": _unverified_profile()},
            budget_node="supervisor",
            clock=lambda: NOW,
            recent_message_limit=recent_message_limit,
        )
