"""R1 assist 会话 Runtime 的应用服务与 Snapshot 投影。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    ConversationRevisionConflictError,
    PixelFlowConversationMessageRecord,
    PixelFlowTaskStore,
    sanitize_client_conversation_context,
)

from .config import AgentRuntimeConfig
from .context import RepositoryCompactionEventOutbox
from .contracts import (
    AgentEvent,
    AgentEventType,
    OrchestrationMode,
    TurnRecord,
    TurnStartRequest,
    TurnStatus,
)
from .persistence import (
    CompactionQueueRepository,
    TurnRegistrationContextConflictError,
    TurnRegistrationUnavailableError,
    make_turn_registration_store,
)
from .runtime_compaction import AgentContextCompactor

logger = logging.getLogger(__name__)

RuntimeRunStatus = Literal[
    "idle",
    "running",
    "waiting_user",
    "paused",
    "failed",
    "completed",
]
RuntimeCompressionStatus = Literal["idle", "compacting", "blocked"]
RuntimeInputStatus = Literal[
    "sending",
    "queued",
    "processing",
    "accepted",
    "failed",
]


class AgentRuntimeUnavailableError(RuntimeError):
    """对话没有启用 Agent Runtime，拒绝访问新入口。"""


class AgentRuntimeContextConflictError(RuntimeError):
    """调用方上下文版本已经落后于权威版本。"""

    def __init__(self, expected_context_version: int, current_context_version: int):
        self.expected_context_version = expected_context_version
        self.current_context_version = current_context_version
        super().__init__("Agent Runtime context version conflict")


@dataclass(frozen=True)
class AgentRuntimeConversationAssignment:
    """记录新对话一次性冻结的 Runtime 与业务编排归属。"""

    orchestration_mode: OrchestrationMode
    orchestration_version: Literal[1]
    context: dict[str, Any]


class _RuntimeResponseModel(BaseModel):
    """统一启用别名输出，保证 Python 与前端字段命名各自稳定。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentTurnStartResponse(_RuntimeResponseModel):
    """Turn 创建或幂等复用结果。"""

    turn_id: str
    run_id: str
    status: Literal["accepted", "queued"]
    context_version: int = Field(ge=0)


class AgentTurnJobResponse(_RuntimeResponseModel):
    """SSE 断开时轮询原 Turn 的当前持久化状态。"""

    turn_id: str
    run_id: str
    status: Literal[
        "accepted",
        "queued",
        "processing",
        "waiting_user",
        "completed",
        "failed",
    ]
    context_version: int = Field(ge=0)


class RuntimeRunProjection(_RuntimeResponseModel):
    run_id: str | None = Field(serialization_alias="runId")
    status: RuntimeRunStatus
    updated_at: datetime | None = Field(serialization_alias="updatedAt")


class RuntimeCompressionProjection(_RuntimeResponseModel):
    status: RuntimeCompressionStatus
    progress_percent: int | None = Field(
        default=None,
        ge=0,
        le=100,
        serialization_alias="progressPercent",
    )
    queued_input_count: int = Field(
        default=0,
        ge=0,
        serialization_alias="queuedInputCount",
    )
    last_outcome: Literal["completed", "failed"] | None = Field(
        default=None,
        serialization_alias="lastOutcome",
    )
    updated_at: datetime | None = Field(serialization_alias="updatedAt")


class RuntimeInputProjection(_RuntimeResponseModel):
    client_input_id: str = Field(serialization_alias="clientInputId")
    turn_id: str | None = Field(serialization_alias="turnId")
    status: RuntimeInputStatus
    queue_position: int | None = Field(
        default=None,
        ge=1,
        serialization_alias="queuePosition",
    )
    updated_at: datetime | None = Field(serialization_alias="updatedAt")


class RuntimeResumeProjection(_RuntimeResponseModel):
    cursor: str | None
    sequence: int = Field(ge=0)


class AgentRuntimeSnapshotResponse(_RuntimeResponseModel):
    """前端 Reducer 可直接恢复的 R1 Snapshot。"""

    conversation_id: str = Field(serialization_alias="conversationId")
    run: RuntimeRunProjection
    compression: RuntimeCompressionProjection
    input_queue: list[RuntimeInputProjection] = Field(
        default_factory=list,
        serialization_alias="inputQueue",
    )
    messages: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    interrupt: dict[str, Any] | None = None
    resume: RuntimeResumeProjection
    context_version: int = Field(ge=0)


def _runtime_context(context: dict[str, Any]) -> dict[str, Any] | None:
    value = context.get(AGENT_RUNTIME_CONTEXT_KEY)
    return value if isinstance(value, dict) else None


def _context_version(context: dict[str, Any]) -> int:
    runtime = _runtime_context(context)
    value = None if runtime is None else runtime.get("context_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentRuntimeUnavailableError("对话缺少合法的 Agent Runtime 上下文版本")
    return value


def _runtime_enabled(context: dict[str, Any]) -> bool:
    runtime = _runtime_context(context)
    return runtime is not None and runtime.get("mode") in {
        "shadow",
        "assist",
        "primary",
    }


def _message_id(conversation_id: str, client_input_id: UUID) -> str:
    key = f"pixelflow-conversation-message:{conversation_id}:{client_input_id}"
    return uuid5(NAMESPACE_URL, key).hex


def _turn_id(conversation_id: str, client_input_id: UUID) -> str:
    """按同一幂等键生成跨进程稳定的 Turn ID。"""

    key = f"pixelflow-agent-turn:{conversation_id}:{client_input_id}"
    return f"turn_{uuid5(NAMESPACE_URL, key).hex}"


def _turn_status_for_response(status: TurnStatus) -> Literal["accepted", "queued"]:
    return "queued" if status is TurnStatus.QUEUED else "accepted"


def _input_status(status: TurnStatus) -> RuntimeInputStatus:
    if status is TurnStatus.QUEUED:
        return "queued"
    if status is TurnStatus.PROCESSING:
        return "processing"
    if status is TurnStatus.FAILED:
        return "failed"
    return "accepted"


class AgentRuntimeService:
    """组合旧消息 Store、Turn Inbox、压缩租约和 Event Outbox。"""

    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        repository: CompactionQueueRepository,
        task_store: PixelFlowTaskStore,
        context_compactor: AgentContextCompactor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = AgentRuntimeConfig.model_validate(
            config.model_dump(mode="python"),
        )
        self.repository = repository
        self.task_store = task_store
        self._context_compactor = context_compactor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._turn_registration_store = make_turn_registration_store(
            repository=repository,
            task_store=task_store,
        )
        self._event_outbox = RepositoryCompactionEventOutbox(
            repository=repository,
        )

    def assignment_for_new_conversation(
        self,
        client_context: dict[str, Any] | None,
    ) -> AgentRuntimeConversationAssignment:
        """只让获批的100%新对话进入 Runtime，业务 owner 在 R1 保持 v2。"""

        context = sanitize_client_conversation_context(client_context)
        enabled = self.config.mode != "off" and self.config.new_conversation_rollout_percent == 100
        if enabled:
            context[AGENT_RUNTIME_CONTEXT_KEY] = {
                "mode": self.config.mode,
                "enabled_intents": list(self.config.enabled_intents),
                "context_compaction_enabled": self.config.context_compaction_enabled,
                "context_version": 0,
            }
        return AgentRuntimeConversationAssignment(
            orchestration_mode=OrchestrationMode.FRONTEND_V2,
            orchestration_version=1,
            context=context,
        )

    async def require_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ):
        """同时校验对话所有者和 Runtime 固定归属。"""

        conversation = await self.task_store.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            return None
        if not _runtime_enabled(conversation.context):
            raise AgentRuntimeUnavailableError("当前对话未启用 Agent Runtime")
        return conversation

    async def start_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        request: TurnStartRequest | dict[str, Any],
    ) -> AgentTurnStartResponse:
        """幂等保存可见消息、Turn、Runtime 版本和首批事件。"""

        owner = user_id.strip()
        body = TurnStartRequest.model_validate(request)
        occurred_at = self._clock()
        try:
            registration = await self._turn_registration_store.register(
                user_id=owner,
                conversation_id=conversation_id,
                message=PixelFlowConversationMessageRecord(
                    message_id=_message_id(
                        conversation_id,
                        body.client_input_id,
                    ),
                    conversation_id=conversation_id,
                    user_id=owner,
                    role="user",
                    content=body.content,
                    payload={
                        "client_message_id": str(body.client_input_id),
                        "materials": body.materials,
                        "reply_to_message_id": body.reply_to_message_id,
                        "artifact_refs": body.artifact_refs,
                    },
                    created_at=occurred_at.isoformat(),
                ),
                turn=TurnRecord(
                    turn_id=_turn_id(
                        conversation_id,
                        body.client_input_id,
                    ),
                    conversation_id=conversation_id,
                    client_input_id=body.client_input_id,
                    status=TurnStatus.ACCEPTED,
                    target_workflow_id=None,
                    decision=None,
                    expected_context_version=body.expected_context_version,
                    created_at=occurred_at,
                ),
                expected_context_version=body.expected_context_version,
                occurred_at=occurred_at,
            )
        except TurnRegistrationContextConflictError as exc:
            raise AgentRuntimeContextConflictError(
                exc.expected_context_version,
                exc.current_context_version,
            ) from exc
        except TurnRegistrationUnavailableError as exc:
            if str(exc) == "Conversation not found":
                raise LookupError("Conversation not found") from exc
            raise AgentRuntimeUnavailableError(str(exc)) from exc
        response_turn = registration.turn
        if registration.created and self.config.context_compaction_enabled and self._context_compactor is not None:
            try:
                await self._context_compactor.maybe_compact(
                    user_id=owner,
                    conversation_id=conversation_id,
                    run_id=registration.turn.turn_id,
                    current_message_id=registration.message.message_id,
                )
            except Exception as exc:
                # M04 Runtime 已把异常写成 retry_required 与安全失败事件；
                # Turn 入口仍返回已持久化状态，前端绝不能因 5xx 自动重发。
                logger.warning(
                    "Agent Runtime 自动压缩失败并保留已登记 Turn：异常类型=%s",
                    type(exc).__name__,
                )
            current_turn = await self.repository.get_turn(
                owner,
                registration.turn.turn_id,
            )
            if current_turn is not None:
                response_turn = current_turn
        return AgentTurnStartResponse(
            turn_id=response_turn.turn_id,
            run_id=response_turn.turn_id,
            status=_turn_status_for_response(response_turn.status),
            context_version=registration.context_version,
        )

    async def snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> AgentRuntimeSnapshotResponse:
        """从持久化 Turn、租约和 Outbox 重建前端唯一权威状态。"""

        owner = user_id.strip()
        await self.reconcile_pending_legacy_handoff(
            user_id=owner,
            conversation_id=conversation_id,
        )
        conversation = await self.require_conversation(
            user_id=owner,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        turns = await self.repository.list_turns(owner, conversation_id)
        events = await self.repository.list_events(owner, conversation_id)
        messages = await self.task_store.list_conversation_messages(
            conversation_id,
            user_id=owner,
        )
        workflows = await self.repository.list_workflows(
            owner,
            conversation_id,
        )
        lease = await self.repository.get_compaction_lease(
            owner,
            conversation_id,
        )
        queued_ids = [turn.turn_id for turn in turns if turn.status is TurnStatus.QUEUED]
        input_queue = [
            RuntimeInputProjection(
                client_input_id=str(turn.client_input_id),
                turn_id=turn.turn_id,
                status=_input_status(turn.status),
                queue_position=(queued_ids.index(turn.turn_id) + 1 if turn.turn_id in queued_ids else None),
                updated_at=turn.created_at,
            )
            for turn in turns
            if turn.status is not TurnStatus.COMPLETED
        ]
        latest_turn = turns[-1] if turns else None
        run_status: RuntimeRunStatus = "idle"
        if latest_turn is not None:
            run_status = {
                TurnStatus.WAITING_USER: "waiting_user",
                TurnStatus.FAILED: "failed",
                TurnStatus.COMPLETED: "completed",
            }.get(latest_turn.status, "running")

        latest_compression_event = next(
            (
                event
                for event in reversed(events)
                if event.type
                in {
                    AgentEventType.CONTEXT_COMPRESSION_STARTED,
                    AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
                    AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
                    AgentEventType.CONTEXT_COMPRESSION_FAILED,
                }
            ),
            None,
        )
        compression_status: RuntimeCompressionStatus = "idle"
        last_outcome: Literal["completed", "failed"] | None = None
        progress_percent: int | None = None
        if latest_compression_event is not None:
            if (
                latest_compression_event.type
                in {
                    AgentEventType.CONTEXT_COMPRESSION_STARTED,
                    AgentEventType.CONTEXT_COMPRESSION_PROGRESSED,
                }
                and lease is not None
            ):
                compression_status = "compacting"
                progress = latest_compression_event.payload.get(
                    "progress_percent",
                )
                progress_percent = progress if isinstance(progress, int) and not isinstance(progress, bool) and 0 <= progress <= 100 else None
            elif latest_compression_event.type is AgentEventType.CONTEXT_COMPRESSION_FAILED:
                compression_status = "blocked"
                last_outcome = "failed"
            elif latest_compression_event.type is AgentEventType.CONTEXT_COMPRESSION_COMPLETED:
                progress_percent = 100
                last_outcome = "completed"

        latest_event = events[-1] if events else None
        return AgentRuntimeSnapshotResponse(
            conversation_id=conversation_id,
            run=RuntimeRunProjection(
                run_id=None if latest_turn is None else latest_turn.turn_id,
                status=run_status,
                updated_at=(None if latest_turn is None else latest_turn.created_at),
            ),
            compression=RuntimeCompressionProjection(
                status=compression_status,
                progress_percent=progress_percent,
                queued_input_count=len(queued_ids),
                last_outcome=last_outcome,
                updated_at=(None if latest_compression_event is None else latest_compression_event.occurred_at),
            ),
            input_queue=input_queue,
            messages=[message.to_dict() for message in messages],
            workflows=[workflow.model_dump(mode="json") for workflow in workflows],
            interrupt=None,
            resume=RuntimeResumeProjection(
                cursor=None if latest_event is None else latest_event.cursor,
                sequence=0 if latest_event is None else latest_event.sequence,
            ),
            context_version=_context_version(conversation.context),
        )

    async def acknowledge_legacy_handoff(
        self,
        *,
        user_id: str,
        conversation_id: str,
        client_input_id: UUID,
    ) -> None:
        """旧 v2 已持久化 pending job 后，完成当前 Turn 并领取下一输入。"""

        owner = user_id.strip()
        conversation = await self.require_conversation(
            user_id=owner,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        turn = await self.repository.get_turn_by_client_input_id(
            owner,
            conversation_id,
            client_input_id,
        )
        if turn is None:
            return
        occurred_at = self._clock()
        if turn.status is TurnStatus.COMPLETED:
            next_turn = next(
                (
                    item
                    for item in await self.repository.list_turns(
                        owner,
                        conversation_id,
                    )
                    if item.turn_id != turn.turn_id and item.status is TurnStatus.PROCESSING
                ),
                None,
            )
        else:
            _completed, next_turn = await self.repository.complete_turn_and_claim_next(
                owner,
                conversation_id,
                turn_id=turn.turn_id,
                now=occurred_at,
            )
        if next_turn is None:
            return
        existing_events = await self.repository.list_events(
            owner,
            conversation_id,
        )
        if any(event.type is AgentEventType.INPUT_STATE_CHANGED and event.payload.get("turn_id") == next_turn.turn_id and event.payload.get("status") == TurnStatus.PROCESSING.value for event in existing_events):
            return
        await self._event_outbox.append(
            owner,
            conversation_id=conversation_id,
            run_id=next_turn.turn_id,
            event_type=AgentEventType.INPUT_STATE_CHANGED,
            payload={
                "client_input_id": str(next_turn.client_input_id),
                "turn_id": next_turn.turn_id,
                "status": TurnStatus.PROCESSING.value,
                "queue_position": None,
            },
            occurred_at=occurred_at,
        )

    async def legacy_handoff_is_eligible(
        self,
        *,
        user_id: str,
        conversation_id: str,
        client_input_id: UUID,
    ) -> bool:
        """只有当前已接力 Turn 才能建立旧流程恢复 marker。"""

        turn = await self.repository.get_turn_by_client_input_id(
            user_id.strip(),
            conversation_id,
            client_input_id,
        )
        return turn is not None and turn.status in {
            TurnStatus.ACCEPTED,
            TurnStatus.PROCESSING,
        }

    async def reconcile_pending_legacy_handoff(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> bool:
        """按持久化 marker 幂等补偿 Turn 完成、下一条领取和事件写入。"""

        owner = user_id.strip()
        conversation = await self.require_conversation(
            user_id=owner,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        runtime = _runtime_context(conversation.context)
        marker = None if runtime is None else runtime.get("legacy_handoff")
        if not isinstance(marker, dict):
            return False
        raw_client_input_id = marker.get("client_input_id")
        client_input_id: UUID | None = None
        try:
            if isinstance(raw_client_input_id, str):
                client_input_id = UUID(raw_client_input_id)
        except ValueError:
            client_input_id = None
        if client_input_id is not None:
            turn = await self.repository.get_turn_by_client_input_id(
                owner,
                conversation_id,
                client_input_id,
            )
            # COMPLETED 只用于恢复“Turn 已完成、marker 尚未来得及清理”的
            # 合法中断窗口；新 marker 的建立仍只允许 ACCEPTED/PROCESSING。
            if turn is not None and turn.status in {
                TurnStatus.ACCEPTED,
                TurnStatus.PROCESSING,
                TurnStatus.COMPLETED,
            }:
                await self.acknowledge_legacy_handoff(
                    user_id=owner,
                    conversation_id=conversation_id,
                    client_input_id=client_input_id,
                )
        try:
            cleared = await self.task_store.patch_agent_runtime_conversation_context(
                conversation_id,
                user_id=owner,
                expected_revision=conversation.revision,
                runtime_patch={"legacy_handoff": None},
            )
        except ConversationRevisionConflictError:
            # Turn/事件已经幂等完成；并发 context 更新保留 marker，
            # 下一次快照会重新读取新 revision 后仅执行清理。
            return False
        return cleared is not None

    async def events_after(
        self,
        *,
        user_id: str,
        conversation_id: str,
        cursor: str | None,
        limit: int = 100,
    ) -> list[AgentEvent] | None:
        """按不透明 cursor 返回可续传事件；未知 cursor 明确要求重载。"""

        conversation = await self.require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        return await self.repository.list_events_after_cursor(
            user_id,
            conversation_id,
            cursor=cursor,
            limit=limit,
        )

    async def get_run(
        self,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
    ) -> AgentTurnJobResponse | None:
        """轮询兜底只查询原 Turn，不创建任何新任务。"""

        conversation = await self.require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        turn = await self.repository.get_turn(user_id, run_id)
        if turn is None or turn.conversation_id != conversation_id:
            return None
        return AgentTurnJobResponse(
            turn_id=turn.turn_id,
            run_id=turn.turn_id,
            status=turn.status.value,
            context_version=_context_version(conversation.context),
        )


__all__ = [
    "AgentRuntimeContextConflictError",
    "AgentRuntimeConversationAssignment",
    "AgentRuntimeService",
    "AgentRuntimeSnapshotResponse",
    "AgentRuntimeUnavailableError",
    "AgentTurnJobResponse",
    "AgentTurnStartResponse",
]
