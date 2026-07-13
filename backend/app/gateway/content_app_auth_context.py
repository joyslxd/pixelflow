"""content-app 登录态的请求级上下文。

这里的职责类似 Java 里的 ``ThreadLocal<LoginUser>``，但 Python async 服务不能
直接用线程局部变量，因为同一个线程里会同时跑很多协程。``ContextVar`` 是 asyncio
下更合适的做法：FastAPI 每个请求拥有独立上下文，``asyncio.create_task`` 和
``asyncio.to_thread`` 会复制当前上下文，因此后台 LangGraph run 与 Borgrise 阻塞
HTTP 调用也能拿到入口请求的 Authorization。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ContentAppAuthState:
    """当前请求从 content-app 继承来的登录态。"""

    authorization: str
    token: str
    username: str


_current_content_app_auth: Final[ContextVar[ContentAppAuthState | None]] = ContextVar("pixelflow_content_app_auth", default=None)


def _extract_bearer_token(authorization: str) -> str:
    """从 ``Bearer xxx`` 中取出原始 JWT 字符串。"""
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return authorization.strip()


def set_current_content_app_auth(authorization: str, *, username: str) -> Token[ContentAppAuthState | None]:
    """写入当前请求的 content-app 登录态，并返回可用于恢复的 token。"""
    state = ContentAppAuthState(
        authorization=authorization.strip(),
        token=_extract_bearer_token(authorization),
        username=username,
    )
    return _current_content_app_auth.set(state)


def reset_current_content_app_auth(token: Token[ContentAppAuthState | None]) -> None:
    """恢复进入请求前的登录态，避免不同请求之间串 token。"""
    _current_content_app_auth.reset(token)


def get_current_content_app_auth() -> ContentAppAuthState | None:
    """读取当前请求登录态；无登录态时返回 None。"""
    return _current_content_app_auth.get()


def require_current_authorization() -> str:
    """返回当前请求的原始 Authorization；缺失时抛错阻止调用计费接口。"""
    state = _current_content_app_auth.get()
    if state is None or not state.authorization:
        raise RuntimeError("当前请求缺少 content-app Authorization，不能调用需要按用户计费的 content-app/Borgrise 接口")
    return state.authorization
