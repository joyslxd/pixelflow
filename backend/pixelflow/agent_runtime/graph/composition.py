"""统一 Agent Runtime Supervisor 图的最小装配入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, Command

from pixelflow.agent_runtime.contracts import ActionDecision
from pixelflow.agent_runtime.supervisor import (
    ANSWER_ONLY_NODE,
    CLARIFICATION_NODE,
    ROUTE_ACTION_NODE,
    WORKFLOW_COMMAND_NODE,
    DecisionValidator,
    SupervisorActionRouter,
)

from .dispatcher import WorkflowCommandDispatcher
from .projection import workflow_projection_command
from .registry import FakeWorkflowRegistry, WorkflowRegistry
from .state import SupervisorState

AGENT_RUNTIME_GRAPH_ID = "pixelflow_agent_runtime"
DISPATCH_WORKFLOW_NODE = WORKFLOW_COMMAND_NODE


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
    *,
    validator: DecisionValidator | None = None,
) -> StateGraph:
    """把已冻结的派发器和投影原语组合成最小 Supervisor 图。"""

    router = SupervisorActionRouter(
        validator=validator or DecisionValidator(),
    )

    def route_action(state: SupervisorState) -> Command:
        """校验当前决策并选择唯一合法动作分支。"""

        return router.route(state)

    def save_answer_only(state: SupervisorState) -> dict[str, Any]:
        """把非推进型回答追加到消息状态。"""

        return router.save_answer_only(state)

    def open_clarification(state: SupervisorState) -> dict[str, Any]:
        """打开追问 interrupt，等待定向恢复。"""

        return router.open_clarification(state)

    async def dispatch_workflow(state: SupervisorState):
        """派发当前决策，并把结果写回同一会话的 Workflow 投影。"""

        conversation_id = state.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("Supervisor state 必须提供 conversation_id")
        decision_value = state.get("decision")
        if decision_value is None:
            raise ValueError("Supervisor state 必须提供 ActionDecision")
        decision = ActionDecision.model_validate(decision_value)
        dispatch_workflow_id = state.get("dispatch_workflow_id")
        if not isinstance(dispatch_workflow_id, str):
            raise ValueError("业务命令必须提供已校验的 dispatch_workflow_id")
        preallocated_workflow_id = dispatch_workflow_id if decision.action.value == "start_workflow" else None
        if preallocated_workflow_id is None and dispatch_workflow_id != decision.target_workflow_id:
            raise ValueError("业务命令目标与已校验路由不一致")
        workflow = await dispatcher.dispatch(
            state,
            decision,
            preallocated_workflow_id=preallocated_workflow_id,
        )
        projection = workflow_projection_command(
            workflow,
            conversation_id=conversation_id,
        )
        update = dict(projection.update)
        update["dispatch_workflow_id"] = None
        return Command(update=update, goto=projection.goto)

    graph = StateGraph(SupervisorState)
    graph.add_node(
        ROUTE_ACTION_NODE,
        route_action,
        destinations=(
            ANSWER_ONLY_NODE,
            CLARIFICATION_NODE,
            DISPATCH_WORKFLOW_NODE,
        ),
    )
    graph.add_node(ANSWER_ONLY_NODE, save_answer_only)
    graph.add_node(CLARIFICATION_NODE, open_clarification)
    graph.add_node(DISPATCH_WORKFLOW_NODE, dispatch_workflow)
    graph.add_edge(START, ROUTE_ACTION_NODE)
    graph.add_edge(ANSWER_ONLY_NODE, END)
    graph.add_edge(CLARIFICATION_NODE, END)
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
