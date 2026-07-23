"""Supervisor 结构化决策合同。"""

from typing import Self

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel
from .enums import AgentAction, AgentIntent


class ActionDecision(ContractModel):
    """保存公开动作摘要，不保存模型隐藏推理。"""

    action: AgentAction
    intent: AgentIntent
    target_workflow_id: str | None = Field(default=None, min_length=1)
    target_stage: str | None = Field(default=None, min_length=1)
    target_artifact_ref: str | None = Field(default=None, min_length=1)
    confidence: float = Field(ge=0, le=1)
    requires_confirmation: bool = False
    clarification_question: str | None = Field(default=None, min_length=1)
    patch: dict[str, JsonValue] = Field(default_factory=dict)
    reason_code: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_question_for_clarification(self) -> Self:
        """追问动作必须携带可直接展示给用户的问题。"""

        if self.action == AgentAction.CLARIFY and not self.clarification_question:
            raise ValueError("clarification_question is required when action is clarify")
        if self.action in {AgentAction.ANSWER_ONLY, AgentAction.CLARIFY} and self.patch:
            raise ValueError("patch must be empty for non-mutating actions")
        return self
