"""统一 Agent Runtime 图内核的公共合同。"""

from .dispatcher import WorkflowCommand, WorkflowCommandDispatcher
from .namespaces import (
    GraphExecutionNamespace,
    supervisor_namespace,
    workflow_namespace,
)
from .registry import (
    FakeWorkflowRegistry,
    WorkflowCommandHandler,
    WorkflowRegistry,
)
from .state import SupervisorState, merge_workflow_records

__all__ = [
    "FakeWorkflowRegistry",
    "GraphExecutionNamespace",
    "SupervisorState",
    "WorkflowCommand",
    "WorkflowCommandDispatcher",
    "WorkflowCommandHandler",
    "WorkflowRegistry",
    "merge_workflow_records",
    "supervisor_namespace",
    "workflow_namespace",
]
