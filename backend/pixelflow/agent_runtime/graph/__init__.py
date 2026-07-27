"""统一 Agent Runtime 图内核的公共合同。"""

from .composition import (
    AGENT_RUNTIME_GRAPH_ID,
    AgentRuntimeGraphComposition,
    build_agent_runtime_graph,
    compose_agent_runtime_graph,
    make_agent_runtime_graph,
)
from .dispatcher import WorkflowCommand, WorkflowCommandDispatcher
from .interrupts import ResumableGraph, resume_graph_from_interrupt
from .namespaces import (
    GraphExecutionNamespace,
    supervisor_namespace,
    workflow_namespace,
)
from .projection import workflow_projection_command
from .registry import (
    FakeWorkflowRegistry,
    WorkflowCommandHandler,
    WorkflowRegistry,
)
from .state import SupervisorState, merge_workflow_records

__all__ = [
    "AGENT_RUNTIME_GRAPH_ID",
    "AgentRuntimeGraphComposition",
    "FakeWorkflowRegistry",
    "GraphExecutionNamespace",
    "ResumableGraph",
    "SupervisorState",
    "WorkflowCommand",
    "WorkflowCommandDispatcher",
    "WorkflowCommandHandler",
    "WorkflowRegistry",
    "build_agent_runtime_graph",
    "compose_agent_runtime_graph",
    "make_agent_runtime_graph",
    "merge_workflow_records",
    "resume_graph_from_interrupt",
    "supervisor_namespace",
    "workflow_projection_command",
    "workflow_namespace",
]
