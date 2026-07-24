"""M04.5 结构化摘要关键事实 100% 保留测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from pixelflow.agent_runtime.context.verification import (
    SummaryVerificationBaseline,
    SummaryVerificationError,
    SummaryVerifier,
    calculate_summary_content_hash,
)
from pixelflow.agent_runtime.contracts import ContextSummary

NOW = datetime(2026, 7, 25, 5, 40, tzinfo=UTC)


def _summary(**updates: Any) -> ContextSummary:
    payload: dict[str, Any] = {
        "summary_id": "summary-2",
        "conversation_id": "conversation-1",
        "version": 2,
        "previous_summary_id": "summary-1",
        "content_hash": "sha256:" + "0" * 64,
        "user_goals": ["制作 30 秒新品视频"],
        "confirmed_decisions": ["使用 9:16 画幅", "模型使用 Seedance 1.5 Pro"],
        "negative_constraints": ["不要真人出镜"],
        "workflow_states": {
            "workflow-video-1": "等待 plan-20260725 人工确认",
        },
        "unresolved_questions": ["是否需要旁白"],
        "artifact_evidence_refs": [
            "artifact:plan-20260725",
            "artifact:asset-manifest-9",
        ],
        "covered_message_ids": ["message-1", "message-2"],
        "covered_sequence_start": 1,
        "covered_sequence_end": 2,
        "compression_model": "summary-model",
        "created_at": NOW,
    }
    payload.update(updates)
    draft = ContextSummary.model_validate(payload)
    return draft.model_copy(
        update={"content_hash": calculate_summary_content_hash(draft)},
        deep=True,
    )


def _baseline(**updates: Any) -> SummaryVerificationBaseline:
    payload: dict[str, Any] = {
        "conversation_id": "conversation-1",
        "required_user_goals": ["制作 30 秒新品视频"],
        "required_confirmed_decisions": ["使用 9:16 画幅"],
        "required_negative_constraints": ["不要真人出镜"],
        "required_workflow_states": {
            "workflow-video-1": "等待 plan-20260725 人工确认",
        },
        "required_unresolved_questions": ["是否需要旁白"],
        "required_artifact_evidence_refs": ["artifact:asset-manifest-9"],
        "required_identifiers": [
            "workflow-video-1",
            "plan-20260725",
            "artifact:asset-manifest-9",
        ],
    }
    payload.update(updates)
    return SummaryVerificationBaseline.model_validate(payload)


def test_summary_verifier_accepts_all_required_facts_and_identifiers() -> None:
    result = SummaryVerifier().verify(_summary(), _baseline())

    assert result.verified is True
    assert result.verified_fact_count == 10
    assert result.content_hash == _summary().content_hash


@pytest.mark.parametrize(
    ("summary_updates", "baseline_updates", "reason_code"),
    [
        (
            {"user_goals": ["制作新品图片"]},
            {},
            "missing_user_goal",
        ),
        (
            {"confirmed_decisions": ["模型使用 Seedance 1.5 Pro"]},
            {},
            "missing_confirmed_decision",
        ),
        (
            {"negative_constraints": []},
            {},
            "missing_negative_constraint",
        ),
        (
            {"workflow_states": {"workflow-video-1": "running"}},
            {},
            "workflow_state_mismatch",
        ),
        (
            {"unresolved_questions": []},
            {},
            "missing_unresolved_question",
        ),
        (
            {"artifact_evidence_refs": ["artifact:plan-20260725"]},
            {},
            "missing_artifact_evidence_ref",
        ),
        (
            {},
            {"required_identifiers": ["contract-77"]},
            "missing_identifier",
        ),
    ],
)
def test_summary_verifier_rejects_each_missing_critical_fact_without_echoing_content(
    summary_updates: dict[str, Any],
    baseline_updates: dict[str, Any],
    reason_code: str,
) -> None:
    summary = _summary(**summary_updates)
    baseline = _baseline(**baseline_updates)

    with pytest.raises(SummaryVerificationError) as caught:
        SummaryVerifier().verify(summary, baseline)

    assert caught.value.reason_code == reason_code
    assert "制作 30 秒新品视频" not in str(caught.value)
    assert "不要真人出镜" not in str(caught.value)
    assert "contract-77" not in str(caught.value)


def test_summary_verifier_rejects_tampered_semantics_with_stale_hash() -> None:
    original = _summary()
    tampered = original.model_copy(
        update={
            "confirmed_decisions": [
                "使用 9:16 画幅",
                "模型使用其他供应商",
            ]
        },
        deep=True,
    )

    with pytest.raises(SummaryVerificationError) as caught:
        SummaryVerifier().verify(tampered, _baseline())

    assert caught.value.reason_code == "content_hash_mismatch"


def test_summary_verification_baseline_rejects_empty_or_duplicate_requirements() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        _baseline(required_identifiers=[" "])

    with pytest.raises(ValueError, match="不能重复"):
        _baseline(
            required_negative_constraints=[
                "不要真人出镜",
                "不要真人出镜",
            ]
        )


def test_summary_verifier_rejects_conversation_mismatch() -> None:
    with pytest.raises(SummaryVerificationError) as caught:
        SummaryVerifier().verify(
            _summary(conversation_id="conversation-2"),
            _baseline(conversation_id="conversation-1"),
        )

    assert caught.value.reason_code == "conversation_mismatch"


def test_summary_verifier_does_not_accept_identifier_prefix_collision() -> None:
    with pytest.raises(SummaryVerificationError) as caught:
        SummaryVerifier().verify(
            _summary(),
            _baseline(required_identifiers=["plan-2026072"]),
        )

    assert caught.value.reason_code == "missing_identifier"
