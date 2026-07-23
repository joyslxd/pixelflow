"""Agent Runtime 模型上下文档案测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def _verified_profile_data() -> dict[str, object]:
    return {
        "max_context_tokens": 256 * 1024,
        "max_output_tokens": 8 * 1024,
        "tokenizer_strategy": "provider_usage",
        "verified_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-08-01T00:00:00Z",
        "source": "AIRouter 模型能力验证记录",
    }


def _verified_profile_data_with(**overrides: object) -> dict[str, object]:
    profile_data = _verified_profile_data()
    profile_data.update(overrides)
    return profile_data


@pytest.mark.parametrize(
    "max_context_tokens",
    [256 * 1024, 384 * 1024, 512 * 1024],
)
def test_parse_verified_model_context_profiles(
    max_context_tokens: int,
) -> None:
    from pixelflow.agent_runtime.context.profiles import parse_model_context_profiles

    profiles = parse_model_context_profiles(
        [
            {
                "name": "verified-model",
                "context_profile": {
                    "max_context_tokens": max_context_tokens,
                    "max_output_tokens": 16 * 1024,
                    "tokenizer_strategy": "provider_usage",
                    "verified_at": "2026-07-01T00:00:00Z",
                    "expires_at": "2026-08-01T00:00:00Z",
                    "source": "AIRouter 模型能力验证记录",
                },
            },
        ],
    )

    profile = profiles["verified-model"]
    assert profile.model_name == "verified-model"
    assert profile.max_context_tokens == max_context_tokens
    assert profile.max_output_tokens == 16 * 1024
    assert profile.tokenizer_strategy == "provider_usage"
    assert profile.verified_at == datetime(2026, 7, 1, tzinfo=UTC)
    assert profile.expires_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert profile.source == "AIRouter 模型能力验证记录"


@pytest.mark.parametrize(
    ("raw_models", "expected_status"),
    [
        ([], "fallback_missing"),
        (
            [
                {
                    "name": "target-model",
                    "context_profile": {
                        "max_context_tokens": 512 * 1024,
                        "max_output_tokens": 32 * 1024,
                        "tokenizer_strategy": "provider_usage",
                    },
                },
            ],
            "fallback_unverified",
        ),
        (
            [
                {
                    "name": "target-model",
                    "context_profile": {
                        "max_context_tokens": 512 * 1024,
                        "max_output_tokens": 32 * 1024,
                        "tokenizer_strategy": "provider_usage",
                        "verified_at": "2026-07-25T00:00:00Z",
                        "expires_at": "2026-08-01T00:00:00Z",
                        "source": "AIRouter 模型能力验证记录",
                    },
                },
            ],
            "fallback_unverified",
        ),
        (
            [
                {
                    "name": "target-model",
                    "context_profile": {
                        "max_context_tokens": 512 * 1024,
                        "max_output_tokens": 32 * 1024,
                        "tokenizer_strategy": "provider_usage",
                        "verified_at": "2026-06-01T00:00:00Z",
                        "expires_at": "2026-07-01T00:00:00Z",
                        "source": "AIRouter 模型能力验证记录",
                    },
                },
            ],
            "fallback_expired",
        ),
    ],
)
def test_resolve_model_context_profile_uses_conservative_fallback(
    raw_models: list[dict[str, object]],
    expected_status: str,
) -> None:
    from pixelflow.agent_runtime.context.profiles import (
        CONSERVATIVE_CONTEXT_TOKENS,
        parse_model_context_profiles,
        resolve_model_context_profile,
    )

    profiles = parse_model_context_profiles(raw_models)
    resolution = resolve_model_context_profile(
        "target-model",
        profiles,
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )

    assert resolution.status == expected_status
    assert resolution.profile.max_context_tokens == CONSERVATIVE_CONTEXT_TOKENS
    assert resolution.profile.max_context_tokens == 128 * 1024
    assert resolution.profile.model_name == "target-model"


def test_resolve_model_context_profile_keeps_current_verified_profile() -> None:
    from pixelflow.agent_runtime.context.profiles import (
        parse_model_context_profiles,
        resolve_model_context_profile,
    )

    profiles = parse_model_context_profiles(
        [
            {
                "name": "target-model",
                "context_profile": {
                    "max_context_tokens": 384 * 1024,
                    "max_output_tokens": 24 * 1024,
                    "tokenizer_strategy": "provider_usage",
                    "verified_at": "2026-07-01T00:00:00Z",
                    "expires_at": "2026-08-01T00:00:00Z",
                    "source": "AIRouter 模型能力验证记录",
                },
            },
        ],
    )

    resolution = resolve_model_context_profile(
        "target-model",
        profiles,
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )

    assert resolution.status == "verified"
    assert resolution.profile is profiles["target-model"]
    assert resolution.profile.max_context_tokens == 384 * 1024


def test_contract_minimum_profile_without_expiry_is_verified() -> None:
    from pixelflow.agent_runtime.context.profiles import (
        parse_model_context_profiles,
        resolve_model_context_profile,
    )

    raw_profile = _verified_profile_data()
    raw_profile.pop("expires_at")
    profiles = parse_model_context_profiles(
        [{"name": "target-model", "context_profile": raw_profile}],
    )

    resolution = resolve_model_context_profile(
        "target-model",
        profiles,
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )

    assert resolution.status == "verified"
    assert resolution.profile is profiles["target-model"]


@pytest.mark.parametrize(
    ("verified_at", "expires_at", "expected_status"),
    [
        (None, None, "fallback_unverified"),
        (
            "2026-06-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
            "fallback_expired",
        ),
    ],
)
def test_fallback_does_not_expand_smaller_declared_profile(
    verified_at: str | None,
    expires_at: str | None,
    expected_status: str,
) -> None:
    from pixelflow.agent_runtime.context.profiles import (
        parse_model_context_profiles,
        resolve_model_context_profile,
    )

    raw_profile: dict[str, object] = {
        "max_context_tokens": 64 * 1024,
        "max_output_tokens": 4 * 1024,
        "tokenizer_strategy": "provider_usage",
        "source": "AIRouter 模型能力验证记录",
    }
    if verified_at is not None:
        raw_profile["verified_at"] = verified_at
    if expires_at is not None:
        raw_profile["expires_at"] = expires_at
    profiles = parse_model_context_profiles(
        [{"name": "small-model", "context_profile": raw_profile}],
    )

    resolution = resolve_model_context_profile(
        "small-model",
        profiles,
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )

    assert resolution.status == expected_status
    assert resolution.profile.max_context_tokens == 64 * 1024
    assert resolution.profile.max_output_tokens == 4 * 1024
    assert resolution.profile.tokenizer_strategy == "conservative_estimate"
    assert resolution.profile.source == "PixelFlow 内建保守档案"


def test_parse_model_context_profiles_accepts_existing_model_config() -> None:
    from deerflow.config.model_config import ModelConfig
    from pixelflow.agent_runtime.context.profiles import parse_model_context_profiles

    model = ModelConfig(
        name="configured-model",
        use="langchain_openai:ChatOpenAI",
        model="provider-model",
        context_profile=_verified_profile_data(),
    )

    profiles = parse_model_context_profiles([model])

    assert profiles["configured-model"].max_context_tokens == 256 * 1024


def test_parse_model_context_profiles_rejects_nested_model_name() -> None:
    from pixelflow.agent_runtime.context.profiles import parse_model_context_profiles

    with pytest.raises(ValueError, match="model_name"):
        parse_model_context_profiles(
            [
                {
                    "name": "actual-model",
                    "context_profile": _verified_profile_data_with(
                        model_name="spoofed-model",
                    ),
                },
            ],
        )


@pytest.mark.parametrize(
    "raw_models",
    [
        [
            {"name": "duplicate-model", "context_profile": _verified_profile_data()},
            {"name": "duplicate-model", "context_profile": _verified_profile_data()},
        ],
        [
            {
                "name": "invalid-output-model",
                "context_profile": _verified_profile_data_with(
                    max_output_tokens=256 * 1024,
                ),
            },
        ],
        [
            {
                "name": "naive-time-model",
                "context_profile": _verified_profile_data_with(
                    verified_at="2026-07-01T00:00:00",
                ),
            },
        ],
        [
            {
                "name": "invalid-expiry-model",
                "context_profile": _verified_profile_data_with(
                    expires_at="2026-06-01T00:00:00Z",
                ),
            },
        ],
        [
            {
                "name": "unknown-field-model",
                "context_profile": _verified_profile_data_with(
                    context_window_guess=999_999,
                ),
            },
        ],
        [{"name": "", "context_profile": _verified_profile_data()}],
        [{"name": "invalid-object-model", "context_profile": []}],
    ],
)
def test_parse_model_context_profiles_rejects_invalid_config(
    raw_models: list[dict[str, object]],
) -> None:
    from pixelflow.agent_runtime.context.profiles import parse_model_context_profiles

    with pytest.raises(ValueError):
        parse_model_context_profiles(raw_models)


def test_resolve_model_context_profile_rejects_naive_current_time() -> None:
    from pixelflow.agent_runtime.context.profiles import (
        parse_model_context_profiles,
        resolve_model_context_profile,
    )

    profiles = parse_model_context_profiles(
        [{"name": "target-model", "context_profile": _verified_profile_data()}],
    )

    with pytest.raises(ValueError, match="now 必须包含时区"):
        resolve_model_context_profile(
            "target-model",
            profiles,
            now=datetime(2026, 7, 24),
        )
