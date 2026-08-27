"""脚本与公开 Plan 的非计费 Harness Tool。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.video.contracts import AgentPlan, VideoToolResult

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

if TYPE_CHECKING:
    from pixelflow.video.workspace.repository import VideoWorkspaceRepository


class InspectScriptInput(BaseModel):
    """脚本只从当前绑定 Workspace 读取，模型不得指定其他项目。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class UpdateScriptInput(BaseModel):
    """脚本编辑内容受限，最终仍由 Workspace revision 乐观锁保护。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str = Field(min_length=1, max_length=8_000)


class InspectVideoPlanInput(BaseModel):
    """Plan 标识只用于当前用户、当前 Workspace 下的读取。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1, max_length=64)


class UpdateVideoPlanInput(InspectVideoPlanInput):
    """Plan 使用独立 revision，不复用 Workspace revision。"""

    expected_plan_revision: int = Field(ge=1)
    public_goal: str | None = Field(default=None, max_length=2_000)


class InspectScriptTool:
    """返回受限脚本证据，避免把整个 Workspace payload 交给模型。"""

    spec = VideoToolSpec(
        name="inspect_script",
        description="读取当前视频项目脚本的受限预览、状态与 Workspace revision。",
        input_model=InspectScriptInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
        model_observation_keys=("script_preview", "script_char_count", "script_status"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        del arguments
        script = context.workspace.payload.get("script")
        script_data = script if isinstance(script, Mapping) else {}
        content = str(script_data.get("content") or "").strip()
        status = str(script_data.get("status") or "").strip() or "未生成"
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"当前脚本状态：{status}，共 {len(content)} 字。",
            model_observation={
                "script_preview": content[:4_000],
                "script_char_count": len(content),
                "script_status": status,
            },
        )


class UpdateScriptTool:
    """通过标准 Workspace patch 修改脚本，实际 CAS 由 Tool Executor 统一完成。"""

    spec = VideoToolSpec(
        name="update_script",
        description="更新当前项目脚本文本；使用当前 Run 冻结的 Workspace revision。",
        input_model=UpdateScriptInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("script",),
        model_observation_keys=("script_char_count", "script_status"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = UpdateScriptInput.model_validate(dict(arguments))
        previous = context.workspace.payload.get("script")
        previous_data = previous if isinstance(previous, Mapping) else {}
        content = request.content.strip()
        script = {
            **dict(previous_data),
            "content": content,
            "status": "已编辑",
        }
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"脚本已更新，共 {len(content)} 字。",
            workspace_patch={"script": script},
            model_observation={"script_char_count": len(content), "script_status": "已编辑"},
        )


class _PlanTool:
    """Plan Tool 的 Repository 依赖只能由 Gateway 注入，Sidecar 不可自行访问。"""

    def __init__(self, *, plan_repository: VideoWorkspaceRepository | object | None = None) -> None:
        self._plan_repository = plan_repository

    async def _load_plan(self, context: VideoToolContext, plan_id: str) -> AgentPlan:
        repository = self._plan_repository
        if repository is None or not hasattr(repository, "get_plan"):
            raise VideoToolExecutionError("Plan 服务尚未装配")
        plan = await repository.get_plan(context.user_id, plan_id)
        if plan is None or plan.workspace_id != context.workspace.workspace_id:
            raise VideoToolValidationError("当前项目中不存在该执行计划")
        return plan


class InspectVideoPlanTool(_PlanTool):
    spec = VideoToolSpec(
        name="inspect_video_plan",
        description="读取当前项目一份执行计划的受限公开摘要和独立 Plan revision。",
        input_model=InspectVideoPlanInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
        model_observation_keys=("plan_id", "plan_revision", "plan_status", "public_goal", "steps"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = InspectVideoPlanInput.model_validate(dict(arguments))
        plan = await self._load_plan(context, request.plan_id)
        return _plan_result(self.spec.name, plan, "执行计划已读取。")


class UpdateVideoPlanTool(_PlanTool):
    spec = VideoToolSpec(
        name="update_video_plan",
        description="以独立 Plan revision 更新当前计划公开目标，不修改步骤或 Tool 参数。",
        input_model=UpdateVideoPlanInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=(),
        model_observation_keys=("plan_id", "plan_revision", "plan_status", "public_goal", "steps"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = UpdateVideoPlanInput.model_validate(dict(arguments))
        await self._load_plan(context, request.plan_id)
        repository = self._plan_repository
        assert repository is not None and hasattr(repository, "update_plan_public_goal")
        try:
            plan = await repository.update_plan_public_goal(
                context.user_id,
                request.plan_id,
                request.public_goal,
                expected_revision=request.expected_plan_revision,
                now=datetime.now(UTC),
            )
        except AgentRuntimeRecordConflictError as error:
            raise VideoToolValidationError("执行计划版本已变化，请先重新读取") from error
        return _plan_result(self.spec.name, plan, "执行计划公开目标已更新。")


def _plan_result(tool_name: str, plan: AgentPlan, summary: str) -> VideoToolResult:
    """构造有字段/条数/长度预算的 Plan observation，不暴露 Tool 参数。"""

    steps = [
        {
            "step_id": step.step_id,
            "sequence": step.sequence,
            "title": step.title[:256],
            "status": step.status.value,
        }
        for step in plan.steps[:12]
    ]
    return VideoToolResult(
        tool_name=tool_name,
        public_summary=summary,
        model_observation={
            "plan_id": plan.plan_id,
            "plan_revision": plan.revision,
            "plan_status": plan.status.value,
            "public_goal": (plan.public_goal or "")[:2_000],
            "steps": steps,
        },
    )


__all__ = [
    "InspectScriptInput",
    "InspectScriptTool",
    "InspectVideoPlanInput",
    "InspectVideoPlanTool",
    "UpdateScriptInput",
    "UpdateScriptTool",
    "UpdateVideoPlanInput",
    "UpdateVideoPlanTool",
]
