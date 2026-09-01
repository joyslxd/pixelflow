"""Gateway GenerationJob Worker：直接 start、poll Provider 并回写 Workspace。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue

from pixelflow.capabilities.image_generation.port import ImageGenerationProvider
from pixelflow.capabilities.video_generation.port import VideoGenerationProvider
from pixelflow.generation_jobs.providers import ProviderJobOutcome, ProviderJobSnapshot
from pixelflow.video.workspace.repository import VideoWorkspaceRepository

from .contracts import GenerationJobKind, GenerationJobRecord, GenerationJobStatus
from .credentials import TransientGenerationJobCredentialStore
from .projector import (
    build_image_asset_failure_patch,
    build_image_asset_success_patch,
    build_scene_generation_failure_patch,
    build_scene_generation_success_patch,
)
from .repository import GenerationJobRepository

logger = logging.getLogger(__name__)


class GenerationJobWorker:
    """单一 Gateway Worker，最多并行领取 6 个 start 或 poll 子任务。"""

    def __init__(
        self,
        *,
        repository: GenerationJobRepository,
        workspace_repository: VideoWorkspaceRepository,
        credential_store: TransientGenerationJobCredentialStore,
        image_provider: ImageGenerationProvider | None = None,
        video_provider: VideoGenerationProvider | None = None,
        worker_id: str,
        scan_interval: timedelta = timedelta(seconds=1),
        poll_interval: timedelta = timedelta(seconds=3),
        lease_duration: timedelta = timedelta(seconds=30),
        clock=None,
    ) -> None:
        if not worker_id.strip() or scan_interval <= timedelta(0) or poll_interval <= timedelta(0):
            raise ValueError("GenerationJob Worker 配置无效")
        if lease_duration <= timedelta(0):
            raise ValueError("GenerationJob Worker lease 必须为正")
        self._jobs = repository
        self._workspaces = workspace_repository
        self._credentials = credential_store
        self._providers = {
            GenerationJobKind.IMAGE: image_provider,
            GenerationJobKind.VIDEO: video_provider,
        }
        self._worker_id = worker_id.strip()
        self._scan_interval = scan_interval
        self._poll_interval = poll_interval
        self._lease_duration = lease_duration
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """提交新任务后立即触发一次扫描。"""

        self._wake.set()

    async def run_once(self) -> int:
        """领取并处理当前最多 6 个 start 与 6 个 due poll 任务。"""

        now = self._clock()
        start_jobs = await self._jobs.claim_start_jobs(
            worker_id=self._worker_id,
            now=now,
            lease_duration=self._lease_duration,
            limit=6,
        )
        poll_jobs = ()
        if len(start_jobs) < 6:
            poll_jobs = await self._jobs.claim_poll_jobs(
                worker_id=self._worker_id,
                now=now,
                lease_duration=self._lease_duration,
                limit=6 - len(start_jobs),
            )
        await asyncio.gather(
            *(self._start(job) for job in start_jobs),
            *(self._poll(job) for job in poll_jobs),
        )
        return len(start_jobs) + len(poll_jobs)

    async def start(self) -> None:
        """启动 Gateway 内部后台扫描。"""

        if self._closed:
            raise RuntimeError("GenerationJob Worker 已关闭")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"generation-job:{self._worker_id}")

    async def aclose(self) -> None:
        """停止扫描并释放 Worker 持有的内存引用。"""

        self._closed = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _start(self, job: GenerationJobRecord) -> None:
        provider = self._providers.get(job.kind)
        credential = await self._credentials.get(generation_job_id=job.generation_job_id)
        if provider is None:
            await self._finish_failure(job, GenerationJobStatus.FAILED, "provider_unavailable")
            return
        if credential is None:
            await self._finish_failure(job, GenerationJobStatus.INDETERMINATE, "authorization_unavailable")
            return
        try:
            snapshot = await provider.start(
                job.request_json,
                authorization=credential.borrow_authorization(),
                idempotency_key=job.idempotency_key,
            )
            await self._accept_snapshot(job, snapshot, phase="start")
        except Exception as exc:  # noqa: BLE001 - Provider 原始异常不得进入日志或 Workspace。
            logger.warning(
                "generation_job_start_failed job_id=%s kind=%s error_type=%s",
                job.generation_job_id,
                job.kind.value,
                type(exc).__name__,
            )
            await self._finish_failure(job, GenerationJobStatus.INDETERMINATE, "provider_start_indeterminate")

    async def _poll(self, job: GenerationJobRecord) -> None:
        provider = self._providers.get(job.kind)
        if provider is None or not job.provider_job_id:
            await self._finish_failure(job, GenerationJobStatus.INDETERMINATE, "provider_job_unavailable")
            return
        credential = await self._credentials.get(generation_job_id=job.generation_job_id)
        if credential is None:
            await self._finish_failure(job, GenerationJobStatus.INDETERMINATE, "authorization_unavailable")
            return
        try:
            snapshot = await provider.status(
                job.provider_job_id,
                user_id=job.user_id,
                conversation_id=job.conversation_id,
            )
            if snapshot.outcome is ProviderJobOutcome.POLLING:
                await self._jobs.reschedule_poll(
                    generation_job_id=job.generation_job_id,
                    worker_id=self._worker_id,
                    now=self._clock(),
                    next_poll_at=self._clock() + self._poll_interval,
                )
                return
            await self._accept_snapshot(job, snapshot, phase="poll")
        except Exception as exc:  # noqa: BLE001 - 单轮失败释放 lease，下一轮安全重试 status。
            logger.warning(
                "generation_job_poll_failed job_id=%s kind=%s error_type=%s",
                job.generation_job_id,
                job.kind.value,
                type(exc).__name__,
            )
            try:
                await self._jobs.reschedule_poll(
                    generation_job_id=job.generation_job_id,
                    worker_id=self._worker_id,
                    now=self._clock(),
                    next_poll_at=self._clock() + self._poll_interval,
                )
            except Exception as lease_exc:  # noqa: BLE001
                logger.warning(
                    "generation_job_poll_reschedule_failed job_id=%s error_type=%s",
                    job.generation_job_id,
                    type(lease_exc).__name__,
                )

    async def _accept_snapshot(
        self,
        job: GenerationJobRecord,
        snapshot: ProviderJobSnapshot,
        *,
        phase: str,
    ) -> None:
        now = self._clock()
        if snapshot.outcome is ProviderJobOutcome.POLLING:
            if not snapshot.provider_job_id:
                await self._finish_failure(job, GenerationJobStatus.INDETERMINATE, f"provider_{phase}_job_id_missing")
                return
            await self._jobs.bind_provider_job(
                generation_job_id=job.generation_job_id,
                provider_job_id=snapshot.provider_job_id,
                worker_id=self._worker_id,
                now=now,
                next_poll_at=now + self._poll_interval,
            )
            return
        if snapshot.outcome is ProviderJobOutcome.SUCCEEDED:
            result = _result_mapping(snapshot.result)
            if not result:
                await self._finish_failure(job, GenerationJobStatus.INDETERMINATE, "provider_result_missing")
                return
            await self._finish(job, GenerationJobStatus.SUCCEEDED, result, None)
            return
        reason = snapshot.reason_code or "provider_business_failed"
        await self._finish_failure(job, GenerationJobStatus.FAILED, reason)

    async def _finish_failure(
        self,
        job: GenerationJobRecord,
        status: GenerationJobStatus,
        reason_code: str,
    ) -> None:
        await self._finish(job, status, None, reason_code)

    async def _finish(
        self,
        job: GenerationJobRecord,
        status: GenerationJobStatus,
        result: dict[str, JsonValue] | None,
        reason_code: str | None,
    ) -> None:
        completed = await self._jobs.complete(
            generation_job_id=job.generation_job_id,
            worker_id=self._worker_id,
            status=status,
            now=self._clock(),
            result_json=result,
            failure_reason_code=reason_code,
        )
        await self._project(completed)
        await self._credentials.discard(generation_job_id=job.generation_job_id)

    async def _project(self, job: GenerationJobRecord) -> None:
        for _attempt in range(3):
            workspace = await self._workspaces.get_workspace(job.user_id, job.workspace_id)
            if workspace is None or workspace.conversation_id != job.conversation_id:
                logger.warning("generation_job_workspace_unavailable job_id=%s", job.generation_job_id)
                return
            patch = _workspace_patch(job, workspace.payload, self._clock())
            if patch is None:
                return
            try:
                await self._workspaces.apply_workspace_patch(
                    job.user_id,
                    job.workspace_id,
                    patch,
                    expected_revision=workspace.revision,
                    now=self._clock(),
                )
                return
            except Exception as exc:  # noqa: BLE001 - 冲突时按最新 Workspace 重建补丁。
                if _attempt == 2:
                    logger.warning(
                        "generation_job_workspace_projection_failed job_id=%s error_type=%s",
                        job.generation_job_id,
                        type(exc).__name__,
                    )

    async def _run(self) -> None:
        while not self._closed:
            try:
                await self.run_once()
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self._scan_interval.total_seconds()
                    )
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Worker 单轮失败后必须继续。
                logger.warning(
                    "generation_job_worker_failed error_type=%s",
                    type(exc).__name__,
                )
                await asyncio.sleep(self._scan_interval.total_seconds())


def _result_mapping(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(key, str)
    }


def _workspace_patch(
    job: GenerationJobRecord,
    payload: Mapping[str, JsonValue],
    now: datetime,
) -> dict[str, JsonValue] | None:
    result = job.result_json or {}
    if job.kind is GenerationJobKind.IMAGE:
        if job.status is GenerationJobStatus.SUCCEEDED:
            return build_image_asset_success_patch(payload, asset_id=job.item_id, result=result, now=now)
        return build_image_asset_failure_patch(
            payload,
            asset_id=job.item_id,
            status=job.status.value,
            reason_code=job.failure_reason_code,
            now=now,
        )
    if job.status is GenerationJobStatus.SUCCEEDED:
        return build_scene_generation_success_patch(
            payload,
            job_id=job.generation_job_id,
            result=result,
            now=now,
        )
    return build_scene_generation_failure_patch(
        payload,
        job_id=job.generation_job_id,
        status=job.status.value,
        reason_code=job.failure_reason_code,
        now=now,
    )


__all__ = ["GenerationJobWorker"]
