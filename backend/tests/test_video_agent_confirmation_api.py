"""Task 11 VideoAgent 公开确认 Controller 与身份隔离测试。"""

from __future__ import annotations

import asyncio
import copy
import pickle
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.auth.models import User
from app.gateway.routers import pixelflow_conversations
from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.service import (
    AgentRuntimeService,
    VideoAgentQuotaResponse,
)
from pixelflow.tasks import MemoryPixelFlowTaskStore
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.credentials import (
    TransientVideoAgentCredential,
    VideoAgentCredentialUnavailableError,
)
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.tools import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
)
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository
from tests._router_auth_helpers import make_authed_test_app

NOW = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-4000-8000-000000000111")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000112")
AUTHORIZATION = "Bearer task11-confirmation-secret"


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_ids: list[str] = Field(default_factory=list)


class CredentialCaptureTool:
    spec = VideoToolSpec(
        name="generate_scenes",
        description="测试确认后的计费步骤",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scenes.variants",),
    )

    def __init__(self) -> None:
        self.calls = 0
        self.credential: TransientVideoAgentCredential | None = None

    async def execute(
        self,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult:
        del arguments
        self.calls += 1
        self.credential = context.credential
        assert context.credential is not None
        assert context.credential.borrow_authorization() == AUTHORIZATION
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="镜头生成完成",
        )


def _user(user_id: UUID, email: str) -> User:
    return User(
        email=email,
        password_hash="x",
        system_role="user",
        id=user_id,
    )


async def _make_confirmation_app() -> tuple[
    object,
    MemoryPixelFlowTaskStore,
    MemoryVideoAgentRepository,
    CredentialCaptureTool,
]:
    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    tool = CredentialCaptureTool()
    ticks = iter(NOW + timedelta(seconds=value) for value in range(1, 30))
    executor = VideoAgentExecutor(
        repository=video_repository,
        registry=VideoToolRegistry([tool]),
        clock=lambda: next(ticks),
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
        video_agent_repository=video_repository,
        video_agent_executor=executor,
        clock=lambda: NOW,
    )
    app = make_authed_test_app(
        user_factory=lambda: _user(USER_ID, "task11@example.com")
    )
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)
    return app, task_store, video_repository, tool


async def _create_waiting_confirmation(
    client: httpx.AsyncClient,
    repository: MemoryVideoAgentRepository,
) -> tuple[str, str]:
    created = await client.post(
        "/agent/conversations",
        json={"title": "Task 11确认测试"},
    )
    conversation_id = created.json()["conversation_id"]
    workspace = await repository.create_workspace(
        str(USER_ID),
        VideoWorkspace(
            workspace_id="workspace-confirmation",
            conversation_id=conversation_id,
            payload={"scenes": [{"scene_id": "scene-1"}]},
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    step = AgentPlanStep(
        step_id="step-confirmation",
        plan_id="plan-confirmation",
        sequence=1,
        tool_name="generate_scenes",
        title="生成第一镜视频",
        status=PlanStepStatus.PENDING,
        arguments={"scene_ids": ["scene-1"]},
        confirmation_required=True,
    )
    await repository.save_plan(
        str(USER_ID),
        AgentPlan(
            plan_id=step.plan_id,
            workspace_id=workspace.workspace_id,
            conversation_id=conversation_id,
            status=AgentPlanStatus.RUNNING,
            public_goal="生成确认后的镜头",
            created_at=NOW,
            updated_at=NOW,
        ),
        [step],
    )
    await repository.request_step_confirmation(
        str(USER_ID),
        step.plan_id,
        step.step_id,
    )
    await repository.update_plan_status(
        str(USER_ID),
        step.plan_id,
        AgentPlanStatus.AWAITING_CONFIRMATION,
        now=NOW,
    )
    snapshot = await client.get(
        f"/agent/conversations/{conversation_id}/agent-snapshot"
    )
    confirmation = snapshot.json()["videoAgent"]["confirmation"]
    assert confirmation["submittable"] is True
    assert confirmation["unavailable_reason"] is None
    return conversation_id, confirmation["confirmation_id"]


@pytest.mark.asyncio
async def test_confirmation_controller_resumes_original_plan_and_discards_credential() -> None:
    """确认必须继续同一个持久化步骤，并在请求结束后销毁临时凭据。"""

    app, _, repository, tool = await _make_confirmation_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://task11.test") as client:
        conversation_id, confirmation_id = await _create_waiting_confirmation(
            client,
            repository,
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/confirmations/"
            f"{confirmation_id}/responses",
            headers={"Authorization": AUTHORIZATION},
            json={"step_id": "step-confirmation", "decision": "confirm"},
        )
        restored = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot"
        )

    assert response.status_code == 200
    assert response.json()["plan_status"] == "completed"
    assert response.json()["step_status"] == "completed"
    assert AUTHORIZATION not in response.text
    assert tool.calls == 1
    assert restored.json()["videoAgent"]["confirmation"] is None
    assert tool.credential is not None
    with pytest.raises(VideoAgentCredentialUnavailableError):
        tool.credential.borrow_authorization()


@pytest.mark.asyncio
async def test_confirmation_schedules_background_continue_for_followup_steps() -> None:
    """创意确认后 HTTP 立即返回；后续步由后台 resume 推进。"""

    class ConfirmThenFollowTool:
        spec = VideoToolSpec(
            name="confirm_script_creative",
            description="确认创意",
            input_model=EmptyInput,
            cost_level=VideoToolCostLevel.NONE,
            confirmation_required=True,
            idempotency_mode=VideoToolIdempotencyMode.REQUEST,
            recovery_mode=VideoToolRecoveryMode.REPLAY,
            workspace_mutations=("script_pipeline",),
        )

        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, context: VideoToolContext, arguments):
            del context, arguments
            self.calls += 1
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="创意已确认",
            )

    class FollowupTool:
        spec = VideoToolSpec(
            name="inspect_video_workspace",
            description="后续阶段",
            input_model=EmptyInput,
            cost_level=VideoToolCostLevel.NONE,
            confirmation_required=False,
            idempotency_mode=VideoToolIdempotencyMode.REQUEST,
            recovery_mode=VideoToolRecoveryMode.REPLAY,
            workspace_mutations=(),
        )

        def __init__(self) -> None:
            self.calls = 0
            self.gate = asyncio.Event()

        async def execute(self, context: VideoToolContext, arguments):
            del context, arguments
            await self.gate.wait()
            self.calls += 1
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="后续完成",
            )
    confirm_tool = ConfirmThenFollowTool()
    followup_tool = FollowupTool()
    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    ticks = iter(NOW + timedelta(seconds=value) for value in range(1, 40))
    executor = VideoAgentExecutor(
        repository=video_repository,
        registry=VideoToolRegistry([confirm_tool, followup_tool]),
        clock=lambda: next(ticks),
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
        video_agent_repository=video_repository,
        video_agent_executor=executor,
        clock=lambda: NOW,
    )
    app = make_authed_test_app(
        user_factory=lambda: _user(USER_ID, "task11-followup@example.com")
    )
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://task11-followup.test"
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "确认后续续跑"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-confirm-followup",
                conversation_id=conversation_id,
                payload={},
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        steps = [
            AgentPlanStep(
                step_id="step-confirm",
                plan_id="plan-confirm-followup",
                sequence=1,
                tool_name=confirm_tool.spec.name,
                title="确认选题创意",
                status=PlanStepStatus.PENDING,
                arguments={},
                confirmation_required=True,
            ),
            AgentPlanStep(
                step_id="step-followup",
                plan_id="plan-confirm-followup",
                sequence=2,
                tool_name=followup_tool.spec.name,
                title="写三幕结构",
                status=PlanStepStatus.PENDING,
                arguments={},
                confirmation_required=False,
            ),
        ]
        await video_repository.save_plan(
            str(USER_ID),
            AgentPlan(
                plan_id="plan-confirm-followup",
                workspace_id=workspace.workspace_id,
                conversation_id=conversation_id,
                status=AgentPlanStatus.RUNNING,
                public_goal="创意确认后继续",
                created_at=NOW,
                updated_at=NOW,
            ),
            steps,
        )
        await video_repository.request_step_confirmation(
            str(USER_ID),
            "plan-confirm-followup",
            "step-confirm",
        )
        await video_repository.update_plan_status(
            str(USER_ID),
            "plan-confirm-followup",
            AgentPlanStatus.AWAITING_CONFIRMATION,
            now=NOW,
        )
        snapshot = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot"
        )
        confirmation_id = snapshot.json()["videoAgent"]["confirmation"][
            "confirmation_id"
        ]

        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/confirmations/"
            f"{confirmation_id}/responses",
            headers={"Authorization": AUTHORIZATION},
            json={"step_id": "step-confirm", "decision": "confirm"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["step_status"] == "completed"
    assert body["plan_status"] == "running"
    assert confirm_tool.calls == 1
    assert followup_tool.calls == 0

    followup_tool.gate.set()
    pending = list(service._executor_notification_tasks)
    if pending:
        await asyncio.gather(*pending)

    plan = await video_repository.get_plan(str(USER_ID), "plan-confirm-followup")
    assert plan is not None
    assert plan.status is AgentPlanStatus.COMPLETED
    assert followup_tool.calls == 1


@pytest.mark.asyncio
async def test_cancel_confirmation_never_executes_billable_tool() -> None:
    """取消只终止当前计划，不调用待确认计费工具。"""

    app, _, repository, tool = await _make_confirmation_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://task11.test") as client:
        conversation_id, confirmation_id = await _create_waiting_confirmation(
            client,
            repository,
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/confirmations/"
            f"{confirmation_id}/responses",
            headers={"Authorization": AUTHORIZATION},
            json={"step_id": "step-confirmation", "decision": "cancel"},
        )
        restored = await client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot"
        )

    assert response.status_code == 200
    assert response.json()["plan_status"] == "cancelled"
    assert response.json()["step_status"] == "skipped"
    assert restored.status_code == 200
    assert restored.json()["videoAgent"]["confirmation"] is None
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_confirmation_controller_rejects_other_user_and_wrong_identity() -> None:
    """确认单必须同时匹配当前用户、对话、最新计划和步骤。"""

    app, task_store, repository, _ = await _make_confirmation_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://task11.test") as client:
        conversation_id, confirmation_id = await _create_waiting_confirmation(
            client,
            repository,
        )
        wrong_identity = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/confirmations/"
            "video_confirmation_wrong/responses",
            json={"step_id": "step-confirmation", "decision": "confirm"},
        )

    other_app = make_authed_test_app(
        user_factory=lambda: _user(OTHER_USER_ID, "other-task11@example.com")
    )
    other_app.state.pixelflow_task_store = task_store
    other_app.state.pixelflow_agent_runtime_service = (
        app.state.pixelflow_agent_runtime_service
    )
    other_app.include_router(pixelflow_conversations.router)
    other_transport = httpx.ASGITransport(app=other_app)
    async with httpx.AsyncClient(
        transport=other_transport,
        base_url="http://task11-other.test",
    ) as other_client:
        other_user = await other_client.post(
            f"/agent/conversations/{conversation_id}/video-agent/confirmations/"
            f"{confirmation_id}/responses",
            json={"step_id": "step-confirmation", "decision": "confirm"},
        )

    assert wrong_identity.status_code == 409
    assert wrong_identity.json()["detail"]["code"] == (
        "video_agent_confirmation_conflict"
    )
    assert other_user.status_code == 404


def test_video_agent_confirmation_openapi_contract_is_strict() -> None:
    """公开确认 DTO 禁止自由文本指令和未声明字段。"""

    app = make_authed_test_app(
        user_factory=lambda: _user(USER_ID, "task11-schema@example.com")
    )
    app.include_router(pixelflow_conversations.router)
    schema = app.openapi()
    request_schema = schema["components"]["schemas"][
        "VideoAgentConfirmationResponseRequest"
    ]

    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {"step_id", "decision"}
    assert set(request_schema["required"]) == {"step_id", "decision"}


@pytest.mark.asyncio
async def test_quota_controller_submits_only_stable_interrupt_and_decision() -> None:
    """额度Controller不得接收Provider job ID或自由文本恢复指令。"""

    class RecordingQuotaService:
        def __init__(self) -> None:
            self.call: dict[str, object] | None = None

        async def respond_to_video_agent_quota(self, **kwargs):
            self.call = kwargs
            request = kwargs["request"]
            return VideoAgentQuotaResponse(
                quota_interrupt_id=kwargs["quota_interrupt_id"],
                plan_id="plan-1",
                step_id="step-1",
                decision=request.decision,
                plan_status=AgentPlanStatus.RUNNING,
                step_status=PlanStepStatus.RUNNING,
            )

    service = RecordingQuotaService()
    app = make_authed_test_app(
        user_factory=lambda: _user(USER_ID, "task11-quota@example.com")
    )
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://task11-quota.test",
    ) as client:
        response = await client.post(
            "/agent/conversations/conversation-1/video-agent/quota/"
            "quota-interrupt-1/responses",
            json={"decision": "resume"},
            headers={"Authorization": AUTHORIZATION},
        )
        rejected = await client.post(
            "/agent/conversations/conversation-1/video-agent/quota/"
            "quota-interrupt-1/responses",
            json={
                "decision": "resume",
                "provider_job_id": "禁止由客户端提交",
            },
        )

    assert response.status_code == 200
    assert response.json()["decision"] == "resume"
    assert service.call is not None
    assert service.call["conversation_id"] == "conversation-1"
    assert service.call["quota_interrupt_id"] == "quota-interrupt-1"
    credential = service.call["credential"]
    assert isinstance(credential, TransientVideoAgentCredential)
    assert credential.borrow_authorization() == AUTHORIZATION
    assert rejected.status_code == 422


def test_video_agent_quota_openapi_contract_is_strict() -> None:
    app = make_authed_test_app(
        user_factory=lambda: _user(USER_ID, "task11-quota-schema@example.com")
    )
    app.include_router(pixelflow_conversations.router)
    request_schema = app.openapi()["components"]["schemas"][
        "VideoAgentQuotaResponseRequest"
    ]

    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {"decision"}
    assert set(request_schema["required"]) == {"decision"}


def test_transient_video_agent_credential_cannot_be_copied_or_serialized() -> None:
    """执行期凭据对象不能通过复制、日志或 pickle 逃逸请求边界。"""

    credential = TransientVideoAgentCredential(AUTHORIZATION)

    assert AUTHORIZATION not in repr(credential)
    with pytest.raises(TypeError):
        copy.copy(credential)
    with pytest.raises(TypeError):
        pickle.dumps(credential)
