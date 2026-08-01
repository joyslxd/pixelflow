"""视频 live Runtime 的 Turn 租约、状态 CAS 与原子投影 Repository。"""

from __future__ import annotations

import base64
import hashlib
import json
from asyncio import Lock
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import ConfigDict, Field, JsonValue, field_serializer, field_validator, model_validator
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
from pixelflow.tasks import AGENT_RUNTIME_CONTEXT_KEY, PixelFlowTaskStore
from pixelflow.tasks.model import PixelFlowConversationRow

from ..contracts import (
    ActionDecision,
    AgentEvent,
    AgentEventType,
    AgentInterruptProjection,
    ExternalJobRef,
    ExternalJobStatus,
    TurnRecord,
    TurnStatus,
    WorkflowRecord,
)
from ..contracts.base import ContractModel
from .compaction_queue import MemoryCompactionQueueRepository, SQLCompactionQueueRepository
from .models import (
    PixelFlowAgentCompactionLockRow,
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
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
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
    update_active_workflow: bool = False
    active_workflow_id: str | None = Field(default=None, min_length=1)
    error_reason_code: str | None = None
    occurred_at: datetime

    def model_post_init(self, context: object, /) -> None:
        del context
        object.__setattr__(self, "decision", _freeze_decision(self.decision))
        if self.workflow is not None:
            object.__setattr__(self, "workflow", _freeze_workflow(self.workflow))

    @field_serializer("decision")
    def serialize_decision(self, value: ActionDecision) -> object:
        return value.model_dump(mode="python")

    @field_serializer("workflow")
    def serialize_workflow(self, value: WorkflowRecord | None) -> object:
        return None if value is None else value.model_dump(mode="python")

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

    async def get_active_workflow_id(
        self, user_id: str, conversation_id: str
    ) -> str | None: ...

    async def export_safe_snapshot(
        self, user_id: str, conversation_id: str
    ) -> VideoRuntimeSafeSnapshot: ...

    async def commit_operation_completion(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        messages: tuple[SupervisorProjectionMessage, ...],
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
        if state.last_action_key != normalized_commit.decision.idempotency_key:
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

    def __init__(self, *, task_store: PixelFlowTaskStore) -> None:
        super().__init__()
        self._task_store = task_store
        self._video_states: dict[tuple[str, str], VideoWorkflowStateEnvelope] = {}
        self._turn_executions: dict[tuple[str, str], _MemoryTurnExecution] = {}
        self._projection_messages: dict[tuple[str, str], SupervisorProjectionMessage] = {}
        self._interrupts: dict[tuple[str, str], StoredAgentInterrupt] = {}
        self._active_workflows: dict[tuple[str, str], str | None] = {}

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
            replay = self._is_idempotent_turn_replay(normalized_claim, normalized_commit)
            if replay is not None:
                return replay
            before = self._snapshot_live_runtime_state()
            try:
                key = (normalized_claim.user_id, normalized_claim.turn.turn_id)
                execution = self._turn_executions.get(key)
                if not self._execution_matches(execution, normalized_claim, normalized_commit.occurred_at):
                    raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
                current = self._turns.get(key)
                if current is None or current.status is not TurnStatus.PROCESSING:
                    raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
                self._compare_and_set_video_state(normalized_commit)
                self._upsert_workflow_and_active_projection(normalized_claim, normalized_commit)
                self._upsert_projection_messages(normalized_claim.user_id, normalized_commit.messages)
                self._apply_interrupt_transition(normalized_claim, normalized_commit)
                async with self._event_write_lock:
                    self._append_events(normalized_claim, normalized_commit)
                finished = current.model_copy(
                    update={
                        "status": normalized_commit.turn_status,
                        "decision": ActionDecision.model_validate(
                            normalized_commit.decision.model_dump(mode="python")
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
                execution.last_reason_code = _video_turn_commit_identity(normalized_commit)
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
        return None if not items else _clone_interrupt(items[0])

    async def get_active_workflow_id(self, user_id: str, conversation_id: str) -> str | None:
        return self._active_workflows.get(
            (_require_text("user_id", user_id, 64), _require_text("conversation_id", conversation_id, 64))
        )

    async def export_safe_snapshot(
        self, user_id: str, conversation_id: str
    ) -> VideoRuntimeSafeSnapshot:
        owner = _require_text("user_id", user_id, 64)
        conversation = _require_text("conversation_id", conversation_id, 64)
        async with self._compaction_write_lock:
            states = [
                state for (record_owner, _), state in self._video_states.items()
                if record_owner == owner and state.conversation_id == conversation
            ]
            states.sort(key=lambda item: (item.created_at, item.workflow_id))
            workflows = [
                workflow for (record_owner, _), workflow in self._workflows.items()
                if record_owner == owner and workflow.conversation_id == conversation
            ]
            workflows.sort(
                key=lambda item: (item.updated_at, item.workflow_id),
                reverse=True,
            )
            turn_keys = [
                key for key, turn in self._turns.items()
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
                item for (record_owner, _), item in self._projection_messages.items()
                if record_owner == owner and item.conversation_id == conversation
            ]
            messages.sort(key=lambda item: (item.created_at, item.message_id))
            interrupts = [
                item for (record_owner, _), item in self._interrupts.items()
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

    async def commit_operation_completion(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        messages: tuple[SupervisorProjectionMessage, ...],
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
            turn_status=TurnStatus.COMPLETED,
            workflow_state=workflow_state,
            workflow=workflow,
            expected_workflow_version=expected_workflow_version,
            messages=messages,
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
                        self._append_events(
                            synthetic_claim,
                            normalized_commit,
                            operation_event_id=normalized_claim.event.event_id,
                            include_turn_terminal=False,
                        )
                        delivery = self._event_delivery[event_key]
                        delivery.status = "published"
                        delivery.published_at = normalized_time
                        return _clone(workflow)
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
    ) -> None:
        super().__init__(session_factory)
        self._task_store = task_store
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
                    replay = await self._sql_idempotent_replay(session, normalized_claim, normalized_commit)
                    if replay is not None:
                        return replay
                    lease_result = await session.execute(
                        update(PixelFlowAgentTurnExecutionRow)
                        .where(
                            PixelFlowAgentTurnExecutionRow.user_id == normalized_claim.user_id,
                            PixelFlowAgentTurnExecutionRow.conversation_id == normalized_claim.turn.conversation_id,
                            PixelFlowAgentTurnExecutionRow.turn_id == normalized_claim.turn.turn_id,
                            PixelFlowAgentTurnExecutionRow.lease_owner == normalized_claim.lease_owner,
                            PixelFlowAgentTurnExecutionRow.lease_token == str(normalized_claim.lease_token),
                            PixelFlowAgentTurnExecutionRow.lease_expires_at > normalized_commit.occurred_at,
                        )
                        .values(
                            lease_owner=None,
                            lease_token=None,
                            lease_expires_at=None,
                            next_attempt_at=None,
                            last_reason_code=_video_turn_commit_identity(normalized_commit),
                            updated_at=normalized_commit.occurred_at,
                        )
                    )
                    if lease_result.rowcount != 1:
                        raise TurnExecutionLeaseConflictError(normalized_claim.turn.turn_id)
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
                    await self._sql_append_events(session, normalized_claim, normalized_commit)
                    turn.status = normalized_commit.turn_status.value
                    turn.decision_json = normalized_commit.decision.model_dump(mode="json")
                    turn.target_workflow_id = (
                        normalized_commit.workflow.workflow_id
                        if normalized_commit.workflow is not None
                        else normalized_commit.decision.target_workflow_id
                    )
                    turn.updated_at = normalized_commit.occurred_at
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
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.scalars(statement)).first()
        return None if row is None else _interrupt_from_row(row)

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

    async def commit_operation_completion(
        self,
        claim: EventDeliveryClaim,
        *,
        user_id: str,
        workflow_state: VideoWorkflowStateEnvelope,
        workflow: WorkflowRecord,
        expected_workflow_version: int,
        messages: tuple[SupervisorProjectionMessage, ...],
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
            turn_status=TurnStatus.COMPLETED,
            workflow_state=workflow_state,
            workflow=workflow,
            expected_workflow_version=expected_workflow_version,
            messages=messages,
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
                        include_turn_terminal=False,
                    )
                    event_row.delivery_status = "published"
                    event_row.published_at = normalized_time
                    await session.flush()
                    return _clone(workflow)
        except IntegrityError:
            raise AgentRuntimeRecordConflictError("Operation 完成事件提交唯一键冲突") from None


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
