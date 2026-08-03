"""PixelFlow 对话历史 API。

本文件是 PixelFlow 会话历史的 Controller 层：负责 `/agent/conversations`
入参、出参、当前用户隔离和恢复 payload 组织。真正持久化仍由
``pixelflow.tasks`` Store 负责。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gateway.content_app_auth import is_admin_user
from app.gateway.deps import get_current_user
from pixelflow.agent_runtime.contracts import (
    InterruptResponseRequest,
    TurnStartRequest,
)
from pixelflow.agent_runtime.service import (
    AgentRuntimeContextConflictError,
    AgentRuntimeInterruptConflictError,
    AgentRuntimeInterruptRequestValidationError,
    AgentRuntimeInterruptStateError,
    AgentRuntimeLegacyInterruptOwnershipError,
    AgentRuntimeService,
    AgentRuntimeSnapshotResponse,
    AgentRuntimeUnavailableError,
    AgentTurnJobResponse,
    AgentTurnStartResponse,
)
from pixelflow.agent_workflows.video.live_capabilities import (
    TransientTurnCredential,
)
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
    title: str = ""
    current_task_id: str | None = None
    last_phase: str = "idle"
    context: dict[str, Any] = Field(default_factory=dict)
    initial_intent: Literal["image", "video", "ppt", "video_analysis"] | None = None


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


def _agent_runtime_service(request: Request) -> AgentRuntimeService:
    service = getattr(
        request.app.state,
        "pixelflow_agent_runtime_service",
        None,
    )
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="PixelFlow Agent Runtime not available",
        )
    return service


def _runtime_http_exception(exc: Exception) -> HTTPException:
    """把 Service 异常稳定映射为新 Runtime API 的 HTTP 合同。"""

    if isinstance(exc, AgentRuntimeUnavailableError):
        return HTTPException(
            status_code=409,
            detail={"code": "agent_runtime_unavailable"},
        )
    if isinstance(exc, AgentRuntimeContextConflictError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "agent_runtime_context_conflict",
                "expected_context_version": exc.expected_context_version,
                "current_context_version": exc.current_context_version,
            },
        )
    if isinstance(exc, AgentRuntimeInterruptConflictError):
        return HTTPException(
            status_code=409,
            detail={"code": "agent_runtime_interrupt_conflict"},
        )
    if isinstance(exc, AgentRuntimeInterruptStateError):
        return HTTPException(
            status_code=409,
            detail={"code": "agent_runtime_interrupt_state_invalid"},
        )
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail="Conversation not found")
    return HTTPException(status_code=500, detail="Agent Runtime request failed")


async def _preflight_agent_interrupt_response(
    conversation_id: str,
    interrupt_id: str,
    request: Request,
) -> None:
    """在 FastAPI DTO 校验前只读确认所有权并生成固定安全错误。"""

    try:
        raw_body = await request.json()
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_runtime_interrupt_response_invalid"},
        ) from None
    service = _agent_runtime_service(request)
    try:
        await service.preflight_interrupt_response(
            user_id=await get_current_user(request),
            conversation_id=conversation_id,
            request=raw_body,
        )
    except AgentRuntimeLegacyInterruptOwnershipError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "agent_runtime_interrupt_owned_by_legacy_v2",
                "interrupt_id": interrupt_id,
            },
        ) from exc
    except AgentRuntimeInterruptRequestValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_runtime_interrupt_response_invalid"},
        ) from exc
    except (
        AgentRuntimeInterruptStateError,
        AgentRuntimeUnavailableError,
        LookupError,
    ) as exc:
        raise _runtime_http_exception(exc) from exc


def _transient_turn_credential(request: Request) -> TransientTurnCredential | None:
    """只把当前 HTTP Authorization 封装为不可序列化的一次性凭据。"""

    authorization = request.headers.get("Authorization")
    if authorization is None or not authorization.strip():
        return None
    return TransientTurnCredential(authorization=authorization)


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
    service = getattr(
        request.app.state,
        "pixelflow_agent_runtime_service",
        None,
    )
    if service is None:
        context = sanitize_client_conversation_context(body.context)
        orchestration_mode = "frontend_v2"
        orchestration_version = 1
    else:
        assignment = service.assignment_for_new_conversation(
            body.context,
            initial_intent=body.initial_intent,
        )
        context = assignment.context
        orchestration_mode = assignment.orchestration_mode.value
        orchestration_version = assignment.orchestration_version
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
    "/{conversation_id}/turns/start",
    response_model=AgentTurnStartResponse,
)
async def start_agent_turn(
    conversation_id: str,
    body: TurnStartRequest,
    request: Request,
) -> AgentTurnStartResponse:
    """保存统一输入并注册可幂等恢复的 Turn。"""

    user_id = await get_current_user(request)
    service = _agent_runtime_service(request)
    try:
        result = await service.start_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            request=body,
        )
    except (
        AgentRuntimeContextConflictError,
        AgentRuntimeUnavailableError,
        LookupError,
    ) as exc:
        raise _runtime_http_exception(exc) from exc
    service.notify_registered_turn(
        result.turn_id,
        credential=_transient_turn_credential(request),
    )
    return result


@router.get(
    "/{conversation_id}/agent-snapshot",
    response_model=AgentRuntimeSnapshotResponse,
)
async def get_agent_snapshot(
    conversation_id: str,
    request: Request,
) -> AgentRuntimeSnapshotResponse:
    """返回刷新、断网或切换对话后的唯一权威恢复快照。"""

    user_id = await get_current_user(request)
    try:
        return await _agent_runtime_service(request).snapshot(
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except (
        AgentRuntimeInterruptStateError,
        AgentRuntimeUnavailableError,
        LookupError,
    ) as exc:
        raise _runtime_http_exception(exc) from exc


@router.get("/{conversation_id}/agent-events")
async def stream_agent_events(
    conversation_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
) -> StreamingResponse:
    """按持久化 cursor 提供可断点续传的 SSE；未知 cursor 要求重载快照。"""

    user_id = await get_current_user(request)
    service = _agent_runtime_service(request)
    try:
        initial = await service.events_after(
            user_id=user_id,
            conversation_id=conversation_id,
            cursor=cursor,
        )
    except (AgentRuntimeUnavailableError, LookupError) as exc:
        raise _runtime_http_exception(exc) from exc
    if initial is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "agent_runtime_cursor_unknown"},
        )

    async def event_stream():
        current_cursor = cursor
        pending = initial
        idle_polls = 0
        while True:
            for event in pending:
                current_cursor = event.cursor
                payload = event.model_dump_json()
                yield f"data: {payload}\n\n"
            if await request.is_disconnected():
                return
            await asyncio.sleep(1)
            try:
                next_events = await service.events_after(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    cursor=current_cursor,
                )
            except (AgentRuntimeUnavailableError, LookupError):
                return
            if next_events is None:
                yield ('event: reload_required\ndata: {"code":"agent_runtime_cursor_unknown"}\n\n')
                return
            pending = next_events
            idle_polls = 0 if pending else idle_polls + 1
            if idle_polls >= 15:
                yield ": heartbeat\n\n"
                idle_polls = 0

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{conversation_id}/turns/jobs/{run_id}",
    response_model=AgentTurnJobResponse,
)
async def get_agent_turn_job(
    conversation_id: str,
    run_id: str,
    request: Request,
) -> AgentTurnJobResponse:
    """SSE 不可用时只轮询原 run，不重新创建 Turn。"""

    user_id = await get_current_user(request)
    try:
        result = await _agent_runtime_service(request).get_run(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
    except (AgentRuntimeUnavailableError, LookupError) as exc:
        raise _runtime_http_exception(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Turn run not found")
    return result


@router.post(
    "/{conversation_id}/interrupts/{interrupt_id}/responses",
    response_model=AgentTurnJobResponse,
    dependencies=[Depends(_preflight_agent_interrupt_response)],
)
async def respond_to_agent_interrupt(
    conversation_id: str,
    interrupt_id: str,
    body: InterruptResponseRequest,
    request: Request,
) -> AgentTurnJobResponse:
    """live 对话在原 Turn 上登记响应；旧 v2 继续保持原所有权。"""

    user_id = await get_current_user(request)
    service = _agent_runtime_service(request)
    try:
        result = await service.respond_to_interrupt(
            user_id=user_id,
            conversation_id=conversation_id,
            interrupt_id=interrupt_id,
            request=body,
        )
    except AgentRuntimeLegacyInterruptOwnershipError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "agent_runtime_interrupt_owned_by_legacy_v2",
                "interrupt_id": interrupt_id,
            },
        ) from exc
    except AgentRuntimeInterruptRequestValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_runtime_interrupt_response_invalid"},
        ) from exc
    except AgentRuntimeInterruptConflictError as exc:
        if exc.reason_code == "video_quota_resume_stale":
            return JSONResponse(
                status_code=409,
                content={"reason_code": "video_quota_resume_stale"},
            )
        raise _runtime_http_exception(exc) from exc
    except (
        AgentRuntimeInterruptStateError,
        AgentRuntimeUnavailableError,
        LookupError,
    ) as exc:
        raise _runtime_http_exception(exc) from exc
    service.notify_registered_interrupt(
        interrupt_id,
        credential=_transient_turn_credential(request),
    )
    return result


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
    replacement_context = fields.get("context")
    pending_message_job = (
        replacement_context.get(
            "pendingMessageJob",
            replacement_context.get("pending_message_job"),
        )
        if isinstance(replacement_context, dict)
        else None
    )
    handoff_marker = _validated_runtime_handoff_marker(
        user_id=user_id,
        conversation_id=conversation_id,
        pending_message_job=pending_message_job,
        replacement_context=replacement_context,
    )
    if handoff_marker is not None:
        marker_client_input_id = uuid.UUID(
            handoff_marker["client_input_id"],
        )
        if not await _agent_runtime_service(
            request,
        ).legacy_handoff_is_eligible(
            user_id=user_id,
            conversation_id=conversation_id,
            client_input_id=marker_client_input_id,
        ):
            handoff_marker = None
    if handoff_marker is not None:
        # 普通 context 替换和补偿 marker 由同一 Conversation Store
        # 临界区提交；进程在后续 ack 前退出时，刷新仍可幂等续做。
        fields["_agent_runtime_patch"] = {
            "legacy_handoff": handoff_marker,
        }
        # 接力标记只用于本次 Controller 请求，不能作为业务快照长期暴露。
        if isinstance(replacement_context, dict):
            sanitized_context = dict(replacement_context)
            sanitized_context.pop("legacy_handoff", None)
            sanitized_context.pop("legacyHandoff", None)
            fields["context"] = sanitized_context
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
    if handoff_marker is not None:
        try:
            reconciled = await _agent_runtime_service(
                request,
            ).reconcile_pending_legacy_handoff(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001 - marker 已持久化，刷新时继续补偿
            logger.warning(
                "Agent Runtime 旧流程接力等待恢复：异常类型=%s",
                type(exc).__name__,
            )
        else:
            if reconciled:
                refreshed = await _task_store(request).get_conversation(
                    conversation_id,
                    user_id=user_id,
                )
                if refreshed is not None:
                    updated = refreshed
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
