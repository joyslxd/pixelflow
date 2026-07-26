"""Agent Runtime 默认关闭与启动配置合同测试。"""

from __future__ import annotations

import os

import pytest

_ENV_KEYS = {
    "PIXELFLOW_AGENT_RUNTIME_MODE",
    "PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS",
    "PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT",
    "PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED",
    "PIXELFLOW_AGENT_RUNTIME_CONTEXT_EFFECTIVE_K",
    "PIXELFLOW_AGENT_RUNTIME_CONTEXT_OUTPUT_RESERVE_K",
    "PIXELFLOW_AGENT_RUNTIME_CONTEXT_SAFETY_RESERVE_K",
    "PIXELFLOW_AGENT_RUNTIME_CONTEXT_REQUIRE_VERIFIED_MODEL_PROFILE",
    "PIXELFLOW_AGENT_RUNTIME_COMPACTION_RETRY_BACKOFF_SECONDS",
}


@pytest.fixture(autouse=True)
def _clean_agent_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_agent_runtime_defaults_are_fully_disabled() -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    config = load_agent_runtime_config_from_env()

    assert config.mode == "off"
    assert config.enabled_intents == ()
    assert config.new_conversation_rollout_percent == 0
    assert config.context_compaction_enabled is False
    assert config.context_budget.effective_context_tokens == 896 * 1024
    assert config.context_budget.output_reserve_tokens == 32 * 1024
    assert config.context_budget.safety_reserve_tokens == 32 * 1024
    assert config.context_budget.usable_input_tokens == 832 * 1024
    assert config.context_budget.require_verified_model_profile is True
    assert config.compaction_retry_backoff_seconds == 30


def test_agent_runtime_accepts_explicit_approved_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_MODE", "primary")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS", '["video", "image"]')
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT", "100")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_EFFECTIVE_K", "900")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_OUTPUT_RESERVE_K", "40")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_SAFETY_RESERVE_K", "20")
    monkeypatch.setenv(
        "PIXELFLOW_AGENT_RUNTIME_CONTEXT_REQUIRE_VERIFIED_MODEL_PROFILE",
        "false",
    )
    monkeypatch.setenv(
        "PIXELFLOW_AGENT_RUNTIME_COMPACTION_RETRY_BACKOFF_SECONDS",
        "45",
    )

    config = load_agent_runtime_config_from_env()

    assert config.mode == "primary"
    assert config.enabled_intents == ("video", "image")
    assert config.new_conversation_rollout_percent == 100
    assert config.context_compaction_enabled is True
    assert config.context_budget.effective_context_tokens == 900 * 1024
    assert config.context_budget.output_reserve_tokens == 40 * 1024
    assert config.context_budget.safety_reserve_tokens == 20 * 1024
    assert config.context_budget.usable_input_tokens == 840 * 1024
    assert config.context_budget.require_verified_model_profile is False
    assert config.compaction_retry_backoff_seconds == 45


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PIXELFLOW_AGENT_RUNTIME_MODE", "automatic"),
        ("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS", '["unknown"]'),
        ("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS", "["),
        ("PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT", "-1"),
        ("PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT", "101"),
        ("PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT", "1.0"),
        ("PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT", "all"),
        ("PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED", "sometimes"),
        ("PIXELFLOW_AGENT_RUNTIME_CONTEXT_EFFECTIVE_K", "0"),
        ("PIXELFLOW_AGENT_RUNTIME_CONTEXT_OUTPUT_RESERVE_K", "-1"),
        ("PIXELFLOW_AGENT_RUNTIME_CONTEXT_SAFETY_RESERVE_K", "1.5"),
        (
            "PIXELFLOW_AGENT_RUNTIME_CONTEXT_REQUIRE_VERIFIED_MODEL_PROFILE",
            "sometimes",
        ),
        ("PIXELFLOW_AGENT_RUNTIME_COMPACTION_RETRY_BACKOFF_SECONDS", "0"),
    ],
)
def test_agent_runtime_rejects_invalid_startup_values(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match="Agent Runtime 配置无效"):
        load_agent_runtime_config_from_env()


def test_agent_runtime_rejects_explicit_empty_intent_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS", "")

    with pytest.raises(ValueError, match="Agent Runtime 配置无效"):
        load_agent_runtime_config_from_env()


def test_agent_runtime_fields_have_chinese_usage_and_impact_descriptions() -> None:
    from pixelflow.agent_runtime.config import (
        AgentRuntimeConfig,
        ContextBudgetConfig,
    )

    for model in (AgentRuntimeConfig, ContextBudgetConfig):
        for field_name, field in model.model_fields.items():
            description = field.description or ""
            assert "用途" in description, field_name
            assert "影响" in description, field_name


def test_agent_runtime_rejects_reserves_that_exhaust_effective_context() -> None:
    from pixelflow.agent_runtime.config import ContextBudgetConfig

    with pytest.raises(ValueError, match="可用输入"):
        ContextBudgetConfig(
            effective_context_k=64,
            output_reserve_k=32,
            safety_reserve_k=32,
        )


def test_agent_runtime_loader_does_not_mutate_environment() -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    before = dict(os.environ)
    load_agent_runtime_config_from_env()
    assert dict(os.environ) == before
