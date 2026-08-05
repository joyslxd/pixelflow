from __future__ import annotations

from collections import deque

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.planner.loop import VideoAgentPlanner, VideoAgentPlanningError
from pixelflow.video_agent.planner.model import (
    DeepSeekVideoPlanningModel,
    VideoAgentPlanningContext,
    VideoPlanProposal,
    VideoPlanStepProposal,
)
from pixelflow.video_agent.tools import InspectVideoWorkspaceTool, VideoToolRegistry


class FakePlanningModel:
    def __init__(self, proposals: list[VideoPlanProposal]) -> None:
        self.proposals = deque(proposals)
        self.feedback: list[str | None] = []

    async def propose(self, context, tool_specs, skill_manifests, feedback):
        self.feedback.append(feedback)
        return self.proposals.popleft()


def context() -> VideoAgentPlanningContext:
    return VideoAgentPlanningContext(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="读取当前视频项目",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
        ),
    )


def proposal(tool_name: str = "inspect_video_workspace") -> VideoPlanProposal:
    return VideoPlanProposal(
        public_goal="读取项目资料",
        steps=[
            VideoPlanStepProposal(
                tool_name=tool_name,
                title="读取项目",
                arguments={},
            )
        ],
    )


@pytest.mark.asyncio
async def test_planner_builds_typed_plan_from_registered_tools() -> None:
    planner = VideoAgentPlanner(
        model=FakePlanningModel([proposal()]),
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )

    plan = await planner.plan_turn(context())

    assert plan.public_goal == "读取项目资料"
    assert [step.tool_name for step in plan.steps] == ["inspect_video_workspace"]
    assert plan.steps[0].confirmation_required is False
    assert plan.steps[0].arguments == {}


@pytest.mark.asyncio
async def test_planner_repairs_unknown_tool_at_most_twice() -> None:
    model = FakePlanningModel([proposal("delete_database"), proposal()])
    planner = VideoAgentPlanner(
        model=model,
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )

    plan = await planner.plan_turn(context())

    assert plan.steps[0].tool_name == "inspect_video_workspace"
    assert model.feedback == [None, "规划包含未注册工具，请只使用服务端提供的工具"]

    broken = VideoAgentPlanner(
        model=FakePlanningModel(
            [proposal("delete_database"), proposal("bash"), proposal("shell")]
        ),
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )
    with pytest.raises(VideoAgentPlanningError, match="两次修复"):
        await broken.plan_turn(context())


@pytest.mark.asyncio
async def test_planner_rejects_more_than_eight_steps() -> None:
    oversized = VideoPlanProposal.model_construct(
        public_goal="非法超长计划",
        steps=[proposal().steps[0] for _ in range(9)],
    )
    planner = VideoAgentPlanner(
        model=FakePlanningModel([oversized, oversized, oversized]),
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )

    with pytest.raises(VideoAgentPlanningError, match="八个步骤"):
        await planner.plan_turn(context())


@pytest.mark.asyncio
async def test_deepseek_boundary_uses_structured_output_without_hidden_workspace_payload() -> None:
    captured: dict[str, object] = {}

    class StructuredModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return proposal()

    class ChatModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return StructuredModel()

    def model_factory(**kwargs):
        captured["factory"] = kwargs
        return ChatModel()

    planning_context = context().model_copy(
        update={
            "workspace": context().workspace.model_copy(
                update={"payload": {"provider_credentials": "secret-value"}}
            )
        }
    )
    model = DeepSeekVideoPlanningModel(model_factory=model_factory)

    result = await model.propose(
        planning_context,
        VideoToolRegistry([InspectVideoWorkspaceTool()]).specs(),
        (),
        None,
    )

    assert result == proposal()
    assert captured["factory"] == {
        "name": "deepseek-v4-pro",
        "thinking_enabled": False,
        "app_config": None,
    }
    assert captured["schema"] is VideoPlanProposal
    assert "secret-value" not in str(captured["messages"])
