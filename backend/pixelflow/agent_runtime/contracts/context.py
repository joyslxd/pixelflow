"""统一 Context Runtime 的请求、预算、摘要与模型输入合同。"""

from datetime import datetime
from math import isclose
from typing import Self

from pydantic import Field, JsonValue, model_validator

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
