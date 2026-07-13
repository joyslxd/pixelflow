"""Borgrise/content-app skill 必须透传当前请求的 Authorization。"""

from __future__ import annotations

from app.gateway.content_app_auth_context import reset_current_content_app_auth, set_current_content_app_auth
from pixelflow.skills.borgrise import run_generation


def test_get_headers_uses_current_request_authorization(monkeypatch):
    """生成接口请求头使用当前用户 token，不允许回退到静态 BORGRISE_API_TOKEN。"""
    monkeypatch.setenv("BORGRISE_API_TOKEN", "static-token-should-not-be-used")
    token = set_current_content_app_auth("Bearer user-token", username="alice")
    try:
        headers = run_generation.get_headers(model="seedance-2.0", bill_type=3, duration=5)
    finally:
        reset_current_content_app_auth(token)

    assert headers["Authorization"] == "Bearer user-token"


def test_make_request_fails_fast_without_request_authorization(monkeypatch):
    """没有用户 Authorization 时不发起扣费接口，直接返回清晰错误。"""
    monkeypatch.setenv("BORGRISE_API_TOKEN", "static-token-should-not-be-used")
    result = run_generation.make_request("/task/demo/status", method="GET")

    assert result["error"] is True
    assert "Authorization" in result["message"]
