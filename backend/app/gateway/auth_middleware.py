"""全局认证中间件：fail-closed 安全兜底。

非公开路径没有有效登录态时直接返回 401。请求通过 cookie 校验后，会把 JWT payload
解析为真实 ``User`` 对象，并写入 ``request.state.user`` 和
``deerflow.runtime.user_context`` ContextVar，让 Repository 层的 owner 过滤可以
自动生效。

更细粒度的资源权限检查仍由 ``authz.py`` 装饰器负责。
"""

from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from app.gateway.internal_auth import INTERNAL_AUTH_HEADER_NAME, get_internal_user, is_valid_internal_auth_token
from deerflow.runtime.user_context import reset_current_user, set_current_user

# 永远不需要认证的路径前缀。
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# 公开的精确 auth 路径：登录、注册、初始化状态检查等。
# /api/v1/auth/me、/api/v1/auth/change-password 等不是公开路径。
_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login/local",
        "/api/v1/auth/register",
        "/api/v1/auth/logout",
        "/api/v1/auth/setup-status",
        "/api/v1/auth/initialize",
    }
)


def _is_public(path: str) -> bool:
    stripped = path.rstrip("/")
    if stripped in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """严格认证门禁：非公开路径必须有有效 session。

    非公开路径分两步检查：

    1. 先检查 cookie 是否存在；缺失时返回 401 NOT_AUTHENTICATED。
    2. 再通过 ``get_current_user_from_request`` 严格校验 JWT；token 伪造、过期、
       用户不存在或 token_version 过旧都会返回 401。

    成功后会写 ``request.state.user`` 和 ``deerflow.runtime.user_context``，让下游
    仓储 owner 过滤生效，不需要每个 route 都加 ``@require_auth``。需要资源级授权
    的接口，例如“用户 A 不能靠猜 URL 读取用户 B 的 thread”，仍应额外使用
    ``@require_permission(..., owner_check=True)``。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if _is_public(request.url.path):
            return await call_next(request)

        internal_user = None
        if is_valid_internal_auth_token(request.headers.get(INTERNAL_AUTH_HEADER_NAME)):
            internal_user = get_internal_user()

        # 非公开路径必须带 session cookie，内部可信调用除外。
        if internal_user is None and not request.cookies.get("access_token"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": AuthErrorResponse(
                        code=AuthErrorCode.NOT_AUTHENTICATED,
                        message="Authentication required",
                    ).model_dump()
                },
            )

        # 严格 JWT 校验：垃圾 token 或过期 token 在这里直接 401，而不是静默放过。
        # 这堵住“随便放一个像 cookie 的字符串就绕过部分非隔离路由”的缺口。
        #
        # 这里调用严格 resolver，以保留 token_expired、token_invalid、user_not_found
        # 等细粒度错误码。BaseHTTPMiddleware 不会让 HTTPException 正常冒泡，所以
        # 这里捕获后手动渲染 JSONResponse。
        from app.gateway.deps import get_current_user_from_request

        if internal_user is not None:
            user = internal_user
        else:
            try:
                user = await get_current_user_from_request(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        # 同时写 request.state.user 和 request.state.auth。前者配合 ContextVar owner
        # 过滤，后者让 @require_permission 不必在同一请求里再次执行 JWT decode + DB 查询。
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=_ALL_PERMISSIONS)
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)
