"""统一 Context Runtime 的请求、预算、摘要与模型输入合同。"""

from datetime import datetime
from math import isclose
from typing import Self

from pydantic import Field, JsonValue, ValidationInfo, field_validator, model_validator

from .base import ContractModel
from .records import WorkflowRecord


class ContextSummary(ContractModel):
    """保留关键事实和证据引用的版本化结构摘要。"""

    summary_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    previous_summary_id: str | None = Field(default=None, min_length=1)
    content_hash: str = Field(min_length=1)
    user_goals: list[str] = Field(default_factory=list)
    confirmed_decisions: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)
    workflow_states: dict[str, str] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    artifact_evidence_refs: list[str] = Field(default_factory=list)
    covered_message_ids: list[str] = Field(default_factory=list)
    covered_sequence_start: int | None = Field(default=None, ge=1)
    covered_sequence_end: int | None = Field(default=None, ge=1)
    compression_model: str = Field(min_length=1)
    created_at: datetime

    @field_validator("artifact_evidence_refs", "covered_message_ids")
    @classmethod
    def require_unique_non_empty_evidence(
        cls,
        values: list[str],
        info: ValidationInfo,
    ) -> list[str]:
        """证据引用必须可定位，并保持稳定且无重复的顺序。"""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError(f"{info.field_name} 不能包含空引用")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{info.field_name} 不能重复")
        return normalized

    @model_validator(mode="after")
    def require_version_chain_and_coverage(self) -> Self:
        """保证单条摘要具备可验证的版本前驱和消息覆盖范围。"""

        if self.previous_summary_id == self.summary_id:
            raise ValueError("摘要不能把自己声明为前一版")
        if self.version == 1 and self.previous_summary_id is not None:
            raise ValueError("第一版摘要不能声明前一版")
        if self.version > 1 and self.previous_summary_id is None:
            raise ValueError("第二版及后续摘要必须声明前一版")

        start = self.covered_sequence_start
        end = self.covered_sequence_end
        if (start is None) != (end is None):
            raise ValueError("覆盖范围起止必须同时存在")
        if start is None:
            if self.covered_message_ids:
                raise ValueError("没有覆盖范围时不能声明消息 ID")
            return self
        if end is None:
            raise AssertionError("覆盖范围终点已由成对校验保证存在")
        if start > end:
            raise ValueError("覆盖范围起点不能大于终点")
        if start != 1:
            raise ValueError("覆盖范围必须从 sequence 1 开始")
        if len(self.covered_message_ids) != end - start + 1:
            raise ValueError("覆盖范围必须与消息 ID 数量一致")
        return self


class ContextBudgetReport(ContractModel):
    """记录本次组装后的可用输入预算，不暴露给最终用户。"""

    estimated_input_tokens: int = Field(ge=0)
    effective_context_tokens: int = Field(ge=1)
    usable_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=0)
    safety_reserve_tokens: int = Field(ge=0)
    utilization: float = Field(ge=0)
    compaction_level: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_usable_input_formula(self) -> Self:
        """保证所有节点使用同一份可用输入预算公式。"""

        expected = self.effective_context_tokens - self.max_output_tokens - self.safety_reserve_tokens
        if self.usable_input_tokens != expected:
            raise ValueError("usable_input_tokens must equal effective_context_tokens minus max_output_tokens and safety_reserve_tokens")
        expected_utilization = self.estimated_input_tokens / self.usable_input_tokens
        if not isclose(self.utilization, expected_utilization, rel_tol=0, abs_tol=1e-8):
            raise ValueError("utilization must equal estimated_input_tokens divided by usable_input_tokens")
        return self


class ContextEnvelope(ContractModel):
    """每次模型调用收到的相关上下文，而不是全部数据库原文。"""

    current_input: str = Field(min_length=1)
    validated_context_version: int = Field(ge=0)
    active_or_target_workflow: WorkflowRecord | None = None
    recent_messages: list[dict[str, JsonValue]] = Field(default_factory=list)
    conversation_summary: ContextSummary | None = None
    related_workflow_summaries: list[ContextSummary] = Field(default_factory=list)
    relevant_long_term_memories: list[dict[str, JsonValue]] = Field(default_factory=list)
    artifact_evidence_refs: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    budget_report: ContextBudgetReport


class ContextRequest(ContractModel):
    """调用 ContextPort 组装一次模型输入所需的最小请求。"""

    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    current_input: str = Field(min_length=1)
    target_workflow_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    expected_context_version: int = Field(ge=0)
