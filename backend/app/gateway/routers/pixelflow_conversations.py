"""PixelFlow 对话历史 API。

本文件是 PixelFlow 会话历史的 Controller 层：负责 `/agent/conversations`
入参、出参、当前用户隔离和恢复 payload 组织。真正持久化仍由
``pixelflow.tasks`` Store 负责。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gateway.content_app_auth import is_admin_user
from app.gateway.deps import get_current_user
from pixelflow.tasks import PixelFlowConversationMessageRecord, PixelFlowConversationRecord, PixelFlowTaskStore

router = APIRouter(prefix="/agent/conversations", tags=["pixelflow-conversations"])

_CONVERSATION_MESSAGE_JOBS: dict[str, dict[str, Any]] = {}
_MAX_CONVERSATION_MESSAGE_JOBS = 300


class ConversationCreateRequest(BaseModel):
    title: str = ""
    current_task_id: str | None = None
    last_phase: str = "idle"
    context: dict[str, Any] = Field(default_factory=dict)


class ConversationUpdateRequest(BaseModel):
    title: str | None = None
    current_task_id: str | None = None
    last_phase: str | None = None
    context: dict[str, Any] | None = None


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
    title: str = ""
    current_task_id: str | None = None
    last_phase: str = "idle"
    context: dict[str, Any] = Field(default_factory=dict)
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
    record = await _task_store(request).create_conversation(
        PixelFlowConversationRecord(
            conversation_id=uuid.uuid4().hex,
            user_id=user_id,
            title=title,
            current_task_id=body.current_task_id,
            last_phase=body.last_phase,
            context=body.context,
        )
    )
    return _conversation_response(record)


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
    updated = await _task_store(request).update_conversation(conversation_id, user_id=user_id, **fields)
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
    job_id = uuid.uuid4().hex
    _CONVERSATION_MESSAGE_JOBS[job_id] = {
        "status": "running",
        "conversation_id": conversation_id,
        "user_id": user_id,
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
    try:
        result = await _append_conversation_message(store, conversation_id, body, user_id=user_id)
        _CONVERSATION_MESSAGE_JOBS[job_id] = {
            "status": "completed",
            "conversation_id": conversation_id,
            "user_id": user_id,
            "result": result,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _CONVERSATION_MESSAGE_JOBS[job_id] = {
            "status": "failed",
            "conversation_id": conversation_id,
            "user_id": user_id,
            "result": None,
            "error": str(exc),
        }


def _trim_conversation_message_jobs() -> None:
    overflow = len(_CONVERSATION_MESSAGE_JOBS) - _MAX_CONVERSATION_MESSAGE_JOBS + 1
    if overflow <= 0:
        return
    for job_id in list(_CONVERSATION_MESSAGE_JOBS.keys())[:overflow]:
        _CONVERSATION_MESSAGE_JOBS.pop(job_id, None)


def _conversation_message_job_message(status: str) -> str:
    if status == "completed":
        return "对话消息已保存。"
    if status == "failed":
        return "对话消息保存失败。"
    return "对话消息保存中。"
