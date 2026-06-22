"""Tests for PixelFlow profile YAML loading.

这里的 profile 配置类似 Spring Boot 的 ``application-dev.yml`` /
``application-prod.yml``：先选择一个环境文件，再把文件中的业务配置转换成
现有 Python 代码读取的环境变量。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_MAPPED_ENV_KEYS = {
    "BORGRISE_BASE_URL",
    "BORGRISE_IMAGE_POLL_TIMEOUT",
    "BORGRISE_MAX_RETRIES",
    "BORGRISE_PROJECT_ID",
    "BORGRISE_REMOTE_VERIFY_ENABLED",
    "BORGRISE_SKIP_SSL_VERIFY",
    "BORGRISE_VERIFY_TIMEOUT_SECONDS",
    "BORGRISE_VIDEO_ANALYSIS_POLL_TIMEOUT",
    "BORGRISE_VIDEO_POLL_TIMEOUT",
    "DEER_FLOW_CONFIG_PATH",
    "GATEWAY_CORS_ORIGINS",
    "GATEWAY_ENABLE_DOCS",
    "GATEWAY_HOST",
    "GATEWAY_PORT",
    "PIXELFLOW_CAPTION_FONT",
    "PIXELFLOW_DRAFT_ROOT",
    "PIXELFLOW_EDIT_SKILL",
    "PIXELFLOW_MEDIA_SKILL",
    "PIXELFLOW_MEM0_ENABLED",
    "PIXELFLOW_MYSQL_URL",
    "PIXELFLOW_RENDER_ROOT",
}


@pytest.fixture(autouse=True)
def _clean_profile_env(monkeypatch: pytest.MonkeyPatch):
    for key in _MAPPED_ENV_KEYS | {"PIXELFLOW_CONFIG_ENV", "PIXELFLOW_CONFIG_FILE", "CUSTOM_PROFILE_KEY"}:
        monkeypatch.delenv(key, raising=False)

    from app.gateway.profile_config import reset_profile_config_for_tests

    reset_profile_config_for_tests()
    yield
    reset_profile_config_for_tests()


def _write_minimal_profile(path: Path, *, port: int = 9001, docs: bool = True) -> None:
    path.write_text(
        f"""\
config_version: 1
log_level: info
gateway:
  host: 127.0.0.1
  port: {port}
  enable_docs: {str(docs).lower()}
  cors_origins: http://localhost:5273
pixelflow:
  mysql_url: mysql+asyncmy://user:pwd@localhost:3306/pixelflow
  mem0_enabled: false
  media_skill: borgrise
  edit_skill: ffmpeg
  draft_root: /tmp/pixelflow-drafts
  render_root: /tmp/pixelflow-renders
  caption_font: /tmp/font.ttf
borgrise:
  base_url: https://example.test/api
  remote_verify_enabled: true
  verify_timeout_seconds: 10
  project_id: "42"
  skip_ssl_verify: true
  video_poll_timeout: 3600
  image_poll_timeout: 600
  video_analysis_poll_timeout: 1200
  max_retries: 7
environment:
  variables:
    CUSTOM_PROFILE_KEY: custom-value
models:
  - name: fake-test-model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
database:
  backend: sqlite
""",
        encoding="utf-8",
    )


def test_explicit_config_file_loads_yaml_into_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.dev.yml"
    _write_minimal_profile(config_file, port=8123, docs=False)
    monkeypatch.setenv("PIXELFLOW_CONFIG_FILE", str(config_file))

    from app.gateway.profile_config import load_profile_config

    loaded = load_profile_config()

    assert loaded.path == config_file
    assert os.environ["GATEWAY_PORT"] == "8123"
    assert os.environ["GATEWAY_ENABLE_DOCS"] == "false"
    assert os.environ["GATEWAY_CORS_ORIGINS"] == "http://localhost:5273"
    assert os.environ["PIXELFLOW_MYSQL_URL"].startswith("mysql+asyncmy://")
    assert os.environ["PIXELFLOW_MEDIA_SKILL"] == "borgrise"
    assert os.environ["PIXELFLOW_EDIT_SKILL"] == "ffmpeg"
    assert "PIXELFLOW_VIDEO_SKILL" not in os.environ
    assert "PIXELFLOW_DECOMPOSE_SKILL" not in os.environ
    assert os.environ["BORGRISE_BASE_URL"] == "https://example.test/api"
    assert os.environ["BORGRISE_REMOTE_VERIFY_ENABLED"] == "true"
    assert os.environ["BORGRISE_VERIFY_TIMEOUT_SECONDS"] == "10"
    assert "CONTENT_APP_API_BASE_URL" not in os.environ
    assert "CONTENT_APP_REMOTE_VERIFY_ENABLED" not in os.environ
    assert "CONTENT_APP_VERIFY_TIMEOUT_SECONDS" not in os.environ
    assert os.environ["BORGRISE_PROJECT_ID"] == "42"
    assert os.environ["BORGRISE_SKIP_SSL_VERIFY"] == "true"
    assert os.environ["BORGRISE_VIDEO_POLL_TIMEOUT"] == "3600"
    assert os.environ["BORGRISE_IMAGE_POLL_TIMEOUT"] == "600"
    assert os.environ["BORGRISE_VIDEO_ANALYSIS_POLL_TIMEOUT"] == "1200"
    assert "BORGRISE_POLL_TIMEOUT" not in os.environ
    assert os.environ["CUSTOM_PROFILE_KEY"] == "custom-value"
    assert os.environ["DEER_FLOW_CONFIG_PATH"] == str(config_file)


def test_shell_environment_keeps_highest_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.dev.yml"
    _write_minimal_profile(config_file, port=8123, docs=True)
    monkeypatch.setenv("PIXELFLOW_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("GATEWAY_PORT", "9999")

    from app.gateway.profile_config import load_profile_config

    load_profile_config()

    assert os.environ["GATEWAY_PORT"] == "9999"


def test_missing_profile_file_raises_actionable_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PIXELFLOW_CONFIG_ENV", "missing-env")

    from app.gateway.profile_config import load_profile_config

    with pytest.raises(FileNotFoundError, match="config.missing-env.yml"):
        load_profile_config()


def test_profile_loader_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.dev.yml"
    _write_minimal_profile(config_file, port=8123)
    monkeypatch.setenv("PIXELFLOW_CONFIG_FILE", str(config_file))

    from app.gateway.profile_config import load_profile_config

    first = load_profile_config()
    second = load_profile_config()

    assert first is second
    assert os.environ["GATEWAY_PORT"] == "8123"


@pytest.mark.parametrize("profile", ["dev", "prod"])
def test_repository_profile_files_are_valid_deerflow_configs(profile: str, monkeypatch: pytest.MonkeyPatch):
    """仓库内置的 dev/prod profile 必须同时能作为 DeerFlow AppConfig 使用。"""
    monkeypatch.setenv("PIXELFLOW_CONFIG_ENV", profile)

    from app.gateway.profile_config import load_profile_config
    from deerflow.config.app_config import get_app_config, reset_app_config

    loaded = load_profile_config()
    reset_app_config()

    app_config = get_app_config()

    assert loaded.path.name == f"config.{profile}.yml"
    assert os.environ["DEER_FLOW_PROJECT_ROOT"] == str(loaded.path.parent)
    assert app_config.sandbox.use == "deerflow.sandbox.local:LocalSandboxProvider"
    assert app_config.database.backend == "sqlite"
