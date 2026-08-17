"""框架 Tool：发布 1–3 步观察计划（不驱动执行）。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pixelflow.video_agent.middleware.plan import VideoPlanMiddleware


class UpdateVideoPlanStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=200)
    tool_name: str | None = Field(default=None, max_length=128)


class UpdateVideoPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=2_000)
    steps: tuple[UpdateVideoPlanStepInput, ...] = Field(min_length=1, max_length=3)

    @field_validator("steps")
    @classmethod
    def _limit_steps(
        cls,
        value: tuple[UpdateVideoPlanStepInput, ...],
    ) -> tuple[UpdateVideoPlanStepInput, ...]:
        if not value:
            raise ValueError("steps 不能为空")
        if len(value) > 3:
            raise ValueError("steps 最多 3 项")
        return value


def build_update_video_plan_tool(
    plan_middleware: VideoPlanMiddleware,
) -> StructuredTool:
    """构造 update_video_plan；只更新观察 Plan，不调用 Executor。"""

    async def _run(**kwargs: Any) -> str:
        parsed = UpdateVideoPlanInput.model_validate(kwargs)
        plan = plan_middleware.publish_plan(
            goal=parsed.goal,
            steps=[step.model_dump(mode="json") for step in parsed.steps],
            source="model",
        )
        return json.dumps(
            {
                "tool_name": "update_video_plan",
                "public_summary": f"已发布计划：{plan.goal}",
                "goal": plan.goal,
                "step_count": len(plan.steps),
                "source": plan.source,
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name="update_video_plan",
        description=(
            "为复杂任务发布 1–3 步短计划（仅用于展示进度，不会自动执行）。"
            "简单问答、澄清或单次状态读取可以不调用。"
        ),
        args_schema=UpdateVideoPlanInput,
    )
