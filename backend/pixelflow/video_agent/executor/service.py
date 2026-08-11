"""VideoAgent 计划执行、确认闸门与断点恢复 Service。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.credentials import TransientVideoAgentCredential
from pixelflow.video_agent.executor.events import build_confirmation_requested_event
from pixelflow.video_agent.production_fields import (
    analyze_production_fields_with_llm,
    creative_confirm_cost_summary,
    user_latest_input,
)
from pixelflow.video_agent.thinking_stream import ThinkingStreamPublisher
from pixelflow.video_agent.tools import VideoToolContext, VideoToolRegistry
from pixelflow.video_agent.tools.script_skill_pipeline import (
    SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS,
)
from uuid import NAMESPACE_URL, uuid5

if TYPE_CHECKING:
    from pixelflow.video_agent.workspace import VideoAgentRepository

logger = logging.getLogger(__name__)

# reload / 事件循环被堵死后，RUNNING 步骤可能无人续跑；超时后再进 executor 时允许重试。
_STALE_RUNNING_GRACE_SECONDS = 30.0


def _is_stale_running_step(step: AgentPlanStep, *, now: datetime) -> bool:
    if step.status is not PlanStepStatus.RUNNING or step.started_at is None:
        return False
    started = step.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    age = (now - started).total_seconds()
    return age > (SCRIPT_SKILL_STAGE_TIMEOUT_SECONDS + _STALE_RUNNING_GRACE_SECONDS)


class VideoAgentExecutor:
    def __init__(
        self,
        *,
        repository: VideoAgentRepository,
        registry: VideoToolRegistry,
        event_repository: AgentRuntimeRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._event_repository = event_repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_plan(
        self,
        user_id: str,
        plan_id: str,
        *,
        credential: TransientVideoAgentCredential | None = None,
    ) -> AgentPlan:
        plan = await self._required_plan(user_id, plan_id)
        if plan.status in {
            AgentPlanStatus.COMPLETED,
            AgentPlanStatus.FAILED,
            AgentPlanStatus.CANCELLED,
            AgentPlanStatus.AWAITING_CONFIRMATION,
        }:
            return plan
        if len(plan.steps) > 9:
            raise AgentRuntimeRecordConflictError("VideoAgent 单个计划不能超过九步")
        if plan.status is AgentPlanStatus.PLANNING:
            plan = await self._repository.update_plan_status(
                user_id,
                plan_id,
                AgentPlanStatus.RUNNING,
                now=self._clock(),
            )
        return await self._continue(user_id, plan, credential=credential)

    async def confirm_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
        *,
        credential: TransientVideoAgentCredential | None = None,
    ) -> AgentPlan:
        plan = await self._required_plan(user_id, plan_id)
        if plan.status is not AgentPlanStatus.AWAITING_CONFIRMATION:
            raise AgentRuntimeRecordConflictError("VideoAgent plan 当前不等待确认")
        await self._repository.confirm_step(
            user_id,
            plan_id,
            step_id,
            now=self._clock(),
        )
        await self._repository.start_step_with_event(
            user_id,
            plan_id,
            step_id,
            run_id=plan_id,
            now=self._clock(),
        )
        plan = await self._repository.update_plan_status(
            user_id,
            plan_id,
            AgentPlanStatus.RUNNING,
            now=self._clock(),
        )
        # 只跑到确认步完成即返回，避免「同意创意」HTTP 被后续 /plan|/characters LLM 拖死。
        return await self._continue(
            user_id,
            plan,
            credential=credential,
            stop_after_step_id=step_id,
        )

    async def cancel_step(
        self,
        user_id: str,
        plan_id: str,
        step_id: str,
    ) -> AgentPlan:
        """取消当前待确认计划，不把取消伪装成新的自然语言 Turn。"""

        plan = await self._required_plan(user_id, plan_id)
        if plan.status is AgentPlanStatus.CANCELLED:
            return plan
        waiting_steps = [
            step
            for step in plan.steps
            if step.status is PlanStepStatus.AWAITING_CONFIRMATION
        ]
        if (
            plan.status is not AgentPlanStatus.AWAITING_CONFIRMATION
            or len(waiting_steps) != 1
            or waiting_steps[0].step_id != step_id
        ):
            raise AgentRuntimeRecordConflictError("VideoAgent plan 当前确认步骤不匹配")
        return await self._repository.cancel_step_confirmation(
            user_id,
            plan_id,
            step_id,
            now=self._clock(),
        )

    async def resume_plan(
        self,
        user_id: str,
        plan_id: str,
        *,
        credential: TransientVideoAgentCredential | None = None,
    ) -> AgentPlan:
        return await self.run_plan(user_id, plan_id, credential=credential)

    async def maybe_resume_stale_running_plan(
        self,
        user_id: str,
        plan_id: str,
    ) -> AgentPlan | None:
        """Snapshot/恢复扫描：若 RUNNING 步骤已超过 Skill 超时宽限，重新拉起执行。"""

        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None or plan.status is not AgentPlanStatus.RUNNING:
            return None
        running = next(
            (
                step
                for step in plan.steps
                if step.status is PlanStepStatus.RUNNING
            ),
            None,
        )
        if running is None or not _is_stale_running_step(running, now=self._clock()):
            return None
        logger.warning(
            "Snapshot 触发陈旧 RUNNING 计划恢复 plan_id=%s step_id=%s",
            plan_id,
            running.step_id,
        )
        return await self.resume_plan(user_id, plan_id, credential=None)

    async def _continue(
        self,
        user_id: str,
        plan: AgentPlan,
        *,
        credential: TransientVideoAgentCredential | None,
        stop_after_step_id: str | None = None,
    ) -> AgentPlan:
        workspace = await self._repository.get_workspace(user_id, plan.workspace_id)
        if workspace is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent workspace 不存在或不属于当前用户"
            )
        turn_id = str(workspace.payload.get("active_turn_id") or plan.plan_id).strip()
        thinking: ThinkingStreamPublisher | None = None
        if self._event_repository is not None and turn_id:
            thinking = ThinkingStreamPublisher(
                repository=self._event_repository,
                user_id=user_id,
                conversation_id=plan.conversation_id,
                turn_id=turn_id,
                clock=self._clock,
            )
        try:
            return await self._continue_steps(
                user_id=user_id,
                plan=plan,
                workspace=workspace,
                thinking=thinking,
                credential=credential,
                stop_after_step_id=stop_after_step_id,
            )
        finally:
            # 避免执行器思考流悬挂导致 Thought 计时拖到整段 Plan 结束、Turn 假占用。
            if thinking is not None:
                try:
                    await thinking.complete()
                except Exception:  # noqa: BLE001
                    try:
                        await thinking.flush()
                    except Exception:  # noqa: BLE001
                        pass

    async def _continue_steps(
        self,
        *,
        user_id: str,
        plan: AgentPlan,
        workspace: VideoWorkspace,
        thinking: ThinkingStreamPublisher | None,
        credential: TransientVideoAgentCredential | None,
        stop_after_step_id: str | None = None,
    ) -> AgentPlan:
        for step in plan.steps:
            if step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}:
                continue
            if step.status is PlanStepStatus.AWAITING_CONFIRMATION:
                return await self._repository.update_plan_status(
                    user_id,
                    plan.plan_id,
                    AgentPlanStatus.AWAITING_CONFIRMATION,
                    now=self._clock(),
                )
            if step.status is PlanStepStatus.PENDING and step.confirmation_required:
                waiting = await self._repository.request_step_confirmation(
                    user_id,
                    plan.plan_id,
                    step.step_id,
                )
                updated = await self._repository.update_plan_status(
                    user_id,
                    plan.plan_id,
                    AgentPlanStatus.AWAITING_CONFIRMATION,
                    now=self._clock(),
                )
                await self._emit_confirmation_requested(
                    user_id=user_id,
                    plan=updated,
                    step=waiting,
                    workspace_payload=workspace.payload,
                )
                return updated
            if step.status is PlanStepStatus.PENDING:
                step, _ = await self._repository.start_step_with_event(
                    user_id,
                    plan.plan_id,
                    step.step_id,
                    run_id=plan.plan_id,
                    now=self._clock(),
                )
            elif step.status is PlanStepStatus.RUNNING:
                stale = _is_stale_running_step(step, now=self._clock())
                if stale:
                    logger.warning(
                        "检测到陈旧 RUNNING 步骤，重新执行 plan_id=%s step_id=%s title=%s",
                        plan.plan_id,
                        step.step_id,
                        step.title,
                    )
                # 陈旧重跑必须刷新 started_at，否则前端会把僵尸等待算进「耗时」（如 44 分钟）。
                step, _ = await self._repository.start_step_with_event(
                    user_id,
                    plan.plan_id,
                    step.step_id,
                    run_id=plan.plan_id,
                    now=self._clock() if stale else (step.started_at or self._clock()),
                )

            current_step_id = step.step_id

            async def report_progress(message: str, *, phase: str) -> None:
                latest = await self._required_plan(user_id, plan.plan_id)
                if latest.status is AgentPlanStatus.CANCELLED:
                    return
                current = next(
                    (item for item in latest.steps if item.step_id == current_step_id),
                    None,
                )
                if current is None or current.status is not PlanStepStatus.RUNNING:
                    return
                await self._repository.progress_step_with_event(
                    user_id,
                    plan.plan_id,
                    current_step_id,
                    public_summary=message,
                    progress_phase=phase,
                    run_id=plan.plan_id,
                    now=self._clock(),
                )
                # 让出事件循环，避免长耗时工具卡住 SSE 推送阶段性 progressed。
                await asyncio.sleep(0.05)

            async def report_thinking(delta: str) -> None:
                if thinking is None:
                    return
                await thinking.push_delta(delta, channel="content")

            result = await self._registry.execute(
                VideoToolContext(
                    user_id=user_id,
                    workspace=workspace,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    credential=credential,
                    report_progress=report_progress,
                    report_thinking=report_thinking,
                ),
                step.tool_name,
                step.arguments,
            )
            plan = await self._required_plan(user_id, plan.plan_id)
            if plan.status is AgentPlanStatus.CANCELLED:
                return plan
            current = next(
                (item for item in plan.steps if item.step_id == current_step_id),
                None,
            )
            if current is None or current.status is not PlanStepStatus.RUNNING:
                return plan
            if result.workspace_patch:
                workspace = await self._repository.apply_workspace_patch(
                    user_id,
                    workspace.workspace_id,
                    result.workspace_patch,
                    expected_revision=workspace.revision,
                    now=self._clock(),
                )
            if result.pending_operation_job_ids:
                return await self._required_plan(user_id, plan.plan_id)
            await self._repository.complete_step_with_event(
                user_id,
                plan.plan_id,
                step.step_id,
                result,
                run_id=plan.plan_id,
                now=self._clock(),
            )
            plan = await self._required_plan(user_id, plan.plan_id)
            if stop_after_step_id and current_step_id == stop_after_step_id:
                # 确认 HTTP 提前返回：若已无后续步，仍需收口 COMPLETED。
                if all(
                    item.status
                    in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
                    for item in plan.steps
                ):
                    return await self._repository.update_plan_status(
                        user_id,
                        plan.plan_id,
                        AgentPlanStatus.COMPLETED,
                        now=self._clock(),
                    )
                return plan
        return await self._repository.update_plan_status(
            user_id,
            plan.plan_id,
            AgentPlanStatus.COMPLETED,
            now=self._clock(),
        )

    async def _emit_confirmation_requested(
        self,
        *,
        user_id: str,
        plan: AgentPlan,
        step: AgentPlanStep,
        workspace_payload: dict,
    ) -> None:
        """写出带 cost_summary 的确认事件，供前端即时投影追问文案。"""

        if self._event_repository is None:
            return
        preview_lines = []
        pipeline = workspace_payload.get("script_pipeline")
        if isinstance(pipeline, dict):
            start = pipeline.get("start")
            if isinstance(start, dict) and isinstance(start.get("content"), str):
                preview_lines = [
                    line.strip(" #-*")
                    for line in str(start["content"]).splitlines()
                    if line.strip() and not line.strip().startswith("```")
                ][:5]
        preview = "；".join(preview_lines) if preview_lines else ""
        if len(preview) > 420:
            preview = preview[:420].strip()
        if step.tool_name == "confirm_script_creative":
            user_text = user_latest_input(workspace_payload)
            analysis = await analyze_production_fields_with_llm(text=user_text)
            cost_summary = creative_confirm_cost_summary(
                user_text=user_text,
                preview=preview,
                missing=analysis.missing,
                duration_sec=analysis.duration_sec,
            )
        else:
            cost_summary = "该步骤会修改项目或调用计费能力，请确认后继续。"
        now = self._clock()
        events = await self._event_repository.list_events(
            user_id,
            plan.conversation_id,
        )
        sequence = 1 if not events else events[-1].sequence + 1
        event_id = f"cnf_{plan.plan_id[-12:]}_{step.step_id[-12:]}_{sequence}"
        if len(event_id) > 64:
            event_id = event_id[:64]
        event = build_confirmation_requested_event(
            event_id=event_id,
            cursor=f"c_{event_id}",
            sequence=sequence,
            conversation_id=plan.conversation_id,
            run_id=str(workspace_payload.get("active_turn_id") or plan.plan_id)[:64],
            occurred_at=now,
            step=step,
            cost_summary=cost_summary,
            confirmation_id=(
                "video_confirmation_"
                + uuid5(
                    NAMESPACE_URL,
                    f"pixelflow-video-confirmation:{plan.plan_id}:{step.step_id}",
                ).hex
            ),
        )
        try:
            await self._event_repository.create_event(user_id, event)
        except AgentRuntimeRecordConflictError:
            logger.warning(
                "确认事件写入冲突 plan_id=%s step_id=%s",
                plan.plan_id,
                step.step_id,
            )

    async def _required_plan(self, user_id: str, plan_id: str) -> AgentPlan:
        plan = await self._repository.get_plan(user_id, plan_id)
        if plan is None:
            raise AgentRuntimeRecordConflictError(
                "VideoAgent plan 不存在或不属于当前用户"
            )
        return plan
