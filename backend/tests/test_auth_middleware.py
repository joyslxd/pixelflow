"""Tests for the global AuthMiddleware (fail-closed safety net)."""

import pytest
from starlette.testclient import TestClient

from app.gateway.auth_middleware import AuthMiddleware, _is_public

# ── _is_public unit tests ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/health/",
        "/agent/docs",
        "/agent/docs/",
        "/agent/redoc",
        "/agent/openapi.json",
    ],
)
def test_public_paths(path: str):
    assert _is_public(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/agent/models",
        "/agent/mcp/config",
        "/agent/memory",
        "/agent/skills",
        "/agent/threads/123",
        "/agent/threads/123/uploads",
        "/agent/agents",
        "/agent/channels",
        "/agent/runs/stream",
        "/agent/threads/123/runs",
        "/agent/auth/me",
        "/agent/auth/login/local",
        "/agent/auth/register",
        "/agent/auth/logout",
        "/agent/auth/setup-status",
    ],
)
def test_protected_paths(path: str):
    assert _is_public(path) is False


# ── Trailing slash / normalization edge cases ─────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/agent/auth/login/local/",
        "/agent/auth/register/",
        "/agent/auth/logout/",
        "/agent/auth/setup-status/",
    ],
)
def test_auth_paths_with_trailing_slash_are_protected(path: str):
    assert _is_public(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "/agent/models/",
        "/agent/auth/me/",
        "/agent/auth/change-password/",
    ],
)
def test_protected_paths_with_trailing_slash(path: str):
    assert _is_public(path) is False


def test_unknown_api_path_is_protected():
    """Fail-closed: any new /agent/* path is protected by default."""
    assert _is_public("/agent/new-feature") is False
    assert _is_public("/agent/v2/something") is False
    assert _is_public("/agent/auth/new-endpoint") is False


# ── Middleware integration tests ──────────────────────────────────────────


def _make_app():
    """Create a minimal FastAPI app with AuthMiddleware for testing."""
    from fastapi import FastAPI

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/agent/auth/me")
    async def auth_me():
        return {"id": "1", "email": "test@test.com"}

    @app.get("/agent/auth/setup-status")
    async def setup_status():
        return {"needs_setup": False}

    @app.get("/agent/models")
    async def models_get():
        return {"models": []}

    @app.put("/agent/mcp/config")
    async def mcp_put():
        return {"ok": True}

    @app.delete("/agent/threads/abc")
    async def thread_delete():
        return {"ok": True}

    @app.patch("/agent/threads/abc")
    async def thread_patch():
        return {"ok": True}

    @app.post("/agent/threads/abc/runs/stream")
    async def stream():
        return {"ok": True}

    @app.get("/agent/future-endpoint")
    async def future():
        return {"ok": True}

    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


def test_public_path_no_cookie(client):
    res = client.get("/health")
    assert res.status_code == 200


def test_removed_setup_status_path_no_authorization(client):
    """旧本地初始化接口不再公开，必须按普通 /agent 路径鉴权。"""
    res = client.get("/agent/auth/setup-status")
    assert res.status_code == 401


def test_protected_auth_path_no_cookie(client):
    """/auth/me requires Authorization even though it's under /agent/auth/."""
    res = client.get("/agent/auth/me")
    assert res.status_code == 401


def test_protected_path_no_cookie_returns_401(client):
    res = client.get("/agent/models")
    assert res.status_code == 401
    body = res.json()
    assert body["detail"]["code"] == "not_authenticated"


def test_protected_path_with_junk_cookie_rejected(client):
    """Junk cookie → 401. Middleware strictly validates the JWT now
    (AUTH_TEST_PLAN test 7.5.8); it no longer silently passes bad
    tokens through to the route handler."""
    res = client.get("/agent/models", cookies={"access_token": "some-token"})
    assert res.status_code == 401


def test_protected_post_no_cookie_returns_401(client):
    res = client.post("/agent/threads/abc/runs/stream")
    assert res.status_code == 401


# ── Method matrix: PUT/DELETE/PATCH also protected ────────────────────────


def test_protected_put_no_cookie(client):
    res = client.put("/agent/mcp/config")
    assert res.status_code == 401


def test_protected_delete_no_cookie(client):
    res = client.delete("/agent/threads/abc")
    assert res.status_code == 401


def test_protected_patch_no_cookie(client):
    res = client.patch("/agent/threads/abc")
    assert res.status_code == 401


def test_put_with_junk_cookie_rejected(client):
    """Junk cookie on PUT → 401 (strict JWT validation in middleware)."""
    client.cookies.set("access_token", "tok")
    res = client.put("/agent/mcp/config")
    assert res.status_code == 401


def test_delete_with_junk_cookie_rejected(client):
    """Junk cookie on DELETE → 401 (strict JWT validation in middleware)."""
    client.cookies.set("access_token", "tok")
    res = client.delete("/agent/threads/abc")
    assert res.status_code == 401


# ── Fail-closed: unknown future endpoints ─────────────────────────────────


def test_unknown_endpoint_no_cookie_returns_401(client):
    """Any new /agent/* endpoint is blocked by default without cookie."""
    res = client.get("/agent/future-endpoint")
    assert res.status_code == 401


def test_unknown_endpoint_with_junk_cookie_rejected(client):
    """New endpoints are also protected by strict JWT validation."""
    client.cookies.set("access_token", "tok")
    res = client.get("/agent/future-endpoint")
    assert res.status_code == 401
