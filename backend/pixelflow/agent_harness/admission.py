"""管理多 Gateway 共享的 Sidecar 新 Run 准入状态。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from pixelflow.agent_control_plane.persistence.models import PixelFlowAgentHarnessAdmissionRow

_GLOBAL_SCOPE = "global"
_ALLOWED_REASON_CODES = frozenset(
    {
        "startup_default",
        "manual_open",
        "manual_close",
        "sidecar_unavailable",
        "health_check_failed",
    },
)


class HarnessAdmissionConflictError(RuntimeError):
    """表示准入状态已经被其他 Gateway 更新，调用方必须重新读取。"""


class HarnessAdmissionClosedError(RuntimeError):
    """表示当前全局准入关闭，新 Run 不得回退到旧内核。"""


@dataclass(frozen=True, slots=True)
class HarnessAdmissionState:
    """准入状态的安全快照，不包含异常正文、地址或凭据。"""

    state: str
    reason_code: str
    revision: int
    updated_by: str

    @property
    def is_open(self) -> bool:
        """只有明确 open 才允许创建新的 Sidecar Run。"""

        return self.state == "open"


class SQLHarnessAdmissionRepository:
    """类似 Repository：通过共享数据库与 revision 乐观锁协调所有 Gateway。"""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """注入 PixelFlow 自有数据库会话工厂。"""

        self._session_factory = session_factory

    async def initialize(self, *, initial_open: bool, updated_by: str) -> HarnessAdmissionState:
        """只在首个 Gateway 创建默认状态；后续实例始终回读已有权威值。"""

        initial = HarnessAdmissionState(
            state="open" if initial_open else "closed",
            reason_code="startup_default",
            revision=1,
            updated_by=updated_by,
        )
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.get(PixelFlowAgentHarnessAdmissionRow, _GLOBAL_SCOPE)
                    if row is None:
                        session.add(
                            PixelFlowAgentHarnessAdmissionRow(
                                scope_key=_GLOBAL_SCOPE,
                                state=initial.state,
                                reason_code=initial.reason_code,
                                revision=initial.revision,
                                updated_by=initial.updated_by,
                            )
                        )
                        return initial
                    return self._state_from_row(row)
            except IntegrityError:
                await session.rollback()
        current = await self.get()
        if current is None:
            raise HarnessAdmissionConflictError("准入状态初始化冲突后无法回读")
        return current

    async def get(self) -> HarnessAdmissionState | None:
        """读取全局准入状态；缺失时调用方必须失败关闭。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowAgentHarnessAdmissionRow, _GLOBAL_SCOPE)
            return None if row is None else self._state_from_row(row)

    async def update_state(
        self,
        *,
        open_for_new_runs: bool,
        reason_code: str,
        expected_revision: int,
        updated_by: str,
    ) -> HarnessAdmissionState:
        """按 revision 原子切换准入；错误原因仅允许固定代码。"""

        if reason_code not in _ALLOWED_REASON_CODES:
            raise ValueError("准入状态原因代码不在白名单")
        if expected_revision < 1 or not updated_by.strip():
            raise ValueError("准入状态更新参数无效")
        target_state = "open" if open_for_new_runs else "closed"
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(PixelFlowAgentHarnessAdmissionRow)
                    .where(
                        PixelFlowAgentHarnessAdmissionRow.scope_key == _GLOBAL_SCOPE,
                        PixelFlowAgentHarnessAdmissionRow.revision == expected_revision,
                    )
                    .values(
                        state=target_state,
                        reason_code=reason_code,
                        revision=expected_revision + 1,
                        updated_by=updated_by,
                    ),
                )
                if result.rowcount != 1:
                    raise HarnessAdmissionConflictError("准入状态 revision 已过期")
        current = await self.get()
        if current is None:
            raise HarnessAdmissionConflictError("准入状态更新后无法回读")
        return current

    async def require_open(self) -> HarnessAdmissionState:
        """只有权威状态明确开放时才允许新 Run，缺失也按关闭处理。"""

        current = await self.get()
        if current is None or not current.is_open:
            raise HarnessAdmissionClosedError("Harness 新 Run 准入已关闭")
        return current

    @staticmethod
    def _state_from_row(row: PixelFlowAgentHarnessAdmissionRow) -> HarnessAdmissionState:
        return HarnessAdmissionState(
            state=row.state,
            reason_code=row.reason_code,
            revision=row.revision,
            updated_by=row.updated_by,
        )


__all__ = [
    "HarnessAdmissionClosedError",
    "HarnessAdmissionConflictError",
    "HarnessAdmissionState",
    "SQLHarnessAdmissionRepository",
]
