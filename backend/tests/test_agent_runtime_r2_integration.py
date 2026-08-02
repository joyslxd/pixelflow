"""M13.2 / R2 视频 Supervisor 非付费集成合同测试。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.context import (
    ContextBudgetPolicyProvider,
    ModelContextProfile,
)
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentIntent,
    ExternalJobStatus,
    TurnRecord,
    TurnStartRequest,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
)
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    WorkflowCommand,
    compose_agent_runtime_graph,
)
from pixelflow.agent_runtime.jobs import (
    MappingProviderJobAdapterResolver,
    OperationManualRecoveryAction,
    OperationRecoveryRuntime,
    OperationStartCoordinator,
    ProviderJobAdapter,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.replay import (
    SupervisorReplayDisposition,
    SupervisorReplayRuntime,
)
from pixelflow.agent_runtime.service import (
    AgentRuntimeService,
    AgentRuntimeUnavailableError,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationRequest,
    DecisionValidationRequest,
    DeterministicResolution,
    DeterministicResolutionStatus,
    evaluate_supervisor_cases,
    load_supervisor_golden_dataset,
)
from pixelflow.agent_workflows.video import VideoPlanningWorkflowService
from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    MemoryPixelFlowTaskStore,
    PixelFlowConversationRecord,
)
from tests._router_auth_helpers import make_authed_test_app

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
USER_ID = "user-m13-r2"
CONVERSATION_ID = "conversation-m13-r2"
CLIENT_INPUT_ID = UUID("22222222-2222-4222-8222-222222222222")
MODEL_NAME = "deepseek-v4-pro"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _config(mode: str = "primary") -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        mode=mode,
        enabled_intents=("video",),
        new_conversation_rollout_percent=100,
        context_compaction_enabled=True,
    )


def _model_profile() -> ModelContextProfile:
    return ModelContextProfile(
        model_name=MODEL_NAME,
        max_context_tokens=1_000_000,
        max_output_tokens=32 * 1024,
        tokenizer_strategy="conservative_estimate",
        verified_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        source="M13.2 R2 非付费回放档案",
    )


def _start_decision() -> ActionDecision:
    return ActionDecision(
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
        target_workflow_id=None,
        target_stage=None,
        target_artifact_ref=None,
        confidence=1,
        requires_confirmation=False,
        clarification_question=None,
        patch={},
        reason_code="explicit_video_start",
        idempotency_key="decision:turn-m13-r2",
    )


def _start_state() -> dict:
    decision = _start_decision()
    resolution = DeterministicResolution(
        status=DeterministicResolutionStatus.RESOLVED,
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
        reason_code="explicit_video_start",
    )
    classification = ActionClassificationRequest(
        turn_id="turn-m13-r2",
        content="用这张参考图生成一条 30 秒竖屏商品视频",
        deterministic_resolution=resolution,
        candidates=(),
    )
    return {
        "conversation_id": CONVERSATION_ID,
        "user_id": USER_ID,
        "turn_id": "turn-m13-r2",
        "run_id": "turn-m13-r2",
        "current_input": classification.content,
        "materials": [
            {
                "type": "image",
                "url": "https://materials.example.com/product.png",
                "name": "商品参考图.png",
            }
        ],
        "reply_to_message_id": "message-brief-v1",
        "artifact_refs": ["artifact:brief:video:v1"],
        "context_version": 0,
        "workflows": {},
        "active_workflow_id": None,
        "decision": decision,
        "decision_validation_request": DecisionValidationRequest(
            decision=decision,
            classification_request=classification,
            current_candidates=(),
            allowed_global_actions=(
                AgentAction.ANSWER_ONLY,
                AgentAction.CLARIFY,
                AgentAction.START_WORKFLOW,
            ),
            expected_context_version=0,
            current_context_version=0,
        ),
    }


class _PlanningHandler:
    """用 M11 Planning Service 执行首个视频 Workflow 命令。"""

    def __init__(self) -> None:
        self.commands: list[WorkflowCommand] = []
        self.provider_calls = 0
        self.powermem_record_calls = 0

    async def dispatch(self, command: WorkflowCommand) -> WorkflowRecord:
        self.commands.append(command)
        # 这两个计数器代表真实系统中的 Provider 与 PowerMem 副作用边界；
        # Shadow 测试必须证明整个 Handler 都没有被调用。
        self.provider_calls += 1
        self.powermem_record_calls += 1
        state = VideoPlanningWorkflowService().start(
            workflow_id=command.workflow_id,
            conversation_id=command.conversation_id,
            intent="video",
            intake_context={
                "source_prompt": command.current_input,
                "materials": command.materials,
                "reply_to_message_id": command.reply_to_message_id,
                "artifact_refs": command.artifact_refs,
            },
            now=NOW,
        )
        return VideoPlanningWorkflowService().to_workflow_record(state)


class _ProviderService:
    """只返回固定 provider job ID 的非付费 Service fake。"""

    def __init__(self) -> None:
        self.start_calls: list[dict] = []

    async def start(
        self,
        request,
        *,
        authorization: str,
        idempotency_key: str,
    ):
        self.start_calls.append(
            {
                "request": dict(request),
                "authorization": authorization,
                "idempotency_key": idempotency_key,
            }
        )
        return {
            "job_id": "provider-video-job-m13-r2",
            "status": "running",
            "result": {"progress": 0},
        }

    async def status(self, provider_job_id: str):
        return {
            "job_id": provider_job_id,
            "status": "running",
            "result": {"progress": 10},
        }


class _RecordingExecutor:
    """记录 Controller 是否错误唤醒 Supervisor 执行器。"""

    def __init__(self) -> None:
        self.turn_ids: list[str] = []
        self.notified = threading.Event()

    async def notify_turn(self, scope, credential) -> None:
        if credential is not None:
            credential.discard()
        self.turn_ids.append(scope.turn_id)
        self.notified.set()


def _runtime(
    *,
    mode: str,
    handler: _PlanningHandler,
) -> SupervisorReplayRuntime:
    graph = compose_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
    ).graph
    config = _config(mode)
    return SupervisorReplayRuntime(
        config=config,
        graph=graph,
        model_name=MODEL_NAME,
        model_profiles={MODEL_NAME: _model_profile()},
        budget_policy_provider=ContextBudgetPolicyProvider(
            config.context_budget,
        ),
        clock=lambda: NOW,
    )


def test_r2_dev_profile_is_primary_video_100_percent_without_changing_prod() -> None:
    """开发测试候选进入 R2，生产仍保持已批准的 R1 assist。"""

    dev = yaml.safe_load(
        (BACKEND_ROOT / "config.dev.yml").read_text(encoding="utf-8"),
    )["pixelflow"]["agent_runtime"]
    prod = yaml.safe_load(
        (BACKEND_ROOT / "config.prod.yml").read_text(encoding="utf-8"),
    )["pixelflow"]["agent_runtime"]

    assert (dev["mode"], dev["enabled_intents"], dev["new_conversation_rollout_percent"]) == (
        "primary",
        ["video"],
        100,
    )
    assert (prod["mode"], prod["enabled_intents"], prod["new_conversation_rollout_percent"]) == (
        "assist",
        [],
        100,
    )
    for profile in (dev, prod):
        assert profile["context_budget"] == {
            "effective_context_k": 896,
            "output_reserve_k": 32,
            "safety_reserve_k": 32,
            "require_verified_model_profile": True,
        }
        assert profile["compaction_retry_backoff_seconds"] == 30


@pytest.mark.parametrize("legacy_intent", ["image", "ppt", "video_analysis", None])
def test_primary_assignment_only_gives_enabled_video_new_conversations_to_supervisor(
    legacy_intent: str | None,
) -> None:
    service = AgentRuntimeService(
        config=_config(),
        repository=MemoryCompactionQueueRepository(),
        task_store=MemoryPixelFlowTaskStore(),
        primary_execution_intents=("video",),
    )

    video_assignments = [
        service.assignment_for_new_conversation(
            {"business_field": "保留"},
            initial_intent="video",
        )
        for _ in range(32)
    ]
    legacy_assignment = service.assignment_for_new_conversation(
        {},
        initial_intent=legacy_intent,
    )

    assert {item.orchestration_mode.value for item in video_assignments} == {
        "supervisor_v1",
    }
    assert legacy_assignment.orchestration_mode.value == "frontend_v2"
    assert all(
        item.context[AGENT_RUNTIME_CONTEXT_KEY]["mode"] == "primary"
        and item.context[AGENT_RUNTIME_CONTEXT_KEY]["enabled_intents"] == ["video"]
        for item in [*video_assignments, legacy_assignment]
    )


def test_frontend_v2_turn_is_persisted_without_notifying_supervisor_executor() -> None:
    """历史 v2 对话继续写 R1 Inbox，但不能交给 Supervisor 抢占业务推进权。"""

    from app.gateway.routers import pixelflow_conversations

    user_id = UUID("00000000-0000-4000-8000-000000000142")
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    executor = _RecordingExecutor()
    service = AgentRuntimeService(
        config=_config(),
        repository=repository,
        task_store=task_store,
        turn_executor=executor,  # type: ignore[arg-type]
        primary_execution_intents=("video",),
    )
    app = make_authed_test_app(
        user_factory=lambda: User(
            email="task14-frontend-owner@example.com",
            password_hash="x",
            system_role="user",
            id=user_id,
        )
    )
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        created = client.post(
            "/agent/conversations",
            json={"title": "既有图片对话", "initial_intent": "image"},
        )
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]
        assert created.json()["orchestration_mode"] == "frontend_v2"
        started = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": "00000000-0000-4000-8000-000000001402",
                "content": "继续由既有 v2 图片流程处理",
                "materials": [],
                "expected_context_version": 0,
            },
        )

    assert started.status_code == 200
    assert started.json()["status"] == "accepted"
    assert executor.notified.wait(timeout=0.1) is False
    assert executor.turn_ids == []
    turns = asyncio.run(repository.list_turns(str(user_id), conversation_id))
    assert len(turns) == 1
    assert turns[0].status is TurnStatus.ACCEPTED


def test_missing_handler_restart_rejects_frozen_supervisor_before_turn_registration() -> None:
    """重启缺 Handler 时旧 owner 固定失败，新视频仍安全归属 v2。"""

    from app.gateway.routers import pixelflow_conversations

    user_id = UUID("00000000-0000-4000-8000-000000000143")
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()

    def make_app(service: AgentRuntimeService):
        app = make_authed_test_app(
            user_factory=lambda: User(
                email="task14-restart-owner@example.com",
                password_hash="x",
                system_role="user",
                id=user_id,
            )
        )
        app.state.pixelflow_task_store = task_store
        app.state.pixelflow_agent_runtime_service = service
        app.include_router(pixelflow_conversations.router)
        return app

    ready_service = AgentRuntimeService(
        config=_config(),
        repository=repository,
        task_store=task_store,
        primary_execution_intents=("video",),
    )
    with TestClient(make_app(ready_service)) as client:
        frozen = client.post(
            "/agent/conversations",
            json={"title": "已冻结视频对话", "initial_intent": "video"},
        )
    assert frozen.status_code == 200
    frozen_id = frozen.json()["conversation_id"]
    assert frozen.json()["orchestration_mode"] == "supervisor_v1"

    restarted_service = AgentRuntimeService(
        config=_config(),
        repository=repository,
        task_store=task_store,
    )
    with TestClient(make_app(restarted_service)) as client:
        new_video = client.post(
            "/agent/conversations",
            json={"title": "重启后新视频对话", "initial_intent": "video"},
        )
        rejected = client.post(
            f"/agent/conversations/{frozen_id}/turns/start",
            json={
                "client_input_id": "00000000-0000-4000-8000-000000001403",
                "content": "继续原视频流程",
                "materials": [],
                "expected_context_version": 0,
            },
        )
        frozen_after_restart = client.get(
            f"/agent/conversations/{frozen_id}",
        )

    assert new_video.status_code == 200
    assert new_video.json()["orchestration_mode"] == "frontend_v2"
    assert rejected.status_code == 409
    assert rejected.json() == {
        "detail": {"code": "agent_runtime_unavailable"},
    }
    assert frozen_after_restart.status_code == 200
    frozen_record = asyncio.run(
        task_store.get_conversation(frozen_id, user_id=str(user_id))
    )
    assert frozen_record is not None
    assert frozen_record.orchestration_mode == "supervisor_v1"
    assert list(asyncio.run(repository.list_turns(str(user_id), frozen_id))) == []


def test_cross_tenant_public_runtime_references_are_rejected_at_conversation_boundary() -> None:
    """conversation 归属先于 workflow、artifact 与 interrupt 引用解析。"""

    from app.gateway.routers import pixelflow_conversations

    owner_id = UUID("00000000-0000-4000-8000-000000000144")
    attacker_id = UUID("00000000-0000-4000-8000-000000000145")
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_config(),
        repository=repository,
        task_store=task_store,
        primary_execution_intents=("video",),
    )

    def make_app(user_id: UUID, email: str):
        app = make_authed_test_app(
            user_factory=lambda: User(
                email=email,
                password_hash="x",
                system_role="user",
                id=user_id,
            )
        )
        app.state.pixelflow_task_store = task_store
        app.state.pixelflow_agent_runtime_service = service
        app.include_router(pixelflow_conversations.router)
        return app

    with TestClient(make_app(owner_id, "task14-owner@example.com")) as client:
        created = client.post(
            "/agent/conversations",
            json={"title": "用户 A 视频对话", "initial_intent": "video"},
        )
    conversation_id = created.json()["conversation_id"]
    malicious_action = {
        "action": "continue_workflow",
        "intent": "video",
        "workflow_id": "workflow-user-a",
        "stage": "plan_review",
        "artifact_ref": "artifact:user-a:plan:v1",
        "patch": {"approved": True},
    }
    with TestClient(make_app(attacker_id, "task14-attacker@example.com")) as client:
        started = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": "00000000-0000-4000-8000-000000001404",
                "content": "尝试引用用户 A 的工作流和产物",
                "materials": [],
                "artifact_refs": ["artifact:user-a:plan:v1"],
                "expected_context_version": 0,
                "explicit_action": malicious_action,
            },
        )
        snapshot = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )
        interrupted = client.post(
            f"/agent/conversations/{conversation_id}/interrupts/"
            "interrupt-user-a/responses",
            json={
                "client_response_id": "00000000-0000-4000-8000-000000001405",
                "value": {
                    "content": "尝试响应用户 A 的中断",
                    "materials": [],
                    "artifact_refs": ["artifact:user-a:plan:v1"],
                    "explicit_action": malicious_action,
                },
            },
        )

    assert started.status_code == 404
    assert snapshot.status_code == 404
    assert interrupted.status_code == 404
    assert list(
        asyncio.run(repository.list_turns(str(attacker_id), conversation_id))
    ) == []


def test_primary_video_without_live_handler_keeps_v2_owner_and_r1_runtime() -> None:
    """配置获批但进程没有真实 Handler 时，业务继续由 v2 安全推进。"""

    service = AgentRuntimeService(
        config=_config(),
        repository=MemoryCompactionQueueRepository(),
        task_store=MemoryPixelFlowTaskStore(),
    )

    assignment = service.assignment_for_new_conversation(
        {},
        initial_intent="video",
    )

    assert assignment.orchestration_mode.value == "frontend_v2"
    assert assignment.context[AGENT_RUNTIME_CONTEXT_KEY]["mode"] == "primary"
    assert assignment.context[AGENT_RUNTIME_CONTEXT_KEY]["enabled_intents"] == [
        "video",
    ]
    assert (
        assignment.context[AGENT_RUNTIME_CONTEXT_KEY][
            "primary_execution_ready"
        ]
        is False
    )


def test_primary_assignment_records_live_handler_readiness() -> None:
    """Supervisor 归属必须把本会话的 live Handler 就绪事实冻结到命名空间。"""

    service = AgentRuntimeService(
        config=_config(),
        repository=MemoryCompactionQueueRepository(),
        task_store=MemoryPixelFlowTaskStore(),
        primary_execution_intents=("video",),
    )

    assignment = service.assignment_for_new_conversation(
        {},
        initial_intent="video",
    )

    assert assignment.orchestration_mode.value == "supervisor_v1"
    assert (
        assignment.context[AGENT_RUNTIME_CONTEXT_KEY][
            "primary_execution_ready"
        ]
        is True
    )


def test_conversation_router_freezes_only_video_hint_as_supervisor_owner() -> None:
    """验证真实创建路由只把获批视频提示冻结为 Supervisor 归属。"""

    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(
        user_factory=lambda: User(
            email="m13-r2-router@example.com",
            password_hash="x",
            system_role="user",
            id=UUID("00000000-0000-0000-0000-000000000132"),
        )
    )
    repository = MemoryCompactionQueueRepository()
    task_store = MemoryPixelFlowTaskStore()
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = AgentRuntimeService(
        config=_config(),
        repository=repository,
        task_store=task_store,
        primary_execution_intents=("video",),
    )
    app.include_router(pixelflow_conversations.router)

    with TestClient(app) as client:
        video = client.post(
            "/agent/conversations",
            json={"title": "视频", "initial_intent": "video"},
        )
        image = client.post(
            "/agent/conversations",
            json={"title": "图片", "initial_intent": "image"},
        )

    assert video.status_code == image.status_code == 200
    assert video.json()["orchestration_mode"] == "supervisor_v1"
    assert image.json()["orchestration_mode"] == "frontend_v2"
    assert video.json()["context"][AGENT_RUNTIME_CONTEXT_KEY] == {
        "mode": "primary",
        "enabled_intents": ["video"],
        "primary_execution_ready": True,
        "context_compaction_enabled": True,
        "context_version": 0,
    }


@pytest.mark.asyncio
async def test_video_primary_mock_e2e_keeps_attachments_and_repeated_start_increment_zero() -> None:
    """串起 M02/M05/M11 后，再用 M06 证明刷新重放不会新增 start。"""

    handler = _PlanningHandler()
    state = _start_state()
    result = await _runtime(mode="primary", handler=handler).replay(state)

    assert result.disposition is SupervisorReplayDisposition.PRIMARY
    assert result.budget_report is not None
    assert result.budget_report.effective_context_tokens == 896 * 1024
    assert result.budget_report.max_output_tokens == 32 * 1024
    assert result.budget_report.safety_reserve_tokens == 32 * 1024
    assert result.budget_report.usable_input_tokens == 832 * 1024
    assert result.output_state is not None
    workflow = next(iter(result.output_state["workflows"].values()))
    assert workflow.kind is WorkflowKind.VIDEO
    assert workflow.current_stage == "intake"
    assert len(handler.commands) == 1
    command = handler.commands[0]
    assert command.materials == state["materials"]
    assert command.reply_to_message_id == "message-brief-v1"
    assert command.artifact_refs == ["artifact:brief:video:v1"]
    command.materials[0]["url"] = "https://attacker.example.com/changed.png"
    assert state["materials"][0]["url"] == "https://materials.example.com/product.png"

    repository = MemoryCompactionQueueRepository()
    provider_service = _ProviderService()
    adapter = ProviderJobAdapter(provider_service)
    provider_request = {
        "scene_id": "scene-1",
        "prompt": "固定的非付费视频生成请求",
    }
    operation_request = build_operation_request(
        workflow_id=workflow.workflow_id,
        stage="generate_scene_video",
        stage_version=workflow.stage_version,
        attempt=1,
        provider_request=provider_request,
    )
    first_coordinator = OperationStartCoordinator(
        repository,
        adapter=adapter,
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        clock=lambda: NOW,
        job_id_factory=lambda: "job-video-m13-r2",
    )
    first = await first_coordinator.start(
        operation_request,
        provider_request=provider_request,
        authorization="Bearer non-production-test-token",
        lease_owner="worker-first",
    )
    calls_after_first = len(provider_service.start_calls)

    # 模拟刷新/进程重建：复用同一 Repository、operation 身份和 provider job。
    replayed = await OperationStartCoordinator(
        repository,
        adapter=adapter,
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        clock=lambda: NOW + timedelta(seconds=1),
    ).start(
        operation_request,
        provider_request=provider_request,
        authorization="Bearer non-production-test-token",
        lease_owner="worker-replay",
    )

    assert first.job_id == replayed.job_id == "job-video-m13-r2"
    assert first.provider_job_id == replayed.provider_job_id
    assert len(provider_service.start_calls) - calls_after_first == 0


@pytest.mark.asyncio
async def test_shadow_records_preview_but_never_enters_provider_or_powermem_boundary() -> None:
    handler = _PlanningHandler()

    result = await _runtime(mode="shadow", handler=handler).replay(
        _start_state(),
    )

    assert result.disposition is SupervisorReplayDisposition.SHADOW
    assert result.command is not None
    assert result.command.materials[0]["url"].endswith("product.png")
    assert result.output_state is None
    assert handler.commands == []
    assert handler.provider_calls == 0
    assert handler.powermem_record_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "assist"])
async def test_kill_switch_modes_do_not_enter_video_handler(mode: str) -> None:
    handler = _PlanningHandler()

    result = await _runtime(mode=mode, handler=handler).replay(_start_state())

    assert result.disposition is SupervisorReplayDisposition.DISABLED
    assert result.output_state is None
    assert handler.commands == []


def test_video_golden_conversations_keep_all_r2_actions_and_zero_billing_error() -> None:
    dataset = load_supervisor_golden_dataset(
        BACKEND_ROOT / "tests" / "fixtures" / "supervisor_golden_cases.json",
    )
    video_cases = tuple(
        case
        for case in dataset.cases
        if case.expected.intent is AgentIntent.VIDEO
    )

    report = evaluate_supervisor_cases(
        dataset_id="m13-r2-video-subset",
        cases=video_cases,
    )

    assert len(video_cases) == 16
    assert {case.expected.action for case in video_cases} == set(AgentAction)
    assert report.action_accuracy == 1
    assert report.target_accuracy == 1
    assert report.clarification_recall == 1
    assert report.billing_misexecutions == 0


@pytest.mark.asyncio
async def test_primary_video_turn_queues_during_compaction_and_recovers_with_same_attachments() -> None:
    """压缩失败退避期间只排队，30 秒边界后接管同一 Turn 和附件。"""

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation(
        {},
        initial_intent="video",
    )
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=CONVERSATION_ID,
            user_id=USER_ID,
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=1,
            context=assignment.context,
        )
    )
    first_lease = await repository.acquire_compaction_lease(
        USER_ID,
        CONVERSATION_ID,
        lease_owner="compactor-first",
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=10),
    )
    assert first_lease is not None

    response = await service.start_turn(
        user_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        request=TurnStartRequest(
            client_input_id=CLIENT_INPUT_ID,
            content="压缩期间继续使用这张商品参考图",
            materials=_start_state()["materials"],
            reply_to_message_id="message-brief-v1",
            artifact_refs=["artifact:brief:video:v1"],
            expected_context_version=0,
        ),
    )
    assert response.status == "queued"

    await repository.finish_compaction(
        USER_ID,
        CONVERSATION_ID,
        lease_owner=first_lease.lease_owner,
        lease_token=first_lease.lease_token,
        now=NOW + timedelta(seconds=1),
        claim_next=False,
        retry_not_before=NOW + timedelta(seconds=31),
    )
    assert await repository.acquire_compaction_lease(
        USER_ID,
        CONVERSATION_ID,
        lease_owner="compactor-too-early",
        now=NOW + timedelta(seconds=30),
        lease_expires_at=NOW + timedelta(seconds=40),
    ) is None
    recovered_lease = await repository.acquire_compaction_lease(
        USER_ID,
        CONVERSATION_ID,
        lease_owner="compactor-recovery",
        now=NOW + timedelta(seconds=31),
        lease_expires_at=NOW + timedelta(seconds=41),
    )
    assert recovered_lease is not None
    claimed = await repository.finish_compaction(
        USER_ID,
        CONVERSATION_ID,
        lease_owner=recovered_lease.lease_owner,
        lease_token=recovered_lease.lease_token,
        now=NOW + timedelta(seconds=32),
        claim_next=True,
    )

    assert claimed is not None
    assert claimed.turn_id == response.turn_id
    assert claimed.status is TurnStatus.PROCESSING
    messages = await task_store.list_conversation_messages(
        CONVERSATION_ID,
        user_id=USER_ID,
    )
    assert messages[0].payload["materials"] == _start_state()["materials"]
    assert messages[0].payload["reply_to_message_id"] == "message-brief-v1"
    assert messages[0].payload["artifact_refs"] == ["artifact:brief:video:v1"]


@pytest.mark.asyncio
async def test_replay_fails_closed_without_verified_deepseek_profile() -> None:
    handler = _PlanningHandler()
    graph = compose_agent_runtime_graph(
        registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
    ).graph
    runtime = SupervisorReplayRuntime(
        config=_config(),
        graph=graph,
        model_name=MODEL_NAME,
        model_profiles={},
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="已验证"):
        await runtime.replay(_start_state())

    assert handler.commands == []
    assert handler.provider_calls == 0
    assert handler.powermem_record_calls == 0


@dataclass(frozen=True)
class _FaultResult:
    """统一呈现每个故障点的权威恢复与隔离证据。"""

    safe_reason_code: str
    allowed_reason_codes: tuple[str, ...]
    expected_attempt: int | None
    actual_attempt: int | None
    expected_provider_job_id: str | None
    actual_provider_job_id: str | None
    expected_turn_status: TurnStatus | None
    actual_turn_status: TurnStatus | None
    leaked_sensitive_values: tuple[str, ...] = ()
    duplicate_provider_starts: int = 0
    cross_tenant_objects: tuple[object, ...] = ()
    graph_calls: int = 0
    provider_calls: int = 0


class _FaultClock:
    """为租约、轮询和重启边界提供确定性时钟。"""

    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class _HttpStatusError(RuntimeError):
    """只向 Provider Adapter 暴露 HTTP 状态码。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("供应商敏感错误正文不得持久化")


class _FaultProviderService:
    """按 provider job ID 返回脚本状态，且不保存临时凭据。"""

    def __init__(self) -> None:
        self.start_calls = 0
        self.status_calls: list[str] = []
        self.status_scripts: dict[str, list[object]] = {}

    async def start(
        self,
        request: Mapping[str, object],
        *,
        authorization: str,
        idempotency_key: str,
    ) -> object:
        assert request
        assert authorization.startswith("Bearer ")
        assert idempotency_key.startswith("operation:v1:sha256:")
        self.start_calls += 1
        return {
            "job_id": f"provider-matrix-{self.start_calls}",
            "status": "running",
            "result": {"progress": 0},
        }

    async def status(self, provider_job_id: str) -> object:
        self.status_calls.append(provider_job_id)
        script = self.status_scripts.get(provider_job_id)
        if not script:
            raise AssertionError("故障矩阵未配置 Provider status")
        result = script.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _FaultGraphResumer:
    """模拟 checkpoint 前后各退出一次，并以 event ID 去重。"""

    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode
        self.failed_once = False
        self.calls: list[AgentEvent] = []
        self.checkpointed_ids: set[str] = set()

    async def resume_external_job(
        self,
        namespace: object,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        del namespace
        self.calls.append(completion_event)
        if self.mode == "before" and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("模拟 Graph checkpoint 前退出")
        self.checkpointed_ids.add(idempotency_key)
        if self.mode == "after" and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("模拟 Graph checkpoint 后退出")


class _LiveVideoFaultScenario:
    """通过生产 Runtime/M06 公开对象执行 Task 14 故障矩阵。"""

    async def run(self, fault: str) -> _FaultResult:
        if fault in {
            "checkpoint_before_commit",
            "checkpoint_after_commit",
            "provider_started_before_event",
            "quota_402",
            "provider_timeout",
            "provider_failed",
            "provider_expired_404",
            "partial_scene_failure",
        }:
            return await self._run_operation_fault(fault)
        if fault == "cross_tenant_reference":
            return await self._run_cross_tenant_reference()
        if fault == "invalid_model_profile":
            return await self._run_invalid_model_profile()
        if fault == "handler_missing_after_restart":
            return await self._run_handler_missing_after_restart()
        raise AssertionError(f"未知故障场景：{fault}")

    @staticmethod
    def _success(provider_job_id: str, suffix: str = "result") -> dict:
        return {
            "job_id": provider_job_id,
            "status": "succeeded",
            "result": {
                "artifact_refs": [f"artifact:matrix:{suffix}"],
                "raw": {},
            },
        }

    @staticmethod
    def _reason(resumer: _FaultGraphResumer, *, job_id: str | None = None) -> str:
        events = (
            resumer.calls
            if job_id is None
            else [
                event
                for event in resumer.calls
                if event.payload.get("job_id") == job_id
            ]
        )
        assert events
        reason = events[-1].payload.get("reason_code")
        assert isinstance(reason, str)
        return reason

    @staticmethod
    def _leaks(*documents: object) -> tuple[str, ...]:
        serialized = json.dumps(
            documents,
            ensure_ascii=False,
            default=str,
        ).lower()
        markers = (
            "matrix-secret",
            "供应商敏感错误正文",
            "不得持久化的失败正文",
            "不得持久化的超时正文",
        )
        return tuple(marker for marker in markers if marker.lower() in serialized)

    async def _run_operation_fault(self, fault: str) -> _FaultResult:
        repository = MemoryCompactionQueueRepository()
        provider = _FaultProviderService()
        adapter = ProviderJobAdapter(provider)
        clock = _FaultClock()
        user_id = "user-task14-matrix"
        conversation_id = "conversation-task14-matrix"
        turn_id = f"turn-{fault}"
        await repository.create_turn(
            user_id,
            TurnRecord(
                turn_id=turn_id,
                conversation_id=conversation_id,
                client_input_id=UUID("00000000-0000-4000-8000-000000001499"),
                status=TurnStatus.WAITING_USER,
                target_workflow_id="workflow-task14-matrix",
                decision=None,
                expected_context_version=0,
                created_at=clock.now(),
            ),
        )
        operations: list[object] = []

        async def start_operation(stage: str, attempt: int):
            provider_request = {
                "scene_id": stage.rsplit(":", 1)[-1],
                "prompt": "Task 14 本地 fake 请求",
            }
            request = build_operation_request(
                workflow_id="workflow-task14-matrix",
                stage=stage,
                stage_version=1,
                attempt=attempt,
                provider_request=provider_request,
            )
            coordinator = OperationStartCoordinator(
                repository,
                adapter=adapter,
                user_id=user_id,
                conversation_id=conversation_id,
                clock=clock.now,
                job_id_factory=lambda: f"job-{len(operations) + 1}-{attempt}",
            )
            started = await coordinator.start(
                request,
                provider_request=provider_request,
                authorization=f"Bearer matrix-secret-{attempt}",
                lease_owner=f"starter-{len(operations) + 1}-{attempt}",
            )
            replayed = await coordinator.start(
                request,
                provider_request=provider_request,
                authorization=f"Bearer matrix-secret-replay-{attempt}",
                lease_owner=f"starter-replay-{len(operations) + 1}-{attempt}",
            )
            assert replayed.job_id == started.job_id
            operations.append(started)
            return started

        first = await start_operation("generate_scene_video:scene-1", 1)
        expected_unique_starts = 1
        resumer = _FaultGraphResumer(
            "before"
            if fault == "checkpoint_before_commit"
            else "after"
            if fault == "checkpoint_after_commit"
            else "normal"
        )

        def make_runtime(worker: str, stages: tuple[str, ...]):
            return OperationRecoveryRuntime(
                repository,
                resolver=MappingProviderJobAdapterResolver(
                    {stage: adapter for stage in stages}
                ),
                resumer=resumer,
                worker_id=worker,
                clock=clock.now,
            )

        stage_names = ("generate_scene_video:scene-1",)
        if fault == "partial_scene_failure":
            second = await start_operation("generate_scene_video:scene-2", 1)
            third = await start_operation("generate_scene_video:scene-3", 1)
            expected_unique_starts = 3
            stage_names = (
                "generate_scene_video:scene-1",
                "generate_scene_video:scene-2",
                "generate_scene_video:scene-3",
            )
            provider.status_scripts[first.provider_job_id] = [
                self._success(first.provider_job_id, "scene-1")
            ]
            provider.status_scripts[second.provider_job_id] = [
                TimeoutError("不得持久化的超时正文")
            ]
            provider.status_scripts[third.provider_job_id] = [
                self._success(third.provider_job_id, "scene-3")
            ]
        elif fault == "quota_402":
            provider.status_scripts[first.provider_job_id] = [
                _HttpStatusError(402),
                self._success(first.provider_job_id, "quota-recovered"),
            ]
        elif fault == "provider_timeout":
            provider.status_scripts[first.provider_job_id] = [
                TimeoutError("不得持久化的超时正文")
            ]
        elif fault == "provider_failed":
            provider.status_scripts[first.provider_job_id] = [
                {
                    "job_id": first.provider_job_id,
                    "status": "failed",
                    "error": "不得持久化的失败正文",
                }
            ]
        elif fault == "provider_expired_404":
            provider.status_scripts[first.provider_job_id] = [
                _HttpStatusError(404)
            ]
        else:
            provider.status_scripts[first.provider_job_id] = [
                self._success(first.provider_job_id, fault)
            ]

        if fault == "provider_started_before_event":
            assert await repository.list_events(user_id, conversation_id) == []

        clock.advance(3)
        runtime = make_runtime(f"worker-{fault}", stage_names)
        await runtime.run_once()

        if fault in {"checkpoint_before_commit", "checkpoint_after_commit"}:
            clock.advance(31)
            restarted = make_runtime(f"worker-restarted-{fault}", stage_names)
            await restarted.run_once()
            assert len(resumer.checkpointed_ids) == 1
            actual = await repository.get_operation(user_id, first.job_id)
            assert actual is not None
            expected_provider_job_id = first.provider_job_id
            expected_attempt = 1
            safe_reason = self._reason(resumer, job_id=first.job_id)
        elif fault == "quota_402":
            paused = await repository.get_operation(user_id, first.job_id)
            assert paused is not None
            assert paused.status is ExternalJobStatus.POLLING
            assert paused.next_poll_at is None
            recovered = await runtime.recover_manually(
                user_id,
                conversation_id,
                first.job_id,
            )
            assert (
                recovered.action
                is OperationManualRecoveryAction.RESUMED_ORIGINAL_JOB
            )
            clock.advance(3)
            await runtime.run_once()
            actual = await repository.get_operation(user_id, first.job_id)
            assert actual is not None
            assert provider.status_calls == [
                first.provider_job_id,
                first.provider_job_id,
            ]
            expected_provider_job_id = first.provider_job_id
            expected_attempt = 1
            safe_reason = self._reason(resumer, job_id=first.job_id)
        elif fault in {
            "provider_timeout",
            "provider_failed",
            "provider_expired_404",
        }:
            terminal = await repository.get_operation(user_id, first.job_id)
            assert terminal is not None
            recovery = await runtime.recover_manually(
                user_id,
                conversation_id,
                first.job_id,
            )
            assert recovery.action is OperationManualRecoveryAction.NEW_ATTEMPT_REQUIRED
            actual = await start_operation("generate_scene_video:scene-1", 2)
            expected_unique_starts = 2
            expected_provider_job_id = "provider-matrix-2"
            expected_attempt = 2
            safe_reason = self._reason(resumer, job_id=first.job_id)
        elif fault == "partial_scene_failure":
            failed = await repository.get_operation(user_id, second.job_id)
            assert failed is not None
            assert failed.status is ExternalJobStatus.TIMEOUT
            actual = await start_operation("generate_scene_video:scene-2", 2)
            expected_unique_starts = 4
            expected_provider_job_id = "provider-matrix-4"
            expected_attempt = 2
            safe_reason = self._reason(resumer, job_id=second.job_id)
        else:
            actual = await repository.get_operation(user_id, first.job_id)
            assert actual is not None
            expected_provider_job_id = first.provider_job_id
            expected_attempt = 1
            safe_reason = self._reason(resumer, job_id=first.job_id)

        stored_turn = await repository.get_turn(user_id, turn_id)
        assert stored_turn is not None
        stored_events = await repository.list_events(user_id, conversation_id)
        persisted_operations = [
            item.model_dump(mode="json")
            for item in [
                await repository.get_operation(user_id, operation.job_id)
                for operation in operations
            ]
            if item is not None
        ]
        return _FaultResult(
            safe_reason_code=safe_reason,
            allowed_reason_codes=(
                "provider_succeeded",
                "provider_timeout",
                "provider_business_failed",
                "provider_job_expired",
            ),
            expected_attempt=expected_attempt,
            actual_attempt=actual.attempt,
            expected_provider_job_id=expected_provider_job_id,
            actual_provider_job_id=actual.provider_job_id,
            expected_turn_status=TurnStatus.WAITING_USER,
            actual_turn_status=stored_turn.status,
            leaked_sensitive_values=self._leaks(
                persisted_operations,
                [event.model_dump(mode="json") for event in stored_events],
                [event.model_dump(mode="json") for event in resumer.calls],
            ),
            duplicate_provider_starts=(
                provider.start_calls - expected_unique_starts
            ),
            provider_calls=provider.start_calls,
        )

    async def _run_cross_tenant_reference(self) -> _FaultResult:
        repository = MemoryCompactionQueueRepository()
        task_store = MemoryPixelFlowTaskStore()
        owner = "user-task14-owner"
        attacker = "user-task14-attacker"
        conversation_id = "conversation-task14-owner"
        service = AgentRuntimeService(
            config=_config(),
            repository=repository,
            task_store=task_store,
            primary_execution_intents=("video",),
        )
        assignment = service.assignment_for_new_conversation(
            {},
            initial_intent="video",
        )
        await task_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id=owner,
                orchestration_mode=assignment.orchestration_mode.value,
                orchestration_version=assignment.orchestration_version,
                context=assignment.context,
            )
        )
        try:
            await service.start_turn(
                user_id=attacker,
                conversation_id=conversation_id,
                request=TurnStartRequest(
                    client_input_id=UUID(
                        "00000000-0000-4000-8000-000000001498"
                    ),
                    content="引用其他租户 workflow/artifact/interrupt",
                    materials=[],
                    artifact_refs=["artifact:owner:video:v1"],
                    expected_context_version=0,
                ),
            )
        except LookupError:
            safe_reason = "tenant_scope_not_found"
        else:
            raise AssertionError("跨租户 Turn 必须在 conversation 边界拒绝")
        visible = [
            await task_store.get_conversation(
                conversation_id,
                user_id=attacker,
            )
        ]
        visible.extend(await repository.list_turns(attacker, conversation_id))
        visible.extend(await repository.list_workflows(attacker, conversation_id))
        visible.extend(await repository.list_events(attacker, conversation_id))
        return _FaultResult(
            safe_reason_code=safe_reason,
            allowed_reason_codes=("tenant_scope_not_found",),
            expected_attempt=None,
            actual_attempt=None,
            expected_provider_job_id=None,
            actual_provider_job_id=None,
            expected_turn_status=None,
            actual_turn_status=None,
            cross_tenant_objects=tuple(item for item in visible if item is not None),
        )

    async def _run_invalid_model_profile(self) -> _FaultResult:
        handler = _PlanningHandler()
        graph = compose_agent_runtime_graph(
            registry=FakeWorkflowRegistry({WorkflowKind.VIDEO: handler}),
        ).graph
        runtime = SupervisorReplayRuntime(
            config=_config(),
            graph=graph,
            model_name=MODEL_NAME,
            model_profiles={},
            clock=lambda: NOW,
        )
        try:
            await runtime.replay(_start_state())
        except ValueError:
            safe_reason = "model_profile_unverified"
        else:
            raise AssertionError("未验证模型档案必须 fail-closed")
        return _FaultResult(
            safe_reason_code=safe_reason,
            allowed_reason_codes=("model_profile_unverified",),
            expected_attempt=None,
            actual_attempt=None,
            expected_provider_job_id=None,
            actual_provider_job_id=None,
            expected_turn_status=None,
            actual_turn_status=None,
            graph_calls=len(handler.commands),
            provider_calls=handler.provider_calls,
        )

    async def _run_handler_missing_after_restart(self) -> _FaultResult:
        repository = MemoryCompactionQueueRepository()
        task_store = MemoryPixelFlowTaskStore()
        owner = "user-task14-restart"
        conversation_id = "conversation-task14-frozen"
        ready_service = AgentRuntimeService(
            config=_config(),
            repository=repository,
            task_store=task_store,
            primary_execution_intents=("video",),
        )
        assignment = ready_service.assignment_for_new_conversation(
            {},
            initial_intent="video",
        )
        await task_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id=owner,
                orchestration_mode=assignment.orchestration_mode.value,
                orchestration_version=assignment.orchestration_version,
                context=assignment.context,
            )
        )
        restarted = AgentRuntimeService(
            config=_config(),
            repository=repository,
            task_store=task_store,
        )
        new_assignment = restarted.assignment_for_new_conversation(
            {},
            initial_intent="video",
        )
        assert new_assignment.orchestration_mode.value == "frontend_v2"
        try:
            await restarted.start_turn(
                user_id=owner,
                conversation_id=conversation_id,
                request=TurnStartRequest(
                    client_input_id=UUID(
                        "00000000-0000-4000-8000-000000001497"
                    ),
                    content="重启后继续原视频流程",
                    materials=[],
                    expected_context_version=0,
                ),
            )
        except AgentRuntimeUnavailableError:
            safe_reason = "agent_runtime_unavailable"
        else:
            raise AssertionError("冻结对话缺 Handler 时必须固定不可用")
        frozen = await task_store.get_conversation(
            conversation_id,
            user_id=owner,
        )
        assert frozen is not None
        assert frozen.orchestration_mode == "supervisor_v1"
        turns = await repository.list_turns(owner, conversation_id)
        return _FaultResult(
            safe_reason_code=safe_reason,
            allowed_reason_codes=("agent_runtime_unavailable",),
            expected_attempt=None,
            actual_attempt=None,
            expected_provider_job_id=None,
            actual_provider_job_id=None,
            expected_turn_status=None,
            actual_turn_status=(None if not turns else turns[-1].status),
        )


@pytest.fixture
def live_video_fault_scenario() -> _LiveVideoFaultScenario:
    return _LiveVideoFaultScenario()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "checkpoint_before_commit",
        "checkpoint_after_commit",
        "provider_started_before_event",
        "quota_402",
        "provider_timeout",
        "provider_failed",
        "provider_expired_404",
        "partial_scene_failure",
        "cross_tenant_reference",
        "invalid_model_profile",
        "handler_missing_after_restart",
    ],
)
async def test_video_live_fault_matrix_is_recoverable_and_isolated(
    fault: str,
    live_video_fault_scenario: _LiveVideoFaultScenario,
) -> None:
    result = await live_video_fault_scenario.run(fault)

    assert result.safe_reason_code in result.allowed_reason_codes
    assert result.actual_attempt == result.expected_attempt
    assert result.actual_provider_job_id == result.expected_provider_job_id
    assert result.actual_turn_status is result.expected_turn_status
    assert result.leaked_sensitive_values == ()
    assert result.duplicate_provider_starts == 0
    assert result.cross_tenant_objects == ()
    if fault == "invalid_model_profile":
        assert result.graph_calls == 0
        assert result.provider_calls == 0
