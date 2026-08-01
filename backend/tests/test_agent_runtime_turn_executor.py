"""Supervisor Turn Executor 的顺序、恢复、fencing 与关闭合同测试。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime import (
    SupervisorExecutorClosedError,
    SupervisorTurnExecutor,
    SupervisorTurnScope,
)
from pixelflow.agent_runtime.config import ContextBudgetConfig
from pixelflow.agent_runtime.context import (
    ContextAssembler,
    ContextBudgetPolicyProvider,
    RepositoryContextSnapshotSource,
)
from pixelflow.agent_runtime.context.profiles import ModelContextProfile
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    ExplicitActionSignal,
    ExternalJobRef,
    ExternalJobStatus,
    InterruptResponseRequest,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    make_agent_runtime_graph,
)
from pixelflow.agent_runtime.identity import conversation_message_id
from pixelflow.agent_runtime.jobs.providers import ProviderJobOutcome
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryTurnRegistrationStore,
    MemoryVideoRuntimeRepository,
    SQLTurnRegistrationStore,
    SQLVideoRuntimeRepository,
    StoredAgentInterrupt,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    DecisionValidationRequest,
    DecisionValidator,
    DeterministicResolution,
    DeterministicResolutionStatus,
    DeterministicTargetResolver,
    SupervisorDecisionService,
)
from pixelflow.agent_workflows.video import (
    VideoPlanningWorkflowService,
    WorkflowDispatchResult,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)
from pixelflow.agent_workflows.video.live_capabilities import (
    TransientTurnCredential,
)
from pixelflow.agent_workflows.video.live_operations import (
    TransientCredentialVault,
)
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)

NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)
LEASE_EXPIRY = NOW + timedelta(seconds=30)
EPSILON = timedelta(microseconds=1)
ALL_ACTIONS = tuple(AgentAction)


class FakeClock:
    """为租约与退避测试提供可控 UTC 时间。"""

    def __init__(self, now: datetime = NOW) -> None:
        self.current = now

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class FakeDecisionService:
    """只根据权威 evidence 生成 Graph 可复验的确定性决策。"""

    def __init__(
        self,
        *,
        clarify_first: bool = False,
        clarification_count: int = 0,
        vault: TransientCredentialVault | None = None,
    ) -> None:
        self.evidence = []
        self.clarification_count = max(
            clarification_count,
            1 if clarify_first else 0,
        )
        self.vault = vault
        self.credential_seen: list[bool] = []

    async def decide(self, evidence):
        self.evidence.append(evidence)
        if self.vault is not None:
            self.credential_seen.append(
                self.vault.get(evidence.turn.turn_id) is not None
            )
        explicit = evidence.explicit_action
        if len(self.evidence) <= self.clarification_count:
            action = AgentAction.CLARIFY
            intent = AgentIntent.GENERAL
            workflow_id = None
            stage = None
            patch = {}
        elif explicit is None:
            action = AgentAction.START_WORKFLOW
            intent = AgentIntent.VIDEO
            workflow_id = None
            stage = None
            patch = {}
        else:
            action = explicit.action
            intent = explicit.intent or AgentIntent.VIDEO
            workflow_id = explicit.workflow_id
            stage = explicit.stage
            patch = dict(explicit.patch)

        candidates = tuple(_candidate(item) for item in evidence.workflows)
        resolution = DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=action,
            intent=intent,
            target_workflow_id=workflow_id,
            target_stage=stage,
            target_artifact_ref=(None if explicit is None else explicit.artifact_ref),
            reason_code="fake_authoritative_decision",
            candidate_workflow_ids=(() if workflow_id is None else (workflow_id,)),
        )
        classification = ActionClassificationRequest(
            turn_id=evidence.turn.turn_id,
            content=evidence.content,
            deterministic_resolution=resolution,
            candidates=candidates,
            context_summary="测试上下文摘要",
        )
        decision = ActionDecision(
            action=action,
            intent=intent,
            target_workflow_id=workflow_id,
            target_stage=stage,
            target_artifact_ref=(None if explicit is None else explicit.artifact_ref),
            confidence=1,
            requires_confirmation=action is AgentAction.CLARIFY,
            clarification_question=(
                "请明确要创建什么视频。" if action is AgentAction.CLARIFY else None
            ),
            patch=patch,
            reason_code="fake_authoritative_decision",
            idempotency_key=classification.idempotency_key,
        )
        validation = DecisionValidationRequest(
            decision=decision,
            classification_request=classification,
            current_candidates=candidates,
            allowed_global_actions=(
                AgentAction.ANSWER_ONLY,
                AgentAction.CLARIFY,
                AgentAction.START_WORKFLOW,
            ),
            expected_context_version=evidence.expected_context_version,
            current_context_version=evidence.authoritative_context_version,
        )
        return SimpleNamespace(
            decision=decision,
            validation_request=validation,
            context=object(),
            answer_message=None,
        )


def _candidate(workflow):
    targets = (
        ActionClassificationTarget(target_stage=workflow.current_stage),
    )
    return ActionClassificationCandidate(
        workflow_id=workflow.workflow_id,
        intent=AgentIntent(workflow.kind.value),
        status=workflow.status,
        current_stage=workflow.current_stage,
        stage_version=workflow.stage_version,
        context_version=workflow.context_version,
        allowed_actions=ALL_ACTIONS,
        targets=targets,
    )


class FakeVideoHandler:
    """用真实视频状态 DTO 返回可提交结果，不调用模型或 Provider。"""

    def __init__(
        self,
        repository: MemoryVideoRuntimeRepository,
        *,
        open_first_interrupt: bool = False,
        block: bool = False,
        parallel_target: int = 0,
        fail_first: bool = False,
        vault: TransientCredentialVault | None = None,
        external_job_status: ExternalJobStatus | None = None,
        workflow_status: WorkflowStatus | None = None,
    ) -> None:
        self.repository = repository
        self.open_first_interrupt = open_first_interrupt
        self.block = block
        self.parallel_target = parallel_target
        self.failures_remaining = 1 if fail_first else 0
        self.vault = vault
        self.external_job_status = external_job_status
        self.workflow_status = workflow_status
        self.turn_ids: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._parallel_count = 0
        self.credential_seen = False
        self.credential_start_calls = 0

    async def dispatch(self, command):
        self.turn_ids.append(command.turn_id)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ValueError("测试 Handler 合同失败")
        if self.vault is not None:
            self.credential_seen = self.vault.get(command.turn_id) is not None
            if self.credential_seen:
                self.credential_start_calls += 1
        self._parallel_count += 1
        if self.parallel_target and self._parallel_count >= self.parallel_target:
            self.entered.set()
        elif self.block:
            self.entered.set()
        if self.block or self.parallel_target:
            await self.release.wait()

        existing = await self.repository.get_video_state(
            command.user_id,
            command.workflow_id,
        )
        if existing is None:
            state = VideoPlanningWorkflowService().start(
                workflow_id=command.workflow_id,
                conversation_id=command.conversation_id,
                intent="video",
                intake_context={"source": "executor-test"},
                now=NOW,
            )
            version = 1
        else:
            state = decode_video_workflow_state(existing)
            version = existing.workflow_version + 1
        workflow = project_video_workflow_state(state)
        if self.workflow_status is not None:
            workflow = workflow.model_copy(update={"status": self.workflow_status})
        if self.external_job_status is not None:
            workflow = workflow.model_copy(
                update={
                    "pending_external_job": ExternalJobRef(
                        job_id=f"job-{command.turn_id}",
                        provider_job_id=f"provider-{command.turn_id}",
                        workflow_id=workflow.workflow_id,
                        stage=workflow.current_stage,
                        status=self.external_job_status,
                        attempt=1,
                        idempotency_key=f"operation-{command.turn_id}",
                    )
                }
            )
        envelope = encode_video_workflow_state(
            user_id=command.user_id,
            state=state,
            workflow_version=version,
            last_turn_id=command.turn_id,
            last_action_key=command.decision.idempotency_key,
        )
        if existing is None and self.open_first_interrupt:
            opened = StoredAgentInterrupt(
                interrupt_id=f"interrupt-{command.turn_id}",
                conversation_id=command.conversation_id,
                workflow_id=command.workflow_id,
                turn_id=command.turn_id,
                kind="video_intake_form",
                reason_code="video_intake_required",
                payload={"workflow_id": command.workflow_id, "stage": workflow.current_stage},
                opened_at=NOW,
                user_id=command.user_id,
                thread_id=command.namespace.thread_id,
                checkpoint_ns="root",
            )
            return WorkflowDispatchResult(
                state=envelope,
                workflow=workflow,
                interrupt=opened,
                turn_status=TurnStatus.WAITING_USER,
                update_active_workflow=True,
                active_workflow_id=command.workflow_id,
            )
        return WorkflowDispatchResult(
            state=envelope,
            workflow=workflow,
            turn_status=TurnStatus.COMPLETED,
        )


class FailFirstCommitRepository(MemoryVideoRuntimeRepository):
    """仅在首次提交前模拟数据库连接瞬断。"""

    def __init__(self, *, task_store: MemoryPixelFlowTaskStore) -> None:
        super().__init__(task_store=task_store)
        self.failures_remaining = 1

    async def commit_turn(self, claim, commit):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ConnectionError("测试连接已断开")
        return await super().commit_turn(claim, commit)


class BlockingFirstClaimRepository(MemoryVideoRuntimeRepository):
    """把扫描领取停在 Graph 前，稳定复现 HTTP 凭据后到的竞争。"""

    def __init__(self, *, task_store: MemoryPixelFlowTaskStore) -> None:
        super().__init__(task_store=task_store)
        self.claim_entered = asyncio.Event()
        self.release_claim = asyncio.Event()

    async def claim_turn(self, *args, **kwargs):
        self.claim_entered.set()
        await self.release_claim.wait()
        return await super().claim_turn(*args, **kwargs)


class MetricsOnlyCommitRepository(MemoryVideoRuntimeRepository):
    """只隔离指标边界，避免伪造的测试 Workflow state 进入真实 CAS。"""

    async def commit_turn(self, claim, commit):
        return claim.turn.model_copy(update={"status": commit.turn_status}, deep=True)


class FakePostCommitRecorder:
    """记录 Executor 提交成功后发出的固定安全摘要。"""

    def __init__(
        self,
        *,
        fail: bool = False,
        vault: TransientCredentialVault | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.fail = fail
        self.records = []
        self.vault = vault
        self.credential_seen: list[bool] = []
        self.delay_seconds = delay_seconds
        self.started = asyncio.Event()
        self.completed_count = 0

    async def record_after_commit(self, record) -> None:
        self.started.set()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        self.records.append(record)
        if self.vault is not None:
            self.credential_seen.append(
                self.vault.get(str(record["turn_id"])) is not None
            )
        if self.fail:
            raise RuntimeError("测试 PowerMem 不可用")
        self.completed_count += 1


@dataclass
class RuntimeHarness:
    executor: SupervisorTurnExecutor
    repository: MemoryVideoRuntimeRepository
    task_store: MemoryPixelFlowTaskStore
    handler: FakeVideoHandler
    clock: FakeClock
    vault: TransientCredentialVault
    recorder: FakePostCommitRecorder
    decision_service: FakeDecisionService


def scope(
    turn_id: str,
    *,
    conversation_id: str = "conversation-1",
    user_id: str = "user-1",
) -> SupervisorTurnScope:
    """只构造 Executor 领取所需的三个稳定 ID。"""

    return SupervisorTurnScope(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )


async def _seed_conversation(
    task_store: MemoryPixelFlowTaskStore,
    *,
    conversation_id: str = "conversation-1",
    user_id: str = "user-1",
    context_version: int = 0,
) -> None:
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            orchestration_mode="supervisor_v1",
            orchestration_version=1,
            context={
                "__agent_runtime": {
                    "mode": "primary",
                    "enabled_intents": ["video"],
                    "primary_execution_ready": True,
                    "context_compaction_enabled": True,
                    "context_version": context_version,
                }
            },
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
        )
    )


async def _seed_turn(
    repository: MemoryVideoRuntimeRepository,
    task_store: MemoryPixelFlowTaskStore,
    *,
    index: int,
    conversation_id: str = "conversation-1",
    user_id: str = "user-1",
    content: str | None = None,
) -> TurnRecord:
    client_input_id = UUID(f"00000000-0000-4000-8000-{index:012d}")
    turn = TurnRecord(
        turn_id=f"turn-{index}",
        conversation_id=conversation_id,
        client_input_id=client_input_id,
        status=TurnStatus.ACCEPTED,
        expected_context_version=index - 1,
        created_at=NOW + timedelta(seconds=index),
    )
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id=conversation_message_id(conversation_id, client_input_id),
            conversation_id=conversation_id,
            user_id=user_id,
            role="user",
            content=content or f"生成第 {index} 个视频",
            payload={
                "client_message_id": str(client_input_id),
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": None,
            },
            created_at=turn.created_at.isoformat(),
        )
    )
    return await repository.enqueue_turn_for_execution(user_id, turn, now=NOW)


async def _runtime(
    *,
    repository_type=MemoryVideoRuntimeRepository,
    open_first_interrupt: bool = False,
    block: bool = False,
    parallel_target: int = 0,
    recorder_fail: bool = False,
    recorder_delay_seconds: float = 0,
    fail_first_handler: bool = False,
    external_job_status: ExternalJobStatus | None = None,
    workflow_status: WorkflowStatus | None = None,
    clarify_first: bool = False,
    clarification_count: int = 0,
    worker_id: str = "worker-1",
    heartbeat_interval_seconds: float = 0.01,
) -> RuntimeHarness:
    task_store = MemoryPixelFlowTaskStore()
    repository = repository_type(task_store=task_store)
    clock = FakeClock()
    vault = TransientCredentialVault()
    handler = FakeVideoHandler(
        repository,
        open_first_interrupt=open_first_interrupt,
        block=block,
        parallel_target=parallel_target,
        fail_first=fail_first_handler,
        vault=vault,
        external_job_status=external_job_status,
        workflow_status=workflow_status,
    )
    graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
        checkpointer=InMemorySaver(),
    )
    recorder = FakePostCommitRecorder(
        fail=recorder_fail,
        vault=vault,
        delay_seconds=recorder_delay_seconds,
    )
    decision_service = FakeDecisionService(
        clarify_first=clarify_first,
        clarification_count=clarification_count,
        vault=vault,
    )
    executor = SupervisorTurnExecutor(
        repository=repository,
        task_store=task_store,
        decision_service=decision_service,
        graph=graph,
        credential_vault=vault,
        clock=clock,
        worker_id=worker_id,
        lease_duration=timedelta(seconds=30),
        heartbeat_step=timedelta(seconds=10),
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        scan_interval_seconds=0.01,
        post_commit_recorder=recorder,
    )
    return RuntimeHarness(
        executor=executor,
        repository=repository,
        task_store=task_store,
        handler=handler,
        clock=clock,
        vault=vault,
        recorder=recorder,
        decision_service=decision_service,
    )


@asynccontextmanager
async def _sql_clarification_runtime(
    database_path: Path,
    *,
    clarification_count: int = 1,
) -> AsyncIterator[tuple[RuntimeHarness, SQLTurnRegistrationStore]]:
    """使用同一 SQLite Session 工厂装配真实登记、Repository 与 Executor。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=(
                    *AGENT_RUNTIME_TABLES,  # 创建 Runtime 权威业务表。
                    *AGENT_RUNTIME_SUPPORT_TABLES,  # 创建 Runtime 辅助协调表。
                    PixelFlowConversationRow.__table__,
                    PixelFlowConversationMessageRow.__table__,
                ),
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    task_store = SQLPixelFlowTaskStore(session_factory)
    repository = SQLVideoRuntimeRepository(
        session_factory,
        task_store=task_store,
    )
    clock = FakeClock()
    vault = TransientCredentialVault()
    handler = FakeVideoHandler(repository, vault=vault)
    graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
        checkpointer=InMemorySaver(),
    )
    recorder = FakePostCommitRecorder(vault=vault)
    decision_service = FakeDecisionService(
        clarification_count=clarification_count,
        vault=vault,
    )
    executor = SupervisorTurnExecutor(
        repository=repository,
        task_store=task_store,
        decision_service=decision_service,
        graph=graph,
        credential_vault=vault,
        clock=clock,
        worker_id="sqlite-worker",
        lease_duration=timedelta(seconds=30),
        heartbeat_step=timedelta(seconds=10),
        heartbeat_interval_seconds=0.01,
        scan_interval_seconds=0.01,
        post_commit_recorder=recorder,
    )
    runtime = RuntimeHarness(
        executor=executor,
        repository=repository,
        task_store=task_store,
        handler=handler,
        clock=clock,
        vault=vault,
        recorder=recorder,
        decision_service=decision_service,
    )
    registration_store = SQLTurnRegistrationStore(
        repository=repository,
        task_store=task_store,
        video_repository=repository,
    )
    try:
        yield runtime, registration_store
    finally:
        await executor.aclose()
        await engine.dispose()


async def _register_clarification_turn(
    registration_store: MemoryTurnRegistrationStore | SQLTurnRegistrationStore,
    *,
    turn_id: str,
    client_input_id: UUID,
    expected_context_version: int,
    occurred_at: datetime,
    content: str = "帮我做一个",
):
    """通过真实 Turn registration 保存会推进全局版本的首轮输入。"""

    return await registration_store.register(
        user_id="user-1",
        conversation_id="conversation-1",
        message=PixelFlowConversationMessageRecord(
            message_id=conversation_message_id("conversation-1", client_input_id),
            conversation_id="conversation-1",
            user_id="user-1",
            role="user",
            content=content,
            payload={
                "client_message_id": str(client_input_id),
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": None,
            },
            created_at=occurred_at.isoformat(),
        ),
        turn=TurnRecord(
            turn_id=turn_id,
            conversation_id="conversation-1",
            client_input_id=client_input_id,
            status=TurnStatus.ACCEPTED,
            expected_context_version=expected_context_version,
            created_at=occurred_at,
        ),
        expected_context_version=expected_context_version,
        occurred_at=occurred_at,
    )


async def _register_clarification_response(
    registration_store: MemoryTurnRegistrationStore | SQLTurnRegistrationStore,
    *,
    interrupt_id: str,
    response_id: UUID,
    content: str,
    occurred_at: datetime,
):
    """通过 Task 10 原子端口保存人工响应及其响应前快照。"""

    return await registration_store.register_interrupt_response(
        user_id="user-1",
        conversation_id="conversation-1",
        interrupt_id=interrupt_id,
        request=InterruptResponseRequest(
            client_response_id=response_id,
            value={"content": content},
        ),
        occurred_at=occurred_at,
    )


async def runtime_with_two_turns_same_conversation() -> RuntimeHarness:
    runtime = await _runtime()
    await _seed_conversation(runtime.task_store, context_version=2)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=2)
    return runtime


async def runtime_with_blocked_handler() -> RuntimeHarness:
    # 这两个用例需要显式跨过原始租约边界，避免静态测试时钟被后台续租提前推进。
    runtime = await _runtime(block=True, heartbeat_interval_seconds=3600)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    return runtime


async def runtime_with_responded_interrupt() -> tuple[RuntimeHarness, StoredAgentInterrupt]:
    runtime = await _runtime(open_first_interrupt=True)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()
    opened = await runtime.repository.get_open_interrupt("user-1", "conversation-1")
    assert opened is not None
    response_id = UUID("10000000-0000-4000-8000-000000000009")
    responded = await runtime.repository.store_interrupt_response(
        "user-1",
        "conversation-1",
        opened.interrupt_id,
        client_response_id=response_id,
        response_value={
            "content": "确认并继续",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "explicit_action": ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id=opened.workflow_id,
                stage=str(opened.payload["stage"]),
                patch={},
            ).model_dump(mode="json"),
        },
        responded_at=NOW + timedelta(seconds=2),
    )
    return runtime, responded


@pytest.mark.asyncio
async def test_registered_turn_uses_its_pre_input_context_snapshot_version() -> None:
    """真实登记的输入加一不能让合法 Turn 在 ContextAssembler 被误判过期。"""

    runtime = await _runtime()
    await _seed_conversation(runtime.task_store, context_version=0)
    client_input_id = UUID("40000000-0000-4000-8000-000000000001")
    explicit_action = ExplicitActionSignal(
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
    )
    registration = await MemoryTurnRegistrationStore(
        repository=runtime.repository,
        task_store=runtime.task_store,
    ).register(
        user_id="user-1",
        conversation_id="conversation-1",
        message=PixelFlowConversationMessageRecord(
            message_id=conversation_message_id("conversation-1", client_input_id),
            conversation_id="conversation-1",
            user_id="user-1",
            role="user",
            content="生成一条商品介绍视频",
            payload={
                "client_message_id": str(client_input_id),
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": explicit_action.model_dump(mode="json"),
            },
            created_at=NOW.isoformat(),
        ),
        turn=TurnRecord(
            turn_id="turn-registered",
            conversation_id="conversation-1",
            client_input_id=client_input_id,
            status=TurnStatus.ACCEPTED,
            expected_context_version=0,
            created_at=NOW,
        ),
        expected_context_version=0,
        occurred_at=NOW,
    )
    assert registration.context_version == 1
    assert registration.turn.expected_context_version == 0

    next_client_input_id = UUID("40000000-0000-4000-8000-000000000002")
    queued = await MemoryTurnRegistrationStore(
        repository=runtime.repository,
        task_store=runtime.task_store,
    ).register(
        user_id="user-1",
        conversation_id="conversation-1",
        message=PixelFlowConversationMessageRecord(
            message_id=conversation_message_id(
                "conversation-1",
                next_client_input_id,
            ),
            conversation_id="conversation-1",
            user_id="user-1",
            role="user",
            content="再生成一条视频",
            payload={
                "client_message_id": str(next_client_input_id),
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": explicit_action.model_dump(mode="json"),
            },
            created_at=(NOW + timedelta(seconds=1)).isoformat(),
        ),
        turn=TurnRecord(
            turn_id="turn-queued",
            conversation_id="conversation-1",
            client_input_id=next_client_input_id,
            status=TurnStatus.ACCEPTED,
            expected_context_version=1,
            created_at=NOW + timedelta(seconds=1),
        ),
        expected_context_version=1,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert queued.context_version == 2
    assert queued.turn.status is TurnStatus.QUEUED

    claim = await runtime.repository.claim_turn(
        "user-1",
        "conversation-1",
        registration.turn.turn_id,
        lease_owner="version-worker",
        now=NOW,
        lease_expires_at=LEASE_EXPIRY,
    )
    assert claim is not None
    evidence = await runtime.executor._load_authoritative_evidence(claim)

    source = RepositoryContextSnapshotSource(
        task_store=runtime.task_store,
        repository=runtime.repository,
    )
    registered_snapshot = await source.load_context_snapshot(
        user_id="user-1",
        conversation_id="conversation-1",
        expected_context_version=registration.turn.expected_context_version,
    )
    assert registered_snapshot.messages == ()

    model_name = "executor-version-test"
    decision_service = SupervisorDecisionService(
        resolver=DeterministicTargetResolver(),
        classifier=None,
        validator=DecisionValidator(),
        context_assembler=ContextAssembler(
            source=source,
            model_name=model_name,
            model_profiles={
                model_name: ModelContextProfile(
                    model_name=model_name,
                    max_context_tokens=1_000_000,
                    max_output_tokens=64 * 1024,
                    tokenizer_strategy="provider_usage",
                )
            },
            budget_node="supervisor",
            clock=lambda: NOW,
            budget_policy_provider=ContextBudgetPolicyProvider(
                ContextBudgetConfig(require_verified_model_profile=False),
            ),
        ),
    )

    result = await decision_service.decide(evidence)

    assert result.decision.action is AgentAction.START_WORKFLOW
    assert result.validation_request.expected_context_version == 0
    assert result.validation_request.current_context_version == 0
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_processes_one_conversation_in_order() -> None:
    runtime = await runtime_with_two_turns_same_conversation()

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    turns = await runtime.repository.list_turns("user-1", "conversation-1")
    assert [item.status for item in turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]
    assert runtime.handler.turn_ids == ["turn-1", "turn-2"]
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_processes_different_conversations_in_parallel() -> None:
    runtime = await _runtime(parallel_target=2)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_conversation(
        runtime.task_store,
        conversation_id="conversation-2",
        context_version=1,
    )
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    await _seed_turn(
        runtime.repository,
        runtime.task_store,
        index=2,
        conversation_id="conversation-2",
    )

    await runtime.executor.recover_due_turns()
    await asyncio.wait_for(runtime.handler.entered.wait(), timeout=1)
    runtime.handler.release.set()
    await asyncio.wait_for(runtime.executor.wait_idle(), timeout=1)

    assert set(runtime.handler.turn_ids) == {"turn-1", "turn-2"}
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_reuses_completed_checkpoint_after_transient_commit_failure() -> None:
    runtime = await _runtime(repository_type=FailFirstCommitRepository)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()
    queued = await runtime.repository.get_turn("user-1", "turn-1")
    assert queued is not None and queued.status is TurnStatus.QUEUED
    runtime.clock.advance(timedelta(seconds=2))
    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    stored = await runtime.repository.get_turn("user-1", "turn-1")
    assert stored is not None and stored.status is TurnStatus.COMPLETED
    assert runtime.handler.turn_ids == ["turn-1"]
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_terminal_failure_wakes_next_turn_in_same_conversation() -> None:
    """前一 Turn 固定失败后仍应让同会话队首继续，避免队列永久饿死。"""

    runtime = await _runtime(fail_first_handler=True)
    await _seed_conversation(runtime.task_store, context_version=2)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=2)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    turns = await runtime.repository.list_turns("user-1", "conversation-1")
    assert [item.status for item in turns] == [
        TurnStatus.FAILED,
        TurnStatus.COMPLETED,
    ]
    assert runtime.handler.turn_ids == ["turn-1", "turn-2"]
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_shutdown_leaves_claim_recoverable() -> None:
    runtime = await runtime_with_blocked_handler()

    await runtime.executor.notify_turn(scope("turn-1"), credential=None)
    await asyncio.wait_for(runtime.handler.entered.wait(), timeout=1)
    await runtime.executor.aclose()

    stored = await runtime.repository.get_turn("user-1", "turn-1")
    assert stored is not None and stored.status is TurnStatus.PROCESSING
    assert await runtime.repository.list_due_turns(
        now=LEASE_EXPIRY + EPSILON,
        limit=10,
    )


@pytest.mark.asyncio
async def test_executor_close_is_terminal_for_start_and_notify() -> None:
    """关闭后的同一实例不能被误重启并重新领取持久化任务。"""

    runtime = await _runtime()
    await runtime.executor.aclose()

    with pytest.raises(SupervisorExecutorClosedError):
        await runtime.executor.start()
    with pytest.raises(SupervisorExecutorClosedError):
        await runtime.executor.notify_turn(scope("turn-1"), credential=None)


@pytest.mark.asyncio
async def test_executor_stale_lease_cannot_commit_after_takeover() -> None:
    runtime = await runtime_with_blocked_handler()
    await runtime.executor.notify_turn(scope("turn-1"), credential=None)
    await asyncio.wait_for(runtime.handler.entered.wait(), timeout=1)
    runtime.clock.advance(timedelta(seconds=31))
    takeover = await runtime.repository.claim_turn(
        "user-1",
        "conversation-1",
        "turn-1",
        lease_owner="worker-2",
        now=runtime.clock(),
        lease_expires_at=runtime.clock() + timedelta(seconds=30),
    )
    assert takeover is not None

    runtime.handler.release.set()
    await runtime.executor.wait_idle()

    stored = await runtime.repository.get_turn("user-1", "turn-1")
    assert stored is not None and stored.status is TurnStatus.PROCESSING
    assert runtime.executor.metrics_snapshot()["lease_conflicts"] >= 1
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_resumes_interrupt_on_original_turn_without_followup() -> None:
    runtime, opened = await runtime_with_responded_interrupt()

    await runtime.executor.recover_due_interrupts()
    await runtime.executor.wait_idle()

    turns = await runtime.repository.list_turns("user-1", opened.conversation_id)
    assert [item.turn_id for item in turns] == [opened.turn_id]
    assert turns[0].status in {TurnStatus.WAITING_USER, TurnStatus.COMPLETED}
    assert runtime.handler.turn_ids == [opened.turn_id, opened.turn_id]
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_interrupt_resume_delivers_request_credential_to_one_paid_start() -> None:
    """人工响应携带的凭据必须进入同一原 Turn，且只触发一次付费边界。"""

    runtime, responded = await runtime_with_responded_interrupt()
    credential = TransientTurnCredential("Bearer interrupt-secret-token")

    await runtime.executor.notify_interrupt(responded, credential=credential)
    await runtime.executor.wait_idle()

    assert runtime.handler.turn_ids == [responded.turn_id, responded.turn_id]
    assert runtime.handler.credential_start_calls == 1
    assert runtime.vault.get(responded.turn_id) is None
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_scan_task_mailbox_accepts_late_notify_credential() -> None:
    """扫描先建本地 task 时，HTTP 后到凭据不能被 existing 分支丢弃。"""

    runtime = await _runtime(repository_type=BlockingFirstClaimRepository)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)

    await runtime.executor.recover_due_turns()
    repository = runtime.repository
    assert isinstance(repository, BlockingFirstClaimRepository)
    await asyncio.wait_for(repository.claim_entered.wait(), timeout=1)
    credential = TransientTurnCredential("Bearer late-notify-secret")
    await runtime.executor.notify_turn(scope("turn-1"), credential=credential)
    repository.release_claim.set()
    await runtime.executor.wait_idle()

    assert runtime.handler.credential_start_calls == 1
    assert runtime.vault.get("turn-1") is None
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_old_done_callback_cannot_clear_replacement_task_credential() -> None:
    """旧 task 的延迟 done callback 不得清理同键新 task 的 mailbox。"""

    runtime = await _runtime()
    old_task = asyncio.create_task(asyncio.sleep(0))
    await old_task
    replacement_gate = asyncio.Event()
    replacement_task = asyncio.create_task(replacement_gate.wait())
    key = ("user-1", "turn-1")
    credential = TransientTurnCredential("Bearer replacement-task-secret")
    runtime.executor._local_tasks[key] = replacement_task
    runtime.executor._pending_credentials[key] = credential

    runtime.executor._forget_task(key, old_task)

    assert runtime.executor._local_tasks[key] is replacement_task
    assert runtime.executor._pending_credentials[key] is credential
    replacement_task.cancel()
    await asyncio.gather(replacement_task, return_exceptions=True)
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_global_clarification_without_active_workflow_resumes_original_turn() -> None:
    """全局追问不伪造 Workflow，并用持久响应继续同一个原 Turn。"""

    runtime = await _runtime(clarify_first=True)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()
    opened = await runtime.repository.get_open_interrupt(
        "user-1",
        "conversation-1",
    )
    assert opened is not None
    assert opened.kind == "clarification"
    assert opened.workflow_id is None
    waiting = await runtime.repository.get_turn("user-1", "turn-1")
    assert waiting is not None and waiting.status is TurnStatus.WAITING_USER
    assert runtime.handler.turn_ids == []

    await runtime.repository.store_interrupt_response(
        "user-1",
        "conversation-1",
        opened.interrupt_id,
        client_response_id=UUID("30000000-0000-4000-8000-000000000001"),
        response_value={
            "content": "创建一条商品介绍视频",
            "materials": [],
            "artifact_refs": [],
        },
        responded_at=NOW + timedelta(seconds=2),
    )
    await runtime.executor.recover_due_interrupts()
    await runtime.executor.wait_idle()

    turns = await runtime.repository.list_turns("user-1", "conversation-1")
    assert len(turns) == 1
    assert turns[0].status is TurnStatus.COMPLETED
    assert runtime.handler.turn_ids == ["turn-1"]
    closed = await runtime.repository.get_interrupt("user-1", opened.interrupt_id)
    assert closed is not None and closed.status == "closed"
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_atomic_response_registration_resumes_global_clarification_snapshot() -> None:
    """响应前版本必须恢复 Graph 快照并完成真实登记的同一个 Turn。"""

    runtime = await _runtime(clarify_first=True)
    await _seed_conversation(runtime.task_store, context_version=0)
    registration_store = MemoryTurnRegistrationStore(
        repository=runtime.repository,
        task_store=runtime.task_store,
        video_repository=runtime.repository,
    )
    client_input_id = UUID("41000000-0000-4000-8000-000000000001")
    initial = await _register_clarification_turn(
        registration_store,
        turn_id="turn-registered-clarification",
        client_input_id=client_input_id,
        expected_context_version=0,
        occurred_at=NOW,
    )
    assert initial.context_version == 1

    await runtime.executor.notify_turn(
        scope(initial.turn.turn_id),
        credential=None,
    )
    await runtime.executor.wait_idle()
    opened = await runtime.repository.get_open_interrupt(
        "user-1",
        "conversation-1",
    )
    assert opened is not None and opened.kind == "clarification"

    response = await _register_clarification_response(
        registration_store,
        interrupt_id=opened.interrupt_id,
        response_id=UUID("41000000-0000-4000-8000-000000000002"),
        content="创建一条商品介绍视频",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert response.context_version == 2
    assert response.turn.turn_id == initial.turn.turn_id
    assert response.turn.expected_context_version == 1

    await runtime.executor.notify_interrupt(response.interrupt)
    await runtime.executor.wait_idle()

    stored = await runtime.repository.get_turn("user-1", initial.turn.turn_id)
    assert stored is not None and stored.status is TurnStatus.COMPLETED, json.dumps(
        runtime.executor.metrics_snapshot(),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert runtime.handler.turn_ids == [initial.turn.turn_id]
    assert runtime.executor.metrics_snapshot()["reason_codes"].get(
        "contract_validation_failed",
        0,
    ) == 0
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_sqlite_atomic_response_resumes_global_clarification_snapshot(
    tmp_path: Path,
) -> None:
    """SQLite 原子登记也必须按响应前版本恢复同一个 Turn。"""

    async with _sql_clarification_runtime(
        tmp_path / "clarification-resume.db",
    ) as (runtime, registration_store):
        await _seed_conversation(runtime.task_store, context_version=0)
        initial = await _register_clarification_turn(
            registration_store,
            turn_id="turn-sqlite-clarification",
            client_input_id=UUID("42000000-0000-4000-8000-000000000001"),
            expected_context_version=0,
            occurred_at=NOW,
        )
        assert initial.context_version == 1
        await runtime.executor.notify_turn(
            scope(initial.turn.turn_id),
            credential=None,
        )
        await runtime.executor.wait_idle()
        opened = await runtime.repository.get_open_interrupt(
            "user-1",
            "conversation-1",
        )
        assert opened is not None and opened.kind == "clarification"

        response = await _register_clarification_response(
            registration_store,
            interrupt_id=opened.interrupt_id,
            response_id=UUID("42000000-0000-4000-8000-000000000002"),
            content="创建一条商品介绍视频",
            occurred_at=NOW + timedelta(seconds=1),
        )
        assert response.context_version == 2
        assert response.turn.expected_context_version == 1
        await runtime.executor.notify_interrupt(response.interrupt)
        await runtime.executor.wait_idle()

        stored = await runtime.repository.get_turn("user-1", initial.turn.turn_id)
        assert stored is not None and stored.status is TurnStatus.COMPLETED
        assert runtime.handler.turn_ids == [initial.turn.turn_id]


@pytest.mark.asyncio
async def test_consecutive_global_clarifications_advance_resume_snapshot() -> None:
    """连续追问必须按每次响应前版本推进，不得退回首轮快照。"""

    runtime = await _runtime(clarification_count=2)
    await _seed_conversation(runtime.task_store, context_version=0)
    registration_store = MemoryTurnRegistrationStore(
        repository=runtime.repository,
        task_store=runtime.task_store,
        video_repository=runtime.repository,
    )
    initial = await _register_clarification_turn(
        registration_store,
        turn_id="turn-consecutive-clarification",
        client_input_id=UUID("43000000-0000-4000-8000-000000000001"),
        expected_context_version=0,
        occurred_at=NOW,
    )
    await runtime.executor.notify_turn(scope(initial.turn.turn_id), credential=None)
    await runtime.executor.wait_idle()
    first = await runtime.repository.get_open_interrupt(
        "user-1",
        "conversation-1",
    )
    assert first is not None

    first_response = await _register_clarification_response(
        registration_store,
        interrupt_id=first.interrupt_id,
        response_id=UUID("43000000-0000-4000-8000-000000000002"),
        content="想做商品内容",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert first_response.context_version == 2
    assert first_response.turn.expected_context_version == 1
    source = RepositoryContextSnapshotSource(
        task_store=runtime.task_store,
        repository=runtime.repository,
    )
    first_resume_snapshot = await source.load_context_snapshot(
        user_id="user-1",
        conversation_id="conversation-1",
        expected_context_version=first_response.turn.expected_context_version,
    )
    assert [
        item.payload["message_id"]
        for item in first_resume_snapshot.messages
        if item.payload["role"] == "user"
    ] == [initial.message.message_id]
    await runtime.executor.notify_interrupt(first_response.interrupt)
    await runtime.executor.wait_idle()
    second = await runtime.repository.get_open_interrupt(
        "user-1",
        "conversation-1",
    )
    assert second is not None and second.interrupt_id != first.interrupt_id
    waiting = await runtime.repository.get_turn("user-1", initial.turn.turn_id)
    assert waiting is not None and waiting.status is TurnStatus.WAITING_USER

    second_response = await _register_clarification_response(
        registration_store,
        interrupt_id=second.interrupt_id,
        response_id=UUID("43000000-0000-4000-8000-000000000003"),
        content="创建一条商品介绍视频",
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert second_response.context_version == 3
    assert second_response.turn.expected_context_version == 2
    second_resume_snapshot = await source.load_context_snapshot(
        user_id="user-1",
        conversation_id="conversation-1",
        expected_context_version=second_response.turn.expected_context_version,
    )
    assert [
        item.payload["message_id"]
        for item in second_resume_snapshot.messages
        if item.payload["role"] == "user"
    ] == [
        initial.message.message_id,
        first_response.message.message_id,
    ]
    await runtime.executor.notify_interrupt(second_response.interrupt)
    await runtime.executor.wait_idle()

    stored = await runtime.repository.get_turn("user-1", initial.turn.turn_id)
    assert stored is not None and stored.status is TurnStatus.COMPLETED
    assert [item.expected_context_version for item in runtime.decision_service.evidence] == [
        0,
        1,
        2,
    ]
    assert runtime.handler.turn_ids == [initial.turn.turn_id]
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_global_clarification_resume_preserves_legally_queued_input() -> None:
    """追问期间的新输入保持排队，并在原 Turn 恢复后按自己的快照执行。"""

    runtime = await _runtime(clarify_first=True)
    await _seed_conversation(runtime.task_store, context_version=0)
    registration_store = MemoryTurnRegistrationStore(
        repository=runtime.repository,
        task_store=runtime.task_store,
        video_repository=runtime.repository,
    )
    initial = await _register_clarification_turn(
        registration_store,
        turn_id="turn-clarification-owner",
        client_input_id=UUID("44000000-0000-4000-8000-000000000001"),
        expected_context_version=0,
        occurred_at=NOW,
    )
    await runtime.executor.notify_turn(scope(initial.turn.turn_id), credential=None)
    await runtime.executor.wait_idle()
    opened = await runtime.repository.get_open_interrupt(
        "user-1",
        "conversation-1",
    )
    assert opened is not None

    queued = await _register_clarification_turn(
        registration_store,
        turn_id="turn-queued-during-clarification",
        client_input_id=UUID("44000000-0000-4000-8000-000000000002"),
        expected_context_version=1,
        occurred_at=NOW + timedelta(seconds=1),
        content="再准备一条品牌视频",
    )
    assert queued.context_version == 2
    assert queued.turn.status is TurnStatus.ACCEPTED
    assert await runtime.repository.list_due_turns(
        now=NOW + timedelta(seconds=1),
        limit=10,
    ) == []
    response = await _register_clarification_response(
        registration_store,
        interrupt_id=opened.interrupt_id,
        response_id=UUID("44000000-0000-4000-8000-000000000003"),
        content="创建一条商品介绍视频",
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert response.context_version == 3
    assert response.turn.expected_context_version == 2

    await runtime.executor.notify_interrupt(response.interrupt)
    await runtime.executor.wait_idle()

    turns = await runtime.repository.list_turns("user-1", "conversation-1")
    assert [item.turn_id for item in turns] == [
        initial.turn.turn_id,
        queued.turn.turn_id,
    ]
    assert [item.status for item in turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]
    assert runtime.handler.turn_ids == [initial.turn.turn_id, queued.turn.turn_id]
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_credential_exists_only_during_graph_and_is_cleared() -> None:
    runtime = await _runtime()
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    credential = TransientTurnCredential("Bearer executor-secret-token")

    await runtime.executor.notify_turn(scope("turn-1"), credential=credential)
    await runtime.executor.wait_idle()

    assert runtime.handler.credential_seen is True
    assert runtime.vault.get("turn-1") is None
    assert runtime.decision_service.credential_seen == [False]
    assert runtime.recorder.credential_seen == [False]
    assert "executor-secret-token" not in json.dumps(
        runtime.executor.metrics_snapshot(),
        ensure_ascii=False,
    )
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_executor_cancellation_discards_graph_credential() -> None:
    """Graph 取消必须同时清理 Vault 映射与凭据背后的不透明 secret。"""

    from pixelflow.agent_workflows.video import live_capabilities

    runtime = await _runtime(block=True, heartbeat_interval_seconds=3600)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    credential = TransientTurnCredential("Bearer cancelled-secret-token")

    await runtime.executor.notify_turn(scope("turn-1"), credential=credential)
    await asyncio.wait_for(runtime.handler.entered.wait(), timeout=1)
    await runtime.executor.aclose()

    assert runtime.vault.get("turn-1") is None
    assert credential not in live_capabilities._TRANSIENT_CREDENTIAL_SECRETS


@pytest.mark.asyncio
async def test_post_commit_recorder_is_fail_open_and_runs_only_after_commit() -> None:
    runtime = await _runtime(recorder_fail=True)
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    stored = await runtime.repository.get_turn("user-1", "turn-1")
    assert stored is not None and stored.status is TurnStatus.COMPLETED
    assert len(runtime.recorder.records) == 1
    record_json = json.dumps(runtime.recorder.records[0], ensure_ascii=False)
    assert "生成第 1 个视频" not in record_json
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_commit_stops_heartbeat_before_slow_recorder_and_next_turn_wakeup() -> None:
    """终态提交后 heartbeat 不能把合法慢记录器和同会话后继 Turn 当成冲突。"""

    runtime = await _runtime(recorder_delay_seconds=0.08)
    await _seed_conversation(runtime.task_store, context_version=2)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=2)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    turns = await runtime.repository.list_turns("user-1", "conversation-1")
    assert [item.status for item in turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]
    assert runtime.handler.turn_ids == ["turn-1", "turn-2"]
    assert runtime.recorder.completed_count == 2
    assert runtime.executor.metrics_snapshot()["lease_conflicts"] == 0
    await runtime.executor.aclose()


def test_agent_runtime_executor_public_import_stays_lazy_in_fresh_process() -> None:
    """防止新增公开导出恢复 agent_runtime 包的 eager import 环。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pixelflow.agent_runtime import SupervisorTurnExecutor; "
                "print(SupervisorTurnExecutor.__name__)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SupervisorTurnExecutor"


def test_metrics_snapshot_is_deep_copied_and_uses_fixed_safe_dimensions() -> None:
    """防止调用方修改指标内部状态，或把敏感动态值扩散为指标键。"""

    runtime = asyncio.run(_runtime())
    first = runtime.executor.metrics_snapshot()
    first["actions"]["start_workflow"] = 999
    second = runtime.executor.metrics_snapshot()
    assert second["actions"]["start_workflow"] == 0
    assert set(second["actions"]) == {item.value for item in AgentAction}
    serialized = json.dumps(second, ensure_ascii=False)
    assert "Authorization" not in serialized
    assert "https://" not in serialized


def test_metrics_observer_counts_all_m06_states_without_dynamic_keys() -> None:
    """六态只接受 ProviderJobOutcome DTO，未知供应商值不能成为指标维度。"""

    runtime = asyncio.run(_runtime())
    for state in ProviderJobOutcome:
        runtime.executor.observe_external_job_state(state)

    with pytest.raises(TypeError):
        runtime.executor.observe_external_job_state(
            "unknown-Authorization-https://provider.invalid"
        )

    snapshot = runtime.executor.metrics_snapshot()
    assert snapshot["external_job_states"] == {
        "polling": 1,
        "succeeded": 1,
        "failed": 1,
        "paused_quota": 1,
        "timeout": 1,
        "expired": 1,
    }
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "Authorization" not in serialized
    assert "https://" not in serialized


@pytest.mark.asyncio
async def test_committed_handler_result_observes_validated_external_job_state() -> None:
    """经 WorkflowDispatchResult 和 commit 的 pending job 应自动更新 M06 指标。"""

    runtime = await _runtime(
        repository_type=MetricsOnlyCommitRepository,
        external_job_status=ExternalJobStatus.POLLING,
    )
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    assert runtime.executor.metrics_snapshot()["external_job_states"]["polling"] == 1
    await runtime.executor.aclose()


@pytest.mark.asyncio
async def test_committed_handler_result_observes_paused_quota_workflow_state() -> None:
    """经 WorkflowDispatchResult 和 commit 的额度暂停状态应自动更新指标。"""

    runtime = await _runtime(
        repository_type=MetricsOnlyCommitRepository,
        workflow_status=WorkflowStatus.PAUSED_QUOTA,
    )
    await _seed_conversation(runtime.task_store, context_version=1)
    await _seed_turn(runtime.repository, runtime.task_store, index=1)

    await runtime.executor.recover_due_turns()
    await runtime.executor.wait_idle()

    assert runtime.executor.metrics_snapshot()["external_job_states"]["paused_quota"] == 1
    await runtime.executor.aclose()
