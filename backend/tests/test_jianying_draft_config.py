from __future__ import annotations

import pytest


def test_jianying_draft_runtime_config_defaults_without_environment(monkeypatch):
    for name in (
        "PIXELFLOW_JIANYING_DRAFT_ENABLED",
        "PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS",
        "PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS",
        "PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)

    from pixelflow.jianying_draft import load_jianying_draft_runtime_config

    config = load_jianying_draft_runtime_config()

    assert config.enabled is False
    assert config.poll_interval_seconds == 2.0
    assert config.timeout_seconds == 1800.0
    assert config.max_retries == 3


def test_jianying_draft_runtime_config_reads_valid_environment(monkeypatch):
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS", "1.5")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "2")

    from pixelflow.jianying_draft import load_jianying_draft_runtime_config

    config = load_jianying_draft_runtime_config()

    assert config.enabled is True
    assert config.poll_interval_seconds == 1.5
    assert config.timeout_seconds == 42.0
    assert config.max_retries == 2


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PIXELFLOW_JIANYING_DRAFT_ENABLED", "sometimes"),
        ("PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS", "NaN"),
        ("PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS", "inf"),
        ("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "-1"),
        ("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "not-a-number"),
    ],
)
def test_jianying_draft_runtime_config_falls_back_for_invalid_environment(
    monkeypatch,
    name: str,
    value: str,
):
    monkeypatch.setenv(name, value)

    from pixelflow.jianying_draft import load_jianying_draft_runtime_config

    config = load_jianying_draft_runtime_config()

    assert config.enabled is False
    assert config.poll_interval_seconds == 2.0
    assert config.timeout_seconds == 1800.0
    assert config.max_retries == 3
