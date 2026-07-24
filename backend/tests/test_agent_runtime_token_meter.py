"""Agent Runtime TokenMeter 与可用输入预算测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def _profile(
    *,
    max_context_tokens: int = 512 * 1024,
    max_output_tokens: int = 64 * 1024,
):
    from pixelflow.agent_runtime.context.profiles import ModelContextProfile

    return ModelContextProfile(
        model_name="verified-model",
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        tokenizer_strategy="provider_usage",
        verified_at=datetime(2026, 7, 1, tzinfo=UTC),
        source="AIRouter 模型能力验证记录",
    )


@pytest.mark.parametrize(
    ("node", "context_cap", "output_reserve", "safety_reserve"),
    [
        ("supervisor", 256 * 1024, 8 * 1024, 32 * 1024),
        ("image", 256 * 1024, 16 * 1024, 32 * 1024),
        ("image_edit", 256 * 1024, 16 * 1024, 32 * 1024),
        ("video", 384 * 1024, 32 * 1024, 48 * 1024),
        ("ppt", 384 * 1024, 32 * 1024, 48 * 1024),
        ("video_analysis", 512 * 1024, 48 * 1024, 64 * 1024),
        ("summary", 384 * 1024, 24 * 1024, 48 * 1024),
    ],
)
def test_context_budget_policies_match_frozen_contract(
    node: str,
    context_cap: int,
    output_reserve: int,
    safety_reserve: int,
) -> None:
    from pixelflow.agent_runtime.context.token_meter import get_context_budget_policy

    policy = get_context_budget_policy(node)

    assert policy.effective_context_cap_tokens == context_cap
    assert policy.output_reserve_tokens == output_reserve
    assert policy.safety_reserve_tokens == safety_reserve


def test_token_meter_uses_verified_model_and_business_caps() -> None:
    from pixelflow.agent_runtime.context.token_meter import (
        TokenMeter,
        get_context_budget_policy,
    )

    report = TokenMeter().measure(
        estimated_input_tokens=120 * 1024,
        profile=_profile(),
        policy=get_context_budget_policy("video"),
    )

    assert report.effective_context_tokens == 384 * 1024
    assert report.max_output_tokens == 32 * 1024
    assert report.safety_reserve_tokens == 48 * 1024
    assert report.usable_input_tokens == 304 * 1024
    assert report.utilization == (120 * 1024) / (304 * 1024)
    assert report.compaction_level == 0


def test_token_meter_never_reserves_more_output_than_model_supports() -> None:
    from pixelflow.agent_runtime.context.token_meter import (
        TokenMeter,
        get_context_budget_policy,
    )

    report = TokenMeter().measure(
        estimated_input_tokens=1,
        profile=_profile(max_output_tokens=4 * 1024),
        policy=get_context_budget_policy("image"),
    )

    assert report.max_output_tokens == 4 * 1024
    assert report.usable_input_tokens == 220 * 1024


def test_token_meter_preserves_conservative_profile_limit() -> None:
    from pixelflow.agent_runtime.context.profiles import resolve_model_context_profile
    from pixelflow.agent_runtime.context.token_meter import (
        TokenMeter,
        get_context_budget_policy,
    )

    resolution = resolve_model_context_profile(
        "unknown-model",
        {},
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )
    report = TokenMeter().measure(
        estimated_input_tokens=1,
        profile=resolution.profile,
        policy=get_context_budget_policy("video_analysis"),
    )

    assert report.effective_context_tokens == 128 * 1024
    assert report.max_output_tokens == 8 * 1024
    assert report.safety_reserve_tokens == 64 * 1024
    assert report.usable_input_tokens == 56 * 1024


@pytest.mark.parametrize(
    ("percentage", "expected_level"),
    [
        (59, 0),
        (60, 1),
        (71, 1),
        (72, 2),
        (84, 2),
        (85, 3),
        (91, 3),
        (92, 4),
    ],
)
def test_token_meter_uses_exact_compaction_boundaries(
    percentage: int,
    expected_level: int,
) -> None:
    from pixelflow.agent_runtime.context.token_meter import (
        ContextBudgetPolicy,
        TokenMeter,
    )

    report = TokenMeter().measure(
        estimated_input_tokens=percentage,
        profile=_profile(max_context_tokens=200, max_output_tokens=50),
        policy=ContextBudgetPolicy(
            effective_context_cap_tokens=200,
            output_reserve_tokens=50,
            safety_reserve_tokens=50,
        ),
    )

    assert report.usable_input_tokens == 100
    assert report.utilization == percentage / 100
    assert report.compaction_level == expected_level


def test_token_meter_reports_overflow_without_hiding_it() -> None:
    from pixelflow.agent_runtime.context.token_meter import (
        ContextBudgetPolicy,
        TokenMeter,
    )

    report = TokenMeter().measure(
        estimated_input_tokens=125,
        profile=_profile(max_context_tokens=200, max_output_tokens=50),
        policy=ContextBudgetPolicy(
            effective_context_cap_tokens=200,
            output_reserve_tokens=50,
            safety_reserve_tokens=50,
        ),
    )

    assert report.utilization == 1.25
    assert report.compaction_level == 4


@pytest.mark.parametrize("estimated_input_tokens", [-1, 1.5, True])
def test_token_meter_rejects_invalid_input_estimates(
    estimated_input_tokens: object,
) -> None:
    from pixelflow.agent_runtime.context.token_meter import (
        TokenMeter,
        get_context_budget_policy,
    )

    with pytest.raises(ValueError, match="estimated_input_tokens"):
        TokenMeter().measure(
            estimated_input_tokens=estimated_input_tokens,
            profile=_profile(),
            policy=get_context_budget_policy("supervisor"),
        )


def test_token_meter_rejects_budget_without_usable_input() -> None:
    from pixelflow.agent_runtime.context.token_meter import (
        ContextBudgetPolicy,
        TokenMeter,
    )

    with pytest.raises(ValueError, match="usable_input"):
        TokenMeter().measure(
            estimated_input_tokens=0,
            profile=_profile(max_context_tokens=64, max_output_tokens=32),
            policy=ContextBudgetPolicy(
                effective_context_cap_tokens=64,
                output_reserve_tokens=32,
                safety_reserve_tokens=32,
            ),
        )


def test_unknown_context_budget_policy_is_rejected() -> None:
    from pixelflow.agent_runtime.context.token_meter import get_context_budget_policy

    with pytest.raises(ValueError, match="未知"):
        get_context_budget_policy("unknown")
