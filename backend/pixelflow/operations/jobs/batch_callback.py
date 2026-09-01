"""M06 子 Operation 终态到 OperationBatch 的 Gateway 回调。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta

from pixelflow.agent_control_plane.contracts import AgentEvent, ExternalJobStatus
from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
)
from pixelflow.operations.namespace import OperationExecutionNamespace
from pixelflow.video.adapters.operations.projector import (
    build_image_asset_failure_patch,
    build_image_asset_success_patch,
    build_scene_generation_failure_patch,
    build_scene_generation_success_patch,
)
from pixelflow.video.workspace.payload import migrate_workspace_payload
from pixelflow.video.workspace.repository import VideoWorkspaceRepository

from ..ports import OperationConflictError
from .batch_repository import ChildStatus, OperationBatchRepository
from .completion import WorkflowGraphResumePort

_TERMINAL_STATUS = {
    ExternalJobStatus.SUCCEEDED.value: "succeeded",
    ExternalJobStatus.FAILED.value: "failed",
    ExternalJobStatus.TIMEOUT.value: "timeout",
    ExternalJobStatus.EXPIRED.value: "expired",
}
_PATCH_ATTEMPTS = 3
logger = logging.getLogger(__name__)


class OperationBatchTerminalCallback:
    """先投影单子项，再聚合批次；绝不因子项终态创建 Harness Run。"""

    def __init__(
        self,
        *,
        batch_repository: OperationBatchRepository,
        video_repository: VideoWorkspaceRepository,
        operation_repository: AgentRuntimeRepository | None = None,
        fallback_resumer: WorkflowGraphResumePort | None = None,
        on_child_terminal: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._batches = batch_repository
        self._video_repository = video_repository
        self._operations = operation_repository
        self._fallback = fallback_resumer
        self._on_child_terminal = on_child_terminal

    async def resume_external_job(
        self,
        namespace: OperationExecutionNamespace,
        *,
        user_id: str,
        conversation_id: str,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        """消费已落库的 M06 完成事件，并仅在批次全终态后让 Outbox 接棒。"""

        handled = await self.handle_external_job(
            namespace,
            user_id=user_id,
            conversation_id=conversation_id,
            completion_event=completion_event,
            idempotency_key=idempotency_key,
        )
        if handled:
            return
        if self._fallback is None:
            raise OperationConflictError("Operation 完成事件未绑定 OperationBatch")
        await self._fallback.resume_external_job(
            namespace,
            user_id=user_id,
            conversation_id=conversation_id,
            completion_event=completion_event,
            idempotency_key=idempotency_key,
        )

    async def handle_external_job(
        self,
        namespace: OperationExecutionNamespace,
        *,
        user_id: str,
        conversation_id: str,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> bool:
        """若是已绑定子项则消费并返回 True；否则交给常规 M06 恢复处理器。"""

        del namespace

        payload = completion_event.payload
        job_id = _required_text(payload, "job_id")
        status = _required_text(payload, "status")
        child_status = _TERMINAL_STATUS.get(status)
        if (
            child_status is None
            or completion_event.conversation_id != conversation_id
            or idempotency_key != completion_event.event_id
        ):
            raise OperationConflictError("OperationBatch 终态事件身份无效")
        batch = await self._batches.get_batch_for_child_job(
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
        )
        if batch is None:
            # 兼容历史并发冲突产生的占位绑定：Operation 真实 job_id 仍可回读其幂等键。
            operation = None if self._operations is None else await self._operations.get_operation(user_id, job_id)
            if operation is not None:
                batch = await self._batches.get_batch_for_child_job(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    job_id=operation.idempotency_key,
                )
        if batch is None:
            return False
        child = next(
            (
                item
                for item in batch.children
                if item.job_id == job_id or item.operation_idempotency_key == job_id
            ),
            None,
        )
        if child is None:
            raise AgentRuntimeRecordConflictError("OperationBatch Job 绑定漂移")
        await self._project_terminal(
            batch_workspace_id=batch.workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
            status=child_status,
            payload=payload,
            now=completion_event.occurred_at,
        )
        await self._batches.mark_child_terminal(
            batch_id=batch.batch_id,
            child_key=child.operation_idempotency_key,
            status=child_status,
            job_id=job_id,
        )
        if self._on_child_terminal is not None:
            await self._on_child_terminal()
        return True

    async def _project_terminal(self, **kwargs: object) -> None:
        """按 Operation stage 将视频分镜或图片资产结果投影到 Workspace。"""

        payload = kwargs["payload"]
        assert isinstance(payload, Mapping)
        stage = _required_text(payload, "stage")
        if stage.startswith("generate_image_asset:"):
            await self._project_image_asset_terminal(
                batch_workspace_id=str(kwargs["batch_workspace_id"]),
                user_id=str(kwargs["user_id"]),
                asset_stage=stage,
                status=str(kwargs["status"]),
                payload=payload,
                now=kwargs["now"],
            )
            return
        await self._project_scene_terminal(**kwargs)

    async def _project_image_asset_terminal(
        self,
        *,
        batch_workspace_id: str,
        user_id: str,
        asset_stage: str,
        status: str,
        payload: Mapping[str, object],
        now: datetime,
    ) -> None:
        digest = asset_stage.removeprefix("generate_image_asset:").split(":", 1)[0]
        workspace = await self._video_repository.get_workspace(user_id, batch_workspace_id)
        if workspace is None:
            raise AgentRuntimeRecordConflictError("图片资产权威工作区不可用")
        # 终态回调也读取 Repository 的原始 JSON，必须与图片 Worker 使用同一 V2 迁移边界。
        workspace_payload = migrate_workspace_payload(workspace.payload)
        assets = workspace_payload.get("asset_registry", [])
        asset_id = next((str(item.get("asset_id")) for item in assets if isinstance(item, Mapping) and hashlib.sha256(str(item.get("asset_id") or "").encode()).hexdigest()[:12] == digest), None)
        if asset_id is None:
            raise AgentRuntimeRecordConflictError("图片资产终态无法匹配资产身份")
        result = payload.get("result")
        patch = (
            build_image_asset_success_patch(workspace_payload, asset_id=asset_id, result=result, now=now)
            if status == "succeeded" and isinstance(result, Mapping)
            else build_image_asset_failure_patch(workspace_payload, asset_id=asset_id, status=status, reason_code=_optional_text(payload.get("reason_code")), now=now)
        )
        if patch is not None:
            await self._video_repository.apply_workspace_patch(user_id, workspace.workspace_id, patch, expected_revision=workspace.revision, now=now)

    async def owns_operation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ) -> bool:
        """供 Gateway Worker 过滤候选，绝不领取非批次完成事件。"""

        return (
            await self._batches.get_batch_for_child_job(
                user_id=user_id,
                conversation_id=conversation_id,
                job_id=job_id,
            )
        ) is not None

    async def _project_scene_terminal(
        self,
        *,
        batch_workspace_id: str,
        user_id: str,
        conversation_id: str,
        job_id: str,
        status: ChildStatus,
        payload: Mapping[str, object],
        now: datetime,
    ) -> None:
        """只用权威 Workspace 和安全 completion payload 回填分镜进度。"""

        stage = _required_text(payload, "stage")
        if not stage.startswith("generate_scene:"):
            raise OperationConflictError("OperationBatch 暂不支持该 Provider 阶段")
        workspace = await self._video_repository.get_workspace(user_id, batch_workspace_id)
        if workspace is None or workspace.conversation_id != conversation_id:
            raise AgentRuntimeRecordConflictError("OperationBatch 权威工作区不可用")
        workspace_payload = (
            workspace.payload if isinstance(workspace.payload, Mapping) else {}
        )
        if status == "succeeded":
            result = payload.get("result")
            if not isinstance(result, Mapping):
                raise OperationConflictError("镜头成功终态缺少安全结果")
            patch = build_scene_generation_success_patch(
                workspace_payload,
                job_id=job_id,
                result=result,
                now=now,
                stage=stage,
            )
        else:
            patch = build_scene_generation_failure_patch(
                workspace_payload,
                job_id=job_id,
                status=status,
                reason_code=_optional_text(payload.get("reason_code")),
                message=_optional_text(payload.get("message")),
                now=now,
                stage=stage,
            )
        if patch is None:
            return
        current = workspace
        last_error: AgentRuntimeRecordConflictError | None = None
        for _ in range(_PATCH_ATTEMPTS):
            try:
                await self._video_repository.apply_workspace_patch(
                    user_id,
                    current.workspace_id,
                    patch,
                    expected_revision=current.revision,
                    now=now,
                )
                return
            except AgentRuntimeRecordConflictError as exc:
                last_error = exc
                refreshed = await self._video_repository.get_workspace(
                    user_id,
                    current.workspace_id,
                )
                if refreshed is None:
                    raise
                current = refreshed
        assert last_error is not None
        raise last_error


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OperationConflictError("OperationBatch 终态事件字段无效")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


class OperationBatchTerminalWorker:
    """Gateway 生命周期 Worker：只投递已终态的批次子 Operation 完成事件。"""

    def __init__(
        self,
        *,
        operation_repository: AgentRuntimeRepository,
        callback: OperationBatchTerminalCallback,
        worker_id: str,
        clock: Callable[[], datetime] | None = None,
        scan_interval: timedelta = timedelta(seconds=1),
        lease_duration: timedelta = timedelta(seconds=30),
        scan_limit: int = 100,
    ) -> None:
        if not worker_id.strip() or scan_interval <= timedelta(0) or lease_duration <= timedelta(0):
            raise ValueError("OperationBatch 终态 Worker 配置无效")
        self._operations = operation_repository
        self._callback = callback
        self._worker_id = worker_id.strip()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._scan_interval = scan_interval
        self._lease_duration = lease_duration
        self._scan_limit = scan_limit
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def run_once(self) -> int:
        """扫描未确认完成事件；仅批次子项由回调领取，避免吞掉普通 M06 事件。"""

        delivered = 0
        now = self._clock()
        candidates = await self._operations.list_pending_operation_completions(
            now=now,
            limit=self._scan_limit,
        )
        from .completion import OperationCompletionDispatcher

        for candidate in candidates:
            operation = candidate.operation
            if not await self._callback.owns_operation(
                user_id=candidate.user_id,
                conversation_id=operation.conversation_id,
                job_id=operation.job_id,
            ):
                continue
            try:
                await OperationCompletionDispatcher(
                    self._operations,
                    resumer=self._callback,
                    user_id=candidate.user_id,
                    conversation_id=operation.conversation_id,
                    clock=self._clock,
                ).dispatch(
                    operation.job_id,
                    lease_owner=self._worker_id,
                    now=self._clock(),
                    lease_expires_at=self._clock() + self._lease_duration,
                )
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - 单项失败必须留给持久化租约重试。
                logger.warning(
                    "operation_batch_terminal_delivery_failed error_type=%s",
                    type(exc).__name__,
                )
        return delivered

    async def start(self) -> None:
        """启动单个扫描循环，重复调用不会并发重复领取。"""

        if self._closed:
            raise RuntimeError("OperationBatch 终态 Worker 已关闭")
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(),
                name=f"operation-batch-terminal:{self._worker_id}",
            )

    async def aclose(self) -> None:
        """停止轮询并等待当前任务退出。"""

        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while not self._closed:
            try:
                delivered = await self.run_once()
                if delivered == 0:
                    await asyncio.sleep(self._scan_interval.total_seconds())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 周期性扫描应在异常后继续恢复。
                logger.warning(
                    "operation_batch_terminal_worker_failed error_type=%s",
                    type(exc).__name__,
                )
                await asyncio.sleep(self._scan_interval.total_seconds())


__all__ = ["OperationBatchTerminalCallback", "OperationBatchTerminalWorker"]
