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
    edit_review_node,
    generate_node,
    intake_node,
    qc_node,
    qc_review_node,
    segment_review_node,
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
    """GENERATE 后的路由：至少有一个可用片段才进入片段人工确认。"""
    return "segment_review" if state.get("generation_ready") is True else END


def _route_after_segment_review(state: TaskState) -> str:
    """片段确认后的路由：批准进入剪辑，拒绝则重新生成。"""
    return "edit" if state.get("segments_approved") is True else "generate"


def _route_after_edit_review(state: TaskState) -> str:
    """剪辑确认后的路由：批准进入质检，拒绝则重新剪辑。"""
    return "qc" if state.get("edit_approved") is True else "edit"


def _route_after_qc_review(state: TaskState) -> str:
    """QC 确认后的路由：批准或重试耗尽则结束，否则回到生成。"""
    if state.get("qc_approved") is True or state.get("qc_attempts", 0) >= MAX_QC_ATTEMPTS:
        return END
    return "generate"


def build_graph() -> StateGraph:
    """构建未编译的 PixelFlow 状态机。

    返回未 compile 的 ``StateGraph``，方便测试直接检查图结构；真正给 LangGraph
    服务使用时会在 ``make_pixelflow_graph`` 中 compile。
    """
    # 创建一个基于 TaskState 的状态图
    graph = StateGraph(TaskState)

    # 向图中添加各个处理节点
    graph.add_node(Phase.INTAKE, intake_node)        # 添加接收节点
    graph.add_node(Phase.CREATIVE, creative_node)    # 添加创意节点
    graph.add_node(Phase.BRIEF_REVIEW, brief_review_node)  # 添加人工审核节点
    graph.add_node(Phase.GENERATE, generate_node)    # 添加生成节点
    graph.add_node(Phase.SEGMENT_REVIEW, segment_review_node)  # 添加片段审核节点
    graph.add_node(Phase.EDIT, edit_node)          # 添加编辑节点
    graph.add_node(Phase.EDIT_REVIEW, edit_review_node)  # 添加剪辑审核节点
    graph.add_node(Phase.QC, qc_node)              # 添加质量控制节点
    graph.add_node(Phase.QC_REVIEW, qc_review_node)  # 添加质检审核节点

    # 添加节点之间的边连接
    graph.add_edge(START, Phase.INTAKE)  # 从开始节点连接到接收节点
    # 添加接收节点后的条件边
    graph.add_conditional_edges(
        Phase.INTAKE,
        _route_after_intake,  # 根据路由函数决定下一个节点
        {"creative": Phase.CREATIVE, "intake": Phase.INTAKE, END: END},  # 可能的下一个节点
    )
    # 从创意节点连接到简报审核节点
    graph.add_edge(Phase.CREATIVE, Phase.BRIEF_REVIEW)
    # 添加简报审核后的条件边
    graph.add_conditional_edges(
        Phase.BRIEF_REVIEW,
        _route_after_brief,  # 根据路由函数决定下一个节点
        {"generate": Phase.GENERATE, "creative": Phase.CREATIVE},  # 可能的下一个节点
    )
    # 添加生成后的条件边
    graph.add_conditional_edges(
        Phase.GENERATE,
        _route_after_generate,
        {"segment_review": Phase.SEGMENT_REVIEW, END: END},
    )
    graph.add_conditional_edges(
        Phase.SEGMENT_REVIEW,
        _route_after_segment_review,
        {"edit": Phase.EDIT, "generate": Phase.GENERATE},
    )
    graph.add_edge(Phase.EDIT, Phase.EDIT_REVIEW)
    graph.add_conditional_edges(
        Phase.EDIT_REVIEW,
        _route_after_edit_review,
        {"qc": Phase.QC, "edit": Phase.EDIT},
    )
    graph.add_edge(Phase.QC, Phase.QC_REVIEW)
    graph.add_conditional_edges(
        Phase.QC_REVIEW,
        _route_after_qc_review,
        {"generate": Phase.GENERATE, END: END},
    )

    # 返回构建完成的状态图
    return graph


def make_pixelflow_graph(*_args, **_kwargs):
    """LangGraph 入口函数，对应 ``langgraph.json`` 中注册的图。"""
    return build_graph().compile()
