"""模型上下文能力档案及其配置解析。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONSERVATIVE_CONTEXT_TOKENS = 128 * 1024
CONSERVATIVE_OUTPUT_TOKENS = 8 * 1024
ProfileResolutionStatus = Literal[
    "verified",
    "fallback_missing",
    "fallback_unverified",
    "fallback_expired",
]


class ModelContextProfile(BaseModel):
    """记录单个模型声明的上下文能力及其验证证据。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    model_name: str = Field(min_length=1)
    max_context_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    tokenizer_strategy: str = Field(min_length=1)
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    source: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_capability_invariants(self) -> Self:
        """校验容量边界和验证证据的时间语义。"""
        if self.max_output_tokens >= self.max_context_tokens:
            raise ValueError("max_output_tokens 必须小于 max_context_tokens")
        for field_name in ("verified_at", "expires_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError(f"{field_name} 必须包含时区")
        if (
            self.verified_at is not None
            and self.expires_at is not None
            and self.expires_at <= self.verified_at
        ):
            raise ValueError("expires_at 必须晚于 verified_at")
        return self


class ModelContextProfileResolution(BaseModel):
    """返回实际采用的档案和可审计的降级原因。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ProfileResolutionStatus
    profile: ModelContextProfile


def _model_config_mapping(
    raw_model: Mapping[str, Any] | BaseModel,
) -> Mapping[str, Any]:
    """把字典或 Pydantic 模型统一转换为只读配置映射。"""
    if isinstance(raw_model, Mapping):
        return raw_model
    if isinstance(raw_model, BaseModel):
        return raw_model.model_dump()
    raise ValueError("models 中的每一项都必须是配置对象")


def parse_model_context_profiles(
    raw_models: Sequence[Mapping[str, Any] | BaseModel],
) -> dict[str, ModelContextProfile]:
    """从现有 ``models[]`` 配置中提取显式上下文档案。"""

    profiles: dict[str, ModelContextProfile] = {}
    for raw_model in raw_models:
        model_config = _model_config_mapping(raw_model)
        raw_profile = model_config.get("context_profile")
        if raw_profile is None:
            continue
        if not isinstance(raw_profile, Mapping):
            raise ValueError("models[].context_profile 必须是对象")
        if "model_name" in raw_profile:
            raise ValueError("context_profile 不得重复声明 model_name")
        model_name = model_config.get("name")
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("配置模型 name 不能为空")
        normalized_model_name = model_name.strip()
        if normalized_model_name in profiles:
            raise ValueError(f"模型 {normalized_model_name} 的 context_profile 重复")
        profile_data = dict(raw_profile)
        profile_data["model_name"] = normalized_model_name
        profiles[normalized_model_name] = ModelContextProfile.model_validate(profile_data)
    return profiles


def _conservative_profile(
    model_name: str,
    declared_profile: ModelContextProfile | None = None,
) -> ModelContextProfile:
    max_context_tokens = CONSERVATIVE_CONTEXT_TOKENS
    max_output_tokens = CONSERVATIVE_OUTPUT_TOKENS
    if declared_profile is not None:
        max_context_tokens = min(
            max_context_tokens,
            declared_profile.max_context_tokens,
        )
        max_output_tokens = min(
            max_output_tokens,
            declared_profile.max_output_tokens,
            max_context_tokens - 1,
        )
    return ModelContextProfile(
        model_name=model_name,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        tokenizer_strategy="conservative_estimate",
        source="PixelFlow 内建保守档案",
    )


def resolve_model_context_profile(
    model_name: str,
    profiles: Mapping[str, ModelContextProfile],
    *,
    now: datetime,
) -> ModelContextProfileResolution:
    """仅采用有效验证档案，否则返回不放大声明上限的至多 128K 档案。"""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    profile = profiles.get(model_name)
    if profile is None:
        status: ProfileResolutionStatus = "fallback_missing"
    elif (
        profile.verified_at is None
        or profile.source is None
        or profile.verified_at > now
    ):
        status = "fallback_unverified"
    elif profile.expires_at is not None and profile.expires_at <= now:
        status = "fallback_expired"
    else:
        return ModelContextProfileResolution(status="verified", profile=profile)
    return ModelContextProfileResolution(
        status=status,
        profile=_conservative_profile(model_name, profile),
    )
