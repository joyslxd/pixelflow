"""验证当前 PixelFlow Gateway 的运行时边界。"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    """读取当前仓库仍交付的运行时文件。"""
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_backend_container_only_exposes_gateway_port():
    """后端镜像只暴露统一 Gateway 端口，不恢复独立 LangGraph 服务端口。"""
    dockerfile = _read("backend/Dockerfile")

    assert not re.search(r"^EXPOSE\s+.*\b2024\b", dockerfile, re.M)
    assert "langgraph: 2024" not in dockerfile
    assert re.search(r"^EXPOSE\s+8001\b", dockerfile, re.M)


def test_gateway_cors_configuration_uses_gateway_allowlist():
    """浏览器跨域名单由 Gateway 配置统一管理。"""
    gateway_config = _read("backend/app/gateway/config.py")
    gateway_app = _read("backend/app/gateway/app.py")
    csrf_middleware = _read("backend/app/gateway/csrf_middleware.py")

    assert not re.search(r"(?<!GATEWAY_)[\"']CORS_ORIGINS[\"']", gateway_config)
    assert "cors_origins" not in gateway_config
    assert "get_configured_cors_origins" in gateway_app
    assert "GATEWAY_CORS_ORIGINS" in csrf_middleware
