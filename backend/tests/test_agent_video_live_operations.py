"""视频 live Operation 桥、临时凭据和 stage 路由合同。"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import JsonValue, ValidationError
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import null
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime import SupervisorTurnExecutor, SupervisorTurnScope
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentIntent,
    ExplicitActionSignal,
    ExternalJobStatus,
    InterruptResponseRequest,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    WorkflowCommand,
    make_agent_runtime_graph,
    supervisor_namespace,
    workflow_namespace,
)
from pixelflow.agent_runtime.identity import conversation_message_id
from pixelflow.agent_runtime.jobs import (
    OperationLeaseCoordinator,
    OperationManualRecoveryAction,
    OperationQuotaCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    ProviderJobOutcome,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    AgentRuntimeQuotaResumeStaleError,
    AgentRuntimeRecordConflictError,
    MemoryVideoRuntimeRepository,
    SQLVideoRuntimeRepository,
    StoredAgentInterrupt,
    SupervisorProjectionMessage,
    TurnExecutionLeaseConflictError,
    VideoRuntimeRepository,
    VideoTurnCommit,
    VideoWorkflowStateConflictError,
)
from pixelflow.agent_runtime.persistence.models import PixelFlowAgentInterruptRow
from pixelflow.agent_runtime.persistence.repositories import (
    EventDeliveryClaim,
    MemoryAgentRuntimeRepository,
)
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationCandidate,
    ActionClassificationRequest,
    ActionClassificationTarget,
    DecisionValidationRequest,
    DeterministicResolution,
    DeterministicResolutionStatus,
)
from pixelflow.agent_workflows.video import (
    VideoLiveStateConflictError,
    VideoLiveWorkflowHandler,
    VideoPlanningWorkflowService,
    VideoPostProductionWorkflowService,
    VideoSceneGenerationWorkflowService,
    VideoScenePackageWorkflowService,
    WorkflowDispatchResult,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)
from pixelflow.agent_workflows.video.live_capabilities import TransientTurnCredential
from pixelflow.agent_workflows.video.live_operations import (
    TransientCredentialVault,
    VideoLiveOperationBridge,
    VideoOperationAdapterResolver,
    VideoOperationCompletionHandler,
    VideoOperationQuotaStateHandler,
    VideoOperationStartRequest,
    _operation_completion_interrupt,
    _OperationEventClaimRegistry,
)
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.intake.forms import draft_creative_directions, validate_form
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

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
USER_ID = "user-live-operation"
CONVERSATION_ID = "conversation-live-operation"
WORKFLOW_ID = "workflow-live-operation"
STAGE = "generate_scene_video:scene-1"
RepositoryKind = Literal["memory", "sql"]
PROVIDER_REQUEST: dict[str, JsonValue] = {
    "scene_id": "scene-1",
    "prompt": "固定测试视频提示词",
}
FAKE_AUTHORIZATION = "Bearer task8-test-only"
VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 30,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_model_capabilities": {
        "generation_types": ["text_to_video", "image_to_video"],
        "upload_file_types": ["image"],
        "aspect_ratios": ["9:16", "16:9"],
        "sizes": ["1080p"],
        "sound_options": ["on", "off"],
        "durations_sec": list(range(4, 16)),
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


class CountingProvider:
    def __init__(self) -> None:
        self.start_calls = 0
        self.status_calls = 0

    async def start(
        self,
        provider_request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        normalized_request = dict(provider_request)
        assert normalized_request
        assert "authorization" not in normalized_request
        assert authorization == FAKE_AUTHORIZATION
        assert idempotency_key.startswith("operation:v1:sha256:")
        self.start_calls += 1
        await asyncio.sleep(0)
        scene_id = normalized_request.get("scene_id")
        suffix = str(scene_id).removeprefix("scene-") if scene_id else "1"
        return {
            "job_id": f"provider-live-operation-{suffix}",
            "status": "running",
            "result": {"progress": 0},
        }

    async def status(self, provider_job_id: str) -> object:
        assert provider_job_id == "provider-live-operation-1"
        self.status_calls += 1
        return {
            "job_id": provider_job_id,
            "status": "running",
            "result": {"progress": 25},
        }


class _HttpStatusError(RuntimeError):
    """只向 Adapter 暴露 HTTP 状态码，不携带供应商正文。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("测试异常正文不得进入稳定状态")


class ScriptedProvider(CountingProvider):
    """按顺序返回固定 start/status 结果，验证恢复状态机。"""

    def __init__(
        self,
        *,
        start_results: list[object] | None = None,
        status_results: list[object] | None = None,
    ) -> None:
        super().__init__()
        self._start_results = list(start_results or [])
        self._status_results = list(status_results or [])
        self.status_job_ids: list[str] = []

    async def start(
        self,
        provider_request: Mapping[str, JsonValue],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        self.start_calls += 1
        assert dict(provider_request)
        assert authorization == FAKE_AUTHORIZATION
        assert idempotency_key.startswith("operation:v1:sha256:")
        if not self._start_results:
            return {
                "job_id": f"provider-scripted-{self.start_calls}",
                "status": "running",
                "result": {"progress": 0},
            }
        result = self._start_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def status(self, provider_job_id: str) -> object:
        self.status_calls += 1
        self.status_job_ids.append(provider_job_id)
        if not self._status_results:
            raise AssertionError("测试未配置下一条 Provider status 结果")
        result = self._status_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _MutableClock:
    """为租约和轮询测试提供显式可推进时钟。"""

    def __init__(self, now: datetime = NOW) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value

    def advance(self, **delta: float) -> None:
        self.value += timedelta(**delta)


class _RecordingResumer:
    """记录 M06 完成事件，不执行真实 Graph。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, AgentEvent, str]] = []

    async def resume_external_job(
        self,
        namespace: object,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls.append((namespace, completion_event, idempotency_key))


class _RecordingExternalJobObserver:
    """记录本进程已经确认提交的固定 Provider 六态。"""

    def __init__(self) -> None:
        self.states: list[ProviderJobOutcome] = []

    def observe_external_job_state(self, state: ProviderJobOutcome) -> None:
        self.states.append(state)


class _ExplicitVideoDecisionService:
    """让真实 Executor 只消费权威消息中已经登记的结构化视频动作。"""

    async def decide(self, evidence):
        explicit = evidence.explicit_action
        if explicit is None:
            raise AssertionError("集成测试必须提供结构化视频动作")
        candidates = tuple(
            ActionClassificationCandidate(
                workflow_id=item.workflow_id,
                intent=AgentIntent.VIDEO,
                status=item.status,
                current_stage=item.current_stage,
                stage_version=item.stage_version,
                context_version=item.context_version,
                allowed_actions=tuple(AgentAction),
                targets=(
                    ActionClassificationTarget(
                        target_stage=item.current_stage,
                        target_artifact_ref=explicit.artifact_ref,
                    ),
                ),
            )
            for item in evidence.workflows
        )
        resolution = DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=explicit.action,
            intent=explicit.intent or AgentIntent.VIDEO,
            target_workflow_id=explicit.workflow_id,
            target_stage=explicit.stage,
            target_artifact_ref=explicit.artifact_ref,
            reason_code="task13_explicit_action",
            candidate_workflow_ids=(explicit.workflow_id,),
        )
        classification = ActionClassificationRequest(
            turn_id=evidence.turn.turn_id,
            content=evidence.content,
            deterministic_resolution=resolution,
            candidates=candidates,
            context_summary="Task 13 完成恢复集成测试",
        )
        decision = ActionDecision(
            action=explicit.action,
            intent=explicit.intent or AgentIntent.VIDEO,
            target_workflow_id=explicit.workflow_id,
            target_stage=explicit.stage,
            target_artifact_ref=explicit.artifact_ref,
            confidence=1,
            requires_confirmation=False,
            patch=dict(explicit.patch),
            reason_code="task13_explicit_action",
            idempotency_key=classification.idempotency_key,
        )
        return SimpleNamespace(
            decision=decision,
            validation_request=DecisionValidationRequest(
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
            ),
            context=object(),
            answer_message=None,
        )


class _FailingExternalJobObserver(_RecordingExternalJobObserver):
    """证明指标失败不能反向破坏已提交的 M06 完成投递。"""

    def observe_external_job_state(self, state: ProviderJobOutcome) -> None:
        super().observe_external_job_state(state)
        raise RuntimeError("测试指标端口不可用")


class _FailOnceResumer:
    """第一次模拟 Graph Handler 崩溃，后续交给真实完成 Handler。"""

    def __init__(self, delegate: VideoOperationCompletionHandler) -> None:
        self._delegate = delegate
        self.calls = 0

    async def resume_external_job(
        self,
        namespace: object,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("模拟 Graph checkpoint 前退出")
        await self._delegate.resume_external_job(
            namespace,
            completion_event=completion_event,
            idempotency_key=idempotency_key,
        )


class _FailOnceQuotaResumer:
    """第一次在 Graph checkpoint 前退出，随后允许测试重启接管。"""

    def __init__(self, delegate: VideoOperationQuotaStateHandler) -> None:
        self._delegate = delegate
        self.calls = 0

    async def resume_external_job_quota(
        self,
        namespace: object,
        *,
        quota_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("模拟 quota Graph checkpoint 前退出")
        await self._delegate.resume_external_job_quota(
            namespace,
            quota_event=quota_event,
            idempotency_key=idempotency_key,
        )


def request(*, attempt: int = 1) -> VideoOperationStartRequest:
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=STAGE,
        stage_version=3,
        attempt=attempt,
        provider_request=PROVIDER_REQUEST,
    )
    return VideoOperationStartRequest(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        operation_request=operation_request,
        provider_request=PROVIDER_REQUEST,
    )


def build_live_operations(
    provider: CountingProvider,
    *,
    clock: _MutableClock | None = None,
    repository: MemoryAgentRuntimeRepository | None = None,
) -> VideoLiveOperationBridge:
    job_sequence = itertools.count(1)
    return VideoLiveOperationBridge(
        repository=repository or MemoryAgentRuntimeRepository(),
        resolver=VideoOperationAdapterResolver(
            {
                "generate_scene_video": ProviderJobAdapter(provider),
                "merge_video": ProviderJobAdapter(provider),
                "quality_review": ProviderJobAdapter(provider),
                "jianying_draft": ProviderJobAdapter(provider),
            },
        ),
        lease_owner="task8-test-worker",
        clock=clock or (lambda: NOW),
        job_id_factory=lambda: f"operation-live-video-{next(job_sequence)}",
    )


class _UnusedCapabilities:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"缺凭据分支不应调用能力端口：{name}")


class _PaidSceneAssetCapabilities:
    """只实现 Plan 确认后真实恢复需要的付费场景资产边界。"""

    def __init__(self) -> None:
        self.generate_scene_assets_calls = 0

    async def generate_scene_assets(
        self,
        state: object,
        *,
        credential: TransientTurnCredential,
    ) -> dict[str, object]:
        del credential
        self.generate_scene_assets_calls += 1
        assets = copy.deepcopy(state.scene_package.global_assets)
        for item in assets["characters"]:
            item["three_view_images"] = [
                f"https://assets.example.com/{item['asset_id']}.png"
            ]
        for collection in ("scenes", "props"):
            for item in assets[collection]:
                item["images"] = [
                    f"https://assets.example.com/{item['asset_id']}.png"
                ]
        return {"ok": True, "global_assets": assets}


class _Clock:
    def now(self) -> datetime:
        return NOW


class _SeededVideoRepository(MemoryVideoRuntimeRepository):
    async def seed_state(self, state: object):
        workflow = project_video_workflow_state(state)
        envelope = encode_video_workflow_state(
            user_id=USER_ID,
            state=state,
            workflow_version=1,
            last_turn_id="turn-seed-live-operation",
            last_action_key="seed:live-operation",
        )
        self._video_states[(USER_ID, workflow.workflow_id)] = envelope
        self._workflows[(USER_ID, workflow.workflow_id)] = workflow
        return workflow

    async def seed_envelope(self, envelope, workflow) -> None:
        """模拟 Task4 已提交上一版 Handler 结果。"""

        self._video_states[(USER_ID, workflow.workflow_id)] = envelope
        self._workflows[(USER_ID, workflow.workflow_id)] = workflow


@asynccontextmanager
async def _video_repository(
    kind: RepositoryKind,
    database_path: Path,
    *,
    completion_clock: _MutableClock | None = None,
) -> AsyncIterator[tuple[VideoRuntimeRepository, object]]:
    """创建同时实现 M06 与 Task4 合同的 Memory/SQLite Repository。"""

    if kind == "memory":
        store = MemoryPixelFlowTaskStore()
        repository = MemoryVideoRuntimeRepository(
            task_store=store,
            completion_clock=(
                (lambda: NOW)
                if completion_clock is None
                else completion_clock.now
            ),
        )
        yield repository, store
        return

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=(
                    tuple(AGENT_RUNTIME_TABLES)
                    + tuple(AGENT_RUNTIME_SUPPORT_TABLES)
                    + (
                        PixelFlowConversationRow.__table__,
                        PixelFlowConversationMessageRow.__table__,
                    )
                ),
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SQLPixelFlowTaskStore(session_factory)
    try:
        repository = SQLVideoRuntimeRepository(
            session_factory,
            task_store=store,
            completion_clock=(
                (lambda: NOW)
                if completion_clock is None
                else completion_clock.now
            ),
        )
        yield repository, store
    finally:
        await engine.dispose()


async def _seed_conversation(store: object) -> None:
    """为公开 Repository 提交边界建立视频会话。"""

    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=CONVERSATION_ID,
            user_id=USER_ID,
            orchestration_mode="supervisor_v1",
            orchestration_version=1,
            context={
                "__agent_runtime": {
                    "mode": "primary",
                    "enabled_intents": ["video"],
                    "primary_execution_ready": True,
                    "context_compaction_enabled": True,
                    "context_version": 0,
                }
            },
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
        )
    )


async def _commit_seed_state(
    repository: VideoRuntimeRepository,
    store: object,
    state: object,
) -> None:
    """通过 Task4 公开 Turn 提交接口保存第一版视频权威状态。"""

    del store
    turn = TurnRecord(
        turn_id="turn-seed-completion",
        conversation_id=CONVERSATION_ID,
        client_input_id=UUID("00000000-0000-0000-0000-000000000801"),
        status=TurnStatus.ACCEPTED,
        target_workflow_id=WORKFLOW_ID,
        decision=None,
        expected_context_version=0,
        created_at=NOW,
    )
    await repository.enqueue_turn_for_execution(USER_ID, turn, now=NOW)
    claim = await repository.claim_turn(
        USER_ID,
        CONVERSATION_ID,
        turn.turn_id,
        lease_owner="task8-seed-worker",
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    assert claim is not None
    workflow = project_video_workflow_state(state)
    envelope = encode_video_workflow_state(
        user_id=USER_ID,
        state=state,
        workflow_version=1,
        last_turn_id=turn.turn_id,
        last_action_key="task8:seed-completion",
    )
    await repository.commit_turn(
        claim,
        VideoTurnCommit(
            decision=ActionDecision(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                target_workflow_id=WORKFLOW_ID,
                target_stage=workflow.current_stage,
                confidence=1,
                requires_confirmation=False,
                patch={},
                reason_code="task8_seed_completion",
                idempotency_key="task8:seed-completion",
            ),
            turn_status=TurnStatus.COMPLETED,
            workflow_state=envelope,
            workflow=workflow,
            expected_workflow_version=0,
            messages=(),
            update_active_workflow=True,
            active_workflow_id=WORKFLOW_ID,
            occurred_at=NOW + timedelta(seconds=1),
        ),
    )


async def _start_generation_operations(
    operations: VideoLiveOperationBridge,
) -> object:
    """领取并启动当前场景包的全部 M06 分镜 Operation。"""

    service = VideoSceneGenerationWorkflowService()
    port = operations.bind(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    )
    state = await service.start_from_reviewed_scene_package(
        _reviewed_scene_package_state(),
        operation_port=port,
        now=NOW,
    )
    requests = {str(item["scene_id"]): item for item in state.generation_requests}
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    try:
        for pending in state.pending_operations:
            scene_id = pending.stage.partition(":")[2]
            await operations.start(
                operations.start_request_from_claim(
                    user_id=USER_ID,
                    conversation_id=CONVERSATION_ID,
                    job=pending,
                    stage_version=state.stage_version,
                    provider_request=requests[scene_id],
                ),
                credential=credential,
            )
    finally:
        credential.discard()
    return await service.resume(state, operation_port=port, now=NOW)


async def _pause_first_generation_operation(
    repository: VideoRuntimeRepository,
    store: object,
    clock: _MutableClock,
    provider: ScriptedProvider,
) -> tuple[VideoLiveOperationBridge, object, object, object, object]:
    """建立真实 M11 pending Operation，并原子生成第一轮 quota pause Event。"""

    operations = build_live_operations(
        provider,
        clock=clock,
        repository=repository,
    )
    state = await _start_generation_operations(operations)
    await _commit_seed_state(repository, store, state)
    pending = state.pending_operations[0]
    clock.advance(seconds=3)
    leased = await OperationLeaseCoordinator(
        repository,
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    ).claim(
        pending.job_id,
        lease_owner="task4-quota-poller",
        now=clock.now(),
        lease_expires_at=clock.now() + timedelta(seconds=30),
    )
    assert leased is not None
    paused = await OperationQuotaCoordinator(
        repository,
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    ).record_pause(
        pending.job_id,
        lease_owner="task4-quota-poller",
        now=clock.now(),
    )
    envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert envelope is not None
    return operations, state, pending, paused, envelope


async def _paused_quota_handler_harness() -> SimpleNamespace:
    """建立已投影授权中断的真实 Memory Handler 测试环境。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    store = MemoryPixelFlowTaskStore()
    repository = MemoryVideoRuntimeRepository(
        task_store=store,
        completion_clock=clock.now,
    )
    await _seed_conversation(store)
    operations, state, pending, paused, envelope = (
        await _pause_first_generation_operation(
            repository,
            store,
            clock,
            provider,
        )
    )
    pause_claim = await repository.claim_operation_quota_event(
        USER_ID,
        CONVERSATION_ID,
        paused.event.event_id,
        pending.job_id,
        quota_pause_revision=1,
        quota_state="paused",
        lease_owner="task5-pause-projection",
        now=clock.now(),
        lease_expires_at=clock.now() + timedelta(seconds=30),
    )
    assert pause_claim is not None
    projection = VideoOperationQuotaProjectionService().build(
        user_id=USER_ID,
        envelope=envelope,
        operation=paused.operation,
        quota_event=pause_claim.event,
    )
    await repository.commit_operation_quota_state(
        pause_claim,
        user_id=USER_ID,
        workflow_state=projection.workflow_state,
        workflow=projection.workflow,
        expected_workflow_version=envelope.workflow_version,
        open_interrupt=projection.open_interrupt,
        close_interrupt_revision=None,
        occurred_at=clock.now(),
    )
    return SimpleNamespace(
        clock=clock,
        provider=provider,
        repository=repository,
        operations=operations,
        state=state,
        pending=pending,
        paused=paused,
        workflow=projection.workflow,
        interrupt=projection.open_interrupt,
    )


async def _reopened_quota_handler_harness() -> SimpleNamespace:
    """模拟其他分镜完成后，Repository 已前进而暂停 checkpoint 保持不变。"""

    harness = await _paused_quota_handler_harness()
    paused_workflow = harness.workflow
    envelope = await harness.repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert envelope is not None
    state = decode_video_workflow_state(envelope)
    state = replace(state, context_version=state.context_version + 1)
    running_workflow = project_video_workflow_state(state)
    advanced_envelope = encode_video_workflow_state(
        user_id=USER_ID,
        state=state,
        workflow_version=envelope.workflow_version + 1,
        last_turn_id=envelope.last_turn_id,
        last_action_key="completion:other-scene",
    )
    harness.repository._video_states[(USER_ID, WORKFLOW_ID)] = advanced_envelope
    harness.repository._workflows[(USER_ID, WORKFLOW_ID)] = running_workflow
    harness.paused_workflow = paused_workflow
    harness.running_workflow = running_workflow
    harness.current_envelope = advanced_envelope
    return harness


def _quota_retry_command(
    harness: SimpleNamespace,
    *,
    suffix: str,
    job_id: str | None = None,
    revision: int = 1,
) -> WorkflowCommand:
    """构造只携带安全 job/revision 的 quota 授权恢复动作。"""

    command = explicit_stage_command(
        harness.workflow,
        action=AgentAction.RETRY_FAILED,
        patch={
            "job_id": job_id or harness.pending.job_id,
            "quota_pause_revision": revision,
        },
        suffix=suffix,
    )
    return replace(
        command,
        source_interrupt_id=harness.interrupt.interrupt_id,
        decision=command.decision.model_copy(
            update={"target_stage": harness.pending.stage}
        ),
    )


async def _commit_next_state(
    repository: VideoRuntimeRepository,
    state: object,
    *,
    expected_workflow_version: int,
    now: datetime,
    index: int,
) -> None:
    """通过公开 Turn CAS 接口提交后续领域状态。"""

    turn_id = f"turn-task8-next-{index}"
    action_key = f"task8:next:{index}"
    turn = TurnRecord(
        turn_id=turn_id,
        conversation_id=CONVERSATION_ID,
        client_input_id=UUID(f"00000000-0000-0000-0000-{800 + index:012d}"),
        status=TurnStatus.ACCEPTED,
        target_workflow_id=WORKFLOW_ID,
        decision=None,
        expected_context_version=0,
        created_at=now,
    )
    await repository.enqueue_turn_for_execution(USER_ID, turn, now=now)
    claim = await repository.claim_turn(
        USER_ID,
        CONVERSATION_ID,
        turn_id,
        lease_owner=f"task8-next-worker-{index}",
        now=now,
        lease_expires_at=now + timedelta(seconds=30),
    )
    assert claim is not None
    workflow = project_video_workflow_state(state)
    envelope = encode_video_workflow_state(
        user_id=USER_ID,
        state=state,
        workflow_version=expected_workflow_version + 1,
        last_turn_id=turn_id,
        last_action_key=action_key,
    )
    await repository.commit_turn(
        claim,
        VideoTurnCommit(
            decision=ActionDecision(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                target_workflow_id=WORKFLOW_ID,
                target_stage=workflow.current_stage,
                confidence=1,
                requires_confirmation=False,
                patch={},
                reason_code="task8_next_state",
                idempotency_key=action_key,
            ),
            turn_status=TurnStatus.COMPLETED,
            workflow_state=envelope,
            workflow=workflow,
            expected_workflow_version=expected_workflow_version,
            messages=(),
            occurred_at=now,
        ),
    )


async def _assert_new_completion_projection(
    repository: VideoRuntimeRepository,
    *,
    before_message_ids: set[str],
    expected_type: str,
    required_fields: set[str],
    expected_count: int = 1,
) -> SupervisorProjectionMessage:
    messages = await repository.list_projection_messages(USER_ID, CONVERSATION_ID)
    created = [item for item in messages if item.message_id not in before_message_ids]
    assert len(created) == expected_count
    events = await repository.list_events(USER_ID, CONVERSATION_ID)
    for message in created:
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        assert artifact["type"] == expected_type
        assert required_fields.issubset(artifact)
        assert "原始" not in json.dumps(artifact, ensure_ascii=False)
        upserted = [event for event in events if event.type.value == "message.upserted" and event.payload["message"]["message_id"] == message.message_id]
        assert len(upserted) == 1
    return created[0]


async def _assert_completion_projection_replay_stable(
    repository: VideoRuntimeRepository,
    runtime: object,
    *,
    before_message_ids: set[str],
    expected_type: str,
    required_fields: set[str],
    expected_count: int = 1,
) -> SupervisorProjectionMessage:
    """确认完成投影在 dispatcher 重放后仍只有同一条消息和事件。"""

    message = await _assert_new_completion_projection(
        repository,
        before_message_ids=before_message_ids,
        expected_type=expected_type,
        required_fields=required_fields,
        expected_count=expected_count,
    )
    await runtime.run_once()
    replayed = await _assert_new_completion_projection(
        repository,
        before_message_ids=before_message_ids,
        expected_type=expected_type,
        required_fields=required_fields,
        expected_count=expected_count,
    )
    assert replayed.message_id == message.message_id
    return message


async def _complete_scenes_and_merge(
    repository: VideoRuntimeRepository,
    store: object,
    operations: VideoLiveOperationBridge,
    clock: _MutableClock,
) -> tuple[object, object]:
    """通过真实 M06 completion 链路推进到合并视频人工复核。"""

    generation = await _start_generation_operations(operations)
    await _commit_seed_state(repository, store, generation)
    completion = VideoOperationCompletionHandler(
        repository=repository,
        operations=operations,
        clock=clock,
    )
    runtime = operations.build_recovery_runtime(
        resumer=completion,
        worker_id="task8-prepare-merged",
    )
    clock.advance(seconds=3)
    await runtime.run_once()
    generation_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert generation_envelope is not None
    completed_generation = decode_video_workflow_state(generation_envelope)
    port = operations.bind(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    )
    post_service = VideoPostProductionWorkflowService(port)
    post = await post_service.start_merge(
        completed_generation,
        operation_port=port,
        now=clock.now(),
    )
    pending = post.pending_operation
    assert pending is not None
    await operations.start(
        operations.start_request_from_claim(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job=pending,
            stage_version=post.stage_version,
            provider_request=post.merge_request,
        ),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )
    post = await post_service.resume(
        post,
        operation_port=port,
        now=clock.now(),
    )
    await _commit_next_state(
        repository,
        post,
        expected_workflow_version=generation_envelope.workflow_version,
        now=clock.now(),
        index=2,
    )
    clock.advance(seconds=3)
    await runtime.run_once()
    envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
    workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
    assert envelope is not None
    assert workflow is not None
    return envelope, workflow


def _plan_review_state():
    planning = VideoPlanningWorkflowService()
    state = planning.start(
        workflow_id=WORKFLOW_ID,
        conversation_id=CONVERSATION_ID,
        intent="video",
        intake_context={},
        now=NOW,
    )
    state = planning.confirm_intake(
        state,
        validate_form("video", VIDEO_FORM),
        now=NOW,
    )
    directions = draft_creative_directions("video", VIDEO_FORM)
    state = planning.publish_directions(state, directions, now=NOW)
    state = planning.select_direction(state, "direction_1", now=NOW)
    state = planning.publish_initial_plan(
        state,
        _concrete_plan(directions[0].to_dict()),
        now=NOW,
    )
    return state


def _reviewed_scene_package_state():
    planning = VideoPlanningWorkflowService()
    state = _plan_review_state()
    approved = planning.approve_plan(state, now=NOW)
    scene_service = VideoScenePackageWorkflowService()
    prepared = scene_service.prepare_from_approved_plan(
        approved,
        materials=[],
        now=NOW,
    )
    assets = prepared.scene_package.global_assets
    for item in assets["characters"]:
        item["three_view_images"] = [f"https://assets.example.com/{item['asset_id']}.png"]
    for collection in ("scenes", "props"):
        for item in assets[collection]:
            item["images"] = [f"https://assets.example.com/{item['asset_id']}.png"]
    return scene_service.publish_generated_asset_images(prepared, assets, now=NOW)


def _concrete_plan(direction: dict[str, object]):
    result = build_plan_markdown("video", VIDEO_FORM, direction)
    blueprints = copy.deepcopy(result.scene_blueprints)
    manifest = copy.deepcopy(result.asset_manifest)
    replacements = {
        "目标用户": "健康管理师林岚",
        "真实使用场景": "晨间公寓健康监测区",
    }
    for blueprint in blueprints:
        for collection in ("characters", "scenes", "props"):
            blueprint["asset_requirements"][collection] = [replacements.get(name, name) for name in blueprint["asset_requirements"][collection]]
        for old_name, new_name in replacements.items():
            for field_name in ("shot_description", "storyline", "narration"):
                blueprint[field_name] = blueprint[field_name].replace(old_name, new_name)
    for collection in ("characters", "scenes", "props"):
        for item in manifest[collection]:
            old_name = item["name"]
            new_name = replacements.get(old_name, old_name)
            item["name"] = new_name
            for field_name in ("description", "three_view_prompt", "image_prompt"):
                if field_name in item:
                    item[field_name] = item[field_name].replace(old_name, new_name)
    normalized_manifest = normalize_asset_manifest(manifest, blueprints)
    history = copy.deepcopy(result.plan_history)
    history[-1]["scene_blueprints"] = copy.deepcopy(blueprints)
    history[-1]["asset_manifest"] = copy.deepcopy(normalized_manifest)
    return replace(
        result,
        scene_blueprints=blueprints,
        asset_manifest=normalized_manifest,
        plan_history=history,
    )


def paid_stage_command(workflow) -> WorkflowCommand:
    return WorkflowCommand(
        conversation_id=workflow.conversation_id,
        workflow_id=workflow.workflow_id,
        kind=WorkflowKind.VIDEO,
        decision=ActionDecision(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            confidence=1,
            requires_confirmation=False,
            patch={},
            reason_code="explicit_target",
            idempotency_key="decision:paid-stage-without-credential",
        ),
        workflow=workflow,
        namespace=workflow_namespace(workflow.conversation_id, workflow.workflow_id),
        user_id=USER_ID,
        turn_id="turn-paid-stage-without-credential",
        current_input="确认分镜并开始生成视频",
        materials=[],
        reply_to_message_id=None,
        artifact_refs=[],
    )


def explicit_stage_command(
    workflow,
    *,
    action: AgentAction,
    patch: Mapping[str, JsonValue],
    suffix: str,
) -> WorkflowCommand:
    """构造携带唯一 Turn 与幂等键的显式阶段动作。"""

    return WorkflowCommand(
        conversation_id=workflow.conversation_id,
        workflow_id=workflow.workflow_id,
        kind=WorkflowKind.VIDEO,
        decision=ActionDecision(
            action=action,
            intent=AgentIntent.VIDEO,
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            confidence=1,
            requires_confirmation=False,
            patch=dict(patch),
            reason_code=f"explicit_{suffix}",
            idempotency_key=f"decision:{suffix}",
        ),
        workflow=workflow,
        namespace=workflow_namespace(
            workflow.conversation_id,
            workflow.workflow_id,
        ),
        user_id=USER_ID,
        turn_id=f"turn-{suffix}",
        current_input=f"执行 {suffix}",
        materials=[],
        reply_to_message_id=None,
        artifact_refs=[],
    )


def quality_stage_command(workflow) -> WorkflowCommand:
    """构造用户提出修改并启动 QAAgent QC 的显式命令。"""

    return WorkflowCommand(
        conversation_id=workflow.conversation_id,
        workflow_id=workflow.workflow_id,
        kind=WorkflowKind.VIDEO,
        decision=ActionDecision(
            action=AgentAction.MODIFY_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            confidence=1,
            requires_confirmation=False,
            patch={"user_feedback": "请检查商品露出与节奏"},
            reason_code="explicit_quality_review",
            idempotency_key="decision:start-quality-review",
        ),
        workflow=workflow,
        namespace=workflow_namespace(workflow.conversation_id, workflow.workflow_id),
        user_id=USER_ID,
        turn_id="turn-start-quality-review",
        current_input="请检查商品露出与节奏",
        materials=[],
        reply_to_message_id=None,
        artifact_refs=[],
    )


def jianying_stage_command(workflow) -> WorkflowCommand:
    """构造用户显式启动剪映草稿的命令。"""

    return WorkflowCommand(
        conversation_id=workflow.conversation_id,
        workflow_id=workflow.workflow_id,
        kind=WorkflowKind.VIDEO,
        decision=ActionDecision(
            action=AgentAction.MODIFY_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=workflow.workflow_id,
            target_stage=workflow.current_stage,
            confidence=1,
            requires_confirmation=False,
            patch={
                "jianying_action": "start",
                "project_name": "Task8 测试草稿",
            },
            reason_code="explicit_jianying_start",
            idempotency_key="decision:start-jianying",
        ),
        workflow=workflow,
        namespace=workflow_namespace(workflow.conversation_id, workflow.workflow_id),
        user_id=USER_ID,
        turn_id="turn-start-jianying",
        current_input="生成剪映草稿",
        materials=[],
        reply_to_message_id=None,
        artifact_refs=[],
    )


def handler_without_credential(
    *,
    repository: MemoryVideoRuntimeRepository,
    operations: VideoLiveOperationBridge,
    clock: object | None = None,
) -> VideoLiveWorkflowHandler:
    return VideoLiveWorkflowHandler(
        repository=repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=TransientCredentialVault(),
        operation_port=operations,
        clock=clock or _Clock(),
    )


def _web_exposes_scene_retry_action(artifact: Mapping[str, object]) -> bool:
    """复刻 MessageBubble 与 ChatPanel 当前共享的场景重试条件。"""

    generated = artifact.get("generatedSceneVideos")
    return bool(
        isinstance(generated, Mapping)
        and generated.get("ok") is False
        and artifact.get("videoScenePackages")
    )


@pytest.mark.parametrize(
    ("provider_request", "secret_marker"),
    [
        ({"nested": {"Authorization": "task8-sensitive-authorization"}}, "task8-sensitive-authorization"),
        ({"nested": [{"access_token": "task8-sensitive-snake-token"}]}, "task8-sensitive-snake-token"),
        ({"nested": {"refresh-token": "task8-sensitive-kebab-token"}}, "task8-sensitive-kebab-token"),
        ({"nested": {"clientToken": "task8-sensitive-camel-token"}}, "task8-sensitive-camel-token"),
        ({"nested": {"API KEY": "task8-sensitive-space-api-key"}}, "task8-sensitive-space-api-key"),
        ({"nested": {"api_key": "task8-sensitive-snake-api-key"}}, "task8-sensitive-snake-api-key"),
        ({"nested": {"api-key": "task8-sensitive-kebab-api-key"}}, "task8-sensitive-kebab-api-key"),
        ({"nested": {"apiKey": "task8-sensitive-camel-api-key"}}, "task8-sensitive-camel-api-key"),
        ({"nested": {"secret": "task8-sensitive-secret"}}, "task8-sensitive-secret"),
        ({"nested": {"passWord": "task8-sensitive-password"}}, "task8-sensitive-password"),
        ({"nested": [{"credential": "task8-sensitive-credential"}]}, "task8-sensitive-credential"),
        ({"nested": {"ＡＰＩ　ＫＥＹ": "task8-sensitive-nfkc"}}, "task8-sensitive-nfkc"),
        ({"note": "Bearer task8-explicit-bearer"}, "task8-explicit-bearer"),
        ({"note": "Authorization: Bearer task8-explicit-authorization"}, "task8-explicit-authorization"),
        ({"note": "token=task8-explicit-token"}, "task8-explicit-token"),
        (
            {"scene_id": "scene-1", "prompt": "Bearer task8-typed-bearer"},
            "task8-typed-bearer",
        ),
        (
            {"scene_id": "scene-1", "prompt": "token=task8-typed-token"},
            "task8-typed-token",
        ),
        (
            {
                "scene_id": "scene-1",
                "prompt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0YXNrOCJ9.c2lnbmF0dXJlLXRhc2s4",
            },
            "eyJhbGciOiJIUzI1NiJ9",
        ),
        ({"nested": {"auth_header": "task8-raw-auth-header"}}, "task8-raw-auth-header"),
        ({"nested": [{"auth": "task8-raw-auth"}]}, "task8-raw-auth"),
        ({"nested": {"Auth-KEY": "task8-raw-auth-key"}}, "task8-raw-auth-key"),
        ({"nested": {"bearerValue": "task8-raw-bearer"}}, "task8-raw-bearer"),
        ({"nested": [{"ＢＥＡＲＥＲ": "task8-nfkc-bearer"}]}, "task8-nfkc-bearer"),
        ({"nested": {"jwt": "task8-raw-jwt-direct"}}, "task8-raw-jwt-direct"),
        ({"nested": {"jwtToken": "task8-raw-jwt-token"}}, "task8-raw-jwt-token"),
        ({"nested": {"ＪＷＴ－ＣＲＥＤＥＮＴＩＡＬ": "task8-raw-jwt"}}, "task8-raw-jwt"),
        ({"nested": {"auth_headers": ["task8-plural-auth-header"]}}, "task8-plural-auth-header"),
        ({"nested": [{"jwtCredentials": "task8-plural-jwt"}]}, "task8-plural-jwt"),
        ({"nested": {"bearer-headers": "task8-plural-bearer"}}, "task8-plural-bearer"),
        ({"nested": {"api_keys": "task8-plural-api-key"}}, "task8-plural-api-key"),
        ({"nested": [{"clientTokens": "task8-plural-client-token"}]}, "task8-plural-client-token"),
        ({"nested": {"ＡＵＴＨ＿ＶＡＬＵＥＳ": "task8-plural-nfkc-value"}}, "task8-plural-nfkc-value"),
        (
            {"note": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0YXNrOCJ9.c2lnbmF0dXJlLXRhc2s4"},
            "eyJhbGciOiJIUzI1NiJ9",
        ),
        ({"note": "sk-task8rawapitoken123456789"}, "sk-task8rawapitoken123456789"),
        (
            {"nested": [{"note": "SK-TASK8RAWAPITOKEN123456789"}]},
            "SK-TASK8RAWAPITOKEN123456789",
        ),
    ],
)
def test_video_operation_start_request_rejects_nested_credentials_without_echo(
    provider_request: dict[str, JsonValue],
    secret_marker: str,
) -> None:
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=STAGE,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    with pytest.raises(ValueError) as raised:
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request=provider_request,
        ).model_dump(mode="json")

    assert secret_marker not in str(raised.value)


def test_video_operation_start_request_hides_provider_request_from_repr() -> None:
    live_request = request()

    assert PROVIDER_REQUEST["prompt"] not in repr(live_request)
    assert "provider_request" not in repr(live_request)


def test_video_operation_start_request_keeps_normal_business_fields_and_hash_check() -> None:
    provider_request: dict[str, JsonValue] = {
        "scene_id": "scene-1",
        "prompt": "展示保险箱的隐藏收纳空间",
        "shot_description": {
            "auth_mode": "signed_request",
            "token_budget": 8192,
            "token_count": 128,
            "token_count_hint": 128,
            "token_hint": "估算值",
            "provider_keys_count": 2,
            "provider_keys_limit": 4,
            "provider_keys_status": "已脱敏",
            "key_frame": "https://assets.example.com/keyframe.png",
            "keyImage": "https://assets.example.com/key-image.png",
            "key_points": ["主体清晰", "构图稳定"],
            "secretary_name": "林女士",
            "passwordless_mode": True,
        },
    }
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=STAGE,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    live_request = VideoOperationStartRequest(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        operation_request=operation_request,
        provider_request=provider_request,
    )

    assert live_request.provider_request == provider_request
    with pytest.raises(ValueError, match="request_hash"):
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request={**provider_request, "prompt": "已被篡改"},
        )


def test_video_operation_start_request_allows_product_codes_and_scene_key_urls() -> None:
    provider_request: dict[str, JsonValue] = {
        "scene_id": "scene-1",
        "prompt": "SK-ABCDEF12345678901234567890",
        "model": "pk-product-model-2026-edition",
        "image_urls": ["https://cdn.example.com/assets/pk-product-model-2026.png"],
    }
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=STAGE,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    live_request = VideoOperationStartRequest(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        operation_request=operation_request,
        provider_request=provider_request,
    )

    assert live_request.model_dump(mode="json")["provider_request"] == provider_request


@pytest.mark.parametrize(
    ("stage", "field", "assignment", "secret_marker"),
    [
        (
            "generate_scene_video:scene-1",
            "prompt",
            '{"Authorization":"sk-task8rawapitoken123456789"}',
            "sk-task8rawapitoken123456789",
        ),
        (
            "generate_scene_video:scene-1",
            "storyline",
            '{"api_key": "sk-task8rawapitoken123456789"}',
            "sk-task8rawapitoken123456789",
        ),
        (
            "quality_review",
            "user_feedback",
            "{'Authorization': 'sk-task8rawapitoken123456789'}",
            "sk-task8rawapitoken123456789",
        ),
        (
            "generate_scene_video:scene-1",
            "prompt",
            "`client_token` = `opaque-value`",
            "opaque-value",
        ),
        (
            "quality_review",
            "user_feedback",
            "｛＂Ａｕｔｈｏｒｉｚａｔｉｏｎ＂　：　＂opaque-nfkc-value＂｝",
            "opaque-nfkc-value",
        ),
    ],
)
def test_video_operation_start_request_rejects_quoted_credential_assignments_without_echo(
    stage: str,
    field: str,
    assignment: str,
    secret_marker: str,
) -> None:
    provider_request: dict[str, JsonValue] = {field: assignment}
    if stage.startswith("generate_scene_video:"):
        provider_request["scene_id"] = "scene-1"
    else:
        provider_request["merged_video_url"] = "https://videos.example.com/merged.mp4"
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    with pytest.raises(ValueError, match="Provider 请求包含敏感凭据") as raised:
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request=provider_request,
        ).model_dump(mode="json")

    assert secret_marker not in str(raised.value)
    assert assignment not in str(raised.value)


@pytest.mark.parametrize(
    ("stage", "field", "business_text"),
    [
        (
            "generate_scene_video:scene-1",
            "prompt",
            '{"product_sku":"SK-ABCDEF12345678901234567890"}',
        ),
        (
            "generate_scene_video:scene-1",
            "storyline",
            '{"model_id":"pk-product-model-2026-edition"}',
        ),
        (
            "quality_review",
            "user_feedback",
            "仅讨论 Authorization 与 api_key 字段名，不填写凭据值",
        ),
    ],
)
def test_video_operation_start_request_allows_quoted_business_json_and_unassigned_field_names(
    stage: str,
    field: str,
    business_text: str,
) -> None:
    provider_request: dict[str, JsonValue] = {field: business_text}
    if stage.startswith("generate_scene_video:"):
        provider_request["scene_id"] = "scene-1"
    else:
        provider_request["merged_video_url"] = "https://videos.example.com/merged.mp4"
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    live_request = VideoOperationStartRequest(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        operation_request=operation_request,
        provider_request=provider_request,
    )

    assert live_request.model_dump(mode="json")["provider_request"] == provider_request


@pytest.mark.parametrize(
    ("stage", "provider_request", "credential_key", "secret_marker"),
    [
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "prompt": r'{\"Authorization\":\"task8-probe-secret-value\"}',
            },
            "Authorization",
            "task8-probe-secret-value",
        ),
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "storyline": r"{\u0022api_key\u0022:\u0022task8-probe-secret-value\u0022}",
            },
            "api_key",
            "task8-probe-secret-value",
        ),
        (
            "quality_review",
            {
                "merged_video_url": "https://videos.example.com/merged.mp4",
                "user_feedback": 'headers["Authorization"] = "task8-probe-secret-value"',
            },
            "Authorization",
            "task8-probe-secret-value",
        ),
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "shot_description": {
                    "text": "headers [ 'api_key' ] = 'task8-probe-secret-value'"
                },
            },
            "api_key",
            "task8-probe-secret-value",
        ),
        (
            "merge_video",
            {
                "scene_videos": [
                    {
                        "scene_id": "scene-1",
                        "note": 'Authorization": "task8-probe-secret-value"',
                    }
                ]
            },
            "Authorization",
            "task8-probe-secret-value",
        ),
        (
            "jianying_draft",
            {
                "request": {
                    "payload": '"Authorization\': "task8-probe-secret-value"'
                }
            },
            "Authorization",
            "task8-probe-secret-value",
        ),
        (
            "quality_review",
            {
                "merged_video_url": "https://videos.example.com/merged.mp4",
                "scene_packages": [
                    {
                        "notes": "ｈｅａｄｅｒｓ［＂ＡＵＴＨＯＲＩＺＡＴＩＯＮ＂］＝＂task8-probe-secret-value＂"
                    }
                ],
            },
            "ＡＵＴＨＯＲＩＺＡＴＩＯＮ",
            "task8-probe-secret-value",
        ),
        (
            "quality_review",
            {
                "merged_video_url": "https://videos.example.com/merged.mp4",
                "user_feedback": r"headers[\u0027api_key\u0027]=\u0027task8-probe-secret-value\u0027",
            },
            "api_key",
            "task8-probe-secret-value",
        ),
        (
            "merge_video",
            {
                "scene_videos": {
                    "scene-1": {
                        "note": r"headers[\u0060client_token\u0060]=\u0060task8-probe-secret-value\u0060"
                    }
                }
            },
            "client_token",
            "task8-probe-secret-value",
        ),
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "prompt": 'HeAdErS [ "aUtHoRiZaTiOn" ] = "task8-probe-secret-value"',
            },
            "aUtHoRiZaTiOn",
            "task8-probe-secret-value",
        ),
        (
            "jianying_draft",
            {
                "request": {
                    "payload": r"{\'API_KEY\':\'task8-probe-secret-value\'}"
                }
            },
            "API_KEY",
            "task8-probe-secret-value",
        ),
    ],
)
def test_video_operation_start_request_rejects_extra_i1b_assignment_syntax_without_echo(
    stage: str,
    provider_request: dict[str, JsonValue],
    credential_key: str,
    secret_marker: str,
) -> None:
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    with pytest.raises(ValueError) as raised:
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request=provider_request,
        ).model_dump(mode="json")

    error_text = str(raised.value)
    assert "Provider 请求包含敏感凭据" in error_text
    assert error_text.count("Provider 请求包含敏感凭据") == 1
    assert credential_key not in error_text
    assert secret_marker not in error_text


@pytest.mark.parametrize(
    ("stage", "provider_request"),
    [
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "prompt": r'{\"product_sku\":\"SK-ABCDEF12345678901234567890\"}',
            },
        ),
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "storyline": r"{\u0022model_id\u0022:\u0022pk-product-model-2026-edition\u0022}",
            },
        ),
        (
            "quality_review",
            {
                "merged_video_url": "https://videos.example.com/merged.mp4",
                "user_feedback": "仅讨论 Authorization、api_key、client_token 字段名，不填写赋值",
            },
        ),
        (
            "merge_video",
            {
                "scene_videos": [
                    {
                        "scene_id": "scene-1",
                        "note": "Authorization/api_key/client_token 是接口说明字段",
                    }
                ]
            },
        ),
        (
            "jianying_draft",
            {
                "request": {
                    "payload": "headers [ 'product_api_keychain' ] = 'catalog-value'"
                }
            },
        ),
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "prompt": "SK-ABCDEF12345678901234567890",
            },
        ),
        (
            "quality_review",
            {
                "merged_video_url": "https://videos.example.com/merged.mp4",
                "user_feedback": 'headers["product_sku"] = "SK-ABCDEF12345678901234567890"',
            },
        ),
    ],
)
def test_video_operation_start_request_allows_extra_i1b_business_text_unchanged(
    stage: str,
    provider_request: dict[str, JsonValue],
) -> None:
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    live_request = VideoOperationStartRequest(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        operation_request=operation_request,
        provider_request=provider_request,
    )

    assert live_request.model_dump(mode="json")["provider_request"] == provider_request


@pytest.mark.parametrize(
    "credential_key",
    [
        "provider_keys",
        "providerKeys",
        "providerkeys",
        "ｐｒｏｖｉｄｅｒ＿ｋｅｙｓ",
        "key",
        "keys",
        "clientKeys",
        "key_value",
        "keyHeader",
        "keymaterial",
        "privateKeys",
        "providerCredentials",
    ],
)
def test_video_operation_start_request_reuses_task6_sensitive_key_contract(
    credential_key: str,
) -> None:
    provider_request: dict[str, JsonValue] = {
        "scene_id": "scene-1",
        "prompt": "固定测试视频提示词",
        "shot_description": {
            "text": "普通镜头描述",
            "nested": {credential_key: "opaque-provider-value"},
        },
    }
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=STAGE,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    with pytest.raises(ValueError) as raised:
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request=provider_request,
        ).model_dump(mode="json")

    assert credential_key not in str(raised.value)
    assert "opaque-provider-value" not in str(raised.value)


@pytest.mark.parametrize(
    ("stage", "provider_request"),
    [
        (
            "generate_scene_video:scene-1",
            {
                "scene_id": "scene-1",
                "scene_index": 1,
                "duration": 10,
                "duration_ms": 10_000,
                "prompt": "SK-ABCDEF12345678901234567890",
                "storyline": "商品亮相",
                "shot_description": {"text": "0-10秒展示商品"},
                "narration": "新品登场",
                "transition": "淡出",
                "generation_mode": "image_to_video",
                "image_urls": ["https://cdn.example.com/pk-product-model-2026.png"],
                "video_urls": [],
                "audio_urls": [],
                "model": "pk-product-model-2026-edition",
                "ratio": "9:16",
                "size": "1080p",
                "sound": "on",
            },
        ),
        (
            "merge_video",
            {
                "video_urls": ["https://videos.example.com/scene-1.mp4"],
                "scene_videos": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "video_url": "https://videos.example.com/scene-1.mp4",
                    }
                ],
                "duration": 10,
                "size": "1080p",
                "model": "pk-product-model-2026-edition",
            },
        ),
        (
            "quality_review",
            {
                "merged_video_url": "https://videos.example.com/merged.mp4",
                "scene_videos": [],
                "scene_packages": [],
                "brief": {"expected_duration_sec": 10},
                "materials": [],
                "user_feedback": "重点检查商品型号 SK-ABCDEF12345678901234567890",
                "ratio": "9:16",
                "size": "1080p",
            },
        ),
        (
            "jianying_draft",
            {
                "request": {
                    "conversation_id": CONVERSATION_ID,
                    "storyboard_version_id": "storyboard-v1",
                    "scenes": [],
                },
                "retry_failed": False,
            },
        ),
    ],
)
def test_video_operation_start_request_accepts_real_m11_stage_fields(
    stage: str,
    provider_request: dict[str, JsonValue],
) -> None:
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    live_request = VideoOperationStartRequest(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        operation_request=operation_request,
        provider_request=provider_request,
    )

    assert live_request.model_dump(mode="json")["provider_request"] == provider_request


@pytest.mark.parametrize(
    ("stage", "provider_request", "secret_marker"),
    [
        (
            "generate_scene_videos",
            {"scene_id": "scene-1", "prompt": "固定测试视频提示词"},
            None,
        ),
        (
            "generate_scene_video:scene-1",
            {"scene_id": "scene-1", "prompt": "固定测试视频提示词", "note": "普通备注"},
            None,
        ),
        (
            "generate_scene_video:scene-1",
            {"scene_id": "scene-1", "prompt": "固定测试视频提示词", "auth_headers": "sk-task8rawapitoken123456789"},
            "sk-task8rawapitoken123456789",
        ),
        (
            "generate_scene_video:scene-1",
            {"scene_id": "scene-1", "prompt": "固定测试视频提示词", "provider_keys": "sk-task8rawapitoken123456789"},
            "sk-task8rawapitoken123456789",
        ),
        (
            "generate_scene_video:scene-1",
            {"scene_id": "scene-1", "prompt": "固定测试视频提示词", "note": "sk-task8rawapitoken123456789"},
            "sk-task8rawapitoken123456789",
        ),
    ],
)
def test_video_operation_start_request_rejects_unknown_stage_or_top_level_field_before_dump(
    stage: str,
    provider_request: dict[str, JsonValue],
    secret_marker: str | None,
) -> None:
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )

    with pytest.raises(ValueError) as raised:
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request=provider_request,
        ).model_dump(mode="json")

    if secret_marker is not None:
        assert secret_marker not in str(raised.value)


@pytest.mark.asyncio
async def test_concurrent_live_start_calls_provider_once_and_never_persists_auth() -> None:
    provider = CountingProvider()
    runtime = build_live_operations(provider)
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)

    first, second = await asyncio.gather(
        runtime.start(request(), credential=credential),
        runtime.start(request(), credential=credential),
    )

    assert first.job_id == second.job_id == "operation-live-video-1"
    assert provider.start_calls == 1
    snapshot = await runtime.safe_persistence_snapshot(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    )
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert FAKE_AUTHORIZATION not in serialized
    assert "task8-test-only" not in serialized
    assert PROVIDER_REQUEST["prompt"] not in serialized


def test_transient_credential_vault_lifecycle_and_empty_credential_rejection() -> None:
    with pytest.raises(ValueError, match="临时 Authorization"):
        TransientTurnCredential("   ")

    vault = TransientCredentialVault()
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault.put("turn-live-1", credential)

    assert vault.get("turn-live-1") is credential
    assert FAKE_AUTHORIZATION not in repr(vault)
    vault.pop("turn-live-1")
    assert vault.get("turn-live-1") is None
    vault.put("turn-live-2", TransientTurnCredential(FAKE_AUTHORIZATION))
    vault.clear()
    assert vault.get("turn-live-2") is None


@pytest.mark.asyncio
async def test_quota_credential_resumes_original_operation_and_is_consumed(
    tmp_path: Path,
) -> None:
    """授权恢复只续跑原 Provider job，并把当前凭据消费一次。"""

    from pixelflow.agent_workflows.video.live_capabilities import (
        _consume_authorization_for_quota_resume_boundary,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        "memory",
        tmp_path / "task5-quota-credential.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations, _, pending, paused, _ = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        credential = TransientTurnCredential(FAKE_AUTHORIZATION)

        authorized = await operations.resume_paused_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            workflow_id=WORKFLOW_ID,
            job_id=pending.job_id,
            expected_revision=1,
            resume_request_key="decision:task5-quota-credential",
            credential=credential,
        )

        assert authorized.operation.job_id == pending.job_id
        assert authorized.operation.provider_job_id == paused.operation.provider_job_id
        assert authorized.operation.attempt == paused.operation.attempt == 1
        assert provider.start_calls == 3
        with pytest.raises(RuntimeError, match="不可用"):
            _consume_authorization_for_quota_resume_boundary(credential)


@pytest.mark.asyncio
async def test_quota_retry_without_credential_reopens_same_revision_interrupt() -> None:
    """缺凭据时保持原 Operation 暂停，并重开同 revision 授权中断。"""

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(harness, suffix="task5-quota-missing")
    handler = handler_without_credential(
        repository=harness.repository,
        operations=harness.operations,
        clock=harness.clock,
    )

    result = await handler.dispatch(command)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.next_poll_at is None
    assert operation.quota_pause_revision == 1
    assert result.workflow.status is WorkflowStatus.RUNNING
    assert result.interrupt is not None
    assert result.interrupt.payload["authorization_action"]["patch"] == {
        "job_id": harness.pending.job_id,
        "quota_pause_revision": 1,
    }
    events = await harness.repository.list_events(USER_ID, CONVERSATION_ID)
    assert not any(
        item.type.value == "external_job.quota_state_changed"
        and item.payload["quota_state"] == "resumed"
        for item in events
    )


@pytest.mark.asyncio
async def test_quota_retry_consumes_new_credential_and_resumes_same_operation() -> None:
    """有效授权只恢复原 job，不 start、不换 attempt。"""

    from pixelflow.agent_workflows.video.live_capabilities import (
        _consume_authorization_for_quota_resume_boundary,
    )

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(harness, suffix="task5-quota-valid")
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    result = await handler.dispatch(command)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.job_id == harness.pending.job_id
    assert operation.provider_job_id == harness.paused.operation.provider_job_id
    assert operation.attempt == 1
    assert harness.provider.start_calls == 3
    assert result.workflow.status is WorkflowStatus.RUNNING
    assert result.operation_event_claim is not None
    assert result.state.last_action_key == result.operation_event_claim.event.event_id
    with pytest.raises(RuntimeError, match="不可用"):
        _consume_authorization_for_quota_resume_boundary(credential)


@pytest.mark.asyncio
async def test_quota_retry_discards_credential_when_bridge_fails_before_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge 在消费前故障时，Handler 仍必须销毁临时授权。"""

    from pixelflow.agent_workflows.video.live_capabilities import (
        _consume_authorization_for_quota_resume_boundary,
    )

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(
        harness,
        suffix="task7-quota-bridge-pre-consume-failure",
    )
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)

    async def fail_before_consumption(**_kwargs: object) -> object:
        raise RuntimeError("模拟 Bridge 在凭据消费前故障")

    monkeypatch.setattr(
        harness.operations,
        "resume_paused_operation",
        fail_before_consumption,
    )
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(RuntimeError, match="消费前故障"):
        await handler.dispatch(command)

    with pytest.raises(RuntimeError, match="不可用"):
        _consume_authorization_for_quota_resume_boundary(credential)


@pytest.mark.asyncio
async def test_missing_quota_credential_commits_then_new_credential_resumes() -> None:
    """缺凭据重开可真实提交，下一响应仍能恢复同一 revision。"""

    harness = await _paused_quota_handler_harness()
    first_interrupt = await harness.repository.get_open_interrupt(
        USER_ID,
        CONVERSATION_ID,
    )
    assert first_interrupt is not None
    first_response = await harness.repository.store_interrupt_response(
        USER_ID,
        CONVERSATION_ID,
        first_interrupt.interrupt_id,
        client_response_id=UUID("00000000-0000-4000-8000-000000001501"),
        response_value={"content": "尚未取得新凭据"},
        responded_at=harness.clock.now(),
    )
    first_claim = await harness.repository.claim_interrupt_resume(
        USER_ID,
        CONVERSATION_ID,
        first_response.interrupt_id,
        lease_owner="task5-missing-credential-turn",
        now=harness.clock.now(),
        lease_expires_at=harness.clock.now() + timedelta(seconds=60),
    )
    assert first_claim is not None
    competing_claim = await harness.repository.claim_interrupt_resume(
        USER_ID,
        CONVERSATION_ID,
        first_response.interrupt_id,
        lease_owner="task7-concurrent-valid-credential",
        now=harness.clock.now(),
        lease_expires_at=harness.clock.now() + timedelta(seconds=60),
    )
    assert competing_claim is None
    first_command = replace(
        _quota_retry_command(harness, suffix="task5-quota-missing-commit"),
        turn_id=first_claim.turn.turn_id,
    )
    handler = handler_without_credential(
        repository=harness.repository,
        operations=harness.operations,
        clock=harness.clock,
    )
    waiting = await handler.dispatch(first_command)
    assert await harness.repository.claim_interrupt_resume(
        USER_ID,
        CONVERSATION_ID,
        first_response.interrupt_id,
        lease_owner="task7-concurrent-valid-after-handler",
        now=harness.clock.now(),
        lease_expires_at=harness.clock.now() + timedelta(seconds=60),
    ) is None
    executor = object.__new__(SupervisorTurnExecutor)
    executor._clock = harness.clock.now
    waiting_commit = executor._commit_from_graph(
        first_claim,
        first_command.decision,
        {
            "decision": first_command.decision.model_dump(mode="json"),
            "workflow_dispatch_result": waiting.model_dump(mode="json"),
        },
        close_interrupt_id=first_interrupt.interrupt_id,
    )

    await harness.repository.commit_turn(first_claim, waiting_commit)

    reopened = await harness.repository.get_open_interrupt(
        USER_ID,
        CONVERSATION_ID,
    )
    assert reopened is not None
    assert reopened.interrupt_id != first_interrupt.interrupt_id
    assert reopened.payload["authorization_action"]["patch"] == {
        "job_id": harness.pending.job_id,
        "quota_pause_revision": 1,
    }
    running_workflow = await harness.repository.get_workflow(USER_ID, WORKFLOW_ID)
    assert running_workflow is not None
    assert running_workflow.status is WorkflowStatus.RUNNING
    second_response = await harness.repository.store_interrupt_response(
        USER_ID,
        CONVERSATION_ID,
        reopened.interrupt_id,
        client_response_id=UUID("00000000-0000-4000-8000-000000001502"),
        response_value={"content": "已取得新凭据"},
        responded_at=harness.clock.now(),
    )
    second_claim = await harness.repository.claim_interrupt_resume(
        USER_ID,
        CONVERSATION_ID,
        second_response.interrupt_id,
        lease_owner="task5-valid-credential-turn",
        now=harness.clock.now(),
        lease_expires_at=harness.clock.now() + timedelta(seconds=60),
    )
    assert second_claim is not None
    second_command = replace(
        explicit_stage_command(
            running_workflow,
            action=AgentAction.RETRY_FAILED,
            patch={
                "job_id": harness.pending.job_id,
                "quota_pause_revision": 1,
            },
            suffix="task5-quota-valid-after-missing",
        ),
        turn_id=second_claim.turn.turn_id,
    )
    second_command = replace(
        second_command,
        source_interrupt_id=reopened.interrupt_id,
        decision=second_command.decision.model_copy(
            update={"target_stage": harness.pending.stage}
        ),
    )
    vault = TransientCredentialVault()
    vault.put(second_command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
    resumed_handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    resumed = await resumed_handler.dispatch(second_command)
    resumed_commit = executor._commit_from_graph(
        second_claim,
        second_command.decision,
        {
            "decision": second_command.decision.model_dump(mode="json"),
            "workflow_dispatch_result": resumed.model_dump(mode="json"),
        },
        close_interrupt_id=reopened.interrupt_id,
    )
    await harness.repository.commit_turn(second_claim, resumed_commit)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.provider_job_id == harness.paused.operation.provider_job_id
    assert operation.attempt == 1
    assert operation.next_poll_at == harness.clock.now()
    assert harness.provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_id", "revision"),
    [
        (None, 2),
        ("operation-not-in-current-workflow", 1),
    ],
)
async def test_quota_revision_or_job_mismatch_fails_closed(
    job_id: str | None,
    revision: int,
) -> None:
    """旧 revision 与错误 job 均返回固定冲突，不改变暂停状态。"""

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(
        harness,
        suffix=f"task5-quota-stale-{revision}-{job_id is not None}",
        job_id=job_id,
        revision=revision,
    )
    from pixelflow.agent_workflows.video.live_capabilities import (
        _consume_authorization_for_quota_resume_boundary,
    )

    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_quota_resume_stale",
    ):
        await handler.dispatch(command)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.next_poll_at is None
    assert operation.quota_pause_revision == 1
    assert (
        _consume_authorization_for_quota_resume_boundary(credential)
        == FAKE_AUTHORIZATION
    )
    with pytest.raises(RuntimeError, match="不可用"):
        _consume_authorization_for_quota_resume_boundary(credential)


@pytest.mark.asyncio
async def test_quota_retry_rejects_forged_paused_workflow_projection() -> None:
    """只有 Repository 中完整的 paused WorkflowRecord 可以进入 quota 窄路由。"""

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(harness, suffix="task5-quota-forged-workflow")
    forged = harness.workflow.model_copy(
        update={"updated_at": harness.workflow.updated_at + timedelta(seconds=1)}
    )
    command = replace(command, workflow=forged)
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_workflow_projection_stale",
    ):
        await handler.dispatch(command)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.next_poll_at is None


@pytest.mark.asyncio
async def test_quota_retry_accepts_strict_pause_token_after_other_scene_reopens_workflow() -> None:
    """其他分镜把权威状态推进为 RUNNING 后，原暂停令牌仍可恢复同一 job。"""

    harness = await _reopened_quota_handler_harness()
    command = _quota_retry_command(
        harness,
        suffix="task7-reopened-quota-token",
    )
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )
    start_calls_before = harness.provider.start_calls
    assert command.workflow is not None
    assert (
        command.workflow.context_version
        < harness.running_workflow.context_version
    )

    result = await handler.dispatch(command)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.job_id == harness.pending.job_id
    assert operation.provider_job_id == harness.paused.operation.provider_job_id
    assert operation.next_poll_at == harness.clock.now()
    assert result.workflow.status is WorkflowStatus.RUNNING
    assert result.operation_event_claim is not None
    assert harness.provider.start_calls == start_calls_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "pause_event_id",
        "thread_version",
        "interrupt_id",
        "interrupt_user_id",
        "source_interrupt_id",
        "checkpoint_pending_job",
        "checkpoint_context_version",
    ],
)
async def test_reopened_quota_retry_rejects_tampered_pause_token_without_side_effects(
    tamper: str,
) -> None:
    """暂停令牌任一身份被改写时，Operation 与临时凭据都必须保持不变。"""

    from pixelflow.agent_workflows.video.live_capabilities import (
        _consume_authorization_for_quota_resume_boundary,
    )

    harness = await _reopened_quota_handler_harness()
    command = _quota_retry_command(
        harness,
        suffix=f"task7-reopened-quota-tamper-{tamper}",
    )
    interrupt = await harness.repository.get_open_interrupt(
        USER_ID,
        CONVERSATION_ID,
    )
    assert interrupt is not None
    if tamper == "pause_event_id":
        forged = interrupt.model_copy(
            update={
                "thread_id": (
                    "quota-paused:evt_job_quota_forged_pause_event:v2"
                )
            }
        )
        harness.repository._interrupts[(USER_ID, interrupt.interrupt_id)] = forged
    elif tamper == "thread_version":
        prefix, _, _ = interrupt.thread_id.rpartition(":v")
        forged = interrupt.model_copy(update={"thread_id": f"{prefix}:v999"})
        harness.repository._interrupts[(USER_ID, interrupt.interrupt_id)] = forged
    elif tamper == "interrupt_id":
        harness.repository._interrupts.pop((USER_ID, interrupt.interrupt_id))
        forged = interrupt.model_copy(
            update={"interrupt_id": "interrupt-task7-forged-pause-token"}
        )
        harness.repository._interrupts[(USER_ID, forged.interrupt_id)] = forged
    elif tamper == "interrupt_user_id":
        forged = interrupt.model_copy(update={"user_id": "user-task7-forged"})
        harness.repository._interrupts[(USER_ID, interrupt.interrupt_id)] = forged
    elif tamper == "source_interrupt_id":
        command = replace(
            command,
            source_interrupt_id="interrupt-task7-forged-source",
        )
    elif tamper == "checkpoint_pending_job":
        pending = command.workflow.pending_external_job
        assert pending is not None
        command = replace(
            command,
            workflow=command.workflow.model_copy(
                update={
                    "pending_external_job": pending.model_copy(
                        update={"job_id": "job-task7-forged-checkpoint"}
                    )
                }
            ),
        )
    elif tamper == "checkpoint_context_version":
        command = replace(
            command,
            workflow=command.workflow.model_copy(
                update={
                    "context_version": (
                        harness.running_workflow.context_version + 1
                    )
                }
            ),
        )
    else:  # pragma: no cover - 参数集合由测试自身固定。
        raise AssertionError(f"未知篡改类型：{tamper}")

    before = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert before is not None
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_quota_resume_stale",
    ):
        await handler.dispatch(command)

    after = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert after == before
    assert (
        _consume_authorization_for_quota_resume_boundary(credential)
        == FAKE_AUTHORIZATION
    )


@pytest.mark.asyncio
async def test_quota_retry_rejects_running_projection_with_original_pause_thread() -> None:
    """RUNNING 投影不能借用原 quota pause checkpoint 冒充缺凭据重开。"""

    harness = await _paused_quota_handler_harness()
    original_interrupt = await harness.repository.get_open_interrupt(
        USER_ID,
        CONVERSATION_ID,
    )
    assert original_interrupt is not None
    assert original_interrupt.thread_id.startswith("quota-paused:")
    envelope = await harness.repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert envelope is not None
    from pixelflow.agent_workflows.video.state_codec import (
        decode_video_workflow_state,
    )

    running_workflow = project_video_workflow_state(
        decode_video_workflow_state(envelope)
    )
    harness.repository._workflows[(USER_ID, WORKFLOW_ID)] = running_workflow
    command = replace(
        _quota_retry_command(harness, suffix="task5-forged-running-projection"),
        workflow=running_workflow,
    )
    vault = TransientCredentialVault()
    vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_quota_resume_stale",
    ):
        await handler.dispatch(command)


@pytest.mark.asyncio
async def test_quota_retry_rejects_double_forged_running_namespace() -> None:
    """RUNNING 命令与中断同时伪造旧 quota thread 也必须拒绝。"""

    harness = await _paused_quota_handler_harness()
    original_interrupt = await harness.repository.get_open_interrupt(
        USER_ID,
        CONVERSATION_ID,
    )
    assert original_interrupt is not None
    old_pause_thread = original_interrupt.thread_id
    assert old_pause_thread.startswith("quota-paused:")
    envelope = await harness.repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert envelope is not None
    running_workflow = project_video_workflow_state(
        decode_video_workflow_state(envelope)
    )
    harness.repository._workflows[(USER_ID, WORKFLOW_ID)] = running_workflow
    command = replace(
        _quota_retry_command(harness, suffix="task5-double-forged-namespace"),
        workflow=running_workflow,
    )
    command = replace(
        command,
        namespace=replace(command.namespace, thread_id=old_pause_thread),
    )
    vault = TransientCredentialVault()
    vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_quota_resume_stale",
    ):
        await handler.dispatch(command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "conversation_id", "workflow_id"),
    [
        ("other-user", CONVERSATION_ID, WORKFLOW_ID),
        (USER_ID, "other-conversation", WORKFLOW_ID),
        (USER_ID, CONVERSATION_ID, "other-workflow"),
    ],
)
async def test_quota_resume_bridge_rejects_cross_scope_identity(
    user_id: str,
    conversation_id: str,
    workflow_id: str,
) -> None:
    """跨用户、跨会话与错误 Workflow 均不能恢复原 Operation。"""

    harness = await _paused_quota_handler_harness()
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)

    with pytest.raises(OperationConflictError):
        await harness.operations.resume_paused_operation(
            user_id=user_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
            job_id=harness.pending.job_id,
            expected_revision=1,
            resume_request_key="decision:task5-cross-scope",
            credential=credential,
        )

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.next_poll_at is None
    assert operation.quota_pause_revision == 1


@pytest.mark.asyncio
async def test_quota_retry_rejects_pending_attempt_mismatch() -> None:
    """Operation attempt 与 M11 pending 不一致时必须在授权前失败关闭。"""

    harness = await _paused_quota_handler_harness()
    current = await harness.repository.get_operation(
        USER_ID,
        harness.pending.job_id,
    )
    assert current is not None
    harness.repository._operations[(USER_ID, current.job_id)] = current.model_copy(
        update={"attempt": current.attempt + 1}
    )
    command = _quota_retry_command(harness, suffix="task5-quota-attempt-stale")
    credential = TransientTurnCredential(FAKE_AUTHORIZATION)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, credential)
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(
        VideoLiveStateConflictError,
        match="video_quota_resume_stale",
    ):
        await handler.dispatch(command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "reason_code"),
    [
        ("user", "video_workflow_state_required"),
        ("conversation", "video_conversation_mismatch"),
        ("workflow", "video_workflow_state_required"),
    ],
)
async def test_quota_retry_handler_rejects_cross_scope_command(
    scope: str,
    reason_code: str,
) -> None:
    """Handler 在任何 Operation 变更前拒绝跨作用域恢复命令。"""

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(harness, suffix=f"task5-quota-{scope}")
    if scope == "user":
        command = replace(command, user_id="other-user")
    elif scope == "conversation":
        command = replace(
            command,
            conversation_id="other-conversation",
            namespace=workflow_namespace("other-conversation", WORKFLOW_ID),
        )
    else:
        command = replace(
            command,
            workflow_id="other-workflow",
            namespace=workflow_namespace(CONVERSATION_ID, "other-workflow"),
            decision=command.decision.model_copy(
                update={"target_workflow_id": "other-workflow"}
            ),
        )
    handler = handler_without_credential(
        repository=harness.repository,
        operations=harness.operations,
        clock=harness.clock,
    )

    with pytest.raises(VideoLiveStateConflictError, match=reason_code):
        await handler.dispatch(command)

    operation = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert operation is not None
    assert operation.next_poll_at is None


@pytest.mark.asyncio
async def test_concurrent_quota_retry_allows_one_client_and_rejects_the_other(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """两个不同响应并发恢复同 revision 时只允许一个 claim。"""

    harness = await _paused_quota_handler_harness()
    first = _quota_retry_command(harness, suffix="task5-quota-client-a")
    second = _quota_retry_command(harness, suffix="task5-quota-client-b")
    first_marker = "Bearer task5-quota-client-a-marker"
    second_marker = "Bearer task5-quota-client-b-marker"
    vault = TransientCredentialVault()
    vault.put(first.turn_id, TransientTurnCredential(first_marker))
    vault.put(second.turn_id, TransientTurnCredential(second_marker))
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )

    outcomes = await asyncio.gather(
        handler.dispatch(first),
        handler.dispatch(second),
        return_exceptions=True,
    )

    successes = [item for item in outcomes if isinstance(item, WorkflowDispatchResult)]
    conflicts = [item for item in outcomes if isinstance(item, VideoLiveStateConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].reason_code == "video_quota_resume_stale"
    events = await harness.repository.list_events(USER_ID, CONVERSATION_ID)
    resumed = [
        item
        for item in events
        if item.type.value == "external_job.quota_state_changed"
        and item.payload["quota_state"] == "resumed"
    ]
    assert len(resumed) == 1
    assert harness.provider.start_calls == 3
    safe_snapshot = await harness.operations.safe_persistence_snapshot(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    )
    serialized = json.dumps(safe_snapshot, ensure_ascii=False, sort_keys=True)
    assert first_marker not in serialized + caplog.text
    assert second_marker not in serialized + caplog.text


def test_video_operation_adapter_resolver_rejects_unknown_stage() -> None:
    provider = CountingProvider()
    adapter = ProviderJobAdapter(provider)
    resolver = VideoOperationAdapterResolver({"generate_scene_video": adapter})

    assert resolver.resolve(STAGE) is adapter
    with pytest.raises(OperationConflictError, match="stage"):
        resolver.resolve("unknown-paid-stage")


def test_video_operation_adapter_resolver_routes_dynamic_scene_stages_to_base_capability() -> None:
    adapter = ProviderJobAdapter(CountingProvider())
    resolver = VideoOperationAdapterResolver({"generate_scene_video": adapter})

    assert resolver.resolve("generate_scene_video") is adapter
    for scene_id in ("scene-1", "scene-4", "hero-shot-04", "custom_17"):
        assert resolver.resolve(f"generate_scene_video:{scene_id}") is adapter

    for invalid_stage in (
        "generate_scene_video:",
        "generate_scene_video:   ",
        "generate_scene_videos:scene-4",
        "prefix_generate_scene_video:scene-4",
    ):
        with pytest.raises(OperationConflictError, match="stage"):
            resolver.resolve(invalid_stage)

    unavailable = VideoOperationAdapterResolver({"merge_video": adapter})
    with pytest.raises(OperationConflictError, match="stage"):
        unavailable.resolve("generate_scene_video")
    with pytest.raises(OperationConflictError, match="stage"):
        unavailable.resolve("generate_scene_video:scene-4")


@pytest.mark.asyncio
async def test_dynamic_scene_stage_preserves_operation_identity_during_recovery() -> None:
    dynamic_stage = "generate_scene_video:hero-shot-04"
    provider_request: dict[str, JsonValue] = {
        "scene_id": "hero-shot-04",
        "prompt": "生成非默认分镜",
    }
    operation_request = build_operation_request(
        workflow_id=WORKFLOW_ID,
        stage=dynamic_stage,
        stage_version=3,
        attempt=1,
        provider_request=provider_request,
    )
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": "provider-scripted-1",
                "status": "completed",
                "result": {"video_url": "https://provider.example/hero-shot-04.mp4"},
            }
        ]
    )
    operations = VideoLiveOperationBridge(
        repository=MemoryAgentRuntimeRepository(),
        resolver=VideoOperationAdapterResolver({"generate_scene_video": ProviderJobAdapter(provider)}),
        lease_owner="task8-dynamic-scene-start",
        clock=clock,
        job_id_factory=lambda: "operation-dynamic-scene",
    )

    started = await operations.start(
        VideoOperationStartRequest(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            operation_request=operation_request,
            provider_request=provider_request,
        ),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )
    resumer = _RecordingResumer()
    runtime = operations.build_recovery_runtime(
        resumer=resumer,
        worker_id="task8-dynamic-scene-recovery",
    )
    clock.advance(seconds=3)
    await runtime.run_once()

    completed = await operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=started.job_id,
    )
    assert completed is not None
    assert completed.stage == dynamic_stage
    assert completed.idempotency_key == operation_request.idempotency_key
    assert provider.status_job_ids == ["provider-scripted-1"]
    assert len(resumer.calls) == 1
    completion_event = resumer.calls[0][1]
    assert completion_event.payload["stage"] == dynamic_stage
    assert resumer.calls[0][2] == completion_event.event_id


@pytest.mark.asyncio
async def test_recovery_without_credential_opens_authorization_interrupt_before_start() -> None:
    provider = CountingProvider()
    operations = build_live_operations(provider)
    repository = _SeededVideoRepository(task_store=MemoryPixelFlowTaskStore())
    workflow = await repository.seed_state(_reviewed_scene_package_state())

    result = await handler_without_credential(
        repository=repository,
        operations=operations,
    ).dispatch(paid_stage_command(workflow))

    assert result.turn_status is TurnStatus.WAITING_USER
    assert result.interrupt is not None
    assert result.interrupt.reason_code == "authorization_required"
    assert provider.start_calls == 0
    assert await operations.safe_persistence_snapshot(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    ) == {"operations": []}


@pytest.mark.asyncio
async def test_paid_scene_stage_starts_each_claimed_m06_operation_once() -> None:
    provider = CountingProvider()
    operations = build_live_operations(provider)
    repository = _SeededVideoRepository(task_store=MemoryPixelFlowTaskStore())
    workflow = await repository.seed_state(_reviewed_scene_package_state())
    command = paid_stage_command(workflow)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
    handler = VideoLiveWorkflowHandler(
        repository=repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=operations,
        clock=_Clock(),
    )

    result = await handler.dispatch(command)

    assert result.turn_status is TurnStatus.COMPLETED
    assert result.workflow.current_stage == "generate_scene_videos"
    assert provider.start_calls == 3
    snapshot = await operations.safe_persistence_snapshot(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    )
    assert len(snapshot["operations"]) == 3
    assert {item["status"] for item in snapshot["operations"]} == {"polling"}
    assert FAKE_AUTHORIZATION not in json.dumps(snapshot, ensure_ascii=False)


@pytest.mark.asyncio
async def test_start_402_releases_lease_and_explicit_retry_reuses_operation() -> None:
    provider = ScriptedProvider(
        start_results=[
            _HttpStatusError(402),
            {
                "job_id": "provider-after-recharge",
                "status": "running",
                "result": {"progress": 0},
            },
        ]
    )
    operations = build_live_operations(provider)

    with pytest.raises(OperationStartQuotaPausedError) as error:
        await operations.start(
            request(),
            credential=TransientTurnCredential(FAKE_AUTHORIZATION),
        )
    retried = await operations.start(
        request(),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )

    assert error.value.reason_code == "provider_quota_insufficient"
    assert error.value.operation.job_id == retried.job_id
    assert retried.status is ExternalJobStatus.POLLING
    assert retried.provider_job_id == "provider-after-recharge"
    assert provider.start_calls == 2


@pytest.mark.asyncio
async def test_quota_pause_authorized_handler_resumes_original_job_without_restart() -> None:
    """quota 暂停只能经真实 Handler 恢复，且不得重新执行 Provider start。"""

    harness = await _paused_quota_handler_harness()
    command = _quota_retry_command(harness, suffix="task5-authorized-handler")
    vault = TransientCredentialVault()
    vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
    handler = VideoLiveWorkflowHandler(
        repository=harness.repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=harness.operations,
        clock=harness.clock,
    )
    start_calls_before = harness.provider.start_calls

    result = await handler.dispatch(command)

    resumed = await harness.operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=harness.pending.job_id,
    )
    assert resumed is not None
    assert resumed.job_id == harness.pending.job_id
    assert resumed.provider_job_id == harness.paused.operation.provider_job_id
    assert resumed.attempt == harness.paused.operation.attempt == 1
    assert resumed.next_poll_at == harness.clock.now()
    assert result.operation_event_claim is not None
    assert harness.provider.start_calls == start_calls_before


@pytest.mark.asyncio
async def test_quota_pause_event_builds_original_turn_authorization_projection() -> None:
    """缺失 quota 投影 Service 时必须暴露暂停 overlay 与原 Turn 中断合同。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )
    from pixelflow.agent_workflows.video.state_codec import (
        decode_video_workflow_state,
        encode_video_workflow_state,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    store = MemoryPixelFlowTaskStore()
    await _seed_conversation(store)
    repository = MemoryVideoRuntimeRepository(
        task_store=store,
        completion_clock=clock.now,
    )
    operations = build_live_operations(
        provider,
        clock=clock,
        repository=repository,
    )
    state = await _start_generation_operations(operations)
    await _commit_seed_state(repository, store, state)
    pending = state.pending_operations[0]
    clock.advance(seconds=3)
    leased = await OperationLeaseCoordinator(
        repository,
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    ).claim(
        pending.job_id,
        lease_owner="task4-quota-poller",
        now=clock.now(),
        lease_expires_at=clock.now() + timedelta(seconds=30),
    )
    assert leased is not None
    paused = await OperationQuotaCoordinator(
        repository,
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
    ).record_pause(
        pending.job_id,
        lease_owner="task4-quota-poller",
        now=clock.now(),
    )
    envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert envelope is not None
    assert (
        pending.workflow_id,
        pending.stage,
        pending.attempt,
        state.workflow_id,
        state.conversation_id,
    ) == (
        paused.operation.workflow_id,
        paused.operation.stage,
        paused.operation.attempt,
        paused.operation.workflow_id,
        paused.operation.conversation_id,
    )

    projection = VideoOperationQuotaProjectionService().build(
        user_id=USER_ID,
        envelope=envelope,
        operation=paused.operation,
        quota_event=paused.event,
    )

    assert projection.workflow.status is WorkflowStatus.PAUSED_QUOTA
    assert projection.workflow_state.payload == envelope.payload
    assert projection.workflow_state.workflow_version == 2
    assert projection.workflow_state.last_turn_id == envelope.last_turn_id
    assert projection.workflow_state.last_action_key == paused.event.event_id
    assert projection.open_interrupt is not None
    assert projection.open_interrupt.turn_id == envelope.last_turn_id
    assert projection.open_interrupt.thread_id == (
        f"quota-paused:{paused.event.event_id}:v2"
    )
    advanced_envelope = encode_video_workflow_state(
        user_id=USER_ID,
        state=decode_video_workflow_state(envelope),
        workflow_version=3,
        last_turn_id=envelope.last_turn_id,
        last_action_key=envelope.last_action_key,
    )
    advanced_projection = VideoOperationQuotaProjectionService().build(
        user_id=USER_ID,
        envelope=advanced_envelope,
        operation=paused.operation,
        quota_event=paused.event,
    )
    assert advanced_projection.open_interrupt is not None
    assert advanced_projection.open_interrupt.thread_id == (
        f"quota-paused:{paused.event.event_id}:v4"
    )
    assert (
        advanced_projection.open_interrupt.thread_id
        != projection.open_interrupt.thread_id
    )
    assert projection.open_interrupt.payload == {
        "workflow_id": WORKFLOW_ID,
        "stage": pending.stage,
        "authorization_action": {
            "action": "retry_failed",
            "intent": "video",
            "workflow_id": WORKFLOW_ID,
            "stage": pending.stage,
            "artifact_ref": None,
            "patch": {
                "job_id": pending.job_id,
                "quota_pause_revision": 1,
            },
        },
    }


@pytest.mark.asyncio
async def test_quota_projection_rejects_subclass_and_payload_extra_fields() -> None:
    """quota Handler 必须拒绝任意序列化子类字段和 payload 扩展。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    class _ExtraQuotaEvent(AgentEvent):
        injected_marker: str

    clock = _MutableClock()
    provider = ScriptedProvider()
    store = MemoryPixelFlowTaskStore()
    await _seed_conversation(store)
    repository = MemoryVideoRuntimeRepository(
        task_store=store,
        completion_clock=clock.now,
    )
    _, _, _, paused, envelope = await _pause_first_generation_operation(
        repository,
        store,
        clock,
        provider,
    )
    injected = _ExtraQuotaEvent.model_validate(
        paused.event.model_dump(mode="json")
        | {"injected_marker": "不得进入 checkpoint"}
    )
    expanded_payload = AgentEvent.model_validate(
        paused.event.model_dump(mode="json")
        | {
            "payload": paused.event.model_dump(mode="json")["payload"]
            | {"unexpected": "不得扩展"}
        }
    )

    for event in (injected, expanded_payload):
        with pytest.raises(OperationConflictError, match="quota Event 合同不合法"):
            VideoOperationQuotaProjectionService().build(
                user_id=USER_ID,
                envelope=envelope,
                operation=paused.operation,
                quota_event=event,
            )


def test_operation_event_claim_registry_returns_deeply_frozen_snapshots() -> None:
    """临时 claim 的写入副本与读取副本都必须深度只读且可序列化。"""

    event_snapshot = AgentEvent(
        event_id="event-task4-frozen-claim",
        sequence=1,
        cursor="1",
        conversation_id=CONVERSATION_ID,
        run_id="run-task4-frozen-claim",
        occurred_at=NOW,
        type="external_job.state_changed",
        payload={
            "job_id": "operation-task4-frozen-claim",
            "nested": {"items": ["scene-1"]},
        },
    )
    source = EventDeliveryClaim(
        event=AgentEvent.model_validate(event_snapshot.model_dump(mode="python")),
        delivery_attempts=1,
        lease_owner="task4-frozen-claim-worker",
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    registry = _OperationEventClaimRegistry()
    registry.remember(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id="operation-task4-frozen-claim",
        claim=source,
        now=NOW,
    )

    source.lease_owner = "mutated-source-worker"
    source.event.payload["nested"]["items"].append("scene-mutated")
    owned = registry.require(
        event_snapshot,
        idempotency_key=event_snapshot.event_id,
        now=NOW,
    )

    assert owned.claim.lease_owner == "task4-frozen-claim-worker"
    assert owned.claim.event.payload["nested"]["items"] == ["scene-1"]
    with pytest.raises(ValidationError):
        owned.claim.lease_owner = "mutated-read-worker"
    with pytest.raises(ValidationError):
        owned.claim.event.sequence = 2
    with pytest.raises(TypeError):
        owned.claim.event.payload["nested"] = {"items": []}
    with pytest.raises(AttributeError):
        owned.claim.event.payload["nested"]["items"].append("scene-2")

    serialized = owned.claim.model_dump(mode="json")
    assert json.loads(json.dumps(serialized))["event"]["payload"]["nested"] == {
        "items": ["scene-1"],
    }
    replayed = registry.require(
        event_snapshot,
        idempotency_key=event_snapshot.event_id,
        now=NOW,
    )
    assert replayed is not owned
    assert replayed.claim is not owned.claim


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_pause_event_atomically_projects_original_turn_once(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """缺失原子提交会留下 paused Workflow、原 Turn 或 Outbox 的半状态。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-pause-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        _, _, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner=f"task4-quota-pause-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert claim is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=claim.event,
        )

        committed = await repository.commit_operation_quota_state(
            claim,
            user_id=USER_ID,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=projection.open_interrupt,
            close_interrupt_revision=projection.close_interrupt_revision,
            occurred_at=clock.now(),
        )
        clock.advance(seconds=31)
        replayed = await repository.commit_operation_quota_state(
            claim,
            user_id=USER_ID,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=projection.open_interrupt,
            close_interrupt_revision=projection.close_interrupt_revision,
            occurred_at=clock.now(),
        )
        conflicting_workflow = projection.workflow.model_copy(
            update={"updated_at": clock.now()},
        )
        with pytest.raises(
            AgentRuntimeRecordConflictError,
            match="已发布投影与重放目标不一致",
        ):
            await repository.commit_operation_quota_state(
                claim,
                user_id=USER_ID,
                workflow_state=projection.workflow_state,
                workflow=conflicting_workflow,
                expected_workflow_version=envelope.workflow_version,
                open_interrupt=projection.open_interrupt,
                close_interrupt_revision=projection.close_interrupt_revision,
                occurred_at=clock.now(),
            )

        snapshot = await repository.export_safe_snapshot(
            USER_ID,
            CONVERSATION_ID,
        )
        assert committed == replayed
        assert snapshot.workflows[0].status is WorkflowStatus.PAUSED_QUOTA
        assert snapshot.turns[0].turn.turn_id == envelope.last_turn_id
        assert snapshot.turns[0].turn.status is TurnStatus.WAITING_USER
        assert len(snapshot.interrupts) == 1
        assert snapshot.interrupts[0] == projection.open_interrupt
        assert (
            await repository.list_pending_operation_quota_events(
                now=clock.now(),
                limit=100,
            )
            == []
        )
        events = await repository.list_events(USER_ID, CONVERSATION_ID)
        assert len(
            [
                event
                for event in events
                if event.type.value == "interrupt.opened"
                and event.payload["interrupt"]["interrupt_id"]
                == projection.open_interrupt.interrupt_id
            ]
        ) == 1


@pytest.mark.asyncio
async def test_sql_quota_commit_locks_turn_before_interrupt(
    tmp_path: Path,
) -> None:
    """后台 quota 与响应路径必须共享 Turn→Interrupt 行锁顺序。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        "sql",
        tmp_path / "task4-quota-lock-order.db",
    ) as (repository, store):
        await _seed_conversation(store)
        _, _, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner="task4-quota-lock-order",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert claim is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=claim.event,
        )
        engine = repository._session_factory.kw["bind"]
        selected_tables: list[str] = []

        def record_select(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            normalized = statement.lower()
            if not normalized.lstrip().startswith("select"):
                return
            if "pixelflow_agent_turns" in normalized:
                selected_tables.append("turn")
            elif "pixelflow_agent_interrupts" in normalized:
                selected_tables.append("interrupt")

        sqlalchemy_event.listen(
            engine.sync_engine,
            "before_cursor_execute",
            record_select,
        )
        try:
            await repository.commit_operation_quota_state(
                claim,
                user_id=USER_ID,
                workflow_state=projection.workflow_state,
                workflow=projection.workflow,
                expected_workflow_version=envelope.workflow_version,
                open_interrupt=projection.open_interrupt,
                close_interrupt_revision=None,
                occurred_at=clock.now(),
            )
        finally:
            sqlalchemy_event.remove(
                engine.sync_engine,
                "before_cursor_execute",
                record_select,
            )

        assert selected_tables[:2] == ["turn", "interrupt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_published_quota_replay_rejects_cross_conversation_interrupt(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """已发布 pause 重放只能绑定当前 Operation 匹配集合内的中断。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-cross-conversation-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        _, _, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner=f"task4-quota-cross-conversation-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert claim is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=claim.event,
        )
        legitimate_interrupt = projection.open_interrupt
        assert legitimate_interrupt is not None
        await repository.commit_operation_quota_state(
            claim,
            user_id=USER_ID,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=legitimate_interrupt,
            close_interrupt_revision=None,
            occurred_at=clock.now(),
        )
        attacker = legitimate_interrupt.model_copy(
            update={
                "interrupt_id": "interrupt-task4-cross-conversation",
                "conversation_id": "conversation-task4-cross-scope",
                "thread_id": "conversation-task4-cross-scope",
            },
        )
        if kind == "memory":
            repository._interrupts[(USER_ID, attacker.interrupt_id)] = attacker
        else:
            values = attacker.model_dump(mode="json")
            async with repository._session_factory() as session:
                session.add(
                    PixelFlowAgentInterruptRow(
                        interrupt_id=attacker.interrupt_id,
                        conversation_id=attacker.conversation_id,
                        user_id=attacker.user_id,
                        workflow_id=attacker.workflow_id,
                        turn_id=attacker.turn_id,
                        thread_id=attacker.thread_id,
                        checkpoint_ns=attacker.checkpoint_ns,
                        kind=attacker.kind,
                        reason_code=attacker.reason_code,
                        status=attacker.status,
                        payload_json=values["payload"],
                        response_id=None,
                        response_json=null(),
                        opened_at=attacker.opened_at,
                        closed_at=None,
                    ),
                )
                await session.commit()

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.commit_operation_quota_state(
                claim,
                user_id=USER_ID,
                workflow_state=projection.workflow_state,
                workflow=projection.workflow,
                expected_workflow_version=envelope.workflow_version,
                open_interrupt=attacker,
                close_interrupt_revision=None,
                occurred_at=clock.now(),
            )
        replayed = await repository.commit_operation_quota_state(
            claim,
            user_id=USER_ID,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=legitimate_interrupt,
            close_interrupt_revision=None,
            occurred_at=clock.now(),
        )
        assert replayed == projection.workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_commit_uses_lock_inner_completion_time(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """quota 提交等待写锁跨过 expiry 后必须保留同一 Event 给接管者。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-lock-clock-{kind}.db",
        completion_clock=clock,
    ) as (repository, store):
        await _seed_conversation(store)
        _, _, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner=f"task4-quota-lock-clock-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert claim is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=claim.event,
        )
        write_lock = (
            repository._compaction_write_lock
            if kind == "memory"
            else repository._sqlite_write_lock
        )
        assert write_lock is not None
        await write_lock.acquire()
        try:
            task = asyncio.create_task(
                repository.commit_operation_quota_state(
                    claim,
                    user_id=USER_ID,
                    workflow_state=projection.workflow_state,
                    workflow=projection.workflow,
                    expected_workflow_version=envelope.workflow_version,
                    open_interrupt=projection.open_interrupt,
                    close_interrupt_revision=None,
                    occurred_at=clock.now(),
                )
            )
            await asyncio.sleep(0)
            assert not task.done()
            clock.advance(seconds=31)
        finally:
            write_lock.release()

        with pytest.raises(TurnExecutionLeaseConflictError):
            await task
        assert await repository.get_video_state(USER_ID, WORKFLOW_ID) == envelope
        assert await repository.get_open_interrupt(USER_ID, CONVERSATION_ID) is None
        takeover = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner=f"task4-quota-lock-takeover-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert takeover is not None
        assert takeover.event.event_id == claim.event.event_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_turn_commit_uses_lock_inner_completion_time(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """当前 Turn 等待写锁跨过执行租约后不得提交旧 Graph 结果。"""

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-turn-lock-clock-{kind}.db",
        completion_clock=clock,
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert envelope is not None
        turn = TurnRecord(
            turn_id=f"turn-task4-lock-clock-{kind}",
            conversation_id=CONVERSATION_ID,
            client_input_id=UUID(
                "00000000-0000-4000-8000-000000001413"
            ),
            status=TurnStatus.ACCEPTED,
            target_workflow_id=WORKFLOW_ID,
            decision=None,
            expected_context_version=0,
            created_at=clock.now(),
        )
        await repository.enqueue_turn_for_execution(USER_ID, turn, now=clock.now())
        claim = await repository.claim_turn(
            USER_ID,
            CONVERSATION_ID,
            turn.turn_id,
            lease_owner=f"task4-turn-lock-clock-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert claim is not None
        action_key = f"task4:turn-lock-clock:{kind}"
        target_state = encode_video_workflow_state(
            user_id=USER_ID,
            state=state,
            workflow_version=envelope.workflow_version + 1,
            last_turn_id=turn.turn_id,
            last_action_key=action_key,
        )
        commit = VideoTurnCommit(
            decision=ActionDecision(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                target_workflow_id=WORKFLOW_ID,
                target_stage=None,
                target_artifact_ref=None,
                confidence=1,
                requires_confirmation=False,
                patch={},
                reason_code="task4_lock_inner_clock",
                idempotency_key=action_key,
            ),
            turn_status=TurnStatus.COMPLETED,
            workflow_state=target_state,
            workflow=project_video_workflow_state(state),
            expected_workflow_version=envelope.workflow_version,
            occurred_at=clock.now(),
        )
        write_lock = (
            repository._compaction_write_lock
            if kind == "memory"
            else repository._sqlite_write_lock
        )
        assert write_lock is not None
        await write_lock.acquire()
        try:
            task = asyncio.create_task(repository.commit_turn(claim, commit))
            await asyncio.sleep(0)
            assert not task.done()
            clock.advance(seconds=31)
        finally:
            write_lock.release()

        with pytest.raises(TurnExecutionLeaseConflictError):
            await task
        stored_turn = await repository.get_turn(USER_ID, turn.turn_id)
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.PROCESSING
        assert await repository.get_video_state(USER_ID, WORKFLOW_ID) == envelope


@pytest.mark.asyncio
async def test_sql_turn_commit_resamples_after_projection_row_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQL 投影行锁等待跨过 expiry 后必须整笔回滚，不得沿用早取时间。"""

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        "sql",
        tmp_path / "task4-turn-projection-lock-clock.db",
        completion_clock=clock,
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert envelope is not None
        turn = TurnRecord(
            turn_id="turn-task4-projection-lock-clock",
            conversation_id=CONVERSATION_ID,
            client_input_id=UUID(
                "00000000-0000-4000-8000-000000001414"
            ),
            status=TurnStatus.ACCEPTED,
            target_workflow_id=WORKFLOW_ID,
            decision=None,
            expected_context_version=0,
            created_at=clock.now(),
        )
        await repository.enqueue_turn_for_execution(USER_ID, turn, now=clock.now())
        claim = await repository.claim_turn(
            USER_ID,
            CONVERSATION_ID,
            turn.turn_id,
            lease_owner="task4-turn-projection-lock-clock",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert claim is not None
        action_key = "task4:turn-projection-lock-clock"
        target_state = encode_video_workflow_state(
            user_id=USER_ID,
            state=state,
            workflow_version=envelope.workflow_version + 1,
            last_turn_id=turn.turn_id,
            last_action_key=action_key,
        )
        commit = VideoTurnCommit(
            decision=ActionDecision(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                target_workflow_id=WORKFLOW_ID,
                target_stage=None,
                target_artifact_ref=None,
                confidence=1,
                requires_confirmation=False,
                patch={},
                reason_code="task4_projection_lock_clock",
                idempotency_key=action_key,
            ),
            turn_status=TurnStatus.COMPLETED,
            workflow_state=target_state,
            workflow=project_video_workflow_state(state),
            expected_workflow_version=envelope.workflow_version,
            occurred_at=clock.now(),
        )
        original_compare = repository._sql_compare_and_set_state

        async def wait_for_projection_row_lock(session: object, target: object) -> None:
            clock.advance(seconds=31)
            await original_compare(session, target)

        monkeypatch.setattr(
            repository,
            "_sql_compare_and_set_state",
            wait_for_projection_row_lock,
        )

        with pytest.raises(TurnExecutionLeaseConflictError):
            await repository.commit_turn(claim, commit)
        stored_turn = await repository.get_turn(USER_ID, turn.turn_id)
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.PROCESSING
        assert await repository.get_video_state(USER_ID, WORKFLOW_ID) == envelope


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_pause_event_opens_one_graph_interrupt_on_original_turn(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """后台 pause Event 必须幂等建立独立 checkpoint 与原 Turn 中断。"""

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-graph-pause-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations, _, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({}),
            checkpointer=InMemorySaver(),
        )
        observer = _RecordingExternalJobObserver()
        quota_handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            graph=graph,
            external_job_observer=observer,
        )
        runtime = operations.build_recovery_runtime(
            resumer=_RecordingResumer(),
            quota_resumer=quota_handler,
            worker_id=f"task4-quota-graph-pause-{kind}",
        )

        await runtime.run_once()
        await runtime.run_once()

        snapshot = await repository.export_safe_snapshot(
            USER_ID,
            CONVERSATION_ID,
        )
        assert snapshot.workflows[0].status is WorkflowStatus.PAUSED_QUOTA
        assert snapshot.turns[0].turn.turn_id == envelope.last_turn_id
        assert snapshot.turns[0].turn.status is TurnStatus.WAITING_USER
        assert len(snapshot.interrupts) == 1
        opened = snapshot.interrupts[0]
        assert opened.payload["authorization_action"]["patch"] == {
            "job_id": pending.job_id,
            "quota_pause_revision": 1,
        }
        assert opened.thread_id == f"quota-paused:{paused.event.event_id}:v2"
        checkpoint = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": opened.thread_id,
                    "checkpoint_ns": "",
                }
            }
        )
        assert len(checkpoint.interrupts) == 1
        assert checkpoint.values["workflow_dispatch_result"]["state"][
            "last_action_key"
        ] == paused.event.event_id
        assert observer.states == [ProviderJobOutcome.PAUSED_QUOTA]
        assert provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_observer_failure_does_not_block_published_pause(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """指标旁路抛错后 pause Event 仍须提交、发布且重放不重复观察。"""

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task6-quota-observer-failure-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations, _, _, paused, _ = await _pause_first_generation_operation(
            repository,
            store,
            clock,
            provider,
        )
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({}),
            checkpointer=InMemorySaver(),
        )
        observer = _FailingExternalJobObserver()
        quota_handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            graph=graph,
            external_job_observer=observer,
        )
        runtime = operations.build_recovery_runtime(
            resumer=_RecordingResumer(),
            quota_resumer=quota_handler,
            worker_id=f"task6-quota-observer-failure-{kind}",
        )

        await runtime.run_once()
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        assert restored.last_action_key == paused.event.event_id
        assert observer.states == [ProviderJobOutcome.PAUSED_QUOTA]
        assert not await repository.list_pending_operation_quota_events(
            now=clock.now(),
            limit=10,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_second_quota_pause_waits_without_poisoning_checkpoint(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """同一 Workflow 的第二个 pause 必须等待首个授权中断关闭后再建 checkpoint。"""

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-two-pauses-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        pending = tuple(state.pending_operations[:2])
        assert len(pending) == 2
        transitions = []
        clock.advance(seconds=3)
        for index, item in enumerate(pending, start=1):
            lease_owner = f"task4-two-pauses-poller-{kind}-{index}"
            leased = await OperationLeaseCoordinator(
                repository,
                user_id=USER_ID,
                conversation_id=CONVERSATION_ID,
            ).claim(
                item.job_id,
                lease_owner=lease_owner,
                now=clock.now(),
                lease_expires_at=clock.now() + timedelta(seconds=30),
            )
            assert leased is not None
            transitions.append(
                await OperationQuotaCoordinator(
                    repository,
                    user_id=USER_ID,
                    conversation_id=CONVERSATION_ID,
                ).record_pause(
                    item.job_id,
                    lease_owner=lease_owner,
                    now=clock.now(),
                )
            )

        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({}),
            checkpointer=InMemorySaver(),
        )
        quota_handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            graph=graph,
        )
        runtime = operations.build_recovery_runtime(
            resumer=_RecordingResumer(),
            quota_resumer=quota_handler,
            worker_id=f"task4-two-pauses-{kind}",
        )

        await runtime.run_once()

        first_interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert first_interrupt is not None
        assert first_interrupt.payload["authorization_action"]["patch"][
            "job_id"
        ] == pending[0].job_id
        second_event = transitions[1].event
        second_checkpoint = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": f"quota-paused:{second_event.event_id}:v2",
                    "checkpoint_ns": "",
                }
            }
        )
        assert dict(second_checkpoint.values or {}) == {}
        assert tuple(second_checkpoint.next or ()) == ()
        assert tuple(second_checkpoint.interrupts or ()) == ()

        responded_first = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            first_interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001411"
            ),
            response_value={"content": "恢复第一个分镜任务"},
            responded_at=clock.now(),
        )
        first_turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded_first.interrupt_id,
            lease_owner=f"task4-two-pauses-turn-{kind}-1",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert first_turn_claim is not None
        first_resume = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            pending[0].job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner=f"task4-two-pauses-resume-{kind}-1",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        operations.remember_quota_resume_claim(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=pending[0].job_id,
            claim=first_resume.claim,
        )
        await quota_handler.resume_external_job_quota(
            workflow_namespace(CONVERSATION_ID, WORKFLOW_ID),
            quota_event=first_resume.claim.event,
            idempotency_key=first_resume.claim.event.event_id,
        )

        clock.advance(seconds=61)
        await runtime.run_once()

        second_interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert second_interrupt is not None
        assert second_interrupt.payload["authorization_action"]["patch"] == {
            "job_id": pending[1].job_id,
            "quota_pause_revision": 1,
        }
        assert second_interrupt.thread_id == (
            f"quota-paused:{second_event.event_id}:v4"
        )
        replayable_checkpoint = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": second_interrupt.thread_id,
                    "checkpoint_ns": "",
                }
            }
        )
        assert len(replayable_checkpoint.interrupts) == 1
        assert replayable_checkpoint.values["workflow_dispatch_result"]["state"][
            "last_action_key"
        ] == second_event.event_id

        responded_second = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            second_interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001412"
            ),
            response_value={"content": "恢复第二个分镜任务"},
            responded_at=clock.now(),
        )
        second_turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded_second.interrupt_id,
            lease_owner=f"task4-two-pauses-turn-{kind}-2",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert second_turn_claim is not None
        second_resume = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            pending[1].job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner=f"task4-two-pauses-resume-{kind}-2",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        operations.remember_quota_resume_claim(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=pending[1].job_id,
            claim=second_resume.claim,
        )
        await quota_handler.resume_external_job_quota(
            workflow_namespace(CONVERSATION_ID, WORKFLOW_ID),
            quota_event=second_resume.claim.event,
            idempotency_key=second_resume.claim.event.event_id,
        )

        for item in pending:
            operation = await repository.get_operation(USER_ID, item.job_id)
            assert operation is not None
            assert operation.next_poll_at is not None
        assert await repository.get_open_interrupt(USER_ID, CONVERSATION_ID) is None
        assert provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_concurrent_quota_pauses_isolate_losing_checkpoint(
    kind: RepositoryKind,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发 pause 的 CAS 失败方必须改用新版本线程，不能覆盖旧 checkpoint。"""

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-concurrent-pauses-{kind}.db",
        completion_clock=clock,
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        pending = tuple(state.pending_operations[:2])
        assert len(pending) == 2
        transitions = []
        clock.advance(seconds=3)
        for index, item in enumerate(pending, start=1):
            lease_owner = f"task4-concurrent-poller-{kind}-{index}"
            leased = await OperationLeaseCoordinator(
                repository,
                user_id=USER_ID,
                conversation_id=CONVERSATION_ID,
            ).claim(
                item.job_id,
                lease_owner=lease_owner,
                now=clock.now(),
                lease_expires_at=clock.now() + timedelta(seconds=30),
            )
            assert leased is not None
            transitions.append(
                await OperationQuotaCoordinator(
                    repository,
                    user_id=USER_ID,
                    conversation_id=CONVERSATION_ID,
                ).record_pause(
                    item.job_id,
                    lease_owner=lease_owner,
                    now=clock.now(),
                )
            )

        claims = []
        for index, (item, transition) in enumerate(
            zip(pending, transitions, strict=True),
            start=1,
        ):
            claim = await operations._recovery_repository.claim_operation_quota_event(
                USER_ID,
                CONVERSATION_ID,
                transition.event.event_id,
                item.job_id,
                quota_pause_revision=1,
                quota_state="paused",
                lease_owner=f"task4-concurrent-event-{kind}-{index}",
                now=clock.now(),
                lease_expires_at=clock.now() + timedelta(seconds=30),
            )
            assert claim is not None
            claims.append(claim)

        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({}),
            checkpointer=InMemorySaver(),
        )
        quota_handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            graph=graph,
        )
        original_checkpoint = quota_handler._checkpoint_quota_projection
        checkpoint_gate = asyncio.Event()
        entered = 0

        async def synchronize_checkpoint(**kwargs: object) -> None:
            nonlocal entered
            entered += 1
            if entered == 2:
                checkpoint_gate.set()
            await checkpoint_gate.wait()
            await original_checkpoint(**kwargs)

        monkeypatch.setattr(
            quota_handler,
            "_checkpoint_quota_projection",
            synchronize_checkpoint,
        )
        namespace = workflow_namespace(CONVERSATION_ID, WORKFLOW_ID)
        results = await asyncio.gather(
            *(
                quota_handler.resume_external_job_quota(
                    namespace,
                    quota_event=claim.event,
                    idempotency_key=claim.event.event_id,
                )
                for claim in claims
            ),
            return_exceptions=True,
        )
        monkeypatch.setattr(
            quota_handler,
            "_checkpoint_quota_projection",
            original_checkpoint,
        )
        assert len([item for item in results if item is None]) == 1
        assert len(
            [
                item
                for item in results
                if isinstance(item, VideoWorkflowStateConflictError)
            ]
        ) == 1

        first_interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert first_interrupt is not None
        first_job_id = first_interrupt.payload["authorization_action"]["patch"][
            "job_id"
        ]
        loser_index = next(
            index
            for index, item in enumerate(pending)
            if item.job_id != first_job_id
        )
        loser_claim = claims[loser_index]
        stale_checkpoint = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": (
                        f"quota-paused:{loser_claim.event.event_id}:v2"
                    ),
                    "checkpoint_ns": "",
                }
            }
        )
        assert len(stale_checkpoint.interrupts) == 1

        responded = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            first_interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001415"
            ),
            response_value={"content": "恢复首个并发配额任务"},
            responded_at=clock.now(),
        )
        turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded.interrupt_id,
            lease_owner=f"task4-concurrent-turn-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert turn_claim is not None
        first_resume = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            first_job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner=f"task4-concurrent-resume-{kind}",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        operations.remember_quota_resume_claim(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_job_id,
            claim=first_resume.claim,
        )
        await quota_handler.resume_external_job_quota(
            namespace,
            quota_event=first_resume.claim.event,
            idempotency_key=first_resume.claim.event.event_id,
        )

        await quota_handler.resume_external_job_quota(
            namespace,
            quota_event=loser_claim.event,
            idempotency_key=loser_claim.event.event_id,
        )
        second_interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert second_interrupt is not None
        assert second_interrupt.payload["authorization_action"]["patch"] == {
            "job_id": pending[loser_index].job_id,
            "quota_pause_revision": 1,
        }
        assert second_interrupt.thread_id == (
            f"quota-paused:{loser_claim.event.event_id}:v4"
        )
        refreshed = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": second_interrupt.thread_id,
                    "checkpoint_ns": "",
                }
            }
        )
        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        assert refreshed.values["workflow_dispatch_result"]["state"][
            "workflow_version"
        ] == restored.workflow_version
        assert refreshed.values["workflow_dispatch_result"]["state"][
            "last_action_key"
        ] == loser_claim.event.event_id
        stale_after_retry = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": (
                        f"quota-paused:{loser_claim.event.event_id}:v2"
                    ),
                    "checkpoint_ns": "",
                }
            }
        )
        assert stale_after_retry.values["workflow_dispatch_result"]["state"][
            "workflow_version"
        ] == 2
        assert provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_resume_event_atomically_restores_domain_state_once(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """resume 必须关闭同 revision 中断、完成原 Turn 并发布同一 Event。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-resume-{kind}.db",
        completion_clock=clock,
    ) as (repository, store):
        await _seed_conversation(store)
        _, state, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        pause_claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner=f"task4-pause-before-resume-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert pause_claim is not None
        pause_projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=pause_claim.event,
        )
        await repository.commit_operation_quota_state(
            pause_claim,
            user_id=USER_ID,
            workflow_state=pause_projection.workflow_state,
            workflow=pause_projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=pause_projection.open_interrupt,
            close_interrupt_revision=None,
            occurred_at=clock.now(),
        )
        interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert interrupt is not None
        responded = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001404"
            ),
            response_value={"content": "已恢复额度"},
            responded_at=clock.now(),
        )
        turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded.interrupt_id,
            lease_owner=f"task4-resume-turn-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert turn_claim is not None
        authorized = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            pending.job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner=f"task4-resume-event-{kind}",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        paused_envelope = await repository.get_video_state(
            USER_ID,
            WORKFLOW_ID,
        )
        assert paused_envelope is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=paused_envelope,
            operation=authorized.operation,
            quota_event=authorized.claim.event,
        )
        assert projection.workflow.status is project_video_workflow_state(
            state
        ).status
        assert projection.open_interrupt is None
        assert projection.close_interrupt_revision == 1

        first_closed_at = clock.now()
        committed = await repository.commit_operation_quota_state(
            authorized.claim,
            user_id=USER_ID,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=paused_envelope.workflow_version,
            open_interrupt=None,
            close_interrupt_revision=1,
            occurred_at=first_closed_at,
        )
        clock.advance(seconds=31)
        replayed = await repository.commit_operation_quota_state(
            authorized.claim,
            user_id=USER_ID,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=paused_envelope.workflow_version,
            open_interrupt=None,
            close_interrupt_revision=1,
            occurred_at=clock.now(),
        )
        conflicting_workflow = projection.workflow.model_copy(
            update={"updated_at": clock.now()},
        )
        with pytest.raises(
            AgentRuntimeRecordConflictError,
            match="已发布投影与重放目标不一致",
        ):
            await repository.commit_operation_quota_state(
                authorized.claim,
                user_id=USER_ID,
                workflow_state=projection.workflow_state,
                workflow=conflicting_workflow,
                expected_workflow_version=paused_envelope.workflow_version,
                open_interrupt=None,
                close_interrupt_revision=1,
                occurred_at=clock.now(),
            )

        closed = await repository.get_interrupt(USER_ID, interrupt.interrupt_id)
        snapshot = await repository.export_safe_snapshot(
            USER_ID,
            CONVERSATION_ID,
        )
        assert committed == replayed
        assert closed is not None
        assert closed.status == "closed"
        assert closed.closed_at == first_closed_at
        assert snapshot.turns[0].turn.status is TurnStatus.COMPLETED
        assert snapshot.workflows[0].status is committed.status
        assert (
            await repository.list_pending_operation_quota_events(
                now=clock.now(),
                limit=100,
            )
            == []
        )
        assert provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_authorized_resume_claim_blocks_background_until_turn_commit(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """当前授权 Turn 必须持有 resume Event，并在提交时原子公开恢复。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-authorized-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        _, _, pending, paused, envelope = await _pause_first_generation_operation(
            repository,
            store,
            clock,
            provider,
        )
        pause_claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner=f"task4-authorized-pause-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert pause_claim is not None
        pause_projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=pause_claim.event,
        )
        await repository.commit_operation_quota_state(
            pause_claim,
            user_id=USER_ID,
            workflow_state=pause_projection.workflow_state,
            workflow=pause_projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=pause_projection.open_interrupt,
            close_interrupt_revision=None,
            occurred_at=clock.now(),
        )
        interrupt = await repository.get_open_interrupt(USER_ID, CONVERSATION_ID)
        assert interrupt is not None
        responded = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001405"
            ),
            response_value={"content": "已恢复额度"},
            responded_at=clock.now(),
        )
        turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded.interrupt_id,
            lease_owner=f"task4-authorized-turn-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert turn_claim is not None
        authorized = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            pending.job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner=f"task4-authorized-event-{kind}",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        paused_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert paused_envelope is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=paused_envelope,
            operation=authorized.operation,
            quota_event=authorized.claim.event,
        )
        assert (
            await repository.list_pending_operation_quota_events(
                now=clock.now(),
                limit=100,
            )
            == []
        )
        blocked_due = await repository.list_due_operations(
            now=clock.now(),
            limit=100,
        )
        assert pending.job_id not in {
            candidate.operation.job_id for candidate in blocked_due
        }

        decision = ActionDecision(
            action=AgentAction.RETRY_FAILED,
            intent=AgentIntent.VIDEO,
            target_workflow_id=WORKFLOW_ID,
            target_stage=authorized.operation.stage,
            target_artifact_ref=None,
            confidence=1,
            requires_confirmation=False,
            patch={
                "job_id": pending.job_id,
                "quota_pause_revision": 1,
            },
            reason_code="provider_quota_resume_authorized",
            idempotency_key="task4:authorized-quota-resume",
        )
        dispatch = WorkflowDispatchResult(
            state=projection.workflow_state,
            workflow=projection.workflow,
            turn_status=TurnStatus.COMPLETED,
            operation_event_claim=authorized.claim,
        )
        executor = object.__new__(SupervisorTurnExecutor)
        executor._clock = clock.now
        graph_state = {
            "decision": decision.model_dump(mode="json"),
            "workflow_dispatch_result": dispatch.model_dump(mode="json"),
        }
        commit = executor._commit_from_graph(
            turn_claim,
            decision,
            graph_state,
            close_interrupt_id=interrupt.interrupt_id,
        )
        assert commit.operation_event_claim == authorized.claim
        assert commit.workflow_state.last_action_key == authorized.claim.event.event_id
        assert "Bearer" not in json.dumps(
            commit.model_dump(mode="json"),
            ensure_ascii=False,
        )

        invalid_decision = decision.model_copy(
            update={"action": AgentAction.CONTINUE_WORKFLOW},
        )
        with pytest.raises(ValueError, match="非授权恢复动作"):
            executor._commit_from_graph(
                turn_claim,
                invalid_decision,
                graph_state | {
                    "decision": invalid_decision.model_dump(mode="json"),
                },
                close_interrupt_id=interrupt.interrupt_id,
            )

        await repository.commit_turn(turn_claim, commit)

        due = await repository.list_due_operations(
            now=clock.now(),
            limit=100,
        )
        assert pending.job_id in {
            candidate.operation.job_id for candidate in due
        }
        assert provider.start_calls == 3


@pytest.mark.asyncio
async def test_sql_authorized_resume_commit_never_relocks_turn_after_interrupt(
    tmp_path: Path,
) -> None:
    """授权恢复提交必须只按 Turn→Interrupt 取锁，不能再形成反向边。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider()
    async with _video_repository(
        "sql",
        tmp_path / "task4-authorized-turn-lock-order.db",
    ) as (repository, store):
        await _seed_conversation(store)
        _, _, pending, paused, envelope = await _pause_first_generation_operation(
            repository,
            store,
            clock,
            provider,
        )
        pause_claim = await repository.claim_operation_quota_event(
            USER_ID,
            CONVERSATION_ID,
            paused.event.event_id,
            pending.job_id,
            quota_pause_revision=1,
            quota_state="paused",
            lease_owner="task4-lock-order-pause",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        assert pause_claim is not None
        pause_projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=envelope,
            operation=paused.operation,
            quota_event=pause_claim.event,
        )
        await repository.commit_operation_quota_state(
            pause_claim,
            user_id=USER_ID,
            workflow_state=pause_projection.workflow_state,
            workflow=pause_projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=pause_projection.open_interrupt,
            close_interrupt_revision=None,
            occurred_at=clock.now(),
        )
        interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert interrupt is not None
        responded = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001416"
            ),
            response_value={"content": "已恢复额度"},
            responded_at=clock.now(),
        )
        turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded.interrupt_id,
            lease_owner="task4-lock-order-turn",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert turn_claim is not None
        authorized = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            pending.job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner="task4-lock-order-event",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        paused_envelope = await repository.get_video_state(
            USER_ID,
            WORKFLOW_ID,
        )
        assert paused_envelope is not None
        projection = VideoOperationQuotaProjectionService().build(
            user_id=USER_ID,
            envelope=paused_envelope,
            operation=authorized.operation,
            quota_event=authorized.claim.event,
        )
        decision = ActionDecision(
            action=AgentAction.RETRY_FAILED,
            intent=AgentIntent.VIDEO,
            target_workflow_id=WORKFLOW_ID,
            target_stage=authorized.operation.stage,
            target_artifact_ref=None,
            confidence=1,
            requires_confirmation=False,
            patch={
                "job_id": pending.job_id,
                "quota_pause_revision": 1,
            },
            reason_code="provider_quota_resume_authorized",
            idempotency_key="task4:authorized-lock-order",
        )
        dispatch = WorkflowDispatchResult(
            state=projection.workflow_state,
            workflow=projection.workflow,
            turn_status=TurnStatus.COMPLETED,
            operation_event_claim=authorized.claim,
        )
        executor = object.__new__(SupervisorTurnExecutor)
        executor._clock = clock.now
        commit = executor._commit_from_graph(
            turn_claim,
            decision,
            {
                "decision": decision.model_dump(mode="json"),
                "workflow_dispatch_result": dispatch.model_dump(mode="json"),
            },
            close_interrupt_id=interrupt.interrupt_id,
        )
        engine = repository._session_factory.kw["bind"]
        selected_tables: list[str] = []

        def record_select(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            normalized = statement.lower()
            if not normalized.lstrip().startswith("select"):
                return
            if "pixelflow_agent_turns" in normalized:
                selected_tables.append("turn")
            elif "pixelflow_agent_interrupts" in normalized:
                selected_tables.append("interrupt")

        sqlalchemy_event.listen(
            engine.sync_engine,
            "before_cursor_execute",
            record_select,
        )
        try:
            await repository.commit_turn(turn_claim, commit)
        finally:
            sqlalchemy_event.remove(
                engine.sync_engine,
                "before_cursor_execute",
                record_select,
            )

        first_interrupt = selected_tables.index("interrupt")
        assert selected_tables[0] == "turn"
        assert "turn" not in selected_tables[first_interrupt + 1 :]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quota_crash_after_authorized_claim_replays_same_resume_event(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """授权 claim 后退出时，后台只能在租约过期后接管同一 resume Event。"""

    clock = _MutableClock()
    running_results = [
        {
            "job_id": f"provider-scripted-{index}",
            "status": "running",
            "result": {"progress": 10},
        }
        for index in range(1, 4)
    ]
    provider = ScriptedProvider(status_results=running_results)
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-claim-crash-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations, _, pending, _, _ = await _pause_first_generation_operation(
            repository,
            store,
            clock,
            provider,
        )
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({}),
            checkpointer=InMemorySaver(),
        )
        observer = _RecordingExternalJobObserver()
        quota_handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            graph=graph,
            external_job_observer=observer,
        )
        runtime = operations.build_recovery_runtime(
            resumer=_RecordingResumer(),
            quota_resumer=quota_handler,
            worker_id=f"task4-quota-claim-crash-{kind}",
        )
        await runtime.run_once()
        assert observer.states == [ProviderJobOutcome.PAUSED_QUOTA]
        interrupt = await repository.get_open_interrupt(
            USER_ID,
            CONVERSATION_ID,
        )
        assert interrupt is not None
        responded = await repository.store_interrupt_response(
            USER_ID,
            CONVERSATION_ID,
            interrupt.interrupt_id,
            client_response_id=UUID(
                "00000000-0000-4000-8000-000000001406"
            ),
            response_value={"content": "已恢复额度"},
            responded_at=clock.now(),
        )
        turn_claim = await repository.claim_interrupt_resume(
            USER_ID,
            CONVERSATION_ID,
            responded.interrupt_id,
            lease_owner=f"task4-quota-crashed-turn-{kind}",
            now=clock.now(),
            lease_expires_at=clock.now() + timedelta(seconds=60),
        )
        assert turn_claim is not None
        authorized = await OperationQuotaCoordinator(
            repository,
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).authorize_resume(
            pending.job_id,
            workflow_id=WORKFLOW_ID,
            expected_revision=1,
            delivery_lease_owner=f"task4-quota-crashed-event-{kind}",
            now=clock.now(),
            delivery_lease_expires_at=clock.now() + timedelta(seconds=30),
        )
        resume_event_id = authorized.claim.event.event_id
        before_resume = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert before_resume is not None
        assert before_resume.last_action_key != resume_event_id

        clock.advance(seconds=31)
        await runtime.run_once()
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        assert restored.last_action_key == resume_event_id
        closed = await repository.get_interrupt(USER_ID, interrupt.interrupt_id)
        assert closed is not None
        assert closed.status == "closed"
        checkpoint = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": (
                        f"quota-resumed:{resume_event_id}"
                        f":v{restored.workflow_version}"
                    ),
                    "checkpoint_ns": "",
                }
            }
        )
        assert checkpoint.next == ()
        assert checkpoint.interrupts == ()
        assert checkpoint.values["workflow_dispatch_result"]["state"][
            "last_action_key"
        ] == resume_event_id
        events = await repository.list_events(USER_ID, CONVERSATION_ID)
        assert len([item for item in events if item.event_id == resume_event_id]) == 1
        assert observer.states == [
            ProviderJobOutcome.PAUSED_QUOTA,
            ProviderJobOutcome.POLLING,
        ]
        assert provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("quota_state", ["paused", "resumed"])
@pytest.mark.parametrize(
    "crash_hook",
    [
        "after_claim",
        "before_graph_checkpoint",
        "after_repository_commit",
        "before_outbox_confirmation",
    ],
)
async def test_quota_crash_windows_replay_one_projection_after_restart(
    kind: RepositoryKind,
    quota_state: str,
    crash_hook: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pause/resume 的四个退出窗口都必须由同一 Event checkpoint 接管。"""

    from pixelflow.agent_workflows.video.live_quota import (
        VideoOperationQuotaProjectionService,
    )

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": f"provider-scripted-{(index % 3) + 1}",
                "status": "running",
                "result": {"progress": 10},
            }
            for index in range(30)
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task4-quota-crash-{quota_state}-{crash_hook}-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations, _, pending, paused, envelope = (
            await _pause_first_generation_operation(
                repository,
                store,
                clock,
                provider,
            )
        )
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({}),
            checkpointer=InMemorySaver(),
        )
        stable_interrupt_id: str | None = None
        target_event = paused.event
        if quota_state == "resumed":
            pause_handler = VideoOperationQuotaStateHandler(
                repository=repository,
                operations=operations,
                clock=clock,
                graph=graph,
            )
            await operations.build_recovery_runtime(
                resumer=_RecordingResumer(),
                quota_resumer=pause_handler,
                worker_id=f"task4-quota-prime-{crash_hook}-{kind}",
            ).run_once()
            opened = await repository.get_open_interrupt(
                USER_ID,
                CONVERSATION_ID,
            )
            assert opened is not None
            stable_interrupt_id = opened.interrupt_id
            responded = await repository.store_interrupt_response(
                USER_ID,
                CONVERSATION_ID,
                opened.interrupt_id,
                client_response_id=UUID(
                    "00000000-0000-4000-8000-000000001407"
                ),
                response_value={"content": "已恢复额度"},
                responded_at=clock.now(),
            )
            turn_claim = await repository.claim_interrupt_resume(
                USER_ID,
                CONVERSATION_ID,
                responded.interrupt_id,
                lease_owner=f"task4-quota-crash-turn-{crash_hook}-{kind}",
                now=clock.now(),
                lease_expires_at=clock.now() + timedelta(seconds=120),
            )
            assert turn_claim is not None
            authorized = await OperationQuotaCoordinator(
                repository,
                user_id=USER_ID,
                conversation_id=CONVERSATION_ID,
            ).authorize_resume(
                pending.job_id,
                workflow_id=WORKFLOW_ID,
                expected_revision=1,
                delivery_lease_owner=(
                    f"task4-quota-initial-resume-{crash_hook}-{kind}"
                ),
                now=clock.now(),
                delivery_lease_expires_at=clock.now()
                + timedelta(seconds=30),
            )
            target_event = authorized.claim.event
            clock.advance(seconds=31)
        else:
            pause_projection = VideoOperationQuotaProjectionService().build(
                user_id=USER_ID,
                envelope=envelope,
                operation=paused.operation,
                quota_event=paused.event,
            )
            assert pause_projection.open_interrupt is not None
            stable_interrupt_id = pause_projection.open_interrupt.interrupt_id

        handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            graph=graph,
        )
        quota_resumer: object = handler
        original_claim_event = (
            operations._recovery_repository.claim_operation_quota_event
        )
        original_commit = repository.commit_operation_quota_state
        original_confirmation = (
            operations._recovery_repository.complete_event_delivery
        )
        failed = False

        async def fail_after_claim(*args, **kwargs):
            nonlocal failed
            claim = await original_claim_event(*args, **kwargs)
            if claim is not None and not failed:
                failed = True
                raise RuntimeError("模拟 quota Event 领取后退出")
            return claim

        async def fail_after_repository(*args, **kwargs):
            nonlocal failed
            result = await original_commit(*args, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("模拟 quota Repository 提交后退出")
            return result

        async def fail_before_confirmation(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("模拟 quota Outbox 确认前退出")
            return await original_confirmation(*args, **kwargs)

        if crash_hook == "after_claim":
            monkeypatch.setattr(
                operations._recovery_repository,
                "claim_operation_quota_event",
                fail_after_claim,
            )
        elif crash_hook == "before_graph_checkpoint":
            quota_resumer = _FailOnceQuotaResumer(handler)
        elif crash_hook == "after_repository_commit":
            monkeypatch.setattr(
                repository,
                "commit_operation_quota_state",
                fail_after_repository,
            )
        else:
            monkeypatch.setattr(
                operations._recovery_repository,
                "complete_event_delivery",
                fail_before_confirmation,
            )

        await operations.build_recovery_runtime(
            resumer=_RecordingResumer(),
            quota_resumer=quota_resumer,
            worker_id=f"task4-quota-crash-first-{crash_hook}-{kind}",
        ).run_once()
        assert failed or crash_hook == "before_graph_checkpoint"

        monkeypatch.setattr(
            repository,
            "commit_operation_quota_state",
            original_commit,
        )
        clock.advance(seconds=31)
        restarted_operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        restarted_handler = VideoOperationQuotaStateHandler(
            repository=repository,
            operations=restarted_operations,
            clock=clock,
            graph=graph,
        )
        restarted_runtime = restarted_operations.build_recovery_runtime(
            resumer=_RecordingResumer(),
            quota_resumer=restarted_handler,
            worker_id=f"task4-quota-crash-restarted-{crash_hook}-{kind}",
        )
        await restarted_runtime.run_once()
        await restarted_runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        assert restored.last_turn_id == envelope.last_turn_id
        assert restored.last_action_key == target_event.event_id
        stored_interrupt = await repository.get_interrupt(
            USER_ID,
            stable_interrupt_id,
        )
        assert stored_interrupt is not None
        expected_thread = (
            f"quota-{quota_state}:{target_event.event_id}"
            f":v{restored.workflow_version}"
        )
        checkpoint = await graph.aget_state(
            {
                "configurable": {
                    "thread_id": expected_thread,
                    "checkpoint_ns": "",
                }
            }
        )
        assert checkpoint.values["workflow_dispatch_result"]["state"][
            "last_action_key"
        ] == target_event.event_id
        if quota_state == "paused":
            assert stored_interrupt.status == "open"
            assert len(checkpoint.interrupts) == 1
        else:
            assert stored_interrupt.status == "closed"
            assert checkpoint.next == ()
            assert checkpoint.interrupts == ()
        events = await repository.list_events(USER_ID, CONVERSATION_ID)
        assert len(
            [item for item in events if item.event_id == target_event.event_id]
        ) == 1
        transition_type = (
            "interrupt.opened"
            if quota_state == "paused"
            else "interrupt.closed"
        )
        assert len(
            [
                item
                for item in events
                if item.type.value == transition_type
                and (
                    item.payload.get("interrupt", {}).get("interrupt_id")
                    if quota_state == "paused"
                    else item.payload.get("interrupt_id")
                )
                == stable_interrupt_id
            ]
        ) == 1
        assert provider.start_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_result", "expected_status", "expected_reason"),
    [
        (TimeoutError("不得持久化的超时正文"), ExternalJobStatus.TIMEOUT, "provider_timeout"),
        (_HttpStatusError(404), ExternalJobStatus.EXPIRED, "provider_job_expired"),
    ],
)
async def test_terminal_recovery_emits_one_safe_event_and_requires_new_attempt(
    status_result: object,
    expected_status: ExternalJobStatus,
    expected_reason: str,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(status_results=[status_result])
    operations = build_live_operations(provider, clock=clock)
    started = await operations.start(
        request(),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )
    resumer = _RecordingResumer()
    runtime = operations.build_recovery_runtime(
        resumer=resumer,
        worker_id="task8-recovery-terminal",
    )

    clock.advance(seconds=3)
    await runtime.run_once()
    completed = await operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=started.job_id,
    )
    manual = await runtime.recover_manually(
        USER_ID,
        CONVERSATION_ID,
        started.job_id,
    )
    await runtime.run_once()

    assert completed is not None
    assert completed.status is expected_status
    assert manual.action is OperationManualRecoveryAction.NEW_ATTEMPT_REQUIRED
    assert provider.start_calls == 1
    assert provider.status_calls == 1
    assert len(resumer.calls) == 1
    event = resumer.calls[0][1]
    assert event.payload["reason_code"] == expected_reason
    assert event.payload["status"] == expected_status.value
    assert "不得持久化" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_expired_operation_only_restarts_with_explicit_new_attempt() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(status_results=[_HttpStatusError(404)])
    operations = build_live_operations(provider, clock=clock)
    first = await operations.start(
        request(),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )
    runtime = operations.build_recovery_runtime(
        resumer=_RecordingResumer(),
        worker_id="task8-recovery-new-attempt",
    )
    clock.advance(seconds=3)
    await runtime.run_once()

    second = await operations.start(
        request(attempt=2),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )

    assert first.job_id != second.job_id
    assert second.attempt == 2
    assert second.status is ExternalJobStatus.POLLING
    assert provider.start_calls == 2


@pytest.mark.asyncio
async def test_completion_event_atomically_updates_m11_state_and_acks_outbox() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": "provider-scripted-1",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-1.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-2",
                "status": "running",
                "result": {"progress": 20},
            },
            {
                "job_id": "provider-scripted-3",
                "status": "running",
                "result": {"progress": 30},
            },
        ]
    )
    repository = _SeededVideoRepository(task_store=MemoryPixelFlowTaskStore())
    operations = build_live_operations(
        provider,
        clock=clock,
        repository=repository,
    )
    workflow = await repository.seed_state(_reviewed_scene_package_state())
    command = paid_stage_command(workflow)
    vault = TransientCredentialVault()
    vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
    dispatch = await VideoLiveWorkflowHandler(
        repository=repository,
        capabilities=_UnusedCapabilities(),
        credential_provider=vault,
        operation_port=operations,
        clock=clock,
    ).dispatch(command)
    await repository.seed_envelope(dispatch.state, dispatch.workflow)
    observer = _FailingExternalJobObserver()
    completion = VideoOperationCompletionHandler(
        repository=repository,
        operations=operations,
        clock=clock,
        external_job_observer=observer,
    )
    runtime = operations.build_recovery_runtime(
        resumer=completion,
        worker_id="task8-completion",
    )

    clock.advance(seconds=3)
    await runtime.run_once()

    restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
    assert restored is not None
    state = decode_video_workflow_state(restored)
    assert [item["scene_id"] for item in state.scene_videos] == ["scene-1"]
    pending_events = await repository.list_pending_operation_completions(
        now=clock.now(),
        limit=100,
    )
    assert pending_events == []
    assert observer.states == [ProviderJobOutcome.SUCCEEDED]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_completion_claim_bridge_works_with_memory_and_sql_repositories(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": "provider-scripted-1",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-1.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-2",
                "status": "running",
                "result": {"progress": 20},
            },
            {
                "job_id": "provider-scripted-3",
                "status": "running",
                "result": {"progress": 30},
            },
            {
                "job_id": "provider-scripted-2",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-2.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-3",
                "status": "running",
                "result": {"progress": 60},
            },
            {
                "job_id": "provider-scripted-3",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-3.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-completion-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id=f"task8-completion-{kind}",
        )

        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        assert [item["scene_id"] for item in updated.scene_videos] == ["scene-1"]
        assert restored.workflow_version == 2
        assert (
            await repository.list_pending_operation_completions(
                now=clock.now(),
                limit=100,
            )
            == []
        )
        assert await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        ) == []

        clock.advance(seconds=3)
        await runtime.run_once()
        second_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert second_envelope is not None
        second = decode_video_workflow_state(second_envelope)
        assert [item["scene_id"] for item in second.scene_videos] == [
            "scene-1",
            "scene-2",
        ]
        assert await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        ) == []

        clock.advance(seconds=3)
        await runtime.run_once()
        final_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert final_envelope is not None
        final = decode_video_workflow_state(final_envelope)
        assert [item["scene_id"] for item in final.scene_videos] == [
            "scene-1",
            "scene-2",
            "scene-3",
        ]
        message = await _assert_new_completion_projection(
            repository,
            before_message_ids=set(),
            expected_type="video_scene_packages",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "videoScenePackages",
                "generatedSceneVideos",
                "videoScenePackageEditedSceneIds",
            },
        )
        await runtime.run_once()
        replayed = await repository.list_projection_messages(USER_ID, CONVERSATION_ID)
        assert [item.message_id for item in replayed] == [message.message_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_plan_scene_asset_authorization_resumes_current_stage_once_after_restart(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """Plan 推进后的授权响应必须在当前权威阶段恢复一次付费场景资产调用。"""

    clock = _MutableClock()
    async with _video_repository(
        kind,
        tmp_path / f"task13-plan-authorization-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        plan_state = _plan_review_state()
        await _commit_seed_state(repository, store, plan_state)
        capabilities = _PaidSceneAssetCapabilities()
        vault = TransientCredentialVault()
        handler = VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=capabilities,
            credential_provider=vault,
            clock=clock,
        )
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
            checkpointer=InMemorySaver(),
        )
        executor = SupervisorTurnExecutor(
            repository=repository,
            task_store=store,
            decision_service=_ExplicitVideoDecisionService(),
            graph=graph,
            credential_vault=vault,
            clock=clock.now,
            worker_id=f"task13-plan-authorization-{kind}",
            heartbeat_interval_seconds=0.01,
            scan_interval_seconds=0.01,
        )
        original_turn_id = "turn-task13-plan-authorization"
        client_input_id = UUID("00000000-0000-4000-8000-000000001321")
        artifact_ref = plan_state.active_plan_artifact_ref
        explicit = ExplicitActionSignal(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.VIDEO,
            workflow_id=WORKFLOW_ID,
            stage="plan_review",
            artifact_ref=artifact_ref,
            patch={},
        )
        turn = TurnRecord(
            turn_id=original_turn_id,
            conversation_id=CONVERSATION_ID,
            client_input_id=client_input_id,
            status=TurnStatus.ACCEPTED,
            target_workflow_id=WORKFLOW_ID,
            decision=None,
            expected_context_version=0,
            created_at=clock.now(),
        )
        await store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id=conversation_message_id(
                    CONVERSATION_ID,
                    client_input_id,
                ),
                conversation_id=CONVERSATION_ID,
                user_id=USER_ID,
                role="user",
                content="同意 Plan 并生成场景资产",
                payload={
                    "client_message_id": str(client_input_id),
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [artifact_ref],
                    "explicit_action": explicit.model_dump(mode="json"),
                },
                created_at=clock.now().isoformat(),
            )
        )
        await repository.enqueue_turn_for_execution(
            USER_ID,
            turn,
            now=clock.now(),
        )
        try:
            await executor.notify_turn(
                SupervisorTurnScope(
                    user_id=USER_ID,
                    conversation_id=CONVERSATION_ID,
                    turn_id=original_turn_id,
                ),
                credential=None,
            )
            await executor.wait_idle()
            assert capabilities.generate_scene_assets_calls == 0

            authorization = await repository.get_open_interrupt(
                USER_ID,
                CONVERSATION_ID,
            )
            assert authorization is not None
            assert authorization.kind == "authorization_required"
            authorization_document = authorization.model_dump(mode="json")
            authorization_action = ExplicitActionSignal.model_validate(
                authorization_document["payload"]["authorization_action"]
            )
            assert authorization_document["payload"]["stage"] == "generate_scene_assets"
            assert authorization_action == ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id=WORKFLOW_ID,
                stage="generate_scene_assets",
                artifact_ref=artifact_ref,
                patch={},
            )
            assert authorization_action.workflow_id == authorization.workflow_id
            assert authorization_action.stage == authorization.payload["stage"]
            assert authorization_action.artifact_ref == authorization.payload["artifact_ref"]
            serialized_authorization = json.dumps(
                authorization_document,
                ensure_ascii=False,
            ).lower()
            assert FAKE_AUTHORIZATION.lower() not in serialized_authorization
            assert "bearer " not in serialized_authorization
            assert "token" not in serialized_authorization
            assert "credential" not in serialized_authorization

            await executor.aclose()
            executor = SupervisorTurnExecutor(
                repository=repository,
                task_store=store,
                decision_service=_ExplicitVideoDecisionService(),
                graph=graph,
                credential_vault=vault,
                clock=clock.now,
                worker_id=f"task13-plan-authorization-restarted-{kind}",
                heartbeat_interval_seconds=0.01,
                scan_interval_seconds=0.01,
            )
            response_id = UUID("00000000-0000-4000-8000-000000001322")
            response_request = InterruptResponseRequest(
                client_response_id=response_id,
                value={
                    "content": "授权后继续生成场景资产",
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [artifact_ref],
                    "explicit_action": authorization_action,
                },
            )
            response = await repository.register_interrupt_response(
                USER_ID,
                CONVERSATION_ID,
                authorization.interrupt_id,
                request=response_request,
                message=PixelFlowConversationMessageRecord(
                    message_id=conversation_message_id(
                        CONVERSATION_ID,
                        response_id,
                    ),
                    conversation_id=CONVERSATION_ID,
                    user_id=USER_ID,
                    role="user",
                    content=response_request.value.content,
                    payload={
                        "client_message_id": str(response_id),
                        "interrupt_id": authorization.interrupt_id,
                        "value": response_request.value.model_dump(mode="json"),
                        "explicit_action": authorization_action.model_dump(
                            mode="json"
                        ),
                    },
                    created_at=clock.now().isoformat(),
                ),
                responded_at=clock.now(),
            )
            await executor.notify_interrupt(
                response.interrupt,
                credential=TransientTurnCredential(FAKE_AUTHORIZATION),
            )
            await executor.wait_idle()

            assert capabilities.generate_scene_assets_calls == 1
            restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
            assert restored is not None
            restored_state = decode_video_workflow_state(restored)
            assert restored_state.current_stage.value == "scene_package_review"
            review = await repository.get_open_interrupt(
                USER_ID,
                CONVERSATION_ID,
            )
            assert review is not None
            assert review.kind == "video_scene_package_review"
            assert review.turn_id == original_turn_id
        finally:
            await executor.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("near_quota", [False, True])
async def test_interrupt_registration_distinguishes_ordinary_and_near_quota_authorization(
    kind: RepositoryKind,
    near_quota: bool,
    tmp_path: Path,
) -> None:
    """普通授权应继续，携带额度修订号的畸形授权必须拒绝。"""

    async with _video_repository(
        kind,
        tmp_path / f"task7-authorization-classification-{kind}-{near_quota}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        state = _plan_review_state()
        await _commit_seed_state(repository, store, state)
        turn_id = f"turn-task7-authorization-classification-{kind}-{near_quota}"
        turn = TurnRecord(
            turn_id=turn_id,
            conversation_id=CONVERSATION_ID,
            client_input_id=UUID(
                "00000000-0000-4000-8000-000000001329"
                if near_quota
                else "00000000-0000-4000-8000-000000001328"
            ),
            status=TurnStatus.ACCEPTED,
            target_workflow_id=WORKFLOW_ID,
            decision=None,
            expected_context_version=0,
            created_at=NOW,
        )
        await repository.enqueue_turn_for_execution(USER_ID, turn, now=NOW)
        claim = await repository.claim_turn(
            USER_ID,
            CONVERSATION_ID,
            turn_id,
            lease_owner=f"task7-authorization-classification-{kind}",
            now=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        assert claim is not None
        action = ExplicitActionSignal(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.VIDEO,
            workflow_id=WORKFLOW_ID,
            stage=state.current_stage.value,
            artifact_ref=state.active_plan_artifact_ref,
            patch={"quota_pause_revision": 1} if near_quota else {},
        )
        interrupt = StoredAgentInterrupt(
            interrupt_id=f"interrupt-task7-authorization-{kind}-{near_quota}",
            conversation_id=CONVERSATION_ID,
            workflow_id=WORKFLOW_ID,
            turn_id=turn_id,
            kind="authorization_required",
            reason_code="authorization_required",
            payload={
                "workflow_id": WORKFLOW_ID,
                "stage": state.current_stage.value,
                "authorization_action": action.model_dump(mode="json"),
            },
            opened_at=NOW,
            user_id=USER_ID,
            thread_id=f"thread-task7-authorization-{kind}",
            checkpoint_ns="root",
        )
        await repository.commit_turn(
            claim,
            VideoTurnCommit(
                decision=ActionDecision(
                    action=AgentAction.CONTINUE_WORKFLOW,
                    intent=AgentIntent.VIDEO,
                    target_workflow_id=WORKFLOW_ID,
                    target_stage=state.current_stage.value,
                    target_artifact_ref=state.active_plan_artifact_ref,
                    confidence=1,
                    requires_confirmation=True,
                    patch={},
                    reason_code="authorization_required",
                    idempotency_key=f"task7:authorization:{kind}:{near_quota}",
                ),
                turn_status=TurnStatus.WAITING_USER,
                expected_workflow_version=1,
                open_interrupt=interrupt,
                occurred_at=NOW,
            ),
        )
        response_id = UUID(
            "00000000-0000-4000-8000-000000001331"
            if near_quota
            else "00000000-0000-4000-8000-000000001330"
        )
        request = InterruptResponseRequest(
            client_response_id=response_id,
            value={
                "content": "确认继续",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [state.active_plan_artifact_ref],
                "explicit_action": action,
            },
        )
        message = PixelFlowConversationMessageRecord(
            message_id=conversation_message_id(CONVERSATION_ID, response_id),
            conversation_id=CONVERSATION_ID,
            user_id=USER_ID,
            role="user",
            content=request.value.content,
            payload={
                "client_message_id": str(response_id),
                "interrupt_id": interrupt.interrupt_id,
                "value": request.value.model_dump(mode="json"),
                "explicit_action": action.model_dump(mode="json"),
            },
            created_at=NOW.isoformat(),
        )

        if near_quota:
            with pytest.raises(AgentRuntimeQuotaResumeStaleError):
                await repository.register_interrupt_response(
                    USER_ID,
                    CONVERSATION_ID,
                    interrupt.interrupt_id,
                    request=request,
                    message=message,
                    responded_at=NOW,
                )
            unchanged = await repository.get_interrupt(USER_ID, interrupt.interrupt_id)
            assert unchanged is not None
            assert unchanged.status == "open"
            assert await store.list_conversation_messages(
                CONVERSATION_ID,
                user_id=USER_ID,
            ) == []
        else:
            registration = await repository.register_interrupt_response(
                USER_ID,
                CONVERSATION_ID,
                interrupt.interrupt_id,
                request=request,
                message=message,
                responded_at=NOW,
            )
            assert registration.created is True
            assert registration.interrupt.status == "responded"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_operation_completion_opens_real_graph_interrupt_and_resumes_original_turn(
    kind: RepositoryKind,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成事务、Graph checkpoint 和公开响应必须共同闭合同一原 Turn。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": f"provider-scripted-{index}",
                "status": "succeeded",
                "result": {
                    "video_url": f"https://videos.example.com/scene-{index}.mp4",
                    "raw": {},
                },
            }
            for index in range(1, 4)
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task13-completion-graph-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        reviewed = _reviewed_scene_package_state()
        await _commit_seed_state(repository, store, reviewed)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        vault = TransientCredentialVault()
        handler = VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=vault,
            operation_port=operations,
            clock=clock,
        )
        checkpointer = InMemorySaver()
        graph = make_agent_runtime_graph(
            registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
            checkpointer=checkpointer,
        )
        executor = SupervisorTurnExecutor(
            repository=repository,
            task_store=store,
            decision_service=_ExplicitVideoDecisionService(),
            graph=graph,
            credential_vault=vault,
            clock=clock.now,
            worker_id=f"task13-graph-{kind}",
            heartbeat_interval_seconds=0.01,
            scan_interval_seconds=0.01,
        )
        original_turn_id = "turn-task13-operation-action"
        client_input_id = UUID("00000000-0000-4000-8000-000000001301")
        artifact_ref = reviewed.scene_package_artifact_ref
        explicit = ExplicitActionSignal(
            action=AgentAction.CONTINUE_WORKFLOW,
            intent=AgentIntent.VIDEO,
            workflow_id=WORKFLOW_ID,
            stage=reviewed.current_stage.value,
            artifact_ref=artifact_ref,
            patch={},
        )
        turn = TurnRecord(
            turn_id=original_turn_id,
            conversation_id=CONVERSATION_ID,
            client_input_id=client_input_id,
            status=TurnStatus.ACCEPTED,
            target_workflow_id=WORKFLOW_ID,
            decision=None,
            expected_context_version=0,
            created_at=clock.now(),
        )
        await store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id=conversation_message_id(CONVERSATION_ID, client_input_id),
                conversation_id=CONVERSATION_ID,
                user_id=USER_ID,
                role="user",
                content="开始生成场景视频",
                payload={
                    "client_message_id": str(client_input_id),
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [artifact_ref],
                    "explicit_action": explicit.model_dump(mode="json"),
                },
                created_at=clock.now().isoformat(),
            )
        )
        await repository.enqueue_turn_for_execution(USER_ID, turn, now=clock.now())
        try:
            await executor.notify_turn(
                SupervisorTurnScope(
                    user_id=USER_ID,
                    conversation_id=CONVERSATION_ID,
                    turn_id=original_turn_id,
                ),
                credential=None,
            )
            await executor.wait_idle()
            assert provider.start_calls == 0

            authorization = await repository.get_open_interrupt(
                USER_ID,
                CONVERSATION_ID,
            )
            assert authorization is not None
            assert authorization.kind == "authorization_required"
            assert authorization.turn_id == original_turn_id
            authorization_document = authorization.model_dump(mode="json")
            authorization_action = ExplicitActionSignal.model_validate(
                authorization_document["payload"]["authorization_action"]
            )
            assert authorization_action == explicit
            assert not any(
                marker in json.dumps(
                    authorization_document,
                    ensure_ascii=False,
                ).lower()
                for marker in ("bearer ", "authorization", "token", "credential")
                if marker != "authorization"
            )

            await executor.aclose()
            executor = SupervisorTurnExecutor(
                repository=repository,
                task_store=store,
                decision_service=_ExplicitVideoDecisionService(),
                graph=graph,
                credential_vault=vault,
                clock=clock.now,
                worker_id=f"task13-graph-restarted-{kind}",
                heartbeat_interval_seconds=0.01,
                scan_interval_seconds=0.01,
            )
            authorization_response_id = UUID(
                "00000000-0000-4000-8000-000000001300"
            )
            authorization_request = InterruptResponseRequest(
                client_response_id=authorization_response_id,
                value={
                    "content": "继续生成场景视频",
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [artifact_ref],
                    "explicit_action": authorization_action,
                },
            )
            authorization_response = await repository.register_interrupt_response(
                USER_ID,
                CONVERSATION_ID,
                authorization.interrupt_id,
                request=authorization_request,
                message=PixelFlowConversationMessageRecord(
                    message_id=conversation_message_id(
                        CONVERSATION_ID,
                        authorization_response_id,
                    ),
                    conversation_id=CONVERSATION_ID,
                    user_id=USER_ID,
                    role="user",
                    content=authorization_request.value.content,
                    payload={
                        "client_message_id": str(authorization_response_id),
                        "interrupt_id": authorization.interrupt_id,
                        "value": authorization_request.value.model_dump(mode="json"),
                        "explicit_action": authorization_action.model_dump(
                            mode="json"
                        ),
                    },
                    created_at=clock.now().isoformat(),
                ),
                responded_at=clock.now(),
            )
            await executor.notify_interrupt(
                authorization_response.interrupt,
                credential=TransientTurnCredential(FAKE_AUTHORIZATION),
            )
            await executor.wait_idle()
            assert provider.start_calls == 3

            original_completion_commit = repository.commit_operation_completion
            failed_after_checkpoint = False

            async def fail_first_actionable_completion(*args, **kwargs):
                nonlocal failed_after_checkpoint
                if (
                    kwargs.get("open_interrupt") is not None
                    and not failed_after_checkpoint
                ):
                    failed_after_checkpoint = True
                    raise RuntimeError("模拟 Graph pause 后、Repository 事务前退出")
                return await original_completion_commit(*args, **kwargs)

            monkeypatch.setattr(
                repository,
                "commit_operation_completion",
                fail_first_actionable_completion,
            )
            completion = VideoOperationCompletionHandler(
                repository=repository,
                operations=operations,
                clock=clock,
                graph=graph,
            )
            runtime = operations.build_recovery_runtime(
                resumer=completion,
                worker_id=f"task13-completion-graph-{kind}",
            )
            for _ in range(3):
                clock.advance(seconds=3)
                await runtime.run_once()

            assert failed_after_checkpoint is True
            assert (
                await repository.get_open_interrupt(USER_ID, CONVERSATION_ID)
                is None
            )
            checkpoint_before_retry = await graph.aget_state(
                supervisor_namespace(CONVERSATION_ID).as_runnable_config()
            )
            paused_dispatch = WorkflowDispatchResult.model_validate(
                checkpoint_before_retry.values["workflow_dispatch_result"]
            )
            assert paused_dispatch.interrupt is not None

            clock.advance(seconds=31)
            await runtime.run_once()

            opened = await repository.get_open_interrupt(USER_ID, CONVERSATION_ID)
            assert opened is not None
            assert opened.opened_at == paused_dispatch.interrupt.opened_at
            assert opened.turn_id == original_turn_id
            assert opened.workflow_id == WORKFLOW_ID
            assert opened.kind == "video_scene_video_review"
            assert opened.payload["stage"] == "scene_video_review"
            completed_envelope = await repository.get_video_state(
                USER_ID,
                WORKFLOW_ID,
            )
            assert completed_envelope is not None
            completed_state = decode_video_workflow_state(completed_envelope)
            assert (
                opened.payload["artifact_ref"]
                == completed_state.scene_videos_artifact_ref
            )
            events = await repository.list_events(USER_ID, CONVERSATION_ID)
            opened_events = [
                item
                for item in events
                if item.type.value == "interrupt.opened"
                and item.payload["interrupt"]["interrupt_id"] == opened.interrupt_id
            ]
            assert len(opened_events) == 1
            graph_snapshot = await graph.aget_state(
                supervisor_namespace(CONVERSATION_ID).as_runnable_config()
            )
            assert any(
                item.value.get("interrupt_id") == opened.interrupt_id
                for item in graph_snapshot.interrupts
            )

            turns_before = await repository.list_turns(USER_ID, CONVERSATION_ID)
            response_id = UUID("00000000-0000-4000-8000-000000001302")
            response_action = ExplicitActionSignal(
                action=AgentAction.MODIFY_WORKFLOW,
                intent=AgentIntent.VIDEO,
                workflow_id=WORKFLOW_ID,
                stage="scene_video_review",
                artifact_ref=opened.payload["artifact_ref"],
                patch={
                    "scene_id": "scene-1",
                    "scene_patch": {"storyline": "补充产品近景"},
                },
            )
            request = InterruptResponseRequest(
                client_response_id=response_id,
                value={
                    "content": "修改第一条分镜",
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [opened.payload["artifact_ref"]],
                    "explicit_action": response_action,
                },
            )
            response = await repository.register_interrupt_response(
                USER_ID,
                CONVERSATION_ID,
                opened.interrupt_id,
                request=request,
                message=PixelFlowConversationMessageRecord(
                    message_id=conversation_message_id(CONVERSATION_ID, response_id),
                    conversation_id=CONVERSATION_ID,
                    user_id=USER_ID,
                    role="user",
                    content=request.value.content,
                    payload={
                        "client_message_id": str(response_id),
                        "interrupt_id": opened.interrupt_id,
                        "value": request.value.model_dump(mode="json"),
                        "explicit_action": response_action.model_dump(mode="json"),
                    },
                    created_at=clock.now().isoformat(),
                ),
                responded_at=clock.now(),
            )
            await executor.notify_interrupt(response.interrupt)
            await executor.wait_idle()

            turns_after = await repository.list_turns(USER_ID, CONVERSATION_ID)
            assert [item.turn_id for item in turns_after] == [
                item.turn_id for item in turns_before
            ]
            assert provider.start_calls == 3
            restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
            assert restored is not None
            assert restored.last_turn_id == original_turn_id
        finally:
            await executor.aclose()


@pytest.mark.parametrize(
    (
        "completion_stage",
        "scene_generation",
        "expected_kind",
        "expected_reason_code",
    ),
    [
        (
            "scene_video_review",
            True,
            "video_scene_video_review",
            "video_scene_video_review_required",
        ),
        (
            "quality_review",
            False,
            "video_result_review",
            "video_result_review_required",
        ),
        (
            "video_review",
            False,
            "video_result_review",
            "video_result_review_required",
        ),
        (
            "delivery",
            False,
            "video_result_review",
            "video_result_review_required",
        ),
    ],
)
def test_operation_completion_interrupt_matrix_is_stable(
    completion_stage: str,
    scene_generation: bool,
    expected_kind: str,
    expected_reason_code: str,
) -> None:
    """分镜、合并、质检和剪映完成都必须映射到可恢复的有限人工中断。"""

    artifact_ref = f"artifact:task13:{completion_stage}"
    workflow = WorkflowRecord(
        workflow_id=WORKFLOW_ID,
        conversation_id=CONVERSATION_ID,
        kind=WorkflowKind.VIDEO,
        status=WorkflowStatus.AWAITING_USER,
        current_stage=completion_stage,
        stage_version=7,
        latest_artifact_refs=[artifact_ref],
        context_version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    first = _operation_completion_interrupt(
        user_id=USER_ID,
        turn_id="turn-task13-completion-matrix",
        workflow=workflow,
        workflow_version=11,
        scene_generation=scene_generation,
        opened_at=NOW,
    )
    replay = _operation_completion_interrupt(
        user_id=USER_ID,
        turn_id="turn-task13-completion-matrix",
        workflow=workflow,
        workflow_version=11,
        scene_generation=scene_generation,
        opened_at=NOW,
    )

    assert first == replay
    assert first.kind == expected_kind
    assert first.reason_code == expected_reason_code
    assert first.payload == {
        "workflow_id": WORKFLOW_ID,
        "stage": completion_stage,
        "artifact_ref": artifact_ref,
        "ui_kind": "video_result_review",
    }
    assert first.turn_id == "turn-task13-completion-matrix"
    assert first.thread_id == supervisor_namespace(
        CONVERSATION_ID
    ).thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_same_timestamp_scene_completions_publish_only_final_actionable_batch(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """
    同时间戳的中间分镜完成不得抢占最终整批卡片。
    """

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": f"provider-scripted-{index}",
                "status": "succeeded",
                "result": {
                    "video_url": f"https://videos.example.com/scene-{index}.mp4",
                    "raw": {},
                },
            }
            for index in range(1, 4)
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-scene-same-time-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id=f"task8-scene-same-time-{kind}",
        )

        clock.advance(seconds=3)
        await runtime.run_once()

        messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        assert len(messages) == 1
        message = messages[0]
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        assert artifact["type"] == "video_scene_packages"
        assert artifact["generatedSceneVideos"]["ok"] is True
        assert artifact["generatedSceneVideos"]["failed_scenes"] == []
        assert len(artifact["generatedSceneVideos"]["scene_videos"]) == 3
        assert artifact["actionLabel"] == "确认分镜视频"
        assert message.content == "视频分镜与场景素材已准备，请审核确认。"

        await runtime.run_once()
        replayed = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        assert [item.message_id for item in replayed] == [message.message_id]


@pytest.mark.asyncio
async def test_handler_failure_keeps_claim_until_expired_worker_takeover() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": "provider-scripted-1",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-1.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-2",
                "status": "running",
                "result": {"progress": 20},
            },
            {
                "job_id": "provider-scripted-3",
                "status": "running",
                "result": {"progress": 30},
            },
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-completion.db"),
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        failed_resumer = _FailOnceResumer(completion)
        first_worker = operations.build_recovery_runtime(
            resumer=failed_resumer,
            worker_id="task8-completion-worker-a",
        )

        clock.advance(seconds=3)
        await first_worker.run_once()
        unchanged = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert unchanged is not None
        assert unchanged.workflow_version == 1
        assert len(operations._operation_event_claims._claims) == 1
        owned = next(iter(operations._operation_event_claims._claims.values()))
        with pytest.raises(
            OperationConflictError,
            match="Operation 事件与 Graph 幂等键不一致",
        ):
            await completion.resume_external_job(
                workflow_namespace(CONVERSATION_ID, WORKFLOW_ID),
                completion_event=owned.claim.event,
                idempotency_key=f"{owned.claim.event.event_id}-other",
            )
        assert len(operations._operation_event_claims._claims) == 1

        clock.advance(seconds=31)
        with pytest.raises(
            OperationConflictError,
                match="Operation 事件投递租约已过期",
        ):
            await completion.resume_external_job(
                workflow_namespace(CONVERSATION_ID, WORKFLOW_ID),
                completion_event=owned.claim.event,
                idempotency_key=owned.claim.event.event_id,
            )
        assert len(operations._operation_event_claims._claims) == 0
        second_worker = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-completion-worker-b",
        )
        await second_worker.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        assert restored.workflow_version == 2
        assert len(operations._operation_event_claims._claims) == 0
        assert provider.start_calls == 3


@pytest.mark.asyncio
async def test_checkpoint_commit_survives_exit_before_dispatcher_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": "provider-scripted-1",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-1.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-2",
                "status": "running",
                "result": {"progress": 20},
            },
            {
                "job_id": "provider-scripted-3",
                "status": "running",
                "result": {"progress": 30},
            },
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-checkpoint.db"),
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        recovery_repository = operations._recovery_repository

        async def crash_before_ack(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("模拟 Graph checkpoint 后进程退出")

        monkeypatch.setattr(
            recovery_repository,
            "complete_event_delivery",
            crash_before_ack,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-checkpoint-crash",
        )

        clock.advance(seconds=3)
        await runtime.run_once()
        committed = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert committed is not None
        assert committed.workflow_version == 2
        assert len(operations._operation_event_claims._claims) == 0

        clock.advance(minutes=1)
        await runtime.run_once()
        replayed = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert replayed is not None
        assert replayed.workflow_version == 2
        assert provider.start_calls == 3
        assert provider.status_job_ids.count("provider-scripted-1") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    (
        "terminal_result",
        "expected_operation_status",
        "expected_action_label",
        "expected_retryable_scene_ids",
        "expected_non_retryable_scene_ids",
    ),
    [
        (
            {
                "job_id": "provider-scripted-1",
                "status": "failed",
                "error": "供应商内部原始失败正文",
            },
            ExternalJobStatus.FAILED,
            "查看失败原因",
            [],
            ["scene-1"],
        ),
        (
            TimeoutError("不得回显的超时正文"),
            ExternalJobStatus.TIMEOUT,
            "重新生成场景视频",
            ["scene-1"],
            [],
        ),
        (
            _HttpStatusError(404),
            ExternalJobStatus.EXPIRED,
            "重新生成场景视频",
            ["scene-1"],
            [],
        ),
    ],
)
async def test_scene_non_success_completion_becomes_safe_m11_failure(
    kind: RepositoryKind,
    terminal_result: object,
    expected_operation_status: ExternalJobStatus,
    expected_action_label: str,
    expected_retryable_scene_ids: list[str],
    expected_non_retryable_scene_ids: list[str],
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            terminal_result,
            {
                "job_id": "provider-scripted-2",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-2.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-3",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-3.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-scene-failure-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        first_job_id = state.pending_operations[0].job_id
        await _commit_seed_state(repository, store, state)
        observer = _RecordingExternalJobObserver()
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
            external_job_observer=observer,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-scene-failure",
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        operation = await operations.get_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_job_id,
        )
        assert restored is not None
        assert operation is not None
        updated = decode_video_workflow_state(restored)
        expected_retryable = expected_operation_status in {
            ExternalJobStatus.TIMEOUT,
            ExternalJobStatus.EXPIRED,
        }
        assert operation.status is expected_operation_status
        assert observer.states == [
            ProviderJobOutcome(expected_operation_status.value),
            ProviderJobOutcome.SUCCEEDED,
            ProviderJobOutcome.SUCCEEDED,
        ]
        assert [item["scene_id"] for item in updated.failed_scenes] == ["scene-1"]
        assert updated.failed_scenes[0]["retryable"] is expected_retryable
        assert "原始" not in json.dumps(updated.failed_scenes, ensure_ascii=False)
        assert (
            await repository.list_pending_operation_completions(
                now=clock.now(),
                limit=100,
            )
            == []
        )
        expected_web_retry = not expected_non_retryable_scene_ids
        required_fields = {
            "title",
            "description",
            "actionLabel",
            "generatedSceneVideos",
            "videoScenePackageEditedSceneIds",
        }
        if expected_web_retry:
            required_fields.add("videoScenePackages")
        message = await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="video_result",
            required_fields=required_fields,
        )
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        assert artifact["generatedSceneVideos"]["ok"] is False
        assert len(artifact["generatedSceneVideos"]["scene_videos"]) == 2
        assert len(artifact["generatedSceneVideos"]["failed_scenes"]) == 1
        assert artifact["actionLabel"] == expected_action_label
        assert artifact["retryableSceneIds"] == expected_retryable_scene_ids
        assert artifact["nonRetryableSceneIds"] == expected_non_retryable_scene_ids
        assert artifact["generatedSceneVideos"]["failed_scenes"][0]["retryable"] is expected_retryable
        assert _web_exposes_scene_retry_action(artifact) is expected_web_retry
        if not expected_web_retry:
            assert "videoScenePackages" not in artifact
        assert "已生成" not in message.content
        assert "可下载" not in message.content
        assert "请确认" not in message.content
        assert "未完成" in message.content
        if expected_retryable:
            assert "重试" in artifact["description"]
            assert "scene-1" in message.content
        else:
            assert "修改" in artifact["description"]
            assert "重试" not in artifact["actionLabel"]
            assert "scene-1" in message.content
        if expected_retryable:
            workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
            assert workflow is not None
            retry_command = explicit_stage_command(
                workflow,
                action=AgentAction.RETRY_FAILED,
                patch={"scene_ids": ["scene-1"]},
                suffix="retry-timeout-scene",
            )
            waiting = await handler_without_credential(
                repository=repository,
                operations=operations,
                clock=clock,
            ).dispatch(retry_command)
            assert waiting.interrupt is not None
            assert waiting.interrupt.reason_code == "authorization_required"
            assert provider.start_calls == 3

            vault = TransientCredentialVault()
            vault.put(
                retry_command.turn_id,
                TransientTurnCredential(FAKE_AUTHORIZATION),
            )
            retried = await VideoLiveWorkflowHandler(
                repository=repository,
                capabilities=_UnusedCapabilities(),
                credential_provider=vault,
                operation_port=operations,
                clock=clock,
            ).dispatch(retry_command)
            retry_state = decode_video_workflow_state(retried.state)
            assert retry_state.pending_operations[0].attempt == 2
            retry_job_id = retry_state.pending_operations[0].job_id
            assert provider.start_calls == 4

            duplicate_vault = TransientCredentialVault()
            duplicate_vault.put(
                retry_command.turn_id,
                TransientTurnCredential(FAKE_AUTHORIZATION),
            )
            duplicate = await VideoLiveWorkflowHandler(
                repository=repository,
                capabilities=_UnusedCapabilities(),
                credential_provider=duplicate_vault,
                operation_port=operations,
                clock=clock,
            ).dispatch(retry_command)
            duplicate_state = decode_video_workflow_state(duplicate.state)
            assert duplicate_state.pending_operations[0].job_id == retry_job_id
            assert duplicate_state.pending_operations[0].attempt == 2
            assert provider.start_calls == 4
            unchanged = await operations.get_operation(
                user_id=USER_ID,
                conversation_id=CONVERSATION_ID,
                job_id=first_job_id,
            )
            assert unchanged is not None
            assert unchanged.status is expected_operation_status
            assert provider.status_job_ids.count("provider-scripted-1") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_mixed_scene_failures_publish_view_only_action_and_retryable_ids(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    """混合失败不能提供 Handler 无法执行的整批重试动作。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": "provider-scripted-1",
                "status": "failed",
                "error": "供应商内部原始失败正文",
            },
            TimeoutError("不得回显的超时正文"),
            {
                "job_id": "provider-scripted-3",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/scene-3.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-scene-mixed-failure-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id=f"task8-scene-mixed-failure-{kind}",
        )

        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        assert [item["scene_id"] for item in updated.failed_scenes] == [
            "scene-1",
            "scene-2",
        ]
        message = await _assert_new_completion_projection(
            repository,
            before_message_ids=set(),
            expected_type="video_result",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "generatedSceneVideos",
                "videoScenePackageEditedSceneIds",
                "retryableSceneIds",
                "nonRetryableSceneIds",
            },
        )
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        failures = artifact["generatedSceneVideos"]["failed_scenes"]
        assert [(item["scene_id"], item["retryable"]) for item in failures] == [
            ("scene-1", False),
            ("scene-2", True),
        ]
        assert artifact["retryableSceneIds"] == ["scene-2"]
        assert artifact["nonRetryableSceneIds"] == ["scene-1"]
        assert artifact["actionLabel"] == "查看失败原因"
        assert "videoScenePackages" not in artifact
        assert _web_exposes_scene_retry_action(artifact) is False
        assert "scene-1" in artifact["description"]
        assert "scene-2" in artifact["description"]
        assert "scene-1" in message.content
        assert "scene-2" in message.content
        assert "整批重试" not in message.content

        await runtime.run_once()
        replayed = await repository.list_projection_messages(USER_ID, CONVERSATION_ID)
        assert [item.message_id for item in replayed] == [message.message_id]


@pytest.mark.asyncio
async def test_merge_paid_stage_requires_credential_and_starts_m06_once() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            {
                "job_id": f"provider-scripted-{index}",
                "status": "succeeded",
                "result": {
                    "video_url": f"https://videos.example.com/scene-{index}.mp4",
                    "raw": {},
                },
            }
            for index in range(1, 4)
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-merge-start.db"),
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        state = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, state)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-complete-scenes",
        )
        clock.advance(seconds=3)
        await runtime.run_once()
        workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
        assert workflow is not None
        command = paid_stage_command(workflow)

        waiting = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(command)
        assert waiting.turn_status is TurnStatus.WAITING_USER
        assert waiting.interrupt is not None
        assert waiting.interrupt.reason_code == "authorization_required"
        assert provider.start_calls == 3

        vault = TransientCredentialVault()
        vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(command)

        assert started.workflow.current_stage == "merge_video"
        assert provider.start_calls == 4
        snapshot = await operations.safe_persistence_snapshot(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        )
        merge = next(item for item in snapshot["operations"] if item["stage"] == "merge_video")
        assert merge["status"] == "polling"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_merge_completion_updates_postproduction_and_acks_once(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": f"https://videos.example.com/scene-{index}.mp4",
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-merge-completion-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        generation = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, generation)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-merge-completion",
        )
        clock.advance(seconds=3)
        await runtime.run_once()
        generation_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert generation_envelope is not None
        completed_generation = decode_video_workflow_state(generation_envelope)
        port = operations.bind(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        )
        post_service = VideoPostProductionWorkflowService(port)
        post = await post_service.start_merge(
            completed_generation,
            operation_port=port,
            now=clock.now(),
        )
        pending = post.pending_operation
        assert pending is not None
        await operations.start(
            operations.start_request_from_claim(
                user_id=USER_ID,
                conversation_id=CONVERSATION_ID,
                job=pending,
                stage_version=post.stage_version,
                provider_request=post.merge_request,
            ),
            credential=TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        post = await post_service.resume(
            post,
            operation_port=port,
            now=clock.now(),
        )
        await _commit_next_state(
            repository,
            post,
            expected_workflow_version=generation_envelope.workflow_version,
            now=clock.now(),
            index=2,
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        assert updated.merged_video["video_url"] == "https://videos.example.com/merged.mp4"
        assert updated.current_stage.value == "video_review"
        assert restored.workflow_version == generation_envelope.workflow_version + 2
        assert provider.status_job_ids.count("provider-scripted-4") == 1
        await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="video_result",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "videoScenePackages",
                "generatedSceneVideos",
                "mergedVideo",
                "videoAccepted",
            },
        )


@pytest.mark.asyncio
async def test_non_paid_video_finish_uses_scoped_live_operation_port() -> None:
    """视频人工结束只校验可信终态，不应把未绑定 bridge 传给 M11。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": (f"https://videos.example.com/scene-{index}.mp4"),
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-video-finish.db"),
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        _, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        finish_command = explicit_stage_command(
            workflow,
            action=AgentAction.CONTINUE_WORKFLOW,
            patch={},
            suffix="finish-video",
        )

        finished = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(finish_command)

        delivery = decode_video_workflow_state(finished.state)
        assert delivery.postproduction_state.finalized_by_user is True
        assert provider.start_calls == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_result", "expected_status", "expected_retryable"),
    [
        (
            {
                "job_id": "provider-scripted-4",
                "status": "failed",
                "error": "合并供应商原始失败正文不得回显",
            },
            ExternalJobStatus.FAILED,
            False,
        ),
        (
            TimeoutError("合并供应商原始超时正文不得回显"),
            ExternalJobStatus.TIMEOUT,
            True,
        ),
        (_HttpStatusError(404), ExternalJobStatus.EXPIRED, True),
    ],
)
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_merge_non_success_is_safe_and_timeout_retry_starts_attempt_two(
    kind: RepositoryKind,
    terminal_result: object,
    expected_status: ExternalJobStatus,
    expected_retryable: bool,
    tmp_path: Path,
) -> None:
    """合并失败可按合同显式重试，404 必须创建新 attempt。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": (f"https://videos.example.com/scene-{index}.mp4"),
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            terminal_result,
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-merge-non-success-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        generation = await _start_generation_operations(operations)
        await _commit_seed_state(repository, store, generation)
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-merge-timeout",
        )
        clock.advance(seconds=3)
        await runtime.run_once()
        generation_envelope = await repository.get_video_state(
            USER_ID,
            WORKFLOW_ID,
        )
        workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
        assert generation_envelope is not None
        assert workflow is not None

        start_command = paid_stage_command(workflow)
        start_vault = TransientCredentialVault()
        start_vault.put(
            start_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=start_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(start_command)
        started_state = decode_video_workflow_state(started.state)
        first_merge_job_id = started_state.pending_operation.job_id
        await _commit_next_state(
            repository,
            started_state,
            expected_workflow_version=generation_envelope.workflow_version,
            now=clock.now(),
            index=21,
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()
        failed_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        failed_workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
        first_operation = await operations.get_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_merge_job_id,
        )
        assert failed_envelope is not None
        assert failed_workflow is not None
        assert first_operation is not None
        failed = decode_video_workflow_state(failed_envelope)
        assert first_operation.status is expected_status
        assert failed.merge_error["retryable"] is expected_retryable
        assert "原始" not in json.dumps(failed.merge_error, ensure_ascii=False)
        message = await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="video_result",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "videoScenePackages",
                "generatedSceneVideos",
                "mergedVideo",
                "videoAccepted",
            },
        )
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        assert artifact["mergedVideo"]["ok"] is False
        assert artifact["mergedVideo"]["merged_video_url"] is None
        assert artifact["mergedVideo"]["raw"] == {}
        assert "已生成" not in message.content
        assert "可下载" not in message.content
        assert "请确认" not in message.content
        assert "未完成" in message.content
        if not expected_retryable:
            return

        retry_command = explicit_stage_command(
            failed_workflow,
            action=AgentAction.RETRY_FAILED,
            patch={},
            suffix=f"retry-merge-{expected_status.value}",
        )
        waiting = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(retry_command)
        assert waiting.interrupt is not None
        assert waiting.interrupt.reason_code == "authorization_required"
        assert provider.start_calls == 4

        retry_vault = TransientCredentialVault()
        retry_vault.put(
            retry_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        retried = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=retry_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(retry_command)
        retry_state = decode_video_workflow_state(retried.state)
        assert retry_state.pending_operation.attempt == 2
        assert retry_state.pending_operation.job_id != first_merge_job_id
        retry_job_id = retry_state.pending_operation.job_id
        assert provider.start_calls == 5

        duplicate_vault = TransientCredentialVault()
        duplicate_vault.put(
            retry_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        duplicate = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=duplicate_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(retry_command)
        duplicate_state = decode_video_workflow_state(duplicate.state)
        assert duplicate_state.pending_operation.job_id == retry_job_id
        assert duplicate_state.pending_operation.attempt == 2
        assert provider.start_calls == 5
        unchanged = await operations.get_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_merge_job_id,
        )
        assert unchanged is not None
        assert unchanged.status is expected_status
        assert provider.status_job_ids.count("provider-scripted-4") == 1


@pytest.mark.asyncio
async def test_quality_paid_stage_requires_credential_and_starts_m06_once() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": f"https://videos.example.com/scene-{index}.mp4",
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-quality-start.db"),
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        _, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        command = quality_stage_command(workflow)

        waiting = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(command)
        assert waiting.turn_status is TurnStatus.WAITING_USER
        assert waiting.interrupt is not None
        assert waiting.interrupt.reason_code == "authorization_required"
        assert provider.start_calls == 4

        vault = TransientCredentialVault()
        vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(command)

        assert started.workflow.current_stage == "quality_review"
        assert provider.start_calls == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("quality_passed", [True, False])
async def test_quality_completion_updates_m11_review_and_acks_once(
    kind: RepositoryKind,
    quality_passed: bool,
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": f"https://videos.example.com/scene-{index}.mp4",
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-5",
                "status": "succeeded",
                "result": {
                    "passed": quality_passed,
                    "summary_markdown": (
                        "质检通过。" if quality_passed else "第二镜需要调整。"
                    ),
                    "quality_report_markdown": (
                        "画面与节奏符合要求。"
                        if quality_passed
                        else "第二镜商品露出不足。"
                    ),
                    "issues": (
                        []
                        if quality_passed
                        else [{"scene_id": "scene-2", "message": "商品露出不足"}]
                    ),
                    "affected_scene_ids": [] if quality_passed else ["scene-2"],
                    "revision_prompt": "" if quality_passed else "强化第二镜商品露出",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-quality-completion-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        merged_envelope, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        command = quality_stage_command(workflow)
        vault = TransientCredentialVault()
        vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(command)
        started_state = decode_video_workflow_state(started.state)
        await _commit_next_state(
            repository,
            started_state,
            expected_workflow_version=merged_envelope.workflow_version,
            now=clock.now(),
            index=3,
        )
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-quality-completion",
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        assert updated.current_stage.value == "video_review"
        assert updated.quality_review["passed"] is quality_passed
        assert provider.status_job_ids.count("provider-scripted-5") == 1
        message = await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="video_quality_review",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "videoQualityReview",
                "videoRevisionFeedback",
                "videoScenePackages",
                "generatedSceneVideos",
                "mergedVideo",
            },
        )
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        review = artifact["videoQualityReview"]
        assert review["ok"] is True
        assert review["passed"] is quality_passed
        assert review["endpoint"] == "/api/creative/video_quality_review"
        assert review["task_id"] == "provider-scripted-5"
        assert review["score"] == 0
        assert review["check_results"] == []
        assert review["issues"] == (
            []
            if quality_passed
            else [{"scene_id": "scene-2", "message": "商品露出不足"}]
        )
        assert review["affected_scene_ids"] == (
            [] if quality_passed else ["scene-2"]
        )
        assert review["raw"] == {}
        if not quality_passed:
            workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
            assert workflow is not None
            command = explicit_stage_command(
                workflow,
                action=AgentAction.MODIFY_WORKFLOW,
                patch={
                    "scene_patches": {
                        "scene-2": {"narration": "强化第二镜商品功能旁白"}
                    }
                },
                suffix="quality-scoped-scene-revision",
            )
            vault = TransientCredentialVault()
            vault.put(
                command.turn_id,
                TransientTurnCredential(FAKE_AUTHORIZATION),
            )
            handler = VideoLiveWorkflowHandler(
                repository=repository,
                capabilities=_UnusedCapabilities(),
                credential_provider=vault,
                operation_port=operations,
                clock=clock,
            )
            starts_before_revision = provider.start_calls

            revised = await handler.dispatch(command)
            revised_state = decode_video_workflow_state(revised.state)

            assert provider.start_calls == starts_before_revision + 1
            assert [
                item["scene_id"] for item in revised_state.generation_requests
            ] == ["scene-2"]
            assert [
                item.stage for item in revised_state.pending_operations
            ] == ["generate_scene_video:scene-2"]
            assert FAKE_AUTHORIZATION not in json.dumps(
                revised.state.model_dump(mode="json"),
                ensure_ascii=False,
            )

            vault.put(
                command.turn_id,
                TransientTurnCredential(FAKE_AUTHORIZATION),
            )
            replayed = await handler.dispatch(command)
            assert replayed.workflow == revised.workflow
            assert provider.start_calls == starts_before_revision + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_result", "expected_status", "expected_retryable"),
    [
        (
            {
                "job_id": "provider-scripted-5",
                "status": "failed",
                "error": "质检供应商原始失败正文不得回显",
            },
            ExternalJobStatus.FAILED,
            False,
        ),
        (
            TimeoutError("质检供应商原始超时正文不得回显"),
            ExternalJobStatus.TIMEOUT,
            True,
        ),
        (_HttpStatusError(404), ExternalJobStatus.EXPIRED, True),
    ],
)
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_quality_non_success_is_safe_and_timeout_retry_starts_attempt_two(
    kind: RepositoryKind,
    terminal_result: object,
    expected_status: ExternalJobStatus,
    expected_retryable: bool,
    tmp_path: Path,
) -> None:
    """质检失败可按合同显式重试，404 必须保留反馈并新建 attempt。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": (f"https://videos.example.com/scene-{index}.mp4"),
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
            terminal_result,
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-quality-non-success-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        merged_envelope, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        quality_command = quality_stage_command(workflow)
        start_vault = TransientCredentialVault()
        start_vault.put(
            quality_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=start_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(quality_command)
        started_state = decode_video_workflow_state(started.state)
        assert started_state.pending_operation is not None
        first_quality_job_id = started_state.pending_operation.job_id
        first_feedback = started_state.quality_feedback
        await _commit_next_state(
            repository,
            started_state,
            expected_workflow_version=merged_envelope.workflow_version,
            now=clock.now(),
            index=22,
        )
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-quality-timeout",
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()
        failed_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        failed_workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
        first_operation = await operations.get_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_quality_job_id,
        )
        assert failed_envelope is not None
        assert failed_workflow is not None
        assert first_operation is not None
        failed = decode_video_workflow_state(failed_envelope)
        assert first_operation.status is expected_status
        assert failed.quality_review["retryable"] is expected_retryable
        assert "原始" not in json.dumps(failed.quality_review, ensure_ascii=False)
        message = await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="video_quality_review",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "videoQualityReview",
                "videoRevisionFeedback",
                "videoScenePackages",
                "generatedSceneVideos",
                "mergedVideo",
            },
        )
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        review = artifact["videoQualityReview"]
        assert artifact["actionLabel"] == (
            "重新质检" if expected_retryable else "查看失败原因"
        )
        assert {
            "issues",
            "affected_scene_ids",
            "passed",
            "summary_markdown",
            "quality_report_markdown",
            "revision_prompt",
            "endpoint",
            "task_id",
            "score",
            "check_results",
            "error",
            "message",
            "raw",
        }.issubset(review)
        assert review["issues"] == []
        assert review["affected_scene_ids"] == []
        assert review["passed"] is False
        assert review["summary_markdown"] == ""
        assert review["quality_report_markdown"] == ""
        assert review["revision_prompt"] == ""
        assert review["endpoint"] == "/api/creative/video_quality_review"
        assert review["task_id"] is None
        assert review["score"] == 0
        assert review["check_results"] == []
        assert review["raw"] == {}
        assert "已生成" not in message.content
        assert "可下载" not in message.content
        assert "请确认" not in message.content
        assert "失败" in message.content or "未完成" in message.content
        if not expected_retryable:
            return

        retry_command = explicit_stage_command(
            failed_workflow,
            action=AgentAction.RETRY_FAILED,
            patch={},
            suffix=f"retry-quality-{expected_status.value}",
        )
        waiting = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(retry_command)
        assert waiting.interrupt is not None
        assert waiting.interrupt.reason_code == "authorization_required"
        assert provider.start_calls == 5

        retry_vault = TransientCredentialVault()
        retry_vault.put(
            retry_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        retried = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=retry_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(retry_command)
        retry_state = decode_video_workflow_state(retried.state)
        assert retry_state.pending_operation is not None
        assert retry_state.pending_operation.attempt == 2
        assert retry_state.pending_operation.job_id != first_quality_job_id
        retry_job_id = retry_state.pending_operation.job_id
        assert retry_state.quality_feedback == first_feedback
        assert provider.start_calls == 6

        duplicate_vault = TransientCredentialVault()
        duplicate_vault.put(
            retry_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        duplicate = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=duplicate_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(retry_command)
        duplicate_state = decode_video_workflow_state(duplicate.state)
        assert duplicate_state.pending_operation is not None
        assert duplicate_state.pending_operation.job_id == retry_job_id
        assert duplicate_state.pending_operation.attempt == 2
        assert duplicate_state.quality_feedback == first_feedback
        assert provider.start_calls == 6
        unchanged = await operations.get_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_quality_job_id,
        )
        assert unchanged is not None
        assert unchanged.status is expected_status
        assert provider.status_job_ids.count("provider-scripted-5") == 1


@pytest.mark.asyncio
async def test_jianying_paid_stage_requires_credential_and_starts_m06_once() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": f"https://videos.example.com/scene-{index}.mp4",
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-jianying-start.db"),
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        _, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        command = jianying_stage_command(workflow)

        waiting = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(command)
        assert waiting.turn_status is TurnStatus.WAITING_USER
        assert waiting.interrupt is not None
        assert waiting.interrupt.reason_code == "authorization_required"
        assert provider.start_calls == 4

        vault = TransientCredentialVault()
        vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(command)

        delivery = decode_video_workflow_state(started.state)
        assert delivery.pending_operation is not None
        assert delivery.pending_operation.stage == "jianying_draft"
        assert delivery.pending_operation.status is ExternalJobStatus.POLLING
        assert provider.start_calls == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_jianying_completion_updates_delivery_record_and_acks_once(
    kind: RepositoryKind,
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": f"https://videos.example.com/scene-{index}.mp4",
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
            {
                "job_id": "provider-scripted-5",
                "status": "succeeded",
                "result": {
                    "download_url": "https://downloads.example.com/draft.zip",
                    "file_name": "task8-draft.zip",
                    "message": "草稿生成成功。",
                },
            },
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-jianying-completion-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        merged_envelope, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        command = jianying_stage_command(workflow)
        vault = TransientCredentialVault()
        vault.put(command.turn_id, TransientTurnCredential(FAKE_AUTHORIZATION))
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(command)
        started_delivery = decode_video_workflow_state(started.state)
        assert started_delivery.pending_operation is not None
        jianying_job_id = started_delivery.pending_operation.job_id
        await _commit_next_state(
            repository,
            started_delivery,
            expected_workflow_version=merged_envelope.workflow_version,
            now=clock.now(),
            index=4,
        )
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-jianying-completion",
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        version_id = updated.current_storyboard_version_id
        record = updated.jianying_draft_records[version_id]
        assert record["status"] == "succeeded"
        assert record["download_url"] == "https://downloads.example.com/draft.zip"
        assert provider.status_job_ids.count("provider-scripted-5") == 1
        trusted = await operations.bind(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
        ).get_video_operation_terminal_claim(job_id=jianying_job_id)
        assert trusted is not None
        assert trusted.result_type == "jianying_succeeded"
        await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="jianying_draft",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "jianyingDraft",
                "pendingJianyingDraftJob",
                "jianyingDraftSceneCount",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_result", "expected_status", "expected_record_status"),
    [
        (
            {
                "job_id": "provider-scripted-5",
                "status": "failed",
                "error": "剪映供应商原始失败正文不得回显",
            },
            ExternalJobStatus.FAILED,
            "failed",
        ),
        (
            TimeoutError("剪映供应商原始超时正文不得回显"),
            ExternalJobStatus.TIMEOUT,
            "timeout",
        ),
        (_HttpStatusError(404), ExternalJobStatus.EXPIRED, "failed"),
    ],
)
@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_jianying_non_success_is_safe_and_retry_starts_attempt_two(
    kind: RepositoryKind,
    terminal_result: object,
    expected_status: ExternalJobStatus,
    expected_record_status: str,
    tmp_path: Path,
) -> None:
    """剪映非成功终态保存安全历史，显式重试复用版本并创建新 attempt。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": (f"https://videos.example.com/scene-{index}.mp4"),
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            {
                "job_id": "provider-scripted-4",
                "status": "succeeded",
                "result": {
                    "video_url": "https://videos.example.com/merged.mp4",
                    "raw": {},
                },
            },
            terminal_result,
        ]
    )
    async with _video_repository(
        kind,
        tmp_path / f"task8-jianying-non-success-{kind}.db",
    ) as (repository, store):
        await _seed_conversation(store)
        operations = build_live_operations(
            provider,
            clock=clock,
            repository=repository,
        )
        merged_envelope, workflow = await _complete_scenes_and_merge(
            repository,
            store,
            operations,
            clock,
        )
        start_command = jianying_stage_command(workflow)
        start_vault = TransientCredentialVault()
        start_vault.put(
            start_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        started = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=start_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(start_command)
        started_state = decode_video_workflow_state(started.state)
        assert started_state.pending_operation is not None
        first_jianying_job_id = started_state.pending_operation.job_id
        version_id = started_state.current_storyboard_version_id
        await _commit_next_state(
            repository,
            started_state,
            expected_workflow_version=merged_envelope.workflow_version,
            now=clock.now(),
            index=23,
        )
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-jianying-timeout",
        )

        before_messages = await repository.list_projection_messages(
            USER_ID,
            CONVERSATION_ID,
        )
        clock.advance(seconds=3)
        await runtime.run_once()
        failed_envelope = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        failed_workflow = await repository.get_workflow(USER_ID, WORKFLOW_ID)
        first_operation = await operations.get_operation(
            user_id=USER_ID,
            conversation_id=CONVERSATION_ID,
            job_id=first_jianying_job_id,
        )
        assert failed_envelope is not None
        assert failed_workflow is not None
        assert first_operation is not None
        failed = decode_video_workflow_state(failed_envelope)
        record = failed.jianying_draft_records[version_id]
        assert first_operation.status is expected_status
        assert record["status"] == expected_record_status
        assert "原始" not in json.dumps(record, ensure_ascii=False)
        message = await _assert_completion_projection_replay_stable(
            repository,
            runtime,
            before_message_ids={item.message_id for item in before_messages},
            expected_type="jianying_draft",
            required_fields={
                "title",
                "description",
                "actionLabel",
                "jianyingDraft",
                "pendingJianyingDraftJob",
                "jianyingDraftSceneCount",
            },
        )
        artifact = message.model_dump(mode="json")["payload"]["artifact"]
        assert artifact["actionLabel"] == "重新生成"
        assert artifact["jianyingDraft"]["status"] == expected_record_status
        assert artifact["jianyingDraft"]["download_url"] is None
        assert "已生成" not in message.content
        assert "可下载" not in message.content
        assert "请确认" not in message.content
        assert "未完成" in message.content

        retry_command = explicit_stage_command(
            failed_workflow,
            action=AgentAction.RETRY_FAILED,
            patch={"jianying_action": "start"},
            suffix=f"retry-jianying-{expected_status.value}",
        )
        waiting = await handler_without_credential(
            repository=repository,
            operations=operations,
            clock=clock,
        ).dispatch(retry_command)
        assert waiting.interrupt is not None
        assert waiting.interrupt.reason_code == "authorization_required"
        assert provider.start_calls == 5

        retry_vault = TransientCredentialVault()
        retry_vault.put(
            retry_command.turn_id,
            TransientTurnCredential(FAKE_AUTHORIZATION),
        )
        retried = await VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_UnusedCapabilities(),
            credential_provider=retry_vault,
            operation_port=operations,
            clock=clock,
        ).dispatch(retry_command)
        retry_state = decode_video_workflow_state(retried.state)
        assert retry_state.current_storyboard_version_id == version_id
        assert retry_state.pending_operation is not None
        assert retry_state.pending_operation.attempt == 2
        assert retry_state.pending_operation.job_id != first_jianying_job_id
        assert provider.start_calls == 6
