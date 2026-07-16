"""剪映草稿生成任务的异步、幂等 Service。"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from .models import JianyingDraftRequest, JianyingDraftResult, JianyingDraftStatus
from .skill import JianyingDraftCapability, JianyingDraftSkill

logger = logging.getLogger(__name__)

_MAX_JOBS = 100
_MAX_REPLACED_JOBS = _MAX_JOBS
_DEFAULT_TIMEOUT_SECONDS = 1800.0
_TERMINAL_STATUSES = {
    JianyingDraftStatus.SUCCEEDED,
    JianyingDraftStatus.FAILED,
    JianyingDraftStatus.TIMEOUT,
    JianyingDraftStatus.NOT_CONFIGURED,
}
_PUBLIC_PROVIDER_MESSAGES = {
    JianyingDraftStatus.FAILED: "剪映草稿生成失败，请稍后重试。",
    JianyingDraftStatus.TIMEOUT: "剪映草稿生成超时，请重试。",
    JianyingDraftStatus.NOT_CONFIGURED: "剪映草稿服务待接入",
}


@dataclass
class _JianyingDraftJob:
    key: tuple[str, str]
    result: JianyingDraftResult
    completed_at: datetime | None = None
    replaced_by_job_id: str | None = None
    task: asyncio.Task[None] | None = None
    terminal_experience_claimed: bool = False


@dataclass
class _ReplacedJianyingDraftJob:
    result: JianyingDraftResult
    replaced_by_job_id: str
    terminal_experience_claimed: bool = False


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
        self._replaced_jobs: OrderedDict[str, _ReplacedJianyingDraftJob] = OrderedDict()
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def job_count(self) -> int:
        """当前仍可查询的任务数。"""
        return len(self._jobs)

    async def capability(self) -> JianyingDraftCapability:
        """查询当前 Provider 能力，关闭后的 Service 不再暴露可用能力。"""

        async with self._lock:
            if self._closed:
                return JianyingDraftCapability(
                    available=False,
                    reason="剪映草稿服务暂不可用",
                )
        return await self._skill.capability()

    async def aclose(self) -> None:
        """拒绝新任务，并取消等待中的后台生成任务。"""

        async with self._lock:
            self._closed = True
            tasks = [
                job.task
                for job in self._jobs.values()
                if job.task is not None and not job.task.done()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start(
        self,
        request: JianyingDraftRequest,
        *,
        retry_failed: bool = False,
    ) -> JianyingDraftResult:
        """复用已有任务，或为一个新的分镜版本启动后台生成。"""
        key = (request.conversation_id, request.storyboard_version_id)
        async with self._lock:
            if self._closed:
                return JianyingDraftResult(
                    status=JianyingDraftStatus.NOT_CONFIGURED,
                    message="剪映草稿服务暂不可用，请稍后重试",
                )
            reusable_result = self._reusable_result(
                key,
                retry_failed=retry_failed,
            )
            if reusable_result is not None:
                return reusable_result

        capability = None
        capability_error_type: str | None = None
        try:
            capability = await self.capability()
        except Exception as exc:  # noqa: BLE001 - provider boundary must not leak details
            capability_error_type = type(exc).__name__

        async with self._lock:
            if self._closed:
                return JianyingDraftResult(
                    status=JianyingDraftStatus.NOT_CONFIGURED,
                    message="剪映草稿服务暂不可用，请稍后重试",
                )
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

            recyclable_job_id = (
                replaced_job.result.job_id if replaced_job is not None else None
            )
            has_room, reclaimed_job = self._make_room_for_new_job(
                recyclable_job_id=recyclable_job_id
            )
            if not has_room:
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
                if reclaimed_job is replaced_job:
                    previous_job_id = replaced_job.result.job_id
                    if previous_job_id is not None:
                        self._remember_replaced_job(
                            previous_job_id,
                            _ReplacedJianyingDraftJob(
                                result=replaced_job.result.model_copy(deep=True),
                                replaced_by_job_id=job_id,
                                terminal_experience_claimed=replaced_job.terminal_experience_claimed,
                            ),
                        )
                else:
                    replaced_job.replaced_by_job_id = job_id
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
            if job is not None:
                return job.result.model_copy(deep=True)
            replaced_job = self._replaced_jobs.get(job_id)
            return (
                replaced_job.result.model_copy(deep=True)
                if replaced_job is not None
                else None
            )

    async def _replaced_job_count(self) -> int:
        """仅供测试确认被替换任务历史的容量边界。"""

        async with self._lock:
            return len(self._replaced_jobs)

    async def claim_terminal_experience(self, job_id: str) -> bool:
        """原子领取终态经验写入权，避免并发轮询重复记录 PowerMem。"""

        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                if (
                    job.result.status not in _TERMINAL_STATUSES
                    or job.terminal_experience_claimed
                ):
                    return False
                job.terminal_experience_claimed = True
                return True

            replaced_job = self._replaced_jobs.get(job_id)
            if (
                replaced_job is None
                or replaced_job.result.status not in _TERMINAL_STATUSES
                or replaced_job.terminal_experience_claimed
            ):
                return False
            replaced_job.terminal_experience_claimed = True
            return True

    async def _get_replaced_by_job_id(self, job_id: str) -> str | None:
        """仅供 Service 内部测试读取失败任务的替代关系。"""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return job.replaced_by_job_id
            replaced_job = self._replaced_jobs.get(job_id)
            return (
                replaced_job.replaced_by_job_id if replaced_job is not None else None
            )

    def _get_current_job(self, key: tuple[str, str]) -> _JianyingDraftJob | None:
        job_id = self._job_ids_by_key.get(key)
        return self._jobs.get(job_id) if job_id is not None else None

    def _remember_replaced_job(
        self,
        job_id: str,
        replaced_job: _ReplacedJianyingDraftJob,
    ) -> None:
        """按替换顺序保留有限历史，淘汰后不再可查询或领取经验写入权。"""

        self._replaced_jobs[job_id] = replaced_job
        self._replaced_jobs.move_to_end(job_id)
        while len(self._replaced_jobs) > _MAX_REPLACED_JOBS:
            self._replaced_jobs.popitem(last=False)

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

    def _make_room_for_new_job(
        self,
        *,
        recyclable_job_id: str | None,
    ) -> tuple[bool, _JianyingDraftJob | None]:
        reclaimed_job: _JianyingDraftJob | None = None
        while len(self._jobs) >= _MAX_JOBS:
            terminal_jobs = [
                (job_id, job)
                for job_id, job in self._jobs.items()
                if job.completed_at is not None
            ]
            if not terminal_jobs:
                return False, None
            job_id, job = min(
                terminal_jobs,
                key=lambda item: item[1].completed_at,
            )
            self._jobs.pop(job_id)
            if job_id == recyclable_job_id:
                reclaimed_job = job
            if self._job_ids_by_key.get(job.key) == job_id:
                self._job_ids_by_key.pop(job.key)
        return True, reclaimed_job

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
            result = self._public_provider_result(generated)

        await self._store_result(job_id, request, result)

    @staticmethod
    def _public_provider_result(result: JianyingDraftResult) -> JianyingDraftResult:
        message = _PUBLIC_PROVIDER_MESSAGES.get(result.status)
        if message is not None:
            return JianyingDraftResult(status=result.status, message=message)
        if result.status in {JianyingDraftStatus.QUEUED, JianyingDraftStatus.RUNNING}:
            return JianyingDraftResult(
                status=JianyingDraftStatus.FAILED,
                message=_PUBLIC_PROVIDER_MESSAGES[JianyingDraftStatus.FAILED],
            )
        return result

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
