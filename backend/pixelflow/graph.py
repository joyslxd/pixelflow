"""PixelFlow 的 LangGraph 状态机装配。

这里把 PixelFlow 的阶段节点串成一个 ``StateGraph``。你可以把它类比成
Spring 里“流程编排 Service”：每个 node 负责一个阶段，条件边负责根据阶段
结果决定下一个 Service 方法。

重点条件边有三类：

1. INTAKE 需求不完整时回到自己继续追问，超过 ``MAX_INTAKE_ROUNDS`` 后终止。
2. Brief 人工确认通过后进入 GENERATE，未通过则回到 CREATIVE 重新策划。
3. QC 通过则结束，失败则回到 GENERATE 重试，最多 ``MAX_QC_ATTEMPTS`` 次。

这里不手动传入 checkpointer；LangGraph 服务会根据 ``langgraph.json`` 注入，
从而让 interrupt 暂停、恢复和持久化统一由平台层处理。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from pixelflow.nodes import (
    MAX_INTAKE_ROUNDS,
    MAX_QC_ATTEMPTS,
    brief_review_node,
    creative_node,
    edit_node,
    generate_node,
    intake_node,
    qc_node,
)
from pixelflow.state import Phase, TaskState


def _route_after_intake(state: TaskState) -> str:
    """INTAKE 后的路由：需求完整就进入策划，不完整则在预算内继续追问。"""
    if state.get("demand_complete", False):
        return "creative"
    if state.get("intake_rounds", 0) >= MAX_INTAKE_ROUNDS:
        return END  # 追问次数耗尽后仍不完整，结束本次任务，避免无限循环。
    return "intake"


def _route_after_brief(state: TaskState) -> str:
    """Brief 人工确认后的路由：批准才允许进入视频生成。"""
    return "generate" if state.get("brief_approved") is True else "creative"


def _route_after_generate(state: TaskState) -> str:
    """GENERATE 后的路由：至少有一个可用片段才进入剪辑。"""
    return "edit" if state.get("generation_ready") is True else END


def _route_after_qc(state: TaskState) -> str:
    """QC 后的路由：通过即结束，失败则在重试预算内回到生成阶段。"""
    if state.get("qc_passed", True):
        return END
    if state.get("qc_attempts", 0) >= MAX_QC_ATTEMPTS:
        return END
    return "generate"


def build_graph() -> StateGraph:
    """构建未编译的 PixelFlow 状态机。

    返回未 compile 的 ``StateGraph``，方便测试直接检查图结构；真正给 LangGraph
    服务使用时会在 ``make_pixelflow_graph`` 中 compile。
    """
    graph = StateGraph(TaskState)

    graph.add_node(Phase.INTAKE, intake_node)
    graph.add_node(Phase.CREATIVE, creative_node)
    graph.add_node(Phase.BRIEF_REVIEW, brief_review_node)
    graph.add_node(Phase.GENERATE, generate_node)
    graph.add_node(Phase.EDIT, edit_node)
    graph.add_node(Phase.QC, qc_node)

    graph.add_edge(START, Phase.INTAKE)
    graph.add_conditional_edges(
        Phase.INTAKE,
        _route_after_intake,
        {"creative": Phase.CREATIVE, "intake": Phase.INTAKE, END: END},
    )
    graph.add_edge(Phase.CREATIVE, Phase.BRIEF_REVIEW)
    graph.add_conditional_edges(
        Phase.BRIEF_REVIEW,
        _route_after_brief,
        {"generate": Phase.GENERATE, "creative": Phase.CREATIVE},
    )
    graph.add_conditional_edges(
        Phase.GENERATE,
        _route_after_generate,
        {"edit": Phase.EDIT, END: END},
    )
    graph.add_edge(Phase.EDIT, Phase.QC)
    graph.add_conditional_edges(
        Phase.QC,
        _route_after_qc,
        {"generate": Phase.GENERATE, END: END},
    )

    return graph


def make_pixelflow_graph(*_args, **_kwargs):
    """LangGraph 入口函数，对应 ``langgraph.json`` 中注册的图。"""
    return build_graph().compile()
