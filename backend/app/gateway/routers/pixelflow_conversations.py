"""PixelFlow 对话历史 API。

本文件是 PixelFlow 会话历史的 Controller 层：负责 `/agent/conversations`
入参、出参、当前用户隔离和恢复 payload 组织。真正持久化仍由
``pixelflow.tasks`` Store 负责。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gateway.content_app_auth import is_admin_user
from app.gateway.deps import get_current_user
from pixelflow.tasks import (
    ConversationRevisionConflictError,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
    PixelFlowTaskStore,
    sanitize_client_conversation_context,
)

router = APIRouter(prefix="/agent/conversations", tags=["pixelflow-conversations"])
logger = logging.getLogger(__name__)

_CONVERSATION_MESSAGE_JOBS: dict[str, dict[str, Any]] = {}
_CONVERSATION_MESSAGE_JOB_KEYS: dict[tuple[str, str, str], str] = {}
_MAX_CONVERSATION_MESSAGE_JOBS = 300


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    current_task_id: str | None = None
    last_phase: str = "idle"
    context: dict[str, Any] = Field(default_factory=dict)


class ConversationUpdateRequest(BaseModel):
    title: str | None = None
    current_task_id: str | None = None
    last_phase: str | None = None
    context: dict[str, Any] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class JianyingDraftConversationContextPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_phase: str = Field(min_length=1)
    expected_job_id: str = Field(min_length=1)
    pendingJianyingDraftJob: dict[str, Any] | None = None
    pending_jianying_draft_job: dict[str, Any] | None = None
    jianyingDraftRecords: dict[str, Any] | None = None
    jianying_draft_records: dict[str, Any] | None = None
    jianying_draft_job_resume_error: str | None = None

    @model_validator(mode="after")
    def validate_dual_fields(self) -> JianyingDraftConversationContextPatchRequest:
        fields = self.model_fields_set
        pending_fields = {"pendingJianyingDraftJob", "pending_jianying_draft_job"}
        record_fields = {"jianyingDraftRecords", "jianying_draft_records"}
        if not fields.intersection(pending_fields):
            raise ValueError("pendingJianyingDraftJob or pending_jianying_draft_job is required")
        if not fields.intersection(record_fields):
            raise ValueError("jianyingDraftRecords or jianying_draft_records is required")
        if pending_fields.issubset(fields) and self.pendingJianyingDraftJob != self.pending_jianying_draft_job:
            raise ValueError("pending camelCase and snake_case values must match")
        if record_fields.issubset(fields) and self.jianyingDraftRecords != self.jianying_draft_records:
            raise ValueError("records camelCase and snake_case values must match")
        pending_job = self.pending_job()
        if pending_job is not None:
            if pending_job.get("job_id") != self.expected_job_id:
                raise ValueError("pending job_id must match expected_job_id")
            if not isinstance(pending_job.get("storyboard_version_id"), str) or not pending_job["storyboard_version_id"]:
                raise ValueError("pending storyboard_version_id is required")
        for storyboard_version_id, record in self.records().items():
            if not isinstance(record, dict) or record.get("job_id") != self.expected_job_id:
                raise ValueError("record job_id must match expected_job_id")
            if record.get("storyboard_version_id") != storyboard_version_id:
                raise ValueError("record storyboard_version_id must match its key")
        return self

    def pending_job(self) -> dict[str, Any] | None:
        if "pendingJianyingDraftJob" in self.model_fields_set:
            return self.pendingJianyingDraftJob
        return self.pending_jianying_draft_job

    def records(self) -> dict[str, Any]:
        if "jianyingDraftRecords" in self.model_fields_set:
            return self.jianyingDraftRecords or {}
        return self.jianying_draft_records or {}


class ConversationMessageCreateRequest(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class HarnessTurnStartRequest(BaseModel):
    """M0 公开 Harness Turn 的最小输入，工作区归属永远由 Gateway 回查。"""

    model_config = ConfigDict(extra="forbid")

    client_input_id: uuid.UUID
    workspace_id: str = Field(min_length=1, max_length=64)
    expected_workspace_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=32_000)
    max_output_tokens: int = Field(default=192, ge=1, le=512)


class HarnessTurnStartResponse(BaseModel):
    """公开返回已绑定并已激活的 M0 Sidecar Run，不暴露 Session 或服务凭据。"""

    message_id: str
    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: Literal["accepted"]
    workspace_revision: int = Field(ge=1)


class HarnessRunCancelResponse(BaseModel):
    """取消结果只包含稳定 Run 终态，不暴露 Harness 运行时细节。"""

    run_id: str = Field(pattern=r"^hrun_[a-f0-9]{32}$")
    status: Literal["completed", "failed", "cancelled"]
    termination_reason: str | None = Field(default=None, max_length=120)


class ConversationMessageUpdateRequest(BaseModel):
    content: str | None = None
    payload: dict[str, Any] | None = None


class ConversationResponse(BaseModel):
    conversation_id: str
    user_id: str | None = None
    orchestration_mode: str = "frontend_v2"
    orchestration_version: int = Field(default=1, ge=1)
    title: str = ""
    current_task_id: str | None = None
    last_phase: str = "idle"
    context: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)
    created_at: str = ""
    updated_at: str = ""


class ConversationMessageResponse(BaseModel):
    message_id: str
    conversation_id: str
    user_id: str | None = None
    role: str
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ConversationMessageJobStartResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str = "running"
    message: str = ""


class ConversationMessageJobStatusResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: str
    result: ConversationMessageResponse | None = None
    error: str | None = None
    message: str = ""


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse] = Field(default_factory=list)
    next_cursor: str | None = None


class ConversationDetailResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[ConversationMessageResponse] = Field(default_factory=list)


class ConversationTraceEventResponse(BaseModel):
    id: int
    conversation_id: str
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ConversationTraceResponse(BaseModel):
    items: list[ConversationTraceEventResponse] = Field(default_factory=list)


def _task_store(request: Request) -> PixelFlowTaskStore:
    store = getattr(request.app.state, "pixelflow_task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="PixelFlow task store not available")
    return store



def _harness_run_bridge(request: Request):
    """读取启动期装配的真实 Sidecar Port；缺失时拒绝而不回退旧 Agent。"""

    from pixelflow.agent_harness import AgentHarnessPort

    bridge = getattr(request.app.state, "pixelflow_harness_run_bridge", None)
    if not isinstance(bridge, AgentHarnessPort):
        raise HTTPException(
            status_code=503,
            detail={"code": "harness_run_bridge_unavailable"},
        )
    return bridge


def _agent_run_bridge(request: Request):
    """读取唯一的新控制面 RunBridge，缺失时拒绝而不退回旧 Agent。"""

    from pixelflow.agent_control_plane import AgentRunBridge

    bridge = getattr(request.app.state, "pixelflow_agent_run_bridge", None)
    if not isinstance(bridge, AgentRunBridge):
        raise HTTPException(status_code=503, detail={"code": "agent_run_bridge_unavailable"})
    return bridge


def _harness_video_repository(request: Request) -> Any:
    """读取与 Broker 共用的 SQL Workspace Repository，禁止 M0 使用内存业务状态。"""

    repository = getattr(request.app.state, "pixelflow_harness_video_repository", None)
    if repository is None or not hasattr(repository, "get_workspace"):
        raise HTTPException(
            status_code=503,
            detail={"code": "harness_workspace_repository_unavailable"},
        )
    return repository


async def _build_harness_context(
    request: Request,
    *,
    user_id: str,
    conversation: PixelFlowConversationRecord,
    workspace: Any,
    user_input: str,
) -> dict[str, Any]:
    """组装 Sidecar 可消费的安全上下文，不发送用户身份、凭据或业务原文。"""

    from pixelflow.video.workspace import build_workspace_digest

    preference_store = getattr(request.app.state, "pixelflow_preference_store", None)
    preference_projection: dict[str, Any] = {}
    if preference_store is not None and hasattr(preference_store, "get"):
        preference = await preference_store.get(user_id)
        preference_projection = {
            "style_preferences": dict(preference.style_preferences),
            "negative_rules": list(preference.negative_rules)[:20],
            "defaults": dict(preference.defaults),
        }
    memory_service = getattr(request.app.state, "pixelflow_long_term_memory_service", None)
    memory_projection: list[dict[str, Any]] = []
    if memory_service is not None and hasattr(memory_service, "search"):
        memories = await memory_service.search(user_id=user_id, query=user_input)
        memory_projection = [
            {"memory_id": item.memory_id, "content": item.content, "category": item.category}
            for item in memories[:20]
        ]
    return {
        "workspace_projection": build_workspace_digest(workspace),
        "conversation_projection": {
            "title": conversation.title[:256],
            "last_phase": conversation.last_phase[:80],
            "revision": conversation.revision,
        },
        "preference_projection": preference_projection,
        "brand_profile_projection": {},
        "long_term_memory_projection": memory_projection,
    }


async def _require_harness_admission(request: Request):
    """仅在共享准入状态开放时接收新 Run，禁止降级到旧 Agent 内核。"""

    from pixelflow.agent_harness.admission import (
        HarnessAdmissionClosedError,
        SQLHarnessAdmissionRepository,
    )

    repository = getattr(request.app.state, "pixelflow_harness_admission_repository", None)
    if not isinstance(repository, SQLHarnessAdmissionRepository):
        raise HTTPException(status_code=503, detail={"code": "harness_admission_unavailable"})
    try:
        return await repository.require_open()
    except HarnessAdmissionClosedError as error:
        raise HTTPException(status_code=503, detail={"code": "harness_admission_closed"}) from error


async def _close_harness_admission_after_sidecar_failure(
    request: Request,
    *,
    expected_revision: int,
) -> None:
    """Sidecar 不可用时尽力关闭共享准入；竞争失败表示其他实例已先处理。"""

    from pixelflow.agent_harness.admission import (
        HarnessAdmissionConflictError,
        SQLHarnessAdmissionRepository,
    )

    repository = getattr(request.app.state, "pixelflow_harness_admission_repository", None)
    if not isinstance(repository, SQLHarnessAdmissionRepository):
        return
    try:
        await repository.update_state(
            open_for_new_runs=False,
            reason_code="sidecar_unavailable",
            expected_revision=expected_revision,
            updated_by="gateway-sidecar-failure",
        )
    except HarnessAdmissionConflictError:
        return


def _harness_run_projector(request: Request):
    """读取启动期装配的 Outbox 投影服务，缺失时拒绝而不退回 Sidecar 直传。"""

    from pixelflow.agent_harness.projector import HarnessRunProjector

    projector = getattr(request.app.state, "pixelflow_harness_run_projector", None)
    if not isinstance(projector, HarnessRunProjector):
        raise HTTPException(
            status_code=503,
            detail={"code": "harness_run_projector_unavailable"},
        )
    return projector


def _harness_recovery_service(request: Request):
    """读取新的 run_recovery Application Service；缺失时拒绝而不从旧 Session 恢复。"""

    from pixelflow.agent_harness.recovery import HarnessRecoveryService

    service = getattr(request.app.state, "pixelflow_harness_recovery_service", None)
    if not isinstance(service, HarnessRecoveryService):
        raise HTTPException(
            status_code=503,
            detail={"code": "harness_recovery_service_unavailable"},
        )
    return service


def _harness_digest(payload: dict[str, Any]) -> str:
    """生成冻结输入的 SHA-256 摘要，摘要不替代权威消息或 Workspace。"""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


async def _ensure_harness_projection(
    request: Request,
    *,
    user_id: str,
    conversation_id: str,
    run_id: str,
):
    """在 Gateway/Sidecar 重启后按 binding 重新拉起只读事件投影，不直接续跑模型。"""

    bridge = _harness_run_bridge(request)
    binding = await bridge.get_owned_binding(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    projector = _harness_run_projector(request)
    await projector.start(harness=bridge, binding=binding)
    return projector



def _conversation_response(record: PixelFlowConversationRecord) -> ConversationResponse:
    return ConversationResponse(**record.to_dict())


def _message_response(record: PixelFlowConversationMessageRecord) -> ConversationMessageResponse:
    return ConversationMessageResponse(**record.to_dict())


async def _conversation_detail(
    store: PixelFlowTaskStore,
    conversation_id: str,
    *,
    user_id: str | None,
) -> ConversationDetailResponse:
    conversation = await store.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await store.list_conversation_messages(conversation_id, user_id=user_id)
    return ConversationDetailResponse(
        conversation=_conversation_response(conversation),
        messages=[_message_response(message) for message in messages],
    )


@router.post("", response_model=ConversationResponse)
async def create_conversation(body: ConversationCreateRequest, request: Request) -> ConversationResponse:
    user_id = await get_current_user(request)
    title = body.title.strip() or "新的对话"
    context = sanitize_client_conversation_context(body.context)
    orchestration_mode = "harness_v1"
    orchestration_version = 1
    record = await _task_store(request).create_conversation(
        PixelFlowConversationRecord(
            conversation_id=uuid.uuid4().hex,
            user_id=user_id,
            orchestration_mode=orchestration_mode,
            orchestration_version=orchestration_version,
            title=title,
            current_task_id=body.current_task_id,
            last_phase=body.last_phase,
            context=context,
        )
    )
    return _conversation_response(record)


@router.post(
    "/{conversation_id}/harness-turns/start",
    response_model=HarnessTurnStartResponse,
)
async def start_harness_turn(
    conversation_id: str,
    body: HarnessTurnStartRequest,
    request: Request,
) -> HarnessTurnStartResponse:
    """M0 真实公开 Turn：先持久化用户消息，再创建、绑定并激活 Sidecar Run。"""

    from pixelflow.agent_harness import (
        GatewayHarnessSidecarError,
        HarnessRunRequest,
    )

    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated"})
    admission = await _require_harness_admission(request)
    store = _task_store(request)
    conversation = await store.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    workspace = await _harness_video_repository(request).get_workspace(
        user_id,
        body.workspace_id,
    )
    if workspace is None or workspace.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail={"code": "harness_workspace_not_found"})
    if workspace.revision != body.expected_workspace_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "harness_workspace_revision_conflict",
                "current_revision": workspace.revision,
            },
        )
    message = await _append_conversation_message(
        store,
        conversation_id,
        ConversationMessageCreateRequest(
            role="user",
            content=body.content,
            payload={
                "client_message_id": str(body.client_input_id),
                "source": "harness_turn",
            },
        ),
        user_id=user_id,
    )
    context = await _build_harness_context(
        request,
        user_id=user_id,
        conversation=conversation,
        workspace=workspace,
        user_input=body.content,
    )
    context_digest = _harness_digest({
        "conversation_id": conversation_id,
        "message_id": message.message_id,
        "workspace_id": workspace.workspace_id,
        "workspace_revision": workspace.revision,
        "context": context,
    })
    try:
        result = await _agent_run_bridge(request).start(
            HarnessRunRequest(
                user_id=user_id,
                conversation_id=conversation_id,
                workspace_id=workspace.workspace_id,
                workspace_revision=workspace.revision,
                trigger_id=body.client_input_id.hex,
                user_input=body.content,
                system_instruction=(
                    "你是 PixelFlow 视频 Agent。当前工作区事实必须先遵循已加载 Skill 的指令，"
                    "并使用受控 Tool 获取证据；不得猜测、编造或绕过 Tool Broker。"
                ),
                context_digest=context_digest,
                model_profile_digest=_harness_digest({"profile": "deepseek-v4-pro"}),
                context_budget_digest=_harness_digest(
                    {"effective_context_k": 896, "output_reserve_k": 32, "safety_reserve_k": 32},
                ),
                run_limits_digest=_harness_digest(
                    {"max_model_steps": 8, "max_business_tools": 3, "deadline_seconds": 90},
                ),
                max_output_tokens=body.max_output_tokens,
                **context,
            ),
        )
    except GatewayHarnessSidecarError as error:
        await _close_harness_admission_after_sidecar_failure(
            request,
            expected_revision=admission.revision,
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "harness_run_unavailable_retryable"},
        ) from error
    if result.status != "accepted":
        raise HTTPException(status_code=503, detail={"code": "harness_run_protocol_invalid"})
    updated_message = await store.update_conversation_message(
        conversation_id,
        message.message_id,
        user_id=user_id,
        payload={
            **message.payload,
            "harness_run_id": result.run_id,
        },
    )
    if updated_message is None:
        raise HTTPException(status_code=503, detail={"code": "harness_input_message_unavailable"})
    try:
        bridge = _harness_run_bridge(request)
        binding = await bridge.get_owned_binding(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=result.run_id,
        )
        await _harness_run_projector(request).start(harness=bridge, binding=binding)
    except LookupError as error:
        raise HTTPException(status_code=503, detail={"code": "harness_run_binding_unavailable"}) from error
    return HarnessTurnStartResponse(
        message_id=message.message_id,
        run_id=result.run_id,
        status="accepted",
        workspace_revision=workspace.revision,
    )


@router.get("/{conversation_id}/harness-runs/{run_id}/events")
async def stream_harness_run_events(
    conversation_id: str,
    run_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """只向 Run owner 回放 Gateway 过滤后的 Sidecar 公开 SSE，不暴露 Harness 原始轨迹。"""

    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated"})
    if await _task_store(request).get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        projector = await _ensure_harness_projection(
            request,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "harness_run_not_found"}) from error
    try:
        await projector.events_after(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "harness_run_not_found"}) from error

    async def event_stream():
        current_sequence = after_sequence
        while True:
            events = await projector.events_after(
                user_id=user_id,
                conversation_id=conversation_id,
                run_id=run_id,
                after_sequence=current_sequence,
            )
            for event in events:
                current_sequence = event.sequence
                yield f"id: {event.cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
            snapshot = await projector.snapshot(
                user_id=user_id,
                conversation_id=conversation_id,
                run_id=run_id,
            )
            if snapshot.status in {"completed", "failed", "cancelled"}:
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.08)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{conversation_id}/harness-runs/{run_id}/snapshot")
async def get_harness_run_snapshot(
    conversation_id: str,
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    """返回由 Gateway Outbox 与权威消息表构成的最小可恢复 Snapshot。"""

    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated"})
    if await _task_store(request).get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        projector = await _ensure_harness_projection(
            request,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        return (
            await projector.snapshot(
                user_id=user_id,
                conversation_id=conversation_id,
                run_id=run_id,
            )
        ).model_dump()
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "harness_run_not_found"}) from error


@router.post(
    "/{conversation_id}/harness-runs/{run_id}/cancel",
    response_model=HarnessRunCancelResponse,
)
async def cancel_harness_run(
    conversation_id: str,
    run_id: str,
    request: Request,
) -> HarnessRunCancelResponse:
    """取消当前 Harness 模型循环；外部 Provider Operation 的取消不在 M2 边界内。"""

    from pixelflow.agent_harness import GatewayHarnessSidecarError

    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated"})
    if await _task_store(request).get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        await _ensure_harness_projection(
            request,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        result = await _agent_run_bridge(request).cancel(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "harness_run_not_found"}) from error
    except GatewayHarnessSidecarError as error:
        raise HTTPException(status_code=503, detail={"code": "harness_run_cancel_unavailable_retryable"}) from error
    if result.status not in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=503, detail={"code": "harness_run_cancel_protocol_invalid"})
    return HarnessRunCancelResponse(
        run_id=result.run_id,
        status=result.status,
        termination_reason=result.termination_reason,
    )


@router.post("/{conversation_id}/harness-runs/{run_id}/recover")
async def recover_harness_run(
    conversation_id: str,
    run_id: str,
    request: Request,
) -> dict[str, str | None]:
    """基于权威消息/Workspace 创建新的 run_recovery，旧 Harness Session 永不续跑。"""

    from pixelflow.agent_harness import GatewayHarnessSidecarError

    user_id = await get_current_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated"})
    if await _task_store(request).get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        projector = await _ensure_harness_projection(
            request,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "harness_run_not_found"}) from error
    snapshot = await projector.snapshot(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    failure_requires_recovery = any(
        event.type == "run.state_changed"
        and event.payload.get("status") == "failed"
        and event.payload.get("code") == "harness_run_recovery_required"
        for event in snapshot.events
    )
    if not failure_requires_recovery:
        raise HTTPException(status_code=409, detail={"code": "harness_run_recovery_not_required"})
    bridge = _harness_run_bridge(request)
    try:
        result = await _harness_recovery_service(request).recover(
            bridge=bridge,
            user_id=user_id,
            conversation_id=conversation_id,
            original_run_id=run_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "harness_run_not_found"}) from error
    except GatewayHarnessSidecarError as error:
        raise HTTPException(status_code=503, detail={"code": "harness_recovery_unavailable_retryable"}) from error
    if result.status == "manual_review":
        raise HTTPException(status_code=409, detail={"code": "harness_recovery_manual_review_required"})
    if result.recovery_run_id is None:
        raise HTTPException(status_code=503, detail={"code": "harness_recovery_incomplete"})
    binding = await bridge.get_owned_binding(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=result.recovery_run_id,
    )
    await projector.start(harness=bridge, binding=binding)
    return {
        "recovery_event_id": result.recovery_event_id,
        "recovery_run_id": result.recovery_run_id,
    }


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    page_size: int = Query(default=5, ge=1, le=50),
    cursor: str | None = Query(default=None),
) -> ConversationListResponse:
    user_id = await get_current_user(request)
    items, next_cursor = await _task_store(request).list_conversations(user_id=user_id, limit=page_size, cursor=cursor)
    return ConversationListResponse(items=[_conversation_response(item) for item in items], next_cursor=next_cursor)


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(conversation_id: str, request: Request) -> ConversationDetailResponse:
    user_id = await get_current_user(request)
    return await _conversation_detail(_task_store(request), conversation_id, user_id=user_id)


@router.put("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(conversation_id: str, body: ConversationUpdateRequest, request: Request) -> ConversationResponse:
    user_id = await get_current_user(request)
    fields = body.model_dump(exclude_unset=True)
    expected_revision = fields.pop("expected_revision", None)
    try:
        updated = await _task_store(request).update_conversation(
            conversation_id,
            user_id=user_id,
            expected_revision=expected_revision, **fields,
        )
    except ConversationRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_revision_conflict",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _conversation_response(updated)


@router.patch("/{conversation_id}/jianying-draft-context", response_model=ConversationResponse)
async def patch_jianying_draft_conversation_context(
    conversation_id: str,
    body: JianyingDraftConversationContextPatchRequest,
    request: Request,
) -> ConversationResponse:
    user_id = await get_current_user(request)
    pending_job = body.pending_job()
    if pending_job is not None and pending_job.get("conversation_id") != conversation_id:
        raise HTTPException(status_code=422, detail="Pending Jianying draft job belongs to another conversation")
    optional_fields: dict[str, Any] = {}
    if "jianying_draft_job_resume_error" in body.model_fields_set:
        optional_fields["resume_error"] = body.jianying_draft_job_resume_error
    updated = await _task_store(request).patch_jianying_draft_conversation_context(
        conversation_id,
        user_id=user_id,
        expected_job_id=body.expected_job_id,
        pending_job=pending_job,
        records=body.records(),
        last_phase=body.last_phase,
        **optional_fields,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _conversation_response(updated)


@router.post("/{conversation_id}/messages", response_model=ConversationMessageResponse)
async def append_conversation_message(
    conversation_id: str,
    body: ConversationMessageCreateRequest,
    request: Request,
) -> ConversationMessageResponse:
    user_id = await get_current_user(request)
    store = _task_store(request)
    return await _append_conversation_message(store, conversation_id, body, user_id=user_id)


@router.post("/{conversation_id}/messages/start", response_model=ConversationMessageJobStartResponse)
async def start_append_conversation_message(
    conversation_id: str,
    body: ConversationMessageCreateRequest,
    request: Request,
) -> ConversationMessageJobStartResponse:
    _trim_conversation_message_jobs()
    user_id = await get_current_user(request)
    store = _task_store(request)
    if await store.get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    message_id = _conversation_message_id(conversation_id, body.payload)
    job_key = (user_id, conversation_id, message_id)
    existing_job_id = _CONVERSATION_MESSAGE_JOB_KEYS.get(job_key)
    existing_job = None if existing_job_id is None else _CONVERSATION_MESSAGE_JOBS.get(existing_job_id)
    if existing_job is not None and existing_job.get("status") != "failed":
        status = str(existing_job.get("status") or "running")
        return ConversationMessageJobStartResponse(
            ok=True,
            job_id=existing_job_id,
            status=status,
            message=_conversation_message_job_message(status),
        )
    job_id = uuid.uuid4().hex
    _CONVERSATION_MESSAGE_JOB_KEYS[job_key] = job_id
    _CONVERSATION_MESSAGE_JOBS[job_id] = {
        "status": "running",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "message_id": message_id,
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_append_conversation_message_job(job_id, store, conversation_id, body, user_id))
    return ConversationMessageJobStartResponse(ok=True, job_id=job_id, status="running", message="对话消息保存任务已启动。")


@router.get("/{conversation_id}/messages/jobs/{job_id}", response_model=ConversationMessageJobStatusResponse)
async def get_append_conversation_message_job(
    conversation_id: str,
    job_id: str,
    request: Request,
) -> ConversationMessageJobStatusResponse:
    user_id = await get_current_user(request)
    job = _CONVERSATION_MESSAGE_JOBS.get(job_id)
    if job is None or job.get("conversation_id") != conversation_id or job.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Conversation message job not found")
    result = job.get("result")
    if isinstance(result, ConversationMessageResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = ConversationMessageResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return ConversationMessageJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_conversation_message_job_message(status),
    )


@router.post("/{conversation_id}/resume", response_model=ConversationDetailResponse)
async def resume_conversation(conversation_id: str, request: Request) -> ConversationDetailResponse:
    user_id = await get_current_user(request)
    store = _task_store(request)
    conversation = await store.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await store.update_conversation(conversation_id, user_id=user_id, context=conversation.context)
    return await _conversation_detail(store, conversation_id, user_id=user_id)


async def _append_conversation_message(
    store: PixelFlowTaskStore,
    conversation_id: str,
    body: ConversationMessageCreateRequest,
    *,
    user_id: str | None,
) -> ConversationMessageResponse:
    if await store.get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    message = await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id=_conversation_message_id(conversation_id, body.payload),
            conversation_id=conversation_id,
            user_id=user_id,
            role=body.role,
            content=body.content,
            payload=body.payload,
        )
    )
    return _message_response(message)


def _conversation_message_id(conversation_id: str, payload: dict[str, Any]) -> str:
    """按对话与前端消息 ID 生成可重试的稳定主键。"""
    client_message_id = payload.get("client_message_id")
    if isinstance(client_message_id, str) and client_message_id.strip():
        idempotency_key = f"pixelflow-conversation-message:{conversation_id}:{client_message_id.strip()}"
        return uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex
    return uuid.uuid4().hex


@router.patch("/{conversation_id}/messages/{message_id}", response_model=ConversationMessageResponse)
async def update_conversation_message(
    conversation_id: str,
    message_id: str,
    body: ConversationMessageUpdateRequest,
    request: Request,
) -> ConversationMessageResponse:
    user_id = await get_current_user(request)
    store = _task_store(request)
    if await store.get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    updated = await store.update_conversation_message(
        conversation_id,
        message_id,
        user_id=user_id,
        **body.model_dump(exclude_unset=True),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation message not found")
    return _message_response(updated)


@router.get("/{conversation_id}/trace", response_model=ConversationTraceResponse)
async def get_conversation_trace(conversation_id: str, request: Request) -> ConversationTraceResponse:
    """内部调试专用：返回某个对话的 vendor_call/llm_call trace 时间线。

    只有 content-app ``ROLE_ADMIN`` 用户能调用；面向内部排查，会包含原始
    prompt 和供应商请求/响应，不能在普通用户可见的 UI 里展示。
    """
    authorization = request.headers.get("Authorization")
    if not await is_admin_user(authorization):
        raise HTTPException(status_code=403, detail="仅管理员可查看对话 trace")

    store = _task_store(request)
    if await store.get_conversation(conversation_id, user_id=None) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    events = await store.list_trace_events(conversation_id, user_id=None)
    return ConversationTraceResponse(items=[ConversationTraceEventResponse(**event) for event in events])


async def _run_append_conversation_message_job(
    job_id: str,
    store: PixelFlowTaskStore,
    conversation_id: str,
    body: ConversationMessageCreateRequest,
    user_id: str | None,
) -> None:
    message_id = _CONVERSATION_MESSAGE_JOBS.get(job_id, {}).get(
        "message_id",
    )
    try:
        result = await _append_conversation_message(store, conversation_id, body, user_id=user_id)
        _CONVERSATION_MESSAGE_JOBS[job_id] = {
            "status": "completed",
            "conversation_id": conversation_id,
            "user_id": user_id,
            "message_id": message_id,
            "result": result,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _CONVERSATION_MESSAGE_JOBS[job_id] = {
            "status": "failed",
            "conversation_id": conversation_id,
            "user_id": user_id,
            "message_id": message_id,
            "result": None,
            "error": str(exc),
        }


def _trim_conversation_message_jobs() -> None:
    overflow = len(_CONVERSATION_MESSAGE_JOBS) - _MAX_CONVERSATION_MESSAGE_JOBS + 1
    if overflow <= 0:
        return
    for job_id in list(_CONVERSATION_MESSAGE_JOBS.keys())[:overflow]:
        removed = _CONVERSATION_MESSAGE_JOBS.pop(job_id, None)
        if not isinstance(removed, dict):
            continue
        message_id = removed.get("message_id")
        conversation_id = removed.get("conversation_id")
        user_id = removed.get("user_id")
        if all(isinstance(value, str) for value in (message_id, conversation_id, user_id)):
            key = (user_id, conversation_id, message_id)
            if _CONVERSATION_MESSAGE_JOB_KEYS.get(key) == job_id:
                _CONVERSATION_MESSAGE_JOB_KEYS.pop(key, None)


def _conversation_message_job_message(status: str) -> str:
    if status == "completed":
        return "对话消息已保存。"
    if status == "failed":
        return "对话消息保存失败。"
    return "对话消息保存中。"


def _validated_runtime_handoff_marker(
    *,
    user_id: str,
    conversation_id: str,
    pending_message_job: object,
    replacement_context: object,
) -> dict[str, str] | None:
    """校验旧流程接力证据，避免前端随意完成尚未处理的 Runtime Turn。"""

    if isinstance(pending_message_job, dict) and pending_message_job.get("kind") == "conversation_message":
        job_id = pending_message_job.get("job_id")
        client_input_id = pending_message_job.get("source_message_id")
        if not isinstance(job_id, str) or not isinstance(client_input_id, str):
            return None
        try:
            normalized_client_input_id = str(uuid.UUID(client_input_id))
        except ValueError:
            return None
        job = _CONVERSATION_MESSAGE_JOBS.get(job_id)
        expected_message_id = _conversation_message_id(
            conversation_id,
            {"client_message_id": normalized_client_input_id},
        )
        if not isinstance(job, dict) or job.get("user_id") != user_id or job.get("conversation_id") != conversation_id or job.get("message_id") != expected_message_id or job.get("status") not in {"running", "completed"}:
            return None
        return {
            "client_input_id": normalized_client_input_id,
            "job_id": job_id,
            "message_id": expected_message_id,
            "status": "pending_ack",
        }

    # 图片、视频、PPT 等旧 v2 直接流程没有 conversation_message job，
    # 由前端在旧流程真正完成后提交受限的通用接力标记。
    if not isinstance(replacement_context, dict):
        return None
    marker = replacement_context.get("legacy_handoff") or replacement_context.get("legacyHandoff")
    if not isinstance(marker, dict) or marker.get("source") != "frontend_v2":
        return None
    client_input_id = marker.get("client_input_id") or marker.get("clientInputId")
    if not isinstance(client_input_id, str):
        return None
    try:
        normalized_client_input_id = str(uuid.UUID(client_input_id))
    except ValueError:
        return None
    return {
        "client_input_id": normalized_client_input_id,
        "status": "pending_ack",
    }
