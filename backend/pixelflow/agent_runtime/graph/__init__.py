"""统一 Agent Runtime 图内核的公共合同。"""

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
    "FakeWorkflowRegistry",
    "GraphExecutionNamespace",
    "ResumableGraph",
    "SupervisorState",
    "WorkflowCommand",
    "WorkflowCommandDispatcher",
    "WorkflowCommandHandler",
    "WorkflowRegistry",
    "merge_workflow_records",
    "resume_graph_from_interrupt",
    "supervisor_namespace",
    "workflow_projection_command",
    "workflow_namespace",
]
