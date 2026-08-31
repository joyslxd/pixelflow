"""全局认证中间件：fail-closed 安全兜底。

非公开路径必须携带 content-app 的 ``Authorization: Bearer <token>``。请求通过
content-app JWT 本地解析和远程 ``/api/auth/verify`` 校验后，会把当前用户写入
``request.state.user``、``request.state.auth`` 以及两个 ContextVar：

- ``pixelflow.platform.auth_context``：给 Repository owner 过滤使用。
- ``content_app_auth_context``：给后续 Borgrise/content-app HTTP 调用透传原始 token。

更细粒度的资源权限检查仍由 ``authz.py`` 装饰器负责。
"""

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from app.gateway.content_app_auth_context import reset_current_content_app_auth, set_current_content_app_auth
from pixelflow.platform.auth_context import reset_current_user, set_current_user
from pixelflow.tracing import set_conversation_id_context

# 永远不需要认证的路径前缀。
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    # 用途：允许容器编排探测进程存活与 Harness 装配状态；影响：仅返回固定状态码，不暴露用户或配置。
    "/live",
    "/ready",
    "/agent/docs",
    "/agent/redoc",
    "/agent/openapi.json",
)

# 当前登录态完全来自 content-app；pixelflow 不再公开本地登录、注册、初始化接口。
_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset()
_INTERNAL_SERVICE_PATH_PREFIXES: tuple[str, ...] = (
    "/agent/internal/agent-tools",
)


def _is_public(path: str) -> bool:
    stripped = path.rstrip("/")
    if stripped in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


def _is_internal_service_path(path: str) -> bool:
    """仅绕过终端用户 JWT；目标 Router 仍必须校验独立 Sidecar 服务身份。"""

    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in _INTERNAL_SERVICE_PATH_PREFIXES
    )


class AuthMiddleware:
    """严格认证门禁：非公开路径必须有有效 content-app Authorization。

    非公开路径分两步检查：

    1. 先检查 ``Authorization`` header 是否存在；缺失时返回 401。
    2. 再通过 ``get_current_user_from_request`` 严格校验 content-app JWT 和用户
       实时状态；token 伪造、过期、用户禁用都会在这里拒绝。

    成功后会写 ``request.state.user`` 和 ``pixelflow.platform.auth_context``，让下游
    仓储 owner 过滤生效，不需要每个 route 都加 ``@require_auth``。需要资源级授权
    的接口，例如“用户 A 不能靠猜 URL 读取用户 B 的 thread”，仍应额外使用
    ``@require_permission(..., owner_check=True)``。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """原生 ASGI 认证边界，避免 BaseHTTPMiddleware 取消下游 SQL 提交。"""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if _is_public(request.url.path) or _is_internal_service_path(request.url.path):
            await self.app(scope, receive, send)
            return

        authorization = request.headers.get("Authorization")

        # 非公开路径必须带 content-app Authorization；旧内部 token 绕过通道已删除。
        if not authorization:
            response = JSONResponse(
                status_code=401,
                content={
                    "detail": AuthErrorResponse(
                        code=AuthErrorCode.NOT_AUTHENTICATED,
                        message="Authentication required",
                    ).model_dump()
                },
            )
            await response(scope, receive, send)
            return

        # 严格 JWT 校验：垃圾 token、过期 token、禁用用户都在这里拒绝。
        #
        # 这里调用严格 resolver，以保留 token_expired、token_invalid、user_not_found
        # 等细粒度错误码。BaseHTTPMiddleware 不会让 HTTPException 正常冒泡，所以
        # 这里捕获后手动渲染 JSONResponse。
        from app.gateway.deps import get_current_user_from_request

        try:
            user = await get_current_user_from_request(request)
        except HTTPException as exc:
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            await response(scope, receive, send)
            return

        # 同时写 request.state.user 和 request.state.auth。前者配合 ContextVar owner
        # 过滤，后者让 @require_permission 不必在同一请求里再次执行 JWT decode + DB 查询。
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=_ALL_PERMISSIONS)
        user_token = set_current_user(user)
        content_app_token = set_current_content_app_auth(authorization, username=getattr(user, "username", str(user.id)))
        # 内部调试用 trace：前端在生成类请求上带的 X-Conversation-Id，
        # 供 pixelflow.tracing.record_trace_event_background 关联 vendor_call/llm_call。
        set_conversation_id_context(request.headers.get("X-Conversation-Id"))
        try:
            await self.app(scope, receive, send)
        finally:
            if content_app_token is not None:
                reset_current_content_app_auth(content_app_token)
            reset_current_user(user_token)
            set_conversation_id_context(None)
