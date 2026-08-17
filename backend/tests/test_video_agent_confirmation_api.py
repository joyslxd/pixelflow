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
async def test_legacy_step_confirmation_is_rejected_after_hard_delete() -> None:
    """P0-5：旧 Plan 步骤确认已删除，必须走 native_pending_confirmation。"""

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

    assert response.status_code in {409, 400, 422}
    assert tool.calls == 0
    body = response.text.lower()
    assert "secret" not in body
    assert AUTHORIZATION not in response.text


@pytest.mark.asyncio
async def test_legacy_background_continue_after_step_confirm_is_removed() -> None:
    """P0-5：确认后续步由原生 Agent resume，不再调度 executor.resume_plan。"""

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

    assert response.status_code in {409, 400, 422}
    assert tool.calls == 0

@pytest.mark.asyncio
async def test_cancel_confirmation_never_executes_billable_tool() -> None:
    """P0-5：旧步骤取消同样关闭；不得执行计费工具。"""

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

    assert response.status_code in {409, 400, 422}
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


@pytest.mark.asyncio
async def test_save_video_agent_script_conflict_returns_current_revision() -> None:
    """expected_revision 过期时 409 必须带回权威 current_revision，供前端重试。"""

    app, task_store, video_repository, _tool = await _make_confirmation_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": AUTHORIZATION},
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "脚本冲突测试"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-script-conflict",
                conversation_id=conversation_id,
                payload={
                    "script": {
                        "artifact_ref": "artifact:script-1",
                        "source": "user_edit",
                        "version": 1,
                        "status": "ready",
                        "content": "旧脚本",
                    }
                },
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        # 先 bump 一次，使 revision=2
        await video_repository.apply_workspace_patch(
            str(USER_ID),
            workspace.workspace_id,
            {"script": {**workspace.payload["script"], "content": "中间版"}},
            expected_revision=workspace.revision,
            now=NOW,
        )
        response = await client.put(
            f"/agent/conversations/{conversation_id}/video-agent/script",
            json={
                "markdown": "新脚本",
                "expected_revision": workspace.revision,
                "confirm_for_generation": True,
            },
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "video_agent_script_conflict"
    assert detail["current_revision"] == workspace.revision + 1
    assert detail["workspace_id"] == workspace.workspace_id


class _FakePreparePort:
    def __init__(self) -> None:
        self.calls = 0

    async def start_prepare_scene_packages(self, context, **kwargs):  # noqa: ANN001, ARG002
        self.calls += 1
        from pixelflow.video_agent.tools.scene_packages import ScenePackageOperationJob

        return ScenePackageOperationJob(
            job_id="job-confirm-prepare-1",
            status="polling",
            result={},
        )


async def _make_confirm_script_plan_app() -> tuple[
    object,
    MemoryVideoAgentRepository,
    _FakePreparePort,
]:
    from pixelflow.video_agent.tools.scene_packages import PrepareScenePackagesTool

    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository(
        event_repository=runtime_repository,
    )
    port = _FakePreparePort()
    ticks = iter(NOW + timedelta(seconds=value) for value in range(1, 40))
    executor = VideoAgentExecutor(
        repository=video_repository,
        registry=VideoToolRegistry([PrepareScenePackagesTool(operation_port=port)]),
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
        user_factory=lambda: _user(USER_ID, "confirm-script-plan@example.com")
    )
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = service
    app.include_router(pixelflow_conversations.router)
    return app, video_repository, port


@pytest.mark.asyncio
async def test_confirm_script_plan_command_starts_prepare() -> None:
    app, video_repository, port = await _make_confirm_script_plan_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": AUTHORIZATION},
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "确认脚本命令"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-confirm-cmd",
                conversation_id=conversation_id,
                payload={
                    "script": {
                        "content": "# 脚本\n0—10秒｜开场\n安然推门",
                        "status": "ready",
                        "version": 1,
                        "aspect_ratio": "9:16",
                        "ending_cta": "none",
                        "missing_requirements": [],
                    }
                },
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/commands/confirm-script-plan",
            json={
                "expected_revision": workspace.revision,
                "markdown": "# 脚本\n0—10秒｜开场\n安然推门\n确认正文",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == "job-confirm-prepare-1"
    assert port.calls == 1
    stored = await video_repository.get_workspace(
        str(USER_ID),
        "workspace-confirm-cmd",
    )
    assert stored is not None
    assert stored.payload.get("script_plan_confirmed") is True


@pytest.mark.asyncio
async def test_confirm_script_plan_command_rejects_missing_cta() -> None:
    app, video_repository, port = await _make_confirm_script_plan_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": AUTHORIZATION},
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "确认脚本缺 CTA"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-confirm-gap",
                conversation_id=conversation_id,
                payload={
                    "script": {
                        "content": "# 脚本\n镜头1",
                        "status": "ready",
                        "version": 1,
                        "aspect_ratio": "9:16",
                        "missing_requirements": ["结尾行动引导"],
                    }
                },
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/commands/confirm-script-plan",
            json={"expected_revision": workspace.revision},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "video_agent_script_not_ready"
    assert "结尾行动引导" in detail["missing_fields"]
    assert port.calls == 0


def _shot_table_markdown(*, picture: str) -> str:
    return (
        "时长：20秒 画幅：16:9\n\n镜头列表：\n\n"
        "| 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        f"| 0-10秒 | 近景 | 推 | {picture} | 安然：开场 | 倒计时 | 无 |\n"
        "| 10-20秒 | 中景 | 跟 | 第二镜画面 | Yann：接话 | 记忆点 | 无 |\n"
    )


@pytest.mark.asyncio
async def test_confirm_script_plan_reuses_packages_when_script_unchanged() -> None:
    """选模阶段再次确认且脚本未改：复用已有场景包，不重跑 prepare。"""

    from pixelflow.creative.script_shots import compute_scene_packages_source_digest

    app, video_repository, port = await _make_confirm_script_plan_app()
    episode = _shot_table_markdown(picture="第一镜原画面")
    payload = {
        "script": {
            "content": episode,
            "status": "ready",
            "version": 2,
            "aspect_ratio": "16:9",
            "ending_cta": "none",
            "missing_requirements": [],
        },
        "script_pipeline": {
            "episode": {"stage": "episode", "content": episode, "source": "import"},
            "characters": {
                "stage": "characters",
                "content": "## 角色设定\n### 安然\n女主\n",
            },
        },
        "script_plan_confirmed": True,
        "scene_packages": [
            {"scene_id": "scene-1", "scene_index": 1},
            {"scene_id": "scene-2", "scene_index": 2},
        ],
    }
    payload["scene_packages_source_digest"] = compute_scene_packages_source_digest(payload)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": AUTHORIZATION},
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "确认脚本复用包"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-confirm-reuse",
                conversation_id=conversation_id,
                payload=payload,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/commands/confirm-script-plan",
            json={
                "expected_revision": workspace.revision,
                "markdown": episode,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] is None
    assert "可继续使用" in body["public_summary"]
    assert port.calls == 0


@pytest.mark.asyncio
async def test_confirm_script_plan_reprepares_when_script_edited() -> None:
    """用户改了可抽镜正文再确认：必须重跑 prepare_scene_packages。"""

    from pixelflow.creative.script_shots import compute_scene_packages_source_digest

    app, video_repository, port = await _make_confirm_script_plan_app()
    old_episode = _shot_table_markdown(picture="第一镜原画面")
    new_episode = _shot_table_markdown(picture="第一镜已改成会议室对峙")
    payload = {
        "script": {
            "content": old_episode,
            "status": "ready",
            "version": 2,
            "aspect_ratio": "16:9",
            "ending_cta": "none",
            "missing_requirements": [],
        },
        "script_pipeline": {
            "episode": {"stage": "episode", "content": old_episode, "source": "import"},
            "characters": {
                "stage": "characters",
                "content": "## 角色设定\n### 安然\n女主\n",
            },
        },
        "script_plan_confirmed": True,
        "scene_packages": [
            {"scene_id": "scene-1", "scene_index": 1},
            {"scene_id": "scene-2", "scene_index": 2},
        ],
    }
    payload["scene_packages_source_digest"] = compute_scene_packages_source_digest(payload)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": AUTHORIZATION},
    ) as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "确认脚本重拆包"},
        )
        conversation_id = created.json()["conversation_id"]
        workspace = await video_repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-confirm-reprepare",
                conversation_id=conversation_id,
                payload=payload,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/commands/confirm-script-plan",
            json={
                "expected_revision": workspace.revision,
                "markdown": new_episode,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == "job-confirm-prepare-1"
    assert port.calls == 1
    stored = await video_repository.get_workspace(
        str(USER_ID),
        "workspace-confirm-reprepare",
    )
    assert stored is not None
    episode = stored.payload.get("script_pipeline", {}).get("episode", {})
    assert "会议室对峙" in str(episode.get("content") or "")


@pytest.mark.asyncio
async def test_native_confirmation_accepts_pending_revision_after_persist_bump() -> None:
    """pending 写入自身 +1 后，确认不得因 expected_revision 旧值 409。"""

    from pixelflow.video_agent.confirmation import native_confirmation_id

    app, _, repository, _tool = await _make_confirmation_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://task11.test") as client:
        created = await client.post(
            "/agent/conversations",
            json={"title": "原生确认 revision"},
            headers={"Authorization": AUTHORIZATION},
        )
        conversation_id = created.json()["conversation_id"]
        confirmation_id = native_confirmation_id(
            plan_id="plan-native-merge",
            tool_call_id="call-compose-1",
        )
        workspace = await repository.create_workspace(
            str(USER_ID),
            VideoWorkspace(
                workspace_id="workspace-native-confirm",
                conversation_id=conversation_id,
                payload={},
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        # 模拟旧 bug：pending.expected_revision = 写入前，workspace 已因 persist +1
        await repository.apply_workspace_patch(
            str(USER_ID),
            workspace.workspace_id,
            {
                "native_pending_confirmation": {
                    "confirmation_id": confirmation_id,
                    "tool_name": "compose_or_export_video",
                    "tool_call_id": "call-compose-1",
                    "expected_revision": workspace.revision,
                    "plan_id": "plan-native-merge",
                    "arguments": {"output_type": "mp4"},
                }
            },
            expected_revision=workspace.revision,
            now=NOW,
        )
        await repository.save_plan(
            str(USER_ID),
            AgentPlan(
                plan_id="plan-native-merge",
                workspace_id="workspace-native-confirm",
                conversation_id=conversation_id,
                status=AgentPlanStatus.RUNNING,
                public_goal="合并分镜视频为成片",
                created_at=NOW,
                updated_at=NOW,
            ),
            [],
        )
        await repository.update_plan_status(
            str(USER_ID),
            "plan-native-merge",
            AgentPlanStatus.AWAITING_CONFIRMATION,
            now=NOW,
        )
        response = await client.post(
            f"/agent/conversations/{conversation_id}/video-agent/confirmations/"
            f"{confirmation_id}/responses",
            headers={"Authorization": AUTHORIZATION},
            json={"step_id": "call-compose-1", "decision": "confirm"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "confirm"
    stored = await repository.get_workspace(str(USER_ID), "workspace-native-confirm")
    assert stored is not None
    assert stored.payload.get("native_pending_confirmation") is None
    approved = stored.payload.get("native_approved_confirmation")
    assert isinstance(approved, dict)
    assert approved["tool_name"] == "compose_or_export_video"
