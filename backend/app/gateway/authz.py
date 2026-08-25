"""PixelFlow Gateway 的最小认证上下文。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.gateway.auth.models import User


class AuthContext:
    """保存已经由 content-app 校验的当前用户和 Gateway 内部权限集合。"""

    __slots__ = ("user", "permissions")

    def __init__(self, user: User | None = None, permissions: list[str] | None = None) -> None:
        self.user = user
        self.permissions = permissions or []

    @property
    def is_authenticated(self) -> bool:
        """返回当前请求是否已通过 content-app 登录态校验。"""

        return self.user is not None


_ALL_PERMISSIONS = ["agent:read", "agent:write"]

__all__ = ["AuthContext", "_ALL_PERMISSIONS"]
