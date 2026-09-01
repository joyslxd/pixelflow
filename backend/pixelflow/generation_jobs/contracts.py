"""GenerationJob 的稳定状态和持久化合同。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field, JsonValue, model_validator

from pixelflow.platform.contracts import ContractModel


class GenerationJobKind(StrEnum):
    """Gateway 直接调度的外部生成能力类型。"""

    IMAGE = "image"
    VIDEO = "video"


class GenerationJobStatus(StrEnum):
    """GenerationJob 的有限状态集合。"""

    QUEUED = "queued"
    STARTING = "starting"
    POLLING = "polling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    EXPIRED = "expired"
    INDETERMINATE = "indeterminate"


_TERMINAL_STATUSES = frozenset(
    {
        GenerationJobStatus.SUCCEEDED,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.TIMEOUT,
        GenerationJobStatus.EXPIRED,
        GenerationJobStatus.INDETERMINATE,
    }
)


class GenerationJobRecord(ContractModel):
    """Gateway 权威保存的一项图片或视频 Provider Job。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    generation_job_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    kind: GenerationJobKind
    item_id: str = Field(min_length=1, max_length=128)
    variant_index: int = Field(default=1, ge=1, le=3)
    status: GenerationJobStatus
    request_json: dict[str, JsonValue] = Field(default_factory=dict)
    request_hash: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)
    provider_id: str = Field(min_length=1, max_length=128)
    provider_job_id: str | None = Field(default=None, max_length=255)
    result_json: dict[str, JsonValue] | None = None
    failure_reason_code: str | None = Field(default=None, max_length=128)
    next_poll_at: datetime | None = None
    lease_owner: str | None = Field(default=None, max_length=128)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_status_contract(self) -> GenerationJobRecord:
        """约束状态、Provider Job ID 和租约字段的组合。"""

        if self.status is GenerationJobStatus.POLLING and (
            not self.provider_job_id or self.next_poll_at is None
        ):
            raise ValueError("polling GenerationJob 必须包含 provider_job_id 和 next_poll_at")
        if self.status is GenerationJobStatus.STARTING and (
            not self.lease_owner or self.lease_expires_at is None
        ):
            raise ValueError("starting GenerationJob 必须包含 start lease")
        if self.status in _TERMINAL_STATUSES and (
            self.lease_owner is not None or self.lease_expires_at is not None
        ):
            raise ValueError("终态 GenerationJob 不能保留 lease")
        if self.status is GenerationJobStatus.SUCCEEDED and not self.result_json:
            raise ValueError("succeeded GenerationJob 必须包含 result_json")
        if self.status in {
            GenerationJobStatus.FAILED,
            GenerationJobStatus.TIMEOUT,
            GenerationJobStatus.EXPIRED,
            GenerationJobStatus.INDETERMINATE,
        } and not self.failure_reason_code:
            raise ValueError("失败或不确定 GenerationJob 必须包含 failure_reason_code")
        return self


__all__ = [
    "GenerationJobKind",
    "GenerationJobRecord",
    "GenerationJobStatus",
]
