"""External Job quota 暂停、授权恢复与 Workflow 投递。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import (
    ConfigDict,
    Field,
    SerializationInfo,
    field_serializer,
    model_validator,
)

from ..contracts import AgentEvent, AgentEventType, ExternalJobStatus
from ..contracts.base import ContractModel
from ..graph import GraphExecutionNamespace, workflow_namespace
from ..persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    EventDeliveryClaim,
    OperationQuotaEventRecord,
    OperationRecord,
    OwnedOperationQuotaEvent,
)
from ..ports import OperationConflictError
from .completion import _freeze_event, _freeze_operation


class OperationQuotaState(StrEnum):
    """Operation quota 事件允许的两种状态。"""

    PAUSED = "paused"
    RESUMED = "resumed"


class _FrozenEventDeliveryClaim(EventDeliveryClaim):
    """冻结投递 claim 及其嵌套事件。"""

    model_config = ConfigDict(frozen=True)

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "event", _freeze_event(self.event))

    @field_serializer("event")
    def serialize_event(self, value: AgentEvent, info: SerializationInfo) -> object:
        """序列化时把只读 payload 恢复为普通 JSON 容器。"""

        return value.model_dump(mode=info.mode)


def _freeze_claim(claim: EventDeliveryClaim) -> EventDeliveryClaim:
    return _FrozenEventDeliveryClaim.model_validate(claim.model_dump(mode="python"))


class OperationQuotaTransitionRecord(ContractModel):
    """返回深度只读且可稳定 JSON 序列化的 Operation 与 quota Event。"""

    model_config = ConfigDict(frozen=True)

    operation: OperationRecord
    event: AgentEvent

    def model_post_init(self, context: object, /) -> None:
        """重建嵌套合同，防止调用方修改持久化快照。"""

        del context
        object.__setattr__(self, "operation", _freeze_operation(self.operation))
        object.__setattr__(self, "event", _freeze_event(self.event))

    @field_serializer("event")
    def serialize_event(self, value: AgentEvent, info: SerializationInfo) -> object:
        """让外层记录复用只读事件的 JSON 序列化。"""

        return value.model_dump(mode=info.mode)


class OperationQuotaAuthorizedResume(ContractModel):
    """原子恢复事务返回的 Operation 与当前请求 Event claim。"""

    model_config = ConfigDict(frozen=True)

    operation: OperationRecord
    claim: EventDeliveryClaim

    def model_post_init(self, context: object, /) -> None:
        """冻结 Operation、claim 与 claim 内的事件 payload。"""

        del context
        object.__setattr__(self, "operation", _freeze_operation(self.operation))
        object.__setattr__(self, "claim", _freeze_claim(self.claim))

    @field_serializer("claim")
    def serialize_claim(
        self,
        value: EventDeliveryClaim,
        info: SerializationInfo,
    ) -> object:
        """让冻结 claim 保持普通 JSON 序列化结果。"""

        return value.model_dump(mode=info.mode)


class OperationQuotaEventPayload(ContractModel):
    """严格限制 quota Outbox 的公开安全字段。"""

    job_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    stage_version: int = Field(ge=1)
    attempt: int = Field(ge=1)
    quota_pause_revision: int = Field(ge=1)
    quota_state: OperationQuotaState
    reason_code: Literal[
        "provider_quota_insufficient",
        "provider_quota_resume_authorized",
    ]

    @model_validator(mode="after")
    def validate_state_reason_pair(self) -> Self:
        """防止 pause 与 resume 原因交叉进入 Workflow 投递。"""

        expected_reason = (
            "provider_quota_insufficient"
            if self.quota_state is OperationQuotaState.PAUSED
            else "provider_quota_resume_authorized"
        )
        if self.reason_code != expected_reason:
            raise ValueError("quota_state 与 reason_code 不一致")
        return self


@runtime_checkable
class WorkflowGraphQuotaStatePort(Protocol):
    """按 quota 事件 ID 幂等恢复原 Workflow Graph。"""

    async def resume_external_job_quota(
        self,
        namespace: GraphExecutionNamespace,
        *,
        quota_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        """只恢复已有 Operation，不得重新调用 Provider start。"""

        ...


def _require_text(field: str, value: str, *, maximum: int = 64) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > maximum:
        raise ValueError(f"{field} 必须是 1 到 {maximum} 个无首尾空白的字符")
    return normalized


def _require_revision(revision: int, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < minimum:
        raise ValueError(f"quota revision 必须是不小于 {minimum} 的整数")
    return revision


def _quota_digest(
    job_id: str,
    revision: int,
    quota_state: OperationQuotaState,
) -> str:
    operation_id = _require_text("job_id", job_id)
    normalized_revision = _require_revision(revision)
    state = OperationQuotaState(quota_state)
    return hashlib.sha256(
        f"pixelflow:external-job-quota:v1:{operation_id}:{normalized_revision}:{state.value}".encode()
    ).hexdigest()


def build_operation_quota_event_id(
    job_id: str,
    revision: int,
    quota_state: OperationQuotaState,
) -> str:
    """仅从内部 job、revision 和 quota 状态派生稳定事件 ID。"""

    digest = _quota_digest(job_id, revision, quota_state)
    return f"evt_job_quota_{digest[:32]}"


def _build_quota_event_record(
    operation: OperationRecord,
    *,
    revision: int,
    quota_state: OperationQuotaState,
    now: datetime,
) -> OperationQuotaEventRecord:
    digest = _quota_digest(operation.job_id, revision, quota_state)
    payload = OperationQuotaEventPayload(
        job_id=operation.job_id,
        workflow_id=operation.workflow_id,
        stage=operation.stage,
        stage_version=operation.stage_version,
        attempt=operation.attempt,
        quota_pause_revision=revision,
        quota_state=quota_state,
        reason_code=(
            "provider_quota_insufficient"
            if quota_state is OperationQuotaState.PAUSED
            else "provider_quota_resume_authorized"
        ),
    )
    return OperationQuotaEventRecord(
        event_id=f"evt_job_quota_{digest[:32]}",
        cursor=f"cursor_job_quota_{digest[:32]}",
        run_id=f"run_job_quota_{digest[:32]}",
        occurred_at=now,
        quota_pause_revision=revision,
        quota_state=quota_state.value,
        payload=payload.model_dump(mode="json"),
    )


class OperationQuotaCoordinator:
    """以原子 Repository 事务协调 quota 暂停与授权恢复。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        user_id: str,
        conversation_id: str,
    ) -> None:
        self._repository = repository
        self._user_id = _require_text("user_id", user_id)
        self._conversation_id = _require_text("conversation_id", conversation_id)

    async def _operation(self, job_id: str) -> OperationRecord:
        operation_id = _require_text("job_id", job_id)
        operation = await self._repository.get_operation(self._user_id, operation_id)
        if operation is None or operation.conversation_id != self._conversation_id:
            raise OperationConflictError("Operation 不存在或不属于当前会话")
        return operation

    async def record_pause(
        self,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationQuotaTransitionRecord:
        """通过 CAS 递增 revision，并在同一事务写入 pause Outbox。"""

        operation = await self._operation(job_id)
        worker = _require_text("lease_owner", lease_owner, maximum=128)
        if (
            operation.status is not ExternalJobStatus.POLLING
            or operation.provider_job_id is None
            or operation.lease_owner != worker
            or operation.lease_expires_at is None
            or operation.lease_expires_at <= now
        ):
            raise OperationConflictError("Operation quota pause 需要当前轮询租约")
        current_revision = _require_revision(
            operation.quota_pause_revision,
            allow_zero=True,
        )
        revision = current_revision + 1
        event = _build_quota_event_record(
            operation,
            revision=revision,
            quota_state=OperationQuotaState.PAUSED,
            now=now,
        )
        try:
            paused, stored_event = await self._repository.pause_operation_for_quota(
                self._user_id,
                self._conversation_id,
                operation.job_id,
                provider_job_id=operation.provider_job_id,
                lease_owner=worker,
                expected_revision=current_revision,
                now=now,
                event=event,
            )
        except AgentRuntimeRecordConflictError:
            raise OperationConflictError("Operation quota pause 持久化冲突") from None
        return OperationQuotaTransitionRecord(
            operation=paused,
            event=stored_event,
        )

    async def authorize_resume(
        self,
        job_id: str,
        *,
        workflow_id: str,
        expected_revision: int,
        delivery_lease_owner: str,
        now: datetime,
        delivery_lease_expires_at: datetime,
    ) -> OperationQuotaAuthorizedResume:
        """校验当前暂停 revision，原子续跑原 job 并领取 resume 事件。"""

        operation = await self._operation(job_id)
        workflow = _require_text("workflow_id", workflow_id)
        revision = _require_revision(expected_revision)
        worker = _require_text(
            "delivery_lease_owner",
            delivery_lease_owner,
            maximum=128,
        )
        if (
            operation.status is not ExternalJobStatus.POLLING
            or operation.provider_job_id is None
            or operation.workflow_id != workflow
            or operation.quota_pause_revision != revision
            or operation.next_poll_at is not None
            or operation.lease_owner is not None
            or operation.lease_expires_at is not None
        ):
            raise OperationConflictError("Operation quota revision 不是当前可恢复暂停")
        event = _build_quota_event_record(
            operation,
            revision=revision,
            quota_state=OperationQuotaState.RESUMED,
            now=now,
        )
        try:
            resumed, claim = await self._repository.resume_operation_from_quota(
                self._user_id,
                self._conversation_id,
                operation.job_id,
                workflow_id=workflow,
                expected_revision=revision,
                now=now,
                delivery_lease_owner=worker,
                delivery_lease_expires_at=delivery_lease_expires_at,
                event=event,
            )
        except AgentRuntimeRecordConflictError:
            raise OperationConflictError("Operation quota resume 持久化冲突") from None
        return OperationQuotaAuthorizedResume(
            operation=resumed,
            claim=claim,
        )


class OperationQuotaDispatcher:
    """精确领取 quota 事件，恢复 Workflow 后再确认 Outbox。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        quota_resumer: WorkflowGraphQuotaStatePort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(quota_resumer, WorkflowGraphQuotaStatePort):
            raise TypeError("quota_resumer 必须实现 WorkflowGraphQuotaStatePort")
        self._repository = repository
        self._quota_resumer = quota_resumer
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch(
        self,
        candidate: OwnedOperationQuotaEvent,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> AgentEvent | None:
        """按 candidate 事件 ID 投递，不让通用 Outbox 过滤或越过。"""

        normalized = OwnedOperationQuotaEvent(
            user_id=candidate.user_id,
            operation=OperationRecord.model_validate(
                candidate.operation.model_dump(mode="python")
            ),
            event=AgentEvent.model_validate(
                candidate.event.model_dump(mode="python")
            ),
        )
        operation = normalized.operation
        event = normalized.event
        payload = OperationQuotaEventPayload.model_validate(event.payload)
        if (
            event.type is not AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
            or event.conversation_id != operation.conversation_id
            or payload.job_id != operation.job_id
            or payload.workflow_id != operation.workflow_id
            or payload.stage != operation.stage
            or payload.stage_version != operation.stage_version
            or payload.attempt != operation.attempt
            or payload.quota_pause_revision != operation.quota_pause_revision
            or event.event_id
            != build_operation_quota_event_id(
                operation.job_id,
                payload.quota_pause_revision,
                payload.quota_state,
            )
        ):
            raise OperationConflictError("Operation quota 事件与当前投影不一致")
        claim = await self._repository.claim_operation_quota_event(
            normalized.user_id,
            operation.conversation_id,
            event.event_id,
            operation.job_id,
            quota_pause_revision=payload.quota_pause_revision,
            quota_state=payload.quota_state.value,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=lease_expires_at,
        )
        if claim is None:
            return None
        try:
            await self._quota_resumer.resume_external_job_quota(
                workflow_namespace(
                    operation.conversation_id,
                    operation.workflow_id,
                ),
                quota_event=_freeze_event(claim.event),
                idempotency_key=claim.event.event_id,
            )
        except Exception:
            raise RuntimeError("Workflow Graph quota 恢复失败") from None
        try:
            completed = await self._repository.complete_event_delivery(
                normalized.user_id,
                claim.event.event_id,
                lease_owner=lease_owner,
                published_at=self._clock(),
            )
        except AgentRuntimeRecordConflictError:
            raise OperationConflictError("Workflow quota 恢复后投递租约已失效") from None
        if completed is None:
            raise OperationConflictError("quota 事件确认时已不可见")
        return _freeze_event(completed)


__all__ = [
    "OperationQuotaAuthorizedResume",
    "OperationQuotaCoordinator",
    "OperationQuotaDispatcher",
    "OperationQuotaEventPayload",
    "OperationQuotaState",
    "OperationQuotaTransitionRecord",
    "WorkflowGraphQuotaStatePort",
    "build_operation_quota_event_id",
]
