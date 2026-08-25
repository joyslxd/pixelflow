"""验证 M1 平台配置与路径基础设施不依赖 DeerFlow。"""

from __future__ import annotations

import pytest

from pixelflow.platform import HarnessSidecarSettings, PixelFlowPaths


def test_harness_sidecar_settings_requires_complete_service_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少任一服务身份配置时不启用 Sidecar，避免退回旧内核。"""

    monkeypatch.delenv("PIXELFLOW_HARNESS_SIDECAR_BASE_URL", raising=False)
    monkeypatch.delenv("PIXELFLOW_GATEWAY_JWT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("PIXELFLOW_GATEWAY_INSTANCE_ID", raising=False)
    assert HarnessSidecarSettings.from_env() is None


def test_harness_sidecar_settings_validates_url_key_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """平台配置必须拒绝不安全地址、短密钥和非法超时。"""

    monkeypatch.setenv("PIXELFLOW_HARNESS_SIDECAR_BASE_URL", "https://sidecar.internal/")
    monkeypatch.setenv("PIXELFLOW_GATEWAY_JWT_SIGNING_KEY", "k" * 32)
    monkeypatch.setenv("PIXELFLOW_GATEWAY_INSTANCE_ID", "gateway-a")
    monkeypatch.setenv("PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS", "12")
    assert HarnessSidecarSettings.from_env().request_timeout_seconds == 12

    monkeypatch.setenv("PIXELFLOW_HARNESS_SIDECAR_BASE_URL", "http://harness-sidecar:8090")
    monkeypatch.setenv("PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS", "12")
    assert HarnessSidecarSettings.from_env().base_url == "http://harness-sidecar:8090"

    monkeypatch.setenv("PIXELFLOW_HARNESS_REQUEST_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="超时"):
        HarnessSidecarSettings.from_env()


def test_pixel_flow_paths_enforces_owner_isolation(tmp_path) -> None:
    """路径服务必须把虚拟路径限定在当前用户和对话目录内。"""

    paths = PixelFlowPaths(tmp_path)
    resolved = paths.resolve_thread_virtual_path(
        user_id="user_a",
        thread_id="thread_1",
        virtual_path="/mnt/user-data/outputs/video.mp4",
    )
    assert resolved == tmp_path / "users/user_a/threads/thread_1/user-data/outputs/video.mp4"

    with pytest.raises(ValueError):
        paths.resolve_thread_virtual_path(
            user_id="user_a",
            thread_id="thread_1",
            virtual_path="/mnt/user-data/../other-user/private.txt",
        )
