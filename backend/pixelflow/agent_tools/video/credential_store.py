"""Gateway 进程内的短时 Run 凭据仓，禁止持久化用户 Authorization。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .credentials import TransientVideoAgentCredential


class TransientRunCredentialStore:
    """确认 Controller 与 Tool Broker 间一次性传递凭据，类似单次消费的服务内票据。"""

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
        """写入新 Run 的一次性凭据；覆盖时先清理旧对象。"""

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
        """在 Sidecar 激活前暂存确认请求凭据，随后必须绑定到唯一 Run。"""

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
        """在 Gateway 写完 Run binding 后、Sidecar activate 前转移凭据。"""

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
        """创建 Run 失败时主动清除未绑定凭据。"""

        async with self._lock:
            item = self._values.pop(grant_id, None)
            if item is not None:
                item[0].discard()

    async def take(self, run_id: str) -> TransientVideoAgentCredential | None:
        """一次性取走当前 Run 的凭据；过期或不存在均返回空。"""

        async with self._lock:
            self._discard_expired_locked()
            item = self._values.pop(run_id, None)
            return None if item is None else item[0]

    async def aclose(self) -> None:
        """Gateway 退出时清除仍在内存中的所有凭据。"""

        async with self._lock:
            for credential, _expires_at in self._values.values():
                credential.discard()
            self._values.clear()

    def _discard_expired_locked(self) -> None:
        now = self._clock()
        expired = [run_id for run_id, (_credential, expires_at) in self._values.items() if expires_at <= now]
        for run_id in expired:
            credential, _expires_at = self._values.pop(run_id)
            credential.discard()


class TransientBatchCredentialStore:
    """批次调度的进程内短期凭据租约，重启后必须由用户重新授权。"""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("批次瞬时凭据有效期必须为正")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._values: dict[str, tuple[TransientVideoAgentCredential, datetime]] = {}
        self._lock = asyncio.Lock()

    async def put(self, *, batch_id: str, authorization: str) -> None:
        """在已确认的当前请求中登记凭据；只保存在 Gateway 进程内。"""

        if not batch_id.startswith("operation-batch-"):
            raise ValueError("批次凭据缺少合法 batch_id")
        credential = TransientVideoAgentCredential(authorization)
        async with self._lock:
            previous = self._values.pop(batch_id, None)
            if previous is not None:
                previous[0].discard()
            self._values[batch_id] = (credential, self._clock() + self._ttl)
            self._discard_expired_locked()

    async def get(self, *, batch_id: str) -> TransientVideoAgentCredential | None:
        """借用当前批次凭据，不转移所有权，供同一批次后续槽位继续 start。"""

        async with self._lock:
            self._discard_expired_locked()
            item = self._values.get(batch_id)
            return None if item is None else item[0]

    async def discard(self, *, batch_id: str) -> None:
        """所有子项已离开 queued/start 状态后立即清除凭据。"""

        async with self._lock:
            item = self._values.pop(batch_id, None)
            if item is not None:
                item[0].discard()

    async def aclose(self) -> None:
        async with self._lock:
            for credential, _expires_at in self._values.values():
                credential.discard()
            self._values.clear()

    def _discard_expired_locked(self) -> None:
        now = self._clock()
        expired = [batch_id for batch_id, (_credential, expires_at) in self._values.items() if expires_at <= now]
        for batch_id in expired:
            credential, _expires_at = self._values.pop(batch_id)
            credential.discard()


__all__ = ["TransientBatchCredentialStore", "TransientRunCredentialStore"]
