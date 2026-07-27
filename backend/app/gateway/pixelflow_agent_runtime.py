"""把 PixelFlow 新 Supervisor 图绑定到 Gateway 生命周期。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.types import Checkpointer

from pixelflow.agent_runtime.graph import (
    AgentRuntimeGraphComposition,
    WorkflowRegistry,
    compose_agent_runtime_graph,
)

_APP_STATE_ATTRIBUTE = "pixelflow_agent_graph_runtime"


@asynccontextmanager
async def make_pixelflow_agent_graph_runtime(
    app: FastAPI,
    *,
    checkpointer: Checkpointer,
    registry: WorkflowRegistry | None = None,
) -> AsyncGenerator[AgentRuntimeGraphComposition, None]:
    """挂载新图运行时，并在共享 checkpointer 关闭前移除引用。"""

    if getattr(app.state, _APP_STATE_ATTRIBUTE, None) is not None:
        raise RuntimeError("PixelFlow Agent 图运行时不可重复挂载")

    runtime = compose_agent_runtime_graph(
        registry=registry,
        checkpointer=checkpointer,
    )
    setattr(app.state, _APP_STATE_ATTRIBUTE, runtime)
    try:
        yield runtime
    finally:
        await runtime.aclose()
        if getattr(app.state, _APP_STATE_ATTRIBUTE, None) is runtime:
            delattr(app.state, _APP_STATE_ATTRIBUTE)
