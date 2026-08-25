"""验证 M1 Profile 配置只映射 PixelFlow 自有运行参数。"""

from __future__ import annotations

import os

from app.gateway.profile_config import load_profile_config, reset_profile_config_for_tests


def test_dev_profile_maps_sidecar_database_and_memory_without_legacy_runtime(monkeypatch) -> None:
    """开发配置不得再设置 DeerFlow、LangGraph 或旧 Skill 存储环境变量。"""

    reset_profile_config_for_tests()
    monkeypatch.setenv("PIXELFLOW_CONFIG_ENV", "dev")
    try:
        load_profile_config()
        assert os.environ["PIXELFLOW_DATABASE_BACKEND"] == "sqlite"
        assert os.environ["PIXELFLOW_GATEWAY_INSTANCE_ID"] == "pixelflow-dev"
        assert os.environ["PIXELFLOW_LONG_TERM_MEMORY_SEARCH_LIMIT"] == "5"
        assert "DEER_FLOW_CONFIG_PATH" not in os.environ
    finally:
        reset_profile_config_for_tests()
