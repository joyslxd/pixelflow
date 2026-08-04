"""统一视频输入进入 VideoAgent 的最小 P0 入口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.executor.events import build_plan_created_event
from pixelflow.video_agent.workspace.repository import VideoAgentRepository


def _stable_id(prefix: str, *parts: str) -> str:
    value = ":".join(("pixelflow-video-agent", prefix, *parts))
    return f"{prefix}_{uuid5(NAMESPACE_URL, value).hex}"


@dataclass(frozen=True)
class VideoAgentSubmission:
    workspace: VideoWorkspace
    plan: AgentPlan


class VideoAgentEntrypoint:
    """把一个已登记的用户 Turn 转换为可恢复的 VideoAgent 首计划。"""

    def __init__(
        self,
        *,
        runtime_repository: AgentRuntimeRepository,
        video_repository: VideoAgentRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._video_repository = video_repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def submit_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        content: str,
        artifact_refs: tuple[str, ...],
    ) -> VideoAgentSubmission:
        owner = user_id.strip()
        text = content.strip()
        if not owner or not conversation_id.strip() or not turn_id.strip() or not text:
            raise ValueError("VideoAgent 输入必须包含用户、对话、Turn 和内容")
        occurred_at = self._clock()
        workspace_id = _stable_id("video_workspace", conversation_id)
        workspace = await self._video_repository.create_workspace(
            owner,
            VideoWorkspace(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                payload={"latest_input": text, "artifact_refs": list(artifact_refs)},
                created_at=occurred_at,
                updated_at=occurred_at,
            ),
        )
        plan_id = _stable_id("video_plan", conversation_id, turn_id)
        plan = await self._video_repository.save_plan(
            owner,
            AgentPlan(
                plan_id=plan_id,
                workspace_id=workspace.workspace_id,
                conversation_id=conversation_id,
                status=AgentPlanStatus.PLANNING,
                public_goal="处理视频创作请求",
                created_at=occurred_at,
                updated_at=occurred_at,
            ),
            [
                AgentPlanStep(
                    step_id=_stable_id("video_step", plan_id, "inspect_workspace"),
                    plan_id=plan_id,
                    sequence=1,
                    tool_name="inspect_video_workspace",
                    title="读取项目资料",
                    status=PlanStepStatus.PENDING,
                )
            ],
        )
        events = await self._runtime_repository.list_events(owner, conversation_id)
        if not any(
            event.type.value == "agent.plan.created"
            and event.payload.get("plan_id") == plan.plan_id
            for event in events
        ):
            event_id = _stable_id("video_event", plan.plan_id, "created")
            await self._runtime_repository.create_event(
                owner,
                build_plan_created_event(
                    event_id=event_id,
                    cursor=_stable_id("video_cursor", event_id),
                    sequence=1 if not events else events[-1].sequence + 1,
                    conversation_id=conversation_id,
                    run_id=turn_id,
                    occurred_at=occurred_at,
                    plan=plan,
                ),
            )
        return VideoAgentSubmission(workspace=workspace, plan=plan)
