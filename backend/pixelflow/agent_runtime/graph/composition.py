"""统一 Agent Runtime Supervisor 图的最小装配入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, Command, interrupt

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    InterruptResponseRequest,
)
from pixelflow.agent_runtime.supervisor import (
    ANSWER_ONLY_NODE,
    CLARIFICATION_NODE,
    ROUTE_ACTION_NODE,
    WORKFLOW_COMMAND_NODE,
    DecisionValidator,
    SupervisorActionRouter,
)
from pixelflow.agent_workflows.video.live_handler import WorkflowDispatchResult

from .dispatcher import WorkflowCommandDispatcher
from .projection import workflow_projection_command
from .registry import FakeWorkflowRegistry, WorkflowRegistry
from .state import SupervisorState

AGENT_RUNTIME_GRAPH_ID = "pixelflow_agent_runtime"
DISPATCH_WORKFLOW_NODE = WORKFLOW_COMMAND_NODE
WORKFLOW_INTERRUPT_NODE = "workflow_interrupt"
OPERATION_COMPLETION_INTERRUPT_NODE = "operation_completion_interrupt"


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
        result = await dispatcher.dispatch_result(
            state,
            decision,
            preallocated_workflow_id=preallocated_workflow_id,
        )
        projection = workflow_projection_command(
            result.workflow,
            conversation_id=conversation_id,
        )
        update = dict(projection.update)
        update["dispatch_workflow_id"] = None
        # 来源中断身份只允许当前恢复动作消费一次，禁止后续普通动作复用。
        update["source_interrupt_id"] = None
        if result.state is None:
            update["workflow_dispatch_result"] = None
            return Command(update=update, goto=END)
        live_result = WorkflowDispatchResult.model_validate(result)
        update["workflow_dispatch_result"] = live_result.model_dump(mode="json")
        if live_result.update_active_workflow:
            update["active_workflow_id"] = live_result.active_workflow_id
        destination = (
            WORKFLOW_INTERRUPT_NODE
            if live_result.interrupt is not None
            else END
        )
        return Command(update=update, goto=destination)

    def resume_workflow_interrupt(state: SupervisorState) -> Command:
        """恢复原 Turn，并把经服务端校验的人工动作送回同一 Workflow。"""

        raw_result = state.get("workflow_dispatch_result")
        if not isinstance(raw_result, dict):
            raise ValueError("workflow interrupt 缺少已保存的派发结果")
        result = WorkflowDispatchResult.model_validate(raw_result)
        opened = result.interrupt
        if opened is None:
            raise ValueError("workflow interrupt 缺少开放中断")
        response = interrupt(
            {
                "type": opened.kind,
                "interrupt_id": opened.interrupt_id,
                "reason_code": opened.reason_code,
                "payload": opened.model_dump(mode="json")["payload"],
            }
        )
        return _resume_workflow_command(
            state,
            result=result,
            response=response,
        )

    def stage_operation_completion_interrupt(
        state: SupervisorState,
    ) -> dict[str, Any]:
        """承接已写入 checkpoint 的 Operation 完成结果并进入统一人工中断节点。"""

        del state
        return {}

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
    graph.add_node(
        DISPATCH_WORKFLOW_NODE,
        dispatch_workflow,
        destinations=(WORKFLOW_INTERRUPT_NODE, END),
    )
    graph.add_node(
        WORKFLOW_INTERRUPT_NODE,
        resume_workflow_interrupt,
        destinations=(DISPATCH_WORKFLOW_NODE,),
    )
    graph.add_node(
        OPERATION_COMPLETION_INTERRUPT_NODE,
        stage_operation_completion_interrupt,
    )
    graph.add_edge(START, ROUTE_ACTION_NODE)
    graph.add_edge(ANSWER_ONLY_NODE, END)
    graph.add_edge(CLARIFICATION_NODE, END)
    graph.add_edge(
        OPERATION_COMPLETION_INTERRUPT_NODE,
        WORKFLOW_INTERRUPT_NODE,
    )
    return graph


def _resume_workflow_command(
    state: SupervisorState,
    *,
    result: WorkflowDispatchResult,
    response: Any,
) -> Command:
    """校验恢复值与 checkpoint 权威证据绑定后重建原 Turn 输入。"""

    if type(response) is not dict:
        raise ValueError("workflow interrupt 恢复值必须是对象")
    expected_keys = {
        "client_response_id",
        "decision",
        "interrupt_id",
        "stage",
        "value",
        "workflow_id",
    }
    if set(response) != expected_keys:
        raise ValueError("workflow interrupt 恢复值字段不合法")
    opened = result.interrupt
    if opened is None:
        raise ValueError("workflow interrupt 已关闭")
    if response["interrupt_id"] != opened.interrupt_id:
        raise ValueError("workflow interrupt 身份不一致")
    if response["workflow_id"] != opened.workflow_id:
        raise ValueError("workflow interrupt 的 workflow_id 不一致")
    stage = response["stage"]
    pending = result.workflow.pending_external_job
    allowed_stages = {result.workflow.current_stage}
    if pending is not None and pending.stage:
        allowed_stages.add(pending.stage)
    if stage not in allowed_stages or opened.payload.get("stage") != stage:
        raise ValueError("workflow interrupt 的 stage 已过期")

    request = InterruptResponseRequest.model_validate(
        {
            "client_response_id": response["client_response_id"],
            "value": response["value"],
        }
    )
    decision = ActionDecision.model_validate(response["decision"])
    explicit = request.value.explicit_action
    if explicit is None:
        raise ValueError("workflow interrupt 缺少显式动作")
    if (
        decision.target_workflow_id != opened.workflow_id
        or decision.target_stage != stage
        or explicit.workflow_id != opened.workflow_id
        or explicit.stage != stage
        or explicit.action != decision.action
        or explicit.intent != decision.intent
        or explicit.artifact_ref != decision.target_artifact_ref
        or explicit.patch != decision.patch
    ):
        raise ValueError("workflow interrupt 的决策证据不一致")
    if decision.idempotency_key != f"decision:{request.client_response_id}":
        raise ValueError("workflow interrupt 的决策幂等键不一致")
    if state.get("turn_id") != opened.turn_id:
        raise ValueError("workflow interrupt 不属于当前 Turn")

    return Command(
        update={
            "current_input": request.value.content,
            "materials": [dict(item) for item in request.value.materials],
            "reply_to_message_id": request.value.reply_to_message_id,
            "artifact_refs": list(request.value.artifact_refs),
            "decision": decision.model_copy(deep=True),
            "dispatch_workflow_id": opened.workflow_id,
            "workflow_dispatch_result": None,
            "last_interrupt_response_id": str(request.client_response_id),
            "source_interrupt_id": opened.interrupt_id,
        },
        goto=DISPATCH_WORKFLOW_NODE,
    )


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
