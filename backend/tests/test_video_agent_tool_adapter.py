"""Video Tool Gateway / StructuredTool 适配合同测试。"""

from __future__ import annotations

import json

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tool_adapter import build_video_agent_tools
from pixelflow.video_agent.tool_gateway import VideoToolGateway
from pixelflow.video_agent.tools.inspect_workspace import InspectVideoWorkspaceTool
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolRegistry,
)


def _registry() -> VideoToolRegistry:
    return VideoToolRegistry([InspectVideoWorkspaceTool()])


def _workspace() -> VideoWorkspace:
    return VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={
            "script": {"title": "demo"},
            "artifact_refs": ["artifact:script-1"],
        },
    )


@pytest.mark.asyncio
async def test_gateway_executes_registered_tool_and_returns_safe_json() -> None:
    registry = _registry()
    gateway = VideoToolGateway(registry=registry)
    context = VideoToolContext(user_id="user-1", workspace=_workspace())

    raw = await gateway.invoke("inspect_video_workspace", {}, context=context)
    payload = json.loads(raw)

    assert payload["tool_name"] == "inspect_video_workspace"
    assert "脚本 1 份" in payload["public_summary"]
    assert payload["artifact_refs"] == ["artifact:script-1"]
    assert "authorization" not in payload
    assert "workspace_patch" not in payload


@pytest.mark.asyncio
async def test_gateway_rejects_unknown_tool_with_safe_summary() -> None:
    gateway = VideoToolGateway(registry=_registry())
    context = VideoToolContext(user_id="user-1", workspace=_workspace())

    raw = await gateway.invoke("delete_database", {}, context=context)
    payload = json.loads(raw)

    assert payload["tool_name"] == "delete_database"
    assert "未注册" in payload["public_summary"]


@pytest.mark.asyncio
async def test_gateway_builds_context_from_runtime_mapping() -> None:
    gateway = VideoToolGateway(registry=_registry())
    workspace = _workspace()

    raw = await gateway.invoke(
        "inspect_video_workspace",
        {"user_id": "attacker", "authorization": "Bearer leaked"},
        runtime_context={
            "user_id": "user-1",
            "workspace": workspace,
            "plan_id": "plan-1",
            "step_id": "step-1",
        },
    )
    payload = json.loads(raw)
    assert payload["tool_name"] == "inspect_video_workspace"
    assert "Bearer" not in raw
    assert "attacker" not in raw


def test_build_video_agent_tools_hides_runtime_context_from_schema() -> None:
    registry = _registry()
    gateway = VideoToolGateway(registry=registry)
    tools = build_video_agent_tools(registry, gateway=gateway)

    assert [tool.name for tool in tools] == ["inspect_video_workspace"]
    schema = tools[0].args_schema.model_json_schema()
    properties = schema.get("properties") or {}
    for forbidden in (
        "user_id",
        "workspace_id",
        "workspace",
        "plan_id",
        "step_id",
        "authorization",
        "credential",
        "revision",
        "runtime",
    ):
        assert forbidden not in properties
