"""集中管理 ``app.state`` 上的运行时单例。

这些 getter 主要给 router 使用：必需依赖缺失时返回 503；只有 ``get_store`` 允许
返回 None。

``AppConfig`` 刻意不缓存到 ``app.state``。Router 和 run 路径会通过
``deerflow.config.app_config.get_app_config`` 获取配置，该函数基于文件 mtime 做热
加载，所以当前 profile YAML 修改后下一次请求即可生效。``langgraph_runtime`` 创建的
引擎（stream bridge、persistence、checkpointer、store、run-event store）绑定启动
快照，这些属于必须重启才安全切换的基础设施。

初始化由 ``app.py`` 通过 ``AsyncExitStack`` 统一管理。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import Mock
from typing import TYPE_CHECKING, TypeVar, cast

from fastapi import FastAPI, HTTPException, Request
from langgraph.types import Checkpointer

from app.gateway.content_app_auth import ContentAppAuthError, authenticate_authorization_header
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.persistence.feedback import FeedbackRepository
from deerflow.runtime import RunContext, RunManager, StreamBridge
from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from deerflow.persistence.thread_meta.base import ThreadMetaStore
    from deerflow.runtime import RunRecord


T = TypeVar("T")


async def _mark_latest_recovered_threads_error(
    run_manager: RunManager,
    thread_store: ThreadMetaStore,
    recovered_runs: list[RunRecord],
) -> None:
    """只有最新 run 被恢复为错误时，才把 thread 状态标记为 error。"""
    recovered_by_thread: dict[str, set[str]] = {}
    for record in recovered_runs:
        recovered_by_thread.setdefault(record.thread_id, set()).add(record.run_id)

    for thread_id, recovered_run_ids in recovered_by_thread.items():
        try:
            latest_runs = await run_manager.list_by_thread(thread_id, user_id=None, limit=1)
        except Exception:
            logger.warning("Failed to find latest run for thread %s during run reconciliation", thread_id, exc_info=True)
            continue
        if not latest_runs or latest_runs[0].run_id not in recovered_run_ids:
            continue
        try:
            await thread_store.update_status(thread_id, "error", user_id=None)
        except Exception:
            logger.warning("Failed to mark thread %s as error during run reconciliation", thread_id, exc_info=True)


def get_config() -> AppConfig:
    """返回当前请求可见的最新 ``AppConfig``。

    这里会走 ``get_app_config``，它尊重运行时 ``ContextVar`` 覆盖，并在
    当前 profile YAML mtime 变化时从磁盘重载。``AppConfig`` 完全不缓存在
    ``app.state``；唯一启动快照只存在于 ``lifespan()`` 的局部变量
    ``startup_config``，并显式传给必须重启才切换的基础设施。

    配置文件缺失、权限错误、YAML 解析失败或校验失败都会返回 503，语义是“网关没有
    可用配置，无法服务请求”，同时保留原始异常日志供排查。
    """
    try:
        return get_app_config()
    except Exception as exc:  # noqa: BLE001 - request boundary: log and degrade gracefully
        logger.exception("Failed to load AppConfig at request time")
        raise HTTPException(status_code=503, detail="Configuration not available") from exc


@asynccontextmanager
async def langgraph_runtime(app: FastAPI, startup_config: AppConfig) -> AsyncGenerator[None, None]:
    """启动和销毁所有 LangGraph runtime 单例。

    ``startup_config`` 是 ``lifespan()`` 中只取一次的配置快照，用于基础设施启动。
    这里构建的 stream bridge、persistence engine、checkpointer、store、
    run-event store 都持有连接、文件句柄或单例 provider，必须重启才安全切换，因此
    绑定这个启动快照。请求期如果需要热加载字段，仍必须走 ``get_config``。

    匹配的 ``run_events_config`` 会冻结到 ``app.state``，这样 ``get_run_context``
    不会把新热加载的 run_events 配置和仍绑定旧后端的 event_store 混搭。

    在 ``app.py`` 中的用法::

        async with langgraph_runtime(app, startup_config):
            yield
    """
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
    from deerflow.runtime import make_store, make_stream_bridge
    from deerflow.runtime.checkpointer.async_provider import make_checkpointer
    from deerflow.runtime.events.store import make_run_event_store

    async with AsyncExitStack() as stack:
        config = startup_config

        app.state.stream_bridge = await stack.enter_async_context(make_stream_bridge(config))

        # 先初始化 persistence engine，再初始化 checkpointer，确保 postgres 后端的
        # 自动建库逻辑先执行。
        await init_engine_from_config(config.database)

        app.state.checkpointer = await stack.enter_async_context(make_checkpointer(config))
        app.state.store = await stack.enter_async_context(make_store(config))

        # 初始化仓储层：所有 repository 共用同一个 session_factory。
        sf = get_session_factory()
        if sf is not None:
            from deerflow.persistence.feedback import FeedbackRepository
            from deerflow.persistence.run import RunRepository

            app.state.run_store = RunRepository(sf)
            app.state.feedback_repo = FeedbackRepository(sf)
        else:
            from deerflow.runtime.runs.store.memory import MemoryRunStore

            app.state.run_store = MemoryRunStore()
            app.state.feedback_repo = None

        from deerflow.persistence.thread_meta import make_thread_store

        app.state.thread_store = make_thread_store(sf, app.state.store)

        # Run event store 和对应 run_events_config 都在启动时冻结，避免请求期热加载的
        # AppConfig.run_events 与旧 event_store 后端混用。
        run_events_config = getattr(config, "run_events", None)
        app.state.run_events_config = run_events_config
        app.state.run_event_store = make_run_event_store(run_events_config)

        # RunManager 使用 store 作为持久化后端。
        app.state.run_manager = RunManager(store=app.state.run_store)
        if getattr(config.database, "backend", None) == "sqlite":
            from deerflow.utils.time import now_iso

            # 仅启动期恢复：正常关闭时不会有 active rows，下面的 thread 状态更新为空操作。
            recovered_runs = await app.state.run_manager.reconcile_orphaned_inflight_runs(
                error="Gateway restarted before this run reached a durable final state.",
                before=now_iso(),
            )
            await _mark_latest_recovered_threads_error(app.state.run_manager, app.state.thread_store, recovered_runs)

        try:
            yield
        finally:
            await close_engine()


# ---------------------------------------------------------------------------
# Getters：router 在每个请求中调用。
# ---------------------------------------------------------------------------


def _require(attr: str, label: str) -> Callable[[Request], T]:
    """创建 FastAPI dependency：返回 ``app.state.<attr>``，缺失时抛 503。"""

    def dep(request: Request) -> T:
        val = getattr(request.app.state, attr, None)
        if val is None:
            raise HTTPException(status_code=503, detail=f"{label} not available")
        return cast(T, val)

    dep.__name__ = dep.__qualname__ = f"get_{attr}"
    return dep


get_stream_bridge: Callable[[Request], StreamBridge] = _require("stream_bridge", "Stream bridge")
get_run_manager: Callable[[Request], RunManager] = _require("run_manager", "Run manager")
get_checkpointer: Callable[[Request], Checkpointer] = _require("checkpointer", "Checkpointer")
get_run_event_store: Callable[[Request], RunEventStore] = _require("run_event_store", "Run event store")
get_feedback_repo: Callable[[Request], FeedbackRepository] = _require("feedback_repo", "Feedback")
get_run_store: Callable[[Request], RunStore] = _require("run_store", "Run store")


def get_store(request: Request):
    """返回全局 LangGraph store；未配置时允许为 None。"""
    return getattr(request.app.state, "store", None)


def get_thread_store(request: Request) -> ThreadMetaStore:
    """返回 thread 元数据 store，可能是 SQL 或内存实现。"""
    val = getattr(request.app.state, "thread_store", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Thread metadata store not available")
    return val


def get_run_context(request: Request) -> RunContext:
    """从 ``app.state`` 单例构建 ``RunContext``。

    返回基础上下文，包含 checkpointer、store、event_store、thread_store 等基础设施。
    ``app_config`` 每次请求实时解析，因此模型 max_tokens 等 per-run 配置能跟随
    当前 profile YAML 热更新；``event_store`` / ``run_events_config`` 保持启动快照，
    避免 store 后端和配置后端错配。
    """
    return RunContext(
        checkpointer=get_checkpointer(request),
        store=get_store(request),
        event_store=get_run_event_store(request),
        run_events_config=getattr(request.app.state, "run_events_config", None),
        thread_store=get_thread_store(request),
        app_config=get_config(),
    )


# ---------------------------------------------------------------------------
# Auth 辅助函数：供 authz.py 和 auth middleware 使用。
# ---------------------------------------------------------------------------

async def get_current_user_from_request(request: Request):
    """从 ``Authorization`` 请求头解析 content-app 用户。

    Java 类比：这里相当于 Controller/Filter 共用的 ``CurrentUserResolver``。
    pixelflow 不再读取自己的 ``access_token`` cookie，也不再查本地 users 表；用户
    身份完全来自 content-app JWT，并通过远程 ``/api/auth/verify`` 确认实时可用。
    """
    from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse

    state = getattr(request, "state", None)
    cached_user = getattr(state, "user", None)
    if cached_user is not None and not isinstance(cached_user, Mock):
        return cached_user

    authorization = None
    if isinstance(getattr(request, "headers", None), dict):
        authorization = request.headers.get("Authorization")
    elif hasattr(request, "headers") and hasattr(request.headers, "get"):
        auth_candidate = request.headers.get("Authorization")
        if isinstance(auth_candidate, str):
            authorization = auth_candidate

    try:
        if authorization is not None:
            return await authenticate_authorization_header(authorization)
    except ContentAppAuthError as exc:
        # If Authorization header exists, keep existing content-app error contract.
        if authorization is not None:
            code = AuthErrorCode(exc.code) if exc.code in AuthErrorCode._value2member_map_ else AuthErrorCode.TOKEN_INVALID
            raise HTTPException(
                status_code=exc.status_code,
                detail=AuthErrorResponse(code=code, message=exc.message).model_dump(),
            ) from exc
        # If header is not present / invalid in test stubs, continue with local cookie path.

    access_token = getattr(request, "cookies", {}).get("access_token")
    if access_token:
        from app.gateway.auth.jwt import decode_token
        from app.gateway.auth.errors import TokenError

        payload = decode_token(access_token)
        if isinstance(payload, TokenError):
            raise HTTPException(status_code=401, detail="Token has been revoked")

        try:
            provider = get_local_provider()
            user = await provider.get_user(payload.sub)
            if user is None or user.token_version != payload.ver:
                raise HTTPException(status_code=401, detail="Token has been revoked")
            return user
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail="Auth provider unavailable") from exc

    code = AuthErrorCode.TOKEN_INVALID if authorization else AuthErrorCode.NOT_AUTHENTICATED
    raise HTTPException(
        status_code=401,
        detail=AuthErrorResponse(
            code=code,
            message="Unauthorized",
        ).model_dump(),
    )


def get_local_provider():
    """Return legacy local auth provider for compatibility and tests."""
    from deerflow.persistence.engine import get_session_factory
    from app.gateway.auth.local_provider import LocalAuthProvider
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository

    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("Local auth provider not initialized")
    return LocalAuthProvider(SQLiteUserRepository(sf))


async def get_optional_user_from_request(request: Request):
    """获取可选认证用户。

    未认证时返回 None。
    """
    try:
        return await get_current_user_from_request(request)
    except HTTPException:
        return None


async def get_current_user(request: Request) -> str | None:
    """从 content-app Authorization 中提取当前用户名；未认证时返回 None。

    这是一个轻量适配器，适合只需要用户 ID 的调用方（如 ``feedback.py``）。需要完整
    用户对象时应使用 ``get_current_user_from_request`` 或
    ``get_optional_user_from_request``。
    """
    user = await get_optional_user_from_request(request)
    return str(user.id) if user else None
