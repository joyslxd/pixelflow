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
    MAX_QC_ATTEMPTS,
    brief_review_node,
    creative_node,
    edit_node,
    generate_node,
    intake_node,
    qc_node,
)
from pixelflow.state import Phase, TaskState


def _route_after_brief(state: TaskState) -> str:
    return "generate" if state.get("brief_approved", True) else "creative"


def _route_after_qc(state: TaskState) -> str:
    if state.get("qc_passed", True):
        return END
    if state.get("qc_attempts", 0) >= MAX_QC_ATTEMPTS:
        return END
    return "generate"


def build_graph() -> StateGraph:
    """Build the uncompiled PixelFlow StateGraph."""
    graph = StateGraph(TaskState)

    graph.add_node(Phase.INTAKE, intake_node)
    graph.add_node(Phase.CREATIVE, creative_node)
    graph.add_node(Phase.BRIEF_REVIEW, brief_review_node)
    graph.add_node(Phase.GENERATE, generate_node)
    graph.add_node(Phase.EDIT, edit_node)
    graph.add_node(Phase.QC, qc_node)

    graph.add_edge(START, Phase.INTAKE)
    graph.add_edge(Phase.INTAKE, Phase.CREATIVE)
    graph.add_edge(Phase.CREATIVE, Phase.BRIEF_REVIEW)
    graph.add_conditional_edges(
        Phase.BRIEF_REVIEW,
        _route_after_brief,
        {"generate": Phase.GENERATE, "creative": Phase.CREATIVE},
    )
    graph.add_edge(Phase.GENERATE, Phase.EDIT)
    graph.add_edge(Phase.EDIT, Phase.QC)
    graph.add_conditional_edges(
        Phase.QC,
        _route_after_qc,
        {"generate": Phase.GENERATE, END: END},
    )

    return graph


def make_pixelflow_graph():
    """LangGraph entrypoint (see ``langgraph.json``)."""
    return build_graph().compile()
