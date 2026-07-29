"""M13.2 / R2 视频 Supervisor 非付费集成合同测试。"""

from __future__ import annotations

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
    AgentIntent,
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
    OperationStartCoordinator,
    ProviderJobAdapter,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.replay import (
    SupervisorReplayDisposition,
    SupervisorReplayRuntime,
)
from pixelflow.agent_runtime.service import AgentRuntimeService
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


def test_primary_assignment_only_gives_enabled_video_new_conversations_to_supervisor() -> None:
    service = AgentRuntimeService(
        config=_config(),
        repository=MemoryCompactionQueueRepository(),
        task_store=MemoryPixelFlowTaskStore(),
    )

    video_assignments = [
        service.assignment_for_new_conversation(
            {"business_field": "保留"},
            initial_intent="video",
        )
        for _ in range(32)
    ]
    image_assignment = service.assignment_for_new_conversation(
        {},
        initial_intent="image",
    )
    ambiguous_assignment = service.assignment_for_new_conversation({})

    assert {item.orchestration_mode.value for item in video_assignments} == {
        "supervisor_v1",
    }
    assert image_assignment.orchestration_mode.value == "frontend_v2"
    assert ambiguous_assignment.orchestration_mode.value == "frontend_v2"
    assert all(
        item.context[AGENT_RUNTIME_CONTEXT_KEY]["mode"] == "primary"
        and item.context[AGENT_RUNTIME_CONTEXT_KEY]["enabled_intents"] == ["video"]
        for item in [*video_assignments, image_assignment, ambiguous_assignment]
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

    assert len(video_cases) == 13
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
