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
from typing import Literal
from uuid import UUID

import pytest
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentIntent,
    ExternalJobStatus,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
)
from pixelflow.agent_runtime.graph import WorkflowCommand, workflow_namespace
from pixelflow.agent_runtime.jobs import (
    OperationManualRecoveryAction,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryVideoRuntimeRepository,
    SQLVideoRuntimeRepository,
    VideoRuntimeRepository,
    VideoTurnCommit,
)
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.agent_workflows.video import (
    VideoLiveWorkflowHandler,
    VideoPlanningWorkflowService,
    VideoPostProductionWorkflowService,
    VideoSceneGenerationWorkflowService,
    VideoScenePackageWorkflowService,
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
    VideoOperationStartRequest,
)
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.intake.forms import draft_creative_directions, validate_form
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationRecord,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import PixelFlowConversationRow

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
USER_ID = "user-live-operation"
CONVERSATION_ID = "conversation-live-operation"
WORKFLOW_ID = "workflow-live-operation"
STAGE = "generate_scene_videos"
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
                STAGE: ProviderJobAdapter(provider),
                "generate_scene_video:scene-1": ProviderJobAdapter(provider),
                "generate_scene_video:scene-2": ProviderJobAdapter(provider),
                "generate_scene_video:scene-3": ProviderJobAdapter(provider),
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
) -> AsyncIterator[tuple[VideoRuntimeRepository, object]]:
    """创建同时实现 M06 与 Task4 合同的 Memory/SQLite Repository。"""

    if kind == "memory":
        store = MemoryPixelFlowTaskStore()
        yield MemoryVideoRuntimeRepository(task_store=store), store
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
                    *AGENT_RUNTIME_TABLES,
                    *AGENT_RUNTIME_SUPPORT_TABLES,
                    PixelFlowConversationRow.__table__,
                ),
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SQLPixelFlowTaskStore(session_factory)
    try:
        yield SQLVideoRuntimeRepository(
            session_factory,
            task_store=store,
        ), store
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


def _reviewed_scene_package_state():
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
    approved = planning.approve_plan(state, now=NOW)
    scene_service = VideoScenePackageWorkflowService()
    prepared = scene_service.prepare_from_approved_plan(
        approved,
        materials=[],
        now=NOW,
    )
    assets = prepared.scene_package.global_assets
    for item in assets["characters"]:
        item["three_view_images"] = [
            f"https://assets.example.com/{item['asset_id']}.png"
        ]
    for collection in ("scenes", "props"):
        for item in assets[collection]:
            item["images"] = [
                f"https://assets.example.com/{item['asset_id']}.png"
            ]
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
            blueprint["asset_requirements"][collection] = [
                replacements.get(name, name)
                for name in blueprint["asset_requirements"][collection]
            ]
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


def test_video_operation_adapter_resolver_rejects_unknown_stage() -> None:
    provider = CountingProvider()
    adapter = ProviderJobAdapter(provider)
    resolver = VideoOperationAdapterResolver({STAGE: adapter})

    assert resolver.resolve(STAGE) is adapter
    with pytest.raises(OperationConflictError, match="stage"):
        resolver.resolve("unknown-paid-stage")


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
async def test_status_402_pauses_original_job_and_manual_recovery_never_restarts() -> None:
    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            _HttpStatusError(402),
            {
                "job_id": "provider-scripted-1",
                "status": "running",
                "result": {"progress": 50},
            },
        ]
    )
    operations = build_live_operations(provider, clock=clock)
    started = await operations.start(
        request(),
        credential=TransientTurnCredential(FAKE_AUTHORIZATION),
    )
    runtime = operations.build_recovery_runtime(
        resumer=_RecordingResumer(),
        worker_id="task8-recovery-quota",
    )

    clock.advance(seconds=3)
    await runtime.run_once()
    paused = await operations.get_operation(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        job_id=started.job_id,
    )
    recovery = await runtime.recover_manually(
        USER_ID,
        CONVERSATION_ID,
        started.job_id,
    )
    await runtime.run_once()

    assert paused is not None
    assert paused.status is ExternalJobStatus.POLLING
    assert paused.next_poll_at is None
    assert recovery.action is OperationManualRecoveryAction.RESUMED_ORIGINAL_JOB
    assert provider.start_calls == 1
    assert provider.status_job_ids == ["provider-scripted-1", "provider-scripted-1"]


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
    completion = VideoOperationCompletionHandler(
        repository=repository,
        operations=operations,
        clock=clock,
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
        assert await repository.list_pending_operation_completions(
            now=clock.now(),
            limit=100,
        ) == []


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
        assert len(operations._completion_claims._claims) == 1
        owned = next(iter(operations._completion_claims._claims.values()))
        with pytest.raises(
            OperationConflictError,
            match="完成事件与 Graph 幂等键不一致",
        ):
            await completion.resume_external_job(
                workflow_namespace(CONVERSATION_ID, WORKFLOW_ID),
                completion_event=owned.claim.event,
                idempotency_key=f"{owned.claim.event.event_id}-other",
            )
        assert len(operations._completion_claims._claims) == 1

        clock.advance(seconds=31)
        with pytest.raises(
            OperationConflictError,
            match="完成事件投递租约已过期",
        ):
            await completion.resume_external_job(
                workflow_namespace(CONVERSATION_ID, WORKFLOW_ID),
                completion_event=owned.claim.event,
                idempotency_key=owned.claim.event.event_id,
            )
        assert len(operations._completion_claims._claims) == 0
        second_worker = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-completion-worker-b",
        )
        await second_worker.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        assert restored.workflow_version == 2
        assert len(operations._completion_claims._claims) == 0
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
        assert len(operations._completion_claims._claims) == 0

        clock.advance(minutes=1)
        await runtime.run_once()
        replayed = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert replayed is not None
        assert replayed.workflow_version == 2
        assert provider.start_calls == 3
        assert provider.status_job_ids.count("provider-scripted-1") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_result", "expected_operation_status"),
    [
        (
            {
                "job_id": "provider-scripted-1",
                "status": "failed",
                "error": "供应商内部原始失败正文",
            },
            ExternalJobStatus.FAILED,
        ),
        (
            TimeoutError("不得回显的超时正文"),
            ExternalJobStatus.TIMEOUT,
        ),
        (
            _HttpStatusError(404),
            ExternalJobStatus.EXPIRED,
        ),
    ],
)
async def test_scene_non_success_completion_becomes_safe_m11_failure(
    terminal_result: object,
    expected_operation_status: ExternalJobStatus,
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
        "memory",
        Path("unused-memory-scene-failure.db"),
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
        completion = VideoOperationCompletionHandler(
            repository=repository,
            operations=operations,
            clock=clock,
        )
        runtime = operations.build_recovery_runtime(
            resumer=completion,
            worker_id="task8-scene-failure",
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
        assert operation.status is expected_operation_status
        assert [item["scene_id"] for item in updated.failed_scenes] == ["scene-1"]
        assert "原始" not in json.dumps(updated.failed_scenes, ensure_ascii=False)
        assert await repository.list_pending_operation_completions(
            now=clock.now(),
            limit=100,
        ) == []
        if expected_operation_status is ExternalJobStatus.TIMEOUT:
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
            assert provider.start_calls == 4


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
        merge = next(
            item for item in snapshot["operations"] if item["stage"] == "merge_video"
        )
        assert merge["status"] == "polling"


@pytest.mark.asyncio
async def test_merge_completion_updates_postproduction_and_acks_once() -> None:
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
        Path("unused-memory-merge-completion.db"),
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

        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        assert updated.merged_video["video_url"] == "https://videos.example.com/merged.mp4"
        assert updated.current_stage.value == "video_review"
        assert restored.workflow_version == generation_envelope.workflow_version + 2
        assert provider.status_job_ids.count("provider-scripted-4") == 1


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
                        "video_url": (
                            f"https://videos.example.com/scene-{index}.mp4"
                        ),
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
        (_HttpStatusError(404), ExternalJobStatus.EXPIRED, False),
    ],
)
async def test_merge_non_success_is_safe_and_timeout_retry_starts_attempt_two(
    terminal_result: object,
    expected_status: ExternalJobStatus,
    expected_retryable: bool,
) -> None:
    """合并非成功终态均安全，只有可重试超时会启动第二次付费调用。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": (
                            f"https://videos.example.com/scene-{index}.mp4"
                        ),
                        "raw": {},
                    },
                }
                for index in range(1, 4)
            ],
            terminal_result,
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-merge-timeout.db"),
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
        assert provider.start_calls == 5


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
async def test_quality_completion_updates_m11_review_and_acks_once() -> None:
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
                    "passed": True,
                    "summary_markdown": "质检通过。",
                    "quality_report_markdown": "画面与节奏符合要求。",
                    "issues": [],
                    "affected_scene_ids": [],
                    "revision_prompt": "",
                    "raw": {},
                },
            },
        ]
    )
    async with _video_repository(
        "memory",
        Path("unused-memory-quality-completion.db"),
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

        clock.advance(seconds=3)
        await runtime.run_once()

        restored = await repository.get_video_state(USER_ID, WORKFLOW_ID)
        assert restored is not None
        updated = decode_video_workflow_state(restored)
        assert updated.current_stage.value == "video_review"
        assert updated.quality_review["passed"] is True
        assert provider.status_job_ids.count("provider-scripted-5") == 1


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
        (_HttpStatusError(404), ExternalJobStatus.EXPIRED, False),
    ],
)
async def test_quality_non_success_is_safe_and_timeout_retry_starts_attempt_two(
    terminal_result: object,
    expected_status: ExternalJobStatus,
    expected_retryable: bool,
) -> None:
    """质检非成功终态均安全，只有可重试超时保留反馈并新建 attempt。"""

    clock = _MutableClock()
    provider = ScriptedProvider(
        status_results=[
            *[
                {
                    "job_id": f"provider-scripted-{index}",
                    "status": "succeeded",
                    "result": {
                        "video_url": (
                            f"https://videos.example.com/scene-{index}.mp4"
                        ),
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
        "memory",
        Path("unused-memory-quality-timeout.db"),
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
        assert retry_state.quality_feedback == first_feedback
        assert provider.start_calls == 6


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
async def test_jianying_completion_updates_delivery_record_and_acks_once() -> None:
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
        "memory",
        Path("unused-memory-jianying-completion.db"),
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
async def test_jianying_non_success_is_safe_and_retry_starts_attempt_two(
    terminal_result: object,
    expected_status: ExternalJobStatus,
    expected_record_status: str,
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
                        "video_url": (
                            f"https://videos.example.com/scene-{index}.mp4"
                        ),
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
        "memory",
        Path("unused-memory-jianying-timeout.db"),
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
