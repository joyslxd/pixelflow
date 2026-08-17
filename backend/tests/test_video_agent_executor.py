from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from pixelflow.video_agent.contracts import VideoToolResult, VideoWorkspace
from pixelflow.video_agent.executor.service import VideoAgentExecutor
from pixelflow.video_agent.tools import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolRegistry,
    VideoToolSpec,
)
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository

T0 = datetime(2026, 8, 5, tzinfo=UTC)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspacePatchTool:
    spec = VideoToolSpec(
        name="import_script",
        description="测试工作区补丁",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script",),
    )

    async def execute(self, context: VideoToolContext, arguments):
        del context, arguments
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary="脚本已导入",
            workspace_patch={
                "script": {
                    "source": "user_import",
                    "version": 1,
                    "content": "展示商品",
                }
            },
        )


class CountingTool:
    spec = VideoToolSpec(
        name="inspect_video_workspace",
        description="计数工具",
        input_model=EmptyInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: VideoToolContext, arguments):
        del context, arguments
        self.calls += 1
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"调用次数 {self.calls}",
        )

@pytest.mark.asyncio
async def test_run_plan_raises_runtime_error() -> None:
    executor = VideoAgentExecutor(
        repository=MemoryVideoAgentRepository(),
        registry=VideoToolRegistry([]),
        clock=lambda: T0,
    )

    with pytest.raises(RuntimeError, match="run_plan"):
        await executor.run_plan("user-1", "plan-1")


@pytest.mark.asyncio
async def test_confirm_step_raises_runtime_error() -> None:
    executor = VideoAgentExecutor(
        repository=MemoryVideoAgentRepository(),
        registry=VideoToolRegistry([]),
        clock=lambda: T0,
    )

    with pytest.raises(RuntimeError, match="确认"):
        await executor.confirm_step("user-1", "plan-1", "step-1")


@pytest.mark.asyncio
async def test_resume_plan_raises_runtime_error() -> None:
    executor = VideoAgentExecutor(
        repository=MemoryVideoAgentRepository(),
        registry=VideoToolRegistry([]),
        clock=lambda: T0,
    )

    with pytest.raises(RuntimeError, match="run_plan"):
        await executor.resume_plan("user-1", "plan-1")


@pytest.mark.asyncio
async def test_execute_tool_call_runs_only_one_tool_without_plan_traversal() -> None:
    first = CountingTool()
    second = WorkspacePatchTool()
    repository = MemoryVideoAgentRepository()
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={},
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=VideoToolRegistry([first, second]),
        clock=lambda: T0,
    )
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None

    result = await executor.execute_tool_call(
        context=VideoToolContext(user_id="user-1", workspace=workspace),
        tool_name="inspect_video_workspace",
        arguments={},
    )

    assert result.public_summary == "调用次数 1"
    assert first.calls == 1
    refreshed = await repository.get_workspace("user-1", "workspace-1")
    assert refreshed is not None
    assert "script" not in refreshed.payload


@pytest.mark.asyncio
async def test_execute_tool_call_persists_workspace_patch() -> None:
    tool = WorkspacePatchTool()
    repository = MemoryVideoAgentRepository()
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={},
            created_at=T0,
            updated_at=T0,
        ),
    )
    executor = VideoAgentExecutor(
        repository=repository,
        registry=VideoToolRegistry([tool]),
        clock=lambda: T0,
    )
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None

    result = await executor.execute_tool_call(
        context=VideoToolContext(user_id="user-1", workspace=workspace),
        tool_name="import_script",
        arguments={},
    )

    assert result.public_summary == "脚本已导入"
    refreshed = await repository.get_workspace("user-1", "workspace-1")
    assert refreshed is not None
    assert refreshed.payload["script"]["source"] == "user_import"
