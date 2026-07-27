"""Workflow 处理器注册表合同及其确定性内存实现。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pixelflow.agent_runtime.contracts import WorkflowKind, WorkflowRecord

if TYPE_CHECKING:
    from .dispatcher import WorkflowCommand


@runtime_checkable
class WorkflowCommandHandler(Protocol):
    """接收经过隔离校验的命令，并返回新的工作流投影。"""

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord: ...


@runtime_checkable
class WorkflowRegistry(Protocol):
    """按工作流类型解析唯一处理器。"""

    def resolve(self, kind: WorkflowKind) -> WorkflowCommandHandler: ...


class FakeWorkflowRegistry:
    """供图内核和后续 Adapter 测试使用的确定性内存注册表。"""

    def __init__(
        self,
        handlers: Mapping[WorkflowKind, WorkflowCommandHandler],
    ) -> None:
        self._handlers = dict(handlers)

    def resolve(self, kind: WorkflowKind) -> WorkflowCommandHandler:
        """返回对应处理器；缺失时拒绝隐式选择其他业务类型。"""

        handler = self._handlers.get(kind)
        if handler is None:
            raise LookupError(f"未注册 {kind.value} Workflow 处理器")
        return handler
