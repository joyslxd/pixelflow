"""PixelFlow 请求级用户上下文与 owner 隔离辅助函数。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Final, Protocol, runtime_checkable


@runtime_checkable
class CurrentUser(Protocol):
    """认证用户的最小结构合同，避免平台层依赖 Gateway 用户实体。"""

    id: str


DEFAULT_USER_ID: Final[str] = "default"
_current_user: Final[ContextVar[CurrentUser | None]] = ContextVar(
    "pixelflow_current_user",
    default=None,
)


def set_current_user(user: CurrentUser) -> Token[CurrentUser | None]:
    """为当前异步任务写入已认证用户，并返回用于 finally 复位的令牌。"""

    if not str(user.id).strip():
        raise ValueError("当前用户标识不能为空")
    return _current_user.set(user)


def reset_current_user(token: Token[CurrentUser | None]) -> None:
    """恢复进入请求前的用户上下文，避免连接复用时串租户。"""

    _current_user.reset(token)


def get_current_user() -> CurrentUser | None:
    """返回当前认证用户；公共请求或离线任务可能没有用户。"""

    return _current_user.get()


def require_current_user() -> CurrentUser:
    """读取必须存在的当前用户，缺失时拒绝 Repository 隐式越权访问。"""

    user = get_current_user()
    if user is None:
        raise RuntimeError("Repository 访问缺少 PixelFlow 用户上下文")
    return user


def get_effective_user_id() -> str:
    """返回当前用户标识；无请求上下文时固定落入隔离的默认桶。"""

    user = get_current_user()
    return DEFAULT_USER_ID if user is None else str(user.id)


__all__ = [
    "DEFAULT_USER_ID",
    "CurrentUser",
    "get_current_user",
    "get_effective_user_id",
    "require_current_user",
    "reset_current_user",
    "set_current_user",
]
