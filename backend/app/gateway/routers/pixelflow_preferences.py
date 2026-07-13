"""PixelFlow 结构化用户偏好 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user
from app.gateway.pixelflow_memory import power_mem_service, record_power_mem_background
from pixelflow.preferences import UserPreferenceStore

router = APIRouter(prefix="/agent/users", tags=["pixelflow-preferences"])


class PreferenceResponse(BaseModel):
    user_id: str
    style_preferences: dict[str, Any] = Field(default_factory=dict)
    negative_rules: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    recent_feedback: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str = ""
    semantic_memory: dict[str, Any] = Field(default_factory=dict)


class PreferenceUpdateRequest(BaseModel):
    style_preferences: dict[str, Any] = Field(default_factory=dict)
    negative_rules: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class PreferenceFeedbackRequest(BaseModel):
    feedback: str = Field(..., min_length=1)
    task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _store(request: Request) -> UserPreferenceStore:
    store = getattr(request.app.state, "pixelflow_preference_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="PixelFlow preference store not available")
    return store


def _preference_payload(request: Request, pref: Any) -> dict[str, Any]:
    data = pref.to_dict()
    service = power_mem_service(request)
    if service is not None:
        data["semantic_memory"] = service.status_snapshot()
    return data


async def _require_self(path_user_id: str, request: Request) -> str:
    current = await get_current_user(request)
    if current is None:
        # 本地开发可能关闭鉴权；此时允许使用路径里的显式 user_id。
        return path_user_id
    if path_user_id != current:
        raise HTTPException(status_code=403, detail="Cannot access another user's PixelFlow preferences")
    return current


@router.get("/{user_id}/preferences", response_model=PreferenceResponse)
async def get_preferences(user_id: str, request: Request) -> PreferenceResponse:
    resolved = await _require_self(user_id, request)
    pref = await _store(request).get(resolved)
    return PreferenceResponse(**_preference_payload(request, pref))


@router.put("/{user_id}/preferences", response_model=PreferenceResponse)
async def update_preferences(user_id: str, body: PreferenceUpdateRequest, request: Request) -> PreferenceResponse:
    resolved = await _require_self(user_id, request)
    pref = await _store(request).update(
        resolved,
        {
            "style_preferences": body.style_preferences,
            "negative_rules": body.negative_rules,
            "defaults": body.defaults,
        },
    )
    service = power_mem_service(request)
    if service is not None:
        record_power_mem_background(
            service,
            user_id=resolved,
            content=_preference_update_summary(body),
            category="preference",
            source_agent="preference_api",
            metadata={"source": "preferences_update"},
            memory_type="preference",
        )
    return PreferenceResponse(**_preference_payload(request, pref))


@router.post("/{user_id}/preferences/feedback", response_model=PreferenceResponse)
async def append_preference_feedback(user_id: str, body: PreferenceFeedbackRequest, request: Request) -> PreferenceResponse:
    resolved = await _require_self(user_id, request)
    pref = await _store(request).append_feedback(resolved, body.feedback, task_id=body.task_id, metadata=body.metadata)
    service = power_mem_service(request)
    if service is not None:
        record_power_mem_background(
            service,
            user_id=resolved,
            content=body.feedback,
            category="preference",
            source_agent="preference_api",
            metadata={"source": "preferences_feedback", "task_id": body.task_id, **body.metadata},
            memory_type="preference",
            run_id=body.task_id,
        )
    return PreferenceResponse(**_preference_payload(request, pref))


def _preference_update_summary(body: PreferenceUpdateRequest) -> str:
    parts: list[str] = []
    if body.style_preferences:
        parts.append(f"风格偏好：{body.style_preferences}")
    if body.negative_rules:
        parts.append(f"负向规则：{body.negative_rules}")
    if body.defaults:
        parts.append(f"默认参数：{body.defaults}")
    return "；".join(parts)
