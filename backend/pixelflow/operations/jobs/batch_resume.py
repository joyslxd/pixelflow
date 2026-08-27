"""将 OperationBatch 完成 Outbox 投递为唯一 operation_resume Run。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from .batch_repository import OperationBatchOutboxRecord, SQLOperationBatchRepository


class OperationBatchResumePort(Protocol):
    """Gateway 实现该 Port，基于 completion_event_id 冻结上下文并创建 Sidecar Run。"""

    async def create_operation_resume(self, event: OperationBatchOutboxRecord) -> str:
        """必须将 event.completion_event_id 用作 trigger_id 与 Run 幂等输入。"""


class OperationBatchResumeDispatcher:
    """类似 M06 Outbox Worker：领取、创建恢复 Run、确认；未知结果留给租约恢复。"""

    def __init__(
        self,
        *,
        repository: SQLOperationBatchRepository,
        resume_port: OperationBatchResumePort,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=30),
    ) -> None:
        if not worker_id.strip() or lease_duration <= timedelta(0):
            raise ValueError("OperationBatch Resume Dispatcher 配置无效")
        self._repository = repository
        self._resume_port = resume_port
        self._worker_id = worker_id
        self._lease_duration = lease_duration

    async def deliver_next(self, *, now: datetime) -> OperationBatchOutboxRecord | None:
        """一次只投递一个批次事件，Run 创建失败不确认，由租约超时后的 Worker 重试。"""

        event = await self._repository.claim_completion(
            worker_id=self._worker_id,
            now=now,
            lease_duration=self._lease_duration,
        )
        if event is None:
            return None
        run_id = await self._resume_port.create_operation_resume(event)
        if not run_id.startswith("hrun_"):
            raise ValueError("operation_resume Run 身份无效")
        return await self._repository.acknowledge_completion(
            completion_event_id=event.completion_event_id,
            worker_id=self._worker_id,
            resume_run_id=run_id,
            now=now,
        )


__all__ = ["OperationBatchResumeDispatcher", "OperationBatchResumePort"]
