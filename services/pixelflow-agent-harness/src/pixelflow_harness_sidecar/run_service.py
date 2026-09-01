"""协调持久化 Run 状态与真实 DeepSeek Harness Engine 生命周期。"""

from __future__ import annotations

import asyncio
import logging

from .contracts import (
    HarnessRunRequest,
    HarnessRunState,
    RunStatus,
    TerminationReason,
)
from .deepseek_engine import DeepSeekHarnessEngine, HarnessExecutionError
from .event_store import RunRequestConflictError, SqliteRunEventStore
from .skill_snapshot import SkillCatalogSnapshot


logger = logging.getLogger(__name__)


class RunService:
    """类似 Java Application Service：负责 Run 幂等、异步调度和安全事件投影。"""

    def __init__(self, store: SqliteRunEventStore, engine: DeepSeekHarnessEngine) -> None:
        """注入真实 Repository 与 Engine，不接受测试替身作为启动依赖。"""

        self._store = store
        self._engine = engine
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_requests: dict[str, tuple[HarnessRunRequest, SkillCatalogSnapshot]] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, request: HarnessRunRequest) -> HarnessRunState:
        """持久化接受状态，等待 Gateway 写入 binding 后再激活真实模型执行。"""

        self._engine.validate_request(request)
        existing = await self._store.get_by_request(request)
        if existing is not None:
            return existing
        snapshot = self._engine.snapshot_skills()
        state, created = await self._store.create_or_get(
            request,
            engine_id=self._engine.engine_id,
            engine_version=self._engine.engine_version,
            skill_snapshot=snapshot,
        )
        if created:
            async with self._lock:
                self._pending_requests[state.run_id] = (request, snapshot)
        return state

    async def activate_run(self, run_id: str) -> HarnessRunState | None:
        """仅在 Gateway 已持久化 Run binding 后调度一次模型，重复激活只回读。"""

        state = await self._store.get(run_id)
        if state is None:
            return None
        async with self._lock:
            if state.status is not RunStatus.ACCEPTED or run_id in self._tasks:
                return state
            pending = self._pending_requests.get(run_id)
            if pending is None:
                raise RunActivationError("Sidecar 重启后缺少待激活 Run 的瞬态请求")
            request, snapshot = pending
            self._tasks[run_id] = asyncio.create_task(
                self._execute(run_id, request, snapshot),
                name=f"pixelflow-harness-run:{run_id}",
            )
        return state

    async def get_run(self, run_id: str) -> HarnessRunState | None:
        """读取已持久化的公开状态。"""

        return await self._store.get(run_id)

    async def cancel_run(self, run_id: str) -> HarnessRunState | None:
        """取消当前模型循环；已创建的外部 Provider Operation 不在此边界内。"""

        async with self._lock:
            state = await self._store.cancel(run_id)
            if state is None:
                return None
            self._pending_requests.pop(run_id, None)
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
        return state

    async def reconcile_interrupted_runs(self) -> tuple[str, ...]:
        """启动期安全收口上个进程遗留的 Run，恢复必须由 Gateway 创建新的 Run。"""

        return await self._store.fail_unfinished_runs_after_restart()

    async def events_after(self, run_id: str, after_sequence: int):
        """回放公开事件；调用方负责 SSE 编码。"""

        return await self._store.events_after(run_id, after_sequence)

    async def has_event_cursor(self, run_id: str, after_sequence: int) -> bool:
        """校验客户端请求的断点游标属于当前 Run 的已持久化事件范围。"""

        return await self._store.has_cursor(run_id, after_sequence)

    async def aclose(self) -> None:
        """只取消本进程尚未开始或可中止的任务，不伪造业务终态。"""

        async with self._lock:
            tasks = tuple(self._tasks.values())
            self._tasks.clear()
            self._pending_requests.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._store.close()

    async def _execute(
        self,
        run_id: str,
        request: HarnessRunRequest,
        skill_snapshot: SkillCatalogSnapshot,
    ) -> None:
        """执行真实模型并将原始 Harness 轨迹收敛为稳定公开事件。"""

        try:
            started = await self._store.start_if_accepted(run_id)
            if started is None or started.status is not RunStatus.RUNNING:
                return
            # 这是一条产品进度，不是模型推理或内部提示词。它保证模型首包、
            # Skill 加载或网络握手较慢时，浏览器仍能立即向用户说明当前阶段。
            await self._store.append_event(
                run_id,
                "public_summary.delta",
                {"delta": "正在分析你的请求并核对工作区。"},
            )
            loop = asyncio.get_running_loop()

            def append_realtime_event(event_type: str, payload: dict[str, str]) -> None:
                """SDK 回调在工作线程触发；SQLite 写入必须回到 Sidecar 事件循环。"""

                future = asyncio.run_coroutine_threadsafe(
                    self._store.append_event(run_id, event_type, payload),
                    loop,
                )
                future.result(timeout=10)

            result = await self._engine.execute(
                run_id,
                request,
                skill_snapshot,
                on_public_event=append_realtime_event,
            )
            current = await self._store.get(run_id)
            if current is None or current.status is not RunStatus.RUNNING:
                return
            if result.suspension_kind is not None:
                await self._suspend(
                    run_id,
                    result.suspension_kind,
                    interrupt_id=result.suspension_interrupt_id,
                )
                return
            if result.finish_reason != "completed":
                await self._store.transition(
                    run_id,
                    status=RunStatus.FAILED,
                    termination_reason=TerminationReason.ENGINE_ERROR,
                )
                await self._store.append_event(
                    run_id,
                    "run.failed",
                    {"code": "engine_finish_reason_unexpected"},
                )
                return
            for tool_name in result.tool_names:
                await self._store.append_event(
                    run_id,
                    "tool.completed",
                    {"tool_name": tool_name},
                )
            if not result.notification_events_emitted:
                for summary in result.public_summaries:
                    await self._store.append_event(
                        run_id,
                        "public_summary.delta",
                        {"delta": summary},
                    )
            if result.public_summaries:
                await self._store.append_event(
                    run_id,
                    "public_summary.completed",
                    {"summary": "\n".join(result.public_summaries)},
                )
            if not result.notification_events_emitted:
                for delta in result.response_deltas:
                    await self._store.append_event(
                        run_id,
                        "response.delta",
                        {"delta": delta},
                    )
            await self._store.append_event(
                run_id,
                "response.completed",
                {"response": result.final_response},
            )
            await self._store.transition(
                run_id,
                status=RunStatus.COMPLETED,
                termination_reason=TerminationReason.COMPLETED,
            )
            await self._store.append_event(
                run_id,
                "run.completed",
                {"status": "completed"},
            )
        except asyncio.CancelledError:
            # asyncio.to_thread 无法强杀底层 SDK 线程；取消后忽略其结果并由
            # 持久化 Run 状态阻止后续公开事件。外部 Operation 取消归 M5。
            await self._store.cancel(run_id)
            return
        except Exception as error:
            diagnostic = (
                error.diagnostic
                if isinstance(error, HarnessExecutionError)
                else None
            )
            failure_phase = (
                diagnostic.failure_phase if diagnostic is not None else "sidecar_execution"
            )
            logger.warning(
                "sidecar_run_failed run_id=%s exception_type=%s failure_phase=%s failure_reason=%s timeout_phase=%s",
                run_id,
                diagnostic.exception_type if diagnostic is not None else type(error).__name__,
                failure_phase,
                diagnostic.failure_reason if diagnostic is not None and diagnostic.failure_reason else "none",
                diagnostic.timeout_phase if diagnostic is not None and diagnostic.timeout_phase else "none",
            )
            current = await self._store.get(run_id)
            if current is not None and current.status not in {
                RunStatus.CANCELLED,
                RunStatus.COMPLETED,
                RunStatus.FAILED,
            }:
                recovery_required = (
                    diagnostic is not None
                    and diagnostic.failure_phase == "result_projection"
                    and diagnostic.failure_reason == "max_output_tokens_without_public_response"
                )
                await self._store.transition(
                    run_id,
                    status=RunStatus.FAILED,
                    termination_reason=(
                        TerminationReason.MAX_OUTPUT_TOKENS
                        if recovery_required
                        else TerminationReason.ENGINE_ERROR
                    ),
                )
                if recovery_required:
                    # 浏览器只能看到固定恢复码。Gateway Recovery Service 会先检查
                    # 旧 Run 是否已有 Tool 调用，避免在副作用边界不明时自动续跑。
                    await self._store.append_event(
                        run_id,
                        "run.failed",
                        {"code": "harness_run_recovery_required"},
                    )
                    return
                failure_payload = {
                    "code": "engine_execution_failed",
                    "exception_type": diagnostic.exception_type if diagnostic is not None else type(error).__name__,
                    "failure_phase": failure_phase,
                    "failure_reason": diagnostic.failure_reason if diagnostic is not None else None,
                    "timeout_phase": diagnostic.timeout_phase if diagnostic is not None else None,
                }
                if diagnostic is not None:
                    for key, value in (
                        ("failure_code", diagnostic.failure_code),
                        ("failure_type", diagnostic.failure_type),
                        ("failure_category", diagnostic.failure_category),
                    ):
                        if value is not None:
                            failure_payload[key] = value
                await self._store.append_event(
                    run_id,
                    "run.failed",
                    failure_payload,
                )
        finally:
            async with self._lock:
                self._tasks.pop(run_id, None)
                self._pending_requests.pop(run_id, None)

    async def _suspend(
        self,
        run_id: str,
        kind: str,
        *,
        interrupt_id: str | None = None,
    ) -> None:
        """把 Broker 的结构化挂起结果收口为可恢复但不可继续的当前 Run 状态。"""

        mapping = {
            "pending_operation": (RunStatus.SUSPENDED_OPERATION, TerminationReason.SUSPENDED_OPERATION),
            "awaiting_confirmation": (RunStatus.SUSPENDED_CONFIRMATION, TerminationReason.SUSPENDED_CONFIRMATION),
            "authorization_required": (RunStatus.SUSPENDED_AUTHORIZATION, TerminationReason.SUSPENDED_AUTHORIZATION),
        }
        try:
            status, reason = mapping[kind]
        except KeyError as error:
            raise RuntimeError("未知 Tool 挂起状态") from error
        await self._store.transition(run_id, status=status, termination_reason=reason)
        payload: dict[str, str] = {"status": status.value}
        if kind in {"awaiting_confirmation", "authorization_required"}:
            if interrupt_id is None:
                raise RuntimeError("人工挂起缺少中断身份")
            payload["interrupt_id"] = interrupt_id
        await self._store.append_event(run_id, "run.suspended", payload)


class RunActivationError(RuntimeError):
    """表示已接受 Run 无法安全激活，禁止在未知输入下自行执行模型。"""


__all__ = ["RunActivationError", "RunRequestConflictError", "RunService"]
