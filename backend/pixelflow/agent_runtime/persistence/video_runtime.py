"""视频 live Runtime 的 Turn 租约、状态 CAS 与原子投影 Repository。"""

from __future__ import annotations

import base64
import hashlib
import json
from asyncio import Lock
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    SerializationInfo,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy import String, and_, cast, exists, null, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from pixelflow.agent_workflows.video import (
    VideoWorkflowStateEnvelope,
    canonical_video_workflow_envelope_sha256,
    decode_video_workflow_state,
    project_video_workflow_state,
)
from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)

from ..contracts import (
    ActionDecision,
    AgentEvent,
    AgentEventType,
    AgentInterruptProjection,
    ContextSummary,
    ExternalJobRef,
    ExternalJobStatus,
    InterruptResponseRequest,
    TurnRecord,
    TurnStatus,
    WorkflowRecord,
)
from ..contracts.base import ContractModel
from ..identity import conversation_message_id
from .compaction_queue import MemoryCompactionQueueRepository, SQLCompactionQueueRepository
from .models import (
    PixelFlowAgentCompactionLockRow,
    PixelFlowAgentContextSummaryRow,
    PixelFlowAgentConversationStateRow,
    PixelFlowAgentEventRow,
    PixelFlowAgentInterruptRow,
    PixelFlowAgentOperationRow,
    PixelFlowAgentProjectionMessageRow,
    PixelFlowAgentTurnExecutionRow,
    PixelFlowAgentTurnRow,
    PixelFlowAgentVideoStateRow,
    PixelFlowAgentWorkflowRow,
)
from .repositories import (
    AgentRuntimeRecordConflictError,
    EventDeliveryClaim,
    OperationRecord,
    _clone,
    _database_utc,
    _event_from_row,
    _MemoryEventDeliveryState,
    _normalize_datetime,
    _operation_from_row,
    _repository_write_transaction,
    _require_text,
    _summary_from_row,
    _turn_from_row,
    _workflow_from_row,
)

_TERMINAL_TURN_STATUSES = {TurnStatus.COMPLETED, TurnStatus.FAILED}
_CLAIMABLE_TURN_STATUSES = {TurnStatus.ACCEPTED, TurnStatus.QUEUED, TurnStatus.PROCESSING}
_TERMINAL_OPERATION_STATUSES = {
    ExternalJobStatus.SUCCEEDED,
    ExternalJobStatus.FAILED,
    ExternalJobStatus.TIMEOUT,
    ExternalJobStatus.EXPIRED,
}
_SAFE_FAILURE_REASON_CODES = frozenset(
    {
        "authorization_required",
        "contract_validation_failed",
        "handler_failed",
        "isolation_violation",
        "state_corrupted",
        "workflow_state_conflict",
    }
)


class TurnExecutionLeaseConflictError(RuntimeError):
    """Turn 执行租约已失效、被接管或续租时间没有变晚。"""


class VideoWorkflowStateConflictError(RuntimeError):
    """视频状态 CAS 版本或动作幂等摘要发生冲突。"""


class _FrozenJsonList(tuple[object, ...]):
    """保留 JSON 数组比较语义，同时拒绝原地修改。"""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, (list, tuple)) and tuple(self) == tuple(other)

    __hash__ = None


def _validate_json(value: object, ancestors: set[int]) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("JSON 数字必须是有限值")
        return
    if type(value) not in {dict, list} and not isinstance(
        value, (MappingProxyType, _FrozenJsonList)
    ):
        raise ValueError("只允许 JSON 原生值")
    identity = id(value)
    if identity in ancestors:
        raise ValueError("JSON 值不能包含循环引用")
    ancestors.add(identity)
    try:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if type(key) is not str:
                    raise ValueError("JSON 对象键必须是字符串")
                _validate_json(child, ancestors)
        else:
            for child in value:
                _validate_json(child, ancestors)
    finally:
        ancestors.remove(identity)


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, (list, _FrozenJsonList)):
        return [_thaw_json(child) for child in value]
    return value


def _json_copy(value: object, *, field_name: str) -> dict[str, JsonValue]:
    thawed = _thaw_json(value)
    if type(thawed) is not dict:
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    _validate_json(thawed, set())
    try:
        return json.loads(
            json.dumps(
                thawed,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是合法 JSON") from exc


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return _FrozenJsonList(_freeze_json(child) for child in value)
    return value


class _FrozenContractModel(ContractModel):
    """让 Repository DTO 在构造后保持顶层不可变并强制实例重验。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )


class _FrozenActionDecision(ActionDecision):
    """冻结公开决策中的 JSON 补丁，避免提交合同被调用方回写。"""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    @field_validator("patch", mode="before")
    @classmethod
    def copy_patch(cls, value: object) -> object:
        del cls
        return _json_copy(value, field_name="decision patch")

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "patch", _freeze_json(self.patch))

    @field_serializer("patch")
    def serialize_patch(self, value: object) -> object:
        return _thaw_json(value)


class _FrozenExternalJobRef(ExternalJobRef):
    """冻结 Workflow 内嵌的外部任务引用。"""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")


class _FrozenWorkflowRecord(WorkflowRecord):
    """冻结 Workflow 投影及其所有容器字段。"""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    @field_validator("creation_contract_snapshot", mode="before")
    @classmethod
    def copy_creation_contract(cls, value: object) -> object:
        del cls
        return _json_copy(value, field_name="Workflow creation contract")

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(
            self,
            "creation_contract_snapshot",
            _freeze_json(self.creation_contract_snapshot),
        )
        object.__setattr__(self, "latest_artifact_refs", tuple(self.latest_artifact_refs))
        if self.pending_external_job is not None:
            object.__setattr__(
                self,
                "pending_external_job",
                _FrozenExternalJobRef.model_validate(
                    self.pending_external_job.model_dump(mode="python")
                ),
            )

    @field_serializer("creation_contract_snapshot")
    def serialize_creation_contract(self, value: object) -> object:
        return _thaw_json(value)

    @field_serializer("latest_artifact_refs")
    def serialize_artifact_refs(self, value: object) -> object:
        return list(value)


class _FrozenTurnRecord(TurnRecord):
    """冻结 Turn 以及其中的公开决策。"""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    def model_post_init(self, context: object, /) -> None:
        del context
        if self.decision is not None:
            object.__setattr__(
                self,
                "decision",
                _FrozenActionDecision.model_validate(self.decision.model_dump(mode="python")),
            )

    @field_serializer("decision")
    def serialize_decision(self, value: ActionDecision | None) -> object:
        return None if value is None else value.model_dump(mode="python")


def _freeze_decision(value: ActionDecision) -> _FrozenActionDecision:
    return _FrozenActionDecision.model_validate(value.model_dump(mode="python"))


def _freeze_workflow(value: WorkflowRecord) -> _FrozenWorkflowRecord:
    return _FrozenWorkflowRecord.model_validate(value.model_dump(mode="python"))


def _freeze_turn(value: TurnRecord) -> _FrozenTurnRecord:
    return _FrozenTurnRecord.model_validate(value.model_dump(mode="python"))


class TurnExecutionClaim(_FrozenContractModel):
    """携带 fencing token 的一次 Turn 执行所有权。"""

    user_id: str = Field(min_length=1)
    turn: TurnRecord
    lease_owner: str = Field(min_length=1)
    lease_token: UUID
    lease_expires_at: datetime
    attempt: int = Field(ge=1)

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "turn", _freeze_turn(self.turn))

    @field_serializer("turn")
    def serialize_turn(self, value: TurnRecord) -> object:
        return value.model_dump(mode="python")


class SupervisorProjectionMessage(_FrozenContractModel):
    """Supervisor 写入 Snapshot 和 SSE 的权威消息投影。"""

    message_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    role: Literal["assistant", "system"]
    content: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def copy_payload(cls, value: object) -> object:
        del cls
        return _json_copy(value, field_name="消息 payload")

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @field_serializer("payload")
    def serialize_payload(self, value: object) -> object:
        return _thaw_json(value)


class StoredAgentInterrupt(AgentInterruptProjection):
    """包含 Graph 恢复定位和幂等人工响应的持久化 interrupt。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        str_strip_whitespace=True,
    )

    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    checkpoint_ns: str
    status: Literal["open", "responded", "closed"] = "open"
    response_id: UUID | None = None
    response: dict[str, JsonValue] | None = None
    closed_at: datetime | None = None

    @model_validator(mode="after")
    def require_workflow_identity(self):
        if self.kind == "clarification":
            if self.workflow_id is not None:
                raise ValueError("全局 clarification 不能伪造 workflow_id")
            if self.checkpoint_ns != "":
                raise ValueError("全局 clarification 必须绑定 Supervisor 根 checkpoint")
        elif not self.workflow_id or not self.checkpoint_ns:
            raise ValueError("非全局 interrupt 必须携带 workflow_id 与 checkpoint_ns")
        return self

    @field_validator("payload", mode="before")
    @classmethod
    def copy_interrupt_payload(cls, value: object) -> object:
        del cls
        return _json_copy(value, field_name="interrupt payload")

    @field_validator("response", mode="before")
    @classmethod
    def copy_interrupt_response(cls, value: object) -> object:
        del cls
        return None if value is None else _json_copy(value, field_name="interrupt response")

    @model_validator(mode="after")
    def validate_response_transition(self):
        has_response = self.response_id is not None and self.response is not None
        if (self.response_id is None) != (self.response is None):
            raise ValueError("interrupt response_id 与 response 必须同时存在")
        if self.status == "open" and has_response:
            raise ValueError("open interrupt 不能携带 response")
        if self.status == "responded" and not has_response:
            raise ValueError("responded interrupt 必须携带 response")
        if self.status == "closed" and self.closed_at is None:
            raise ValueError("closed interrupt 必须携带 closed_at")
        if self.status != "closed" and self.closed_at is not None:
            raise ValueError("未关闭 interrupt 不能携带 closed_at")
        return self

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "payload", _freeze_json(self.payload))
        if self.response is not None:
            object.__setattr__(self, "response", _freeze_json(self.response))

    @field_serializer("payload", "response")
    def serialize_json_fields(self, value: object) -> object:
        return _thaw_json(value)


@dataclass(frozen=True, slots=True)
class InterruptResponseRegistration:
    """原 Turn 上一次人工响应登记或幂等回读的权威结果。"""

    interrupt: StoredAgentInterrupt
    turn: TurnRecord
    message: PixelFlowConversationMessageRecord
    context_version: int
    created: bool


class OwnedTurnRecord(_FrozenContractModel):
    """恢复扫描使用的所有者与排期快照。"""

    user_id: str = Field(min_length=1)
    turn: TurnRecord
    next_attempt_at: datetime | None = None

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "turn", _freeze_turn(self.turn))

    @field_serializer("turn")
    def serialize_turn(self, value: TurnRecord) -> object:
        return value.model_dump(mode="python")


class VideoTurnCommit(_FrozenContractModel):
    """一次 Graph 结果对应的完整原子业务提交。"""

    decision: ActionDecision
    turn_status: Literal[TurnStatus.WAITING_USER, TurnStatus.COMPLETED, TurnStatus.FAILED]
    workflow_state: VideoWorkflowStateEnvelope | None = None
    workflow: WorkflowRecord | None = None
    expected_workflow_version: int = Field(ge=0)
    messages: tuple[SupervisorProjectionMessage, ...] = ()
    open_interrupt: StoredAgentInterrupt | None = None
    close_interrupt_id: str | None = Field(default=None, min_length=1)
    operation_event_claim: EventDeliveryClaim | None = None
    update_active_workflow: bool = False
    active_workflow_id: str | None = Field(default=None, min_length=1)
    error_reason_code: str | None = None
    occurred_at: datetime

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "decision", _freeze_decision(self.decision))
        if self.workflow is not None:
            object.__setattr__(self, "workflow", _freeze_workflow(self.workflow))
        if self.operation_event_claim is not None:
            from pixelflow.agent_runtime.jobs.quota import _freeze_claim

            object.__setattr__(
                self,
                "operation_event_claim",
                _freeze_claim(self.operation_event_claim),
            )

    @field_serializer("decision")
    def serialize_decision(self, value: ActionDecision) -> object:
        return value.model_dump(mode="python")

    @field_serializer("workflow")
    def serialize_workflow(self, value: WorkflowRecord | None) -> object:
        return None if value is None else value.model_dump(mode="python")

    @field_serializer("operation_event_claim")
    def serialize_operation_event_claim(
        self,
        value: EventDeliveryClaim | None,
        info: SerializationInfo,
    ) -> object:
        """把深只读 claim 恢复为普通 checkpoint JSON。"""

        return None if value is None else value.model_dump(mode=info.mode)

    @model_validator(mode="after")
    def validate_atomic_shape(self):
        if (self.workflow_state is None) != (self.workflow is None):
            raise ValueError("workflow_state 与 workflow 必须同时存在或同时为空")
        if self.turn_status is TurnStatus.WAITING_USER:
            if self.open_interrupt is None or self.open_interrupt.status != "open":
                raise ValueError("waiting_user 必须打开 open interrupt")
        elif self.open_interrupt is not None:
            raise ValueError("Turn 终态不能留下 open interrupt")
        if self.turn_status is TurnStatus.FAILED:
            if self.error_reason_code not in _SAFE_FAILURE_REASON_CODES:
                raise ValueError("failed Turn 只能保存固定安全 reason code")
        elif self.error_reason_code is not None:
            raise ValueError("非 failed Turn 不能保存 error_reason_code")
        if not self.update_active_workflow and self.active_workflow_id is not None:
            raise ValueError("未更新 active workflow 时不能携带 active_workflow_id")
        if self.operation_event_claim is not None:
            from pixelflow.agent_runtime.jobs import (
                OperationQuotaEventPayload,
                OperationQuotaState,
            )

            payload = OperationQuotaEventPayload.model_validate(
                self.operation_event_claim.event.payload
            )
            if (
                self.operation_event_claim.event.type
                is not AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
                or payload.quota_state is not OperationQuotaState.RESUMED
                or self.turn_status is not TurnStatus.COMPLETED
                or self.workflow_state is None
                or self.workflow is None
                or self.open_interrupt is not None
                or self.close_interrupt_id is None
                or self.workflow_state.last_action_key
                != self.operation_event_claim.event.event_id
                or self.workflow_state.workflow_id != payload.workflow_id
                or self.workflow.workflow_id != payload.workflow_id
            ):
                raise ValueError("Operation Event claim 不是完整 quota resume 提交")
        return self


class VideoRuntimeSafeSnapshot(_FrozenContractModel):
    """可稳定 JSON 序列化且不暴露 Repository 可变别名的快照。"""

    conversation_id: str = Field(min_length=1)
    active_workflow_id: str | None = Field(default=None, min_length=1)
    workflow_states: tuple[VideoWorkflowStateEnvelope, ...] = ()
    workflows: tuple[WorkflowRecord, ...] = ()
    turns: tuple[OwnedTurnRecord, ...] = ()
    messages: tuple[SupervisorProjectionMessage, ...] = ()
    interrupts: tuple[StoredAgentInterrupt, ...] = ()

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "workflows", tuple(_freeze_workflow(item) for item in self.workflows))
        object.__setattr__(
            self,
            "turns",
            tuple(
                OwnedTurnRecord(
                    user_id=item.user_id,
                    turn=_freeze_turn(item.turn),
                    next_attempt_at=item.next_attempt_at,
                )
                for item in self.turns
            ),
        )

    @field_serializer("workflows")
    def serialize_workflows(self, value: tuple[WorkflowRecord, ...]) -> object:
        return [item.model_dump(mode="python") for item in value]

    @field_serializer("turns")
    def serialize_turns(self, value: tuple[OwnedTurnRecord, ...]) -> object:
        return [item.model_dump(mode="python") for item in value]


class _FrozenContextTaskMessage(_FrozenContractModel):
    """冻结任务 Store 消息在 Context 读取边界上的公开投影。"""

    message_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    user_id: str | None = None
    role: str = Field(min_length=1)
    content: str = ""
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: str = ""

    @field_validator("payload", mode="before")
    @classmethod
    def copy_payload(cls, value: object) -> object:
        del cls
        return _json_copy(value, field_name="Context 消息 payload")

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @field_serializer("payload")
    def serialize_payload(self, value: object) -> object:
        return _thaw_json(value)


class _FrozenContextSummary(ContextSummary):
    """冻结摘要的列表与状态映射，同时保留 ContextSummary 公共合同。"""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    def model_post_init(self, context: object, /) -> None:
        del context
        for field_name in (
            "user_goals",
            "confirmed_decisions",
            "negative_constraints",
            "unresolved_questions",
            "artifact_evidence_refs",
            "covered_message_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _FrozenJsonList(getattr(self, field_name)),
            )
        object.__setattr__(
            self,
            "workflow_states",
            MappingProxyType(dict(self.workflow_states)),
        )

    @field_serializer(
        "user_goals",
        "confirmed_decisions",
        "negative_constraints",
        "unresolved_questions",
        "artifact_evidence_refs",
        "covered_message_ids",
    )
    def serialize_string_list(self, value: object) -> object:
        return list(value)  # type: ignore[arg-type]

    @field_serializer("workflow_states")
    def serialize_workflow_states(self, value: object) -> object:
        return dict(value)  # type: ignore[arg-type]


class _FrozenContextEvent(AgentEvent):
    """冻结 Context 版本裁剪依赖的事件与嵌套 payload。"""

    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    @field_validator("payload", mode="before")
    @classmethod
    def copy_payload(cls, value: object) -> object:
        del cls
        return _json_copy(value, field_name="Context 事件 payload")

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @field_serializer("payload")
    def serialize_payload(self, value: object) -> object:
        return _thaw_json(value)


class VideoRuntimeContextSnapshot(_FrozenContractModel):
    """同一 Memory 临界区或 SQL 事务读取的完整深只读 Context 原料。"""

    conversation_id: str = Field(min_length=1)
    expected_context_version: int = Field(ge=0)
    current_context_version: int = Field(ge=0)
    runtime: VideoRuntimeSafeSnapshot
    task_messages: tuple[_FrozenContextTaskMessage, ...]
    summaries: tuple[ContextSummary, ...]
    events: tuple[AgentEvent, ...]

    @field_validator("task_messages", mode="before")
    @classmethod
    def copy_task_messages(cls, value: object) -> object:
        del cls
        if not isinstance(value, (list, tuple)):
            raise ValueError("Context 消息必须是有序集合")
        return tuple(
            item.to_dict()
            if isinstance(item, PixelFlowConversationMessageRecord)
            else item
            for item in value
        )

    def model_post_init(self, context: object, /) -> None:
        del context
        if self.current_context_version < self.expected_context_version:
            raise ValueError("Context Snapshot 版本边界非法")
        object.__setattr__(
            self,
            "summaries",
            tuple(
                _FrozenContextSummary.model_validate(item.model_dump(mode="python"))
                for item in self.summaries
            ),
        )
        object.__setattr__(
            self,
            "events",
            tuple(
                _FrozenContextEvent.model_validate(item.model_dump(mode="python"))
                for item in self.events
            ),
        )

    @field_serializer("runtime")
    def serialize_runtime(self, value: VideoRuntimeSafeSnapshot) -> object:
        return value.model_dump(mode="python")

    @field_serializer("task_messages")
    def serialize_task_messages(
        self,
        value: tuple[_FrozenContextTaskMessage, ...],
    ) -> object:
        return [item.model_dump(mode="python") for item in value]

    @field_serializer("summaries")
    def serialize_summaries(self, value: tuple[ContextSummary, ...]) -> object:
        return [item.model_dump(mode="python") for item in value]

    @field_serializer("events")
    def serialize_events(self, value: tuple[AgentEvent, ...]) -> object:
        return [item.model_dump(mode="python") for item in value]


class VideoRuntimeContextSnapshotConflictError(RuntimeError):
    """请求版本无法从当前权威记录安全还原。"""


@runtime_checkable
class VideoRuntimeRepository(Protocol):
    """约束 live 视频执行的 Memory/SQL 同构语义。"""

    async def claim_turn(
        self,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim | None: ...

    async def list_due_turns(self, *, now: datetime, limit: int = 100) -> list[OwnedTurnRecord]: ...

    async def list_due_interrupt_responses(
        self, *, now: datetime, limit: int = 100
    ) -> list[StoredAgentInterrupt]: ...

    async def claim_interrupt_resume(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim | None: ...

    async def heartbeat_turn(
        self,
        claim: TurnExecutionClaim,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim: ...

    async def reschedule_turn(
        self,
        claim: TurnExecutionClaim,
        *,
        now: datetime,
        next_attempt_at: datetime,
        reason_code: str,
    ) -> TurnRecord: ...

    async def commit_turn(self, claim: TurnExecutionClaim, commit: VideoTurnCommit) -> TurnRecord: ...

    async def get_video_state(
        self, user_id: str, workflow_id: str
    ) -> VideoWorkflowStateEnvelope | None: ...

    async def list_projection_messages(
        self, user_id: str, conversation_id: str
    ) -> list[SupervisorProjectionMessage]: ...

    async def get_open_interrupt(
        self, user_id: str, conversation_id: str
    ) -> StoredAgentInterrupt | None: ...

    async def get_interrupt(
        self, user_id: str, interrupt_id: str
    ) -> StoredAgentInterrupt | None: ...

    async def store_interrupt_response(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        client_response_id: UUID,
        response_value: dict[str, JsonValue],
        responded_at: datetime,
    ) -> StoredAgentInterrupt: ...

    async def register_interrupt_response(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        request: InterruptResponseRequest,
        message: PixelFlowConversationMessageRecord,
        responded_at: datetime,
    ) -> InterruptResponseRegistration: ...

    async def get_active_workflow_id(
        self, user_id: str, conversation_id: str
    ) -> str | None: ...

    async def export_safe_snapshot(
        self, user_id: str, conversation_id: str
    ) -> VideoRuntimeSafeSnapshot: ...

    async def read_versioned_context_snapshot(
        self,
        user_id: str,
        conversation_id: str,
        *,
        expected_context_version: int,
    ) -> VideoRuntimeContextSnapshot: ...

    async def commit_operation_completion(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        messages: tuple[SupervisorProjectionMessage, ...],
        open_interrupt: StoredAgentInterrupt | None,
        occurred_at: datetime,
    ) -> WorkflowRecord: ...

    async def commit_operation_quota_state(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        open_interrupt: StoredAgentInterrupt | None,
        close_interrupt_revision: int | None,
        occurred_at: datetime,
    ) -> WorkflowRecord: ...


@dataclass
class _MemoryTurnExecution:
    attempt: int = 0
    lease_owner: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_reason_code: str | None = None


def _normalize_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ValueError("limit 必须是 1 到 1000 的整数")
    return limit


def _lease_window(
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
) -> tuple[str, datetime, datetime]:
    owner = _require_text("lease_owner", lease_owner, 128)
    normalized_now = _normalize_datetime("now", now)
    normalized_expiry = _normalize_datetime("lease_expires_at", lease_expires_at)
    if normalized_expiry <= normalized_now:
        raise ValueError("lease_expires_at 必须晚于 now")
    return owner, normalized_now, normalized_expiry


@asynccontextmanager
async def _repository_snapshot_transaction(
    session: AsyncSession,
    sqlite_write_lock: Lock | None,
) -> AsyncIterator[None]:
    """为 Snapshot 建立 SQLite 串行边界或 SQL REPEATABLE READ 一致读。"""

    if session.get_bind().dialect.name == "sqlite":
        async with _repository_write_transaction(session, sqlite_write_lock):
            yield
        return
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    try:
        yield
    except BaseException:
        await session.rollback()
        raise
    else:
        await session.commit()


def _clone_message(message: SupervisorProjectionMessage) -> SupervisorProjectionMessage:
    return SupervisorProjectionMessage.model_validate(message.model_dump(mode="python"))


def _clone_interrupt(interrupt: StoredAgentInterrupt) -> StoredAgentInterrupt:
    return StoredAgentInterrupt.model_validate(interrupt.model_dump(mode="python"))


def _clone_state(state: VideoWorkflowStateEnvelope) -> VideoWorkflowStateEnvelope:
    return VideoWorkflowStateEnvelope.model_validate(state.model_dump(mode="python"))


def _clone_claim(claim: TurnExecutionClaim) -> TurnExecutionClaim:
    return TurnExecutionClaim.model_validate(claim.model_dump(mode="python"))


def _stable_event_identity(*parts: str) -> tuple[str, str]:
    key = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    identity = uuid5(NAMESPACE_URL, f"pixelflow-video-runtime-event:{key}").hex
    return f"evt_{identity}", f"cursor_{identity}"


def _normalize_interrupt_response(
    request: InterruptResponseRequest,
) -> InterruptResponseRequest:
    """重新校验可能被调用方绕过 frozen 约束污染的响应 DTO。"""

    return InterruptResponseRequest.model_validate(
        request.model_dump(mode="python"),
    )


def _response_document(
    request: InterruptResponseRequest,
    *,
    pre_input_context_version: int | None = None,
) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {
        "client_response_id": str(request.client_response_id),
        "value": request.value.model_dump(mode="json"),
    }
    if pre_input_context_version is not None:
        document["pre_input_context_version"] = pre_input_context_version
    return document


def _interrupt_matches_response(
    interrupt: StoredAgentInterrupt,
    request: InterruptResponseRequest,
) -> bool:
    response = interrupt.response
    return (
        interrupt.response_id == request.client_response_id
        and response is not None
        and response.get("client_response_id") == str(request.client_response_id)
        and response.get("value") == request.value.model_dump(mode="json")
    )


def _response_context_version(interrupt: StoredAgentInterrupt) -> int:
    response = interrupt.response
    pre_input = None if response is None else response.get("pre_input_context_version")
    if isinstance(pre_input, bool) or not isinstance(pre_input, int) or pre_input < 0:
        raise AgentRuntimeRecordConflictError("interrupt 响应缺少合法快照身份")
    return pre_input + 1


def _validate_response_message(
    *,
    user_id: str,
    conversation_id: str,
    interrupt_id: str,
    request: InterruptResponseRequest,
    message: PixelFlowConversationMessageRecord,
) -> PixelFlowConversationMessageRecord:
    """只允许登记由响应合同确定性派生的公开用户消息。"""

    normalized = deepcopy(message)
    value = request.value.model_dump(mode="json")
    expected_payload = {
        "client_message_id": str(request.client_response_id),
        "interrupt_id": interrupt_id,
        "value": value,
        "explicit_action": value.get("explicit_action"),
    }
    if (
        normalized.message_id
        != conversation_message_id(conversation_id, request.client_response_id)
        or normalized.conversation_id != conversation_id
        or normalized.user_id != user_id
        or normalized.role != "user"
        or normalized.content != request.value.content
        or normalized.payload != expected_payload
    ):
        raise AgentRuntimeRecordConflictError("interrupt 响应消息身份或内容不一致")
    return normalized


def _response_message_matches(
    stored: PixelFlowConversationMessageRecord,
    expected: PixelFlowConversationMessageRecord,
) -> bool:
    """幂等回读忽略重试请求产生的新时间戳，只比较公开响应语义。"""

    return all(
        getattr(stored, field) == getattr(expected, field)
        for field in (
            "message_id",
            "conversation_id",
            "user_id",
            "role",
            "content",
            "payload",
        )
    )


def _response_event_specs(
    *,
    interrupt: StoredAgentInterrupt,
    turn: TurnRecord,
    message: PixelFlowConversationMessageRecord,
    client_response_id: UUID,
) -> tuple[tuple[AgentEventType, dict[str, JsonValue], str], ...]:
    """生成 Memory/SQL 共用的四类响应登记事件。"""

    response_id = str(client_response_id)
    return (
        (
            AgentEventType.INTERRUPT_RESPONDED,
            {
                "interrupt_id": interrupt.interrupt_id,
                "response_id": response_id,
            },
            interrupt.interrupt_id,
        ),
        (
            AgentEventType.MESSAGE_UPSERTED,
            {"message": message.to_dict()},
            message.message_id,
        ),
        (
            AgentEventType.INPUT_STATE_CHANGED,
            {
                "turn_id": turn.turn_id,
                "status": TurnStatus.WAITING_USER.value,
                "response_id": response_id,
            },
            turn.turn_id,
        ),
        (
            AgentEventType.RUN_STATE_CHANGED,
            {
                "run_id": turn.turn_id,
                "status": TurnStatus.WAITING_USER.value,
            },
            turn.turn_id,
        ),
    )


def _conversation_message_from_row(
    row: PixelFlowConversationMessageRow,
) -> PixelFlowConversationMessageRecord:
    return PixelFlowConversationMessageRecord(
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        role=row.role,
        content=row.content,
        payload=deepcopy(row.payload_json or {}),
        created_at=_database_utc(row.created_at).isoformat(),
    )


def _sql_runtime_context_version(row: PixelFlowConversationRow) -> int:
    context = row.context_json or {}
    runtime = context.get(AGENT_RUNTIME_CONTEXT_KEY)
    value = None if not isinstance(runtime, dict) else runtime.get("context_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentRuntimeRecordConflictError(
            "Agent Runtime 响应缺少合法上下文版本",
        )
    return value


def _runtime_context_version(context: object) -> int:
    runtime = (
        context.get(AGENT_RUNTIME_CONTEXT_KEY)
        if isinstance(context, Mapping)
        else None
    )
    value = runtime.get("context_version") if isinstance(runtime, Mapping) else None
    if type(value) is not int or value < 0:
        raise VideoRuntimeContextSnapshotConflictError(
            "context_snapshot_version_conflict"
        )
    return value


def _conversation_is_video_live(record: object | None) -> bool:
    if record is None or getattr(record, "orchestration_mode", None) != "supervisor_v1":
        return False
    if getattr(record, "orchestration_version", None) != 1:
        return False
    context = getattr(record, "context", None)
    runtime = context.get(AGENT_RUNTIME_CONTEXT_KEY) if isinstance(context, dict) else None
    return (
        isinstance(runtime, dict)
        and runtime.get("primary_execution_ready") is True
        and isinstance(runtime.get("enabled_intents"), list)
        and "video" in runtime["enabled_intents"]
    )


def _validate_commit_contract(
    claim: TurnExecutionClaim,
    commit: VideoTurnCommit,
) -> tuple[TurnExecutionClaim, VideoTurnCommit]:
    normalized_claim = TurnExecutionClaim.model_validate(claim.model_dump(mode="python"))
    normalized_commit = VideoTurnCommit.model_validate(commit.model_dump(mode="python"))
    operation_event_claim = normalized_commit.operation_event_claim
    expected_action_key = normalized_commit.decision.idempotency_key
    if operation_event_claim is not None:
        from pixelflow.agent_runtime.contracts import AgentAction, AgentIntent
        from pixelflow.agent_runtime.jobs import (
            OperationQuotaEventPayload,
            OperationQuotaState,
            build_operation_quota_event_id,
        )

        event = operation_event_claim.event
        payload = OperationQuotaEventPayload.model_validate(event.payload)
        expected_patch = {
            "job_id": payload.job_id,
            "quota_pause_revision": payload.quota_pause_revision,
        }
        decision = normalized_commit.decision
        if (
            event.type is not AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
            or event.conversation_id != normalized_claim.turn.conversation_id
            or event.event_id
            != build_operation_quota_event_id(
                payload.job_id,
                payload.quota_pause_revision,
                payload.quota_state,
            )
            or payload.quota_state is not OperationQuotaState.RESUMED
            or decision.action is not AgentAction.RETRY_FAILED
            or decision.intent is not AgentIntent.VIDEO
            or decision.target_workflow_id != payload.workflow_id
            or decision.target_stage != payload.stage
            or decision.target_artifact_ref is not None
            or decision.patch != expected_patch
        ):
            raise AgentRuntimeRecordConflictError(
                "非授权恢复动作携带了 Operation Event claim",
            )
        expected_action_key = event.event_id
    for message in normalized_commit.messages:
        if message.conversation_id != normalized_claim.turn.conversation_id:
            raise AgentRuntimeRecordConflictError("消息不属于当前 Turn 会话")
    interrupt = normalized_commit.open_interrupt
    if interrupt is not None and (
        interrupt.user_id != normalized_claim.user_id
        or interrupt.conversation_id != normalized_claim.turn.conversation_id
        or interrupt.turn_id != normalized_claim.turn.turn_id
    ):
        raise AgentRuntimeRecordConflictError("interrupt 不属于当前 Turn")
    state = normalized_commit.workflow_state
    workflow = normalized_commit.workflow
    if state is not None and workflow is not None:
        if state.user_id != normalized_claim.user_id:
            raise AgentRuntimeRecordConflictError("视频状态不属于当前用户")
        if state.conversation_id != normalized_claim.turn.conversation_id:
            raise AgentRuntimeRecordConflictError("视频状态不属于当前会话")
        if state.last_turn_id != normalized_claim.turn.turn_id:
            raise AgentRuntimeRecordConflictError("视频状态 last_turn_id 不属于当前 Turn")
        if state.last_action_key != expected_action_key:
            raise AgentRuntimeRecordConflictError("视频状态动作键与 decision 不一致")
        if canonical_video_workflow_envelope_sha256(state) != state.payload_sha256:
            raise VideoWorkflowStateConflictError("视频状态完整信封摘要不一致")
        decoded = decode_video_workflow_state(state)
        authority = project_video_workflow_state(decoded)
        if authority.model_dump(mode="json") != workflow.model_dump(mode="json"):
            raise VideoWorkflowStateConflictError("视频状态与 Workflow 投影不一致")
        if workflow.workflow_id != state.workflow_id or workflow.context_version != state.context_version:
            raise VideoWorkflowStateConflictError("视频状态与 Workflow 身份或 context 不一致")
    return normalized_claim, normalized_commit


def _validate_operation_completion_binding(
    *,
    user_id: str,
    event: AgentEvent,
    operation: OperationRecord | None,
    workflow_state: VideoWorkflowStateEnvelope,
    workflow: WorkflowRecord,
) -> None:
    """把完成事件、Operation 与目标视频状态绑定为同一权威身份。"""

    job_id = event.payload.get("job_id")
    event_status = event.payload.get("status")
    if (
        type(job_id) is not str
        or operation is None
        or operation.job_id != job_id
        or operation.status not in _TERMINAL_OPERATION_STATUSES
        or event.type is not AgentEventType.EXTERNAL_JOB_STATE_CHANGED
        or event_status != operation.status.value
        or event.conversation_id != operation.conversation_id
        or workflow_state.user_id != user_id
        or workflow_state.conversation_id != operation.conversation_id
        or workflow.conversation_id != operation.conversation_id
        or workflow_state.workflow_id != operation.workflow_id
        or workflow.workflow_id != operation.workflow_id
    ):
        raise AgentRuntimeRecordConflictError("Operation 完成事件与目标 Workflow 身份不一致")


def _validate_operation_quota_resume_binding(
    *,
    user_id: str,
    event: AgentEvent,
    operation: OperationRecord | None,
    workflow_state: VideoWorkflowStateEnvelope,
    workflow: WorkflowRecord,
) -> None:
    """把当前授权 Turn 的 resume claim 绑定到同一原 Operation。"""

    from pixelflow.agent_runtime.jobs import (
        OperationQuotaEventPayload,
        OperationQuotaState,
        build_operation_quota_event_id,
    )

    payload = OperationQuotaEventPayload.model_validate(event.payload)
    if (
        operation is None
        or event.type is not AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED
        or payload.quota_state is not OperationQuotaState.RESUMED
        or event.event_id
        != build_operation_quota_event_id(
            payload.job_id,
            payload.quota_pause_revision,
            payload.quota_state,
        )
        or event.conversation_id != operation.conversation_id
        or payload.job_id != operation.job_id
        or payload.workflow_id != operation.workflow_id
        or payload.stage != operation.stage
        or payload.stage_version != operation.stage_version
        or payload.attempt != operation.attempt
        or payload.quota_pause_revision != operation.quota_pause_revision
        or operation.status is not ExternalJobStatus.POLLING
        or operation.provider_job_id is None
        or operation.next_poll_at is None
        or workflow_state.user_id != user_id
        or workflow_state.conversation_id != operation.conversation_id
        or workflow.conversation_id != operation.conversation_id
        or workflow_state.workflow_id != operation.workflow_id
        or workflow.workflow_id != operation.workflow_id
        or workflow_state.last_action_key != event.event_id
    ):
        raise AgentRuntimeRecordConflictError(
            "Operation quota resume claim 与目标 Workflow 身份不一致",
        )


def _quota_projection_commit(
    *,
    claim: EventDeliveryClaim,
    user_id: str,
    workflow_state: VideoWorkflowStateEnvelope,
    workflow: WorkflowRecord,
    expected_workflow_version: int,
    open_interrupt: StoredAgentInterrupt | None,
    close_interrupt_id: str | None,
    turn_status: TurnStatus,
    occurred_at: datetime,
) -> tuple[TurnExecutionClaim, VideoTurnCommit]:
    """只为 quota Repository 临界区构造不依赖真实 Turn 租约的提交目标。"""

    synthetic_claim = TurnExecutionClaim(
        user_id=user_id,
        turn=TurnRecord(
            turn_id=workflow_state.last_turn_id,
            conversation_id=workflow_state.conversation_id,
            client_input_id=UUID(int=0),
            status=TurnStatus.PROCESSING,
            target_workflow_id=workflow_state.workflow_id,
            decision=None,
            expected_context_version=0,
            created_at=workflow_state.created_at,
        ),
        lease_owner=claim.lease_owner,
        lease_token=UUID(int=0),
        lease_expires_at=claim.lease_expires_at,
        attempt=1,
    )
    commit = VideoTurnCommit(
        decision=ActionDecision(
            action="continue_workflow",
            intent="video",
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            confidence=1,
            reason_code="operation_quota_state",
            idempotency_key=claim.event.event_id,
        ),
        turn_status=turn_status,
        workflow_state=workflow_state,
        workflow=workflow,
        expected_workflow_version=expected_workflow_version,
        messages=(),
        open_interrupt=open_interrupt,
        close_interrupt_id=close_interrupt_id,
        occurred_at=occurred_at,
    )
    return synthetic_claim, commit


def _matches_quota_authorization_interrupt(
    interrupt: StoredAgentInterrupt,
    *,
    operation: OperationRecord,
    quota_pause_revision: int,
) -> bool:
    """按 job 与 pause revision 精确识别可关闭的授权中断。"""

    return (
        interrupt.conversation_id == operation.conversation_id
        and interrupt.workflow_id == operation.workflow_id
        and interrupt.kind == "authorization_required"
        and interrupt.reason_code == "authorization_required"
        and interrupt.payload
        == {
            "workflow_id": operation.workflow_id,
            "stage": operation.stage,
            "authorization_action": {
                "action": "retry_failed",
                "intent": "video",
                "workflow_id": operation.workflow_id,
                "stage": operation.stage,
                "artifact_ref": None,
                "patch": {
                    "job_id": operation.job_id,
                    "quota_pause_revision": quota_pause_revision,
                },
            },
        }
    )


def _video_turn_commit_identity(commit: VideoTurnCommit) -> str:
    """生成只保存于 execution 元数据的完整提交身份，不进入公开投影。"""

    canonical = json.dumps(
        commit.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).rstrip(b"=")
    return f"commit:{digest.decode('ascii')}"


def _event(
    *,
    sequence: int,
    conversation_id: str,
    run_id: str,
    occurred_at: datetime,
    event_type: AgentEventType,
    payload: dict[str, JsonValue],
    identity_parts: tuple[str, ...],
) -> AgentEvent:
    event_id, cursor = _stable_event_identity(*identity_parts)
    return AgentEvent(
        event_id=event_id,
        sequence=sequence,
        cursor=cursor,
        conversation_id=conversation_id,
        run_id=run_id,
        occurred_at=occurred_at,
        type=event_type,
        payload=payload,
    )


class MemoryVideoRuntimeRepository(MemoryCompactionQueueRepository):
    """复用压缩锁，在一个可回滚临界区内提交全部 live 投影。"""

    def __init__(
        self,
        *,
        task_store: MemoryPixelFlowTaskStore,
        completion_clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        self._task_store = task_store
        self._completion_clock = completion_clock or (
            lambda: datetime.now(UTC)
        )
        self._video_states: dict[tuple[str, str], VideoWorkflowStateEnvelope] = {}
        self._turn_executions: dict[tuple[str, str], _MemoryTurnExecution] = {}
        self._projection_messages: dict[tuple[str, str], SupervisorProjectionMessage] = {}
        self._interrupts: dict[tuple[str, str], StoredAgentInterrupt] = {}
        self._active_workflows: dict[tuple[str, str], str | None] = {}

    async def create_workflow(
        self,
        user_id: str,
        record: WorkflowRecord,
    ) -> WorkflowRecord:
        """让独立 Workflow 写入与 Context 一次性读取共享 Repository 锁。"""

        async with self._compaction_write_lock:
            return await super().create_workflow(user_id, record)

    async def create_summary(
        self,
        user_id: str,
        record: ContextSummary,
    ) -> ContextSummary:
        """让独立摘要写入与 Context 一次性读取共享 Repository 锁。"""

        async with self._compaction_write_lock:
            return await super().create_summary(user_id, record)

    async def _eligible(self, user_id: str, conversation_id: str) -> bool:
        conversation = await self._task_store.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        return _conversation_is_video_live(conversation)

    def _owner_turn_keys(self, user_id: str, conversation_id: str) -> list[tuple[str, str]]:
        keys = [
            key
            for key, turn in self._turns.items()
            if key[0] == user_id and turn.conversation_id == conversation_id
        ]
        keys.sort(key=self._turn_owner_sequences.__getitem__)
        return keys

    def _compaction_due(self, user_id: str, conversation_id: str, now: datetime) -> bool:
        state = self._compaction_leases.get(conversation_id)
        if state is None:
            return True
        return state[0] == user_id and state[1].lease_expires_at <= now

    def _claim_turn_locked(
        self,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        *,
        worker: str,
        now: datetime,
        expires_at: datetime,
        interrupt_resume: bool = False,
    ) -> TurnExecutionClaim | None:
        if not self._compaction_due(user_id, conversation_id, now):
            return None
        keys = self._owner_turn_keys(user_id, conversation_id)
        active = [key for key in keys if self._turns[key].status not in _TERMINAL_TURN_STATUSES]
        if not active or active[0][1] != turn_id:
            return None
        key = active[0]
        turn = self._turns[key]
        if interrupt_resume:
            if turn.status is not TurnStatus.WAITING_USER:
                return None
        elif turn.status not in _CLAIMABLE_TURN_STATUSES:
            return None
        execution = self._turn_executions.get(key)
        if execution is not None and execution.next_attempt_at is not None and execution.next_attempt_at > now:
            return None
        if execution is None:
            execution = _MemoryTurnExecution()
            self._turn_executions[key] = execution
        if execution.lease_owner is not None and execution.lease_expires_at is not None:
            if execution.lease_expires_at > now:
                if execution.lease_owner != worker or execution.lease_token is None:
                    return None
                return TurnExecutionClaim(
                    user_id=user_id,
                    turn=_clone(turn),
                    lease_owner=worker,
                    lease_token=execution.lease_token,
                    lease_expires_at=execution.lease_expires_at,
                    attempt=execution.attempt,
                )
        execution.attempt += 1
        execution.lease_owner = worker
        execution.lease_token = uuid4()
        execution.lease_expires_at = expires_at
        execution.next_attempt_at = None
        self._turns[key] = _clone(turn.model_copy(update={"status": TurnStatus.PROCESSING}))
        return TurnExecutionClaim(
            user_id=user_id,
            turn=_clone(self._turns[key]),
            lease_owner=worker,
            lease_token=execution.lease_token,
            lease_expires_at=expires_at,
            attempt=execution.attempt,
        )

    async def claim_turn(
        self,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("turn_id", turn_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(lease_owner, now, lease_expires_at)
        if not await self._eligible(owner, conversation):
            return None
        async with self._compaction_write_lock:
            return self._claim_turn_locked(
                owner,
                conversation,
                identity,
                worker=worker,
                now=normalized_now,
                expires_at=normalized_expiry,
            )

    async def list_due_turns(self, *, now: datetime, limit: int = 100) -> list[OwnedTurnRecord]:
        normalized_now = _normalize_datetime("now", now)
        page_size = _normalize_limit(limit)
        async with self._compaction_write_lock:
            candidates: list[tuple[int, str, str, TurnRecord, datetime | None]] = []
            conversations = sorted(
                {(owner, turn.conversation_id) for (owner, _), turn in self._turns.items()}
            )
            for owner, conversation in conversations:
                if not self._compaction_due(owner, conversation, normalized_now):
                    continue
                keys = self._owner_turn_keys(owner, conversation)
                active = [key for key in keys if self._turns[key].status not in _TERMINAL_TURN_STATUSES]
                if not active:
                    continue
                key = active[0]
                turn = self._turns[key]
                execution = self._turn_executions.get(key)
                next_attempt = None if execution is None else execution.next_attempt_at
                due = turn.status is TurnStatus.ACCEPTED
                if turn.status is TurnStatus.QUEUED:
                    due = next_attempt is None or next_attempt <= normalized_now
                elif turn.status is TurnStatus.PROCESSING:
                    due = (
                        execution is not None
                        and execution.lease_expires_at is not None
                        and execution.lease_expires_at <= normalized_now
                    )
                if due:
                    candidates.append(
                        (self._turn_owner_sequences[key], owner, conversation, _clone(turn), next_attempt)
                    )
        filtered: list[OwnedTurnRecord] = []
        for _, owner, conversation, turn, next_attempt in sorted(candidates):
            if await self._eligible(owner, conversation):
                filtered.append(OwnedTurnRecord(user_id=owner, turn=turn, next_attempt_at=next_attempt))
        return filtered[:page_size]

    async def heartbeat_turn(
        self,
        claim: TurnExecutionClaim,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim:
        normalized_claim = TurnExecutionClaim.model_validate(claim.model_dump(mode="python"))
        _, normalized_now, normalized_expiry = _lease_window(
            normalized_claim.lease_owner,
            now,
            lease_expires_at,
        )
        async with self._compaction_write_lock:
            key = (normalized_claim.user_id, normalized_claim.turn.turn_id)
            execution = self._turn_executions.get(key)
            if (
                execution is None
                or execution.lease_owner != normalized_claim.lease_owner
                or execution.lease_token != normalized_claim.lease_token
                or execution.lease_expires_at is None
                or execution.lease_expires_at <= normalized_now
                or normalized_expiry <= execution.lease_expires_at
            ):
                raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
            execution.lease_expires_at = normalized_expiry
            return TurnExecutionClaim(
                user_id=normalized_claim.user_id,
                turn=_clone(self._turns[key]),
                lease_owner=normalized_claim.lease_owner,
                lease_token=normalized_claim.lease_token,
                lease_expires_at=normalized_expiry,
                attempt=execution.attempt,
            )

    async def reschedule_turn(
        self,
        claim: TurnExecutionClaim,
        *,
        now: datetime,
        next_attempt_at: datetime,
        reason_code: str,
    ) -> TurnRecord:
        normalized_claim = TurnExecutionClaim.model_validate(claim.model_dump(mode="python"))
        normalized_now = _normalize_datetime("now", now)
        normalized_next = _normalize_datetime("next_attempt_at", next_attempt_at)
        reason = _require_text("reason_code", reason_code, 64)
        if normalized_next <= normalized_now:
            raise ValueError("next_attempt_at 必须晚于 now")
        async with self._compaction_write_lock:
            key = (normalized_claim.user_id, normalized_claim.turn.turn_id)
            execution = self._turn_executions.get(key)
            if not self._execution_matches(execution, normalized_claim, normalized_now):
                raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
            turn = self._turns[key].model_copy(update={"status": TurnStatus.QUEUED})
            self._turns[key] = _clone(turn)
            execution.lease_owner = None
            execution.lease_token = None
            execution.lease_expires_at = None
            execution.next_attempt_at = normalized_next
            execution.last_reason_code = reason
            return _clone(turn)

    @staticmethod
    def _execution_matches(
        execution: _MemoryTurnExecution | None,
        claim: TurnExecutionClaim,
        now: datetime,
    ) -> bool:
        return (
            execution is not None
            and execution.lease_owner == claim.lease_owner
            and execution.lease_token == claim.lease_token
            and execution.lease_expires_at is not None
            and execution.lease_expires_at > now
        )

    def _snapshot_live_runtime_state(self) -> dict[str, object]:
        return {
            "workflows": deepcopy(self._workflows),
            "workflow_ids": set(self._workflow_ids),
            "turns": deepcopy(self._turns),
            "events": deepcopy(self._events),
            "event_ids": set(self._event_ids),
            "event_sequence_keys": set(self._event_sequence_keys),
            "event_cursor_keys": set(self._event_cursor_keys),
            "event_delivery": deepcopy(self._event_delivery),
            "video_states": {key: _clone_state(value) for key, value in self._video_states.items()},
            "turn_executions": deepcopy(self._turn_executions),
            "projection_messages": {key: _clone_message(value) for key, value in self._projection_messages.items()},
            "interrupts": {key: _clone_interrupt(value) for key, value in self._interrupts.items()},
            "active_workflows": dict(self._active_workflows),
        }

    def _restore_live_runtime_state(self, state: dict[str, object]) -> None:
        self._workflows = state["workflows"]  # type: ignore[assignment]
        self._workflow_ids = state["workflow_ids"]  # type: ignore[assignment]
        self._turns = state["turns"]  # type: ignore[assignment]
        self._events = state["events"]  # type: ignore[assignment]
        self._event_ids = state["event_ids"]  # type: ignore[assignment]
        self._event_sequence_keys = state["event_sequence_keys"]  # type: ignore[assignment]
        self._event_cursor_keys = state["event_cursor_keys"]  # type: ignore[assignment]
        self._event_delivery = state["event_delivery"]  # type: ignore[assignment]
        self._video_states = state["video_states"]  # type: ignore[assignment]
        self._turn_executions = state["turn_executions"]  # type: ignore[assignment]
        self._projection_messages = state["projection_messages"]  # type: ignore[assignment]
        self._interrupts = state["interrupts"]  # type: ignore[assignment]
        self._active_workflows = state["active_workflows"]  # type: ignore[assignment]

    def _commit_projection_matches(
        self,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> bool:
        if commit.workflow_state is not None:
            stored_state = self._video_states.get(
                (claim.user_id, commit.workflow_state.workflow_id)
            )
            if stored_state is None or stored_state != commit.workflow_state:
                return False
        for message in commit.messages:
            stored = self._projection_messages.get((claim.user_id, message.message_id))
            if stored is None or stored.model_dump(mode="json") != message.model_dump(mode="json"):
                return False
        if commit.open_interrupt is not None:
            stored_interrupt = self._interrupts.get(
                (claim.user_id, commit.open_interrupt.interrupt_id)
            )
            if (
                stored_interrupt is None
                or stored_interrupt.model_dump(mode="json")
                != commit.open_interrupt.model_dump(mode="json")
            ):
                return False
        if commit.close_interrupt_id is not None:
            closed = self._interrupts.get((claim.user_id, commit.close_interrupt_id))
            if closed is None or closed.status != "closed" or closed.closed_at != commit.occurred_at:
                return False
        if commit.workflow is not None:
            stored_workflow = self._workflows.get((claim.user_id, commit.workflow.workflow_id))
            if (
                stored_workflow is None
                or stored_workflow.model_dump(mode="json")
                != commit.workflow.model_dump(mode="json")
            ):
                return False
        if commit.update_active_workflow and self._active_workflows.get(
            (claim.user_id, claim.turn.conversation_id)
        ) != commit.active_workflow_id:
            return False
        return True

    def _is_idempotent_turn_replay(
        self,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> TurnRecord | None:
        turn = self._turns.get((claim.user_id, claim.turn.turn_id))
        execution = self._turn_executions.get((claim.user_id, claim.turn.turn_id))
        if turn is None or execution is None:
            return None
        commit_identity = _video_turn_commit_identity(commit)
        stored_identity = execution.last_reason_code
        same_action_key = (
            turn.decision is not None
            and turn.decision.idempotency_key == commit.decision.idempotency_key
        )
        if stored_identity is not None and stored_identity.startswith("commit:"):
            if stored_identity != commit_identity:
                if same_action_key:
                    raise AgentRuntimeRecordConflictError("相同动作键对应不同视频 Turn 提交摘要")
                return None
            if (
                turn.status is not commit.turn_status
                or turn.decision is None
                or turn.decision.model_dump(mode="json")
                != commit.decision.model_dump(mode="json")
                or not self._commit_projection_matches(claim, commit)
            ):
                raise AgentRuntimeRecordConflictError("视频 Turn 提交摘要与持久化投影不一致")
            return _clone(turn)
        if (
            turn.status is not commit.turn_status
            or turn.decision is None
            or turn.decision.model_dump(mode="json") != commit.decision.model_dump(mode="json")
        ):
            return None
        state = commit.workflow_state
        if state is None:
            return _clone(turn) if turn.status in _TERMINAL_TURN_STATUSES else None
        existing = self._video_states.get((claim.user_id, state.workflow_id))
        if (
            existing is not None
            and existing.last_action_key == state.last_action_key
            and existing.payload_sha256 == state.payload_sha256
        ):
            return _clone(turn)
        return None

    def _compare_and_set_video_state(self, commit: VideoTurnCommit) -> None:
        state = commit.workflow_state
        if state is None:
            return
        key = (state.user_id, state.workflow_id)
        existing = self._video_states.get(key)
        if existing is not None and existing.last_action_key == state.last_action_key:
            if existing.payload_sha256 == state.payload_sha256:
                return
            raise VideoWorkflowStateConflictError("同一动作键对应不同完整信封摘要")
        current_version = 0 if existing is None else existing.workflow_version
        if current_version != commit.expected_workflow_version or state.workflow_version != current_version + 1:
            raise VideoWorkflowStateConflictError("视频 Workflow 状态版本 CAS 冲突")
        self._video_states[key] = _clone_state(state)

    def _upsert_workflow_and_active_projection(
        self,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> None:
        workflow = commit.workflow
        if workflow is not None:
            key = (claim.user_id, workflow.workflow_id)
            existing_owner = next((owner for owner, workflow_id in self._workflows if workflow_id == workflow.workflow_id), None)
            if existing_owner is not None and existing_owner != claim.user_id:
                raise AgentRuntimeRecordConflictError("Workflow 已属于其他用户")
            self._workflows[key] = WorkflowRecord.model_validate(workflow.model_dump(mode="python"))
            self._workflow_ids.add(workflow.workflow_id)
        if not commit.update_active_workflow:
            return
        active = commit.active_workflow_id
        if active is not None:
            target = self._workflows.get((claim.user_id, active))
            if target is None or target.conversation_id != claim.turn.conversation_id:
                raise AgentRuntimeRecordConflictError("active Workflow 不属于当前会话")
        self._active_workflows[(claim.user_id, claim.turn.conversation_id)] = active

    def _upsert_projection_messages(
        self,
        user_id: str,
        messages: tuple[SupervisorProjectionMessage, ...],
    ) -> None:
        for message in messages:
            key = (user_id, message.message_id)
            existing = self._projection_messages.get(key)
            if existing is not None and existing != message:
                raise AgentRuntimeRecordConflictError("投影消息 ID 已对应不同内容")
            self._projection_messages[key] = _clone_message(message)

    def _apply_interrupt_transition(
        self,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> None:
        if commit.close_interrupt_id is not None:
            key = (claim.user_id, commit.close_interrupt_id)
            existing = self._interrupts.get(key)
            if existing is None or existing.conversation_id != claim.turn.conversation_id:
                raise AgentRuntimeRecordConflictError("待关闭 interrupt 不存在")
            if existing.status not in {"open", "responded", "closed"}:
                raise AgentRuntimeRecordConflictError("interrupt 状态不允许关闭")
            if existing.status != "closed":
                self._interrupts[key] = _clone_interrupt(
                    existing.model_copy(update={"status": "closed", "closed_at": commit.occurred_at})
                )
        if commit.open_interrupt is None:
            return
        open_existing = [
            item
            for (owner, _), item in self._interrupts.items()
            if owner == claim.user_id
            and item.conversation_id == claim.turn.conversation_id
            and item.status == "open"
            and item.interrupt_id != commit.close_interrupt_id
        ]
        if open_existing:
            raise AgentRuntimeRecordConflictError("同一会话只能保留一个 open interrupt")
        key = (claim.user_id, commit.open_interrupt.interrupt_id)
        existing = self._interrupts.get(key)
        if existing is not None and existing != commit.open_interrupt:
            raise AgentRuntimeRecordConflictError("interrupt ID 已对应不同内容")
        self._interrupts[key] = _clone_interrupt(commit.open_interrupt)

    def _append_events(
        self,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
        *,
        operation_event_id: str | None = None,
        include_turn_terminal: bool = True,
    ) -> None:
        conversation_events = [
            event for (owner, _), event in self._events.items()
            if owner == claim.user_id and event.conversation_id == claim.turn.conversation_id
        ]
        sequence = 1 if not conversation_events else max(item.sequence for item in conversation_events) + 1
        specs: list[tuple[AgentEventType, dict[str, JsonValue], str]] = []
        if commit.workflow is not None:
            specs.append(
                (
                    AgentEventType.WORKFLOW_PROGRESSED,
                    {"workflow": commit.workflow.model_dump(mode="json")},
                    commit.workflow.workflow_id,
                )
            )
        for message in commit.messages:
            specs.append(
                (
                    AgentEventType.MESSAGE_UPSERTED,
                    {"message": message.model_dump(mode="json")},
                    message.message_id,
                )
            )
        if commit.close_interrupt_id is not None:
            specs.append((AgentEventType.INTERRUPT_CLOSED, {"interrupt_id": commit.close_interrupt_id}, commit.close_interrupt_id))
        if commit.open_interrupt is not None:
            specs.append(
                (
                    AgentEventType.INTERRUPT_OPENED,
                    {"interrupt": commit.open_interrupt.model_dump(mode="json")},
                    commit.open_interrupt.interrupt_id,
                )
            )
        if include_turn_terminal:
            specs.append(
                (
                    AgentEventType.INPUT_STATE_CHANGED,
                    {
                        "turn_id": claim.turn.turn_id,
                        "status": commit.turn_status.value,
                        "reason_code": commit.error_reason_code,
                    },
                    claim.turn.turn_id,
                )
            )
            if commit.error_reason_code is not None:
                specs.append(
                    (
                        AgentEventType.ERROR_RAISED,
                        {"turn_id": claim.turn.turn_id, "reason_code": commit.error_reason_code},
                        commit.error_reason_code,
                    )
                )
        action_key = operation_event_id or commit.decision.idempotency_key
        for offset, (event_type, payload, subject) in enumerate(specs):
            event = _event(
                sequence=sequence + offset,
                conversation_id=claim.turn.conversation_id,
                run_id=claim.turn.turn_id,
                occurred_at=commit.occurred_at,
                event_type=event_type,
                payload=payload,
                identity_parts=(claim.turn.turn_id, action_key, event_type.value, subject),
            )
            key = (claim.user_id, event.event_id)
            existing = self._events.get(key)
            if existing is not None:
                if existing != event:
                    raise AgentRuntimeRecordConflictError("事件 ID 已对应不同内容")
                continue
            sequence_key = (event.conversation_id, event.sequence)
            cursor_key = (event.conversation_id, event.cursor)
            if sequence_key in self._event_sequence_keys or cursor_key in self._event_cursor_keys:
                raise AgentRuntimeRecordConflictError("事件 sequence 或 cursor 冲突")
            self._event_ids.add(event.event_id)
            self._event_sequence_keys.add(sequence_key)
            self._event_cursor_keys.add(cursor_key)
            self._events[key] = _clone(event)
            self._event_delivery[key] = _MemoryEventDeliveryState()

    async def commit_turn(self, claim: TurnExecutionClaim, commit: VideoTurnCommit) -> TurnRecord:
        normalized_claim, normalized_commit = _validate_commit_contract(claim, commit)
        async with self._compaction_write_lock:
            async with self._operation_write_lock:
                async with self._event_write_lock:
                    replay = self._is_idempotent_turn_replay(
                        normalized_claim,
                        normalized_commit,
                    )
                    operation_claim = normalized_commit.operation_event_claim
                    if replay is not None:
                        if operation_claim is not None:
                            event_key = (
                                normalized_claim.user_id,
                                operation_claim.event.event_id,
                            )
                            delivery = self._event_delivery.get(event_key)
                            operation = self._operations.get(
                                (
                                    normalized_claim.user_id,
                                    str(operation_claim.event.payload["job_id"]),
                                )
                            )
                            if (
                                self._events.get(event_key)
                                != operation_claim.event
                                or delivery is None
                                or delivery.status != "published"
                                or normalized_commit.workflow_state is None
                                or normalized_commit.workflow is None
                            ):
                                raise AgentRuntimeRecordConflictError(
                                    "quota resume Turn 重放缺少已发布 Event",
                                )
                            _validate_operation_quota_resume_binding(
                                user_id=normalized_claim.user_id,
                                event=operation_claim.event,
                                operation=operation,
                                workflow_state=normalized_commit.workflow_state,
                                workflow=normalized_commit.workflow,
                            )
                        return replay
                    completed_at = _normalize_datetime(
                        "completed_at",
                        self._completion_clock(),
                    )
                    before = self._snapshot_live_runtime_state()
                    try:
                        key = (
                            normalized_claim.user_id,
                            normalized_claim.turn.turn_id,
                        )
                        execution = self._turn_executions.get(key)
                        if not self._execution_matches(
                            execution,
                            normalized_claim,
                            completed_at,
                        ):
                            raise TurnExecutionLeaseConflictError(
                                normalized_claim.turn.turn_id,
                            )
                        current = self._turns.get(key)
                        if (
                            current is None
                            or current.status is not TurnStatus.PROCESSING
                        ):
                            raise TurnExecutionLeaseConflictError(
                                normalized_claim.turn.turn_id,
                            )
                        if operation_claim is not None:
                            event_key = (
                                normalized_claim.user_id,
                                operation_claim.event.event_id,
                            )
                            event = self._events.get(event_key)
                            delivery = self._event_delivery.get(event_key)
                            operation = self._operations.get(
                                (
                                    normalized_claim.user_id,
                                    str(operation_claim.event.payload["job_id"]),
                                )
                            )
                            if (
                                event != operation_claim.event
                                or delivery is None
                                or delivery.status != "delivering"
                                or delivery.delivery_attempts
                                != operation_claim.delivery_attempts
                                or delivery.lease_owner
                                != operation_claim.lease_owner
                                or delivery.lease_expires_at
                                != operation_claim.lease_expires_at
                                or completed_at
                                >= operation_claim.lease_expires_at
                                or normalized_commit.workflow_state is None
                                or normalized_commit.workflow is None
                            ):
                                raise TurnExecutionLeaseConflictError(
                                    operation_claim.event.event_id,
                                )
                            _validate_operation_quota_resume_binding(
                                user_id=normalized_claim.user_id,
                                event=event,
                                operation=operation,
                                workflow_state=normalized_commit.workflow_state,
                                workflow=normalized_commit.workflow,
                            )
                            payload = operation_claim.event.payload
                            matching_interrupts = [
                                item
                                for (owner, _), item in self._interrupts.items()
                                if owner == normalized_claim.user_id
                                and operation is not None
                                and _matches_quota_authorization_interrupt(
                                    item,
                                    operation=operation,
                                    quota_pause_revision=int(
                                        payload["quota_pause_revision"]
                                    ),
                                )
                                and item.status != "closed"
                            ]
                            if (
                                len(matching_interrupts) != 1
                                or matching_interrupts[0].interrupt_id
                                != normalized_commit.close_interrupt_id
                            ):
                                raise AgentRuntimeRecordConflictError(
                                    "quota resume Turn 未绑定唯一授权中断",
                                )
                        self._compare_and_set_video_state(normalized_commit)
                        self._upsert_workflow_and_active_projection(
                            normalized_claim,
                            normalized_commit,
                        )
                        self._upsert_projection_messages(
                            normalized_claim.user_id,
                            normalized_commit.messages,
                        )
                        self._apply_interrupt_transition(
                            normalized_claim,
                            normalized_commit,
                        )
                        self._append_events(
                            normalized_claim,
                            normalized_commit,
                            operation_event_id=(
                                None
                                if operation_claim is None
                                else operation_claim.event.event_id
                            ),
                        )
                        if operation_claim is not None:
                            delivery = self._event_delivery[event_key]
                            delivery.status = "published"
                            delivery.published_at = completed_at
                        finished = current.model_copy(
                            update={
                                "status": normalized_commit.turn_status,
                                "decision": ActionDecision.model_validate(
                                    normalized_commit.decision.model_dump(
                                        mode="python"
                                    )
                                ),
                                "target_workflow_id": (
                                    normalized_commit.workflow.workflow_id
                                    if normalized_commit.workflow is not None
                                    else normalized_commit.decision.target_workflow_id
                                ),
                            }
                        )
                        self._turns[key] = _clone(finished)
                        execution.lease_owner = None
                        execution.lease_token = None
                        execution.lease_expires_at = None
                        execution.next_attempt_at = None
                        execution.last_reason_code = _video_turn_commit_identity(
                            normalized_commit
                        )
                        return _clone(finished)
                    except Exception:
                        self._restore_live_runtime_state(before)
                        raise

    async def store_interrupt_response(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        client_response_id: UUID,
        response_value: dict[str, JsonValue],
        responded_at: datetime,
    ) -> StoredAgentInterrupt:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("interrupt_id", interrupt_id, 64)
        occurred_at = _normalize_datetime("responded_at", responded_at)
        response = {
            "client_response_id": str(client_response_id),
            "value": _json_copy(response_value, field_name="interrupt response value"),
        }
        async with self._compaction_write_lock:
            key = (owner, identity)
            existing = self._interrupts.get(key)
            if existing is None or existing.conversation_id != conversation:
                raise AgentRuntimeRecordConflictError("interrupt 不存在或不属于当前会话")
            if existing.status == "responded":
                if existing.response_id == client_response_id and existing.response == response:
                    return _clone_interrupt(existing)
                raise AgentRuntimeRecordConflictError("interrupt 已保存不同响应")
            if existing.status != "open":
                raise AgentRuntimeRecordConflictError("interrupt 已关闭")
            responded = StoredAgentInterrupt.model_validate(
                existing.model_dump(mode="python")
                | {"status": "responded", "response_id": client_response_id, "response": response}
            )
            self._interrupts[key] = _clone_interrupt(responded)
            del occurred_at
            return _clone_interrupt(responded)

    async def register_interrupt_response(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        request: InterruptResponseRequest,
        message: PixelFlowConversationMessageRecord,
        responded_at: datetime,
    ) -> InterruptResponseRegistration:
        """在共享临界区内登记响应，并由 Memory 对话写单元协同回滚。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("interrupt_id", interrupt_id, 64)
        occurred_at = _normalize_datetime("responded_at", responded_at)
        normalized_request = _normalize_interrupt_response(request)
        normalized_message = _validate_response_message(
            user_id=owner,
            conversation_id=conversation,
            interrupt_id=identity,
            request=normalized_request,
            message=message,
        )
        async with self._compaction_write_lock:
            key = (owner, identity)
            existing = self._interrupts.get(key)
            if existing is None or existing.conversation_id != conversation:
                raise AgentRuntimeRecordConflictError(
                    "interrupt 不存在或不属于当前会话",
                )
            turn_key = (owner, existing.turn_id)
            current_turn = self._turns.get(turn_key)
            if current_turn is None or current_turn.conversation_id != conversation:
                raise AgentRuntimeRecordConflictError("interrupt 原 Turn 不存在")

            if existing.status in {"responded", "closed"}:
                if not _interrupt_matches_response(existing, normalized_request):
                    raise AgentRuntimeRecordConflictError("interrupt 已保存不同响应")
                stored_messages = await self._task_store.list_conversation_messages(
                    conversation,
                    user_id=owner,
                )
                stored_message = next(
                    (
                        item
                        for item in stored_messages
                        if item.message_id == normalized_message.message_id
                    ),
                    None,
                )
                if stored_message is None or not _response_message_matches(
                    stored_message,
                    normalized_message,
                ):
                    raise AgentRuntimeRecordConflictError(
                        "interrupt 响应缺少对应权威消息",
                    )
                return InterruptResponseRegistration(
                    interrupt=_clone_interrupt(existing),
                    turn=_clone(current_turn),
                    message=deepcopy(stored_message),
                    context_version=_response_context_version(existing),
                    created=False,
                )
            if existing.status != "open":
                raise AgentRuntimeRecordConflictError("interrupt 状态非法")
            open_interrupts = [
                item
                for (record_owner, _), item in self._interrupts.items()
                if record_owner == owner
                and item.conversation_id == conversation
                and item.status == "open"
            ]
            if len(open_interrupts) != 1 or open_interrupts[0].interrupt_id != identity:
                raise AgentRuntimeRecordConflictError(
                    "当前会话 open interrupt 状态非法",
                )
            if current_turn.status is not TurnStatus.WAITING_USER:
                raise AgentRuntimeRecordConflictError(
                    "interrupt 原 Turn 不在 waiting_user",
                )

            before = self._snapshot_live_runtime_state()
            try:
                async with self._task_store.agent_runtime_interrupt_response_write(
                    conversation_id=conversation,
                    user_id=owner,
                    message=normalized_message,
                    occurred_at=occurred_at,
                ) as write:
                    response = _response_document(
                        normalized_request,
                        pre_input_context_version=write.pre_input_context_version,
                    )
                    responded = StoredAgentInterrupt.model_validate(
                        existing.model_dump(mode="python")
                        | {
                            "status": "responded",
                            "response_id": normalized_request.client_response_id,
                            "response": response,
                        }
                    )
                    updated_turn = current_turn.model_copy(
                        update={
                            "expected_context_version": write.pre_input_context_version,
                        }
                    )
                    stored_message = deepcopy(write.message)
                    self._interrupts[key] = _clone_interrupt(responded)
                    self._turns[turn_key] = _clone(updated_turn)
                    self._append_interrupt_response_events(
                        user_id=owner,
                        interrupt=responded,
                        turn=updated_turn,
                        message=stored_message,
                        request=normalized_request,
                        occurred_at=occurred_at,
                    )
                return InterruptResponseRegistration(
                    interrupt=_clone_interrupt(responded),
                    turn=_clone(updated_turn),
                    message=deepcopy(stored_message),
                    context_version=write.context_version,
                    created=True,
                )
            except ValueError as exc:
                self._restore_live_runtime_state(before)
                raise AgentRuntimeRecordConflictError(
                    "Agent Runtime 响应对话写入冲突",
                ) from exc
            except BaseException:
                self._restore_live_runtime_state(before)
                raise

    def _append_interrupt_response_events(
        self,
        *,
        user_id: str,
        interrupt: StoredAgentInterrupt,
        turn: TurnRecord,
        message: PixelFlowConversationMessageRecord,
        request: InterruptResponseRequest,
        occurred_at: datetime,
    ) -> None:
        """无 await 地追加响应事件，确保退出 Memory 写单元前没有半写可见。"""

        conversation_events = [
            (record_owner, event)
            for (record_owner, _), event in self._events.items()
            if event.conversation_id == turn.conversation_id
        ]
        if any(record_owner != user_id for record_owner, _ in conversation_events):
            raise AgentRuntimeRecordConflictError(
                "AgentEvent conversation 已被其他所有者占用",
            )
        next_sequence = (
            1
            if not conversation_events
            else max(event.sequence for _, event in conversation_events) + 1
        )
        action_key = f"interrupt-response:{request.client_response_id}"
        specs = _response_event_specs(
            interrupt=interrupt,
            turn=turn,
            message=message,
            client_response_id=request.client_response_id,
        )
        for offset, (event_type, payload, subject) in enumerate(specs):
            event = _event(
                sequence=next_sequence + offset,
                conversation_id=turn.conversation_id,
                run_id=turn.turn_id,
                occurred_at=occurred_at,
                event_type=event_type,
                payload=payload,
                identity_parts=(turn.turn_id, action_key, event_type.value, subject),
            )
            owner_key = (user_id, event.event_id)
            sequence_key = (event.conversation_id, event.sequence)
            cursor_key = (event.conversation_id, event.cursor)
            if (
                event.event_id in self._event_ids
                or sequence_key in self._event_sequence_keys
                or cursor_key in self._event_cursor_keys
            ):
                raise AgentRuntimeRecordConflictError(
                    "interrupt 响应事件身份冲突",
                )
            self._event_ids.add(event.event_id)
            self._event_sequence_keys.add(sequence_key)
            self._event_cursor_keys.add(cursor_key)
            self._events[owner_key] = _clone(event)
            self._event_delivery[owner_key] = _MemoryEventDeliveryState()

    async def get_interrupt(self, user_id: str, interrupt_id: str) -> StoredAgentInterrupt | None:
        item = self._interrupts.get(
            (_require_text("user_id", user_id, 64), _require_text("interrupt_id", interrupt_id, 64))
        )
        return None if item is None else _clone_interrupt(item)

    async def list_due_interrupt_responses(
        self, *, now: datetime, limit: int = 100
    ) -> list[StoredAgentInterrupt]:
        normalized_now = _normalize_datetime("now", now)
        page_size = _normalize_limit(limit)
        async with self._compaction_write_lock:
            candidates: list[StoredAgentInterrupt] = []
            for (owner, _), interrupt in self._interrupts.items():
                if (
                    interrupt.status != "responded"
                    or not self._compaction_due(owner, interrupt.conversation_id, normalized_now)
                ):
                    continue
                keys = self._owner_turn_keys(owner, interrupt.conversation_id)
                active = [key for key in keys if self._turns[key].status not in _TERMINAL_TURN_STATUSES]
                if not active or active[0] != (owner, interrupt.turn_id):
                    continue
                if self._turns[active[0]].status is not TurnStatus.WAITING_USER:
                    continue
                candidates.append(_clone_interrupt(interrupt))
        filtered = [
            item for item in candidates
            if await self._eligible(item.user_id, item.conversation_id)
        ]
        filtered.sort(key=lambda item: (item.opened_at, item.interrupt_id))
        return filtered[:page_size]

    async def claim_interrupt_resume(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("interrupt_id", interrupt_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(lease_owner, now, lease_expires_at)
        if not await self._eligible(owner, conversation):
            return None
        async with self._compaction_write_lock:
            interrupt = self._interrupts.get((owner, identity))
            if interrupt is None or interrupt.conversation_id != conversation or interrupt.status != "responded":
                return None
            return self._claim_turn_locked(
                owner,
                conversation,
                interrupt.turn_id,
                worker=worker,
                now=normalized_now,
                expires_at=normalized_expiry,
                interrupt_resume=True,
            )

    async def get_video_state(self, user_id: str, workflow_id: str) -> VideoWorkflowStateEnvelope | None:
        state = self._video_states.get(
            (_require_text("user_id", user_id, 64), _require_text("workflow_id", workflow_id, 64))
        )
        return None if state is None else _clone_state(state)

    async def list_projection_messages(
        self, user_id: str, conversation_id: str
    ) -> list[SupervisorProjectionMessage]:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        items = [
            item for (record_owner, _), item in self._projection_messages.items()
            if record_owner == owner and item.conversation_id == conversation
        ]
        items.sort(key=lambda item: (item.created_at, item.message_id))
        return [_clone_message(item) for item in items]

    async def get_open_interrupt(
        self, user_id: str, conversation_id: str
    ) -> StoredAgentInterrupt | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        items = [
            item for (record_owner, _), item in self._interrupts.items()
            if record_owner == owner and item.conversation_id == conversation and item.status == "open"
        ]
        items.sort(key=lambda item: (item.opened_at, item.interrupt_id))
        if len(items) > 1:
            raise AgentRuntimeRecordConflictError(
                "当前会话存在多个 open interrupt",
            )
        return None if not items else _clone_interrupt(items[0])

    async def get_active_workflow_id(self, user_id: str, conversation_id: str) -> str | None:
        return self._active_workflows.get(
            (_require_text("user_id", user_id, 64), _require_text("conversation_id", conversation_id, 64))
        )

    def _safe_snapshot_locked(
        self,
        owner: str,
        conversation: str,
    ) -> VideoRuntimeSafeSnapshot:
        """调用方持有 Repository 锁时复制全部 live 权威投影。"""

        states = [
            state
            for (record_owner, _), state in self._video_states.items()
            if record_owner == owner and state.conversation_id == conversation
        ]
        states.sort(key=lambda item: (item.created_at, item.workflow_id))
        workflows = [
            workflow
            for (record_owner, _), workflow in self._workflows.items()
            if record_owner == owner and workflow.conversation_id == conversation
        ]
        workflows.sort(
            key=lambda item: (item.updated_at, item.workflow_id),
            reverse=True,
        )
        turn_keys = [
            key
            for key, turn in self._turns.items()
            if key[0] == owner and turn.conversation_id == conversation
        ]
        turn_keys.sort(key=self._turn_owner_sequences.__getitem__)
        owned_turns = []
        for key in turn_keys:
            turn = self._turns[key]
            execution = self._turn_executions.get(key)
            owned_turns.append(
                OwnedTurnRecord(
                    user_id=owner,
                    turn=_clone(turn),
                    next_attempt_at=(
                        None if execution is None else execution.next_attempt_at
                    ),
                )
            )
        messages = [
            item
            for (record_owner, _), item in self._projection_messages.items()
            if record_owner == owner and item.conversation_id == conversation
        ]
        messages.sort(key=lambda item: (item.created_at, item.message_id))
        interrupts = [
            item
            for (record_owner, _), item in self._interrupts.items()
            if record_owner == owner and item.conversation_id == conversation
        ]
        interrupts.sort(key=lambda item: (item.opened_at, item.interrupt_id))
        return VideoRuntimeSafeSnapshot(
            conversation_id=conversation,
            active_workflow_id=self._active_workflows.get((owner, conversation)),
            workflow_states=tuple(_clone_state(item) for item in states),
            workflows=tuple(_clone(item) for item in workflows),
            turns=tuple(owned_turns),
            messages=tuple(_clone_message(item) for item in messages),
            interrupts=tuple(_clone_interrupt(item) for item in interrupts),
        )

    async def export_safe_snapshot(
        self, user_id: str, conversation_id: str
    ) -> VideoRuntimeSafeSnapshot:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        async with self._compaction_write_lock:
            return self._safe_snapshot_locked(owner, conversation)

    async def read_versioned_context_snapshot(
        self,
        user_id: str,
        conversation_id: str,
        *,
        expected_context_version: int,
    ) -> VideoRuntimeContextSnapshot:
        """在共享 Repository/Event 锁内一次读取全部 Context 原料。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        if type(expected_context_version) is not int or expected_context_version < 0:
            raise VideoRuntimeContextSnapshotConflictError(
                "context_snapshot_version_conflict"
            )
        async with self._compaction_write_lock:
            async with self._event_write_lock:
                conversation_record = await self._task_store.get_conversation(
                    conversation,
                    user_id=owner,
                )
                if conversation_record is None:
                    raise LookupError("对话不存在或不属于当前用户")
                current_context_version = _runtime_context_version(
                    conversation_record.context
                )
                if expected_context_version > current_context_version:
                    raise VideoRuntimeContextSnapshotConflictError(
                        "context_snapshot_version_conflict"
                    )
                task_messages = await self._task_store.list_conversation_messages(
                    conversation,
                    user_id=owner,
                )
                summaries = [
                    item
                    for (record_owner, _), item in self._summaries.items()
                    if record_owner == owner
                    and item.conversation_id == conversation
                ]
                summaries.sort(
                    key=lambda item: (item.version, item.created_at, item.summary_id)
                )
                events = [
                    item
                    for (record_owner, _), item in self._events.items()
                    if record_owner == owner
                    and item.conversation_id == conversation
                ]
                events.sort(key=lambda item: (item.sequence, item.event_id))
                return VideoRuntimeContextSnapshot(
                    conversation_id=conversation,
                    expected_context_version=expected_context_version,
                    current_context_version=current_context_version,
                    runtime=self._safe_snapshot_locked(owner, conversation),
                    task_messages=tuple(task_messages),
                    summaries=tuple(summaries),
                    events=tuple(events),
                )

    async def commit_operation_completion(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        messages: tuple[SupervisorProjectionMessage, ...],
        open_interrupt: StoredAgentInterrupt | None = None,
        occurred_at: datetime,
    ) -> WorkflowRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized_time = _normalize_datetime("occurred_at", occurred_at)
        normalized_claim = EventDeliveryClaim.model_validate(claim.model_dump(mode="python"))
        synthetic_claim = TurnExecutionClaim(
            user_id=owner,
            turn=TurnRecord(
                turn_id=workflow_state.last_turn_id,
                conversation_id=workflow_state.conversation_id,
                client_input_id=UUID(int=0),
                status=TurnStatus.PROCESSING,
                target_workflow_id=workflow_state.workflow_id,
                decision=None,
                expected_context_version=0,
                created_at=workflow_state.created_at,
            ),
            lease_owner=normalized_claim.lease_owner,
            lease_token=UUID(int=0),
            lease_expires_at=normalized_claim.lease_expires_at,
            attempt=1,
        )
        commit = VideoTurnCommit(
            decision=ActionDecision(
                action="continue_workflow",
                intent="video",
                target_workflow_id=workflow.workflow_id,
                target_stage=workflow.current_stage,
                confidence=1,
                reason_code="operation_completion",
                idempotency_key=normalized_claim.event.event_id,
            ),
            turn_status=(
                TurnStatus.WAITING_USER
                if open_interrupt is not None
                else TurnStatus.COMPLETED
            ),
            workflow_state=workflow_state,
            workflow=workflow,
            expected_workflow_version=expected_workflow_version,
            messages=messages,
            open_interrupt=open_interrupt,
            occurred_at=normalized_time,
        )
        _, normalized_commit = _validate_commit_contract(synthetic_claim, commit)
        async with self._compaction_write_lock:
            async with self._operation_write_lock:
                async with self._event_write_lock:
                    event_key = (owner, normalized_claim.event.event_id)
                    event = self._events.get(event_key)
                    delivery = self._event_delivery.get(event_key)
                    job_id = normalized_claim.event.payload.get("job_id")
                    operation = (
                        self._operations.get((owner, job_id))
                        if type(job_id) is str
                        else None
                    )
                    if event is not None:
                        _validate_operation_completion_binding(
                            user_id=owner,
                            event=event,
                            operation=operation,
                            workflow_state=normalized_commit.workflow_state,
                            workflow=normalized_commit.workflow,
                        )
                    if (
                        event is None
                        or event != normalized_claim.event
                        or delivery is None
                        or delivery.status != "delivering"
                        or delivery.delivery_attempts != normalized_claim.delivery_attempts
                        or delivery.lease_owner != normalized_claim.lease_owner
                        or delivery.lease_expires_at is None
                        or delivery.lease_expires_at != normalized_claim.lease_expires_at
                        or normalized_time >= delivery.lease_expires_at
                    ):
                        raise TurnExecutionLeaseConflictError(normalized_claim.event.event_id)
                    before = self._snapshot_live_runtime_state()
                    try:
                        self._compare_and_set_video_state(normalized_commit)
                        self._upsert_workflow_and_active_projection(synthetic_claim, normalized_commit)
                        self._upsert_projection_messages(owner, normalized_commit.messages)
                        self._apply_interrupt_transition(
                            synthetic_claim,
                            normalized_commit,
                        )
                        if normalized_commit.open_interrupt is not None:
                            turn_key = (owner, workflow_state.last_turn_id)
                            current_turn = self._turns.get(turn_key)
                            if (
                                current_turn is None
                                or current_turn.conversation_id
                                != workflow_state.conversation_id
                                or current_turn.status
                                not in {
                                    TurnStatus.COMPLETED,
                                    TurnStatus.WAITING_USER,
                                }
                            ):
                                raise AgentRuntimeRecordConflictError(
                                    "Operation 完成中断的原 Turn 状态不允许恢复",
                                )
                            self._turns[turn_key] = _clone(
                                current_turn.model_copy(
                                    update={"status": TurnStatus.WAITING_USER},
                                )
                            )
                        self._append_events(
                            synthetic_claim,
                            normalized_commit,
                            operation_event_id=normalized_claim.event.event_id,
                            include_turn_terminal=(
                                normalized_commit.open_interrupt is not None
                            ),
                        )
                        delivery = self._event_delivery[event_key]
                        delivery.status = "published"
                        delivery.published_at = normalized_time
                        return _clone(workflow)
                    except Exception:
                        self._restore_live_runtime_state(before)
                        raise

    async def commit_operation_quota_state(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        open_interrupt: StoredAgentInterrupt | None,
        close_interrupt_revision: int | None,
        occurred_at: datetime,
    ) -> WorkflowRecord:
        """在同一 Memory 临界区公开 pause overlay 与原 Turn 中断。"""

        from pixelflow.agent_runtime.jobs import (
            OperationQuotaEventPayload,
            OperationQuotaState,
        )
        from pixelflow.agent_workflows.video.live_quota import (
            VideoOperationQuotaProjectionService,
        )

        owner = _require_text("user_id", user_id, 64)
        _normalize_datetime("occurred_at", occurred_at)
        normalized_claim = EventDeliveryClaim.model_validate(
            claim.model_dump(mode="python")
        )
        payload = OperationQuotaEventPayload.model_validate(
            normalized_claim.event.payload
        )
        is_pause = payload.quota_state is OperationQuotaState.PAUSED
        if is_pause:
            if open_interrupt is None or close_interrupt_revision is not None:
                raise AgentRuntimeRecordConflictError("quota pause 投影目标不完整")
        elif open_interrupt is not None or close_interrupt_revision != (
            payload.quota_pause_revision
        ):
            raise AgentRuntimeRecordConflictError("quota resume 投影目标不完整")
        target_state = VideoWorkflowStateEnvelope.model_validate(
            workflow_state.model_dump(mode="python")
        )
        target_workflow = WorkflowRecord.model_validate(
            workflow.model_dump(mode="python")
        )
        target_interrupt = (
            None
            if open_interrupt is None
            else StoredAgentInterrupt.model_validate(
                open_interrupt.model_dump(mode="python")
            )
        )
        async with self._compaction_write_lock:
            async with self._operation_write_lock:
                async with self._event_write_lock:
                    event_key = (owner, normalized_claim.event.event_id)
                    event = self._events.get(event_key)
                    delivery = self._event_delivery.get(event_key)
                    operation = self._operations.get((owner, payload.job_id))
                    if (
                        event is None
                        or event != normalized_claim.event
                        or delivery is None
                        or operation is None
                    ):
                        raise AgentRuntimeRecordConflictError(
                            "quota pause Event 或 Operation 不存在",
                        )
                    current_state = self._video_states.get(
                        (owner, operation.workflow_id)
                    )
                    stored_workflow = self._workflows.get(
                        (owner, operation.workflow_id)
                    )
                    turn_key = (owner, target_state.last_turn_id)
                    current_turn = self._turns.get(turn_key)
                    matching_interrupts = [
                        item
                        for (record_owner, _), item in self._interrupts.items()
                        if record_owner == owner
                        and _matches_quota_authorization_interrupt(
                            item,
                            operation=operation,
                            quota_pause_revision=payload.quota_pause_revision,
                        )
                    ]
                    unclosed_interrupts = [
                        item
                        for item in matching_interrupts
                        if item.status != "closed"
                    ]
                    if len(unclosed_interrupts) > 1:
                        raise AgentRuntimeRecordConflictError(
                            "quota resume 匹配到多个未关闭授权中断",
                        )
                    stored_interrupt = (
                        None
                        if target_interrupt is None
                        else next(
                            (
                                item
                                for item in matching_interrupts
                                if item.interrupt_id
                                == target_interrupt.interrupt_id
                            ),
                            None,
                        )
                    )
                    close_interrupt = (
                        unclosed_interrupts[0]
                        if unclosed_interrupts
                        else next(
                            (
                                item
                                for item in matching_interrupts
                                if item.status == "closed"
                            ),
                            None,
                        )
                    )
                    if delivery.status == "published":
                        if (
                            delivery.delivery_attempts
                            != normalized_claim.delivery_attempts
                            or delivery.lease_owner
                            != normalized_claim.lease_owner
                            or delivery.lease_expires_at
                            != normalized_claim.lease_expires_at
                            or delivery.published_at is None
                            or delivery.published_at
                            >= normalized_claim.lease_expires_at
                            or current_state != target_state
                            or stored_workflow != target_workflow
                            or current_turn is None
                            or (
                                is_pause
                                and (
                                    stored_interrupt != target_interrupt
                                    or current_turn.status
                                    is not TurnStatus.WAITING_USER
                                )
                            )
                            or (
                                not is_pause
                                and (
                                    close_interrupt is None
                                    or close_interrupt.status != "closed"
                                    or current_turn.status
                                    is not TurnStatus.COMPLETED
                                )
                            )
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "quota pause 已发布投影与重放目标不一致",
                            )
                        return _clone(target_workflow)
                    completed_at = _normalize_datetime(
                        "completed_at",
                        self._completion_clock(),
                    )
                    if (
                        delivery.status != "delivering"
                        or delivery.delivery_attempts
                        != normalized_claim.delivery_attempts
                        or delivery.lease_owner != normalized_claim.lease_owner
                        or delivery.lease_expires_at
                        != normalized_claim.lease_expires_at
                        or completed_at >= normalized_claim.lease_expires_at
                        or current_state is None
                        or current_turn is None
                        or current_turn.conversation_id
                        != target_state.conversation_id
                        or (
                            is_pause
                            and (
                                operation.next_poll_at is not None
                                or current_turn.status
                                not in {
                                    TurnStatus.COMPLETED,
                                    TurnStatus.WAITING_USER,
                                }
                            )
                        )
                        or (
                            not is_pause
                            and (
                                operation.next_poll_at is None
                                or close_interrupt is None
                                or current_turn.status
                                not in {
                                    TurnStatus.PROCESSING,
                                    TurnStatus.COMPLETED,
                                }
                            )
                        )
                    ):
                        raise TurnExecutionLeaseConflictError(
                            normalized_claim.event.event_id,
                        )
                    projection = VideoOperationQuotaProjectionService().build(
                        user_id=owner,
                        envelope=current_state,
                        operation=operation,
                        quota_event=event,
                    )
                    if (
                        expected_workflow_version
                        != current_state.workflow_version
                        or projection.workflow_state != target_state
                        or projection.workflow != target_workflow
                        or projection.open_interrupt != target_interrupt
                        or projection.close_interrupt_revision
                        != close_interrupt_revision
                    ):
                        raise VideoWorkflowStateConflictError(
                            "quota Event 投影与权威目标不一致",
                        )
                    synthetic_claim, commit = _quota_projection_commit(
                        claim=normalized_claim,
                        user_id=owner,
                        workflow_state=target_state,
                        workflow=target_workflow,
                        expected_workflow_version=expected_workflow_version,
                        open_interrupt=target_interrupt,
                        close_interrupt_id=(
                            None
                            if is_pause or close_interrupt is None
                            else close_interrupt.interrupt_id
                        ),
                        turn_status=(
                            TurnStatus.WAITING_USER
                            if is_pause
                            else TurnStatus.COMPLETED
                        ),
                        occurred_at=completed_at,
                    )
                    before = self._snapshot_live_runtime_state()
                    try:
                        self._compare_and_set_video_state(commit)
                        self._upsert_workflow_and_active_projection(
                            synthetic_claim,
                            commit,
                        )
                        self._apply_interrupt_transition(
                            synthetic_claim,
                            commit,
                        )
                        self._turns[turn_key] = _clone(
                            current_turn.model_copy(
                                update={
                                    "status": (
                                        TurnStatus.WAITING_USER
                                        if is_pause
                                        else TurnStatus.COMPLETED
                                    )
                                },
                            )
                        )
                        self._append_events(
                            synthetic_claim,
                            commit,
                            operation_event_id=event.event_id,
                            include_turn_terminal=(
                                is_pause
                                or current_turn.status
                                is TurnStatus.PROCESSING
                            ),
                        )
                        delivery = self._event_delivery[event_key]
                        delivery.status = "published"
                        delivery.published_at = completed_at
                        return _clone(target_workflow)
                    except Exception:
                        self._restore_live_runtime_state(before)
                        raise


def _video_state_from_row(row: PixelFlowAgentVideoStateRow) -> VideoWorkflowStateEnvelope:
    return VideoWorkflowStateEnvelope.model_validate(
        {
            "schema_version": row.schema_version,
            "workflow_id": row.workflow_id,
            "conversation_id": row.conversation_id,
            "user_id": row.user_id,
            "state_kind": row.state_kind,
            "workflow_version": row.workflow_version,
            "context_version": row.context_version,
            "payload": row.payload_json,
            "payload_sha256": row.payload_sha256,
            "last_turn_id": row.last_turn_id,
            "last_action_key": row.last_action_key,
            "created_at": _database_utc(row.created_at),
            "updated_at": _database_utc(row.updated_at),
        }
    )


def _message_from_row(row: PixelFlowAgentProjectionMessageRow) -> SupervisorProjectionMessage:
    return SupervisorProjectionMessage(
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        role=row.role,
        content=row.content,
        payload=row.payload_json,
        created_at=_database_utc(row.created_at),
    )


def _interrupt_from_row(row: PixelFlowAgentInterruptRow) -> StoredAgentInterrupt:
    return StoredAgentInterrupt(
        interrupt_id=row.interrupt_id,
        conversation_id=row.conversation_id,
        workflow_id=row.workflow_id,
        turn_id=row.turn_id,
        kind=row.kind,
        reason_code=row.reason_code,
        payload=row.payload_json,
        opened_at=_database_utc(row.opened_at),
        user_id=row.user_id,
        thread_id=row.thread_id,
        checkpoint_ns=row.checkpoint_ns,
        status=row.status,
        response_id=row.response_id,
        response=row.response_json,
        closed_at=None if row.closed_at is None else _database_utc(row.closed_at),
    )


def _sql_conversation_is_video_live(row: PixelFlowConversationRow | None) -> bool:
    if row is None or row.orchestration_mode != "supervisor_v1" or row.orchestration_version != 1:
        return False
    runtime = (row.context_json or {}).get(AGENT_RUNTIME_CONTEXT_KEY)
    return (
        isinstance(runtime, dict)
        and runtime.get("primary_execution_ready") is True
        and isinstance(runtime.get("enabled_intents"), list)
        and "video" in runtime["enabled_intents"]
    )


def _sql_execution_claim(
    *,
    user_id: str,
    turn: PixelFlowAgentTurnRow,
    execution: PixelFlowAgentTurnExecutionRow,
) -> TurnExecutionClaim:
    if execution.lease_owner is None or execution.lease_token is None or execution.lease_expires_at is None:
        raise TurnExecutionLeaseConflictError(turn.turn_id)
    return TurnExecutionClaim(
        user_id=user_id,
        turn=_turn_from_row(turn),
        lease_owner=execution.lease_owner,
        lease_token=UUID(execution.lease_token),
        lease_expires_at=_database_utc(execution.lease_expires_at),
        attempt=execution.attempt,
    )


class SQLVideoRuntimeRepository(SQLCompactionQueueRepository):
    """使用条件更新、行锁和同一事务提交 live 视频投影。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        task_store: PixelFlowTaskStore,
        completion_clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._task_store = task_store
        self._completion_clock = completion_clock or (
            lambda: datetime.now(UTC)
        )
        store_factory = getattr(task_store, "session_factory", None)
        if store_factory is not None and store_factory is not session_factory:
            raise ValueError("SQL 视频 Repository 与 Task Store 必须复用同一 Session 工厂")

    @staticmethod
    def _conversation_statement(user_id: str, conversation_id: str):
        return (
            select(PixelFlowConversationRow)
            .where(
                PixelFlowConversationRow.user_id == user_id,
                PixelFlowConversationRow.conversation_id == conversation_id,
            )
            .with_for_update()
        )

    @staticmethod
    def _first_unfinished_turn_statement(user_id: str, conversation_id: str):
        return (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == user_id,
                PixelFlowAgentTurnRow.conversation_id == conversation_id,
                PixelFlowAgentTurnRow.status.not_in(
                    (TurnStatus.COMPLETED.value, TurnStatus.FAILED.value)
                ),
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
            .limit(1)
            .with_for_update()
        )

    @staticmethod
    def _execution_statement(user_id: str, conversation_id: str, turn_id: str):
        return (
            select(PixelFlowAgentTurnExecutionRow)
            .where(
                PixelFlowAgentTurnExecutionRow.user_id == user_id,
                PixelFlowAgentTurnExecutionRow.conversation_id == conversation_id,
                PixelFlowAgentTurnExecutionRow.turn_id == turn_id,
            )
            .with_for_update()
        )

    async def _claim_in_transaction(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        worker: str,
        now: datetime,
        expires_at: datetime,
        interrupt_resume: bool,
    ) -> TurnExecutionClaim | None:
        conversation = (
            await session.scalars(self._conversation_statement(user_id, conversation_id))
        ).one_or_none()
        if not _sql_conversation_is_video_live(conversation):
            return None
        coordination = (
            await session.scalars(self._coordination_statement(conversation_id))
        ).one_or_none()
        if coordination is not None:
            if coordination.user_id != user_id:
                return None
            if coordination.state != "idle":
                expiry = (
                    None
                    if coordination.lease_expires_at is None
                    else _database_utc(coordination.lease_expires_at)
                )
                if expiry is None or expiry > now:
                    return None
        turn = (
            await session.scalars(
                self._first_unfinished_turn_statement(user_id, conversation_id)
            )
        ).first()
        if turn is None or turn.turn_id != turn_id:
            return None
        if interrupt_resume:
            if turn.status != TurnStatus.WAITING_USER.value:
                return None
        elif turn.status not in {item.value for item in _CLAIMABLE_TURN_STATUSES}:
            return None
        execution = (
            await session.scalars(
                self._execution_statement(user_id, conversation_id, turn_id)
            )
        ).one_or_none()
        if execution is not None:
            next_attempt = (
                None
                if execution.next_attempt_at is None
                else _database_utc(execution.next_attempt_at)
            )
            if next_attempt is not None and next_attempt > now:
                return None
            current_expiry = (
                None
                if execution.lease_expires_at is None
                else _database_utc(execution.lease_expires_at)
            )
            if current_expiry is not None and current_expiry > now:
                if execution.lease_owner != worker:
                    return None
                return _sql_execution_claim(
                    user_id=user_id,
                    turn=turn,
                    execution=execution,
                )
        else:
            execution = PixelFlowAgentTurnExecutionRow(
                turn_id=turn_id,
                conversation_id=conversation_id,
                user_id=user_id,
                attempt=0,
                created_at=now,
                updated_at=now,
            )
            session.add(execution)
        execution.attempt += 1
        execution.lease_owner = worker
        execution.lease_token = str(uuid4())
        execution.lease_expires_at = expires_at
        execution.next_attempt_at = None
        execution.updated_at = now
        turn.status = TurnStatus.PROCESSING.value
        turn.updated_at = now
        await session.flush()
        return _sql_execution_claim(user_id=user_id, turn=turn, execution=execution)

    async def claim_turn(
        self,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("turn_id", turn_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner, now, lease_expires_at
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(session, self._sqlite_write_lock):
                return await self._claim_in_transaction(
                    session,
                    user_id=owner,
                    conversation_id=conversation,
                    turn_id=identity,
                    worker=worker,
                    now=normalized_now,
                    expires_at=normalized_expiry,
                    interrupt_resume=False,
                )

    @staticmethod
    def _live_conversation_predicate():
        runtime = PixelFlowConversationRow.context_json[AGENT_RUNTIME_CONTEXT_KEY]
        return and_(
            PixelFlowConversationRow.orchestration_mode == "supervisor_v1",
            PixelFlowConversationRow.orchestration_version == 1,
            runtime["primary_execution_ready"].as_boolean().is_(True),
            cast(runtime["enabled_intents"], String).like('%"video"%'),
        )

    async def list_due_turns(self, *, now: datetime, limit: int = 100) -> list[OwnedTurnRecord]:
        normalized_now = _normalize_datetime("now", now)
        page_size = _normalize_limit(limit)
        execution = PixelFlowAgentTurnExecutionRow
        coordination = PixelFlowAgentCompactionLockRow
        earlier_turn = aliased(PixelFlowAgentTurnRow)
        statement = (
            select(PixelFlowAgentTurnRow, execution.next_attempt_at)
            .join(
                PixelFlowConversationRow,
                and_(
                    PixelFlowConversationRow.conversation_id
                    == PixelFlowAgentTurnRow.conversation_id,
                    PixelFlowConversationRow.user_id == PixelFlowAgentTurnRow.user_id,
                ),
            )
            .outerjoin(execution, execution.turn_id == PixelFlowAgentTurnRow.turn_id)
            .outerjoin(
                coordination,
                coordination.conversation_id == PixelFlowAgentTurnRow.conversation_id,
            )
            .where(
                self._live_conversation_predicate(),
                or_(
                    coordination.conversation_id.is_(None),
                    coordination.state == "idle",
                    coordination.lease_expires_at <= normalized_now,
                ),
                or_(
                    PixelFlowAgentTurnRow.status == TurnStatus.ACCEPTED.value,
                    and_(
                        PixelFlowAgentTurnRow.status == TurnStatus.QUEUED.value,
                        or_(execution.next_attempt_at.is_(None), execution.next_attempt_at <= normalized_now),
                    ),
                    and_(
                        PixelFlowAgentTurnRow.status == TurnStatus.PROCESSING.value,
                        execution.lease_expires_at.is_not(None),
                        execution.lease_expires_at <= normalized_now,
                    ),
                ),
                ~exists(
                    select(1).where(
                        earlier_turn.user_id == PixelFlowAgentTurnRow.user_id,
                        earlier_turn.conversation_id == PixelFlowAgentTurnRow.conversation_id,
                        earlier_turn.inbox_sequence < PixelFlowAgentTurnRow.inbox_sequence,
                        earlier_turn.status.not_in(
                            (TurnStatus.COMPLETED.value, TurnStatus.FAILED.value)
                        ),
                    )
                ),
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
            .limit(page_size)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            OwnedTurnRecord(
                user_id=row.user_id,
                turn=_turn_from_row(row),
                next_attempt_at=(None if next_attempt is None else _database_utc(next_attempt)),
            )
            for row, next_attempt in rows
        ]

    async def heartbeat_turn(
        self,
        claim: TurnExecutionClaim,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim:
        normalized_claim = TurnExecutionClaim.model_validate(claim.model_dump(mode="python"))
        _, normalized_now, normalized_expiry = _lease_window(
            normalized_claim.lease_owner, now, lease_expires_at
        )
        statement = (
            update(PixelFlowAgentTurnExecutionRow)
            .where(
                PixelFlowAgentTurnExecutionRow.user_id == normalized_claim.user_id,
                PixelFlowAgentTurnExecutionRow.turn_id == normalized_claim.turn.turn_id,
                PixelFlowAgentTurnExecutionRow.lease_owner == normalized_claim.lease_owner,
                PixelFlowAgentTurnExecutionRow.lease_token == str(normalized_claim.lease_token),
                PixelFlowAgentTurnExecutionRow.lease_expires_at > normalized_now,
                PixelFlowAgentTurnExecutionRow.lease_expires_at < normalized_expiry,
            )
            .values(lease_expires_at=normalized_expiry, updated_at=normalized_now)
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(session, self._sqlite_write_lock):
                result = await session.execute(statement)
                if result.rowcount != 1:
                    raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
                execution = (
                    await session.scalars(
                        self._execution_statement(
                            normalized_claim.user_id,
                            normalized_claim.turn.conversation_id,
                            normalized_claim.turn.turn_id,
                        )
                    )
                ).one()
                turn = (
                    await session.scalars(
                        select(PixelFlowAgentTurnRow).where(
                            PixelFlowAgentTurnRow.user_id == normalized_claim.user_id,
                            PixelFlowAgentTurnRow.turn_id == normalized_claim.turn.turn_id,
                        )
                    )
                ).one()
                return _sql_execution_claim(
                    user_id=normalized_claim.user_id,
                    turn=turn,
                    execution=execution,
                )

    async def reschedule_turn(
        self,
        claim: TurnExecutionClaim,
        *,
        now: datetime,
        next_attempt_at: datetime,
        reason_code: str,
    ) -> TurnRecord:
        normalized_claim = TurnExecutionClaim.model_validate(claim.model_dump(mode="python"))
        normalized_now = _normalize_datetime("now", now)
        normalized_next = _normalize_datetime("next_attempt_at", next_attempt_at)
        reason = _require_text("reason_code", reason_code, 64)
        if normalized_next <= normalized_now:
            raise ValueError("next_attempt_at 必须晚于 now")
        execution_update = (
            update(PixelFlowAgentTurnExecutionRow)
            .where(
                PixelFlowAgentTurnExecutionRow.user_id == normalized_claim.user_id,
                PixelFlowAgentTurnExecutionRow.turn_id == normalized_claim.turn.turn_id,
                PixelFlowAgentTurnExecutionRow.lease_owner == normalized_claim.lease_owner,
                PixelFlowAgentTurnExecutionRow.lease_token == str(normalized_claim.lease_token),
                PixelFlowAgentTurnExecutionRow.lease_expires_at > normalized_now,
            )
            .values(
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                next_attempt_at=normalized_next,
                last_reason_code=reason,
                updated_at=normalized_now,
            )
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(session, self._sqlite_write_lock):
                result = await session.execute(execution_update)
                if result.rowcount != 1:
                    raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
                turn = (
                    await session.scalars(
                        select(PixelFlowAgentTurnRow)
                        .where(
                            PixelFlowAgentTurnRow.user_id == normalized_claim.user_id,
                            PixelFlowAgentTurnRow.conversation_id == normalized_claim.turn.conversation_id,
                            PixelFlowAgentTurnRow.turn_id == normalized_claim.turn.turn_id,
                        )
                        .with_for_update()
                    )
                ).one()
                turn.status = TurnStatus.QUEUED.value
                turn.updated_at = normalized_now
                await session.flush()
                return _turn_from_row(turn)

    async def _sql_compare_and_set_state(
        self,
        session: AsyncSession,
        commit: VideoTurnCommit,
    ) -> None:
        state = commit.workflow_state
        if state is None:
            return
        row = (
            await session.scalars(
                select(PixelFlowAgentVideoStateRow)
                .where(PixelFlowAgentVideoStateRow.workflow_id == state.workflow_id)
                .with_for_update()
            )
        ).one_or_none()
        if row is not None:
            if row.user_id != state.user_id or row.conversation_id != state.conversation_id:
                raise AgentRuntimeRecordConflictError("视频状态已属于其他用户或会话")
            if row.last_action_key == state.last_action_key:
                if row.payload_sha256 == state.payload_sha256:
                    return
                raise VideoWorkflowStateConflictError("同一动作键对应不同完整信封摘要")
        current_version = 0 if row is None else row.workflow_version
        if current_version != commit.expected_workflow_version or state.workflow_version != current_version + 1:
            raise VideoWorkflowStateConflictError("视频 Workflow 状态版本 CAS 冲突")
        values = state.model_dump(mode="json")
        if row is None:
            session.add(
                PixelFlowAgentVideoStateRow(
                    workflow_id=state.workflow_id,
                    conversation_id=state.conversation_id,
                    user_id=state.user_id,
                    schema_version=state.schema_version,
                    state_kind=state.state_kind.value,
                    workflow_version=state.workflow_version,
                    context_version=state.context_version,
                    payload_json=values["payload"],
                    payload_sha256=state.payload_sha256,
                    last_turn_id=state.last_turn_id,
                    last_action_key=state.last_action_key,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                )
            )
            return
        row.schema_version = state.schema_version
        row.state_kind = state.state_kind.value
        row.workflow_version = state.workflow_version
        row.context_version = state.context_version
        row.payload_json = values["payload"]
        row.payload_sha256 = state.payload_sha256
        row.last_turn_id = state.last_turn_id
        row.last_action_key = state.last_action_key
        row.updated_at = state.updated_at

    async def _sql_upsert_workflow_and_active(
        self,
        session: AsyncSession,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> None:
        workflow = commit.workflow
        if workflow is not None:
            row = (
                await session.scalars(
                    select(PixelFlowAgentWorkflowRow)
                    .where(PixelFlowAgentWorkflowRow.workflow_id == workflow.workflow_id)
                    .with_for_update()
                )
            ).one_or_none()
            values = workflow.model_dump(mode="json")
            if row is None:
                row = PixelFlowAgentWorkflowRow(
                    workflow_id=workflow.workflow_id,
                    conversation_id=workflow.conversation_id,
                    user_id=claim.user_id,
                    kind=workflow.kind.value,
                    status=workflow.status.value,
                    current_stage=workflow.current_stage,
                    stage_version=workflow.stage_version,
                    creation_contract_snapshot_json=values["creation_contract_snapshot"],
                    pending_external_job_json=values["pending_external_job"],
                    latest_artifact_refs_json=values["latest_artifact_refs"],
                    context_version=workflow.context_version,
                    created_at=workflow.created_at,
                    updated_at=workflow.updated_at,
                )
                session.add(row)
            else:
                if row.user_id != claim.user_id or row.conversation_id != claim.turn.conversation_id:
                    raise AgentRuntimeRecordConflictError("Workflow 已属于其他用户或会话")
                row.kind = workflow.kind.value
                row.status = workflow.status.value
                row.current_stage = workflow.current_stage
                row.stage_version = workflow.stage_version
                row.creation_contract_snapshot_json = values["creation_contract_snapshot"]
                row.pending_external_job_json = values["pending_external_job"]
                row.latest_artifact_refs_json = values["latest_artifact_refs"]
                row.context_version = workflow.context_version
                row.updated_at = workflow.updated_at
        if not commit.update_active_workflow:
            return
        active = commit.active_workflow_id
        if active is not None:
            target = (
                await session.scalars(
                    select(PixelFlowAgentWorkflowRow)
                    .where(
                        PixelFlowAgentWorkflowRow.user_id == claim.user_id,
                        PixelFlowAgentWorkflowRow.conversation_id == claim.turn.conversation_id,
                        PixelFlowAgentWorkflowRow.workflow_id == active,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if target is None:
                raise AgentRuntimeRecordConflictError("active Workflow 不属于当前会话")
        row = (
            await session.scalars(
                select(PixelFlowAgentConversationStateRow)
                .where(PixelFlowAgentConversationStateRow.conversation_id == claim.turn.conversation_id)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            session.add(
                PixelFlowAgentConversationStateRow(
                    conversation_id=claim.turn.conversation_id,
                    user_id=claim.user_id,
                    active_workflow_id=active,
                    created_at=commit.occurred_at,
                    updated_at=commit.occurred_at,
                )
            )
        else:
            if row.user_id != claim.user_id:
                raise AgentRuntimeRecordConflictError("conversation state 已属于其他用户")
            row.active_workflow_id = active
            row.updated_at = commit.occurred_at

    async def _sql_upsert_messages(
        self,
        session: AsyncSession,
        user_id: str,
        messages: tuple[SupervisorProjectionMessage, ...],
    ) -> None:
        for message in messages:
            row = (
                await session.scalars(
                    select(PixelFlowAgentProjectionMessageRow)
                    .where(PixelFlowAgentProjectionMessageRow.message_id == message.message_id)
                    .with_for_update()
                )
            ).one_or_none()
            values = message.model_dump(mode="json")
            if row is None:
                session.add(
                    PixelFlowAgentProjectionMessageRow(
                        message_id=message.message_id,
                        conversation_id=message.conversation_id,
                        user_id=user_id,
                        run_id=message.run_id,
                        role=message.role,
                        content=message.content,
                        payload_json=values["payload"],
                        created_at=message.created_at,
                        updated_at=message.created_at,
                    )
                )
            elif _message_from_row(row) != message or row.user_id != user_id:
                raise AgentRuntimeRecordConflictError("投影消息 ID 已对应不同内容")

    async def _sql_apply_interrupt_transition(
        self,
        session: AsyncSession,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> None:
        if commit.close_interrupt_id is not None:
            row = (
                await session.scalars(
                    select(PixelFlowAgentInterruptRow)
                    .where(
                        PixelFlowAgentInterruptRow.user_id == claim.user_id,
                        PixelFlowAgentInterruptRow.conversation_id == claim.turn.conversation_id,
                        PixelFlowAgentInterruptRow.interrupt_id == commit.close_interrupt_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise AgentRuntimeRecordConflictError("待关闭 interrupt 不存在")
            if row.status != "closed":
                row.status = "closed"
                row.closed_at = commit.occurred_at
        if commit.open_interrupt is None:
            return
        open_row = (
            await session.scalars(
                select(PixelFlowAgentInterruptRow)
                .where(
                    PixelFlowAgentInterruptRow.user_id == claim.user_id,
                    PixelFlowAgentInterruptRow.conversation_id == claim.turn.conversation_id,
                    PixelFlowAgentInterruptRow.status == "open",
                    PixelFlowAgentInterruptRow.interrupt_id != (commit.close_interrupt_id or ""),
                )
                .limit(1)
                .with_for_update()
            )
        ).first()
        if open_row is not None:
            raise AgentRuntimeRecordConflictError("同一会话只能保留一个 open interrupt")
        interrupt = commit.open_interrupt
        existing = (
            await session.scalars(
                select(PixelFlowAgentInterruptRow)
                .where(PixelFlowAgentInterruptRow.interrupt_id == interrupt.interrupt_id)
                .with_for_update()
            )
        ).one_or_none()
        values = interrupt.model_dump(mode="json")
        if existing is not None:
            if _interrupt_from_row(existing) != interrupt or existing.user_id != claim.user_id:
                raise AgentRuntimeRecordConflictError("interrupt ID 已对应不同内容")
            return
        session.add(
            PixelFlowAgentInterruptRow(
                interrupt_id=interrupt.interrupt_id,
                conversation_id=interrupt.conversation_id,
                user_id=interrupt.user_id,
                workflow_id=interrupt.workflow_id,
                turn_id=interrupt.turn_id,
                thread_id=interrupt.thread_id,
                checkpoint_ns=interrupt.checkpoint_ns,
                kind=interrupt.kind,
                reason_code=interrupt.reason_code,
                status=interrupt.status,
                payload_json=values["payload"],
                response_id=None if interrupt.response_id is None else str(interrupt.response_id),
                response_json=(null() if values["response"] is None else values["response"]),
                opened_at=interrupt.opened_at,
                closed_at=interrupt.closed_at,
            )
        )

    @staticmethod
    def _event_specs(
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
        *,
        include_turn_terminal: bool = True,
    ) -> list[tuple[AgentEventType, dict[str, JsonValue], str]]:
        specs: list[tuple[AgentEventType, dict[str, JsonValue], str]] = []
        if commit.workflow is not None:
            specs.append((AgentEventType.WORKFLOW_PROGRESSED, {"workflow": commit.workflow.model_dump(mode="json")}, commit.workflow.workflow_id))
        specs.extend(
            (AgentEventType.MESSAGE_UPSERTED, {"message": item.model_dump(mode="json")}, item.message_id)
            for item in commit.messages
        )
        if commit.close_interrupt_id is not None:
            specs.append((AgentEventType.INTERRUPT_CLOSED, {"interrupt_id": commit.close_interrupt_id}, commit.close_interrupt_id))
        if commit.open_interrupt is not None:
            specs.append((AgentEventType.INTERRUPT_OPENED, {"interrupt": commit.open_interrupt.model_dump(mode="json")}, commit.open_interrupt.interrupt_id))
        if include_turn_terminal:
            specs.append((AgentEventType.INPUT_STATE_CHANGED, {"turn_id": claim.turn.turn_id, "status": commit.turn_status.value, "reason_code": commit.error_reason_code}, claim.turn.turn_id))
            if commit.error_reason_code is not None:
                specs.append((AgentEventType.ERROR_RAISED, {"turn_id": claim.turn.turn_id, "reason_code": commit.error_reason_code}, commit.error_reason_code))
        return specs

    async def _sql_append_events(
        self,
        session: AsyncSession,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
        *,
        action_key: str | None = None,
        include_turn_terminal: bool = True,
    ) -> None:
        last = (
            await session.scalars(
                select(PixelFlowAgentEventRow)
                .where(PixelFlowAgentEventRow.conversation_id == claim.turn.conversation_id)
                .order_by(PixelFlowAgentEventRow.sequence.desc())
                .limit(1)
                .with_for_update()
            )
        ).first()
        if last is not None and last.user_id != claim.user_id:
            raise AgentRuntimeRecordConflictError("AgentEvent conversation 已属于其他用户")
        sequence = 1 if last is None else last.sequence + 1
        key = action_key or commit.decision.idempotency_key
        for offset, (event_type, payload, subject) in enumerate(
            self._event_specs(
                claim,
                commit,
                include_turn_terminal=include_turn_terminal,
            )
        ):
            event = _event(
                sequence=sequence + offset,
                conversation_id=claim.turn.conversation_id,
                run_id=claim.turn.turn_id,
                occurred_at=commit.occurred_at,
                event_type=event_type,
                payload=payload,
                identity_parts=(claim.turn.turn_id, key, event_type.value, subject),
            )
            existing = (
                await session.scalars(
                    select(PixelFlowAgentEventRow)
                    .where(PixelFlowAgentEventRow.event_id == event.event_id)
                    .with_for_update()
                )
            ).one_or_none()
            if existing is not None:
                if _event_from_row(existing) != event or existing.user_id != claim.user_id:
                    raise AgentRuntimeRecordConflictError("事件 ID 已对应不同内容")
                continue
            session.add(
                PixelFlowAgentEventRow(
                    schema_version=event.schema_version,
                    event_id=event.event_id,
                    sequence=event.sequence,
                    cursor=event.cursor,
                    conversation_id=event.conversation_id,
                    user_id=claim.user_id,
                    run_id=event.run_id,
                    occurred_at=event.occurred_at,
                    event_type=event.type.value,
                    payload_json=event.payload,
                    delivery_status="pending",
                    delivery_attempts=0,
                )
            )

    async def _sql_commit_projection_matches(
        self,
        session: AsyncSession,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> bool:
        if commit.workflow_state is not None:
            state_row = (
                await session.scalars(
                    select(PixelFlowAgentVideoStateRow).where(
                        PixelFlowAgentVideoStateRow.user_id == claim.user_id,
                        PixelFlowAgentVideoStateRow.workflow_id
                        == commit.workflow_state.workflow_id,
                    )
                )
            ).one_or_none()
            if state_row is None or _video_state_from_row(state_row) != commit.workflow_state:
                return False
        for message in commit.messages:
            row = (
                await session.scalars(
                    select(PixelFlowAgentProjectionMessageRow).where(
                        PixelFlowAgentProjectionMessageRow.user_id == claim.user_id,
                        PixelFlowAgentProjectionMessageRow.message_id == message.message_id,
                    )
                )
            ).one_or_none()
            if row is None or _message_from_row(row).model_dump(
                mode="json"
            ) != message.model_dump(mode="json"):
                return False
        if commit.open_interrupt is not None:
            row = (
                await session.scalars(
                    select(PixelFlowAgentInterruptRow).where(
                        PixelFlowAgentInterruptRow.user_id == claim.user_id,
                        PixelFlowAgentInterruptRow.interrupt_id
                        == commit.open_interrupt.interrupt_id,
                    )
                )
            ).one_or_none()
            if row is None or _interrupt_from_row(row).model_dump(
                mode="json"
            ) != commit.open_interrupt.model_dump(mode="json"):
                return False
        if commit.close_interrupt_id is not None:
            closed = (
                await session.scalars(
                    select(PixelFlowAgentInterruptRow).where(
                        PixelFlowAgentInterruptRow.user_id == claim.user_id,
                        PixelFlowAgentInterruptRow.interrupt_id == commit.close_interrupt_id,
                    )
                )
            ).one_or_none()
            if (
                closed is None
                or closed.status != "closed"
                or closed.closed_at is None
                or _database_utc(closed.closed_at) != commit.occurred_at
            ):
                return False
        if commit.workflow is not None:
            workflow_row = (
                await session.scalars(
                    select(PixelFlowAgentWorkflowRow).where(
                        PixelFlowAgentWorkflowRow.user_id == claim.user_id,
                        PixelFlowAgentWorkflowRow.workflow_id == commit.workflow.workflow_id,
                    )
                )
            ).one_or_none()
            if workflow_row is None or _workflow_from_row(
                workflow_row
            ).model_dump(mode="json") != commit.workflow.model_dump(mode="json"):
                return False
        if commit.update_active_workflow:
            active_row = (
                await session.scalars(
                    select(PixelFlowAgentConversationStateRow).where(
                        PixelFlowAgentConversationStateRow.user_id == claim.user_id,
                        PixelFlowAgentConversationStateRow.conversation_id
                        == claim.turn.conversation_id,
                    )
                )
            ).one_or_none()
            if active_row is None or active_row.active_workflow_id != commit.active_workflow_id:
                return False
        return True

    async def _sql_idempotent_replay(
        self,
        session: AsyncSession,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
    ) -> TurnRecord | None:
        turn = (
            await session.scalars(
                select(PixelFlowAgentTurnRow)
                .where(
                    PixelFlowAgentTurnRow.user_id == claim.user_id,
                    PixelFlowAgentTurnRow.turn_id == claim.turn.turn_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if turn is None:
            return None
        execution = (
            await session.scalars(
                self._execution_statement(
                    claim.user_id,
                    claim.turn.conversation_id,
                    claim.turn.turn_id,
                )
            )
        ).one_or_none()
        commit_identity = _video_turn_commit_identity(commit)
        stored_identity = None if execution is None else execution.last_reason_code
        same_action_key = (
            isinstance(turn.decision_json, dict)
            and turn.decision_json.get("idempotency_key") == commit.decision.idempotency_key
        )
        if stored_identity is not None and stored_identity.startswith("commit:"):
            if stored_identity != commit_identity:
                if same_action_key:
                    raise AgentRuntimeRecordConflictError("相同动作键对应不同视频 Turn 提交摘要")
                return None
            if (
                turn.status != commit.turn_status.value
                or turn.decision_json != commit.decision.model_dump(mode="json")
                or not await self._sql_commit_projection_matches(session, claim, commit)
            ):
                raise AgentRuntimeRecordConflictError("视频 Turn 提交摘要与持久化投影不一致")
            return _turn_from_row(turn)
        if (
            turn.status != commit.turn_status.value
            or turn.decision_json != commit.decision.model_dump(mode="json")
        ):
            return None
        if commit.workflow_state is None:
            return _turn_from_row(turn) if TurnStatus(turn.status) in _TERMINAL_TURN_STATUSES else None
        state = (
            await session.scalars(
                select(PixelFlowAgentVideoStateRow)
                .where(PixelFlowAgentVideoStateRow.workflow_id == commit.workflow_state.workflow_id)
                .with_for_update()
            )
        ).one_or_none()
        if state is not None and state.user_id == claim.user_id and state.last_action_key == commit.workflow_state.last_action_key and state.payload_sha256 == commit.workflow_state.payload_sha256:
            return _turn_from_row(turn)
        return None

    async def commit_turn(self, claim: TurnExecutionClaim, commit: VideoTurnCommit) -> TurnRecord:
        normalized_claim, normalized_commit = _validate_commit_contract(claim, commit)
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(session, self._sqlite_write_lock):
                    operation_claim = normalized_commit.operation_event_claim
                    operation_row = None
                    event_row = None
                    operation = None
                    if operation_claim is not None:
                        operation_row = (
                            await session.scalars(
                                select(PixelFlowAgentOperationRow)
                                .where(
                                    PixelFlowAgentOperationRow.user_id
                                    == normalized_claim.user_id,
                                    PixelFlowAgentOperationRow.job_id
                                    == str(operation_claim.event.payload["job_id"]),
                                )
                                .with_for_update()
                            )
                        ).one_or_none()
                        event_row = (
                            await session.scalars(
                                select(PixelFlowAgentEventRow)
                                .where(
                                    PixelFlowAgentEventRow.user_id
                                    == normalized_claim.user_id,
                                    PixelFlowAgentEventRow.event_id
                                    == operation_claim.event.event_id,
                                )
                                .with_for_update()
                            )
                        ).one_or_none()
                        if operation_row is None or event_row is None:
                            raise AgentRuntimeRecordConflictError(
                                "quota resume Turn 缺少 Event 或 Operation",
                            )
                        operation = _operation_from_row(operation_row)
                    replay = await self._sql_idempotent_replay(session, normalized_claim, normalized_commit)
                    if replay is not None:
                        if (
                            operation_claim is not None
                            and (
                                event_row is None
                                or _event_from_row(event_row)
                                != operation_claim.event
                                or event_row.delivery_status != "published"
                                or normalized_commit.workflow_state is None
                                or normalized_commit.workflow is None
                            )
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "quota resume Turn 重放缺少已发布 Event",
                            )
                        if operation_claim is not None:
                            _validate_operation_quota_resume_binding(
                                user_id=normalized_claim.user_id,
                                event=operation_claim.event,
                                operation=operation,
                                workflow_state=normalized_commit.workflow_state,
                                workflow=normalized_commit.workflow,
                            )
                        return replay
                    if operation_claim is not None:
                        assert event_row is not None
                        expiry = (
                            None
                            if event_row.lease_expires_at is None
                            else _database_utc(event_row.lease_expires_at)
                        )
                        if (
                            _event_from_row(event_row)
                            != operation_claim.event
                            or event_row.delivery_status != "delivering"
                            or event_row.delivery_attempts
                            != operation_claim.delivery_attempts
                            or event_row.lease_owner
                            != operation_claim.lease_owner
                            or expiry != operation_claim.lease_expires_at
                            or normalized_commit.workflow_state is None
                            or normalized_commit.workflow is None
                        ):
                            raise TurnExecutionLeaseConflictError(
                                operation_claim.event.event_id,
                            )
                        _validate_operation_quota_resume_binding(
                            user_id=normalized_claim.user_id,
                            event=operation_claim.event,
                            operation=operation,
                            workflow_state=normalized_commit.workflow_state,
                            workflow=normalized_commit.workflow,
                        )
                        interrupt_rows = list(
                            (
                                await session.scalars(
                                    select(PixelFlowAgentInterruptRow)
                                    .where(
                                        PixelFlowAgentInterruptRow.user_id
                                        == normalized_claim.user_id,
                                        PixelFlowAgentInterruptRow.conversation_id
                                        == normalized_claim.turn.conversation_id,
                                        PixelFlowAgentInterruptRow.workflow_id
                                        == operation.workflow_id,
                                        PixelFlowAgentInterruptRow.kind
                                        == "authorization_required",
                                        PixelFlowAgentInterruptRow.reason_code
                                        == "authorization_required",
                                    )
                                    .with_for_update()
                                )
                            ).all()
                        )
                        matching_interrupts = [
                            _interrupt_from_row(item)
                            for item in interrupt_rows
                            if _matches_quota_authorization_interrupt(
                                _interrupt_from_row(item),
                                operation=operation,
                                quota_pause_revision=int(
                                    operation_claim.event.payload[
                                        "quota_pause_revision"
                                    ]
                                ),
                            )
                            and item.status != "closed"
                        ]
                        if (
                            len(matching_interrupts) != 1
                            or matching_interrupts[0].interrupt_id
                            != normalized_commit.close_interrupt_id
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "quota resume Turn 未绑定唯一授权中断",
                            )
                    turn = (
                        await session.scalars(
                            select(PixelFlowAgentTurnRow)
                            .where(
                                PixelFlowAgentTurnRow.user_id == normalized_claim.user_id,
                                PixelFlowAgentTurnRow.conversation_id == normalized_claim.turn.conversation_id,
                                PixelFlowAgentTurnRow.turn_id == normalized_claim.turn.turn_id,
                                PixelFlowAgentTurnRow.status == TurnStatus.PROCESSING.value,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if turn is None:
                        raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
                    await self._sql_compare_and_set_state(session, normalized_commit)
                    await self._sql_upsert_workflow_and_active(session, normalized_claim, normalized_commit)
                    await self._sql_upsert_messages(session, normalized_claim.user_id, normalized_commit.messages)
                    await self._sql_apply_interrupt_transition(session, normalized_claim, normalized_commit)
                    await self._sql_append_events(
                        session,
                        normalized_claim,
                        normalized_commit,
                        action_key=(
                            None
                            if operation_claim is None
                            else operation_claim.event.event_id
                        ),
                    )
                    await session.flush()
                    completed_at = _normalize_datetime(
                        "completed_at",
                        self._completion_clock(),
                    )
                    if (
                        operation_claim is not None
                        and completed_at
                        >= operation_claim.lease_expires_at
                    ):
                        raise TurnExecutionLeaseConflictError(
                            operation_claim.event.event_id,
                        )
                    lease_result = await session.execute(
                        update(PixelFlowAgentTurnExecutionRow)
                        .where(
                            PixelFlowAgentTurnExecutionRow.user_id
                            == normalized_claim.user_id,
                            PixelFlowAgentTurnExecutionRow.conversation_id
                            == normalized_claim.turn.conversation_id,
                            PixelFlowAgentTurnExecutionRow.turn_id
                            == normalized_claim.turn.turn_id,
                            PixelFlowAgentTurnExecutionRow.lease_owner
                            == normalized_claim.lease_owner,
                            PixelFlowAgentTurnExecutionRow.lease_token
                            == str(normalized_claim.lease_token),
                            PixelFlowAgentTurnExecutionRow.lease_expires_at
                            > completed_at,
                        )
                        .values(
                            lease_owner=None,
                            lease_token=None,
                            lease_expires_at=None,
                            next_attempt_at=None,
                            last_reason_code=_video_turn_commit_identity(
                                normalized_commit,
                            ),
                            updated_at=completed_at,
                        )
                    )
                    if lease_result.rowcount != 1:
                        raise TurnExecutionLeaseConflictError(
                            normalized_claim.turn.turn_id,
                        )
                    if event_row is not None:
                        event_row.delivery_status = "published"
                        event_row.published_at = completed_at
                    turn.status = normalized_commit.turn_status.value
                    turn.decision_json = normalized_commit.decision.model_dump(mode="json")
                    turn.target_workflow_id = (
                        normalized_commit.workflow.workflow_id
                        if normalized_commit.workflow is not None
                        else normalized_commit.decision.target_workflow_id
                    )
                    turn.updated_at = completed_at
                    await session.flush()
                    return _turn_from_row(turn)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("视频 Turn 原子提交唯一键冲突") from None

    async def store_interrupt_response(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        client_response_id: UUID,
        response_value: dict[str, JsonValue],
        responded_at: datetime,
    ) -> StoredAgentInterrupt:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("interrupt_id", interrupt_id, 64)
        occurred_at = _normalize_datetime("responded_at", responded_at)
        response = {
            "client_response_id": str(client_response_id),
            "value": _json_copy(response_value, field_name="interrupt response value"),
        }
        async with self._session_factory() as session:
            async with _repository_write_transaction(session, self._sqlite_write_lock):
                row = (
                    await session.scalars(
                        select(PixelFlowAgentInterruptRow)
                        .where(
                            PixelFlowAgentInterruptRow.user_id == owner,
                            PixelFlowAgentInterruptRow.conversation_id == conversation,
                            PixelFlowAgentInterruptRow.interrupt_id == identity,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if row is None:
                    raise AgentRuntimeRecordConflictError("interrupt 不存在或不属于当前会话")
                if row.status == "responded":
                    if row.response_id == str(client_response_id) and row.response_json == response:
                        return _interrupt_from_row(row)
                    raise AgentRuntimeRecordConflictError("interrupt 已保存不同响应")
                if row.status != "open":
                    raise AgentRuntimeRecordConflictError("interrupt 已关闭")
                row.status = "responded"
                row.response_id = str(client_response_id)
                row.response_json = response
                del occurred_at
                await session.flush()
                return _interrupt_from_row(row)

    async def register_interrupt_response(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        request: InterruptResponseRequest,
        message: PixelFlowConversationMessageRecord,
        responded_at: datetime,
    ) -> InterruptResponseRegistration:
        """在同一 SQL 事务中登记响应、原 Turn 快照身份、消息和事件。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("interrupt_id", interrupt_id, 64)
        occurred_at = _normalize_datetime("responded_at", responded_at)
        normalized_request = _normalize_interrupt_response(request)
        normalized_message = _validate_response_message(
            user_id=owner,
            conversation_id=conversation,
            interrupt_id=identity,
            request=normalized_request,
            message=message,
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    conversation_row = (
                        await session.scalars(
                            self._conversation_statement(owner, conversation),
                        )
                    ).one_or_none()
                    if not _sql_conversation_is_video_live(conversation_row):
                        raise AgentRuntimeRecordConflictError(
                            "interrupt 所属对话不可执行 live 视频",
                        )
                    interrupt_turn_id = (
                        await session.scalar(
                            select(PixelFlowAgentInterruptRow.turn_id)
                            .where(
                                PixelFlowAgentInterruptRow.user_id == owner,
                                PixelFlowAgentInterruptRow.conversation_id
                                == conversation,
                                PixelFlowAgentInterruptRow.interrupt_id == identity,
                            )
                        )
                    )
                    if interrupt_turn_id is None:
                        raise AgentRuntimeRecordConflictError(
                            "interrupt 不存在或不属于当前会话",
                        )
                    turn_row = (
                        await session.scalars(
                            select(PixelFlowAgentTurnRow)
                            .where(
                                PixelFlowAgentTurnRow.user_id == owner,
                                PixelFlowAgentTurnRow.conversation_id == conversation,
                                PixelFlowAgentTurnRow.turn_id
                                == interrupt_turn_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if turn_row is None:
                        raise AgentRuntimeRecordConflictError(
                            "interrupt 原 Turn 不存在",
                        )
                    interrupt_row = (
                        await session.scalars(
                            select(PixelFlowAgentInterruptRow)
                            .where(
                                PixelFlowAgentInterruptRow.user_id == owner,
                                PixelFlowAgentInterruptRow.conversation_id
                                == conversation,
                                PixelFlowAgentInterruptRow.interrupt_id == identity,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if (
                        interrupt_row is None
                        or interrupt_row.turn_id != turn_row.turn_id
                    ):
                        raise AgentRuntimeRecordConflictError(
                            "interrupt 不存在或不属于当前会话",
                        )
                    stored_interrupt = _interrupt_from_row(interrupt_row)
                    if interrupt_row.status in {"responded", "closed"}:
                        if not _interrupt_matches_response(
                            stored_interrupt,
                            normalized_request,
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "interrupt 已保存不同响应",
                            )
                        message_row = (
                            await session.scalars(
                                select(PixelFlowConversationMessageRow)
                                .where(
                                    PixelFlowConversationMessageRow.message_id
                                    == normalized_message.message_id,
                                    PixelFlowConversationMessageRow.conversation_id
                                    == conversation,
                                    PixelFlowConversationMessageRow.user_id == owner,
                                )
                                .with_for_update()
                            )
                        ).one_or_none()
                        if message_row is None:
                            raise AgentRuntimeRecordConflictError(
                                "interrupt 响应缺少对应权威消息",
                            )
                        stored_message = _conversation_message_from_row(message_row)
                        if not _response_message_matches(
                            stored_message,
                            normalized_message,
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "interrupt 响应消息内容冲突",
                            )
                        return InterruptResponseRegistration(
                            interrupt=stored_interrupt,
                            turn=_turn_from_row(turn_row),
                            message=stored_message,
                            context_version=_response_context_version(
                                stored_interrupt,
                            ),
                            created=False,
                        )
                    if interrupt_row.status != "open":
                        raise AgentRuntimeRecordConflictError(
                            "interrupt 状态非法",
                        )
                    open_rows = (
                        await session.scalars(
                            select(PixelFlowAgentInterruptRow)
                            .where(
                                PixelFlowAgentInterruptRow.user_id == owner,
                                PixelFlowAgentInterruptRow.conversation_id
                                == conversation,
                                PixelFlowAgentInterruptRow.status == "open",
                            )
                            .with_for_update()
                        )
                    ).all()
                    if len(open_rows) != 1 or open_rows[0].interrupt_id != identity:
                        raise AgentRuntimeRecordConflictError(
                            "当前会话 open interrupt 状态非法",
                        )
                    if turn_row.status != TurnStatus.WAITING_USER.value:
                        raise AgentRuntimeRecordConflictError(
                            "interrupt 原 Turn 不在 waiting_user",
                        )
                    existing_message = await session.get(
                        PixelFlowConversationMessageRow,
                        normalized_message.message_id,
                        with_for_update=True,
                    )
                    if existing_message is not None:
                        raise AgentRuntimeRecordConflictError(
                            "Agent Runtime 响应消息 ID 已存在",
                        )

                    pre_input_context_version = _sql_runtime_context_version(
                        conversation_row,
                    )
                    next_context_version = pre_input_context_version + 1
                    response = _response_document(
                        normalized_request,
                        pre_input_context_version=pre_input_context_version,
                    )
                    interrupt_row.status = "responded"
                    interrupt_row.response_id = str(
                        normalized_request.client_response_id,
                    )
                    interrupt_row.response_json = response
                    turn_row.expected_context_version = pre_input_context_version
                    turn_row.updated_at = occurred_at
                    context = deepcopy(conversation_row.context_json or {})
                    runtime = deepcopy(context[AGENT_RUNTIME_CONTEXT_KEY])
                    runtime["context_version"] = next_context_version
                    context[AGENT_RUNTIME_CONTEXT_KEY] = runtime
                    conversation_row.context_json = context
                    conversation_row.revision += 1
                    conversation_row.updated_at = occurred_at
                    message_row = PixelFlowConversationMessageRow(
                        message_id=normalized_message.message_id,
                        conversation_id=conversation,
                        user_id=owner,
                        role=normalized_message.role,
                        content=normalized_message.content,
                        payload_json=deepcopy(normalized_message.payload),
                        created_at=occurred_at,
                    )
                    session.add(message_row)
                    responded = _interrupt_from_row(interrupt_row)
                    updated_turn = _turn_from_row(turn_row)
                    stored_message = _conversation_message_from_row(message_row)
                    await self._sql_append_interrupt_response_events(
                        session=session,
                        user_id=owner,
                        interrupt=responded,
                        turn=updated_turn,
                        message=stored_message,
                        request=normalized_request,
                        occurred_at=occurred_at,
                    )
                    await session.flush()
                    return InterruptResponseRegistration(
                        interrupt=_interrupt_from_row(interrupt_row),
                        turn=_turn_from_row(turn_row),
                        message=_conversation_message_from_row(message_row),
                        context_version=next_context_version,
                        created=True,
                    )
        except IntegrityError:
            raise AgentRuntimeRecordConflictError(
                "interrupt 响应原子登记唯一键冲突",
            ) from None

    async def _sql_append_interrupt_response_events(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        interrupt: StoredAgentInterrupt,
        turn: TurnRecord,
        message: PixelFlowConversationMessageRecord,
        request: InterruptResponseRequest,
        occurred_at: datetime,
    ) -> None:
        """锁定事件尾部并追加跨实现稳定的四类响应事件。"""

        last = (
            await session.scalars(
                select(PixelFlowAgentEventRow)
                .where(
                    PixelFlowAgentEventRow.conversation_id
                    == turn.conversation_id,
                )
                .order_by(PixelFlowAgentEventRow.sequence.desc())
                .limit(1)
                .with_for_update()
            )
        ).first()
        if last is not None and last.user_id != user_id:
            raise AgentRuntimeRecordConflictError(
                "AgentEvent conversation 已属于其他用户",
            )
        next_sequence = 1 if last is None else last.sequence + 1
        action_key = f"interrupt-response:{request.client_response_id}"
        rows: list[PixelFlowAgentEventRow] = []
        for offset, (event_type, payload, subject) in enumerate(
            _response_event_specs(
                interrupt=interrupt,
                turn=turn,
                message=message,
                client_response_id=request.client_response_id,
            )
        ):
            event = _event(
                sequence=next_sequence + offset,
                conversation_id=turn.conversation_id,
                run_id=turn.turn_id,
                occurred_at=occurred_at,
                event_type=event_type,
                payload=payload,
                identity_parts=(turn.turn_id, action_key, event_type.value, subject),
            )
            existing = await session.get(
                PixelFlowAgentEventRow,
                event.event_id,
                with_for_update=True,
            )
            if existing is not None:
                raise AgentRuntimeRecordConflictError(
                    "interrupt 响应事件身份冲突",
                )
            rows.append(
                PixelFlowAgentEventRow(
                    schema_version=1,
                    event_id=event.event_id,
                    sequence=event.sequence,
                    cursor=event.cursor,
                    conversation_id=event.conversation_id,
                    user_id=user_id,
                    run_id=event.run_id,
                    occurred_at=event.occurred_at,
                    event_type=event.type.value,
                    payload_json=event.model_dump(mode="json")["payload"],
                    delivery_status="pending",
                    delivery_attempts=0,
                    lease_owner=None,
                    lease_expires_at=None,
                    published_at=None,
                )
            )
        session.add_all(rows)

    async def get_interrupt(self, user_id: str, interrupt_id: str) -> StoredAgentInterrupt | None:
        statement = select(PixelFlowAgentInterruptRow).where(
            PixelFlowAgentInterruptRow.user_id == _require_text("user_id", user_id, 64),
            PixelFlowAgentInterruptRow.interrupt_id == _require_text("interrupt_id", interrupt_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _interrupt_from_row(row)

    async def list_due_interrupt_responses(
        self, *, now: datetime, limit: int = 100
    ) -> list[StoredAgentInterrupt]:
        normalized_now = _normalize_datetime("now", now)
        page_size = _normalize_limit(limit)
        earlier_turn = aliased(PixelFlowAgentTurnRow)
        statement = (
            select(PixelFlowAgentInterruptRow)
            .join(
                PixelFlowConversationRow,
                and_(
                    PixelFlowConversationRow.conversation_id == PixelFlowAgentInterruptRow.conversation_id,
                    PixelFlowConversationRow.user_id == PixelFlowAgentInterruptRow.user_id,
                ),
            )
            .join(
                PixelFlowAgentTurnRow,
                and_(
                    PixelFlowAgentTurnRow.turn_id == PixelFlowAgentInterruptRow.turn_id,
                    PixelFlowAgentTurnRow.user_id == PixelFlowAgentInterruptRow.user_id,
                    PixelFlowAgentTurnRow.status == TurnStatus.WAITING_USER.value,
                ),
            )
            .outerjoin(
                PixelFlowAgentCompactionLockRow,
                PixelFlowAgentCompactionLockRow.conversation_id == PixelFlowAgentInterruptRow.conversation_id,
            )
            .where(
                self._live_conversation_predicate(),
                PixelFlowAgentInterruptRow.status == "responded",
                or_(
                    PixelFlowAgentCompactionLockRow.conversation_id.is_(None),
                    PixelFlowAgentCompactionLockRow.state == "idle",
                    PixelFlowAgentCompactionLockRow.lease_expires_at <= normalized_now,
                ),
                ~exists(
                    select(1).where(
                        earlier_turn.user_id == PixelFlowAgentTurnRow.user_id,
                        earlier_turn.conversation_id == PixelFlowAgentTurnRow.conversation_id,
                        earlier_turn.inbox_sequence < PixelFlowAgentTurnRow.inbox_sequence,
                        earlier_turn.status.not_in(
                            (TurnStatus.COMPLETED.value, TurnStatus.FAILED.value)
                        ),
                    )
                ),
            )
            .order_by(PixelFlowAgentInterruptRow.opened_at.asc(), PixelFlowAgentInterruptRow.interrupt_id.asc())
            .limit(page_size)
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_interrupt_from_row(row) for row in rows]

    async def claim_interrupt_resume(
        self,
        user_id: str,
        conversation_id: str,
        interrupt_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> TurnExecutionClaim | None:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        identity = _require_text("interrupt_id", interrupt_id, 64)
        worker, normalized_now, normalized_expiry = _lease_window(
            lease_owner, now, lease_expires_at
        )
        async with self._session_factory() as session:
            async with _repository_write_transaction(session, self._sqlite_write_lock):
                locked_conversation = (
                    await session.scalars(self._conversation_statement(owner, conversation))
                ).one_or_none()
                if not _sql_conversation_is_video_live(locked_conversation):
                    return None
                interrupt = (
                    await session.scalars(
                        select(PixelFlowAgentInterruptRow)
                        .where(
                            PixelFlowAgentInterruptRow.user_id == owner,
                            PixelFlowAgentInterruptRow.conversation_id == conversation,
                            PixelFlowAgentInterruptRow.interrupt_id == identity,
                            PixelFlowAgentInterruptRow.status == "responded",
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if interrupt is None:
                    return None
                return await self._claim_in_transaction(
                    session,
                    user_id=owner,
                    conversation_id=conversation,
                    turn_id=interrupt.turn_id,
                    worker=worker,
                    now=normalized_now,
                    expires_at=normalized_expiry,
                    interrupt_resume=True,
                )

    async def get_video_state(self, user_id: str, workflow_id: str) -> VideoWorkflowStateEnvelope | None:
        statement = select(PixelFlowAgentVideoStateRow).where(
            PixelFlowAgentVideoStateRow.user_id == _require_text("user_id", user_id, 64),
            PixelFlowAgentVideoStateRow.workflow_id == _require_text("workflow_id", workflow_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else _video_state_from_row(row)

    async def list_projection_messages(
        self, user_id: str, conversation_id: str
    ) -> list[SupervisorProjectionMessage]:
        statement = (
            select(PixelFlowAgentProjectionMessageRow)
            .where(
                PixelFlowAgentProjectionMessageRow.user_id == _require_text("user_id", user_id, 64),
                PixelFlowAgentProjectionMessageRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
            )
            .order_by(PixelFlowAgentProjectionMessageRow.created_at.asc(), PixelFlowAgentProjectionMessageRow.message_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return [_message_from_row(row) for row in rows]

    async def get_open_interrupt(
        self, user_id: str, conversation_id: str
    ) -> StoredAgentInterrupt | None:
        statement = (
            select(PixelFlowAgentInterruptRow)
            .where(
                PixelFlowAgentInterruptRow.user_id == _require_text("user_id", user_id, 64),
                PixelFlowAgentInterruptRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
                PixelFlowAgentInterruptRow.status == "open",
            )
            .order_by(PixelFlowAgentInterruptRow.opened_at.asc(), PixelFlowAgentInterruptRow.interrupt_id.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        if len(rows) > 1:
            raise AgentRuntimeRecordConflictError(
                "当前会话存在多个 open interrupt",
            )
        return None if not rows else _interrupt_from_row(rows[0])

    async def get_active_workflow_id(self, user_id: str, conversation_id: str) -> str | None:
        statement = select(PixelFlowAgentConversationStateRow).where(
            PixelFlowAgentConversationStateRow.user_id == _require_text("user_id", user_id, 64),
            PixelFlowAgentConversationStateRow.conversation_id == _require_text("conversation_id", conversation_id, 64),
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).one_or_none()
        return None if row is None else row.active_workflow_id

    async def export_safe_snapshot(
        self, user_id: str, conversation_id: str
    ) -> VideoRuntimeSafeSnapshot:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        state_statement = (
            select(PixelFlowAgentVideoStateRow)
            .where(
                PixelFlowAgentVideoStateRow.user_id == owner,
                PixelFlowAgentVideoStateRow.conversation_id == conversation,
            )
            .order_by(PixelFlowAgentVideoStateRow.created_at.asc(), PixelFlowAgentVideoStateRow.workflow_id.asc())
        )
        execution_statement = select(PixelFlowAgentTurnExecutionRow).where(
            PixelFlowAgentTurnExecutionRow.user_id == owner,
            PixelFlowAgentTurnExecutionRow.conversation_id == conversation,
        )
        workflow_statement = (
            select(PixelFlowAgentWorkflowRow)
            .where(
                PixelFlowAgentWorkflowRow.user_id == owner,
                PixelFlowAgentWorkflowRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentWorkflowRow.updated_at.desc(),
                PixelFlowAgentWorkflowRow.workflow_id.desc(),
            )
        )
        turn_statement = (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id == conversation,
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
        )
        message_statement = (
            select(PixelFlowAgentProjectionMessageRow)
            .where(
                PixelFlowAgentProjectionMessageRow.user_id == owner,
                PixelFlowAgentProjectionMessageRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentProjectionMessageRow.created_at.asc(),
                PixelFlowAgentProjectionMessageRow.message_id.asc(),
            )
        )
        interrupt_statement = (
            select(PixelFlowAgentInterruptRow)
            .where(
                PixelFlowAgentInterruptRow.user_id == owner,
                PixelFlowAgentInterruptRow.conversation_id == conversation,
            )
            .order_by(PixelFlowAgentInterruptRow.opened_at.asc(), PixelFlowAgentInterruptRow.interrupt_id.asc())
        )
        active_statement = select(PixelFlowAgentConversationStateRow).where(
            PixelFlowAgentConversationStateRow.user_id == owner,
            PixelFlowAgentConversationStateRow.conversation_id == conversation,
        )
        async with self._session_factory() as session:
            async with _repository_snapshot_transaction(session, self._sqlite_write_lock):
                state_rows = (await session.scalars(state_statement)).all()
                execution_rows = (await session.scalars(execution_statement)).all()
                workflow_rows = (await session.scalars(workflow_statement)).all()
                turn_rows = (await session.scalars(turn_statement)).all()
                message_rows = (await session.scalars(message_statement)).all()
                interrupt_rows = (await session.scalars(interrupt_statement)).all()
                active_row = (await session.scalars(active_statement)).one_or_none()
                schedules = {
                    row.turn_id: (
                        None
                        if row.next_attempt_at is None
                        else _database_utc(row.next_attempt_at)
                    )
                    for row in execution_rows
                }
                return VideoRuntimeSafeSnapshot(
                    conversation_id=conversation,
                    active_workflow_id=(
                        None if active_row is None else active_row.active_workflow_id
                    ),
                    workflow_states=tuple(
                        _video_state_from_row(row) for row in state_rows
                    ),
                    workflows=tuple(_workflow_from_row(row) for row in workflow_rows),
                    turns=tuple(
                        OwnedTurnRecord(
                            user_id=owner,
                            turn=_turn_from_row(row),
                            next_attempt_at=schedules.get(row.turn_id),
                        )
                        for row in turn_rows
                    ),
                    messages=tuple(_message_from_row(row) for row in message_rows),
                    interrupts=tuple(_interrupt_from_row(row) for row in interrupt_rows),
                )

    async def read_versioned_context_snapshot(
        self,
        user_id: str,
        conversation_id: str,
        *,
        expected_context_version: int,
    ) -> VideoRuntimeContextSnapshot:
        """在同一数据库一致读事务内返回全部 Context 原料。"""

        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        if type(expected_context_version) is not int or expected_context_version < 0:
            raise VideoRuntimeContextSnapshotConflictError(
                "context_snapshot_version_conflict"
            )
        conversation_statement = select(PixelFlowConversationRow).where(
            PixelFlowConversationRow.user_id == owner,
            PixelFlowConversationRow.conversation_id == conversation,
        )
        task_message_statement = (
            select(PixelFlowConversationMessageRow)
            .where(
                PixelFlowConversationMessageRow.user_id == owner,
                PixelFlowConversationMessageRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowConversationMessageRow.created_at.asc(),
                PixelFlowConversationMessageRow.message_id.asc(),
            )
        )
        state_statement = (
            select(PixelFlowAgentVideoStateRow)
            .where(
                PixelFlowAgentVideoStateRow.user_id == owner,
                PixelFlowAgentVideoStateRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentVideoStateRow.created_at.asc(),
                PixelFlowAgentVideoStateRow.workflow_id.asc(),
            )
        )
        execution_statement = select(PixelFlowAgentTurnExecutionRow).where(
            PixelFlowAgentTurnExecutionRow.user_id == owner,
            PixelFlowAgentTurnExecutionRow.conversation_id == conversation,
        )
        workflow_statement = (
            select(PixelFlowAgentWorkflowRow)
            .where(
                PixelFlowAgentWorkflowRow.user_id == owner,
                PixelFlowAgentWorkflowRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentWorkflowRow.updated_at.desc(),
                PixelFlowAgentWorkflowRow.workflow_id.desc(),
            )
        )
        turn_statement = (
            select(PixelFlowAgentTurnRow)
            .where(
                PixelFlowAgentTurnRow.user_id == owner,
                PixelFlowAgentTurnRow.conversation_id == conversation,
            )
            .order_by(PixelFlowAgentTurnRow.inbox_sequence.asc())
        )
        projection_message_statement = (
            select(PixelFlowAgentProjectionMessageRow)
            .where(
                PixelFlowAgentProjectionMessageRow.user_id == owner,
                PixelFlowAgentProjectionMessageRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentProjectionMessageRow.created_at.asc(),
                PixelFlowAgentProjectionMessageRow.message_id.asc(),
            )
        )
        interrupt_statement = (
            select(PixelFlowAgentInterruptRow)
            .where(
                PixelFlowAgentInterruptRow.user_id == owner,
                PixelFlowAgentInterruptRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentInterruptRow.opened_at.asc(),
                PixelFlowAgentInterruptRow.interrupt_id.asc(),
            )
        )
        active_statement = select(PixelFlowAgentConversationStateRow).where(
            PixelFlowAgentConversationStateRow.user_id == owner,
            PixelFlowAgentConversationStateRow.conversation_id == conversation,
        )
        summary_statement = (
            select(PixelFlowAgentContextSummaryRow)
            .where(
                PixelFlowAgentContextSummaryRow.user_id == owner,
                PixelFlowAgentContextSummaryRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentContextSummaryRow.version.asc(),
                PixelFlowAgentContextSummaryRow.created_at.asc(),
                PixelFlowAgentContextSummaryRow.summary_id.asc(),
            )
        )
        event_statement = (
            select(PixelFlowAgentEventRow)
            .where(
                PixelFlowAgentEventRow.user_id == owner,
                PixelFlowAgentEventRow.conversation_id == conversation,
            )
            .order_by(
                PixelFlowAgentEventRow.sequence.asc(),
                PixelFlowAgentEventRow.event_id.asc(),
            )
        )
        async with self._session_factory() as session:
            async with _repository_snapshot_transaction(
                session,
                self._sqlite_write_lock,
            ):
                conversation_row = (
                    await session.scalars(conversation_statement)
                ).one_or_none()
                if conversation_row is None:
                    raise LookupError("对话不存在或不属于当前用户")
                try:
                    current_context_version = _sql_runtime_context_version(
                        conversation_row
                    )
                except AgentRuntimeRecordConflictError as exc:
                    raise VideoRuntimeContextSnapshotConflictError(
                        "context_snapshot_version_conflict"
                    ) from exc
                if expected_context_version > current_context_version:
                    raise VideoRuntimeContextSnapshotConflictError(
                        "context_snapshot_version_conflict"
                    )
                task_message_rows = (
                    await session.scalars(task_message_statement)
                ).all()
                state_rows = (await session.scalars(state_statement)).all()
                execution_rows = (await session.scalars(execution_statement)).all()
                workflow_rows = (await session.scalars(workflow_statement)).all()
                turn_rows = (await session.scalars(turn_statement)).all()
                projection_message_rows = (
                    await session.scalars(projection_message_statement)
                ).all()
                interrupt_rows = (await session.scalars(interrupt_statement)).all()
                active_row = (
                    await session.scalars(active_statement)
                ).one_or_none()
                summary_rows = (await session.scalars(summary_statement)).all()
                event_rows = (await session.scalars(event_statement)).all()
                schedules = {
                    row.turn_id: (
                        None
                        if row.next_attempt_at is None
                        else _database_utc(row.next_attempt_at)
                    )
                    for row in execution_rows
                }
                runtime = VideoRuntimeSafeSnapshot(
                    conversation_id=conversation,
                    active_workflow_id=(
                        None if active_row is None else active_row.active_workflow_id
                    ),
                    workflow_states=tuple(
                        _video_state_from_row(row) for row in state_rows
                    ),
                    workflows=tuple(_workflow_from_row(row) for row in workflow_rows),
                    turns=tuple(
                        OwnedTurnRecord(
                            user_id=owner,
                            turn=_turn_from_row(row),
                            next_attempt_at=schedules.get(row.turn_id),
                        )
                        for row in turn_rows
                    ),
                    messages=tuple(
                        _message_from_row(row) for row in projection_message_rows
                    ),
                    interrupts=tuple(
                        _interrupt_from_row(row) for row in interrupt_rows
                    ),
                )
                return VideoRuntimeContextSnapshot(
                    conversation_id=conversation,
                    expected_context_version=expected_context_version,
                    current_context_version=current_context_version,
                    runtime=runtime,
                    task_messages=tuple(
                        _conversation_message_from_row(row)
                        for row in task_message_rows
                    ),
                    summaries=tuple(_summary_from_row(row) for row in summary_rows),
                    events=tuple(_event_from_row(row) for row in event_rows),
                )

    async def commit_operation_completion(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        messages: tuple[SupervisorProjectionMessage, ...],
        open_interrupt: StoredAgentInterrupt | None = None,
        occurred_at: datetime,
    ) -> WorkflowRecord:
        owner = _require_text("user_id", user_id, 64)
        normalized_time = _normalize_datetime("occurred_at", occurred_at)
        normalized_claim = EventDeliveryClaim.model_validate(claim.model_dump(mode="python"))
        synthetic_claim = TurnExecutionClaim(
            user_id=owner,
            turn=TurnRecord(
                turn_id=workflow_state.last_turn_id,
                conversation_id=workflow_state.conversation_id,
                client_input_id=UUID(int=0),
                status=TurnStatus.PROCESSING,
                target_workflow_id=workflow_state.workflow_id,
                decision=None,
                expected_context_version=0,
                created_at=workflow_state.created_at,
            ),
            lease_owner=normalized_claim.lease_owner,
            lease_token=UUID(int=0),
            lease_expires_at=normalized_claim.lease_expires_at,
            attempt=1,
        )
        commit = VideoTurnCommit(
            decision=ActionDecision(
                action="continue_workflow",
                intent="video",
                target_workflow_id=workflow.workflow_id,
                target_stage=workflow.current_stage,
                confidence=1,
                reason_code="operation_completion",
                idempotency_key=normalized_claim.event.event_id,
            ),
            turn_status=(
                TurnStatus.WAITING_USER
                if open_interrupt is not None
                else TurnStatus.COMPLETED
            ),
            workflow_state=workflow_state,
            workflow=workflow,
            expected_workflow_version=expected_workflow_version,
            messages=messages,
            open_interrupt=open_interrupt,
            occurred_at=normalized_time,
        )
        _, normalized_commit = _validate_commit_contract(synthetic_claim, commit)
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(session, self._sqlite_write_lock):
                    job_id = normalized_claim.event.payload.get("job_id")
                    operation_row = None
                    if type(job_id) is str:
                        operation_row = (
                            await session.scalars(
                                select(PixelFlowAgentOperationRow)
                                .where(
                                    PixelFlowAgentOperationRow.user_id == owner,
                                    PixelFlowAgentOperationRow.job_id == job_id,
                                )
                                .with_for_update()
                            )
                        ).one_or_none()
                    operation = (
                        None
                        if operation_row is None
                        else _operation_from_row(operation_row)
                    )
                    turn_row = None
                    if normalized_commit.open_interrupt is not None:
                        turn_row = (
                            await session.scalars(
                                select(PixelFlowAgentTurnRow)
                                .where(
                                    PixelFlowAgentTurnRow.user_id == owner,
                                    PixelFlowAgentTurnRow.conversation_id
                                    == workflow_state.conversation_id,
                                    PixelFlowAgentTurnRow.turn_id
                                    == workflow_state.last_turn_id,
                                )
                                .with_for_update()
                            )
                        ).one_or_none()
                        if (
                            turn_row is None
                            or turn_row.status
                            not in {
                                TurnStatus.COMPLETED.value,
                                TurnStatus.WAITING_USER.value,
                            }
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "Operation 完成中断的原 Turn 状态不允许恢复",
                            )
                    _validate_operation_completion_binding(
                        user_id=owner,
                        event=normalized_claim.event,
                        operation=operation,
                        workflow_state=normalized_commit.workflow_state,
                        workflow=normalized_commit.workflow,
                    )
                    await self._sql_compare_and_set_state(session, normalized_commit)
                    await self._sql_upsert_workflow_and_active(
                        session,
                        synthetic_claim,
                        normalized_commit,
                    )
                    await self._sql_upsert_messages(
                        session,
                        owner,
                        normalized_commit.messages,
                    )
                    await self._sql_apply_interrupt_transition(
                        session,
                        synthetic_claim,
                        normalized_commit,
                    )
                    event_row = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow)
                            .where(
                                PixelFlowAgentEventRow.user_id == owner,
                                PixelFlowAgentEventRow.event_id == normalized_claim.event.event_id,
                                PixelFlowAgentEventRow.event_type == AgentEventType.EXTERNAL_JOB_STATE_CHANGED.value,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if event_row is not None:
                        _validate_operation_completion_binding(
                            user_id=owner,
                            event=_event_from_row(event_row),
                            operation=operation,
                            workflow_state=normalized_commit.workflow_state,
                            workflow=normalized_commit.workflow,
                        )
                    expiry = None if event_row is None or event_row.lease_expires_at is None else _database_utc(event_row.lease_expires_at)
                    if (
                        event_row is None
                        or _event_from_row(event_row) != normalized_claim.event
                        or event_row.delivery_status != "delivering"
                        or event_row.delivery_attempts != normalized_claim.delivery_attempts
                        or event_row.lease_owner != normalized_claim.lease_owner
                        or expiry is None
                        or expiry != normalized_claim.lease_expires_at
                        or normalized_time >= expiry
                    ):
                        raise TurnExecutionLeaseConflictError(normalized_claim.event.event_id)
                    await self._sql_append_events(
                        session,
                        synthetic_claim,
                        normalized_commit,
                        action_key=normalized_claim.event.event_id,
                        include_turn_terminal=(
                            normalized_commit.open_interrupt is not None
                        ),
                    )
                    if turn_row is not None:
                        turn_row.status = TurnStatus.WAITING_USER.value
                        turn_row.updated_at = normalized_time
                    event_row.delivery_status = "published"
                    event_row.published_at = normalized_time
                    await session.flush()
                    return _clone(workflow)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("Operation 完成事件提交唯一键冲突") from None

    async def commit_operation_quota_state(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        open_interrupt: StoredAgentInterrupt | None,
        close_interrupt_revision: int | None,
        occurred_at: datetime,
    ) -> WorkflowRecord:
        """在单个 SQL 事务中公开 quota overlay 或恢复原领域状态。"""

        from pixelflow.agent_runtime.jobs import (
            OperationQuotaEventPayload,
            OperationQuotaState,
        )
        from pixelflow.agent_workflows.video.live_quota import (
            VideoOperationQuotaProjectionService,
        )

        owner = _require_text("user_id", user_id, 64)
        _normalize_datetime("occurred_at", occurred_at)
        normalized_claim = EventDeliveryClaim.model_validate(
            claim.model_dump(mode="python")
        )
        payload = OperationQuotaEventPayload.model_validate(
            normalized_claim.event.payload
        )
        is_pause = payload.quota_state is OperationQuotaState.PAUSED
        if is_pause:
            if open_interrupt is None or close_interrupt_revision is not None:
                raise AgentRuntimeRecordConflictError("quota pause 投影目标不完整")
        elif open_interrupt is not None or close_interrupt_revision != (
            payload.quota_pause_revision
        ):
            raise AgentRuntimeRecordConflictError("quota resume 投影目标不完整")
        target_state = VideoWorkflowStateEnvelope.model_validate(
            workflow_state.model_dump(mode="python")
        )
        target_workflow = WorkflowRecord.model_validate(
            workflow.model_dump(mode="python")
        )
        target_interrupt = (
            None
            if open_interrupt is None
            else StoredAgentInterrupt.model_validate(
                open_interrupt.model_dump(mode="python")
            )
        )
        try:
            async with self._session_factory() as session:
                async with _repository_write_transaction(
                    session,
                    self._sqlite_write_lock,
                ):
                    operation_row = (
                        await session.scalars(
                            select(PixelFlowAgentOperationRow)
                            .where(
                                PixelFlowAgentOperationRow.user_id == owner,
                                PixelFlowAgentOperationRow.job_id
                                == payload.job_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    event_row = (
                        await session.scalars(
                            select(PixelFlowAgentEventRow)
                            .where(
                                PixelFlowAgentEventRow.user_id == owner,
                                PixelFlowAgentEventRow.event_id
                                == normalized_claim.event.event_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    if operation_row is None or event_row is None:
                        raise AgentRuntimeRecordConflictError(
                            "quota pause Event 或 Operation 不存在",
                        )
                    operation = _operation_from_row(operation_row)
                    stored_event = _event_from_row(event_row)
                    state_row = (
                        await session.scalars(
                            select(PixelFlowAgentVideoStateRow)
                            .where(
                                PixelFlowAgentVideoStateRow.user_id == owner,
                                PixelFlowAgentVideoStateRow.workflow_id
                                == operation.workflow_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    workflow_row = (
                        await session.scalars(
                            select(PixelFlowAgentWorkflowRow)
                            .where(
                                PixelFlowAgentWorkflowRow.user_id == owner,
                                PixelFlowAgentWorkflowRow.workflow_id
                                == operation.workflow_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    turn_row = (
                        await session.scalars(
                            select(PixelFlowAgentTurnRow)
                            .where(
                                PixelFlowAgentTurnRow.user_id == owner,
                                PixelFlowAgentTurnRow.conversation_id
                                == target_state.conversation_id,
                                PixelFlowAgentTurnRow.turn_id
                                == target_state.last_turn_id,
                            )
                            .with_for_update()
                        )
                    ).one_or_none()
                    interrupt_rows = list(
                        (
                            await session.scalars(
                                select(PixelFlowAgentInterruptRow)
                                .where(
                                    PixelFlowAgentInterruptRow.user_id == owner,
                                    PixelFlowAgentInterruptRow.conversation_id
                                    == operation.conversation_id,
                                    PixelFlowAgentInterruptRow.workflow_id
                                    == operation.workflow_id,
                                    PixelFlowAgentInterruptRow.kind
                                    == "authorization_required",
                                    PixelFlowAgentInterruptRow.reason_code
                                    == "authorization_required",
                                )
                                .with_for_update()
                            )
                        ).all()
                    )
                    matching_interrupts = [
                        _interrupt_from_row(item)
                        for item in interrupt_rows
                        if _matches_quota_authorization_interrupt(
                            _interrupt_from_row(item),
                            operation=operation,
                            quota_pause_revision=payload.quota_pause_revision,
                        )
                    ]
                    unclosed_interrupts = [
                        item
                        for item in matching_interrupts
                        if item.status != "closed"
                    ]
                    if len(unclosed_interrupts) > 1:
                        raise AgentRuntimeRecordConflictError(
                            "quota resume 匹配到多个未关闭授权中断",
                        )
                    stored_interrupt = (
                        None
                        if target_interrupt is None
                        else next(
                            (
                                item
                                for item in matching_interrupts
                                if item.interrupt_id
                                == target_interrupt.interrupt_id
                            ),
                            None,
                        )
                    )
                    close_interrupt = (
                        unclosed_interrupts[0]
                        if unclosed_interrupts
                        else next(
                            (
                                item
                                for item in matching_interrupts
                                if item.status == "closed"
                            ),
                            None,
                        )
                    )
                    expiry = (
                        None
                        if event_row.lease_expires_at is None
                        else _database_utc(event_row.lease_expires_at)
                    )
                    published_at = (
                        None
                        if event_row.published_at is None
                        else _database_utc(event_row.published_at)
                    )
                    if event_row.delivery_status == "published":
                        if (
                            stored_event != normalized_claim.event
                            or event_row.delivery_attempts
                            != normalized_claim.delivery_attempts
                            or event_row.lease_owner
                            != normalized_claim.lease_owner
                            or expiry != normalized_claim.lease_expires_at
                            or published_at is None
                            or published_at >= normalized_claim.lease_expires_at
                            or state_row is None
                            or _video_state_from_row(state_row) != target_state
                            or workflow_row is None
                            or _workflow_from_row(workflow_row)
                            != target_workflow
                            or turn_row is None
                            or (
                                is_pause
                                and (
                                    stored_interrupt != target_interrupt
                                    or turn_row.status
                                    != TurnStatus.WAITING_USER.value
                                )
                            )
                            or (
                                not is_pause
                                and (
                                    close_interrupt is None
                                    or close_interrupt.status != "closed"
                                    or turn_row.status
                                    != TurnStatus.COMPLETED.value
                                )
                            )
                        ):
                            raise AgentRuntimeRecordConflictError(
                                "quota Event 已发布投影与重放目标不一致",
                            )
                        return _clone(target_workflow)
                    completed_at = _normalize_datetime(
                        "completed_at",
                        self._completion_clock(),
                    )
                    if (
                        stored_event != normalized_claim.event
                        or event_row.delivery_status != "delivering"
                        or event_row.delivery_attempts
                        != normalized_claim.delivery_attempts
                        or event_row.lease_owner != normalized_claim.lease_owner
                        or expiry != normalized_claim.lease_expires_at
                        or expiry is None
                        or completed_at >= expiry
                        or state_row is None
                        or turn_row is None
                        or turn_row.conversation_id
                        != target_state.conversation_id
                        or (
                            is_pause
                            and (
                                operation.next_poll_at is not None
                                or turn_row.status
                                not in {
                                    TurnStatus.COMPLETED.value,
                                    TurnStatus.WAITING_USER.value,
                                }
                            )
                        )
                        or (
                            not is_pause
                            and (
                                operation.next_poll_at is None
                                or close_interrupt is None
                                or turn_row.status
                                not in {
                                    TurnStatus.PROCESSING.value,
                                    TurnStatus.COMPLETED.value,
                                }
                            )
                        )
                    ):
                        raise TurnExecutionLeaseConflictError(
                            normalized_claim.event.event_id,
                        )
                    current_state = _video_state_from_row(state_row)
                    projection = VideoOperationQuotaProjectionService().build(
                        user_id=owner,
                        envelope=current_state,
                        operation=operation,
                        quota_event=stored_event,
                    )
                    if (
                        expected_workflow_version
                        != current_state.workflow_version
                        or projection.workflow_state != target_state
                        or projection.workflow != target_workflow
                        or projection.open_interrupt != target_interrupt
                        or projection.close_interrupt_revision
                        != close_interrupt_revision
                    ):
                        raise VideoWorkflowStateConflictError(
                            "quota Event 投影与权威目标不一致",
                        )
                    synthetic_claim, commit = _quota_projection_commit(
                        claim=normalized_claim,
                        user_id=owner,
                        workflow_state=target_state,
                        workflow=target_workflow,
                        expected_workflow_version=expected_workflow_version,
                        open_interrupt=target_interrupt,
                        close_interrupt_id=(
                            None
                            if is_pause or close_interrupt is None
                            else close_interrupt.interrupt_id
                        ),
                        turn_status=(
                            TurnStatus.WAITING_USER
                            if is_pause
                            else TurnStatus.COMPLETED
                        ),
                        occurred_at=completed_at,
                    )
                    await self._sql_compare_and_set_state(session, commit)
                    await self._sql_upsert_workflow_and_active(
                        session,
                        synthetic_claim,
                        commit,
                    )
                    await self._sql_apply_interrupt_transition(
                        session,
                        synthetic_claim,
                        commit,
                    )
                    await self._sql_append_events(
                        session,
                        synthetic_claim,
                        commit,
                        action_key=stored_event.event_id,
                        include_turn_terminal=(
                            is_pause
                            or turn_row.status
                            == TurnStatus.PROCESSING.value
                        ),
                    )
                    turn_row.status = (
                        TurnStatus.WAITING_USER.value
                        if is_pause
                        else TurnStatus.COMPLETED.value
                    )
                    turn_row.updated_at = completed_at
                    event_row.delivery_status = "published"
                    event_row.published_at = completed_at
                    await session.flush()
                    return _clone(target_workflow)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError(
                "Operation quota Event 提交唯一键冲突",
            ) from None


__all__ = [
    "MemoryVideoRuntimeRepository",
    "OwnedTurnRecord",
    "SQLVideoRuntimeRepository",
    "StoredAgentInterrupt",
    "SupervisorProjectionMessage",
    "TurnExecutionClaim",
    "TurnExecutionLeaseConflictError",
    "VideoRuntimeRepository",
    "VideoRuntimeSafeSnapshot",
    "VideoTurnCommit",
    "VideoWorkflowStateConflictError",
]
