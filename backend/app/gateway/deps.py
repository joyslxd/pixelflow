"""Gateway 的当前用户解析依赖。"""

from __future__ import annotations

from unittest.mock import Mock

from fastapi import HTTPException, Request

from app.gateway.content_app_auth import ContentAppAuthError, authenticate_authorization_header


async def get_current_user_from_request(request: Request):
    """从 content-app Authorization 解析当前用户，不依赖 DeerFlow Runtime。"""

    from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse

    cached_user = getattr(getattr(request, "state", None), "user", None)
    if cached_user is not None and not isinstance(cached_user, Mock):
        return cached_user
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(
                code=AuthErrorCode.NOT_AUTHENTICATED,
                message="Unauthorized",
            ).model_dump(),
        )
    try:
        return await authenticate_authorization_header(authorization)
    except ContentAppAuthError as exc:
        code = (
            AuthErrorCode(exc.code)
            if exc.code in AuthErrorCode._value2member_map_
            else AuthErrorCode.TOKEN_INVALID
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=AuthErrorResponse(code=code, message=exc.message).model_dump(),
        ) from exc


async def get_optional_user_from_request(request: Request):
    """未认证时返回空，供只读兼容场景使用。"""

    try:
        return await get_current_user_from_request(request)
    except HTTPException:
        return None


async def get_current_user(request: Request) -> str | None:
    """返回当前用户稳定标识，供 Controller 的 owner 隔离使用。"""

    user = await get_optional_user_from_request(request)
    return str(user.id) if user is not None else None
