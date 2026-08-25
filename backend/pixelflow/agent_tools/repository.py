"""持久化 Gateway 认可的 Run Binding 与 Tool Call Observation。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from pixelflow.agent_control_plane.persistence.models import (
    PixelFlowAgentHarnessRecoveryRow,
    PixelFlowAgentHarnessRunBindingRow,
    PixelFlowAgentHarnessToolCallRow,
)


@dataclass(frozen=True, slots=True)
class RunBinding:
    """表示 Tool Broker 回查 owner 所需的不可变 Run 绑定。"""

    run_id: str
    session_id: str
    user_id: str
    conversation_id: str
    workspace_id: str
    workspace_revision: int
    context_digest: str
    toolset_version: str
    tool_manifest_digest: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class HarnessRecoveryRecord:
    """记录一个原 Run 至多触发一次的恢复事件与其后继 Run。"""

    original_run_id: str
    recovery_event_id: str
    status: str
    recovery_run_id: str | None


class AgentToolBindingConflictError(RuntimeError):
    """表示既有 Run/Tool 身份携带了不同冻结摘要。"""


class SQLAgentToolRepository:
    """类似 Repository：所有 Tool owner 与幂等身份都从 SQL 读取，而非 Sidecar 入参。"""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def register_run_binding(self, binding: RunBinding) -> RunBinding:
        """原子写入或回读 Run binding；同 run_id 的任何漂移都失败关闭。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessRunBindingRow, binding.run_id)
                if row is None:
                    session.add(
                        PixelFlowAgentHarnessRunBindingRow(
                            **asdict(binding),
                        )
                    )
                    return binding
                existing = self._binding_from_row(row)
                if existing != binding:
                    raise AgentToolBindingConflictError("同一 Sidecar Run 的绑定信息不一致")
                return existing

    async def get_run_binding(self, run_id: str) -> RunBinding | None:
        """按 run_id 回查权威 owner binding。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowAgentHarnessRunBindingRow, run_id)
            return None if row is None else self._binding_from_row(row)

    async def get_or_create_recovery_event(self, original_run_id: str) -> HarnessRecoveryRecord:
        """先持久化唯一恢复事件；同一原 Run 重试始终回读相同恢复身份。"""

        recovery_event_id = "hrecovery_" + hashlib.sha256(
            f"v1:harness-recovery:{original_run_id}".encode(),
        ).hexdigest()[:32]
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessRecoveryRow, original_run_id)
                if row is None:
                    row = PixelFlowAgentHarnessRecoveryRow(
                        original_run_id=original_run_id,
                        recovery_event_id=recovery_event_id,
                        status="pending",
                    )
                    session.add(row)
                    return self._recovery_from_row(row)
                return self._recovery_from_row(row)

    async def mark_recovery_manual_review(self, original_run_id: str) -> HarnessRecoveryRecord:
        """无法安全重建恢复上下文时留下稳定人工核对状态，不自动重跑。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessRecoveryRow, original_run_id)
                if row is None:
                    raise LookupError("Harness recovery event 不存在")
                if row.status == "created":
                    return self._recovery_from_row(row)
                row.status = "manual_review"
                return self._recovery_from_row(row)

    async def bind_recovery_run(
        self,
        *,
        original_run_id: str,
        recovery_run_id: str,
    ) -> HarnessRecoveryRecord:
        """把已经接受的后继 Run 绑定到唯一恢复事件，身份漂移必须失败关闭。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessRecoveryRow, original_run_id)
                if row is None:
                    raise LookupError("Harness recovery event 不存在")
                if row.status == "created":
                    if row.recovery_run_id != recovery_run_id:
                        raise AgentToolBindingConflictError("同一恢复事件绑定了不同后继 Run")
                    return self._recovery_from_row(row)
                if row.status != "pending":
                    raise AgentToolBindingConflictError("人工核对状态禁止自动创建恢复 Run")
                row.status = "created"
                row.recovery_run_id = recovery_run_id
                return self._recovery_from_row(row)

    async def get_or_save_tool_response(
        self,
        *,
        tool_call_key: str,
        run_id: str,
        tool_call_id: str,
        request_digest: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """按稳定 Tool Call 身份保存或回放 Observation，摘要漂移必须失败关闭。"""

        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.get(PixelFlowAgentHarnessToolCallRow, tool_call_key)
                    if row is not None:
                        if row.request_digest != request_digest:
                            raise AgentToolBindingConflictError("同一 Tool Call 的请求摘要不一致")
                        return dict(row.response_json)
                    session.add(
                        PixelFlowAgentHarnessToolCallRow(
                            tool_call_key=tool_call_key,
                            run_id=run_id,
                            tool_call_id=tool_call_id,
                            request_digest=request_digest,
                            response_json=dict(response),
                        )
                    )
                    return dict(response)
            except IntegrityError:
                await session.rollback()
        async with self._session_factory() as session:
            row = await session.get(PixelFlowAgentHarnessToolCallRow, tool_call_key)
            if row is None or row.request_digest != request_digest:
                raise AgentToolBindingConflictError("并发 Tool Call 未能获得一致 Observation")
            return dict(row.response_json)

    async def get_tool_response(
        self,
        *,
        tool_call_key: str,
        request_digest: str,
    ) -> dict[str, Any] | None:
        """在执行业务副作用前回读已完成 Observation，避免重放再次修改工作区。"""

        async with self._session_factory() as session:
            row = await session.get(PixelFlowAgentHarnessToolCallRow, tool_call_key)
            if row is None:
                return None
            if row.request_digest != request_digest:
                raise AgentToolBindingConflictError("同一 Tool Call 的请求摘要不一致")
            return dict(row.response_json)

    async def has_tool_calls(self, run_id: str) -> bool:
        """恢复前确认原 Run 是否已到达任何业务 Tool Ledger；未知边界必须人工核对。"""

        statement = select(PixelFlowAgentHarnessToolCallRow.tool_call_key).where(
            PixelFlowAgentHarnessToolCallRow.run_id == run_id,
        ).limit(1)
        async with self._session_factory() as session:
            return (await session.scalar(statement)) is not None

    @staticmethod
    def request_digest(*, binding: RunBinding, tool_name: str, arguments: dict[str, Any], expected_revision: int) -> str:
        """按方案计算 Tool 请求摘要，不把用户正文或凭据纳入输入。"""

        payload = {
            "protocol_version": "v1",
            "session_id": binding.session_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "expected_workspace_revision": expected_revision,
            "context_digest": binding.context_digest,
            "toolset_version": binding.toolset_version,
        }
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def tool_call_key(*, run_id: str, tool_call_id: str) -> str:
        """计算稳定 Tool Call 身份。"""

        return "sha256:" + hashlib.sha256(f"{run_id}:{tool_call_id}".encode()).hexdigest()

    @staticmethod
    def _binding_from_row(row: PixelFlowAgentHarnessRunBindingRow) -> RunBinding:
        return RunBinding(
            run_id=row.run_id,
            session_id=row.session_id,
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            workspace_id=row.workspace_id,
            workspace_revision=row.workspace_revision,
            context_digest=row.context_digest,
            toolset_version=row.toolset_version,
            tool_manifest_digest=row.tool_manifest_digest,
            request_digest=row.request_digest,
        )

    @staticmethod
    def _recovery_from_row(row: PixelFlowAgentHarnessRecoveryRow) -> HarnessRecoveryRecord:
        return HarnessRecoveryRecord(
            original_run_id=row.original_run_id,
            recovery_event_id=row.recovery_event_id,
            status=row.status,
            recovery_run_id=row.recovery_run_id,
        )


async def ensure_sql_agent_tool_schema(engine: object) -> None:
    """启动期创建 M0 新增的 Run binding/Tool Call 表，不修改既有业务表。"""

    async with engine.begin() as connection:  # type: ignore[union-attr]
        await connection.run_sync(
            lambda sync_connection: PixelFlowAgentHarnessRunBindingRow.metadata.create_all(
                sync_connection,
                tables=[
                    PixelFlowAgentHarnessRunBindingRow.__table__,
                    PixelFlowAgentHarnessToolCallRow.__table__,
                    PixelFlowAgentHarnessRecoveryRow.__table__,
                ],
            ),
        )
