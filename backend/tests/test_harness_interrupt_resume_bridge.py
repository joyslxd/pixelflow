"""验证确认恢复必须经过 Gateway RunBridge，而非直接调用 Sidecar Client。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.gateway.routers import pixelflow_conversations
from pixelflow.agent_control_plane.run_bridge import AgentRunBridge
from pixelflow.agent_harness import HarnessRunHandle
from pixelflow.agent_harness.model_profile import HarnessModelProfile
from pixelflow.agent_tools.repository import HarnessInterruptRecord, RunBinding, SQLAgentToolRepository
from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowConversationRecord
from pixelflow.video.contracts import VideoWorkspace


class _Harness:
    """记录 RunBridge 接收到的恢复请求，不触发真实 Sidecar。"""

    def __init__(self) -> None:
        self.request = None

    async def create_and_bind(self, request):
        self.request = request
        return HarnessRunHandle(run_id="hrun_" + "a" * 32, status="accepted")

    async def get_owned_binding(self, *, user_id: str, conversation_id: str, run_id: str) -> RunBinding:
        assert self.request is not None
        return RunBinding(
            run_id=run_id,
            session_id="pfh_interrupt_resume_test",
            user_id=user_id,
            conversation_id=conversation_id,
            workspace_id=self.request.workspace_id,
            workspace_revision=self.request.workspace_revision,
            context_digest=self.request.context_digest,
            toolset_version="agent-tools-v1",
            tool_manifest_digest="sha256:" + "b" * 64,
            request_digest="sha256:" + "c" * 64,
        )


class _Projector:
    """记录恢复 Run 已被交给 Gateway 投影层。"""

    def __init__(self) -> None:
        self.calls = 0

    async def start(self, *, harness, binding) -> None:
        self.calls += 1
        assert binding.run_id == "hrun_" + "a" * 32


@pytest.mark.asyncio
async def test_confirmation_resume_uses_gateway_run_bridge_and_current_workspace(monkeypatch) -> None:
    """确认恢复必须创建、绑定并投影新 Run，且使用当前权威 Workspace revision。"""

    async def fake_context(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(pixelflow_conversations, "_build_harness_context", fake_context)
    monkeypatch.setenv(
        "PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES",
        json.dumps(
            {
                "video_interactive_v1": {"deadline_seconds": 90, "max_model_steps": 8, "max_business_tools": 3, "max_billable_batch_starts": 1},
                "confirmation_resume_v1": {"deadline_seconds": 150, "max_model_steps": 10, "max_business_tools": 5, "max_billable_batch_starts": 1},
                "run_recovery_v1": {"deadline_seconds": 90, "max_model_steps": 4, "max_business_tools": 0, "max_billable_batch_starts": 0},
            }
        ),
    )
    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-confirmation-resume",
            user_id="confirmation-owner",
            title="确认恢复测试",
        )
    )
    harness = _Harness()
    projector = _Projector()
    app = FastAPI()
    app.state.pixelflow_task_store = store
    app.state.pixelflow_agent_run_bridge = AgentRunBridge(
        harness=harness,  # type: ignore[arg-type]
        projector=projector,  # type: ignore[arg-type]
    )
    app.state.pixelflow_harness_model_profile = HarnessModelProfile(
        "deepseek-v4-pro", "deepseek-v4-flash-vision-exp", "test-v1", "test-budget-v1",
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app})
    workspace = VideoWorkspace(
        workspace_id="workspace-confirmation-resume",
        conversation_id="conversation-confirmation-resume",
        revision=21,
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    interrupt = HarnessInterruptRecord(
        interrupt_id="hint_confirmation_resume",
        tool_call_key="sha256:" + "d" * 64,
        run_id="hrun_" + "e" * 32,
        user_id="confirmation-owner",
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        workspace_revision=17,
        kind="awaiting_confirmation",
        status="responded",
        payload={"tool_name": "generate_scenes"},
        response_id="response-confirmation-resume",
        resumed_run_id=None,
        response_payload={"action": "confirm"},
    )

    run_id = await pixelflow_conversations._start_harness_interrupt_resume(
        request=request,
        user_id="confirmation-owner",
        conversation_id=workspace.conversation_id,
        workspace=workspace,
        interrupt=interrupt,
        trigger_type="confirmation_resume",
        user_input="用户已确认继续执行。",
    )

    assert run_id == "hrun_" + "a" * 32
    assert harness.request is not None
    assert harness.request.trigger_type == "confirmation_resume"
    assert harness.request.system_instruction.startswith("你是 PixelFlow Agent")
    assert "本轮触发约束" in harness.request.system_instruction
    assert "视频 Agent" not in harness.request.system_instruction
    assert harness.request.workspace_revision == 21
    assert harness.request.max_output_tokens == 32_768
    assert projector.calls == 1


@pytest.mark.asyncio
async def test_confirm_response_recovers_after_response_was_written_before_resume_run(monkeypatch) -> None:
    """进程在响应落库后中断时，同内容的新点击必须补齐原 response_id 的恢复 Run。"""

    workspace = VideoWorkspace(
        workspace_id="workspace-confirmation-retry",
        conversation_id="conversation-confirmation-retry",
        revision=21,
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    binding = RunBinding(
        run_id="hrun_" + "f" * 32,
        session_id="pfh_confirmation_retry",
        user_id="confirmation-owner",
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        workspace_revision=17,
        context_digest="sha256:" + "1" * 64,
        toolset_version="agent-tools-v1",
        tool_manifest_digest="sha256:" + "2" * 64,
        request_digest="sha256:" + "3" * 64,
    )
    interrupt = HarnessInterruptRecord(
        interrupt_id="hint_confirmation_retry",
        tool_call_key="sha256:" + "4" * 64,
        run_id=binding.run_id,
        user_id=binding.user_id,
        conversation_id=binding.conversation_id,
        workspace_id=binding.workspace_id,
        workspace_revision=17,
        kind="awaiting_confirmation",
        status="responded",
        payload={"tool_name": "generate_scenes"},
        response_id="response-written-before-crash",
        resumed_run_id=None,
        response_payload={"action": "confirm"},
    )

    class Repository(SQLAgentToolRepository):
        def __init__(self) -> None:
            self.bound_response_id = ""

        async def get_run_binding_by_interrupt(self, _interrupt_id: str):
            return binding

        async def get_interrupt(self, _interrupt_id: str):
            return interrupt

        async def bind_interrupt_resume_run(self, *, client_response_id: str, **_kwargs):
            self.bound_response_id = client_response_id
            return replace(interrupt, resumed_run_id="hrun_" + "b" * 32)

    repository = Repository()
    app = FastAPI()
    app.state.pixelflow_agent_tool_repository = repository
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", b"Bearer confirmation-test-token")],
            "app": app,
        }
    )

    async def current_user(_request):
        return "confirmation-owner"

    async def current_workspace(*_args, **_kwargs):
        return workspace

    captured_resume: dict[str, object] = {}

    async def start_resume(**kwargs):
        captured_resume.update(kwargs)
        return "hrun_" + "b" * 32

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(pixelflow_conversations, "get_current_user", current_user)
    monkeypatch.setattr(pixelflow_conversations, "_require_conversation_workspace", current_workspace)
    monkeypatch.setattr(pixelflow_conversations, "_start_harness_interrupt_resume", start_resume)
    monkeypatch.setattr(pixelflow_conversations, "_publish_harness_interrupt_event", publish)

    result = await pixelflow_conversations.respond_to_harness_interrupt(
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        interrupt_id=interrupt.interrupt_id,
        body=pixelflow_conversations.HarnessInterruptSubmissionRequest(
            client_response_id=uuid4(),
            expected_workspace_revision=17,
            action="confirm",
        ),
        request=request,
    )

    assert result.run_id == "hrun_" + "b" * 32
    assert repository.bound_response_id == "response-written-before-crash"
    assert captured_resume["authorization"] == "Bearer confirmation-test-token"


@pytest.mark.asyncio
async def test_authorization_resume_recovers_after_response_was_written_before_run_binding(monkeypatch) -> None:
    """刷新后生成的新 client_response_id 不得阻断已落库授权响应的安全续接。"""

    workspace = VideoWorkspace(
        workspace_id="workspace-authorization-retry",
        conversation_id="conversation-authorization-retry",
        revision=21,
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    binding = RunBinding(
        run_id="hrun_" + "c" * 32,
        session_id="pfh_authorization_retry",
        user_id="authorization-owner",
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        workspace_revision=17,
        context_digest="sha256:" + "1" * 64,
        toolset_version="agent-tools-v1",
        tool_manifest_digest="sha256:" + "2" * 64,
        request_digest="sha256:" + "3" * 64,
    )
    interrupt = HarnessInterruptRecord(
        interrupt_id="hint_authorization_retry",
        tool_call_key="sha256:" + "4" * 64,
        run_id=binding.run_id,
        user_id=binding.user_id,
        conversation_id=binding.conversation_id,
        workspace_id=binding.workspace_id,
        workspace_revision=17,
        kind="authorization_required",
        status="responded",
        payload={"tool_name": "generate_scenes"},
        response_id="response-written-before-crash",
        resumed_run_id=None,
        response_payload={},
    )

    class Repository(SQLAgentToolRepository):
        def __init__(self) -> None:
            self.bound_response_id = ""

        async def get_run_binding_by_interrupt(self, _interrupt_id: str):
            return binding

        async def get_interrupt(self, _interrupt_id: str):
            return interrupt

        async def bind_interrupt_resume_run(self, *, client_response_id: str, **_kwargs):
            self.bound_response_id = client_response_id
            return replace(interrupt, resumed_run_id="hrun_" + "d" * 32)

    repository = Repository()
    app = FastAPI()
    app.state.pixelflow_agent_tool_repository = repository
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", b"Bearer authorization-test-token")],
            "app": app,
        }
    )

    async def current_user(_request):
        return "authorization-owner"

    async def current_workspace(*_args, **_kwargs):
        return workspace

    captured_resume: dict[str, object] = {}

    async def start_resume(**kwargs):
        captured_resume.update(kwargs)
        return "hrun_" + "d" * 32

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(pixelflow_conversations, "get_current_user", current_user)
    monkeypatch.setattr(pixelflow_conversations, "_require_conversation_workspace", current_workspace)
    monkeypatch.setattr(pixelflow_conversations, "_start_harness_interrupt_resume", start_resume)
    monkeypatch.setattr(pixelflow_conversations, "_publish_harness_interrupt_event", publish)

    result = await pixelflow_conversations.resume_harness_interrupt_authorization(
        conversation_id=workspace.conversation_id,
        workspace_id=workspace.workspace_id,
        interrupt_id=interrupt.interrupt_id,
        body=pixelflow_conversations.HarnessConfirmationResponseRequest(
            client_response_id=uuid4(),
            expected_workspace_revision=17,
        ),
        request=request,
    )

    assert result.run_id == "hrun_" + "d" * 32
    assert repository.bound_response_id == "response-written-before-crash"
    assert captured_resume["authorization"] == "Bearer authorization-test-token"
