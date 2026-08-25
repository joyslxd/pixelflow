"""External Job Operation 的轮询租约领域入口。"""

from __future__ import annotations

from datetime import datetime

from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRepository, OperationRecord


def _require_scope_text(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} 不能为空")
    if len(normalized) > 64:
        raise ValueError(f"{field} 不能超过 64 个字符")
    return normalized


class OperationLeaseCoordinator:
    """把 operation 的数据库租约限制在当前用户和对话作用域内。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        user_id: str,
        conversation_id: str,
    ) -> None:
        self._repository = repository
        self._user_id = _require_scope_text("user_id", user_id)
        self._conversation_id = _require_scope_text(
            "conversation_id",
            conversation_id,
        )

    async def claim(
        self,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        """领取已到轮询时间的 operation，竞争失败时保持不可见。"""

        return await self._repository.claim_operation_lease(
            self._user_id,
            self._conversation_id,
            job_id,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=lease_expires_at,
        )

    async def heartbeat(
        self,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        """仅允许当前有效持有者把租约严格延长到更晚时间。"""

        return await self._repository.heartbeat_operation_lease(
            self._user_id,
            self._conversation_id,
            job_id,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=lease_expires_at,
        )

    async def schedule_next_poll(
        self,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        next_poll_at: datetime,
    ) -> OperationRecord | None:
        """原子安排下一次轮询并释放当前数据库租约。"""

        return await self._repository.schedule_operation_poll(
            self._user_id,
            self._conversation_id,
            job_id,
            lease_owner=lease_owner,
            now=now,
            next_poll_at=next_poll_at,
        )
