"""视频工作区、计划与步骤的领域 Repository Port。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from pixelflow.agent_control_plane.contracts import AgentEvent
from pixelflow.video.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    VideoToolResult,
    VideoWorkspace,
)


@runtime_checkable
class VideoWorkspaceRepository(Protocol):
    async def create_workspace(self, user_id: str, workspace: VideoWorkspace) -> VideoWorkspace: ...

    async def get_workspace(self, user_id: str, workspace_id: str) -> VideoWorkspace | None: ...

    async def discard_workspace(self, user_id: str, workspace_id: str) -> None:
        """补偿删除：仅用于升级失败回滚；已切模式的权威工作区禁止调用。"""
        ...

    async def load_conversation_state(
        self,
        user_id: str,
        conversation_id: str,
    ) -> tuple[VideoWorkspace, AgentPlan | None] | None: ...

    async def list_conversation_plans(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[AgentPlan]:
        """按创建时间返回会话内全部计划（含步骤），供 Snapshot 恢复执行方案历史。"""
        ...

    async def apply_workspace_patch(
        self,
        user_id: str,
        workspace_id: str,
        patch: Mapping[str, JsonValue],
        *,
        expected_revision: int,
        now: datetime,
    ) -> VideoWorkspace: ...

    async def get_plan(self, user_id: str, plan_id: str) -> AgentPlan | None: ...

    async def save_plan(
        self,
        user_id: str,
        plan: AgentPlan,
        steps: list[AgentPlanStep],
        *,
        expected_revision: int | None = None,
    ) -> AgentPlan: ...

    async def update_plan_status(
        self,
        user_id: str,
        plan_id: str,
        status: AgentPlanStatus,
        *,
        now: datetime,
    ) -> AgentPlan: ...

    async def update_plan_public_goal(self, user_id: str, plan_id: str, public_goal: str | None, *, expected_revision: int, now: datetime) -> AgentPlan: ...

    async def start_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlanStep: ...

    async def complete_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        result: VideoToolResult,
        *,
        now: datetime,
    ) -> AgentPlanStep: ...

    async def request_step_confirmation(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
    ) -> AgentPlanStep: ...

    async def confirm_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlanStep: ...

    async def cancel_step_confirmation(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        now: datetime,
    ) -> AgentPlan: ...

    async def cancel_quota_interrupted_plan(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        quota_interrupt_id: str,
        job_id: str,
        quota_pause_revision: int,
        now: datetime,
    ) -> AgentPlan: ...

    async def cancel_active_script_skill_plans(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
    ) -> list[AgentPlan]: ...

    async def cancel_waiting_for_input_plans(
        self,
        user_id: str,
        conversation_id: str,
        *,
        now: datetime,
        exclude_plan_id: str | None = None,
    ) -> list[AgentPlan]: ...

    async def start_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]: ...

    async def progress_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        public_summary: str,
        progress_phase: str,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]: ...

    async def complete_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        result: VideoToolResult,
        *,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]: ...

    async def fail_step_with_event(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        reason_code: str,
        public_summary: str,
        run_id: str,
        now: datetime,
    ) -> tuple[AgentPlanStep, AgentEvent]: ...

    async def list_plan_steps(self, user_id: str, plan_id: str) -> list[AgentPlanStep]: ...


__all__ = ["VideoWorkspaceRepository"]
