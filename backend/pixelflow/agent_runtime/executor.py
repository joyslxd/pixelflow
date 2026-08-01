"""持续领取、执行并恢复 Supervisor Turn 的进程内编排器。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import JsonValue, ValidationError
from sqlalchemy.exc import OperationalError

from pixelflow.agent_workflows.video.live_capabilities import (
    TransientTurnCredential,
)
from pixelflow.agent_workflows.video.live_handler import (
    VideoLiveStateConflictError,
    WorkflowDispatchResult,
)
from pixelflow.agent_workflows.video.live_operations import (
    TransientCredentialVault,
)
from pixelflow.tasks import AGENT_RUNTIME_CONTEXT_KEY, PixelFlowTaskStore

from .contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    ExplicitActionSignal,
    InterruptResponseRequest,
    TurnRecord,
    TurnStatus,
    WorkflowRecord,
)
from .graph import resume_graph_from_interrupt, supervisor_namespace
from .identity import conversation_message_id, interrupt_id
from .jobs.providers import ProviderJobOutcome
from .persistence import (
    AgentRuntimeRecordConflictError,
    StoredAgentInterrupt,
    SupervisorProjectionMessage,
    TurnExecutionClaim,
    TurnExecutionLeaseConflictError,
    VideoRuntimeRepository,
    VideoTurnCommit,
    VideoWorkflowStateConflictError,
)
from .supervisor import (
    DecisionValidationError,
    DecisionValidationRequest,
    DecisionValidator,
    SupervisorDecisionUnavailableError,
    SupervisorRoutingError,
    SupervisorTurnEvidence,
)

_SAFE_DIMENSION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TRANSIENT_REASON_CODES = frozenset(
    {
        "database_unavailable",
        "executor_infrastructure_unavailable",
        "model_infrastructure_unavailable",
    }
)
_FAILURE_REASON_CODES = frozenset(
    {
        "contract_validation_failed",
        "handler_failed",
        "isolation_violation",
        "state_corrupted",
        "workflow_state_conflict",
    }
)
_M06_STATES = (
    "polling",
    "succeeded",
    "failed",
    "paused_quota",
    "timeout",
    "expired",
)


class SupervisorExecutorClosedError(RuntimeError):
    """Executor 已关闭，不能再接受本进程唤醒。"""


class SupervisorTransientExecutionError(RuntimeError):
    """由基础设施 Adapter 显式抛出的可退避错误。"""

    def __init__(self, reason_code: str) -> None:
        if reason_code not in _TRANSIENT_REASON_CODES:
            raise ValueError("Executor 临时错误必须使用固定安全 reason code")
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SupervisorTurnScope:
    """一次本地唤醒所需的最小稳定身份。"""

    user_id: str
    conversation_id: str
    turn_id: str

    def __post_init__(self) -> None:
        for field_name in ("user_id", "conversation_id", "turn_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{field_name} 必须是无首尾空白的非空字符串")


@dataclass(frozen=True, slots=True)
class _CommittedTurnResult:
    """把 fencing 提交结果带出 heartbeat 生命周期后再做提交后工作。"""

    claim: TurnExecutionClaim
    commit: VideoTurnCommit
    stored: TurnRecord


@runtime_checkable
class SupervisorPostCommitRecorder(Protocol):
    """隔离 PowerMem 的提交后安全摘要端口。"""

    def record_after_commit(
        self,
        record: Mapping[str, JsonValue],
    ) -> Awaitable[None] | None: ...


class _ExecutorRepository(VideoRuntimeRepository, Protocol):
    """组合 Task 4 live 接口与其公开基础查询接口。"""

    async def get_turn(self, user_id: str, turn_id: str) -> TurnRecord | None: ...

    async def list_turns(self, user_id: str, conversation_id: str) -> list[TurnRecord]: ...

    async def list_workflows(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[WorkflowRecord]: ...


class _DecisionService(Protocol):
    async def decide(self, evidence: SupervisorTurnEvidence) -> Any: ...


class _CompiledGraph(Protocol):
    async def ainvoke(self, input: Any, config: dict[str, Any]) -> Any: ...

    async def aget_state(self, config: dict[str, Any]) -> Any: ...


class SupervisorExecutionMetrics:
    """只聚合固定维度计数与耗时，不保存任何业务正文。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._data: dict[str, Any] = {
            "turn_wait_seconds": {"count": 0, "total": 0.0, "maximum": 0.0},
            "turn_claimed": 0,
            "turn_completed": 0,
            "turn_retried": 0,
            "lease_conflicts": 0,
            "actions": {item.value: 0 for item in AgentAction},
            "interrupt_wait_seconds": {"count": 0, "total": 0.0, "maximum": 0.0},
            "stage_duration_seconds": {},
            "external_job_states": {item: 0 for item in _M06_STATES},
            "reason_codes": {},
        }

    def observe_wait(self, seconds: float) -> None:
        self._observe_duration("turn_wait_seconds", seconds)

    def observe_interrupt_wait(self, seconds: float) -> None:
        self._observe_duration("interrupt_wait_seconds", seconds)

    def observe_stage(self, stage: str, seconds: float) -> None:
        key = stage if _SAFE_DIMENSION.fullmatch(stage or "") else "unknown"
        with self._lock:
            target = self._data["stage_duration_seconds"].setdefault(
                key,
                {"count": 0, "total": 0.0, "maximum": 0.0},
            )
            self._update_duration(target, seconds)

    def increment(self, key: str) -> None:
        with self._lock:
            self._data[key] += 1

    def action(self, value: AgentAction) -> None:
        with self._lock:
            self._data["actions"][value.value] += 1

    def observe_external_job_state(self, state: ProviderJobOutcome) -> None:
        """只接受 Provider Adapter 已校验的固定六态 DTO。"""

        if not isinstance(state, ProviderJobOutcome):
            raise TypeError("M06 指标只接受 ProviderJobOutcome")
        with self._lock:
            self._data["external_job_states"][state.value] += 1

    def reason(self, reason_code: str) -> None:
        allowed = _TRANSIENT_REASON_CODES | _FAILURE_REASON_CODES | {
            "authorization_required",
            "error.raised",
        }
        key = reason_code if reason_code in allowed else "unknown"
        with self._lock:
            reasons = self._data["reason_codes"]
            reasons[key] = reasons.get(key, 0) + 1

    def snapshot(self) -> dict[str, JsonValue]:
        with self._lock:
            return json.loads(json.dumps(self._data, allow_nan=False))

    def _observe_duration(self, key: str, seconds: float) -> None:
        with self._lock:
            self._update_duration(self._data[key], seconds)

    @staticmethod
    def _update_duration(target: dict[str, float | int], seconds: float) -> None:
        safe = max(0.0, float(seconds))
        target["count"] += 1
        target["total"] = round(float(target["total"]) + safe, 6)
        target["maximum"] = round(max(float(target["maximum"]), safe), 6)


class SupervisorTurnExecutor:
    """相当于带租约的工作流消费 Service，只提交权威 Repository DTO。"""

    def __init__(
        self,
        *,
        repository: _ExecutorRepository,
        task_store: PixelFlowTaskStore,
        decision_service: _DecisionService,
        graph: _CompiledGraph,
        credential_vault: TransientCredentialVault,
        clock: Callable[[], datetime] | None = None,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=30),
        heartbeat_step: timedelta = timedelta(seconds=10),
        heartbeat_interval_seconds: float = 10.0,
        scan_interval_seconds: float = 1.0,
        post_commit_recorder: SupervisorPostCommitRecorder | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id 必须是非空字符串")
        if lease_duration <= timedelta(0) or heartbeat_step <= timedelta(0):
            raise ValueError("租约与 heartbeat 步长必须大于零")
        if heartbeat_interval_seconds <= 0 or scan_interval_seconds <= 0:
            raise ValueError("扫描与 heartbeat 间隔必须大于零")
        self._repository = repository
        self._task_store = task_store
        self._decision_service = decision_service
        self._graph = graph
        self._credential_vault = credential_vault
        self._clock = clock or (lambda: datetime.now(UTC))
        self._worker_id = worker_id.strip()
        self._lease_duration = lease_duration
        self._heartbeat_step = heartbeat_step
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._scan_interval_seconds = scan_interval_seconds
        self._post_commit_recorder = post_commit_recorder
        self._metrics = SupervisorExecutionMetrics()
        self._local_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._pending_credentials: dict[
            tuple[str, str],
            TransientTurnCredential,
        ] = {}
        self._credential_active: set[tuple[str, str]] = set()
        self._task_guard = asyncio.Lock()
        self._scan_task: asyncio.Task[None] | None = None
        self._scan_wakeup = asyncio.Event()
        self._closing = False

    async def start(self) -> None:
        """创建唯一恢复扫描任务；重复启动保持幂等。"""

        if self._closing:
            raise SupervisorExecutorClosedError("Executor 已关闭")
        if self._scan_task is not None:
            return
        self._scan_task = asyncio.create_task(
            self._scan_loop(),
            name="supervisor-turn-scan",
        )

    async def notify_turn(
        self,
        scope: SupervisorTurnScope,
        credential: TransientTurnCredential | None,
    ) -> None:
        """立即尝试领取已持久化 Turn，不保存入口凭据。"""

        await self._schedule(scope, credential=credential, interrupt=None)

    async def notify_interrupt(
        self,
        interrupt: StoredAgentInterrupt,
        credential: TransientTurnCredential | None = None,
    ) -> None:
        """仅按已持久化 responded interrupt 唤醒原 Turn。"""

        normalized = StoredAgentInterrupt.model_validate(
            interrupt.model_dump(mode="python")
        )
        if normalized.status != "responded":
            raise ValueError("notify_interrupt 只接受已持久化响应")
        await self._schedule(
            SupervisorTurnScope(
                user_id=normalized.user_id,
                conversation_id=normalized.conversation_id,
                turn_id=normalized.turn_id,
            ),
            credential=credential,
            interrupt=normalized,
        )

    async def recover_due_interrupts(self) -> None:
        """稳定扫描并独立调度每个到期人工响应。"""

        if self._closing:
            return
        try:
            candidates = await self._repository.list_due_interrupt_responses(
                now=self._now(),
                limit=100,
            )
        except Exception:
            self._metrics.reason("database_unavailable")
            return
        for interrupt in candidates:
            try:
                await self.notify_interrupt(interrupt)
            except Exception:
                self._metrics.reason("executor_infrastructure_unavailable")

    async def recover_due_turns(self) -> None:
        """稳定扫描并独立调度每个到期 Turn。"""

        if self._closing:
            return
        try:
            candidates = await self._repository.list_due_turns(
                now=self._now(),
                limit=100,
            )
        except Exception:
            self._metrics.reason("database_unavailable")
            return
        for candidate in candidates:
            try:
                await self._schedule(
                    SupervisorTurnScope(
                        user_id=candidate.user_id,
                        conversation_id=candidate.turn.conversation_id,
                        turn_id=candidate.turn.turn_id,
                    ),
                    credential=None,
                    interrupt=None,
                )
            except Exception:
                self._metrics.reason("executor_infrastructure_unavailable")

    def metrics_snapshot(self) -> dict[str, JsonValue]:
        """返回与内部聚合器隔离的安全 JSON。"""

        return self._metrics.snapshot()

    def observe_external_job_state(self, state: ProviderJobOutcome) -> None:
        """为不经过 Turn Executor 的 M06 完成边界提供受限观察入口。"""

        self._metrics.observe_external_job_state(state)

    async def wait_idle(self) -> None:
        """等待当前及其唤醒的同会话后继 Turn 全部退出。"""

        while True:
            async with self._task_guard:
                tasks = tuple(self._local_tasks.values())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        """只取消本进程任务，不释放或改写持久化租约。"""

        self._closing = True
        self._scan_wakeup.set()
        if self._scan_task is not None:
            self._scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        await self._cancel_and_join_local_turn_tasks()
        self._credential_vault.clear()

    async def _scan_loop(self) -> None:
        while not self._closing:
            await self.recover_due_interrupts()
            await self.recover_due_turns()
            try:
                await asyncio.wait_for(
                    self._scan_wakeup.wait(),
                    timeout=self._scan_interval_seconds,
                )
            except TimeoutError:
                pass
            self._scan_wakeup.clear()

    async def _schedule(
        self,
        scope: SupervisorTurnScope,
        *,
        credential: TransientTurnCredential | None,
        interrupt: StoredAgentInterrupt | None,
    ) -> None:
        if self._closing:
            if credential is not None:
                credential.discard()
            raise SupervisorExecutorClosedError("Executor 已关闭")
        key = (scope.user_id, scope.turn_id)
        async with self._task_guard:
            existing = self._local_tasks.get(key)
            if existing is not None and not existing.done():
                if credential is not None:
                    if key in self._credential_active:
                        self._credential_vault.put(scope.turn_id, credential)
                    else:
                        self._replace_pending_credential(key, credential)
                return
            if credential is not None:
                self._replace_pending_credential(key, credential)
            task = asyncio.create_task(
                self._claim_and_execute(
                    scope,
                    interrupt=interrupt,
                ),
                name=f"supervisor-turn:{scope.turn_id}",
            )
            self._local_tasks[key] = task
            task.add_done_callback(
                lambda completed, identity=key: self._forget_task(identity, completed)
            )

    def _forget_task(
        self,
        key: tuple[str, str],
        task: asyncio.Task[None],
    ) -> None:
        current = self._local_tasks.get(key)
        if current is task:
            self._local_tasks.pop(key, None)
            pending = self._pending_credentials.pop(key, None)
            if pending is not None:
                pending.discard()
            if key in self._credential_active:
                self._credential_active.discard(key)
                self._credential_vault.pop(key[1])
        with suppress(asyncio.CancelledError, Exception):
            task.result()

    async def _claim_and_execute(
        self,
        scope: SupervisorTurnScope,
        *,
        interrupt: StoredAgentInterrupt | None,
    ) -> None:
        try:
            now = self._now()
            if interrupt is None:
                claim = await self._repository.claim_turn(
                    scope.user_id,
                    scope.conversation_id,
                    scope.turn_id,
                    lease_owner=self._worker_id,
                    now=now,
                    lease_expires_at=now + self._lease_duration,
                )
            else:
                claim = await self._repository.claim_interrupt_resume(
                    scope.user_id,
                    scope.conversation_id,
                    interrupt.interrupt_id,
                    lease_owner=self._worker_id,
                    now=now,
                    lease_expires_at=now + self._lease_duration,
                )
            if claim is None:
                self._metrics.increment("lease_conflicts")
                return
            self._metrics.increment("turn_claimed")
            self._metrics.observe_wait((now - claim.turn.created_at).total_seconds())
            if interrupt is None:
                interrupt = await self._responded_interrupt_for_claim(claim)
            if interrupt is not None:
                self._metrics.observe_interrupt_wait(
                    (now - interrupt.opened_at).total_seconds()
                )
            await self._execute_with_heartbeat(
                claim,
                interrupt=interrupt,
            )
        except asyncio.CancelledError:
            raise
        except TurnExecutionLeaseConflictError:
            self._metrics.increment("lease_conflicts")
        except (ConnectionError, OperationalError, TimeoutError) as exc:
            del exc
            if "claim" in locals() and claim is not None:
                await self._reschedule_transient_failure(
                    claim,
                    reason_code="database_unavailable",
                )
        except SupervisorTransientExecutionError as exc:
            if "claim" in locals() and claim is not None:
                await self._reschedule_transient_failure(
                    claim,
                    reason_code=exc.reason_code,
                )
        except Exception as exc:
            if "claim" in locals() and claim is not None:
                await self._commit_failed_claim(
                    claim,
                    reason_code=self._failure_reason_code(exc),
                    close_interrupt_id=(None if interrupt is None else interrupt.interrupt_id),
                )
    async def _execute_with_heartbeat(
        self,
        claim: TurnExecutionClaim,
        *,
        interrupt: StoredAgentInterrupt | None,
    ) -> None:
        task_key = (claim.user_id, claim.turn.turn_id)
        work = asyncio.create_task(
            (
                self._execute_claim(claim, task_key)
                if interrupt is None
                else self._resume_interrupt_claim(claim, interrupt, task_key)
            ),
            name=f"supervisor-turn-work:{claim.turn.turn_id}",
        )
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(claim),
            name=f"supervisor-turn-heartbeat:{claim.turn.turn_id}",
        )
        committed: _CommittedTurnResult | None = None
        try:
            done, _ = await asyncio.wait(
                (work, heartbeat),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work in done:
                committed = await work
            elif heartbeat in done:
                error = heartbeat.exception()
                if error is not None:
                    work.cancel()
                    with suppress(asyncio.CancelledError):
                        await work
                    raise error
                committed = await work
        finally:
            for task in (work, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)
        if committed is not None:
            await self._after_commit(
                committed.claim,
                committed.commit,
                committed.stored,
            )
            await self._notify_next_turn(
                committed.claim.user_id,
                committed.claim.turn.conversation_id,
            )

    async def _heartbeat_loop(self, claim: TurnExecutionClaim) -> None:
        current = claim
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            current = await self._heartbeat(current)

    async def _heartbeat(self, claim: TurnExecutionClaim) -> TurnExecutionClaim:
        now = self._now()
        renewed_until = max(
            claim.lease_expires_at + self._heartbeat_step,
            now + self._lease_duration,
        )
        return await self._repository.heartbeat_turn(
            claim,
            now=now,
            lease_expires_at=renewed_until,
        )

    async def _execute_claim(
        self,
        claim: TurnExecutionClaim,
        task_key: tuple[str, str],
    ) -> _CommittedTurnResult:
        evidence = await self._load_authoritative_evidence(claim)
        decision = await self._decision_service.decide(evidence)
        self._metrics.action(decision.decision.action)
        started_at = self._now()
        graph_snapshot = await self._invoke_or_recover_graph(
            evidence,
            decision,
            task_key=task_key,
        )
        graph_state = dict(getattr(graph_snapshot, "values", {}) or {})
        commit = self._commit_from_graph(
            claim,
            decision.decision,
            graph_state,
            graph_interrupts=tuple(
                getattr(graph_snapshot, "interrupts", ()) or ()
            ),
        )
        stored = await self._repository.commit_turn(claim, commit)
        self._observe_commit_external_job(commit)
        self._metrics.observe_stage(
            self._commit_stage(commit),
            (self._now() - started_at).total_seconds(),
        )
        return _CommittedTurnResult(claim=claim, commit=commit, stored=stored)

    async def _resume_interrupt_claim(
        self,
        claim: TurnExecutionClaim,
        interrupt: StoredAgentInterrupt,
        task_key: tuple[str, str],
    ) -> _CommittedTurnResult:
        if interrupt.status != "responded" or interrupt.response is None:
            raise ValueError("恢复路径只接受已持久化响应")
        if interrupt.kind == "clarification":
            return await self._resume_clarification_claim(
                claim,
                interrupt,
                task_key,
            )
        request = InterruptResponseRequest.model_validate(
            self._thaw_json(interrupt.response)
        )
        evidence = await self._load_authoritative_evidence(
            claim,
            response_value=request.value.model_dump(mode="json"),
        )
        decision_result = await self._decision_service.decide(evidence)
        decision = self._bind_interrupt_decision(
            decision_result,
            client_response_id=str(request.client_response_id),
        )
        self._metrics.action(decision.action)
        namespace = supervisor_namespace(evidence.conversation_id)
        config = namespace.as_runnable_config()
        snapshot = await self._graph.aget_state(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        response_id = str(request.client_response_id)
        if values.get("last_interrupt_response_id") != response_id:
            graph_interrupt_id = self._graph_interrupt_id(snapshot, interrupt)
            internal_resume_envelope = {
                "client_response_id": response_id,
                "interrupt_id": interrupt.interrupt_id,
                "workflow_id": interrupt.workflow_id,
                "stage": interrupt.payload.get("stage"),
                "decision": decision.model_dump(mode="json"),
                "value": request.value.model_dump(mode="json"),
            }
            await self._run_graph_with_credential(
                task_key,
                lambda: resume_graph_from_interrupt(
                    self._graph,
                    namespace,
                    interrupt_id=graph_interrupt_id,
                    response=internal_resume_envelope,
                ),
            )
            snapshot = await self._graph.aget_state(config)
            values = dict(getattr(snapshot, "values", {}) or {})
        commit = self._commit_from_graph(
            claim,
            decision,
            values,
            close_interrupt_id=interrupt.interrupt_id,
        )
        stored = await self._repository.commit_turn(claim, commit)
        self._observe_commit_external_job(commit)
        return _CommittedTurnResult(claim=claim, commit=commit, stored=stored)

    async def _resume_clarification_claim(
        self,
        claim: TurnExecutionClaim,
        interrupt: StoredAgentInterrupt,
        task_key: tuple[str, str],
    ) -> _CommittedTurnResult:
        """用严格内部信封恢复全局追问，并让新决策重新经过 Graph Validator。"""

        response_document = self._thaw_json(interrupt.response)
        if not isinstance(response_document, dict) or set(response_document) not in (
            {"client_response_id", "value"},
            {"client_response_id", "pre_input_context_version", "value"},
        ):
            raise ValueError("全局 clarification 响应文档结构非法")
        stored_snapshot_version = response_document.get(
            "pre_input_context_version",
            claim.turn.expected_context_version,
        )
        if (
            type(stored_snapshot_version) is not int
            or stored_snapshot_version < 0
            or stored_snapshot_version != claim.turn.expected_context_version
        ):
            raise ValueError("全局 clarification 响应快照身份冲突")
        request = InterruptResponseRequest.model_validate(
            {
                "client_response_id": response_document["client_response_id"],
                "value": response_document["value"],
            }
        )
        evidence = await self._load_authoritative_evidence(
            claim,
            response_value=request.value.model_dump(mode="json"),
        )
        decision_result = await self._decision_service.decide(evidence)
        decision = decision_result.decision
        self._metrics.action(decision.action)
        namespace = supervisor_namespace(evidence.conversation_id)
        config = namespace.as_runnable_config()
        snapshot = await self._graph.aget_state(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        response_id = str(request.client_response_id)
        if values.get("last_interrupt_response_id") != response_id:
            graph_interrupt_id = self._global_graph_interrupt_id(
                snapshot,
                interrupt,
            )
            source_decision = ActionDecision.model_validate(values.get("decision"))
            internal_resume_envelope = {
                "client_response_id": response_id,
                "interrupt_id": interrupt.interrupt_id,
                "resume_context_version": claim.turn.expected_context_version,
                "source_decision_idempotency_key": (
                    source_decision.idempotency_key
                ),
                "decision": decision.model_dump(mode="json"),
                "decision_validation_request": (
                    decision_result.validation_request.model_dump(mode="json")
                ),
                "value": request.value.model_dump(mode="json"),
                "answer_message": decision_result.answer_message,
            }
            await self._run_graph_with_credential(
                task_key,
                lambda: resume_graph_from_interrupt(
                    self._graph,
                    namespace,
                    interrupt_id=graph_interrupt_id,
                    response=internal_resume_envelope,
                ),
            )
            snapshot = await self._graph.aget_state(config)
            values = dict(getattr(snapshot, "values", {}) or {})
        commit = self._commit_from_graph(
            claim,
            decision,
            values,
            close_interrupt_id=interrupt.interrupt_id,
            graph_interrupts=tuple(getattr(snapshot, "interrupts", ()) or ()),
        )
        stored = await self._repository.commit_turn(claim, commit)
        self._observe_commit_external_job(commit)
        return _CommittedTurnResult(claim=claim, commit=commit, stored=stored)

    async def _load_authoritative_evidence(
        self,
        claim: TurnExecutionClaim,
        *,
        response_value: Mapping[str, Any] | None = None,
    ) -> SupervisorTurnEvidence:
        user_id = claim.user_id
        conversation_id = claim.turn.conversation_id
        conversation = await self._task_store.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        if (
            conversation is None
            or conversation.user_id != user_id
            or conversation.orchestration_mode != "supervisor_v1"
            or conversation.orchestration_version != 1
        ):
            raise AgentRuntimeRecordConflictError("对话不属于 supervisor_v1 owner")
        runtime = conversation.context.get(AGENT_RUNTIME_CONTEXT_KEY)
        if (
            not isinstance(runtime, dict)
            or runtime.get("primary_execution_ready") is not True
            or "video" not in runtime.get("enabled_intents", [])
        ):
            raise AgentRuntimeRecordConflictError("对话未启用 video live owner")
        current_version = runtime.get("context_version")
        if (
            isinstance(current_version, bool)
            or not isinstance(current_version, int)
            or current_version < claim.turn.expected_context_version
        ):
            raise ValueError("对话 context_version 已损坏")

        messages = await self._task_store.list_conversation_messages(
            conversation_id,
            user_id=user_id,
            limit=200,
        )
        stable_message_id = conversation_message_id(
            conversation_id,
            claim.turn.client_input_id,
        )
        current_index = next(
            (
                index
                for index, item in enumerate(messages)
                if item.message_id == stable_message_id
            ),
            None,
        )
        if current_index is None:
            raise AgentRuntimeRecordConflictError("Turn 缺少稳定输入消息")
        current = messages[current_index]
        if current.role != "user" or current.user_id != user_id:
            raise AgentRuntimeRecordConflictError("Turn 输入消息 owner 不一致")
        payload = deepcopy(current.payload)
        source = payload if response_value is None else dict(response_value)
        content = current.content if response_value is None else source.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Turn 当前输入不能为空")

        materials = self._json_object_list(source.get("materials", []), "materials")
        artifact_refs = self._string_list(source.get("artifact_refs", []), "artifact_refs")
        reply_to = source.get("reply_to_message_id")
        if reply_to is not None and (not isinstance(reply_to, str) or not reply_to.strip()):
            raise ValueError("reply_to_message_id 必须是非空字符串")
        explicit_value = source.get("explicit_action")
        explicit = (
            None
            if explicit_value is None
            else ExplicitActionSignal.model_validate(explicit_value)
        )

        workflows = tuple(
            await self._repository.list_workflows(user_id, conversation_id)
        )
        workflow_ids = {item.workflow_id for item in workflows}
        visible = [self._task_message(item) for item in messages[: current_index + 1]]
        projections = await self._repository.list_projection_messages(
            user_id,
            conversation_id,
        )
        visible.extend(
            self._projection_message(item, workflow_ids=workflow_ids)
            for item in projections
        )
        active_workflow_id = await self._repository.get_active_workflow_id(
            user_id,
            conversation_id,
        )
        return SupervisorTurnEvidence(
            user_id=user_id,
            conversation_id=conversation_id,
            turn=claim.turn,
            content=content.strip(),
            visible_messages=tuple(visible),
            workflows=workflows,
            active_workflow_id=active_workflow_id,
            materials=tuple(materials),
            reply_to_message_id=reply_to,
            artifact_refs=tuple(artifact_refs),
            explicit_action=explicit,
            expected_context_version=claim.turn.expected_context_version,
            authoritative_context_version=claim.turn.expected_context_version,
        )

    def _graph_input(self, evidence: SupervisorTurnEvidence, decision: Any) -> dict[str, Any]:
        """填满 SupervisorState 全字段，并只使用权威 active Workflow。"""

        return {
            "conversation_id": evidence.conversation_id,
            "user_id": evidence.user_id,
            "turn_id": evidence.turn.turn_id,
            "run_id": evidence.turn.turn_id,
            "current_input": evidence.content,
            "materials": [dict(item) for item in evidence.materials],
            "reply_to_message_id": evidence.reply_to_message_id,
            "artifact_refs": list(evidence.artifact_refs),
            "context_version": evidence.expected_context_version,
            "messages": self._langchain_messages(evidence.visible_messages),
            "workflows": {item.workflow_id: item for item in evidence.workflows},
            "active_workflow_id": evidence.active_workflow_id,
            "decision": decision.decision,
            "decision_validation_request": decision.validation_request,
            "answer_message": decision.answer_message,
            "dispatch_workflow_id": None,
            "workflow_dispatch_result": None,
            "last_interrupt_response_id": None,
        }

    async def _invoke_or_recover_graph(
        self,
        evidence: SupervisorTurnEvidence,
        decision: Any,
        *,
        task_key: tuple[str, str],
    ) -> Any:
        namespace = supervisor_namespace(evidence.conversation_id)
        config = namespace.as_runnable_config()
        snapshot = await self._graph.aget_state(config)
        existing = dict(getattr(snapshot, "values", {}) or {})
        if self._checkpoint_matches(
            existing,
            turn_id=evidence.turn.turn_id,
            action_key=decision.decision.idempotency_key,
        ):
            return snapshot
        await self._run_graph_with_credential(
            task_key,
            lambda: self._graph.ainvoke(
                self._graph_input(evidence, decision),
                config,
            ),
        )
        snapshot = await self._graph.aget_state(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        if values.get("turn_id") != evidence.turn.turn_id:
            raise ValueError("Graph checkpoint 未绑定当前 Turn")
        return snapshot

    def _replace_pending_credential(
        self,
        key: tuple[str, str],
        credential: TransientTurnCredential,
    ) -> None:
        """在 task guard 内转移 mailbox 所有权，并清理被替换凭据。"""

        previous = self._pending_credentials.get(key)
        self._pending_credentials[key] = credential
        if previous is not None and previous is not credential:
            previous.discard()

    async def _run_graph_with_credential(
        self,
        key: tuple[str, str],
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """只在一次真实 Graph invoke/resume 期间开放当前 Turn 凭据。"""

        await self._activate_graph_credential(key)
        try:
            return await operation()
        finally:
            await self._deactivate_graph_credential(key)

    async def _activate_graph_credential(self, key: tuple[str, str]) -> None:
        async with self._task_guard:
            self._credential_active.add(key)
            credential = self._pending_credentials.pop(key, None)
            if credential is not None:
                self._credential_vault.put(key[1], credential)

    async def _deactivate_graph_credential(self, key: tuple[str, str]) -> None:
        async with self._task_guard:
            self._credential_active.discard(key)
            self._credential_vault.pop(key[1])

    def _commit_from_graph(
        self,
        claim: TurnExecutionClaim,
        decision: ActionDecision,
        graph_state: Mapping[str, Any],
        *,
        close_interrupt_id: str | None = None,
        graph_interrupts: Sequence[Any] = (),
    ) -> VideoTurnCommit:
        """把 Graph 的公开结果收敛为唯一原子提交 DTO。"""

        stored_decision = ActionDecision.model_validate(graph_state.get("decision", decision))
        raw_dispatch = graph_state.get("workflow_dispatch_result")
        if isinstance(raw_dispatch, Mapping):
            result = WorkflowDispatchResult.model_validate(raw_dispatch)
            return VideoTurnCommit(
                decision=stored_decision,
                turn_status=result.turn_status,
                workflow_state=result.state,
                workflow=result.workflow,
                expected_workflow_version=result.state.workflow_version - 1,
                messages=result.messages,
                open_interrupt=result.interrupt,
                close_interrupt_id=close_interrupt_id,
                update_active_workflow=result.update_active_workflow,
                active_workflow_id=result.active_workflow_id,
                occurred_at=self._now(),
            )
        if stored_decision.action is AgentAction.ANSWER_ONLY:
            message = self._answer_projection(claim, stored_decision, graph_state)
            return VideoTurnCommit(
                decision=stored_decision,
                turn_status=TurnStatus.COMPLETED,
                expected_workflow_version=0,
                messages=(message,),
                close_interrupt_id=close_interrupt_id,
                occurred_at=self._now(),
            )
        if stored_decision.action is AgentAction.CLARIFY:
            opened = self._global_clarification_interrupt(
                claim,
                stored_decision,
                graph_interrupts=graph_interrupts,
                previous_response_id=graph_state.get(
                    "last_interrupt_response_id"
                ),
            )
            return VideoTurnCommit(
                decision=stored_decision,
                turn_status=TurnStatus.WAITING_USER,
                expected_workflow_version=0,
                open_interrupt=opened,
                close_interrupt_id=close_interrupt_id,
                occurred_at=self._now(),
            )
        raise ValueError("Graph 未返回可提交的 WorkflowDispatchResult")

    async def _reschedule_transient_failure(
        self,
        claim: TurnExecutionClaim,
        *,
        reason_code: str,
    ) -> None:
        if reason_code not in _TRANSIENT_REASON_CODES:
            raise ValueError("退避只接受固定安全 reason code")
        delay = min(30, 2 ** min(claim.attempt, 5))
        now = self._now()
        try:
            await self._repository.reschedule_turn(
                claim,
                now=now,
                next_attempt_at=now + timedelta(seconds=delay),
                reason_code=reason_code,
            )
        except TurnExecutionLeaseConflictError:
            self._metrics.increment("lease_conflicts")
            return
        self._metrics.increment("turn_retried")
        self._metrics.reason(reason_code)

    async def _commit_failed_claim(
        self,
        claim: TurnExecutionClaim,
        *,
        reason_code: str,
        close_interrupt_id: str | None,
    ) -> None:
        if reason_code not in _FAILURE_REASON_CODES:
            raise ValueError("失败提交只接受固定安全 reason code")
        decision = ActionDecision(
            action=AgentAction.CLARIFY,
            intent=AgentIntent.GENERAL,
            confidence=0,
            requires_confirmation=True,
            clarification_question="当前请求无法继续处理，请重新发起或联系管理员。",
            reason_code="error.raised",
            idempotency_key=f"error:{claim.turn.turn_id}:{claim.attempt}",
        )
        commit = VideoTurnCommit(
            decision=decision,
            turn_status=TurnStatus.FAILED,
            expected_workflow_version=0,
            close_interrupt_id=close_interrupt_id,
            error_reason_code=reason_code,
            occurred_at=self._now(),
        )
        try:
            stored = await self._repository.commit_turn(claim, commit)
        except TurnExecutionLeaseConflictError:
            self._metrics.increment("lease_conflicts")
            return
        self._metrics.reason(reason_code)
        await self._after_commit(claim, commit, stored)
        await self._notify_next_turn(
            claim.user_id,
            claim.turn.conversation_id,
        )

    async def _after_commit(
        self,
        claim: TurnExecutionClaim,
        commit: VideoTurnCommit,
        stored: TurnRecord,
    ) -> None:
        if stored.status is TurnStatus.COMPLETED:
            self._metrics.increment("turn_completed")
        recorder = self._post_commit_recorder
        if recorder is None:
            return
        record: dict[str, JsonValue] = {
            "summary": "视频 live Turn 已完成原子提交",
            "category": "experience",
            "user_id": claim.user_id,
            "conversation_id": claim.turn.conversation_id,
            "turn_id": claim.turn.turn_id,
            "turn_status": stored.status.value,
            "action": commit.decision.action.value,
            "stage": self._commit_stage(commit),
            "reason_code": commit.error_reason_code,
        }
        try:
            result = recorder.record_after_commit(deepcopy(record))
            if inspect.isawaitable(result):
                await result
            elif result is not None:
                raise TypeError("提交后记录端口只能返回 awaitable 或 None")
        except Exception:
            self._metrics.reason("executor_infrastructure_unavailable")

    def _observe_commit_external_job(self, commit: VideoTurnCommit) -> None:
        """只从已校验且已提交的 Workflow 投影读取固定 M06 状态。"""

        workflow = commit.workflow
        if workflow is None:
            return
        if workflow.status.value == ProviderJobOutcome.PAUSED_QUOTA.value:
            self._metrics.observe_external_job_state(
                ProviderJobOutcome.PAUSED_QUOTA,
            )
            return
        pending = workflow.pending_external_job
        if pending is None:
            return
        try:
            outcome = ProviderJobOutcome(pending.status.value)
        except ValueError:
            return
        self._metrics.observe_external_job_state(outcome)

    async def _notify_next_turn(self, user_id: str, conversation_id: str) -> None:
        if self._closing:
            return
        candidates = await self._repository.list_due_turns(now=self._now(), limit=100)
        next_turn = next(
            (
                item
                for item in candidates
                if item.user_id == user_id and item.turn.conversation_id == conversation_id
            ),
            None,
        )
        if next_turn is None:
            return
        await self._schedule(
            SupervisorTurnScope(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=next_turn.turn.turn_id,
            ),
            credential=None,
            interrupt=None,
        )

    async def _responded_interrupt_for_claim(
        self,
        claim: TurnExecutionClaim,
    ) -> StoredAgentInterrupt | None:
        """让崩溃后的 processing/queued 原 Turn 回到独立恢复路径。"""

        snapshot = await self._repository.export_safe_snapshot(
            claim.user_id,
            claim.turn.conversation_id,
        )
        matches = [
            item
            for item in snapshot.interrupts
            if item.turn_id == claim.turn.turn_id and item.status == "responded"
        ]
        if len(matches) > 1:
            raise AgentRuntimeRecordConflictError("原 Turn 存在多个待恢复响应")
        return matches[0] if matches else None

    @staticmethod
    def _bind_interrupt_decision(decision_result: Any, *, client_response_id: str) -> ActionDecision:
        """用持久化响应 ID 重绑并重新校验显式动作的幂等键。"""

        decision = decision_result.decision.model_copy(
            update={"idempotency_key": f"decision:{client_response_id}"},
            deep=True,
        )
        validation = decision_result.validation_request
        classification = validation.classification_request.model_copy(
            update={"turn_id": client_response_id},
            deep=True,
        )
        rebound = DecisionValidationRequest.model_validate(
            validation.model_copy(
                update={
                    "decision": decision,
                    "classification_request": classification,
                },
                deep=True,
            )
        )
        return DecisionValidator().validate(rebound)

    @staticmethod
    def _graph_interrupt_id(snapshot: Any, stored: StoredAgentInterrupt) -> str:
        for item in getattr(snapshot, "interrupts", ()):
            value = getattr(item, "value", None)
            if isinstance(value, Mapping) and value.get("interrupt_id") == stored.interrupt_id:
                return item.id
        raise LookupError("Graph checkpoint 缺少已持久化 interrupt")

    @staticmethod
    def _global_graph_interrupt_id(
        snapshot: Any,
        stored: StoredAgentInterrupt,
    ) -> str:
        """把全局业务 interrupt 与唯一开放的 LangGraph interrupt 精确绑定。"""

        expected_key = stored.payload.get("idempotency_key")
        expected_question = stored.payload.get("question")
        matches = []
        for item in getattr(snapshot, "interrupts", ()):
            value = getattr(item, "value", None)
            if (
                isinstance(value, Mapping)
                and value.get("type") == "clarification"
                and value.get("reason_code") == stored.reason_code
                and value.get("idempotency_key") == expected_key
                and value.get("question") == expected_question
            ):
                matches.append(item.id)
        if len(matches) != 1:
            raise LookupError("Graph checkpoint 缺少唯一全局 clarification")
        return matches[0]

    def _global_clarification_interrupt(
        self,
        claim: TurnExecutionClaim,
        decision: ActionDecision,
        *,
        graph_interrupts: Sequence[Any],
        previous_response_id: Any,
    ) -> StoredAgentInterrupt:
        """把 Graph 全局追问投影成可跨进程恢复的权威业务 interrupt。"""

        if decision.clarification_question is None:
            raise ValueError("全局 clarification 缺少问题")
        expected_value = {
            "type": "clarification",
            "question": decision.clarification_question,
            "reason_code": decision.reason_code,
            "idempotency_key": decision.idempotency_key,
        }
        matches = [
            item
            for item in graph_interrupts
            if getattr(item, "value", None) == expected_value
        ]
        if len(matches) != 1:
            raise ValueError("Graph 未打开唯一全局 clarification")
        if previous_response_id is not None and (
            not isinstance(previous_response_id, str)
            or not previous_response_id.strip()
        ):
            raise ValueError("Graph clarification 响应身份已损坏")
        namespace = supervisor_namespace(claim.turn.conversation_id)
        identity_turn_id = claim.turn.turn_id
        if previous_response_id is not None:
            identity_turn_id = f"{identity_turn_id}:{previous_response_id}"
        return StoredAgentInterrupt(
            interrupt_id=interrupt_id(identity_turn_id, decision.reason_code),
            conversation_id=claim.turn.conversation_id,
            workflow_id=None,
            turn_id=claim.turn.turn_id,
            kind="clarification",
            reason_code=decision.reason_code,
            payload={
                "question": decision.clarification_question,
                "idempotency_key": decision.idempotency_key,
            },
            opened_at=self._now(),
            user_id=claim.user_id,
            thread_id=namespace.thread_id,
            checkpoint_ns=namespace.checkpoint_ns,
        )

    @staticmethod
    def _checkpoint_matches(
        values: Mapping[str, Any],
        *,
        turn_id: str,
        action_key: str,
    ) -> bool:
        if values.get("turn_id") != turn_id:
            return False
        try:
            decision = ActionDecision.model_validate(values.get("decision"))
        except ValidationError:
            return False
        return decision.idempotency_key == action_key

    @staticmethod
    def _task_message(record: Any) -> dict[str, JsonValue]:
        return {
            "message_id": record.message_id,
            "conversation_id": record.conversation_id,
            "user_id": record.user_id,
            "role": record.role,
            "content": record.content,
            "payload": deepcopy(record.payload),
            "created_at": record.created_at,
        }

    @staticmethod
    def _projection_message(
        record: SupervisorProjectionMessage,
        *,
        workflow_ids: set[str],
    ) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "message_id": record.message_id,
            "conversation_id": record.conversation_id,
            "role": record.role,
            "content": record.content,
            "payload": record.model_dump(mode="json")["payload"],
            "created_at": record.created_at.isoformat(),
        }
        if record.run_id in workflow_ids:
            value["workflow_id"] = record.run_id
        return value

    @staticmethod
    def _langchain_messages(
        visible_messages: Sequence[Mapping[str, JsonValue]],
    ) -> list[Any]:
        converted = []
        for item in visible_messages:
            content = item.get("content")
            if not isinstance(content, str):
                continue
            message_id_value = item.get("message_id")
            message_id = message_id_value if isinstance(message_id_value, str) else None
            role = item.get("role")
            if role == "user":
                converted.append(HumanMessage(content=content, id=message_id))
            elif role == "system":
                converted.append(SystemMessage(content=content, id=message_id))
            else:
                converted.append(AIMessage(content=content, id=message_id))
        return converted

    def _answer_projection(
        self,
        claim: TurnExecutionClaim,
        decision: ActionDecision,
        graph_state: Mapping[str, Any],
    ) -> SupervisorProjectionMessage:
        expected_id = f"assistant:{decision.idempotency_key}"
        answer = next(
            (
                item
                for item in graph_state.get("messages", [])
                if isinstance(item, AIMessage) and item.id == expected_id
            ),
            None,
        )
        if answer is None or not isinstance(answer.content, str) or not answer.content.strip():
            raise ValueError("Graph answer message 缺失")
        return SupervisorProjectionMessage(
            message_id=expected_id,
            conversation_id=claim.turn.conversation_id,
            run_id=claim.turn.turn_id,
            role="assistant",
            content=answer.content,
            payload={},
            created_at=self._now(),
        )

    @staticmethod
    def _json_object_list(value: Any, field_name: str) -> list[dict[str, JsonValue]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{field_name} 必须是对象数组")
        return deepcopy(value)

    @staticmethod
    def _string_list(value: Any, field_name: str) -> list[str]:
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"{field_name} 必须是非空字符串数组")
        return list(value)

    @staticmethod
    def _thaw_json(value: Any) -> Any:
        """把 Repository 深度只读快照还原成 Pydantic 可校验的普通 JSON。"""

        if isinstance(value, Mapping):
            return {
                str(key): SupervisorTurnExecutor._thaw_json(child)
                for key, child in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return [SupervisorTurnExecutor._thaw_json(child) for child in value]
        return value

    @staticmethod
    def _commit_stage(commit: VideoTurnCommit) -> str:
        if commit.workflow is None:
            return "supervisor"
        stage = commit.workflow.current_stage
        return stage if _SAFE_DIMENSION.fullmatch(stage) else "unknown"

    @staticmethod
    def _failure_reason_code(exc: Exception) -> str:
        if isinstance(exc, AgentRuntimeRecordConflictError):
            return "isolation_violation"
        if isinstance(exc, VideoWorkflowStateConflictError):
            return "workflow_state_conflict"
        if isinstance(exc, VideoLiveStateConflictError):
            return "workflow_state_conflict"
        if isinstance(
            exc,
            (
                DecisionValidationError,
                SupervisorDecisionUnavailableError,
                SupervisorRoutingError,
                ValidationError,
                ValueError,
                KeyError,
                LookupError,
            ),
        ):
            return "contract_validation_failed"
        return "handler_failed"

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Executor clock 必须返回含时区 datetime")
        return value.astimezone(UTC)

    async def _cancel_and_join_local_turn_tasks(self) -> None:
        async with self._task_guard:
            tasks = tuple(self._local_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "SupervisorExecutionMetrics",
    "SupervisorExecutorClosedError",
    "SupervisorPostCommitRecorder",
    "SupervisorTransientExecutionError",
    "SupervisorTurnExecutor",
    "SupervisorTurnScope",
]
