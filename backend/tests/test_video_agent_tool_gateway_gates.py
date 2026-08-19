"""P0-3.1 VideoToolGateway 确认 / 额度 / revision 闸门。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.contracts import VideoToolResult, VideoWorkspace
from pixelflow.video_agent.tool_gateway import VideoToolGateway
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class _EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _BillableTool:
    spec = VideoToolSpec(
        name="generate_scenes",
        description="计费生视频",
        input_model=_EmptyInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("scenes",),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult:
        self.calls += 1
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="已启动分镜视频生成",
            pending_operation_job_ids=("job-1",),
        )


class _DestructiveTool:
    spec = VideoToolSpec(
        name="replace_scene_asset",
        description="破坏性替换素材",
        input_model=_EmptyInput,
        cost_level=VideoToolCostLevel.DESTRUCTIVE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("assets",),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult:
        self.calls += 1
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="已替换素材",
        )


class _ComposeTool:
    spec = VideoToolSpec(
        name="compose_or_export_video",
        description="计费合并成片",
        input_model=_EmptyInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("outputs",),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult:
        self.calls += 1
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="已启动成片合并",
        )


async def _setup(
    *,
    tool: Any,
    payload: dict[str, object] | None = None,
) -> tuple[VideoToolGateway, MemoryVideoAgentRepository, MemoryAgentRuntimeRepository, VideoWorkspace, Any]:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    events = MemoryAgentRuntimeRepository()
    repo = MemoryVideoAgentRepository(event_repository=events)
    workspace = await repo.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload or {},
            created_at=now,
            updated_at=now,
        ),
    )
    registry = VideoToolRegistry([tool])
    gateway = VideoToolGateway(
        registry=registry,
        video_repository=repo,
        runtime_repository=events,
    )
    return gateway, repo, events, workspace, tool


@pytest.mark.asyncio
async def test_gateway_blocks_confirmation_required_tool_and_persists_pending() -> None:
    tool = _BillableTool()
    gateway, repo, events, workspace, _ = await _setup(tool=tool)

    raw = await gateway.invoke(
        "generate_scenes",
        {},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "tool_call_id": "call-1",
        },
    )
    payload = json.loads(raw)
    assert tool.calls == 0
    assert payload["requires_confirmation"] is True
    assert "确认" in payload["public_summary"]
    assert "请勿再次调用" in payload["public_summary"]
    assert payload["confirmation_id"]

    refreshed = await repo.get_workspace("user-1", "workspace-1")
    assert refreshed is not None
    pending = refreshed.payload.get("native_pending_confirmation")
    assert isinstance(pending, dict)
    assert pending["tool_name"] == "generate_scenes"
    assert pending["tool_call_id"] == "call-1"
    # 必须对齐写入后 revision；否则确认 API 会恒 409
    assert pending["expected_revision"] == refreshed.revision
    assert pending["expected_revision"] == workspace.revision + 1

    emitted = await events.list_events("user-1", "conversation-1")
    assert any(item.type.value == "agent.confirmation.requested" for item in emitted)


@pytest.mark.asyncio
async def test_gateway_does_not_overwrite_existing_pending_confirmation() -> None:
    """第二个计费 Tool 不得覆盖本 Turn 已持久化的第一张确认单。"""

    generate = _BillableTool()
    compose = _ComposeTool()
    now = datetime(2026, 8, 19, 13, 9, tzinfo=UTC)
    events = MemoryAgentRuntimeRepository()
    repo = MemoryVideoAgentRepository(event_repository=events)
    workspace = await repo.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={"latest_input": "请继续处理当前视频任务"},
            created_at=now,
            updated_at=now,
        ),
    )
    gateway = VideoToolGateway(
        registry=VideoToolRegistry([generate, compose]),
        video_repository=repo,
        runtime_repository=events,
    )
    runtime = {
        "user_id": "user-1",
        "workspace": workspace,
        "plan_id": "plan-1",
        "step_id": "plan-1-native",
        "turn_id": "turn-1",
        "conversation_id": "conversation-1",
        "tool_call_id": "generate-call",
    }

    first = json.loads(await gateway.invoke("generate_scenes", {}, runtime_context=runtime))
    refreshed = await repo.get_workspace("user-1", "workspace-1")
    assert refreshed is not None
    second = json.loads(
        await gateway.invoke(
            "compose_or_export_video",
            {},
            runtime_context={
                **runtime,
                "workspace": refreshed,
                "tool_call_id": "compose-call",
            },
        )
    )

    stored = await repo.get_workspace("user-1", "workspace-1")
    assert stored is not None
    pending = stored.payload.get("native_pending_confirmation")
    assert isinstance(pending, dict)
    assert pending["tool_name"] == "generate_scenes"
    assert pending["confirmation_id"] == first["confirmation_id"]
    assert second["confirmation_id"] == first["confirmation_id"]
    assert generate.calls == 0
    assert compose.calls == 0
    confirmations = [
        event
        for event in await events.list_events("user-1", "conversation-1")
        if event.type.value == "agent.confirmation.requested"
    ]
    assert len(confirmations) == 1


@pytest.mark.asyncio
async def test_gateway_rejects_compose_when_user_requested_generate_all_scenes() -> None:
    """即使模型第一步就选错 Tool，Gateway 也不得为合并成片发确认单。"""

    compose = _ComposeTool()
    gateway, repo, events, workspace, _ = await _setup(
        tool=compose,
        payload={"latest_input": "生成全部分镜视频"},
    )

    result = json.loads(
        await gateway.invoke(
            "compose_or_export_video",
            {},
            runtime_context={
                "user_id": "user-1",
                "workspace": workspace,
                "plan_id": "plan-1",
                "step_id": "plan-1-native",
                "turn_id": "turn-1",
                "conversation_id": "conversation-1",
                "tool_call_id": "compose-call",
            },
        )
    )

    assert result["requires_confirmation"] is False
    assert "generate_scenes" in result["public_summary"]
    assert compose.calls == 0
    stored = await repo.get_workspace("user-1", "workspace-1")
    assert stored is not None
    assert stored.payload.get("native_pending_confirmation") is None
    confirmations = [
        event
        for event in await events.list_events("user-1", "conversation-1")
        if event.type.value == "agent.confirmation.requested"
    ]
    assert confirmations == []


@pytest.mark.asyncio
async def test_gateway_executes_after_matching_approved_confirmation() -> None:
    tool = _BillableTool()
    gateway, _, _, workspace, _ = await _setup(tool=tool)

    raw = await gateway.invoke(
        "generate_scenes",
        {},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "tool_call_id": "call-1",
            "approved_confirmation": {
                "tool_name": "generate_scenes",
                "tool_call_id": "call-1",
                "expected_revision": workspace.revision,
            },
        },
    )
    payload = json.loads(raw)
    assert tool.calls == 1
    assert payload["requires_confirmation"] is False
    assert "已启动" in payload["public_summary"]


@pytest.mark.asyncio
async def test_gateway_rejects_stale_revision_for_destructive_tool() -> None:
    tool = _DestructiveTool()
    gateway, _, _, workspace, _ = await _setup(tool=tool)

    raw = await gateway.invoke(
        "replace_scene_asset",
        {},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "tool_call_id": "call-2",
            "approved_confirmation": {
                "tool_name": "replace_scene_asset",
                "tool_call_id": "call-2",
                "expected_revision": workspace.revision - 1 if workspace.revision > 1 else 0,
            },
        },
    )
    payload = json.loads(raw)
    assert tool.calls == 0
    assert payload["requires_confirmation"] is True
    assert "revision" in payload["public_summary"].lower() or "版本" in payload["public_summary"]


@pytest.mark.asyncio
async def test_gateway_allows_resume_with_new_tool_call_id_after_confirm() -> None:
    """用户确认后 resume 换新 tool_call_id / revision 漂移时，不得再次卡确认。"""

    tool = _BillableTool()
    gateway, _, _, workspace, _ = await _setup(tool=tool)

    raw = await gateway.invoke(
        "generate_scenes",
        {},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
            "turn_id": "turn-confirm-resume",
            "conversation_id": "conversation-1",
            "tool_call_id": "call-new-from-model",
            "approved_confirmation": {
                "tool_name": "generate_scenes",
                "tool_call_id": "call-old-at-confirm",
                "confirmation_id": "video_confirmation_abc",
                "expected_revision": workspace.revision - 1 if workspace.revision > 1 else 0,
            },
        },
    )
    payload = json.loads(raw)
    assert tool.calls == 1
    assert payload["requires_confirmation"] is False


@pytest.mark.asyncio
async def test_gateway_persists_pending_when_arguments_contain_tuple() -> None:
    """模型偶发把 scene_ids 传成 tuple 时，确认单仍须可入库，避免死循环 FORCED STOP。"""

    tool = _BillableTool()
    gateway, repo, _, workspace, _ = await _setup(tool=tool)

    raw = await gateway.invoke(
        "generate_scenes",
        {"scene_ids": ("scene-1",)},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "tool_call_id": "call-tuple-1",
        },
    )
    payload = json.loads(raw)
    assert tool.calls == 0
    assert payload["requires_confirmation"] is True

    refreshed = await repo.get_workspace("user-1", "workspace-1")
    assert refreshed is not None
    pending = refreshed.payload.get("native_pending_confirmation")
    assert isinstance(pending, dict)
    assert pending["arguments"]["scene_ids"] == ["scene-1"]


@pytest.mark.asyncio
async def test_gateway_blocks_billable_when_quota_interrupt_present() -> None:
    tool = _BillableTool()
    gateway, _, _, workspace, _ = await _setup(
        tool=tool,
        payload={
            "quota_interrupt": {
                "quota_interrupt_id": "video_start_quota_abc",
                "plan_id": "plan-1",
                "step_id": "plan-1-native",
            }
        },
    )

    raw = await gateway.invoke(
        "generate_scenes",
        {},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "plan-1-native",
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "tool_call_id": "call-3",
            "approved_confirmation": {
                "tool_name": "generate_scenes",
                "tool_call_id": "call-3",
                "expected_revision": workspace.revision,
            },
        },
    )
    payload = json.loads(raw)
    assert tool.calls == 0
    assert "额度" in payload["public_summary"]
