"""验证 Harness Workspace Command 与额度 Interrupt 的 Gateway 公开边界。"""

from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Request

from app.gateway.content_app_auth import ContentAppUser
from app.gateway.routers import pixelflow_conversations
from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowConversationRecord
from pixelflow.video.contracts import VideoWorkspace


class _WorkspaceRepository:
    """仅模拟 Router 所需的权威工作区行为，不替代 SQL Repository 的并发测试。"""

    def __init__(self, workspace: VideoWorkspace) -> None:
        self.workspace = workspace
        self.cancel_calls = 0

    async def get_workspace(self, user_id: str, workspace_id: str) -> VideoWorkspace | None:
        if user_id != "workspace-owner" or workspace_id != self.workspace.workspace_id:
            return None
        return self.workspace

    async def apply_workspace_patch(
        self,
        user_id: str,
        workspace_id: str,
        patch: dict[str, object],
        *,
        expected_revision: int,
        now,
    ) -> VideoWorkspace:
        if user_id != "workspace-owner" or workspace_id != self.workspace.workspace_id:
            raise RuntimeError("workspace owner mismatch")
        if (
            self.workspace.revision == expected_revision + 1
            and all(self.workspace.payload.get(key) == value for key, value in patch.items())
        ):
            return self.workspace
        if self.workspace.revision != expected_revision:
            from pixelflow.agent_control_plane.persistence.repositories import (
                AgentRuntimeRecordConflictError,
            )

            raise AgentRuntimeRecordConflictError("workspace revision conflict")
        self.workspace = self.workspace.model_copy(
            update={
                "revision": self.workspace.revision + 1,
                "payload": {**self.workspace.payload, **patch},
                "updated_at": now,
            }
        )
        return self.workspace

    async def cancel_quota_interrupted_plan(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        quota_interrupt_id: str,
        job_id: str,
        quota_pause_revision: int,
        now,
    ) -> None:
        assert user_id == "workspace-owner"
        assert (plan_id, step_id, quota_interrupt_id, job_id, quota_pause_revision) == (
            "plan-1",
            "step-1",
            "interrupt-1",
            "job-1",
            2,
        )
        self.cancel_calls += 1
        payload = deepcopy(self.workspace.payload)
        payload["quota_interrupt"] = None
        payload["last_quota_resolution"] = {
            "event_id": quota_interrupt_id,
            "job_id": job_id,
            "quota_pause_revision": quota_pause_revision,
            "state": "cancelled",
        }
        self.workspace = self.workspace.model_copy(
            update={
                "revision": self.workspace.revision + 1,
                "payload": payload,
                "updated_at": now,
            }
        )


async def _app(workspace: VideoWorkspace) -> tuple[FastAPI, _WorkspaceRepository]:
    """组装带已认证用户、内存对话 Store 和权威工作区替身的公开 Router。"""

    store = MemoryPixelFlowTaskStore()
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-1",
            user_id="workspace-owner",
            title="Harness 工作区测试",
        )
    )
    repository = _WorkspaceRepository(workspace)
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        request.state.user = ContentAppUser(id="workspace-owner", username="workspace-owner")
        return await call_next(request)

    app.state.pixelflow_task_store = store
    app.state.pixelflow_harness_video_repository = repository
    app.include_router(pixelflow_conversations.router)
    return app, repository


@pytest.mark.asyncio
async def test_workspace_command_requires_conversation_owner_revision_and_safe_patch() -> None:
    """命令必须经会话归属、revision 和字段白名单，重复同补丁只回读同一版本。"""

    app, _repository = await _app(
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            revision=3,
            payload={},
        )
    )
    transport = httpx.ASGITransport(app=app)
    command = {
        "client_command_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "workspace-1",
        "expected_workspace_revision": 3,
        "patch": {"workspace_note": "用户已确认夏季主题"},
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/agent/conversations/conversation-1/workspaces/commands", json=command)
        assert first.status_code == 200, first.text
        assert first.json()["workspace"]["revision"] == 4
        replay = await client.post("/agent/conversations/conversation-1/workspaces/commands", json=command)
        assert replay.status_code == 200, replay.text
        assert replay.json()["workspace"]["revision"] == 4
        forbidden = await client.post(
            "/agent/conversations/conversation-1/workspaces/commands",
            json={**command, "client_command_id": "22222222-2222-4222-8222-222222222222", "patch": {"quota_interrupt": {}}},
        )
        assert forbidden.status_code == 422
        nested_forbidden = await client.post(
            "/agent/conversations/conversation-1/workspaces/commands",
            json={
                **command,
                "client_command_id": "44444444-4444-4444-8444-444444444444",
                "patch": {"draft": {"provider_token": "禁止写入"}},
            },
        )
        assert nested_forbidden.status_code == 422
        stale = await client.post(
            "/agent/conversations/conversation-1/workspaces/commands",
            json={**command, "client_command_id": "33333333-3333-4333-8333-333333333333", "patch": {"workspace_note": "过期更新"}},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "harness_workspace_revision_conflict"


@pytest.mark.asyncio
async def test_quota_interrupt_cancel_is_owner_isolated_and_idempotent() -> None:
    """仅取消匹配当前权威状态的额度中断，重复响应不得再次取消计划。"""

    app, repository = await _app(
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            revision=3,
            payload={
                "quota_interrupt": {
                    "quota_interrupt_id": "interrupt-1",
                    "plan_id": "plan-1",
                    "step_id": "step-1",
                    "job_id": "job-1",
                    "quota_pause_revision": 2,
                    "state": "paused",
                    "reason_code": "quota_insufficient",
                }
            },
        )
    )
    transport = httpx.ASGITransport(app=app)
    response = {
        "client_response_id": "22222222-2222-4222-8222-222222222222",
        "value": {
            "content": "取消当前额度中断任务。",
            "explicit_action": {"action": "cancel_workflow", "patch": {}},
        },
    }
    path = "/agent/conversations/conversation-1/workspaces/workspace-1/interrupts/interrupt-1/responses"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(path, json=response)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "cancelled"
        assert repository.cancel_calls == 1
        replay = await client.post(path, json=response)
        assert replay.status_code == 200, replay.text
        assert repository.cancel_calls == 1
        unsupported = await client.post(
            path,
            json={
                **response,
                "client_response_id": "33333333-3333-4333-8333-333333333333",
                "value": {"content": "继续", "explicit_action": {"action": "continue_workflow", "patch": {}}},
            },
        )
        assert unsupported.status_code == 422
