"""Agent Runtime 默认关闭与启动配置合同测试。"""

from __future__ import annotations

import os

import pytest

_ENV_KEYS = {
    "PIXELFLOW_AGENT_RUNTIME_MODE",
    "PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS",
    "PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT",
    "PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED",
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


def test_agent_runtime_accepts_explicit_approved_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_MODE", "primary")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_ENABLED_INTENTS", '["video", "image"]')
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_NEW_CONVERSATION_ROLLOUT_PERCENT", "100")
    monkeypatch.setenv("PIXELFLOW_AGENT_RUNTIME_CONTEXT_COMPACTION_ENABLED", "true")

    config = load_agent_runtime_config_from_env()

    assert config.mode == "primary"
    assert config.enabled_intents == ("video", "image")
    assert config.new_conversation_rollout_percent == 100
    assert config.context_compaction_enabled is True


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
    from pixelflow.agent_runtime.config import AgentRuntimeConfig

    for field_name, field in AgentRuntimeConfig.model_fields.items():
        description = field.description or ""
        assert "用途" in description, field_name
        assert "影响" in description, field_name


def test_agent_runtime_loader_does_not_mutate_environment() -> None:
    from pixelflow.agent_runtime.config import load_agent_runtime_config_from_env

    before = dict(os.environ)
    load_agent_runtime_config_from_env()
    assert dict(os.environ) == before
