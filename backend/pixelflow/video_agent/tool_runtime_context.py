"""原生 Video Agent Tool 调用的进程内上下文（不暴露给模型 schema）。"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_TOOL_RUNTIME_CONTEXT: ContextVar[Mapping[str, object] | None] = ContextVar(
    "video_agent_tool_runtime_context",
    default=None,
)


def get_tool_runtime_context() -> Mapping[str, object] | None:
    """读取当前 Task 绑定的 Tool 执行上下文。"""

    return _TOOL_RUNTIME_CONTEXT.get()


@contextmanager
def bind_tool_runtime_context(
    context: Mapping[str, object],
) -> Iterator[None]:
    """在原生 Agent invocation 期间绑定 user/workspace/credential 等上下文字段。"""

    token: Token = _TOOL_RUNTIME_CONTEXT.set(dict(context))
    try:
        yield
    finally:
        _TOOL_RUNTIME_CONTEXT.reset(token)
