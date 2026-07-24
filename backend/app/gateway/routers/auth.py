"""content-app 登录态查询接口。

pixelflow 不再提供本地登录、注册、初始化管理员等能力。登录统一发生在
content-app，前端访问 pixelflow 时只需要在请求头携带
``Authorization: Bearer <content-app-jwt>``。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.gateway.deps import get_current_user_from_request

router = APIRouter(prefix="/agent/auth", tags=["auth"])


class CurrentUserResponse(BaseModel):
    """当前 content-app 登录用户信息。"""

    authenticated: bool = True
    id: str
    username: str


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(request: Request) -> CurrentUserResponse:
    """返回当前用户。

    Java 类比：这相当于一个只读的 ``/current-user`` Controller。它不会创建
    pixelflow session，也不会刷新 token，只是复用全局 AuthMiddleware 已校验过的
    content-app 登录态。
    """
    user = await get_current_user_from_request(request)
    return CurrentUserResponse(id=str(user.id), username=getattr(user, "username", str(user.id)))
