"""LangGraph 人工中断的精确恢复边界。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from langgraph.types import Command

from .namespaces import GraphExecutionNamespace


class ResumableGraph(Protocol):
    """约束恢复逻辑实际使用的最小 LangGraph 接口。"""

    async def aget_state(self, config: dict[str, Any]) -> Any: ...

    async def ainvoke(
        self,
        input: Any,
        config: dict[str, Any],
    ) -> Any: ...


async def resume_graph_from_interrupt(
    graph: ResumableGraph,
    namespace: GraphExecutionNamespace,
    *,
    interrupt_id: str,
    response: Any,
) -> Any:
    """校验原中断仍开放，再通过定向 Command 恢复同一图线程。"""

    if not isinstance(interrupt_id, str) or not interrupt_id.strip():
        raise ValueError("interrupt_id 必须是非空字符串")

    config = namespace.as_runnable_config()
    snapshot = await graph.aget_state(config)
    open_interrupt_ids = {
        item.id
        for item in snapshot.interrupts
    }
    if interrupt_id not in open_interrupt_ids:
        raise LookupError(f"目标 interrupt 不存在或已经关闭：{interrupt_id}")

    command = Command(
        resume={
            interrupt_id: deepcopy(response),
        }
    )
    return await graph.ainvoke(command, config)
