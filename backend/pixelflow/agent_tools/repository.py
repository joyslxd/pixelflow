"""持久化 Gateway 认可的 Run Binding 与 Tool Call Observation。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from pixelflow.agent_control_plane.persistence.models import (
    PixelFlowAgentHarnessInterruptRow,
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


@dataclass(frozen=True, slots=True)
class HarnessInterruptRecord:
    """一个 Tool Call 唯一对应一个可公开响应的 M5 人工中断。"""

    interrupt_id: str
    tool_call_key: str
    run_id: str
    user_id: str
    conversation_id: str
    workspace_id: str
    workspace_revision: int
    kind: str
    status: str
    payload: dict[str, Any]
    response_id: str | None
    resumed_run_id: str | None


class ToolCallClaimState(StrEnum):
    """Tool Call 的执行权状态；必须先领取再触碰业务副作用。"""

    CLAIMED = "claimed"
    COMPLETED = "completed"
    EXECUTING = "executing"


@dataclass(frozen=True, slots=True)
class ToolCallClaim:
    """Tool Call ledger 的领取结果，不把执行中的占位记录暴露给模型。"""

    state: ToolCallClaimState
    response: dict[str, Any] | None = None


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

    async def get_or_create_interrupt(
        self,
        *,
        tool_call_key: str,
        binding: RunBinding,
        kind: str,
        payload: dict[str, Any],
    ) -> HarnessInterruptRecord:
        """按 Tool Call 身份创建或回读中断；重复模型请求不得生成第二个确认按钮。"""

        if kind not in {"awaiting_confirmation", "authorization_required"}:
            raise ValueError("Harness Interrupt 类型不受支持")
        interrupt_id = "hint_" + hashlib.sha256(
            f"v1:harness-interrupt:{tool_call_key}".encode(),
        ).hexdigest()[:32]
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessInterruptRow, interrupt_id)
                if row is None:
                    row = PixelFlowAgentHarnessInterruptRow(
                        interrupt_id=interrupt_id,
                        tool_call_key=tool_call_key,
                        run_id=binding.run_id,
                        user_id=binding.user_id,
                        conversation_id=binding.conversation_id,
                        workspace_id=binding.workspace_id,
                        workspace_revision=binding.workspace_revision,
                        kind=kind,
                        payload_json=dict(payload),
                    )
                    session.add(row)
                    return self._interrupt_from_row(row)
                record = self._interrupt_from_row(row)
                expected = (tool_call_key, binding.run_id, kind, payload)
                actual = (record.tool_call_key, record.run_id, record.kind, record.payload)
                if actual != expected:
                    raise AgentToolBindingConflictError("同一 Harness 中断身份发生漂移")
                return record

    async def get_run_binding_by_interrupt(self, interrupt_id: str) -> RunBinding | None:
        """从权威中断回查原 Run binding，浏览器不得直接指定恢复来源。"""

        async with self._session_factory() as session:
            interrupt = await session.get(PixelFlowAgentHarnessInterruptRow, interrupt_id)
            if interrupt is None:
                return None
            binding = await session.get(PixelFlowAgentHarnessRunBindingRow, interrupt.run_id)
            return None if binding is None else self._binding_from_row(binding)

    async def is_tool_confirmation_granted(self, *, run_id: str, tool_name: str) -> bool:
        """只允许指定 confirmation_resume Run 执行其已确认的同名 Tool。"""

        statement = select(PixelFlowAgentHarnessInterruptRow).where(
            PixelFlowAgentHarnessInterruptRow.resumed_run_id == run_id,
            PixelFlowAgentHarnessInterruptRow.status == "responded",
            PixelFlowAgentHarnessInterruptRow.kind == "awaiting_confirmation",
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return any(row.payload_json.get("tool_name") == tool_name for row in rows)

    async def respond_interrupt(
        self,
        *,
        interrupt_id: str,
        binding: RunBinding,
        client_response_id: str,
        expected_workspace_revision: int,
    ) -> HarnessInterruptRecord:
        """以客户端响应 ID 幂等确认中断，revision 漂移必须在创建恢复 Run 前失败关闭。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessInterruptRow, interrupt_id)
                if row is None or row.user_id != binding.user_id or row.conversation_id != binding.conversation_id:
                    raise LookupError("Harness 中断不存在或不属于当前会话")
                if row.workspace_id != binding.workspace_id or row.run_id != binding.run_id:
                    raise AgentToolBindingConflictError("Harness 中断绑定发生漂移")
                if row.response_id is not None:
                    if row.response_id != client_response_id:
                        raise AgentToolBindingConflictError("Harness 中断已由其他响应处理")
                    return self._interrupt_from_row(row)
                if row.status != "open" or row.workspace_revision != expected_workspace_revision:
                    raise AgentToolBindingConflictError("Harness 中断已关闭或工作区版本不一致")
                row.status = "responded"
                row.response_id = client_response_id
                return self._interrupt_from_row(row)

    async def bind_interrupt_resume_run(
        self,
        *,
        interrupt_id: str,
        client_response_id: str,
        resumed_run_id: str,
    ) -> HarnessInterruptRecord:
        """将唯一 confirmation_resume Run 回写给响应记录，重复网络请求只能回读同一 Run。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessInterruptRow, interrupt_id)
                if row is None or row.status != "responded" or row.response_id != client_response_id:
                    raise AgentToolBindingConflictError("Harness 中断响应不存在或不一致")
                if row.resumed_run_id is not None and row.resumed_run_id != resumed_run_id:
                    raise AgentToolBindingConflictError("同一 Harness 中断创建了多个恢复 Run")
                row.resumed_run_id = resumed_run_id
                return self._interrupt_from_row(row)

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

    async def claim_tool_call(
        self,
        *,
        tool_call_key: str,
        run_id: str,
        tool_call_id: str,
        request_digest: str,
    ) -> ToolCallClaim:
        """原子领取 Tool Call 执行权，避免并发请求重复产生业务副作用。"""

        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.get(PixelFlowAgentHarnessToolCallRow, tool_call_key)
                    if row is not None:
                        if row.request_digest != request_digest:
                            raise AgentToolBindingConflictError("同一 Tool Call 的请求摘要不一致")
                        if row.execution_state == "completed":
                            return ToolCallClaim(
                                state=ToolCallClaimState.COMPLETED,
                                response=dict(row.response_json),
                            )
                        return ToolCallClaim(state=ToolCallClaimState.EXECUTING)
                    session.add(
                        PixelFlowAgentHarnessToolCallRow(
                            tool_call_key=tool_call_key,
                            run_id=run_id,
                            tool_call_id=tool_call_id,
                            request_digest=request_digest,
                            execution_state="executing",
                            response_json={},
                        )
                    )
                    return ToolCallClaim(state=ToolCallClaimState.CLAIMED)
            except IntegrityError:
                await session.rollback()
        async with self._session_factory() as session:
            row = await session.get(PixelFlowAgentHarnessToolCallRow, tool_call_key)
            if row is None or row.request_digest != request_digest:
                raise AgentToolBindingConflictError("并发 Tool Call 未能获得一致 Observation")
            if row.execution_state == "completed":
                return ToolCallClaim(
                    state=ToolCallClaimState.COMPLETED,
                    response=dict(row.response_json),
                )
            return ToolCallClaim(state=ToolCallClaimState.EXECUTING)

    async def complete_tool_call(
        self,
        *,
        tool_call_key: str,
        request_digest: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """完成已领取的 Tool Call；未知或摘要漂移均不允许写入结果。"""

        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(PixelFlowAgentHarnessToolCallRow, tool_call_key)
                if row is None or row.request_digest != request_digest:
                    raise AgentToolBindingConflictError("Tool Call 领取记录不存在或请求摘要不一致")
                if row.execution_state == "completed":
                    return dict(row.response_json)
                row.execution_state = "completed"
                row.response_json = dict(response)
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
            if row.execution_state != "completed":
                return None
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

    @staticmethod
    def _interrupt_from_row(row: PixelFlowAgentHarnessInterruptRow) -> HarnessInterruptRecord:
        return HarnessInterruptRecord(
            interrupt_id=row.interrupt_id,
            tool_call_key=row.tool_call_key,
            run_id=row.run_id,
            user_id=row.user_id,
            conversation_id=row.conversation_id,
            workspace_id=row.workspace_id,
            workspace_revision=row.workspace_revision,
            kind=row.kind,
            status=row.status,
            payload=dict(row.payload_json),
            response_id=row.response_id,
            resumed_run_id=row.resumed_run_id,
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
                    PixelFlowAgentHarnessInterruptRow.__table__,
                ],
            ),
        )
        await connection.run_sync(_ensure_tool_call_execution_state_column)


def _ensure_tool_call_execution_state_column(sync_connection: object) -> None:
    """为既有 SQLite/MySQL 表补齐执行状态，create_all 不会变更历史表结构。"""

    from sqlalchemy import inspect, text

    inspector = inspect(sync_connection)
    columns = {column["name"] for column in inspector.get_columns("pixelflow_agent_harness_tool_calls")}
    if "execution_state" not in columns:
        sync_connection.execute(
            text(
                "ALTER TABLE pixelflow_agent_harness_tool_calls "
                "ADD COLUMN execution_state VARCHAR(16) NOT NULL DEFAULT 'completed'",
            ),
        )
