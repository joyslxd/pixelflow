"""全局 AuthMiddleware 与 content-app Authorization 的集成测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.gateway.auth_middleware import AuthMiddleware, _is_public
from app.gateway.content_app_auth import ContentAppAuthError, ContentAppUser


def _make_app(monkeypatch: pytest.MonkeyPatch, *, disabled: bool = False) -> FastAPI:
    """创建只挂 AuthMiddleware 的最小测试应用。"""

    async def fake_authenticate(authorization: str | None):
        if disabled:
            raise ContentAppAuthError(status_code=403, code="user_disabled", message="该账户已被禁用")
        assert authorization == "Bearer user-token"
        return ContentAppUser(id="alice", username="alice")

    monkeypatch.setattr("app.gateway.deps.authenticate_authorization_header", fake_authenticate)

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/agent/models")
    async def models(request: Request):
        user = request.state.user
        auth = request.state.auth
        return {
            "user_id": user.id,
            "username": user.username,
            "authenticated": auth.is_authenticated,
        }

    return app


@pytest.mark.parametrize("path", ["/health", "/agent/docs", "/agent/openapi.json"])
def test_public_paths_do_not_require_authorization(path: str):
    assert _is_public(path) is True


@pytest.mark.parametrize("path", ["/agent/models", "/agent/flows", "/agent/auth/me", "/agent/flows/demo/events"])
def test_agent_paths_require_authorization(path: str):
    assert _is_public(path) is False


def test_protected_path_requires_authorization_header(monkeypatch: pytest.MonkeyPatch):
    client = TestClient(_make_app(monkeypatch))
    res = client.get("/agent/models")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "not_authenticated"


def test_protected_path_accepts_content_app_authorization(monkeypatch: pytest.MonkeyPatch):
    client = TestClient(_make_app(monkeypatch))
    res = client.get("/agent/models", headers={"Authorization": "Bearer user-token"})
    assert res.status_code == 200
    assert res.json() == {"user_id": "alice", "username": "alice", "authenticated": True}


def test_cookie_no_longer_authenticates_request(monkeypatch: pytest.MonkeyPatch):
    client = TestClient(_make_app(monkeypatch))
    res = client.get("/agent/models", cookies={"access_token": "old-pixelflow-cookie"})
    assert res.status_code == 401


def test_disabled_content_app_user_is_rejected(monkeypatch: pytest.MonkeyPatch):
    client = TestClient(_make_app(monkeypatch, disabled=True))
    res = client.get("/agent/models", headers={"Authorization": "Bearer user-token"})
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "user_disabled"
