"""验证 Gateway 虚拟路径解析已改用 PixelFlow 自有路径服务。"""

from __future__ import annotations

import pytest

from app.gateway.path_utils import resolve_thread_virtual_path


def test_gateway_virtual_path_rejects_prefix_confusion(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Gateway 必须拒绝近似前缀和路径穿越，避免读取其他用户文件。"""

    monkeypatch.setenv("PIXELFLOW_RUNTIME_HOME", str(tmp_path))
    monkeypatch.setattr("app.gateway.path_utils.get_effective_user_id", lambda: "path-user")

    resolved = resolve_thread_virtual_path("thread-a", "/mnt/user-data/outputs/final.mp4")
    assert resolved == tmp_path / "users/path-user/threads/thread-a/user-data/outputs/final.mp4"

    with pytest.raises(Exception) as invalid:
        resolve_thread_virtual_path("thread-a", "/mnt/user-data-private/secret.txt")
    assert getattr(invalid.value, "status_code", None) == 400
