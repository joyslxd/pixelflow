"""PixelFlow structured user preference API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user
from pixelflow.preferences import UserPreferenceStore

router = APIRouter(prefix="/api/users", tags=["pixelflow-preferences"])


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


async def _require_self(path_user_id: str, request: Request) -> str:
    current = await get_current_user(request)
    if current is None:
        # Auth may be disabled in local dev; allow the explicit path id.
        return path_user_id
    if path_user_id != current:
        raise HTTPException(status_code=403, detail="Cannot access another user's PixelFlow preferences")
    return current


@router.get("/{user_id}/preferences", response_model=PreferenceResponse)
async def get_preferences(user_id: str, request: Request) -> PreferenceResponse:
    resolved = await _require_self(user_id, request)
    pref = await _store(request).get(resolved)
    return PreferenceResponse(**pref.to_dict())


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
    return PreferenceResponse(**pref.to_dict())


@router.post("/{user_id}/preferences/feedback", response_model=PreferenceResponse)
async def append_preference_feedback(user_id: str, body: PreferenceFeedbackRequest, request: Request) -> PreferenceResponse:
    resolved = await _require_self(user_id, request)
    pref = await _store(request).append_feedback(resolved, body.feedback, task_id=body.task_id, metadata=body.metadata)
    return PreferenceResponse(**pref.to_dict())
