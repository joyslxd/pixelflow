"""R1 assist 会话 Runtime 的应用服务与 Snapshot 投影。"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pixelflow.agent_runtime.jobs import OperationRecoveryRuntime
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    ConversationRevisionConflictError,
    PixelFlowConversationMessageRecord,
    PixelFlowTaskStore,
    sanitize_client_conversation_context,
)
from pixelflow.video_agent.contracts import AgentPlanStatus, PlanStepStatus
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.runner import VideoAgentRunner, VideoAgentRunScope
from pixelflow.video_agent.workspace.repository import VideoAgentRepository

from .config import AgentRuntimeConfig
from .context import RepositoryCompactionEventOutbox
from .contracts import (
    AgentEvent,
    AgentEventType,
    ExternalJobStatus,
    OrchestrationMode,
    RouteDecision,
    RouteIntent,
    TurnRecord,
    TurnStartRequest,
    TurnStatus,
    WorkflowKind,
)
from .conversation_router import ConversationRouteService
from .identity import conversation_message_id, turn_id
from .persistence import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
    CompactionQueueRepository,
    ConversationCompactionLease,
    TurnRegistrationContextConflictError,
    TurnRegistrationResult,
    TurnRegistrationUnavailableError,
    TurnRouteAssignment,
    make_turn_registration_store,
)
from .runtime_compaction import AgentContextCompactor

logger = logging.getLogger(__name__)
_ROUTING_LOCKS = tuple(asyncio.Lock() for _ in range(64))

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


class AgentRuntimeInterruptStateError(RuntimeError):
    """Snapshot 或响应发现损坏、歧义的 live interrupt 状态。"""


class AgentRuntimeLegacyInterruptOwnershipError(RuntimeError):
    """当前对话的人工确认仍归旧 v2 Controller 所有。"""


class AgentRuntimeVideoConfirmationConflictError(RuntimeError):
    """VideoAgent 确认身份、决定或持久化状态发生冲突。"""


class AgentRuntimeVideoConfirmationUnavailableError(RuntimeError):
    """VideoAgent 公开确认执行器尚未完成服务端装配。"""


class AgentRuntimeVideoWorkflowRetirementError(RuntimeError):
    """请求的历史记录不是可只读归档的V1视频Workflow。"""


class AgentRuntimeVideoQuotaConflictError(RuntimeError):
    """VideoAgent额度响应与当前Plan、步骤或Operation不一致。"""


class AgentRuntimeVideoQuotaUnavailableError(RuntimeError):
    """VideoAgent额度恢复Worker或Repository尚未装配。"""


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
    orchestration_mode: OrchestrationMode
    route_decision: RouteDecision | None = None


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


class VideoAgentConfirmationResponseRequest(_RuntimeResponseModel):
    """确认卡只提交稳定步骤身份和显式决定。"""

    step_id: str = Field(min_length=1, max_length=64)
    decision: Literal["confirm", "cancel"]


class VideoAgentConfirmationResponse(_RuntimeResponseModel):
    """确认执行后的安全状态摘要，不返回工具参数或凭据。"""

    confirmation_id: str
    plan_id: str
    step_id: str
    decision: Literal["confirm", "cancel"]
    plan_status: AgentPlanStatus
    step_status: PlanStepStatus


class VideoAgentQuotaResponseRequest(_RuntimeResponseModel):
    """额度卡只提交稳定中断身份和显式决定。"""

    decision: Literal["resume", "cancel"]


class VideoAgentQuotaResponse(_RuntimeResponseModel):
    """额度动作后的安全Plan摘要。"""

    quota_interrupt_id: str
    plan_id: str
    step_id: str
    decision: Literal["resume", "cancel"]
    plan_status: AgentPlanStatus
    step_status: PlanStepStatus


class RetiredVideoWorkflowResponse(_RuntimeResponseModel):
    """历史V1视频Workflow的最小只读归档结果。"""

    code: Literal["video_workflow_retired"] = "video_workflow_retired"
    workflow_id: str
    created_at: datetime
    artifact_refs: list[str] = Field(default_factory=list)


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


class RuntimeVideoAgentStepProjection(_RuntimeResponseModel):
    """只公开时间线和确认界面需要的步骤字段。"""

    step_id: str
    plan_id: str
    sequence: int = Field(ge=1)
    title: str
    status: PlanStepStatus
    confirmation_required: bool = False
    public_summary: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class RuntimeVideoAgentConfirmationProjection(_RuntimeResponseModel):
    """计费步骤的安全恢复卡片，不包含工具原始参数。"""

    confirmation_id: str
    plan_id: str
    step_id: str
    title: str
    cost_summary: str
    affected_scene_ids: list[str] = Field(default_factory=list)
    submittable: bool = False
    unavailable_reason: str | None = None


class RuntimeVideoAgentQuotaProjection(_RuntimeResponseModel):
    """只公开额度恢复所需稳定身份，不公开Provider job标识。"""

    quota_interrupt_id: str
    plan_id: str
    step_id: str
    quota_pause_revision: int = Field(ge=0)
    phase: Literal["start", "status"]
    reason_code: Literal["provider_quota_insufficient"]
    submittable: bool = False
    unavailable_reason: str | None = None


class RuntimeVideoAgentProjection(_RuntimeResponseModel):
    """右侧面板和执行时间线共享的 VideoAgent 权威投影。"""

    workspace: dict[str, Any]
    plan: dict[str, Any] | None = None
    steps: list[RuntimeVideoAgentStepProjection] = Field(default_factory=list)
    confirmation: RuntimeVideoAgentConfirmationProjection | None = None
    quota: RuntimeVideoAgentQuotaProjection | None = None


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
    video_agent: RuntimeVideoAgentProjection | None = Field(
        default=None,
        serialization_alias="videoAgent",
    )
    resume: RuntimeResumeProjection
    context_version: int = Field(ge=0)


def _runtime_context(context: dict[str, Any]) -> dict[str, Any] | None:
    value = context.get(AGENT_RUNTIME_CONTEXT_KEY)
    return value if isinstance(value, dict) else None


def _video_confirmation_id(plan_id: str, step_id: str) -> str:
    identity = f"pixelflow-video-confirmation:{plan_id}:{step_id}"
    return f"video_confirmation_{uuid5(NAMESPACE_URL, identity).hex}"


def _safe_affected_scene_ids(
    arguments: dict[str, Any],
    workspace_payload: dict[str, Any],
) -> list[str]:
    candidates: list[object] = []
    for value in (
        arguments.get("scene_ids"),
        arguments.get("affected_scene_ids"),
        workspace_payload.get("dirty_scene_ids"),
    ):
        if isinstance(value, list):
            candidates.extend(value)
    result: list[str] = []
    for value in candidates:
        if not isinstance(value, str):
            continue
        scene_id = value.strip()
        if (
            not scene_id
            or len(scene_id) > 128
            or any(not (character.isalnum() or character in "._:-") for character in scene_id)
            or scene_id in result
        ):
            continue
        result.append(scene_id)
    return result


def _safe_internal_artifact_refs(values: Iterable[object]) -> list[str]:
    """只允许历史归档公开内部Artifact标识，不返回URL或任意旧字段。"""

    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.startswith("artifact:"):
            continue
        suffix = value.removeprefix("artifact:")
        if (
            not suffix
            or len(value) > 256
            or any(
                not (character.isalnum() or character in "._:-")
                for character in suffix
            )
            or value in result
        ):
            continue
        result.append(value)
    return result


def _confirmation_cost_summary(tool_name: str, affected_count: int) -> str:
    if tool_name == "generate_scenes":
        target = f"{affected_count}个镜头" if affected_count else "所选镜头"
        return f"将生成{target}的新视频版本，执行后可能产生模型调用费用。"
    if tool_name == "compose_or_export_video":
        return "将生成视频交付产物，执行后可能产生合成、存储或导出费用。"
    return "该步骤会修改项目或调用计费能力，请确认后继续。"


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


def _video_agent_execution_ready(conversation: object) -> bool:
    """只允许创建时已冻结给 V2 VideoAgent Runner 的视频对话。"""

    if getattr(conversation, "orchestration_mode", None) != "video_agent_v2":
        return False
    if getattr(conversation, "orchestration_version", None) != 1:
        return False
    context = getattr(conversation, "context", None)
    runtime = _runtime_context(context) if isinstance(context, dict) else None
    return (
        isinstance(runtime, dict)
        and runtime.get("mode") == "primary"
        and runtime.get("primary_execution_ready") is True
        and isinstance(runtime.get("enabled_intents"), list)
        and "video" in runtime["enabled_intents"]
    )


class AgentRuntimeService:
    """组合旧消息 Store、Turn Inbox、压缩租约和 Event Outbox。"""

    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        repository: CompactionQueueRepository,
        task_store: PixelFlowTaskStore,
        context_compactor: AgentContextCompactor | None = None,
        video_agent_repository: VideoAgentRepository | None = None,
        video_agent_entrypoint: VideoAgentEntrypoint | None = None,
        video_agent_executor: VideoAgentExecutor | None = None,
        video_agent_runner: VideoAgentRunner | None = None,
        operation_repository: AgentRuntimeRepository | None = None,
        video_agent_operation_recovery: OperationRecoveryRuntime | None = None,
        conversation_router: ConversationRouteService | None = None,
        primary_execution_intents: Iterable[str] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = AgentRuntimeConfig.model_validate(
            config.model_dump(mode="python"),
        )
        normalized_execution_intents = frozenset(
            item.strip()
            for item in primary_execution_intents
            if isinstance(item, str) and item.strip()
        )
        unsupported_execution_intents = normalized_execution_intents.difference(
            self.config.enabled_intents,
        )
        if unsupported_execution_intents:
            raise ValueError(
                "primary_execution_intents 必须是 enabled_intents 的子集",
            )
        self.primary_execution_intents = normalized_execution_intents
        self.repository = repository
        self.task_store = task_store
        self._video_agent_repository = video_agent_repository
        self._video_agent_entrypoint = video_agent_entrypoint
        self._video_agent_executor = video_agent_executor
        self._video_agent_runner = video_agent_runner
        self._operation_repository = operation_repository
        self._video_agent_operation_recovery = video_agent_operation_recovery
        self._conversation_router = conversation_router
        self._context_compactor = context_compactor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._turn_registration_store = make_turn_registration_store(
            repository=repository,
            task_store=task_store,
        )
        self._event_outbox = RepositoryCompactionEventOutbox(
            repository=repository,
        )
        self._compaction_recovery_tasks: dict[
            tuple[str, str],
            asyncio.Task[None],
        ] = {}
        self._executor_notification_tasks: set[asyncio.Task[None]] = set()
        self._pending_video_agent_turns: dict[str, VideoAgentRunScope] = {}

    def assignment_for_new_conversation(
        self,
        client_context: dict[str, Any] | None,
    ) -> AgentRuntimeConversationAssignment:
        """创建等待首个 Turn 服务端路由的新对话快照。"""

        context = sanitize_client_conversation_context(client_context)
        enabled = self.config.mode != "off" and self.config.new_conversation_rollout_percent == 100
        if enabled:
            runtime_context: dict[str, Any] = {
                "mode": self.config.mode,
                "enabled_intents": list(self.config.enabled_intents),
                "primary_execution_ready": False,
                "context_compaction_enabled": self.config.context_compaction_enabled,
                "context_version": 0,
            }
            if self._conversation_router is not None:
                runtime_context["routing_status"] = "pending"
            context[AGENT_RUNTIME_CONTEXT_KEY] = runtime_context
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
        raw_request = (
            request.model_dump(mode="python")
            if isinstance(request, TurnStartRequest)
            else request
        )
        body = TurnStartRequest.model_validate(raw_request)
        occurred_at = self._clock()
        conversation = await self.require_conversation(
            user_id=owner,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        if self._conversation_router is None:
            registration = await self._register_turn(
                user_id=owner,
                conversation_id=conversation_id,
                body=body,
                occurred_at=occurred_at,
            )
            video_agent_execution_ready = _video_agent_execution_ready(conversation)
        else:
            lock = _ROUTING_LOCKS[
                hash((owner, conversation_id)) % len(_ROUTING_LOCKS)
            ]
            async with lock:
                conversation = await self.require_conversation(
                    user_id=owner,
                    conversation_id=conversation_id,
                )
                if conversation is None:
                    raise LookupError("Conversation not found")
                runtime = _runtime_context(conversation.context)
                stored_decision = (
                    runtime.get("route_decision") if runtime else None
                )
                route_assignment: TurnRouteAssignment | None = None
                if isinstance(stored_decision, dict):
                    route_decision = RouteDecision.model_validate(
                        stored_decision,
                    )
                else:
                    route_decision = await self._conversation_router.route(
                        content=body.content,
                        materials=body.materials,
                    )
                    primary_ready = (
                        self.config.mode == "primary"
                        and route_decision.intent is RouteIntent.VIDEO
                        and "video" in self.config.enabled_intents
                        and "video" in self.primary_execution_intents
                        and self._video_agent_entrypoint is not None
                    )
                    route_assignment = TurnRouteAssignment(
                        decision=route_decision,
                        orchestration_mode=(
                            OrchestrationMode.VIDEO_AGENT_V2
                            if primary_ready
                            else OrchestrationMode.FRONTEND_V2
                        ),
                        primary_execution_ready=primary_ready,
                    )
                video_agent_execution_ready = (
                    _video_agent_execution_ready(conversation)
                    if route_assignment is None
                    else route_assignment.primary_execution_ready
                )
                self._require_video_agent_entrypoint(video_agent_execution_ready)
                registration = await self._register_turn(
                    user_id=owner,
                    conversation_id=conversation_id,
                    body=body,
                    occurred_at=occurred_at,
                    route_assignment=route_assignment,
                )
                route_decision = registration.route_decision
                video_agent_execution_ready = (
                    registration.orchestration_mode
                    is OrchestrationMode.VIDEO_AGENT_V2
                )
        response_turn = registration.turn
        if video_agent_execution_ready and self._video_agent_entrypoint is not None:
            submission = await self._video_agent_entrypoint.submit_turn(
                user_id=owner,
                conversation_id=conversation_id,
                turn_id=registration.turn.turn_id,
                content=body.content,
                artifact_refs=tuple(body.artifact_refs),
                materials=body.materials,
            )
            if registration.created and self._video_agent_runner is not None:
                self._pending_video_agent_turns[registration.turn.turn_id] = (
                    VideoAgentRunScope(
                        user_id=owner,
                        conversation_id=conversation_id,
                        turn_id=registration.turn.turn_id,
                        plan_id=submission.plan.plan_id,
                    )
                )
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
            orchestration_mode=registration.orchestration_mode,
            route_decision=registration.route_decision,
        )

    def _require_video_agent_entrypoint(self, video_agent_execution_ready: bool) -> None:
        if video_agent_execution_ready and self._video_agent_entrypoint is None:
            raise AgentRuntimeUnavailableError("V2 VideoAgent入口当前不可用")

    async def _register_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        body: TurnStartRequest,
        occurred_at: datetime,
        route_assignment: TurnRouteAssignment | None = None,
    ) -> TurnRegistrationResult:
        try:
            return await self._turn_registration_store.register(
                user_id=user_id,
                conversation_id=conversation_id,
                message=PixelFlowConversationMessageRecord(
                    message_id=conversation_message_id(
                        conversation_id,
                        body.client_input_id,
                    ),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role="user",
                    content=body.content,
                    payload={
                        "client_message_id": str(body.client_input_id),
                        "materials": body.materials,
                        "reply_to_message_id": body.reply_to_message_id,
                        "artifact_refs": body.artifact_refs,
                        "explicit_action": (
                            body.explicit_action.model_dump(mode="json")
                            if body.explicit_action is not None
                            else None
                        ),
                    },
                    created_at=occurred_at.isoformat(),
                ),
                turn=TurnRecord(
                    turn_id=turn_id(
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
                route_assignment=route_assignment,
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

    def notify_registered_turn(
        self,
        turn_id: str,
        credential: TransientVideoAgentCredential | None,
    ) -> None:
        """非阻塞唤醒一次新登记 Turn；失败时保留扫描恢复语义。"""

        video_scope = self._pending_video_agent_turns.pop(turn_id, None)
        if video_scope is not None and self._video_agent_runner is not None:
            self._schedule_executor_notification(
                self._video_agent_runner.notify_turn(video_scope, credential),
                credential=credential,
                kind="VideoAgent Turn",
            )
            return
        if credential is not None:
            credential.discard()

    def _schedule_executor_notification(
        self,
        notification,
        *,
        credential: TransientVideoAgentCredential | None,
        kind: str,
    ) -> None:
        """追踪唤醒协程并吞掉安全边界内异常，避免 HTTP 被诱导重试。"""

        task = asyncio.create_task(
            notification,
            name=f"pixelflow-supervisor-notify-{kind.lower()}",
        )
        self._executor_notification_tasks.add(task)

        def _finish(completed: asyncio.Task[None]) -> None:
            self._executor_notification_tasks.discard(completed)
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                error = asyncio.CancelledError()
            if error is None:
                return
            if credential is not None:
                credential.discard()
            logger.warning(
                "Agent Runtime %s 唤醒失败并等待持久化扫描恢复：异常类型=%s",
                kind,
                type(error).__name__,
            )

        task.add_done_callback(_finish)

    async def respond_to_video_agent_confirmation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        confirmation_id: str,
        request: VideoAgentConfirmationResponseRequest,
        credential: TransientVideoAgentCredential | None = None,
    ) -> VideoAgentConfirmationResponse:
        """校验当前会话确认单归属，并在同一持久化 Plan 上继续或取消。"""

        owner = user_id.strip()
        try:
            conversation = await self.require_conversation(
                user_id=owner,
                conversation_id=conversation_id,
            )
            if conversation is None:
                raise LookupError("Conversation not found")
            if (
                self._video_agent_repository is None
                or self._video_agent_executor is None
            ):
                raise AgentRuntimeVideoConfirmationUnavailableError(
                    "VideoAgent 公开确认执行器尚未安装"
                )
            state = await self._video_agent_repository.load_conversation_state(
                owner,
                conversation_id,
            )
            if state is None or state[1] is None:
                raise LookupError("VideoAgent plan not found")
            _, plan = state
            step = next(
                (item for item in plan.steps if item.step_id == request.step_id),
                None,
            )
            if step is None or not hmac.compare_digest(
                _video_confirmation_id(plan.plan_id, request.step_id),
                confirmation_id,
            ):
                raise AgentRuntimeVideoConfirmationConflictError(
                    "VideoAgent confirmation 身份不匹配"
                )

            try:
                if request.decision == "confirm":
                    if (
                        plan.status is AgentPlanStatus.AWAITING_CONFIRMATION
                        and step.status is PlanStepStatus.AWAITING_CONFIRMATION
                    ):
                        result = await self._video_agent_executor.confirm_step(
                            owner,
                            plan.plan_id,
                            step.step_id,
                            credential=credential,
                        )
                    elif (
                        plan.status
                        in {AgentPlanStatus.RUNNING, AgentPlanStatus.COMPLETED}
                        and step.status
                        in {PlanStepStatus.RUNNING, PlanStepStatus.COMPLETED}
                    ):
                        result = plan
                    else:
                        raise AgentRuntimeVideoConfirmationConflictError(
                            "VideoAgent confirmation 已不能确认"
                        )
                else:
                    if plan.status is AgentPlanStatus.CANCELLED:
                        result = plan
                    elif (
                        plan.status is AgentPlanStatus.AWAITING_CONFIRMATION
                        and step.status is PlanStepStatus.AWAITING_CONFIRMATION
                    ):
                        result = await self._video_agent_executor.cancel_step(
                            owner,
                            plan.plan_id,
                            step.step_id,
                        )
                    else:
                        raise AgentRuntimeVideoConfirmationConflictError(
                            "VideoAgent confirmation 已不能取消"
                        )
            except AgentRuntimeRecordConflictError as exc:
                raise AgentRuntimeVideoConfirmationConflictError(
                    "VideoAgent confirmation 与权威状态冲突"
                ) from exc

            result_step = next(
                (item for item in result.steps if item.step_id == step.step_id),
                None,
            )
            if result_step is None:
                raise AgentRuntimeVideoConfirmationConflictError(
                    "VideoAgent confirmation 结果缺少原步骤"
                )
            return VideoAgentConfirmationResponse(
                confirmation_id=confirmation_id,
                plan_id=result.plan_id,
                step_id=result_step.step_id,
                decision=request.decision,
                plan_status=result.status,
                step_status=result_step.status,
            )
        finally:
            if credential is not None:
                credential.discard()

    async def respond_to_video_agent_quota(
        self,
        *,
        user_id: str,
        conversation_id: str,
        quota_interrupt_id: str,
        request: VideoAgentQuotaResponseRequest,
        credential: TransientVideoAgentCredential | None = None,
    ) -> VideoAgentQuotaResponse:
        """校验V2额度卡归属，恢复原job或原子取消原Plan。"""

        owner = user_id.strip()
        try:
            return await self._respond_to_video_agent_quota(
                owner=owner,
                conversation_id=conversation_id,
                quota_interrupt_id=quota_interrupt_id,
                request=request,
                credential=credential,
            )
        finally:
            if credential is not None:
                credential.discard()

    async def _respond_to_video_agent_quota(
        self,
        *,
        owner: str,
        conversation_id: str,
        quota_interrupt_id: str,
        request: VideoAgentQuotaResponseRequest,
        credential: TransientVideoAgentCredential | None,
    ) -> VideoAgentQuotaResponse:
        conversation = await self.require_conversation(
            user_id=owner,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        if (
            self._video_agent_repository is None
            or self._operation_repository is None
            or self._video_agent_operation_recovery is None
        ):
            raise AgentRuntimeVideoQuotaUnavailableError(
                "VideoAgent额度恢复能力尚未安装"
            )
        state = await self._video_agent_repository.load_conversation_state(
            owner,
            conversation_id,
        )
        if state is None or state[1] is None:
            raise LookupError("VideoAgent plan not found")
        workspace, plan = state
        interrupt = workspace.payload.get("quota_interrupt")
        if not isinstance(interrupt, dict):
            raise AgentRuntimeVideoQuotaConflictError(
                "VideoAgent当前没有额度中断"
            )
        interrupt_id = interrupt.get("quota_interrupt_id")
        plan_id = interrupt.get("plan_id")
        step_id = interrupt.get("step_id")
        job_id = interrupt.get("job_id")
        revision = interrupt.get("quota_pause_revision")
        phase = interrupt.get("phase", "status")
        if (
            not isinstance(interrupt_id, str)
            or not hmac.compare_digest(interrupt_id, quota_interrupt_id)
            or plan_id != plan.plan_id
            or not isinstance(step_id, str)
            or not isinstance(job_id, str)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or phase not in {"start", "status"}
        ):
            raise AgentRuntimeVideoQuotaConflictError(
                "VideoAgent额度中断身份不匹配"
            )
        step = next(
            (item for item in plan.steps if item.step_id == step_id),
            None,
        )
        operation = await self._operation_repository.get_operation(owner, job_id)
        if (
            step is None
            or step.status is not PlanStepStatus.RUNNING
            or plan.status is not AgentPlanStatus.RUNNING
            or operation is None
            or operation.conversation_id != conversation_id
            or operation.workflow_id != plan.plan_id
            or (
                phase == "status"
                and (
                    operation.quota_pause_revision != revision
                    or operation.status is not ExternalJobStatus.POLLING
                    or operation.provider_job_id is None
                    or operation.next_poll_at is not None
                )
            )
            or (
                phase == "start"
                and (
                    revision != 0
                    or operation.status is not ExternalJobStatus.CREATED
                    or operation.provider_job_id is not None
                )
            )
        ):
            raise AgentRuntimeVideoQuotaConflictError(
                "VideoAgent额度中断已失效"
            )
        try:
            if request.decision == "resume":
                if phase == "status":
                    await self._video_agent_operation_recovery.authorize_quota_resume(
                        user_id=owner,
                        conversation_id=conversation_id,
                        workflow_id=plan.plan_id,
                        job_id=job_id,
                        expected_revision=revision,
                    )
                else:
                    if self._video_agent_executor is None or credential is None:
                        raise AgentRuntimeVideoQuotaUnavailableError(
                            "VideoAgent start额度恢复缺少执行凭据"
                        )
                    await self._video_agent_executor.resume_plan(
                        owner,
                        plan.plan_id,
                        credential=credential,
                    )
            else:
                await self._video_agent_repository.cancel_quota_interrupted_plan(
                    owner,
                    plan.plan_id,
                    step.step_id,
                    quota_interrupt_id=quota_interrupt_id,
                    job_id=job_id,
                        quota_pause_revision=revision,
                    now=self._clock(),
                )
        except (AgentRuntimeRecordConflictError, OperationConflictError) as exc:
            raise AgentRuntimeVideoQuotaConflictError(
                "VideoAgent额度响应与权威状态冲突"
            ) from exc
        restored = await self._video_agent_repository.get_plan(owner, plan.plan_id)
        if restored is None:
            raise AgentRuntimeVideoQuotaConflictError(
                "VideoAgent额度响应后Plan不可见"
            )
        restored_step = next(
            (item for item in restored.steps if item.step_id == step.step_id),
            None,
        )
        if restored_step is None:
            raise AgentRuntimeVideoQuotaConflictError(
                "VideoAgent额度响应后步骤不可见"
            )
        return VideoAgentQuotaResponse(
            quota_interrupt_id=quota_interrupt_id,
            plan_id=restored.plan_id,
            step_id=restored_step.step_id,
            decision=request.decision,
            plan_status=restored.status,
            step_status=restored_step.status,
        )

    async def resume_workflow(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> RetiredVideoWorkflowResponse:
        """把历史V1视频恢复请求固定收敛为只读归档，不执行任何副作用。"""

        owner = user_id.strip()
        conversation = await self.require_conversation(
            user_id=owner,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        if not isinstance(self.repository, AgentRuntimeRepository):
            raise AgentRuntimeUnavailableError(
                "历史V1视频Workflow Repository不可用"
            )
        workflow = await self.repository.get_workflow(owner, workflow_id)
        if workflow is None or workflow.conversation_id != conversation_id:
            raise LookupError("Workflow not found")
        if workflow.kind is not WorkflowKind.VIDEO:
            raise AgentRuntimeVideoWorkflowRetirementError(
                "只有历史V1视频Workflow可以进入只读归档"
            )
        return RetiredVideoWorkflowResponse(
            workflow_id=workflow.workflow_id,
            created_at=workflow.created_at,
            artifact_refs=_safe_internal_artifact_refs(
                workflow.latest_artifact_refs
            ),
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
        stored_messages = await self.task_store.list_conversation_messages(
            conversation_id,
            user_id=owner,
        )
        workflows = await self.repository.list_workflows(
            owner,
            conversation_id,
        )
        messages_by_id = {
            message.message_id: deepcopy(message.to_dict())
            for message in stored_messages
        }
        messages = sorted(
            messages_by_id.values(),
            key=lambda item: (
                str(item.get("created_at", "")),
                str(item.get("message_id", "")),
            ),
        )
        lease = await self.repository.get_compaction_lease(
            owner,
            conversation_id,
        )
        await self._schedule_compaction_recovery_if_due(
            owner,
            conversation_id,
            lease=lease,
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
        video_agent: RuntimeVideoAgentProjection | None = None
        if self._video_agent_repository is not None:
            try:
                video_state = await self._video_agent_repository.load_conversation_state(
                    owner,
                    conversation_id,
                )
            except (AgentRuntimeRecordConflictError, ValidationError, ValueError) as exc:
                raise AgentRuntimeInterruptStateError(
                    "VideoAgent Snapshot 投影状态非法",
                ) from exc
            if video_state is not None:
                workspace, plan = video_state
                if workspace.conversation_id != conversation_id or (
                    plan is not None
                    and (
                        plan.conversation_id != conversation_id
                        or plan.workspace_id != workspace.workspace_id
                    )
                ):
                    raise AgentRuntimeInterruptStateError(
                        "VideoAgent Snapshot 投影身份非法",
                    )
                plan_payload = (
                    None
                    if plan is None
                    else plan.model_dump(mode="json", exclude={"steps"})
                )
                confirmation: RuntimeVideoAgentConfirmationProjection | None = None
                quota: RuntimeVideoAgentQuotaProjection | None = None
                if plan is not None:
                    waiting_steps = [
                        step
                        for step in plan.steps
                        if step.status is PlanStepStatus.AWAITING_CONFIRMATION
                    ]
                    if (
                        plan.status is AgentPlanStatus.AWAITING_CONFIRMATION
                        and len(waiting_steps) != 1
                    ) or (
                        plan.status is not AgentPlanStatus.AWAITING_CONFIRMATION
                        and waiting_steps
                    ):
                        raise AgentRuntimeInterruptStateError(
                            "VideoAgent 确认投影状态非法",
                        )
                    if waiting_steps:
                        waiting_step = waiting_steps[0]
                        affected_scene_ids = _safe_affected_scene_ids(
                            waiting_step.arguments,
                            workspace.payload,
                        )
                        confirmation = RuntimeVideoAgentConfirmationProjection(
                            confirmation_id=_video_confirmation_id(
                                plan.plan_id,
                                waiting_step.step_id,
                            ),
                            plan_id=plan.plan_id,
                            step_id=waiting_step.step_id,
                            title=waiting_step.title,
                            cost_summary=_confirmation_cost_summary(
                                waiting_step.tool_name,
                                len(affected_scene_ids),
                            ),
                            affected_scene_ids=affected_scene_ids,
                            submittable=self._video_agent_executor is not None,
                            unavailable_reason=(
                                None
                                if self._video_agent_executor is not None
                                else "确认执行入口将在统一VideoAgent入口装配后开放。"
                            ),
                        )
                    raw_quota = workspace.payload.get("quota_interrupt")
                    if raw_quota is not None:
                        if not isinstance(raw_quota, dict):
                            raise AgentRuntimeInterruptStateError(
                                "VideoAgent额度投影格式非法"
                            )
                        quota_interrupt_id = raw_quota.get(
                            "quota_interrupt_id"
                        )
                        quota_plan_id = raw_quota.get("plan_id")
                        quota_step_id = raw_quota.get("step_id")
                        quota_revision = raw_quota.get(
                            "quota_pause_revision"
                        )
                        quota_phase = raw_quota.get("phase", "status")
                        quota_reason = raw_quota.get("reason_code")
                        quota_step = next(
                            (
                                item
                                for item in plan.steps
                                if item.step_id == quota_step_id
                            ),
                            None,
                        )
                        if (
                            not isinstance(quota_interrupt_id, str)
                            or quota_plan_id != plan.plan_id
                            or not isinstance(quota_step_id, str)
                            or isinstance(quota_revision, bool)
                            or not isinstance(quota_revision, int)
                            or quota_revision < 0
                            or quota_phase not in {"start", "status"}
                            or quota_reason
                            != "provider_quota_insufficient"
                            or quota_step is None
                            or quota_step.status
                            is not PlanStepStatus.RUNNING
                        ):
                            raise AgentRuntimeInterruptStateError(
                                "VideoAgent额度投影身份非法"
                            )
                        quota_ready = (
                            self._operation_repository is not None
                            and self._video_agent_operation_recovery is not None
                        )
                        quota = RuntimeVideoAgentQuotaProjection(
                            quota_interrupt_id=quota_interrupt_id,
                            plan_id=plan.plan_id,
                            step_id=quota_step_id,
                            quota_pause_revision=quota_revision,
                            phase=quota_phase,
                            reason_code="provider_quota_insufficient",
                            submittable=quota_ready,
                            unavailable_reason=(
                                None
                                if quota_ready
                                else "额度恢复入口尚未安装。"
                            ),
                        )
                video_agent = RuntimeVideoAgentProjection(
                    workspace=workspace.model_dump(mode="json"),
                    plan=plan_payload,
                    steps=(
                        []
                        if plan is None
                        else [
                            RuntimeVideoAgentStepProjection(
                                step_id=step.step_id,
                                plan_id=step.plan_id,
                                sequence=step.sequence,
                                title=step.title,
                                status=step.status,
                                confirmation_required=step.confirmation_required,
                                public_summary=step.public_summary,
                                artifact_refs=list(step.artifact_refs),
                                started_at=step.started_at,
                                completed_at=step.completed_at,
                                duration_ms=step.duration_ms,
                            )
                            for step in plan.steps
                        ]
                    ),
                    confirmation=confirmation,
                    quota=quota,
                )
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
            messages=messages,
            workflows=[workflow.model_dump(mode="json") for workflow in workflows],
            video_agent=video_agent,
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
        await self._append_processing_event(
            user_id=owner,
            conversation_id=conversation_id,
            turn=next_turn,
            occurred_at=occurred_at,
        )

    async def _append_processing_event(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn: TurnRecord,
        occurred_at: datetime,
    ) -> None:
        """幂等补齐队首进入 processing 的事件，驱动 assist 前端接力旧流程。"""

        existing_events = await self.repository.list_events(
            user_id,
            conversation_id,
        )
        if any(event.type is AgentEventType.INPUT_STATE_CHANGED and event.payload.get("turn_id") == turn.turn_id and event.payload.get("status") == TurnStatus.PROCESSING.value for event in existing_events):
            return
        await self._event_outbox.append(
            user_id,
            conversation_id=conversation_id,
            run_id=turn.turn_id,
            event_type=AgentEventType.INPUT_STATE_CHANGED,
            payload={
                "client_input_id": str(turn.client_input_id),
                "turn_id": turn.turn_id,
                "status": TurnStatus.PROCESSING.value,
                "queue_position": None,
            },
            occurred_at=occurred_at,
        )

    def _schedule_compaction_recovery(
        self,
        user_id: str,
        conversation_id: str,
    ) -> None:
        """由 Snapshot/SSE/轮询读取轻量唤醒失败压缩，不阻塞恢复接口。"""

        if not self.config.context_compaction_enabled or self._context_compactor is None:
            return
        key = (user_id, conversation_id)
        existing = self._compaction_recovery_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._recover_compaction(
                user_id=user_id,
                conversation_id=conversation_id,
            ),
            name=f"pixelflow-compaction-recovery-{conversation_id}",
        )
        self._compaction_recovery_tasks[key] = task

        def _discard(completed: asyncio.Task[None]) -> None:
            if self._compaction_recovery_tasks.get(key) is completed:
                self._compaction_recovery_tasks.pop(key, None)

        task.add_done_callback(_discard)

    async def _schedule_compaction_recovery_if_due(
        self,
        user_id: str,
        conversation_id: str,
        *,
        lease: ConversationCompactionLease | None = None,
    ) -> None:
        """只在持久化退避时间到达后创建恢复任务。"""

        if not self.config.context_compaction_enabled or self._context_compactor is None:
            return
        current_lease = lease
        if current_lease is None:
            current_lease = await self.repository.get_compaction_lease(
                user_id,
                conversation_id,
            )
        if current_lease is None or current_lease.lease_expires_at > self._clock():
            return
        self._schedule_compaction_recovery(user_id, conversation_id)

    async def _recover_compaction(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> None:
        """用持久化 Turn 和稳定消息 ID 接管过期或 retry_required 压缩租约。"""

        try:
            lease = await self.repository.get_compaction_lease(
                user_id,
                conversation_id,
            )
            now = self._clock()
            if lease is None or lease.lease_expires_at > now:
                return
            pending_turn = next(
                (
                    turn
                    for turn in await self.repository.list_turns(
                        user_id,
                        conversation_id,
                    )
                    if turn.status in {
                        TurnStatus.ACCEPTED,
                        TurnStatus.QUEUED,
                    }
                ),
                None,
            )
            if pending_turn is None:
                return
            retry = getattr(
                self._context_compactor,
                "retry_compaction",
                None,
            )
            if retry is None:
                return
            result = await retry(
                user_id=user_id,
                conversation_id=conversation_id,
                run_id=pending_turn.turn_id,
                current_message_id=conversation_message_id(
                    conversation_id,
                    pending_turn.client_input_id,
                ),
            )
            if result.next_turn is not None:
                await self._append_processing_event(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    turn=result.next_turn,
                    occurred_at=self._clock(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Agent Runtime 压缩恢复失败并保留原队列：异常类型=%s",
                type(exc).__name__,
            )

    async def aclose(self) -> None:
        """停止进程内恢复任务；持久化队列由下一进程继续接管。"""

        notification_tasks = tuple(self._executor_notification_tasks)
        self._executor_notification_tasks.clear()
        for task in notification_tasks:
            task.cancel()
        if notification_tasks:
            await asyncio.gather(*notification_tasks, return_exceptions=True)
        self._pending_video_agent_turns.clear()
        tasks = tuple(self._compaction_recovery_tasks.values())
        self._compaction_recovery_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
        events = await self.repository.list_events_after_cursor(
            user_id,
            conversation_id,
            cursor=cursor,
            limit=limit,
        )
        if events is not None and any(
            event.type is AgentEventType.CONTEXT_COMPRESSION_FAILED
            for event in events
        ):
            await self._schedule_compaction_recovery_if_due(
                user_id.strip(),
                conversation_id,
            )
        return events

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
        if turn.status is TurnStatus.QUEUED:
            await self._schedule_compaction_recovery_if_due(
                user_id.strip(),
                conversation_id,
            )
        return AgentTurnJobResponse(
            turn_id=turn.turn_id,
            run_id=turn.turn_id,
            status=turn.status.value,
            context_version=_context_version(conversation.context),
        )


__all__ = [
    "AgentRuntimeContextConflictError",
    "AgentRuntimeConversationAssignment",
    "AgentRuntimeInterruptStateError",
    "AgentRuntimeLegacyInterruptOwnershipError",
    "AgentRuntimeService",
    "AgentRuntimeSnapshotResponse",
    "AgentRuntimeUnavailableError",
    "AgentTurnJobResponse",
    "AgentTurnStartResponse",
]
