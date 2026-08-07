"""轻量化对话路由的服务端权威合同。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .base import ContractModel


class RouteIntent(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    PPT = "ppt"
    VIDEO_ANALYSIS = "video_analysis"
    UNKNOWN = "unknown"


class RouteDecisionSource(StrEnum):
    RULE = "rule"
    LLM = "llm"
    FALLBACK = "fallback"


class RouteMaterial(ContractModel):
    artifact_ref: str | None = Field(default=None, max_length=256)
    media_type: str | None = Field(default=None, max_length=64)
    filename: str | None = Field(default=None, max_length=512)
    mime_type: str | None = Field(default=None, max_length=128)


class RouteRequest(ContractModel):
    content: str = Field(min_length=1)
    materials: tuple[RouteMaterial, ...] = ()


RouteReasonCode = Literal[
    "explicit_video_request",
    "explicit_video_analysis",
    "explicit_ppt_request",
    "explicit_image_request",
    "llm_classified",
    "ambiguous_request",
    "classifier_unavailable",
    "request_too_large",
]


class RouteDecision(ContractModel):
    intent: RouteIntent
    confidence: float = Field(ge=0, le=1)
    decision_source: RouteDecisionSource
    reason_code: RouteReasonCode
    requires_clarification: bool
