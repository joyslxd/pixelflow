"""Gateway 进程内的短时 Run 凭据仓。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .credentials import TransientVideoAgentCredential


class TransientRunCredentialStore:
    """确认 Controller 与 Tool Broker 间一次性传递凭据。"""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("瞬时 Run 凭据有效期必须为正")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._values: dict[str, tuple[TransientVideoAgentCredential, datetime]] = {}
        self._lock = asyncio.Lock()

    async def put(self, *, run_id: str, authorization: str) -> None:
        """写入新 Run 的一次性凭据。"""

        if not run_id.startswith("hrun_"):
            raise ValueError("Run 凭据缺少合法 run_id")
        credential = TransientVideoAgentCredential(authorization)
        async with self._lock:
            previous = self._values.pop(run_id, None)
            if previous is not None:
                previous[0].discard()
            self._values[run_id] = (credential, self._clock() + self._ttl)
            self._discard_expired_locked()

    async def put_grant(self, *, grant_id: str, authorization: str) -> None:
        """在 Sidecar 激活前暂存确认请求凭据。"""

        if not grant_id.strip() or len(grant_id) > 128:
            raise ValueError("Run 凭据授权票据无效")
        credential = TransientVideoAgentCredential(authorization)
        async with self._lock:
            previous = self._values.pop(grant_id, None)
            if previous is not None:
                previous[0].discard()
            self._values[grant_id] = (credential, self._clock() + self._ttl)
            self._discard_expired_locked()

    async def bind_grant(self, *, grant_id: str, run_id: str) -> None:
        """在 Gateway 写完 Run binding 后转移凭据。"""

        if not run_id.startswith("hrun_"):
            raise ValueError("Run 凭据缺少合法 run_id")
        async with self._lock:
            self._discard_expired_locked()
            item = self._values.pop(grant_id, None)
            if item is None:
                raise LookupError("Run 瞬时凭据已过期或不存在")
            previous = self._values.pop(run_id, None)
            if previous is not None:
                previous[0].discard()
            self._values[run_id] = item

    async def discard_grant(self, grant_id: str) -> None:
        """创建 Run 失败时清除未绑定凭据。"""

        async with self._lock:
            item = self._values.pop(grant_id, None)
            if item is not None:
                item[0].discard()

    async def take(self, run_id: str) -> TransientVideoAgentCredential | None:
        """一次性取走当前 Run 的凭据。"""

        async with self._lock:
            self._discard_expired_locked()
            item = self._values.pop(run_id, None)
            return None if item is None else item[0]

    async def aclose(self) -> None:
        """Gateway 退出时清除内存凭据。"""

        async with self._lock:
            for credential, _expires_at in self._values.values():
                credential.discard()
            self._values.clear()

    def _discard_expired_locked(self) -> None:
        now = self._clock()
        expired = [
            run_id
            for run_id, (_credential, expires_at) in self._values.items()
            if expires_at <= now
        ]
        for run_id in expired:
            credential, _expires_at = self._values.pop(run_id)
            credential.discard()


__all__ = ["TransientRunCredentialStore"]
