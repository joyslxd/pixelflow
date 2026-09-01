"""GenerationJob 的进程内短时凭据仓，不把用户授权写入数据库。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential


class TransientGenerationJobCredentialStore:
    """按 GenerationJob 保存 start/status 所需的短租约授权。"""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("GenerationJob 瞬时凭据有效期必须为正")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._values: dict[str, tuple[TransientVideoAgentCredential, datetime]] = {}
        self._lock = asyncio.Lock()

    async def put(self, *, generation_job_id: str, authorization: str) -> None:
        """登记已确认请求的授权，只保存在 Gateway 进程内。"""

        if not generation_job_id.startswith("generation-job-"):
            raise ValueError("GenerationJob 凭据缺少合法任务标识")
        credential = TransientVideoAgentCredential(authorization)
        async with self._lock:
            previous = self._values.pop(generation_job_id, None)
            if previous is not None:
                previous[0].discard()
            self._values[generation_job_id] = (
                credential,
                self._clock() + self._ttl,
            )
            self._discard_expired_locked()

    async def get(
        self,
        *,
        generation_job_id: str,
    ) -> TransientVideoAgentCredential | None:
        """借用任务凭据，供同一任务的 start/status 使用。"""

        async with self._lock:
            self._discard_expired_locked()
            item = self._values.get(generation_job_id)
            return None if item is None else item[0]

    async def discard(self, *, generation_job_id: str) -> None:
        """任务结束后清除授权引用。"""

        async with self._lock:
            item = self._values.pop(generation_job_id, None)
            if item is not None:
                item[0].discard()

    async def aclose(self) -> None:
        """Gateway 退出时清除全部任务凭据。"""

        async with self._lock:
            for credential, _expires_at in self._values.values():
                credential.discard()
            self._values.clear()

    def _discard_expired_locked(self) -> None:
        now = self._clock()
        expired = [
            job_id
            for job_id, (_credential, expires_at) in self._values.items()
            if expires_at <= now
        ]
        for job_id in expired:
            credential, _expires_at = self._values.pop(job_id)
            credential.discard()


__all__ = ["TransientGenerationJobCredentialStore"]
