"""在摘要进入持久化边界前校验关键事实和内容 hash。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import ContextSummary


class _VerificationRecord(BaseModel):
    """冻结验证输入，避免调用方在校验期间改写嵌套事实。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class SummaryVerificationBaseline(_VerificationRecord):
    """列出当前权威状态要求摘要逐项保留的关键事实。"""

    conversation_id: str = Field(min_length=1)
    required_user_goals: tuple[str, ...] = ()
    required_confirmed_decisions: tuple[str, ...] = ()
    required_negative_constraints: tuple[str, ...] = ()
    required_workflow_states: dict[str, str] = Field(default_factory=dict)
    required_unresolved_questions: tuple[str, ...] = ()
    required_artifact_evidence_refs: tuple[str, ...] = ()
    required_identifiers: tuple[str, ...] = ()

    @field_validator(
        "required_user_goals",
        "required_confirmed_decisions",
        "required_negative_constraints",
        "required_unresolved_questions",
        "required_artifact_evidence_refs",
        "required_identifiers",
    )
    @classmethod
    def require_unique_non_empty_values(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        """关键事实必须可精确比较，不能用空值或重复项稀释保留率。"""

        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("关键事实不能为空")
        if len(normalized) != len(set(normalized)):
            raise ValueError("关键事实不能重复")
        return normalized

    @model_validator(mode="after")
    def require_valid_workflow_states(self) -> Self:
        """Workflow 标识和状态摘要都必须是可定位的非空文本。"""

        if any(not workflow_id.strip() or not state.strip() for workflow_id, state in self.required_workflow_states.items()):
            raise ValueError("Workflow 关键事实不能为空")
        return self


class SummaryVerificationResult(_VerificationRecord):
    """返回可审计的验证计数，不回显用户摘要正文。"""

    verified: bool
    verified_fact_count: int = Field(ge=0)
    content_hash: str = Field(min_length=1)


class SummaryVerificationError(RuntimeError):
    """摘要缺失关键事实或内容 hash 不可信。"""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"结构化摘要验证失败：{reason_code}")


def calculate_summary_content_hash(summary: ContextSummary) -> str:
    """按 M04.2 的规范语义和覆盖范围重新计算稳定内容 hash。"""

    payload = {
        "semantic": {
            "user_goals": summary.user_goals,
            "confirmed_decisions": summary.confirmed_decisions,
            "negative_constraints": summary.negative_constraints,
            "workflow_states": summary.workflow_states,
            "unresolved_questions": summary.unresolved_questions,
            "artifact_evidence_refs": summary.artifact_evidence_refs,
        },
        "covered_message_ids": summary.covered_message_ids,
        "covered_sequence_start": summary.covered_sequence_start,
        "covered_sequence_end": summary.covered_sequence_end,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_subset(
    required: tuple[str, ...],
    actual: list[str],
    *,
    reason_code: str,
) -> None:
    if not set(required).issubset(actual):
        raise SummaryVerificationError(reason_code)


def _contains_stable_identifier(identifier: str, value: str) -> bool:
    """按稳定 ID 边界匹配，避免把 `plan-1` 误认成 `plan-10`。"""

    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])"
    return re.search(pattern, value) is not None


class SummaryVerifier:
    """使用精确匹配保证关键事实保留率为 100%。"""

    def verify(
        self,
        summary: ContextSummary,
        baseline: SummaryVerificationBaseline,
    ) -> SummaryVerificationResult:
        """验证会话、内容 hash 和每一类当前必需事实。"""

        frozen_summary = ContextSummary.model_validate(summary.model_dump(mode="python"))
        frozen_baseline = SummaryVerificationBaseline.model_validate(baseline.model_dump(mode="python"))
        if frozen_summary.conversation_id != frozen_baseline.conversation_id:
            raise SummaryVerificationError("conversation_mismatch")
        expected_hash = calculate_summary_content_hash(frozen_summary)
        if frozen_summary.content_hash != expected_hash:
            raise SummaryVerificationError("content_hash_mismatch")

        _require_subset(
            frozen_baseline.required_user_goals,
            frozen_summary.user_goals,
            reason_code="missing_user_goal",
        )
        _require_subset(
            frozen_baseline.required_confirmed_decisions,
            frozen_summary.confirmed_decisions,
            reason_code="missing_confirmed_decision",
        )
        _require_subset(
            frozen_baseline.required_negative_constraints,
            frozen_summary.negative_constraints,
            reason_code="missing_negative_constraint",
        )
        for workflow_id, state in frozen_baseline.required_workflow_states.items():
            if frozen_summary.workflow_states.get(workflow_id) != state:
                raise SummaryVerificationError("workflow_state_mismatch")
        _require_subset(
            frozen_baseline.required_unresolved_questions,
            frozen_summary.unresolved_questions,
            reason_code="missing_unresolved_question",
        )
        _require_subset(
            frozen_baseline.required_artifact_evidence_refs,
            frozen_summary.artifact_evidence_refs,
            reason_code="missing_artifact_evidence_ref",
        )

        searchable_values = list(frozen_summary.user_goals)
        searchable_values.extend(frozen_summary.confirmed_decisions)
        searchable_values.extend(frozen_summary.negative_constraints)
        searchable_values.extend(frozen_summary.workflow_states)
        searchable_values.extend(frozen_summary.workflow_states.values())
        searchable_values.extend(frozen_summary.unresolved_questions)
        searchable_values.extend(frozen_summary.artifact_evidence_refs)
        if any(not any(_contains_stable_identifier(identifier, value) for value in searchable_values) for identifier in frozen_baseline.required_identifiers):
            raise SummaryVerificationError("missing_identifier")

        verified_fact_count = (
            len(frozen_baseline.required_user_goals)
            + len(frozen_baseline.required_confirmed_decisions)
            + len(frozen_baseline.required_negative_constraints)
            + len(frozen_baseline.required_workflow_states)
            + len(frozen_baseline.required_unresolved_questions)
            + len(frozen_baseline.required_artifact_evidence_refs)
            + len(frozen_baseline.required_identifiers)
            + 1
        )
        return SummaryVerificationResult(
            verified=True,
            verified_fact_count=verified_fact_count,
            content_hash=expected_hash,
        )


__all__ = [
    "SummaryVerificationBaseline",
    "SummaryVerificationError",
    "SummaryVerificationResult",
    "SummaryVerifier",
    "calculate_summary_content_hash",
]
