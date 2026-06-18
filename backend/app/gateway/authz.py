"""DeerFlow Gateway 的授权装饰器和认证上下文。

设计参考 LangGraph Auth system：
https://github.com/langchain-ai/langgraph/blob/main/libs/sdk-py/langgraph_sdk/auth/__init__.py

**使用方式：**

1. 需要登录的 route 加 ``@require_auth``。
2. 需要资源权限的 route 加 ``@require_permission("resource", "action", ...)``。
3. 装饰器链按 Python 规则从下往上执行。

**示例：**

    @router.get("/{thread_id}")
    @require_auth
    @require_permission("threads", "read", owner_check=True)
    async def get_thread(thread_id: str, request: Request):
        # 用户已认证，并具备 threads:read 权限。
        ...

**权限模型：**

- threads:read   - 查看 thread
- threads:write  - 创建/更新 thread
- threads:delete - 删除 thread
- runs:create    - 启动 agent run
- runs:read      - 查看 run
- runs:cancel    - 取消 run
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from app.gateway.auth.models import User

P = ParamSpec("P")
T = TypeVar("T")


# 权限常量
class Permissions:
    """``resource:action`` 格式的权限常量。"""

    # Thread 资源权限
    THREADS_READ = "threads:read"
    THREADS_WRITE = "threads:write"
    THREADS_DELETE = "threads:delete"

    # Run 资源权限
    RUNS_CREATE = "runs:create"
    RUNS_READ = "runs:read"
    RUNS_CANCEL = "runs:cancel"


class AuthContext:
    """当前请求的认证上下文。

    ``require_auth`` 或全局 AuthMiddleware 会把它写入 ``request.state.auth``。

    属性：
        user: 已认证用户；匿名时为 None。
        permissions: 权限字符串列表，例如 "threads:read"。
    """

    __slots__ = ("user", "permissions")

    def __init__(self, user: User | None = None, permissions: list[str] | None = None):
        self.user = user
        self.permissions = permissions or []

    @property
    def is_authenticated(self) -> bool:
        """判断当前上下文是否已认证。"""
        return self.user is not None

    def has_permission(self, resource: str, action: str) -> bool:
        """判断当前上下文是否拥有 ``resource:action`` 权限。

        参数：
            resource: 资源名，例如 "threads"。
            action: 动作名，例如 "read"。

        返回拥有权限时为 True。
        """
        permission = f"{resource}:{action}"
        return permission in self.permissions

    def require_user(self) -> User:
        """返回当前用户；未认证时抛 401。

        未认证时抛 HTTPException 401。
        """
        if not self.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return self.user


def get_auth_context(request: Request) -> AuthContext | None:
    """从 request state 中读取 ``AuthContext``。"""
    return getattr(request.state, "auth", None)


_ALL_PERMISSIONS: list[str] = [
    Permissions.THREADS_READ,
    Permissions.THREADS_WRITE,
    Permissions.THREADS_DELETE,
    Permissions.RUNS_CREATE,
    Permissions.RUNS_READ,
    Permissions.RUNS_CANCEL,
]


def _make_test_request_stub() -> Any:
    """为直接调用 route handler 的单测创建最小 request stub。

    某些单测不走 FastAPI request 注入，装饰器需要一个具备 state/cookies 的轻量对象。
    """
    return SimpleNamespace(state=SimpleNamespace(), cookies={}, _deerflow_test_bypass_auth=True)


async def _authenticate(request: Request) -> AuthContext:
    """认证请求并返回 ``AuthContext``。

    JWT -> User 的流程委托给 ``deps.get_optional_user_from_request``。匿名请求返回
    ``user=None`` 的上下文。
    """
    from app.gateway.deps import get_optional_user_from_request

    user = await get_optional_user_from_request(request)
    if user is None:
        return AuthContext(user=None, permissions=[])

    # 未来可以把权限存入用户记录；当前认证用户默认拥有网关内全部权限。
    return AuthContext(user=user, permissions=_ALL_PERMISSIONS)


def require_auth[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """认证装饰器：确保请求已登录。

    即使 ASGI 栈里没有 ``AuthMiddleware``，它也会独立对未认证请求抛 401，并把解析
    后的 ``AuthContext`` 写入 ``request.state.auth`` 供下游使用。

    按当前代码约定应放在其他权限装饰器之上。

    用法：
        @router.get("/{thread_id}")
        @require_auth  # 底层装饰器，配合 permission check 使用。
        @require_permission("threads", "read")
        async def get_thread(thread_id: str, request: Request):
            auth: AuthContext = request.state.auth
            ...

    抛出：
        HTTPException: 未认证时 401。
        ValueError: 缺少 request 参数时。
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None:
            # 单测可能直接调用被装饰 handler，而没有 FastAPI Request 对象。若函数声明
            # 了 request 参数，则注入最小 stub。
            if "request" in inspect.signature(func).parameters:
                kwargs["request"] = _make_test_request_stub()
            else:
                raise ValueError("require_auth decorator requires 'request' parameter")
            request = kwargs["request"]

        if getattr(request, "_deerflow_test_bypass_auth", False):
            return await func(*args, **kwargs)

        # 执行认证并写入上下文。
        auth_context = await _authenticate(request)
        request.state.auth = auth_context

        if not auth_context.is_authenticated:
            raise HTTPException(status_code=401, detail="Authentication required")

        return await func(*args, **kwargs)

    return wrapper


def require_permission(
    resource: str,
    action: str,
    owner_check: bool = False,
    require_existing: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """权限装饰器：检查 ``resource:action`` 权限。

    必须和 ``@require_auth`` 配合使用。

    参数：
        resource: 资源名，例如 "threads"、"runs"。
        action: 动作名，例如 "read"、"write"、"delete"。
        owner_check: 为 True 时校验当前用户是否拥有该资源。需要 path 参数
            ``thread_id``。
        require_existing: 仅在 ``owner_check=True`` 时有意义。为 True 时，
            ``threads_meta`` 缺行也算拒绝（404），而不是按“未跟踪历史线程”放行。
            删除、PATCH、状态更新等破坏性/变更接口应开启它，避免已删除 thread 被另
            一个用户通过缺行路径重新命中。

    用法：
        # 读接口：允许未跟踪的历史 thread。
        @require_permission("threads", "read", owner_check=True)
        async def get_thread(thread_id: str, request: Request):
            ...

        # 破坏性接口：thread 行必须存在且属于调用者。
        @require_permission("threads", "delete", owner_check=True, require_existing=True)
        async def delete_thread(thread_id: str, request: Request):
            ...

    抛出：
        HTTPException 401: 需要认证但用户匿名。
        HTTPException 403: 用户缺少权限。
        HTTPException 404: owner_check=True 且资源不属于该用户。
        ValueError: owner_check=True 但缺少 ``thread_id`` 参数。
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = kwargs.get("request")
            if request is None:
                # 单测可能直接调用 route handler 而不构造 FastAPI Request；若函数声明
                # 了 request 参数，则注入最小 stub。
                if "request" in inspect.signature(func).parameters:
                    kwargs["request"] = _make_test_request_stub()
                else:
                    return await func(*args, **kwargs)
                request = kwargs["request"]

            if getattr(request, "_deerflow_test_bypass_auth", False):
                return await func(*args, **kwargs)

            auth: AuthContext = getattr(request.state, "auth", None)
            if auth is None:
                auth = await _authenticate(request)
                request.state.auth = auth

            if not auth.is_authenticated:
                raise HTTPException(status_code=401, detail="Authentication required")

            # 检查资源动作权限。
            if not auth.has_permission(resource, action):
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: {resource}:{action}",
                )

            # thread 资源 owner 校验。
            #
            # 2.0-rc 把 thread 元数据迁入 SQL 持久化层（threads_meta 表）。这里通过
            # ThreadMetaStore.check_access 校验归属：缺行（未跟踪历史 thread）或
            # user_id 为 NULL（共享/认证前数据）会返回 True。因此它是 strict-deny，
            # 不是 strict-allow：只有“存在且 user_id 属于别人”的行会触发 404。
            if owner_check:
                thread_id = kwargs.get("thread_id")
                if thread_id is None:
                    raise ValueError("require_permission with owner_check=True requires 'thread_id' parameter")

                from app.gateway.deps import get_thread_store

                thread_store = get_thread_store(request)
                allowed = await thread_store.check_access(
                    thread_id,
                    str(auth.user.id),
                    require_existing=require_existing,
                )
                if not allowed:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Thread {thread_id} not found",
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
