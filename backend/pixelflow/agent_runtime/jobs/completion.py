"""External Job Operation 的事务性终态事件与 Workflow 恢复。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pydantic import ConfigDict, SerializationInfo, field_serializer

from ..contracts import AgentEvent, ExternalJobStatus
from ..contracts.base import ContractModel
from ..graph import GraphExecutionNamespace, workflow_namespace
from ..persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    OperationRecord,
    OperationTerminalEventRecord,
)
from ..ports import OperationConflictError
from .providers import ProviderJobOutcome, ProviderJobSnapshot

_TERMINAL_STATUS_BY_OUTCOME = {
    ProviderJobOutcome.SUCCEEDED: ExternalJobStatus.SUCCEEDED,
    ProviderJobOutcome.FAILED: ExternalJobStatus.FAILED,
    ProviderJobOutcome.TIMEOUT: ExternalJobStatus.TIMEOUT,
}


class OperationCompletionConflictError(OperationConflictError):
    """Operation 终态、完成事件或恢复租约不满足安全合同。"""


class OperationCompletionDispatchError(RuntimeError):
    """Workflow Graph 恢复失败，且不回显内部异常内容。"""


class _FrozenJsonList(tuple[object, ...]):
    """保持 JSON list 相等语义、但不暴露原地修改方法。"""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, (list, tuple)) and tuple(self) == tuple(other)

    __hash__ = None


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenJsonList(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(child) for child in value]
    return value


class _FrozenOperationRecord(OperationRecord):
    """只用于完成边界的不可变 Operation 快照。"""

    model_config = ConfigDict(frozen=True)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, OperationRecord) and self.model_dump(mode="python") == other.model_dump(mode="python")

    __hash__ = None


class _FrozenAgentEvent(AgentEvent):
    """冻结事件 envelope 及其全部 JSON 容器。"""

    model_config = ConfigDict(frozen=True)

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @field_serializer("payload")
    def serialize_payload(self, value: object) -> object:
        return _thaw_json(value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AgentEvent) and self.model_dump(mode="python") == other.model_dump(mode="python")

    __hash__ = None


def _freeze_operation(record: OperationRecord) -> OperationRecord:
    return _FrozenOperationRecord.model_validate(record.model_dump(mode="python"))


def _freeze_event(event: AgentEvent) -> AgentEvent:
    return _FrozenAgentEvent.model_validate(event.model_dump(mode="python"))


class OperationCompletionRecord(ContractModel):
    """原子事务返回的 Operation 与完成事件快照。"""

    model_config = ConfigDict(frozen=True)

    operation: OperationRecord
    event: AgentEvent

    def model_post_init(self, context: object, /) -> None:
        """把嵌套合同复制为深度只读快照。"""

        del context
        object.__setattr__(self, "operation", _freeze_operation(self.operation))
        object.__setattr__(self, "event", _freeze_event(self.event))

    @field_serializer("event")
    def serialize_event(self, value: AgentEvent, info: SerializationInfo) -> object:
        """让外层记录复用只读事件的容器解冻序列化。"""

        return value.model_dump(mode=info.mode)


@runtime_checkable
class WorkflowGraphResumePort(Protocol):
    """按完成事件 ID 幂等恢复原 Workflow Graph。"""

    async def resume_external_job(
        self,
        namespace: GraphExecutionNamespace,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        """恢复只消费既有结果，不得重新调用供应商 start。"""

        ...


def _require_job_id(job_id: str) -> str:
    if not isinstance(job_id, str):
        raise ValueError("job_id 必须是字符串")
    normalized = job_id.strip()
    if not normalized or normalized != job_id or len(normalized) > 64:
        raise ValueError("job_id 必须是 1 到 64 个无首尾空白的字符")
    return normalized


def _completion_digest(job_id: str) -> str:
    normalized = _require_job_id(job_id)
    return hashlib.sha256(f"pixelflow:external-job-completion:v1:{normalized}".encode()).hexdigest()


def build_operation_completion_event_id(job_id: str) -> str:
    """从内部 job ID 派生稳定且不暴露业务内容的完成事件 ID。"""

    return f"evt_job_done_{_completion_digest(job_id)[:32]}"


def _build_completion_cursor(job_id: str) -> str:
    return f"cursor_job_done_{_completion_digest(job_id)[:32]}"


def _build_completion_run_id(job_id: str) -> str:
    return f"run_job_done_{_completion_digest(job_id)[:32]}"


class OperationCompletionCoordinator:
    """把 Provider 安全终态与唯一 Outbox 事件原子落库。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        user_id: str,
        conversation_id: str,
        max_finalize_attempts: int = 8,
    ) -> None:
        if isinstance(max_finalize_attempts, bool) or max_finalize_attempts < 1:
            raise ValueError("max_finalize_attempts 必须是大于零的整数")
        self._repository = repository
        self._user_id = self._require_scope("user_id", user_id)
        self._conversation_id = self._require_scope(
            "conversation_id",
            conversation_id,
        )
        self._max_finalize_attempts = max_finalize_attempts

    @staticmethod
    def _require_scope(field: str, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是字符串")
        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError(f"{field} 必须是 1 到 64 个字符")
        return normalized

    async def record_terminal(
        self,
        job_id: str,
        snapshot: ProviderJobSnapshot,
        *,
        lease_owner: str,
        now: datetime,
    ) -> OperationCompletionRecord:
        """保存成功、业务失败或超时终态；其他结果拒绝进入完成通道。"""

        operation_id = _require_job_id(job_id)
        normalized_snapshot = ProviderJobSnapshot.model_validate(snapshot.model_dump(mode="json"))
        terminal_status = _TERMINAL_STATUS_BY_OUTCOME.get(normalized_snapshot.outcome)
        if terminal_status is None:
            raise OperationCompletionConflictError("Provider 结果不是可完成的 Operation 终态")
        provider_job_id = normalized_snapshot.provider_job_id
        if provider_job_id is None:
            raise OperationCompletionConflictError("Provider 终态缺少原任务 ID")

        operation = await self._repository.get_operation(
            self._user_id,
            operation_id,
        )
        if operation is None or operation.conversation_id != self._conversation_id:
            raise OperationCompletionConflictError("Operation 不存在或不属于当前会话")
        if operation.provider_job_id != provider_job_id:
            raise OperationCompletionConflictError("Provider Job ID 与 Operation 不一致")
        snapshot_payload = normalized_snapshot.model_dump(mode="json")
        event_record = OperationTerminalEventRecord(
            event_id=build_operation_completion_event_id(operation_id),
            cursor=_build_completion_cursor(operation_id),
            run_id=_build_completion_run_id(operation_id),
            occurred_at=now,
            payload={
                "job_id": operation.job_id,
                "provider_job_id": provider_job_id,
                "workflow_id": operation.workflow_id,
                "stage": operation.stage,
                "stage_version": operation.stage_version,
                "attempt": operation.attempt,
                "status": terminal_status.value,
                "reason_code": snapshot_payload["reason_code"],
                "message": snapshot_payload["message"],
                "result": snapshot_payload["result"],
            },
        )

        last_error: AgentRuntimeRecordConflictError | None = None
        for _ in range(self._max_finalize_attempts):
            try:
                completed_operation, completion_event = await self._repository.finalize_operation_terminal(
                    self._user_id,
                    self._conversation_id,
                    operation_id,
                    provider_job_id=provider_job_id,
                    terminal_status=terminal_status,
                    lease_owner=lease_owner,
                    now=now,
                    event=event_record,
                )
                return OperationCompletionRecord(
                    operation=completed_operation,
                    event=completion_event,
                )
            except AgentRuntimeRecordConflictError as exc:
                last_error = exc
        raise OperationCompletionConflictError("Operation 终态或完成事件持久化冲突") from last_error


class OperationCompletionDispatcher:
    """领取唯一完成事件并把既有 Provider 结果恢复到原 Workflow。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        resumer: WorkflowGraphResumePort,
        user_id: str,
        conversation_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._resumer = resumer
        self._user_id = OperationCompletionCoordinator._require_scope(
            "user_id",
            user_id,
        )
        self._conversation_id = OperationCompletionCoordinator._require_scope(
            "conversation_id",
            conversation_id,
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch(
        self,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> AgentEvent | None:
        """至少一次投递同一事件；成功 checkpoint 后再确认 Outbox。"""

        operation_id = _require_job_id(job_id)
        event_id = build_operation_completion_event_id(operation_id)
        claim = await self._repository.claim_operation_completion_event(
            self._user_id,
            self._conversation_id,
            event_id,
            operation_id,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=lease_expires_at,
        )
        if claim is None:
            return None
        operation = await self._repository.get_operation(
            self._user_id,
            operation_id,
        )
        if operation is None or operation.conversation_id != self._conversation_id or claim.event.payload.get("workflow_id") != operation.workflow_id:
            raise OperationCompletionConflictError("完成事件与 Operation 身份不一致")
        namespace = workflow_namespace(
            self._conversation_id,
            operation.workflow_id,
        )
        try:
            completion_event = _freeze_event(claim.event)
            await self._resumer.resume_external_job(
                namespace,
                completion_event=completion_event,
                idempotency_key=claim.event.event_id,
            )
        except Exception:
            raise OperationCompletionDispatchError("Workflow Graph 恢复失败") from None

        try:
            completed = await self._repository.complete_event_delivery(
                self._user_id,
                claim.event.event_id,
                lease_owner=lease_owner,
                published_at=self._clock(),
            )
        except AgentRuntimeRecordConflictError:
            raise OperationCompletionConflictError("Workflow 恢复后的完成事件投递租约已失效") from None
        if completed is None:
            raise OperationCompletionConflictError("完成事件确认时已不可见")
        return _freeze_event(completed)


__all__ = [
    "OperationCompletionConflictError",
    "OperationCompletionCoordinator",
    "OperationCompletionDispatchError",
    "OperationCompletionDispatcher",
    "OperationCompletionRecord",
    "WorkflowGraphResumePort",
    "build_operation_completion_event_id",
]
