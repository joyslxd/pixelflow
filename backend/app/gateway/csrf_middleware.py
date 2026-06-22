"""FastAPI 的 CSRF 防护中间件。

按 RFC-001：会改变状态的请求都需要 CSRF 防护。
"""

import os
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_LENGTH = 64  # 字节


def is_secure_request(request: Request) -> bool:
    """判断原始客户端请求是否通过 HTTPS 发起。"""
    return _request_scheme(request) == "https"


def generate_csrf_token() -> str:
    """生成安全随机 CSRF token。"""
    return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)


def should_check_csrf(request: Request) -> bool:
    """判断请求是否需要 CSRF 校验。

    只对会改变状态的方法校验：POST、PUT、DELETE、PATCH。GET、HEAD、OPTIONS、
    TRACE 按 RFC 7231 豁免。
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return False

    path = request.url.path.rstrip("/")
    # /agent/auth/me 不改变状态，豁免。
    if path == "/agent/auth/me":
        return False
    return True


_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/agent/auth/login/local",
        "/agent/auth/logout",
        "/agent/auth/register",
        "/agent/auth/initialize",
    }
)


def is_auth_endpoint(request: Request) -> bool:
    """判断请求是否打到认证端点。

    登录/注册/初始化这类认证端点首次调用时还没有 CSRF token，因此走 Origin 兜底。
    """
    return request.url.path.rstrip("/") in _AUTH_EXEMPT_PATHS


def _host_with_optional_port(hostname: str, port: int | None, scheme: str) -> str:
    """返回标准化 host[:port]，默认端口会省略。"""
    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return host
    return f"{host}:{port}"


def _normalize_origin(origin: str) -> str | None:
    """标准化 ``scheme://host[:port]`` origin；非法输入返回 None。"""
    try:
        parsed = urlsplit(origin.strip())
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None

    # 浏览器 Origin 只包含 scheme/host/port；带路径、query、账号密码的值一律拒绝。
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return None

    return f"{scheme}://{_host_with_optional_port(parsed.hostname, port, scheme)}"


def _configured_cors_origins() -> set[str]:
    """返回显式配置的、允许调用 auth 路由的浏览器 origin。"""
    origins = set()
    for raw_origin in os.environ.get("GATEWAY_CORS_ORIGINS", "").split(","):
        origin = raw_origin.strip()
        if not origin or origin == "*":
            continue
        normalized = _normalize_origin(origin)
        if normalized:
            origins.add(normalized)
    return origins


def get_configured_cors_origins() -> set[str]:
    """从 GATEWAY_CORS_ORIGINS 返回标准化后的显式浏览器 origin。"""
    return _configured_cors_origins()


def _first_header_value(value: str | None) -> str | None:
    """从逗号分隔的代理头中取第一个值。"""
    if not value:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _forwarded_param(request: Request, name: str) -> str | None:
    """从第一个 RFC 7239 Forwarded 头条目中提取参数。"""
    forwarded = _first_header_value(request.headers.get("forwarded"))
    if not forwarded:
        return None

    for part in forwarded.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.lower() == name:
            return value.strip().strip('"') or None
    return None


def _request_scheme(request: Request) -> str:
    """从可信代理头中解析原始请求 scheme。"""
    scheme = _forwarded_param(request, "proto") or _first_header_value(request.headers.get("x-forwarded-proto")) or request.url.scheme
    return scheme.lower()


def _request_origin(request: Request) -> str | None:
    """构造浏览器实际访问目标的 origin。"""
    scheme = _request_scheme(request)
    host = _forwarded_param(request, "host") or _first_header_value(request.headers.get("x-forwarded-host")) or request.headers.get("host") or request.url.netloc

    forwarded_port = _first_header_value(request.headers.get("x-forwarded-port"))
    if forwarded_port and ":" not in host.rsplit("]", 1)[-1]:
        host = f"{host}:{forwarded_port}"

    return _normalize_origin(f"{scheme}://{host}")


def is_allowed_auth_origin(request: Request) -> bool:
    """只允许同源或显式配置 origin 发起 auth POST。

    login/register/initialize 豁免 double-submit token，因为首次浏览器请求还没有
    CSRF token。但这些请求会创建 session cookie，因此带恶意 Origin 的浏览器请求
    仍必须拒绝，避免登录 CSRF / session fixation。没有 Origin 的请求允许通过，
    用于 curl、移动端等非浏览器客户端。
    """
    origin = request.headers.get("origin")
    if not origin:
        return True

    normalized_origin = _normalize_origin(origin)
    if normalized_origin is None:
        return False

    request_origin = _request_origin(request)
    return normalized_origin in _configured_cors_origins() or (request_origin is not None and normalized_origin == request_origin)


class CSRFMiddleware(BaseHTTPMiddleware):
    """使用 Double Submit Cookie 模式实现 CSRF 防护的中间件。"""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        _is_auth = is_auth_endpoint(request)

        if should_check_csrf(request) and _is_auth and not is_allowed_auth_origin(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-site auth request denied."},
            )

        if should_check_csrf(request) and not _is_auth:
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME)

            if not cookie_token or not header_token:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing. Include X-CSRF-Token header."},
                )

            if not secrets.compare_digest(cookie_token, header_token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token mismatch."},
                )

        response = await call_next(request)

        # 认证端点创建 session 时，同步下发 CSRF cookie。
        if _is_auth and request.method == "POST":
            # 为当前 session 生成新的 CSRF token。
            csrf_token = generate_csrf_token()
            is_https = is_secure_request(request)
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=csrf_token,
                httponly=False,  # Double Submit Cookie 需要前端 JS 读取后放入请求头。
                secure=is_https,
                samesite="strict",
            )

        return response


def get_csrf_token(request: Request) -> str | None:
    """从当前请求 cookie 中读取 CSRF token。

    服务端渲染页面需要把 token 写入表单或请求头时会用到。
    """
    return request.cookies.get(CSRF_COOKIE_NAME)
