"""Validated creation contract shared by PixelFlow video workflow stages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator


class ImageModelCapabilities(BaseModel):
    aspect_ratios: list[str] = Field(min_length=1)
    sizes: list[str] = Field(min_length=1)

    @field_validator("aspect_ratios", "sizes", mode="before")
    @classmethod
    def normalize_options(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("image model capability options must be a list")
        normalized: list[str] = []
        for item in value:
            option = str(item or "").strip()
            if option and option not in normalized:
                normalized.append(option)
        if not normalized:
            raise ValueError("image model capability options cannot be empty")
        return normalized


class VideoCreationContract(BaseModel):
    version: StrictInt = 1
    intent: Literal["video"] = "video"
    video_duration_sec: StrictInt = Field(ge=4, le=300)
    video_ratio: str
    video_model_mode: Literal["system_recommended", "manual"] = "system_recommended"
    video_model: str
    video_size: str = "1080p"
    video_sound: Literal["on", "off"] = "on"
    image_model: str
    image_model_capabilities: ImageModelCapabilities
    video_usage: str
    visual_style: str = ""
    confirmed_by_user: bool = True
    scene_image_ratio: str | None = None
    scene_image_size: str | None = None
    scene_image_spec_source: Literal["plan_llm", "deterministic_fallback"] | None = None

    @field_validator("video_ratio", "video_model", "video_size", "image_model", "video_usage")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("creation contract text fields cannot be empty")
        return normalized

    @field_validator("video_model")
    @classmethod
    def require_seedance_video_model(cls, value: str) -> str:
        if "seedance" not in value.lower():
            raise ValueError("video_model must be a Seedance model")
        return value


def build_video_creation_contract(form_values: Mapping[str, Any]) -> VideoCreationContract:
    """Build the confirmed input contract without guessing scene-image specs."""
    payload = dict(form_values)
    payload.setdefault("version", 1)
    payload.setdefault("intent", "video")
    payload.setdefault("video_duration_sec", 30)
    payload.setdefault("video_ratio", "9:16")
    payload.setdefault("video_model_mode", "system_recommended")
    payload.setdefault("video_model", "seedance-2.0")
    payload.setdefault("video_size", "1080p")
    payload.setdefault("video_sound", "on")
    payload.setdefault("image_model", "gpt-image-2")
    payload.setdefault(
        "image_model_capabilities",
        {
            "aspect_ratios": ["1:1", "16:9", "9:16"],
            "sizes": ["1080p", "2K", "4K"],
        },
    )
    payload.setdefault("video_usage", "宣传片")
    payload.setdefault("visual_style", "")
    payload.setdefault("confirmed_by_user", True)
    return VideoCreationContract.model_validate(payload)


def resolve_scene_image_spec(
    contract: VideoCreationContract,
    suggested_ratio: str | None,
    suggested_size: str | None,
) -> tuple[VideoCreationContract, list[str]]:
    """Constrain Plan LLM scene-image choices to the selected model config."""
    ratios = contract.image_model_capabilities.aspect_ratios
    sizes = contract.image_model_capabilities.sizes
    corrections: list[str] = []

    ratio = _supported_option(suggested_ratio, ratios)
    if ratio is None:
        ratio = _supported_option(contract.video_ratio, ratios) or ratios[0]
        corrections.append(f"scene image ratio adjusted to {ratio}")

    size = _supported_option(suggested_size, sizes)
    if size is None:
        size = next((_supported_option(preferred, sizes) for preferred in ("4K", "2K", "1080p") if _supported_option(preferred, sizes)), None)
        size = size or sizes[0]
        corrections.append(f"scene image size adjusted to {size}")

    source: Literal["plan_llm", "deterministic_fallback"] = "plan_llm" if not corrections else "deterministic_fallback"
    resolved = contract.model_copy(
        update={
            "scene_image_ratio": ratio,
            "scene_image_size": size,
            "scene_image_spec_source": source,
        }
    )
    return resolved, corrections


def _supported_option(value: str | None, options: list[str]) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return next((option for option in options if option.lower() == normalized), None)
