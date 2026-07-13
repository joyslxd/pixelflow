"""content-app Authorization 鉴权适配。

这个模块相当于 Java/Spring 项目里的 ``AuthenticationFilter`` 的 Python 版本，
但它不负责登录、不签发 token，只负责识别 content-app 已经签发的
``Authorization: Bearer <jwt>``。

校验分两步：

1. 本地只读取 JWT payload 中的 ``sub`` 用户名；不在 pixelflow 保存 content-app
   的签名密钥，也不在本地判断 token 真伪。
2. 远程实时校验：调用 content-app 的 ``/api/auth/verify``，让 token 签名、
   过期、用户禁用这类服务端状态立即对 pixelflow 生效。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

DEFAULT_BORGRISE_BASE_URL = "https://test-video.borgrise.com/api"
CONTENT_APP_AUTHORIZATION_HEADER = "Authorization"


@dataclass(frozen=True)
class ContentAppUser:
    """pixelflow 内部使用的当前用户模型。

    content-app 的 token 里稳定携带的是 username，而不是 pixelflow 旧体系里的
    UUID/email。因此这里用 username 同时作为 ``id`` 和 ``username``：

    - ``id`` 给 DeerFlow/Persistence 做 owner 隔离。
    - ``username`` 给业务代码、日志和后续调用说明使用。
    """

    id: str
    username: str
    system_role: str = "user"


@dataclass(frozen=True)
class ContentAppAuthConfig:
    """content-app 鉴权配置。

    当前 content-app 和 Borgrise 媒体生成服务共用同一个站点/API 根地址，所以登录
    校验复用 ``borgrise.base_url``。这里仍叫 ContentAppAuthConfig，是因为它负责
    的业务语义是“校验 content-app 登录态”，不是生成视频。

    ``remote_verify_enabled`` 默认开启，因为用户被禁用后需要立即阻断 pixelflow。
    本地开发如果暂时没启动 content-app，可以在配置文件里显式关掉它做离线调试。
    """

    base_url: str = DEFAULT_BORGRISE_BASE_URL
    remote_verify_enabled: bool = True
    verify_timeout_seconds: float = 10.0
    skip_ssl_verify: bool = False

    @property
    def auth_verify_endpoint(self) -> str:
        """返回 content-app token 校验接口地址。

        ``borgrise.base_url`` 通常写到 ``/api``，例如
        ``https://test-video.borgrise.com/api``。为了兼容本地调试直接写站点根地址，
        如果传入值没有以 ``/api`` 结尾，会自动追加。
        """
        base_url = self.base_url.strip().rstrip("/") or DEFAULT_BORGRISE_BASE_URL.rstrip("/")
        if not base_url.endswith("/api"):
            base_url = f"{base_url}/api"
        return f"{base_url}/auth/verify"

    @property
    def user_me_endpoint(self) -> str:
        """返回 content-app 当前用户信息接口地址（含 roles），拼接规则同上。"""
        base_url = self.base_url.strip().rstrip("/") or DEFAULT_BORGRISE_BASE_URL.rstrip("/")
        if not base_url.endswith("/api"):
            base_url = f"{base_url}/api"
        return f"{base_url}/user/me"


class ContentAppAuthError(Exception):
    """可被网关边界转换成 HTTP 响应的鉴权错误。"""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _bool_env(value: str | None, *, default: bool) -> bool:
    """解析配置中的布尔值，兼容 true/false、1/0、yes/no。"""
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_content_app_auth_config() -> ContentAppAuthConfig:
    """从环境变量读取 content-app 鉴权配置。

    这些环境变量由 ``profile_config.py`` 从 ``config.dev.yml`` /
    ``config.prod.yml`` 写入，保留环境变量入口是为了兼容测试和临时命令行覆盖。
    """
    base_url = os.getenv("BORGRISE_BASE_URL", DEFAULT_BORGRISE_BASE_URL).strip() or DEFAULT_BORGRISE_BASE_URL
    verify_timeout_raw = os.getenv("BORGRISE_VERIFY_TIMEOUT_SECONDS", "10").strip()
    try:
        verify_timeout_seconds = float(verify_timeout_raw)
    except ValueError:
        logger.warning("BORGRISE_VERIFY_TIMEOUT_SECONDS=%r 非法，回退到 10 秒", verify_timeout_raw)
        verify_timeout_seconds = 10.0
    return ContentAppAuthConfig(
        base_url=base_url,
        remote_verify_enabled=_bool_env(os.getenv("BORGRISE_REMOTE_VERIFY_ENABLED"), default=True),
        verify_timeout_seconds=verify_timeout_seconds,
        skip_ssl_verify=_bool_env(os.getenv("BORGRISE_SKIP_SSL_VERIFY"), default=False),
    )


def _extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization header 中提取 JWT；格式不对时抛业务化错误。"""
    if not authorization:
        raise ContentAppAuthError(status_code=401, code="not_authenticated", message="缺少 Authorization 请求头")
    authorization = authorization.strip()
    if not authorization.startswith("Bearer "):
        raise ContentAppAuthError(status_code=401, code="not_authenticated", message="Authorization 必须使用 Bearer token")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise ContentAppAuthError(status_code=401, code="not_authenticated", message="Authorization 中没有 token")
    return token


def _read_username_from_unverified_jwt(token: str) -> str:
    """只读取 JWT payload 里的 subject(username)，不在本地验证签名。

    content-app 当前 ``/api/auth/verify`` 只返回 ``valid``，不返回用户名；pixelflow
    又需要用户名做任务隔离，所以这里先读取 ``sub``。安全性不依赖这一步：
    token 真伪、过期、用户禁用都由后续 content-app 远程校验决定。
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError as exc:
        raise ContentAppAuthError(status_code=401, code="token_invalid", message="无效的认证令牌") from exc

    username = claims.get("sub")
    if not isinstance(username, str) or not username.strip():
        raise ContentAppAuthError(status_code=401, code="token_invalid", message="认证令牌中缺少用户名")
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        raise ContentAppAuthError(status_code=403, code="token_expired", message="登录已过期，请重新登录")
    return username.strip()


def _content_app_error_code(payload: Any) -> str | None:
    """从 content-app 错误响应中提取 code，兼容 Map/ApiResponse 两种形态。"""
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if isinstance(code, str):
        return code
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("code"), str):
        return data["code"]
    return None


def _map_remote_rejection(status_code: int, payload: Any) -> ContentAppAuthError:
    """把 content-app 的拒绝响应映射成 pixelflow 统一错误。"""
    remote_code = (_content_app_error_code(payload) or "").upper()
    if remote_code == "USER_DISABLED":
        return ContentAppAuthError(status_code=403, code="user_disabled", message="该账户已被禁用，请联系管理员")
    if remote_code == "TOKEN_EXPIRED":
        return ContentAppAuthError(status_code=403, code="token_expired", message="登录已过期，请重新登录")
    return ContentAppAuthError(status_code=401 if status_code < 500 else 503, code="token_invalid", message="content-app 拒绝了当前认证令牌")


async def verify_authorization_header_remote(
    authorization: str,
    *,
    config: ContentAppAuthConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """调用 content-app `/api/auth/verify` 做实时校验。

    这个函数只做远程校验，不重复解析 JWT；SSE 长连接可以周期性调用它，让禁用用户
    不用等到下次普通 HTTP 请求才失效。
    """
    config = config or get_content_app_auth_config()
    if not config.remote_verify_enabled:
        return

    async def _post(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            config.auth_verify_endpoint,
            headers={CONTENT_APP_AUTHORIZATION_HEADER: authorization},
        )

    last_error: httpx.HTTPError | None = None
    for attempt in range(2):
        try:
            if http_client is not None:
                response = await _post(http_client)
            else:
                async with httpx.AsyncClient(
                    timeout=config.verify_timeout_seconds,
                    verify=not config.skip_ssl_verify,
                ) as client:
                    response = await _post(client)
            last_error = None
            break
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 0:
                logger.warning("content-app auth verify failed (attempt %s), retrying: %s", attempt + 1, exc)
                continue
    if last_error is not None:
        logger.error("content-app auth verify unavailable: %s", last_error)
        raise ContentAppAuthError(status_code=503, code="auth_service_unavailable", message="content-app 认证服务暂不可用") from last_error

    try:
        payload: Any = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 500:
        raise ContentAppAuthError(status_code=503, code="auth_service_unavailable", message="content-app 认证服务暂不可用")
    if response.status_code >= 400:
        raise _map_remote_rejection(response.status_code, payload)

    valid = False
    if isinstance(payload, dict):
        valid = payload.get("valid") is True
        data = payload.get("data")
        if isinstance(data, dict):
            valid = valid or data.get("valid") is True

    if not valid:
        raise ContentAppAuthError(status_code=401, code="token_invalid", message="content-app 认为当前认证令牌无效")


async def is_admin_user(
    authorization: str | None,
    *,
    config: ContentAppAuthConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """调用 content-app ``GET /api/user/me``，判断当前用户是否带 ``ROLE_ADMIN``。

    只给内部调试类接口（例如对话 trace 查看）做权限门禁使用；不缓存结果，
    每次请求都实时问 content-app，跟 ``verify_authorization_header_remote``
    的“禁用立即生效”原则保持一致。查询失败一律当作非管理员处理（fail closed）。
    """
    if not authorization:
        return False
    config = config or get_content_app_auth_config()

    async def _get(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(
            config.user_me_endpoint,
            headers={CONTENT_APP_AUTHORIZATION_HEADER: authorization},
        )

    try:
        if http_client is not None:
            response = await _get(http_client)
        else:
            async with httpx.AsyncClient(
                timeout=config.verify_timeout_seconds,
                verify=not config.skip_ssl_verify,
            ) as client:
                response = await _get(client)
    except httpx.HTTPError:
        logger.warning("content-app /user/me unavailable while checking admin role", exc_info=True)
        return False

    if response.status_code >= 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False

    user = payload.get("user") if isinstance(payload, dict) else None
    roles = user.get("roles") if isinstance(user, dict) else None
    if not isinstance(roles, list):
        return False
    return any(isinstance(role, dict) and role.get("name") == "ROLE_ADMIN" for role in roles)


async def authenticate_authorization_header(
    authorization: str | None,
    *,
    config: ContentAppAuthConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ContentAppUser:
    """校验 Authorization，并返回 pixelflow 当前用户。"""
    config = config or get_content_app_auth_config()
    token = _extract_bearer_token(authorization)
    username = _read_username_from_unverified_jwt(token)
    await verify_authorization_header_remote(authorization or "", config=config, http_client=http_client)
    return ContentAppUser(id=username, username=username)
