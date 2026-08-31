"""Gateway 进程内的 Content-App 用户 Authorization 短期租约。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta


class TransientContentAppAuthorizationStore:
    """按用户与 Provider Job 保存短时授权；禁止序列化、落库和日志输出。"""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Content-App 临时授权租约必须为正")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._users: dict[str, tuple[str, datetime]] = {}
        self._jobs: dict[str, tuple[str, datetime]] = {}
        self._lock = asyncio.Lock()

    async def put_user(self, *, user_id: str, authorization: str) -> None:
        """在已认证浏览器请求中刷新当前用户租约。"""

        normalized_user, normalized_authorization = _validate(user_id, authorization)
        async with self._lock:
            self._discard_expired_locked()
            self._users[normalized_user] = (
                normalized_authorization,
                self._clock() + self._ttl,
            )

    async def put_job(self, *, provider_job_id: str, authorization: str) -> None:
        """把创建异步任务的同一用户授权绑定到 Provider Job。"""

        normalized_job, normalized_authorization = _validate(provider_job_id, authorization)
        async with self._lock:
            self._discard_expired_locked()
            self._jobs[normalized_job] = (
                normalized_authorization,
                self._clock() + self._ttl,
            )

    async def borrow(self, *, provider_job_id: str, user_id: str) -> str:
        """优先读取任务租约；Gateway 重启后可回退到该用户最新浏览器授权。"""

        normalized_job, _ = _validate(provider_job_id, "Bearer placeholder")
        normalized_user, _ = _validate(user_id, "Bearer placeholder")
        async with self._lock:
            self._discard_expired_locked()
            item = self._jobs.get(normalized_job) or self._users.get(normalized_user)
            if item is None:
                raise LookupError("content_app_authorization_unavailable")
            return item[0]

    async def discard_job(self, *, provider_job_id: str) -> None:
        normalized_job, _ = _validate(provider_job_id, "Bearer placeholder")
        async with self._lock:
            self._jobs.pop(normalized_job, None)

    async def aclose(self) -> None:
        async with self._lock:
            self._users.clear()
            self._jobs.clear()

    def _discard_expired_locked(self) -> None:
        now = self._clock()
        for values in (self._users, self._jobs):
            expired = [key for key, (_authorization, expires_at) in values.items() if expires_at <= now]
            for key in expired:
                values.pop(key, None)


def _validate(identifier: str, authorization: str) -> tuple[str, str]:
    normalized_identifier = identifier.strip() if isinstance(identifier, str) else ""
    normalized_authorization = authorization.strip() if isinstance(authorization, str) else ""
    if not normalized_identifier or len(normalized_identifier) > 255:
        raise ValueError("Content-App 临时授权身份无效")
    if not normalized_authorization.startswith("Bearer ") or not normalized_authorization.removeprefix("Bearer ").strip():
        raise ValueError("Content-App 临时授权无效")
    return normalized_identifier, normalized_authorization


__all__ = ["TransientContentAppAuthorizationStore"]
