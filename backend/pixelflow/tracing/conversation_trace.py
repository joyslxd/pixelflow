"""v2 对话工作流的内部调试 trace 记录。

只覆盖当前主用的 v2 工作流（intake/creative/image/video/ppt），跟旧
LangGraph 任务流的 ``pixelflow_task_events``/``run_events`` 是两套独立的东西。

用途仅限内部调试，不面向普通用户展示（参见 README「关键约束」：前端不能对
普通用户暴露原始 prompt、供应商调用详情、完整堆栈）。

用法：
    - 网关在请求入口读取 ``X-Conversation-Id`` 请求头，调用
      ``set_conversation_id_context`` 写入当前请求的 ContextVar。
    - 业务代码（``run_generation.make_request`` / ``intake.llm._invoke_json_model``）
      调用 ``record_trace_event_background`` 记录一条事件；当前请求没有
      conversation_id（例如旧流程、后台任务）时直接跳过，不报错、不阻塞。
    - ``app.py`` 启动时调用 ``configure_trace_sink`` 注入真正的存储实现，
      避免这个业务层模块直接依赖 FastAPI ``app.state``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

TraceSink = Callable[[str, str, dict[str, Any], str | None], Awaitable[None]]
"""签名：(conversation_id, event, data, user_id) -> None"""

_conversation_id_ctx: ContextVar[str | None] = ContextVar("pixelflow_conversation_trace_id", default=None)
_trace_sink: TraceSink | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def configure_trace_sink(sink: TraceSink | None) -> None:
    """注入实际的写入实现；通常在网关启动时(在运行中的事件循环里)调用一次。

    顺带记下当前主事件循环：``run_generation.make_request`` 等业务函数是同步的，
    路由层用 ``asyncio.to_thread`` 丢到线程池执行，那个线程里没有运行中的事件循环，
    不能直接 ``asyncio.create_task``；记录下主循环后可以用
    ``run_coroutine_threadsafe`` 把写入调度回主循环。
    """
    global _trace_sink, _main_loop
    _trace_sink = sink
    if sink is not None:
        try:
            _main_loop = asyncio.get_running_loop()
        except RuntimeError:
            _main_loop = None
    else:
        _main_loop = None


def set_conversation_id_context(conversation_id: str | None) -> None:
    """在当前请求的 ContextVar 里记录 conversation_id；空值视为清空。"""
    _conversation_id_ctx.set(conversation_id.strip() if isinstance(conversation_id, str) and conversation_id.strip() else None)


def get_conversation_id_context() -> str | None:
    return _conversation_id_ctx.get()


def record_trace_event_background(event: str, data: dict[str, Any], *, user_id: str | None = None) -> None:
    """后台写入一条 trace 事件；没有 conversation_id 上下文或没配置 sink 时直接跳过。

    跟 ``record_power_mem_background`` 一样用 ``asyncio.create_task`` 做
    fire-and-forget，写入失败只记 warning，绝不影响主生成流程。
    """
    conversation_id = get_conversation_id_context()
    sink = _trace_sink
    if not conversation_id or sink is None:
        return

    async def _run() -> None:
        try:
            await sink(conversation_id, event, data, user_id)
        except Exception:
            logger.warning("Conversation trace background record failed", exc_info=True)

    try:
        asyncio.get_running_loop()
        asyncio.create_task(_run())
    except RuntimeError:
        # 当前线程没有运行中的事件循环：多半是同步业务函数被
        # ``asyncio.to_thread`` 丢到线程池执行（例如 run_generation.make_request）。
        # 用记录下来的主循环把写入调度回去；主循环也拿不到时放弃写入，不阻塞、不抛异常。
        if _main_loop is not None:
            asyncio.run_coroutine_threadsafe(_run(), _main_loop)
        else:
            logger.warning("No event loop available to record conversation trace event; dropping")
