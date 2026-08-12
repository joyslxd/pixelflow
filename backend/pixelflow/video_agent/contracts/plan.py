"""VideoAgent 公开计划与步骤合同。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field, JsonValue, computed_field, model_validator

from pixelflow.agent_runtime.contracts.base import ContractModel


class AgentPlanStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    # 已完成上下文判断，等待用户补充画幅/CTA 等；无任何可执行 Tool Step。
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_TERMINAL_STEP_STATUSES = {
    PlanStepStatus.COMPLETED,
    PlanStepStatus.FAILED,
    PlanStepStatus.SKIPPED,
}


class VideoAgentContract(ContractModel):
    """V2 合同不接受未知字段，并在创建后保持不可变。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AgentPlanStep(VideoAgentContract):
    step_id: str = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    status: PlanStepStatus
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    confirmation_required: bool = False
    public_summary: str | None = Field(default=None, max_length=2_000)
    artifact_refs: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_timestamps(self) -> AgentPlanStep:
        if self.status in {
            PlanStepStatus.PENDING,
            PlanStepStatus.AWAITING_CONFIRMATION,
        }:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError(
                    "pending/awaiting_confirmation 未执行 step 不能包含执行时间"
                )
            if (
                self.status is PlanStepStatus.AWAITING_CONFIRMATION
                and not self.confirmation_required
            ):
                raise ValueError("awaiting_confirmation step 必须声明确认要求")
            return self
        if self.started_at is None:
            raise ValueError("started_at is required once a step leaves pending")
        if self.status in _TERMINAL_STEP_STATUSES:
            if self.completed_at is None:
                raise ValueError("completed_at is required for a terminal step")
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot precede started_at")
        elif self.completed_at is not None:
            raise ValueError("non-terminal step cannot include completed_at")
        return self

    @computed_field(return_type=int | None)
    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at).total_seconds() * 1_000)


class AgentPlan(VideoAgentContract):
    plan_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    status: AgentPlanStatus
    public_goal: str | None = Field(default=None, max_length=2_000)
    steps: tuple[AgentPlanStep, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VideoToolCall(VideoAgentContract):
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    requires_confirmation: bool = False


class VideoToolResult(VideoAgentContract):
    tool_name: str = Field(min_length=1, max_length=128)
    public_summary: str = Field(min_length=1, max_length=2_000)
    workspace_patch: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()
    pending_operation_job_ids: tuple[str, ...] = ()
    requires_confirmation: bool = False

    @model_validator(mode="after")
    def validate_pending_operations(self) -> VideoToolResult:
        if any(not job_id.strip() for job_id in self.pending_operation_job_ids):
            raise ValueError("pending_operation_job_ids 不能包含空任务标识")
        if len(set(self.pending_operation_job_ids)) != len(
            self.pending_operation_job_ids
        ):
            raise ValueError("pending_operation_job_ids 不能重复")
        return self
