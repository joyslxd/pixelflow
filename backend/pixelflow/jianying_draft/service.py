"""剪映草稿生成任务的异步、幂等 Service。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from .models import JianyingDraftRequest, JianyingDraftResult, JianyingDraftStatus
from .skill import JianyingDraftSkill

logger = logging.getLogger(__name__)

_MAX_JOBS = 100
_DEFAULT_TIMEOUT_SECONDS = 60.0
_TERMINAL_STATUSES = {
    JianyingDraftStatus.SUCCEEDED,
    JianyingDraftStatus.FAILED,
    JianyingDraftStatus.TIMEOUT,
    JianyingDraftStatus.NOT_CONFIGURED,
}


@dataclass
class _JianyingDraftJob:
    key: tuple[str, str]
    result: JianyingDraftResult
    completed_at: datetime | None = None
    task: asyncio.Task[None] | None = None


class JianyingDraftService:
    """管理同一对话分镜版本的单个可恢复生成任务。"""

    def __init__(
        self,
        *,
        skill: JianyingDraftSkill,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._skill = skill
        self._timeout_seconds = timeout_seconds
        self._jobs: dict[str, _JianyingDraftJob] = {}
        self._job_ids_by_key: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    @property
    def job_count(self) -> int:
        """当前仍可查询的任务数。"""
        return len(self._jobs)

    async def start(
        self,
        request: JianyingDraftRequest,
        *,
        retry_failed: bool = False,
    ) -> JianyingDraftResult:
        """复用已有任务，或为一个新的分镜版本启动后台生成。"""
        key = (request.conversation_id, request.storyboard_version_id)
        async with self._lock:
            reusable_result = self._reusable_result(
                key,
                retry_failed=retry_failed,
            )
            if reusable_result is not None:
                return reusable_result

        capability = None
        capability_error_type: str | None = None
        try:
            capability = await self._skill.capability()
        except Exception as exc:  # noqa: BLE001 - provider boundary must not leak details
            capability_error_type = type(exc).__name__

        async with self._lock:
            reusable_result = self._reusable_result(
                key,
                retry_failed=retry_failed,
            )
            if reusable_result is not None:
                return reusable_result

            if capability_error_type is not None:
                logger.error(
                    "[pixelflow] jianying draft capability check failed error_type=%s",
                    capability_error_type,
                )
                return JianyingDraftResult(
                    status=JianyingDraftStatus.NOT_CONFIGURED,
                    message="剪映草稿服务暂不可用，请稍后重试",
                )
            if capability is None or not capability.available:
                return JianyingDraftResult(
                    status=JianyingDraftStatus.NOT_CONFIGURED,
                    message="剪映草稿服务待接入",
                )

            previous = self._get_current_job(key)
            replaced_job: _JianyingDraftJob | None = None
            if (
                previous is not None
                and retry_failed
                and previous.result.status
                in {JianyingDraftStatus.FAILED, JianyingDraftStatus.TIMEOUT}
            ):
                replaced_job = previous

            preserved_job_id = (
                replaced_job.result.job_id if replaced_job is not None else None
            )
            if not self._make_room_for_new_job(preserved_job_id=preserved_job_id):
                return JianyingDraftResult(
                    status=JianyingDraftStatus.FAILED,
                    message="剪映草稿任务繁忙，请稍后重试",
                )

            job_id = uuid4().hex
            result = JianyingDraftResult(
                status=JianyingDraftStatus.QUEUED,
                job_id=job_id,
                conversation_id=request.conversation_id,
                storyboard_version_id=request.storyboard_version_id,
            )
            job = _JianyingDraftJob(
                key=key,
                result=result,
            )
            self._jobs[job_id] = job
            self._job_ids_by_key[key] = job_id
            if replaced_job is not None:
                replaced_job.result = replaced_job.result.model_copy(
                    update={"replaced_by_job_id": job_id}
                )
            job.task = asyncio.create_task(self._run(job_id, request))
            return result.model_copy(deep=True)

    def _reusable_result(
        self,
        key: tuple[str, str],
        *,
        retry_failed: bool,
    ) -> JianyingDraftResult | None:
        previous = self._get_current_job(key)
        if previous is None:
            return None
        if self._should_reuse(previous.result, retry_failed=retry_failed):
            return previous.result.model_copy(deep=True)
        return None

    async def get_job(self, job_id: str) -> JianyingDraftResult | None:
        """查询任务当前状态；不存在或已清理时返回 ``None``。"""
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.result.model_copy(deep=True) if job is not None else None

    def _get_current_job(self, key: tuple[str, str]) -> _JianyingDraftJob | None:
        job_id = self._job_ids_by_key.get(key)
        return self._jobs.get(job_id) if job_id is not None else None

    def _should_reuse(
        self,
        result: JianyingDraftResult,
        *,
        retry_failed: bool,
    ) -> bool:
        if result.status in {JianyingDraftStatus.QUEUED, JianyingDraftStatus.RUNNING}:
            return True
        if result.status == JianyingDraftStatus.SUCCEEDED:
            return not self._is_expired(result)
        if result.status in {JianyingDraftStatus.FAILED, JianyingDraftStatus.TIMEOUT}:
            return not retry_failed
        return True

    @staticmethod
    def _is_expired(result: JianyingDraftResult) -> bool:
        if result.expire_at is None:
            return False
        return datetime.now(result.expire_at.tzinfo) >= result.expire_at

    def _make_room_for_new_job(self, *, preserved_job_id: str | None) -> bool:
        while len(self._jobs) >= _MAX_JOBS:
            terminal_jobs = [
                (job_id, job)
                for job_id, job in self._jobs.items()
                if job_id != preserved_job_id and job.completed_at is not None
            ]
            if not terminal_jobs:
                return False
            job_id, job = min(
                terminal_jobs,
                key=lambda item: item[1].completed_at,
            )
            self._jobs.pop(job_id)
            if self._job_ids_by_key.get(job.key) == job_id:
                self._job_ids_by_key.pop(job.key)
        return True

    async def _run(self, job_id: str, request: JianyingDraftRequest) -> None:
        await self._set_running(job_id)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                generated = await self._skill.generate(request)
        except TimeoutError:
            result = JianyingDraftResult(
                status=JianyingDraftStatus.TIMEOUT,
                message="剪映草稿生成超时，请稍后重试",
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary must not fail background task
            logger.error(
                "[pixelflow] jianying draft generation failed job_id=%s error_type=%s",
                job_id,
                type(exc).__name__,
            )
            result = JianyingDraftResult(
                status=JianyingDraftStatus.FAILED,
                message="剪映草稿生成失败，请稍后重试",
            )
        else:
            result = generated

        await self._store_result(job_id, request, result)

    async def _set_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.result = job.result.model_copy(
                    update={"status": JianyingDraftStatus.RUNNING}
                )

    async def _store_result(
        self,
        job_id: str,
        request: JianyingDraftRequest,
        result: JianyingDraftResult,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.result = result.model_copy(
                update={
                    "job_id": job_id,
                    "conversation_id": request.conversation_id,
                    "storyboard_version_id": request.storyboard_version_id,
                }
            )
            if job.result.status in _TERMINAL_STATUSES:
                job.completed_at = datetime.now()
