"""验证 M1 Profile 配置只映射 PixelFlow 自有运行参数。"""

from __future__ import annotations

import json
import os

import pytest

from app.gateway.profile_config import load_profile_config, reset_profile_config_for_tests
from pixelflow.agent_harness.limits import LimitProfileResolver


def test_dev_profile_maps_sidecar_database_and_memory_without_legacy_runtime(monkeypatch) -> None:
    """开发配置不得再设置 DeerFlow、LangGraph 或旧 Skill 存储环境变量。"""

    reset_profile_config_for_tests()
    monkeypatch.setenv("PIXELFLOW_CONFIG_ENV", "dev")
    try:
        load_profile_config()
        assert os.environ["PIXELFLOW_DATABASE_BACKEND"] == "sqlite"
        assert os.environ["PIXELFLOW_GATEWAY_INSTANCE_ID"] == "pixelflow-dev"
        assert os.environ["PIXELFLOW_LONG_TERM_MEMORY_SEARCH_LIMIT"] == "5"
        profiles = json.loads(os.environ["PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES"])
        assert profiles["video_interactive_v1"]["max_model_steps"] == 24
        assert LimitProfileResolver().resolve("confirmation_resume").profile == "confirmation_resume_v1"
        assert LimitProfileResolver().resolve("authorization_resume").profile == "confirmation_resume_v1"
        assert LimitProfileResolver().resolve("form_resume").profile == "confirmation_resume_v1"
        assert LimitProfileResolver().resolve("run_recovery").max_billable_batch_starts == 0
        assert "DEER_FLOW_CONFIG_PATH" not in os.environ
    finally:
        reset_profile_config_for_tests()


@pytest.mark.parametrize(
    ("profile", "expected_content_app_base_url"),
    [
        ("borgrise-test", "https://test-video.borgrise.com"),
        ("borgrise-prod", "https://video.borgrise.com"),
        ("ec-prod", "http://creator.vitamazing.top"),
    ],
)
def test_deployment_profiles_select_the_expected_content_app_endpoint(
    monkeypatch,
    profile: str,
    expected_content_app_base_url: str,
) -> None:
    """防止发布时把环境 Profile 误连到另一套 Content-App。"""

    reset_profile_config_for_tests()
    monkeypatch.setenv("PIXELFLOW_CONFIG_ENV", profile)
    monkeypatch.delenv("BORGRISE_BASE_URL", raising=False)
    try:
        load_profile_config()
        assert os.environ["BORGRISE_BASE_URL"] == expected_content_app_base_url
    finally:
        reset_profile_config_for_tests()
