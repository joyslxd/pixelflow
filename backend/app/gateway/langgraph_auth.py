"""LangGraph 兼容认证入口：复用 content-app Authorization。

FastAPI 网关是 pixelflow 的主入口；本模块只给 ``langgraph.json`` 的
``auth.path`` 使用。为了避免两套认证行为不一致，这里不再读取 pixelflow 旧
``access_token`` cookie，而是和 ``AuthMiddleware`` 一样校验
``Authorization: Bearer <content-app-jwt>``。
"""

from __future__ import annotations

from langgraph_sdk import Auth

from app.gateway.content_app_auth import ContentAppAuthError, authenticate_authorization_header

auth = Auth()


@auth.authenticate
async def authenticate(request):
    """校验 content-app token，并把用户名作为 LangGraph identity。"""
    try:
        user = await authenticate_authorization_header(request.headers.get("Authorization"))
    except ContentAppAuthError as exc:
        raise Auth.exceptions.HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return user.id


@auth.on
async def add_owner_filter(ctx: Auth.types.AuthContext, value: dict):
    """写入/过滤 ``metadata.user_id``，保证 LangGraph 资源按 content-app 用户隔离。"""
    metadata = value.setdefault("metadata", {})
    metadata["user_id"] = ctx.user.identity
    return {"user_id": ctx.user.identity}
