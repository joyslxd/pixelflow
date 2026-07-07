"""PixelFlow 对话历史 API。

本文件是 PixelFlow 会话历史的 Controller 层：负责 `/agent/conversations`
入参、出参、当前用户隔离和恢复 payload 组织。真正持久化仍由
``pixelflow.tasks`` Store 负责。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.content_app_auth import is_admin_user
from app.gateway.deps import get_current_user
from pixelflow.tasks import PixelFlowConversationMessageRecord, PixelFlowConversationRecord, PixelFlowTaskStore

router = APIRouter(prefix="/agent/conversations", tags=["pixelflow-conversations"])


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


class ConversationMessageCreateRequest(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


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


@router.post("/{conversation_id}/messages", response_model=ConversationMessageResponse)
async def append_conversation_message(
    conversation_id: str,
    body: ConversationMessageCreateRequest,
    request: Request,
) -> ConversationMessageResponse:
    user_id = await get_current_user(request)
    store = _task_store(request)
    if await store.get_conversation(conversation_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    message = await store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            user_id=user_id,
            role=body.role,
            content=body.content,
            payload=body.payload,
        )
    )
    return _message_response(message)


@router.post("/{conversation_id}/resume", response_model=ConversationDetailResponse)
async def resume_conversation(conversation_id: str, request: Request) -> ConversationDetailResponse:
    user_id = await get_current_user(request)
    store = _task_store(request)
    conversation = await store.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await store.update_conversation(conversation_id, user_id=user_id, context=conversation.context)
    return await _conversation_detail(store, conversation_id, user_id=user_id)


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
