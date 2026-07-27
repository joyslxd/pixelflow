"""统一 Agent Runtime Supervisor 图的最小装配入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer

from pixelflow.agent_runtime.contracts import ActionDecision

from .dispatcher import WorkflowCommandDispatcher
from .projection import workflow_projection_command
from .registry import FakeWorkflowRegistry, WorkflowRegistry
from .state import SupervisorState

AGENT_RUNTIME_GRAPH_ID = "pixelflow_agent_runtime"
DISPATCH_WORKFLOW_NODE = "dispatch_workflow"


@dataclass(slots=True)
class AgentRuntimeGraphComposition:
    """保存一次 Supervisor 图装配产生的稳定运行时对象。"""

    graph_id: str
    graph: Any
    registry: WorkflowRegistry
    dispatcher: WorkflowCommandDispatcher
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def closed(self) -> bool:
        """返回 Gateway 是否已经释放本次图运行时引用。"""

        return self._closed

    async def aclose(self) -> None:
        """幂等关闭装配对象，但不关闭由外层 lifespan 所有的 checkpointer。"""

        self._closed = True


def build_agent_runtime_graph(
    dispatcher: WorkflowCommandDispatcher,
) -> StateGraph:
    """把已冻结的派发器和投影原语组合成最小 Supervisor 图。"""

    async def dispatch_workflow(state: SupervisorState):
        """派发当前决策，并把结果写回同一会话的 Workflow 投影。"""

        conversation_id = state.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("Supervisor state 必须提供 conversation_id")
        decision_value = state.get("decision")
        if decision_value is None:
            raise ValueError("Supervisor state 必须提供 ActionDecision")
        decision = ActionDecision.model_validate(decision_value)
        workflow = await dispatcher.dispatch(state, decision)
        return workflow_projection_command(
            workflow,
            conversation_id=conversation_id,
        )

    graph = StateGraph(SupervisorState)
    graph.add_node(DISPATCH_WORKFLOW_NODE, dispatch_workflow)
    graph.add_edge(START, DISPATCH_WORKFLOW_NODE)
    graph.add_edge(DISPATCH_WORKFLOW_NODE, END)
    return graph


def compose_agent_runtime_graph(
    *,
    registry: WorkflowRegistry | None = None,
    checkpointer: Checkpointer = None,
) -> AgentRuntimeGraphComposition:
    """创建共享 checkpointer 上运行的完整图装配对象。"""

    resolved_registry = registry or FakeWorkflowRegistry({})
    dispatcher = WorkflowCommandDispatcher(resolved_registry)
    graph = build_agent_runtime_graph(dispatcher).compile(
        checkpointer=checkpointer,
    )
    return AgentRuntimeGraphComposition(
        graph_id=AGENT_RUNTIME_GRAPH_ID,
        graph=graph,
        registry=resolved_registry,
        dispatcher=dispatcher,
    )


def make_agent_runtime_graph(
    _config: dict[str, Any] | None = None,
    *,
    registry: WorkflowRegistry | None = None,
    checkpointer: Checkpointer = None,
):
    """为 Gateway、测试和 ``langgraph.json`` 返回同一份编译图。"""

    return compose_agent_runtime_graph(
        registry=registry,
        checkpointer=checkpointer,
    ).graph
