"""content-app Authorization 鉴权适配层测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from app.gateway.content_app_auth import ContentAppAuthConfig, ContentAppAuthError, authenticate_authorization_header, get_content_app_auth_config, verify_authorization_header_remote

TEST_SECRET = "volcengine-secret-key-256-bits-minimum-required-for-jwt-security"


def _token(username: str = "java_dev", *, secret: str = TEST_SECRET, expired: bool = False) -> str:
    """生成和 content-app JwtUtil 结构一致的测试 token。"""
    now = datetime.now(UTC)
    exp = now - timedelta(minutes=1) if expired else now + timedelta(minutes=30)
    return jwt.encode({"sub": username, "iat": now, "exp": exp}, secret, algorithm="HS512")


@pytest.mark.asyncio
async def test_authenticate_authorization_header_parses_username_and_calls_remote_verify():
    """从 JWT payload 读取 username，同时远程调用 content-app 确认 token 真实可用。"""
    calls: list[str] = []
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        urls.append(str(request.url))
        return httpx.Response(200, json={"valid": True, "message": "Token有效"})

    auth_header = f"Bearer {_token('alice')}"
    user = await authenticate_authorization_header(
        auth_header,
        config=ContentAppAuthConfig(
            base_url="https://content-app.test/",
            remote_verify_enabled=True,
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert user.id == "alice"
    assert user.username == "alice"
    assert calls == [auth_header]
    assert urls == ["https://content-app.test/api/auth/verify"]


@pytest.mark.asyncio
async def test_remote_verify_accepts_borgrise_base_url_with_or_without_api_suffix():
    """borgrise.base_url 既支持站点根地址，也兼容直接写到 /api 的配置。"""
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"valid": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await verify_authorization_header_remote(
        "Bearer user-token",
        config=ContentAppAuthConfig(base_url="https://test-video.borgrise.com/", remote_verify_enabled=True),
        http_client=client,
    )
    await verify_authorization_header_remote(
        "Bearer user-token",
        config=ContentAppAuthConfig(base_url="https://test-video.borgrise.com/api", remote_verify_enabled=True),
        http_client=client,
    )

    assert urls == [
        "https://test-video.borgrise.com/api/auth/verify",
        "https://test-video.borgrise.com/api/auth/verify",
    ]


@pytest.mark.asyncio
async def test_authenticate_authorization_header_rejects_disabled_user_from_remote_verify():
    """content-app 返回 USER_DISABLED 时，pixelflow 必须立刻拒绝访问。"""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"success": False, "message": "该账户已被禁用", "code": "USER_DISABLED"})

    with pytest.raises(ContentAppAuthError) as excinfo:
        await authenticate_authorization_header(
            f"Bearer {_token('disabled_user')}",
            config=ContentAppAuthConfig(
                base_url="https://content-app.test",
                remote_verify_enabled=True,
            ),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "user_disabled"


@pytest.mark.asyncio
async def test_authenticate_authorization_header_rejects_missing_and_expired_token():
    """缺少 Bearer 或 token 自带 exp 已过期时，不需要打到 content-app 就直接拒绝。"""
    config = ContentAppAuthConfig(remote_verify_enabled=False)

    with pytest.raises(ContentAppAuthError) as missing:
        await authenticate_authorization_header(None, config=config)
    assert missing.value.status_code == 401
    assert missing.value.code == "not_authenticated"

    with pytest.raises(ContentAppAuthError) as expired:
        await authenticate_authorization_header(f"Bearer {_token(expired=True)}", config=config)
    assert expired.value.status_code == 403
    assert expired.value.code == "token_expired"


def test_auth_config_reads_borgrise_environment(monkeypatch: pytest.MonkeyPatch):
    """登录校验复用 borgrise.base_url，不再需要独立 content_app 配置段。"""
    monkeypatch.setenv("BORGRISE_BASE_URL", "https://test-video.borgrise.com/api")
    monkeypatch.setenv("BORGRISE_REMOTE_VERIFY_ENABLED", "false")
    monkeypatch.setenv("BORGRISE_VERIFY_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("CONTENT_APP_API_BASE_URL", "https://old-content-app.example/")
    monkeypatch.setenv("CONTENT_APP_REMOTE_VERIFY_ENABLED", "true")
    monkeypatch.setenv("CONTENT_APP_VERIFY_TIMEOUT_SECONDS", "99")

    config = get_content_app_auth_config()

    assert config.base_url == "https://test-video.borgrise.com/api"
    assert config.auth_verify_endpoint == "https://test-video.borgrise.com/api/auth/verify"
    assert config.remote_verify_enabled is False
    assert config.verify_timeout_seconds == 12


def test_auth_config_default_verify_timeout_is_short_for_login_check(monkeypatch: pytest.MonkeyPatch):
    """登录态实时校验只是短 HTTP 校验，默认 10 秒即可，不能套用生成轮询超时。"""
    monkeypatch.delenv("BORGRISE_VERIFY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CONTENT_APP_VERIFY_TIMEOUT_SECONDS", raising=False)

    config = get_content_app_auth_config()

    assert config.verify_timeout_seconds == 10
