from __future__ import annotations

import pytest

_JIANYING_ENV_NAMES = (
    "PIXELFLOW_JIANYING_DRAFT_ENABLED",
    "PIXELFLOW_JIANYING_DRAFT_BASE_URL",
    "PIXELFLOW_JIANYING_DRAFT_TOKEN",
    "PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS",
    "PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS",
    "PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES",
    "PIXELFLOW_JIANYING_DRAFT_CONNECT_TIMEOUT_SECONDS",
    "PIXELFLOW_JIANYING_DRAFT_CREATE_READ_TIMEOUT_SECONDS",
    "PIXELFLOW_JIANYING_DRAFT_QUERY_READ_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_jianying_environment(monkeypatch: pytest.MonkeyPatch):
    """隔离 profile loader 写入的进程级环境变量，保证测试不依赖执行顺序。"""

    for name in _JIANYING_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_jianying_draft_runtime_config_defaults_without_environment():
    from pixelflow.jianying_draft import load_jianying_draft_runtime_config

    config = load_jianying_draft_runtime_config()

    assert config.enabled is False
    assert config.base_url == ""
    assert config.token == ""
    assert config.poll_interval_seconds == 2.0
    assert config.timeout_seconds == 1800.0
    assert config.max_retries == 2
    assert config.connect_timeout_seconds == 5.0
    assert config.create_read_timeout_seconds == 30.0
    assert config.query_read_timeout_seconds == 15.0


def test_jianying_draft_runtime_config_reads_valid_environment(monkeypatch):
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_ENABLED", "true")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_BASE_URL", "https://provider.example.com")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TOKEN", "provider-token")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS", "1.5")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "2")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_CONNECT_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_CREATE_READ_TIMEOUT_SECONDS", "24")
    monkeypatch.setenv("PIXELFLOW_JIANYING_DRAFT_QUERY_READ_TIMEOUT_SECONDS", "12")

    from pixelflow.jianying_draft import load_jianying_draft_runtime_config

    config = load_jianying_draft_runtime_config()

    assert config.enabled is True
    assert config.base_url == "https://provider.example.com"
    assert config.token == "provider-token"
    assert config.poll_interval_seconds == 1.5
    assert config.timeout_seconds == 42.0
    assert config.max_retries == 2
    assert config.connect_timeout_seconds == 4.0
    assert config.create_read_timeout_seconds == 24.0
    assert config.query_read_timeout_seconds == 12.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PIXELFLOW_JIANYING_DRAFT_ENABLED", "sometimes"),
        ("PIXELFLOW_JIANYING_DRAFT_POLL_INTERVAL_SECONDS", "NaN"),
        ("PIXELFLOW_JIANYING_DRAFT_TIMEOUT_SECONDS", "inf"),
        ("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "-1"),
        ("PIXELFLOW_JIANYING_DRAFT_MAX_RETRIES", "not-a-number"),
        ("PIXELFLOW_JIANYING_DRAFT_CONNECT_TIMEOUT_SECONDS", "0"),
        ("PIXELFLOW_JIANYING_DRAFT_CREATE_READ_TIMEOUT_SECONDS", "NaN"),
        ("PIXELFLOW_JIANYING_DRAFT_QUERY_READ_TIMEOUT_SECONDS", "-1"),
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
    assert config.max_retries == 2
