"""观察 Plan 与业务 Tool 上限合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.middleware.loop_limit import VideoLoopLimitMiddleware
from pixelflow.video_agent.middleware.plan import VideoPlanMiddleware
from pixelflow.video_agent.tool_runtime_context import bind_tool_runtime_context
from pixelflow.video_agent.tools.plan import build_update_video_plan_tool

T0 = datetime(2026, 8, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_update_video_plan_tool_publishes_observation_plan_only() -> None:
    plan_mw = VideoPlanMiddleware()
    tool = build_update_video_plan_tool(plan_mw)

    raw = await tool.ainvoke(
        {
            "goal": "导入脚本并检查状态",
            "steps": [
                {"title": "导入脚本", "tool_name": "import_script"},
                {"title": "读取工作区", "tool_name": "inspect_video_workspace"},
            ],
        }
    )
    assert "已发布计划" in raw
    assert plan_mw.current_plan is not None
    assert plan_mw.current_plan.source == "model"
    assert len(plan_mw.current_plan.steps) == 2
    # 明确：发布计划不调用 Executor（本测试无 executor 注入）。


def test_plan_middleware_auto_creates_single_step_when_model_skips_plan() -> None:
    plan_mw = VideoPlanMiddleware()
    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={"latest_input": "帮我看看项目"},
        created_at=T0,
        updated_at=T0,
    )
    with bind_tool_runtime_context({"workspace": workspace}):
        plan_mw.note_business_tool("inspect_video_workspace")
    assert plan_mw.current_plan is not None
    assert plan_mw.current_plan.source == "auto"
    assert len(plan_mw.current_plan.steps) == 1
    assert plan_mw.current_plan.steps[0].tool_name == "inspect_video_workspace"


def test_loop_limit_blocks_business_tools_after_max() -> None:
    mw = VideoLoopLimitMiddleware(max_business_tools=2)
    mw.before_agent({}, SimpleNamespace())

    class ReqRequest:
        def __init__(self, name: str, call_id: str):
            self.tool_call = {"name": name, "id": call_id}

    calls: list[str] = []

    def handler(req: ReqRequest):
        calls.append(str(req.tool_call["name"]))
        return SimpleNamespace(ok=True)

    assert mw.wrap_tool_call(ReqRequest("inspect_video_workspace", "1"), handler)
    assert mw.wrap_tool_call(ReqRequest("import_script", "2"), handler)
    blocked = mw.wrap_tool_call(ReqRequest("patch_scene", "3"), handler)

    assert calls == ["inspect_video_workspace", "import_script"]
    assert getattr(blocked, "status", None) == "error"
    assert "上限" in str(getattr(blocked, "content", ""))
    # 框架 Tool 不受业务上限计数阻塞前的拦截逻辑影响（仍可调用 handler）
    mw.wrap_tool_call(ReqRequest("update_video_plan", "4"), handler)
    assert "update_video_plan" in calls
