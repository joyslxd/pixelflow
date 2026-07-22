"""Workflow Adapter 与后续平台模块之间的稳定异步端口。"""

from typing import Protocol, runtime_checkable

from .contracts import ContextEnvelope, ContextRequest, ExternalJobRef, OperationRequest


class OperationConflictError(RuntimeError):
    """同一幂等键被不同请求摘要复用时拒绝继续。"""


@runtime_checkable
class OperationPort(Protocol):
    """领取、查询和保存可恢复外部任务引用的端口。"""

    async def claim(self, request: OperationRequest) -> ExternalJobRef: ...

    async def get(self, job_id: str) -> ExternalJobRef | None: ...

    async def save(self, job: ExternalJobRef) -> ExternalJobRef: ...


@runtime_checkable
class ContextPort(Protocol):
    """按当前输入和目标 Workflow 组装 ContextEnvelope 的端口。"""

    async def assemble(self, request: ContextRequest) -> ContextEnvelope: ...
