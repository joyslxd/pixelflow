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

    async def reconcile_interrupted_runs(self) -> tuple[str, ...]:
        """启动期安全收口上个进程遗留的 Run，恢复必须由 Gateway 创建新的 Run。"""

        return await self._store.fail_unfinished_runs_after_restart()

    async def events_after(self, run_id: str, after_sequence: int):
        """回放公开事件；调用方负责 SSE 编码。"""

        return await self._store.events_after(run_id, after_sequence)

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
            await self._store.transition(run_id, status=RunStatus.RUNNING)
            await self._store.append_event(run_id, "run.started", {"status": "running"})
            result = await self._engine.execute(run_id, request, skill_snapshot)
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
            raise
        except Exception as error:
            diagnostic = (
                error.diagnostic
                if isinstance(error, HarnessExecutionError)
                else None
            )
            logger.warning(
                "sidecar_run_failed run_id=%s exception_type=%s timeout_phase=%s",
                run_id,
                diagnostic.exception_type if diagnostic is not None else type(error).__name__,
                diagnostic.timeout_phase if diagnostic is not None and diagnostic.timeout_phase else "none",
            )
            await self._store.transition(
                run_id,
                status=RunStatus.FAILED,
                termination_reason=TerminationReason.ENGINE_ERROR,
            )
            await self._store.append_event(
                run_id,
                "run.failed",
                {
                    "code": "engine_execution_failed",
                    "exception_type": diagnostic.exception_type if diagnostic is not None else type(error).__name__,
                    "timeout_phase": diagnostic.timeout_phase if diagnostic is not None else None,
                },
            )
        finally:
            async with self._lock:
                self._tasks.pop(run_id, None)
                self._pending_requests.pop(run_id, None)


class RunActivationError(RuntimeError):
    """表示已接受 Run 无法安全激活，禁止在未知输入下自行执行模型。"""


__all__ = ["RunActivationError", "RunRequestConflictError", "RunService"]
