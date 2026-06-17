"""PixelFlow LangGraph assembly.

Wires the five pipeline phases into a StateGraph with two conditional edges:
the Brief review gate (approve -> generate, revise -> creative) and the QC loop
(pass -> end, fail -> regenerate, bounded by MAX_QC_ATTEMPTS).

The graph is compiled WITHOUT a checkpointer: the LangGraph server injects one
via ``langgraph.json`` so interrupts and persistence work at the platform level.
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
    if state.get("demand_complete", False):
        return "creative"
    if state.get("intake_rounds", 0) >= MAX_INTAKE_ROUNDS:
        return END  # demand still incomplete after the follow-up budget: give up
    return "intake"


def _route_after_brief(state: TaskState) -> str:
    return "generate" if state.get("brief_approved") is True else "creative"


def _route_after_generate(state: TaskState) -> str:
    return "segment_review" if state.get("generation_ready") is True else END


def _route_after_segment_review(state: TaskState) -> str:
    return "edit" if state.get("segments_approved") is True else "generate"


def _route_after_edit_review(state: TaskState) -> str:
    return "qc" if state.get("edit_approved") is True else "edit"


def _route_after_qc_review(state: TaskState) -> str:
    if state.get("qc_approved") is True or state.get("qc_attempts", 0) >= MAX_QC_ATTEMPTS:
        return END
    return "generate"


def build_graph() -> StateGraph:
    """Build the uncompiled PixelFlow StateGraph."""
    graph = StateGraph(TaskState)

    graph.add_node(Phase.INTAKE, intake_node)
    graph.add_node(Phase.CREATIVE, creative_node)
    graph.add_node(Phase.BRIEF_REVIEW, brief_review_node)
    graph.add_node(Phase.GENERATE, generate_node)
    graph.add_node(Phase.SEGMENT_REVIEW, segment_review_node)
    graph.add_node(Phase.EDIT, edit_node)
    graph.add_node(Phase.EDIT_REVIEW, edit_review_node)
    graph.add_node(Phase.QC, qc_node)
    graph.add_node(Phase.QC_REVIEW, qc_review_node)

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

    return graph


def make_pixelflow_graph(*_args, **_kwargs):
    """LangGraph entrypoint (see ``langgraph.json``)."""
    return build_graph().compile()
