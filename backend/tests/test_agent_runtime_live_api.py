"""Supervisor live Turn、Snapshot 与人工响应 API 集成合同测试。"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.gateway.auth.models import User
from app.gateway.routers import pixelflow_conversations
from pixelflow.agent_runtime import (
    SupervisorTurnExecutor,
)
from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    AgentInterruptProjection,
    TurnRecord,
    TurnStatus,
    WorkflowKind,
)
from pixelflow.agent_runtime.conversation_router import ConversationRouteService
from pixelflow.agent_runtime.graph import (
    FakeWorkflowRegistry,
    make_agent_runtime_graph,
)
from pixelflow.agent_runtime.persistence import (
    AgentRuntimeRecordConflictError,
    MemoryVideoRuntimeRepository,
    StoredAgentInterrupt,
    SupervisorProjectionMessage,
    VideoTurnCommit,
)
from pixelflow.agent_runtime.service import (
    AgentRuntimeService,
    AgentRuntimeSnapshotResponse,
)
from pixelflow.agent_runtime.supervisor import (
    ActionClassificationRequest,
    DecisionValidationRequest,
    DeterministicResolution,
    DeterministicResolutionStatus,
)
from pixelflow.agent_workflows.video import (
    VideoPlanningWorkflowService,
    WorkflowDispatchResult,
    encode_video_workflow_state,
    project_video_workflow_state,
)
from pixelflow.agent_workflows.video.live_operations import (
    TransientCredentialVault,
)
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository
from tests._router_auth_helpers import make_authed_test_app

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-4000-8000-000000000010")
CLIENT_INPUT_ID = UUID("11111111-1111-4111-8111-111111111110")
CLIENT_RESPONSE_ID = UUID("22222222-2222-4222-8222-222222222222")
AUTHORIZATION = "Bearer task10-secret-token"


def _stable_user() -> User:
    return User(
        email="task10@example.com",
        password_hash="x",
        system_role="user",
        id=USER_ID,
    )


def test_snapshot_interrupt_uses_frozen_projection_schema() -> None:
    """运行时类型与 OpenAPI 都必须引用 Task 1 冻结的 interrupt DTO。"""

    assert AgentRuntimeSnapshotResponse.model_fields["interrupt"].annotation == (
        AgentInterruptProjection | None
    )
    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_conversations.router)
    schema = app.openapi()
    interrupt_schema = schema["components"]["schemas"][
        "AgentRuntimeSnapshotResponse"
    ]["properties"]["interrupt"]

    assert interrupt_schema == {
        "anyOf": [
            {"$ref": "#/components/schemas/AgentInterruptProjection"},
            {"type": "null"},
        ],
    }
    projection_schema = schema["components"]["schemas"][
        "AgentInterruptProjection"
    ]
    assert set(projection_schema["properties"]) == {
        "interrupt_id",
        "conversation_id",
        "workflow_id",
        "turn_id",
        "kind",
        "reason_code",
        "payload",
        "opened_at",
    }
    assert projection_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_snapshot_projects_authoritative_video_workspace_plan_and_steps() -> None:
    """刷新页面必须从同一 Snapshot 恢复工作区 revision、计划和有序步骤。"""

    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryVideoRuntimeRepository(
        task_store=task_store,
        completion_clock=lambda: NOW,
    )
    video_agent_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="assist",
            enabled_intents=(),
            new_conversation_rollout_percent=100,
            context_compaction_enabled=True,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_repository=video_agent_repository,
        clock=lambda: NOW,
    )
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://task10-video-agent-snapshot.test",
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "视频工作区恢复"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_agent_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-snapshot",
                conversation_id=conversation_id,
                revision=4,
                payload={"script": {"content": "权威脚本第四版"}},
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        await video_agent_repository.save_plan(
            str(USER_ID),
            AgentPlan(
                plan_id="plan-snapshot",
                workspace_id=workspace.workspace_id,
                conversation_id=conversation_id,
                status=AgentPlanStatus.RUNNING,
                public_goal="修改第一条分镜",
                created_at=NOW,
                updated_at=NOW,
            ),
            [
                AgentPlanStep(
                    step_id="step-snapshot",
                    plan_id="plan-snapshot",
                    sequence=1,
                    tool_name="generate_scenes",
                    title="重新生成第一条分镜",
                    status=PlanStepStatus.PENDING,
                    confirmation_required=True,
                    arguments={
                        "scene_ids": ["scene-1"],
                        "provider_secret": "不得进入公开Snapshot",
                    },
                )
            ],
        )
        await video_agent_repository.request_step_confirmation(
            str(USER_ID),
            "plan-snapshot",
            "step-snapshot",
        )
        await video_agent_repository.update_plan_status(
            str(USER_ID),
            "plan-snapshot",
            AgentPlanStatus.AWAITING_CONFIRMATION,
            now=NOW,
        )
        response = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )

    assert response.status_code == 200
    projection = response.json()["videoAgent"]
    assert projection["workspace"]["revision"] == 4
    assert projection["workspace"]["payload"]["script"]["content"] == "权威脚本第四版"
    assert projection["plan"]["plan_id"] == "plan-snapshot"
    assert "steps" not in projection["plan"]
    assert [step["step_id"] for step in projection["steps"]] == ["step-snapshot"]
    assert "arguments" not in projection["steps"][0]
    assert "tool_name" not in projection["steps"][0]
    assert "不得进入公开Snapshot" not in response.text
    confirmation = projection["confirmation"]
    assert confirmation["confirmation_id"].startswith("video_confirmation_")
    assert confirmation["plan_id"] == "plan-snapshot"
    assert confirmation["step_id"] == "step-snapshot"
    assert confirmation["affected_scene_ids"] == ["scene-1"]
    assert confirmation["submittable"] is False
    assert "1个镜头" in confirmation["cost_summary"]


def test_interrupt_response_request_uses_frozen_openapi_schema() -> None:
    """人工响应入口必须继续公开精确 DTO，不能退化为通用 object。"""

    app = make_authed_test_app(user_factory=_stable_user)
    app.include_router(pixelflow_conversations.router)
    schema = app.openapi()
    operation = schema["paths"][
        "/agent/conversations/{conversation_id}/interrupts/"
        "{interrupt_id}/responses"
    ]["post"]
    request_schema = operation["requestBody"]["content"][
        "application/json"
    ]["schema"]

    assert request_schema == {
        "$ref": "#/components/schemas/InterruptResponseRequest",
    }
    request_component = schema["components"]["schemas"][
        "InterruptResponseRequest"
    ]
    assert set(request_component["properties"]) == {
        "client_response_id",
        "value",
    }
    assert set(request_component["required"]) == {
        "client_response_id",
        "value",
    }
    assert request_component["additionalProperties"] is False


class _FakeDecisionService:
    """只生成可由现有 Validator 复验的视频启动决策。"""

    async def decide(self, evidence):
        resolution = DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=None,
            target_stage=None,
            target_artifact_ref=None,
            reason_code="task10_fake_start",
            candidate_workflow_ids=(),
        )
        classification = ActionClassificationRequest(
            turn_id=evidence.turn.turn_id,
            content=evidence.content,
            deterministic_resolution=resolution,
            candidates=(),
            context_summary="Task 10 本地测试上下文",
        )
        decision = ActionDecision(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            confidence=1,
            requires_confirmation=False,
            patch={},
            reason_code="task10_fake_start",
            idempotency_key=classification.idempotency_key,
        )
        return SimpleNamespace(
            decision=decision,
            validation_request=DecisionValidationRequest(
                decision=decision,
                classification_request=classification,
                current_candidates=(),
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


class _WaitingUserVideoHandler:
    """用真实视频状态 DTO 打开审核 interrupt，不调用模型或 Provider。"""

    async def dispatch(self, command):
        state = VideoPlanningWorkflowService().start(
            workflow_id=command.workflow_id,
            conversation_id=command.conversation_id,
            intent="video",
            intake_context={"source": "task10-api-test"},
            now=NOW,
        )
        workflow = project_video_workflow_state(state)
        envelope = encode_video_workflow_state(
            user_id=command.user_id,
            state=state,
            workflow_version=1,
            last_turn_id=command.turn_id,
            last_action_key=command.decision.idempotency_key,
        )
        opened = StoredAgentInterrupt(
            interrupt_id=f"interrupt-{command.turn_id}",
            conversation_id=command.conversation_id,
            workflow_id=command.workflow_id,
            turn_id=command.turn_id,
            kind="video_intake_form",
            reason_code="video_intake_required",
            payload={
                "workflow_id": command.workflow_id,
                "stage": workflow.current_stage,
            },
            opened_at=NOW,
            user_id=command.user_id,
            thread_id=command.namespace.thread_id,
            checkpoint_ns="root",
        )
        return WorkflowDispatchResult(
            state=envelope,
            workflow=workflow,
            messages=(
                SupervisorProjectionMessage(
                    message_id="live-assistant-plan",
                    conversation_id=command.conversation_id,
                    run_id=command.turn_id,
                    role="assistant",
                    content="请审核视频方案。",
                    payload={},
                    created_at=NOW,
                ),
            ),
            interrupt=opened,
            turn_status=TurnStatus.WAITING_USER,
            update_active_workflow=True,
            active_workflow_id=command.workflow_id,
        )


class _FailingNotificationExecutor:
    """模拟进程内唤醒失败，证明 HTTP 仍以持久化登记为成功边界。"""

    def __init__(self) -> None:
        self.turn_notifications = 0
        self.interrupt_notifications = 0
        self.received_credential = False

    async def notify_turn(self, _scope, credential) -> None:
        self.turn_notifications += 1
        self.received_credential = credential is not None
        raise RuntimeError("注入 Turn 唤醒失败")

    async def notify_interrupt(self, _interrupt, credential=None) -> None:
        self.interrupt_notifications += 1
        self.received_credential = credential is not None
        raise RuntimeError("注入 interrupt 唤醒失败")


class _MultipleOpenSnapshotRepository:
    """模拟 Repository 检出多个 open interrupt 后的 fail-closed 结果。"""

    async def list_projection_messages(self, _user_id, _conversation_id):
        return []

    async def get_open_interrupt(self, _user_id, _conversation_id):
        raise AgentRuntimeRecordConflictError(
            "当前会话存在多个 open interrupt",
        )


class _CorruptedMessageSnapshotRepository:
    """模拟持久层返回无法按公开消息合同解释的损坏投影。"""

    async def list_projection_messages(self, _user_id, _conversation_id):
        return [{"payload": object()}]

    async def get_open_interrupt(self, _user_id, _conversation_id):
        return None


async def _seed_waiting_interrupt(
    repository: MemoryVideoRuntimeRepository,
    *,
    conversation_id: str,
) -> tuple[TurnRecord, StoredAgentInterrupt]:
    """只经公开 Repository 端口建立可供路由响应的 waiting_user 状态。"""

    turn = TurnRecord(
        turn_id="turn-notify-failure",
        conversation_id=conversation_id,
        client_input_id=UUID("33333333-3333-4333-8333-333333333333"),
        status=TurnStatus.ACCEPTED,
        target_workflow_id=None,
        decision=None,
        expected_context_version=0,
        created_at=NOW,
    )
    await repository.enqueue_turn_for_execution(str(USER_ID), turn, now=NOW)
    claim = await repository.claim_turn(
        str(USER_ID),
        conversation_id,
        turn.turn_id,
        lease_owner="task10-notify-seed",
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    assert claim is not None
    state = VideoPlanningWorkflowService().start(
        workflow_id="workflow-notify-failure",
        conversation_id=conversation_id,
        intent="video",
        intake_context={"source": "task10-notify-test"},
        now=NOW,
    )
    workflow = project_video_workflow_state(state)
    decision = ActionDecision(
        action=AgentAction.START_WORKFLOW,
        intent=AgentIntent.VIDEO,
        target_workflow_id=workflow.workflow_id,
        target_stage="intake",
        confidence=1,
        reason_code="task10_notify_seed",
        idempotency_key="task10-notify-seed",
    )
    interrupt = StoredAgentInterrupt(
        interrupt_id="interrupt-notify-failure",
        conversation_id=conversation_id,
        workflow_id=workflow.workflow_id,
        turn_id=turn.turn_id,
        kind="video_intake_form",
        reason_code="video_intake_required",
        payload={"stage": workflow.current_stage},
        opened_at=NOW,
        user_id=str(USER_ID),
        thread_id=conversation_id,
        checkpoint_ns="root",
    )
    await repository.commit_turn(
        claim,
        VideoTurnCommit(
            decision=decision,
            turn_status=TurnStatus.WAITING_USER,
            workflow_state=encode_video_workflow_state(
                user_id=str(USER_ID),
                state=state,
                workflow_version=1,
                last_turn_id=turn.turn_id,
                last_action_key=decision.idempotency_key,
            ),
            workflow=workflow,
            expected_workflow_version=0,
            open_interrupt=interrupt,
            update_active_workflow=True,
            active_workflow_id=workflow.workflow_id,
            occurred_at=NOW,
        ),
    )
    return turn, interrupt


async def _freeze_test_supervisor_owner(
    task_store: MemoryPixelFlowTaskStore,
    conversation_id: str,
) -> None:
    """为只验证历史 live 投影的用例建立已冻结服务端归属。"""

    conversation = await task_store.get_conversation(
        conversation_id,
        user_id=str(USER_ID),
    )
    assert conversation is not None
    updated = await task_store.update_conversation(
        conversation_id,
        user_id=str(USER_ID),
        expected_revision=conversation.revision,
        orchestration_mode="supervisor_v1",
        orchestration_version=1,
        _agent_runtime_patch={"primary_execution_ready": True},
    )
    assert updated is not None


async def _wait_for_open_interrupt(
    repository: MemoryVideoRuntimeRepository,
    conversation_id: str,
) -> StoredAgentInterrupt:
    for _ in range(500):
        opened = await repository.get_open_interrupt(str(USER_ID), conversation_id)
        if opened is not None:
            return opened
        await asyncio.sleep(0.01)
    turns = await repository.list_turns(str(USER_ID), conversation_id)
    events = await repository.list_events(str(USER_ID), conversation_id)
    raise AssertionError(
        "fake Handler 未在限定时间内打开 interrupt："
        f"turns={[item.model_dump(mode='json') for item in turns]}，"
        f"events={[item.model_dump(mode='json') for item in events]}"
    )


@pytest.mark.asyncio
async def test_supervisor_interrupt_response_resumes_original_turn_idempotently() -> None:
    """相同响应必须恢复原 Turn，不能创建 follow-up Turn。"""

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryVideoRuntimeRepository(
        task_store=task_store,
        completion_clock=lambda: NOW,
    )
    graph = make_agent_runtime_graph(
        registry=FakeWorkflowRegistry(
            {WorkflowKind.VIDEO: _WaitingUserVideoHandler()},
        ),
        checkpointer=InMemorySaver(),
    )
    executor = SupervisorTurnExecutor(
        repository=repository,
        task_store=task_store,
        decision_service=_FakeDecisionService(),
        graph=graph,
        credential_vault=TransientCredentialVault(),
        clock=lambda: NOW,
        worker_id="task10-api-worker",
        lease_duration=timedelta(seconds=30),
        heartbeat_step=timedelta(seconds=10),
        scan_interval_seconds=0.01,
    )
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
            context_compaction_enabled=True,
        ),
        repository=repository,
        task_store=task_store,
        turn_executor=executor,
        video_repository=repository,
        conversation_router=ConversationRouteService(),
        primary_execution_intents=("video",),
        clock=lambda: NOW,
    )
    app.include_router(pixelflow_conversations.router)
    await executor.start()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://task10.test",
        ) as client:
            created = await client.post(
                "/agent/conversations",
                json={"title": "Task 10 live API"},
            )
            assert created.status_code == 200
            conversation_id = created.json()["conversation_id"]
            started = await client.post(
                f"/agent/conversations/{conversation_id}/turns/start",
                headers={"Authorization": AUTHORIZATION},
                json={
                    "client_input_id": str(CLIENT_INPUT_ID),
                    "content": "制作一条商品视频",
                    "materials": [],
                    "reply_to_message_id": None,
                    "artifact_refs": [],
                    "expected_context_version": 0,
                },
            )
            assert started.status_code == 200
            opened = await _wait_for_open_interrupt(repository, conversation_id)
            await task_store.append_conversation_message(
                PixelFlowConversationMessageRecord(
                    message_id="live-assistant-plan",
                    conversation_id=conversation_id,
                    user_id=str(USER_ID),
                    role="assistant",
                    content="旧 Store 投影不应覆盖 live 消息。",
                    payload={"stale": True},
                    created_at=(NOW + timedelta(seconds=1)).isoformat(),
                )
            )
            for role, message_id in (
                ("assistant", "client-forged-assistant"),
                ("system", "client-forged-system"),
            ):
                await task_store.append_conversation_message(
                    PixelFlowConversationMessageRecord(
                        message_id=message_id,
                        conversation_id=conversation_id,
                        user_id=str(USER_ID),
                        role=role,
                        content="客户端非权威消息不得进入 live Snapshot。",
                        payload={"forged": True},
                        created_at=(NOW + timedelta(seconds=2)).isoformat(),
                    )
                )
            waiting_snapshot = await client.get(
                f"/agent/conversations/{conversation_id}/agent-snapshot",
            )
            assert waiting_snapshot.status_code == 200
            public_interrupt = waiting_snapshot.json()["interrupt"]
            assert set(public_interrupt) == {
                "interrupt_id",
                "conversation_id",
                "workflow_id",
                "turn_id",
                "kind",
                "reason_code",
                "payload",
                "opened_at",
            }
            assert public_interrupt["interrupt_id"] == opened.interrupt_id
            assert next(
                item
                for item in waiting_snapshot.json()["messages"]
                if item["message_id"] == "live-assistant-plan"
            )["content"] == "请审核视频方案。"
            waiting_message_ids = {
                item["message_id"] for item in waiting_snapshot.json()["messages"]
            }
            assert "client-forged-assistant" not in waiting_message_ids
            assert "client-forged-system" not in waiting_message_ids
            body = {
                "client_response_id": str(CLIENT_RESPONSE_ID),
                "value": {
                    "content": "同意方案",
                    "materials": [],
                    "reply_to_message_id": "message-plan-v1",
                    "artifact_refs": ["artifact:video-plan:wf-1:v1"],
                    "explicit_action": {
                        "action": "continue_workflow",
                        "intent": "video",
                        "workflow_id": opened.workflow_id,
                        "stage": opened.payload["stage"],
                        "artifact_ref": "artifact:video-plan:wf-1:v1",
                        "patch": {"approved": True},
                    },
                },
            }
            response_url = (
                f"/agent/conversations/{conversation_id}/interrupts/"
                f"{opened.interrupt_id}/responses"
            )
            first = await client.post(
                response_url,
                headers={"Authorization": AUTHORIZATION},
                json=body,
            )
            second = await client.post(
                response_url,
                headers={"Authorization": AUTHORIZATION},
                json=body,
            )
            invalid = await client.post(
                response_url,
                headers={"Authorization": AUTHORIZATION},
                json={},
            )
            restored = await client.get(
                f"/agent/conversations/{conversation_id}/agent-snapshot",
            )

        assert first.status_code == second.status_code == 200
        assert invalid.status_code == 422
        assert invalid.json() == {
            "detail": {"code": "agent_runtime_interrupt_response_invalid"},
        }
        assert first.json()["turn_id"] == second.json()["turn_id"]
        assert first.json()["turn_id"] == started.json()["turn_id"]
        assert restored.status_code == 200
        assert restored.json()["interrupt"] is None
        assert any(
            item["payload"].get("interrupt_id") == opened.interrupt_id
            and item["content"] == "同意方案"
            for item in restored.json()["messages"]
        )
        events = await repository.list_events(str(USER_ID), conversation_id)
        assert any(
            event.type.value == "interrupt.responded"
            and event.run_id == started.json()["turn_id"]
            for event in events
        )
        turns = await repository.list_turns(str(USER_ID), conversation_id)
        assert [turn.turn_id for turn in turns] == [started.json()["turn_id"]]
        messages = await task_store.list_conversation_messages(
            conversation_id,
            user_id=str(USER_ID),
        )
        conversation = await task_store.get_conversation(
            conversation_id,
            user_id=str(USER_ID),
        )
        security_surface = json.dumps(
            {
                "snapshot": restored.json(),
                "events": [event.model_dump(mode="json") for event in events],
                "messages": [message.to_dict() for message in messages],
                "conversation": None if conversation is None else conversation.to_dict(),
            },
            ensure_ascii=False,
            default=str,
        )
        assert "task10-secret-token" not in security_surface
    finally:
        await executor.aclose()


@pytest.mark.asyncio
async def test_interrupt_notify_failure_keeps_http_success_and_secret_transient() -> None:
    """响应已原子登记后，进程内唤醒失败不能返回 5xx 或持久化 Authorization。"""

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryVideoRuntimeRepository(
        task_store=task_store,
        completion_clock=lambda: NOW,
    )
    executor = _FailingNotificationExecutor()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
            context_compaction_enabled=True,
        ),
        repository=repository,
        task_store=task_store,
        turn_executor=executor,  # type: ignore[arg-type]
        video_repository=repository,
        conversation_router=ConversationRouteService(),
        primary_execution_intents=("video",),
        clock=lambda: NOW,
    )
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://task10-notify.test",
    ) as client:
        start_conversation = await client.post(
            "/agent/conversations",
            json={"title": "Turn 唤醒失败"},
        )
        start_conversation_id = start_conversation.json()["conversation_id"]
        started = await client.post(
            f"/agent/conversations/{start_conversation_id}/turns/start",
            headers={"Authorization": AUTHORIZATION},
            json={
                "client_input_id": "44444444-4444-4444-8444-444444444444",
                "content": "制作视频，登记后由扫描恢复",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 0,
            },
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        created = await client.post(
            "/agent/conversations",
            json={"title": "唤醒失败恢复"},
        )
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]
        await _freeze_test_supervisor_owner(task_store, conversation_id)
        turn, interrupt = await _seed_waiting_interrupt(
            repository,
            conversation_id=conversation_id,
        )
        body = {
            "client_response_id": str(CLIENT_RESPONSE_ID),
            "value": {
                "content": "同意方案",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "explicit_action": None,
            },
        }
        response_url = (
            f"/agent/conversations/{conversation_id}/interrupts/"
            f"{interrupt.interrupt_id}/responses"
        )
        response = await client.post(
            response_url,
            headers={"Authorization": AUTHORIZATION},
            json=body,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        replay = await client.post(
            response_url,
            headers={"Authorization": AUTHORIZATION},
            json=body,
        )
        conflict_body = deepcopy(body)
        conflict_body["value"]["content"] = "拒绝方案"
        conflict = await client.post(response_url, json=conflict_body)
        snapshot = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )

    assert started.status_code == 200
    assert executor.turn_notifications == 1
    assert response.status_code == replay.status_code == 200
    assert response.json()["turn_id"] == replay.json()["turn_id"] == turn.turn_id
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "agent_runtime_interrupt_conflict",
    }
    assert executor.interrupt_notifications == 1
    assert executor.received_credential is True
    stored_interrupt = await repository.get_interrupt(
        str(USER_ID),
        interrupt.interrupt_id,
    )
    assert stored_interrupt is not None and stored_interrupt.status == "responded"
    assert snapshot.status_code == 200
    persisted = json.dumps(
        {
            "snapshot": snapshot.json(),
            "interrupt": stored_interrupt.model_dump(mode="json"),
            "events": [
                event.model_dump(mode="json")
                for event in await repository.list_events(
                    str(USER_ID),
                    conversation_id,
                )
            ],
            "messages": [
                message.to_dict()
                for message in await task_store.list_conversation_messages(
                    conversation_id,
                    user_id=str(USER_ID),
                )
            ],
            "start_events": [
                event.model_dump(mode="json")
                for event in await repository.list_events(
                    str(USER_ID),
                    start_conversation_id,
                )
            ],
            "start_messages": [
                message.to_dict()
                for message in await task_store.list_conversation_messages(
                    start_conversation_id,
                    user_id=str(USER_ID),
                )
            ],
        },
        ensure_ascii=False,
        default=str,
    )
    assert "task10-secret-token" not in persisted
    await service.aclose()


@pytest.mark.parametrize(
    "video_repository",
    [_MultipleOpenSnapshotRepository(), _CorruptedMessageSnapshotRepository()],
    ids=["multiple-open", "corrupted-message"],
)
@pytest.mark.asyncio
async def test_snapshot_rejects_invalid_live_projection_with_fixed_code(
    video_repository,
) -> None:
    """歧义中断或损坏消息必须 fail-closed，且不得回显内部正文。"""

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryVideoRuntimeRepository(
        task_store=task_store,
        completion_clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
            context_compaction_enabled=True,
        ),
        repository=repository,
        task_store=task_store,
        video_repository=video_repository,  # type: ignore[arg-type]
        primary_execution_intents=("video",),
        clock=lambda: NOW,
    )
    app = make_authed_test_app(user_factory=_stable_user)
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://task10-snapshot.test",
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "损坏中断快照"},
        )
        await _freeze_test_supervisor_owner(
            task_store,
            created.json()["conversation_id"],
        )
        response = await client.get(
            "/agent/conversations/"
            f"{created.json()['conversation_id']}/agent-snapshot",
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "agent_runtime_interrupt_state_invalid",
    }
    assert "多个 open" not in response.text
