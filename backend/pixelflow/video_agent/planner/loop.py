"""把模型提案收敛为仅含注册工具的持久化短计划。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
)
from pixelflow.video_agent.skills import SkillCatalog
from pixelflow.video_agent.tools import VideoToolRegistry

from .model import VideoAgentPlanningContext, VideoPlanningModel, VideoPlanProposal


class VideoAgentPlanningError(ValueError):
    """表示模型提案在两次修复后仍不符合受控工具合同。"""


def _stable_id(prefix: str, *parts: str) -> str:
    value = ":".join(("pixelflow-video-agent", prefix, *parts))
    return f"{prefix}_{uuid5(NAMESPACE_URL, value).hex}"


class VideoAgentPlanner:
    def __init__(
        self,
        *,
        model: VideoPlanningModel,
        registry: VideoToolRegistry,
        skill_catalog: SkillCatalog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._registry = registry
        self._skill_catalog = skill_catalog
        self._clock = clock or (lambda: datetime.now(UTC))

    async def plan_turn(self, context: VideoAgentPlanningContext) -> AgentPlan:
        manifests = (
            await asyncio.to_thread(
                self._skill_catalog.load_applicable,
                tool_names=self._registry.names(),
            )
            if self._skill_catalog is not None
            else ()
        )
        feedback: str | None = None
        last_feedback = "规划不符合受控工具合同"
        for _ in range(3):
            try:
                proposal = await self._model.propose(
                    context,
                    self._registry.specs(),
                    manifests,
                    feedback,
                )
            except ValidationError:
                last_feedback = "规划结构无效，请按计划 DTO 修正"
                feedback = last_feedback
                continue
            last_feedback = self._validate_proposal(proposal)
            if not last_feedback:
                return self._build_plan(context, proposal)
            feedback = last_feedback
        raise VideoAgentPlanningError(
            f"VideoAgent 规划经过两次修复仍不合法：{last_feedback}"
        )

    def _validate_proposal(self, proposal: VideoPlanProposal) -> str:
        if not 1 <= len(proposal.steps) <= 8:
            return "规划最多只能包含八个步骤"
        for step in proposal.steps:
            tool = self._registry.resolve(step.tool_name)
            if tool is None:
                return "规划包含未注册工具，请只使用服务端提供的工具"
            try:
                tool.spec.input_model.model_validate(step.arguments)
            except ValidationError:
                return "规划工具参数无效，请按工具 DTO 修正"
        return ""

    def _build_plan(
        self,
        context: VideoAgentPlanningContext,
        proposal: VideoPlanProposal,
    ) -> AgentPlan:
        now = self._clock()
        plan_id = _stable_id("video_plan", context.conversation_id, context.turn_id)
        steps = tuple(
            AgentPlanStep(
                step_id=_stable_id("video_step", plan_id, str(index)),
                plan_id=plan_id,
                sequence=index,
                tool_name=proposed.tool_name,
                title=proposed.title,
                status=PlanStepStatus.PENDING,
                arguments=proposed.arguments,
                confirmation_required=self._registry.resolve(
                    proposed.tool_name
                ).spec.confirmation_required,
            )
            for index, proposed in enumerate(proposal.steps, start=1)
        )
        return AgentPlan(
            plan_id=plan_id,
            workspace_id=context.workspace.workspace_id,
            conversation_id=context.conversation_id,
            status=AgentPlanStatus.PLANNING,
            public_goal=proposal.public_goal,
            steps=steps,
            created_at=now,
            updated_at=now,
        )
