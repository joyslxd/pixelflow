from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.planner.loop import VideoAgentPlanner, VideoAgentPlanningError
from pixelflow.video_agent.planner.model import (
    DeepSeekVideoPlanningModel,
    VideoAgentPlanningContext,
    VideoPlanProposal,
    VideoPlanStepProposal,
)
from pixelflow.video_agent.planner.workspace_digest import build_workspace_digest
from pixelflow.video_agent.tools import (
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    ReplaceProjectAssetsTool,
    VideoToolRegistry,
)
from pixelflow.video_agent.tools.reference import AnalyzeReferenceVideoTool
from pixelflow.video_agent.tools.script import ImportScriptTool
from pixelflow.video_agent.tools.script_skill_pipeline import (
    ConfirmScriptCreativeTool,
    RunScriptSkillStageTool,
)


class FakePlanningModel:
    def __init__(self, proposals: list[VideoPlanProposal]) -> None:
        self.proposals = deque(proposals)
        self.feedback: list[str | None] = []
        self.contexts: list[VideoAgentPlanningContext] = []

    async def propose(self, context, tool_specs, skill_manifests, feedback):
        self.contexts.append(context)
        self.feedback.append(feedback)
        return self.proposals.popleft()


def context(**overrides) -> VideoAgentPlanningContext:
    base = VideoAgentPlanningContext(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="读取当前视频项目",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
        ),
        workspace_digest={"has_script": False, "scene_count": 0},
    )
    return base.model_copy(update=overrides) if overrides else base


def proposal(*steps: VideoPlanStepProposal, public_goal: str = "读取项目资料") -> VideoPlanProposal:
    if not steps:
        steps = (
            VideoPlanStepProposal(
                tool_name="inspect_video_workspace",
                title="读取项目",
                arguments={},
            ),
        )
    return VideoPlanProposal(public_goal=public_goal, steps=steps)


def registry() -> VideoToolRegistry:
    return VideoToolRegistry(
        [
            InspectVideoWorkspaceTool(),
            ImportScriptTool(),
            RunScriptSkillStageTool(),
            ConfirmScriptCreativeTool(),
            AnalyzeReferenceVideoTool(),
            PatchSceneTool(),
            ReplaceProjectAssetsTool(),
        ]
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
    model = FakePlanningModel(
        [
            proposal(
                VideoPlanStepProposal(tool_name="delete_database", title="删库", arguments={})
            ),
            proposal(),
        ]
    )
    planner = VideoAgentPlanner(
        model=model,
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )

    plan = await planner.plan_turn(context())

    assert plan.steps[0].tool_name == "inspect_video_workspace"
    assert model.feedback == [None, "规划包含未注册工具，请只使用服务端提供的工具"]

    broken = VideoAgentPlanner(
        model=FakePlanningModel(
            [
                proposal(VideoPlanStepProposal(tool_name="delete_database", title="a", arguments={})),
                proposal(VideoPlanStepProposal(tool_name="bash", title="b", arguments={})),
                proposal(VideoPlanStepProposal(tool_name="shell", title="c", arguments={})),
            ]
        ),
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )
    with pytest.raises(VideoAgentPlanningError, match="两次修复"):
        await broken.plan_turn(context())


@pytest.mark.asyncio
async def test_planner_rejects_more_than_three_steps() -> None:
    oversized = VideoPlanProposal.model_construct(
        public_goal="非法超长计划",
        steps=[
            VideoPlanStepProposal(
                tool_name="inspect_video_workspace",
                title=f"step-{index}",
                arguments={},
            )
            for index in range(4)
        ],
    )
    planner = VideoAgentPlanner(
        model=FakePlanningModel([oversized, oversized, oversized]),
        registry=VideoToolRegistry([InspectVideoWorkspaceTool()]),
    )

    with pytest.raises(VideoAgentPlanningError, match="三个步骤"):
        await planner.plan_turn(context())


@pytest.mark.asyncio
async def test_planner_accepts_three_step_short_plan() -> None:
    short = proposal(
        VideoPlanStepProposal(
            tool_name="run_script_skill_stage",
            title="选题",
            arguments={"stage": "start", "creative_direction": ""},
        ),
        VideoPlanStepProposal(
            tool_name="confirm_script_creative",
            title="确认创意",
            arguments={},
        ),
        VideoPlanStepProposal(
            tool_name="run_script_skill_stage",
            title="结构",
            arguments={"stage": "plan", "creative_direction": ""},
        ),
        public_goal="推进脚本创作",
    )
    planner = VideoAgentPlanner(model=FakePlanningModel([short]), registry=registry())
    plan = await planner.plan_turn(context())
    assert len(plan.steps) == 3


@pytest.mark.asyncio
async def test_four_journey_inputs_select_different_registered_tools() -> None:
    """批次 A 验收：四类输入由不同已注册 Tool 组合出短 Plan。"""

    journeys = [
        (
            "帮我构思一个蓝妹友谊广告创意",
            proposal(
                VideoPlanStepProposal(
                    tool_name="run_script_skill_stage",
                    title="选题",
                    arguments={"stage": "start", "creative_direction": ""},
                ),
                VideoPlanStepProposal(
                    tool_name="confirm_script_creative",
                    title="确认创意",
                    arguments={},
                ),
                public_goal="创意到选题确认",
            ),
            ["run_script_skill_stage", "confirm_script_creative"],
        ),
        (
            "这是完整脚本，请导入并检查",
            proposal(
                VideoPlanStepProposal(
                    tool_name="import_script",
                    title="导入脚本",
                    arguments={"markdown": "# 脚本\n镜头1"},
                ),
                VideoPlanStepProposal(
                    tool_name="inspect_video_workspace",
                    title="检查工作区",
                    arguments={},
                ),
                public_goal="成熟脚本导入",
            ),
            ["import_script", "inspect_video_workspace"],
        ),
        (
            "分析参考视频并替换商品素材",
            proposal(
                VideoPlanStepProposal(
                    tool_name="analyze_reference_video",
                    title="分析参考视频",
                    arguments={"reference_asset_ref": "artifact:ref-video-1"},
                ),
                VideoPlanStepProposal(
                    tool_name="replace_project_assets",
                    title="替换商品",
                    arguments={
                        "replacements": [
                            {
                                "source_asset_ref": "artifact:old-product",
                                "target_asset_ref": "artifact:new-product",
                            }
                        ]
                    },
                ),
                public_goal="参考视频换商品",
            ),
            ["analyze_reference_video", "replace_project_assets"],
        ),
        (
            "修改第 3 镜旁白",
            proposal(
                VideoPlanStepProposal(
                    tool_name="patch_scene",
                    title="修改第3镜",
                    arguments={
                        "scene_id": "scene-3",
                        "patch": {"narration": "新旁白"},
                    },
                ),
                public_goal="单镜头修改",
            ),
            ["patch_scene"],
        ),
    ]

    for content, planned, expected_tools in journeys:
        model = FakePlanningModel([planned])
        planner = VideoAgentPlanner(model=model, registry=registry())
        plan = await planner.plan_turn(context(content=content))
        assert [step.tool_name for step in plan.steps] == expected_tools
        assert 1 <= len(plan.steps) <= 3
        assert model.contexts[0].content == content


@pytest.mark.asyncio
async def test_deepseek_boundary_uses_digest_without_hidden_workspace_payload() -> None:
    from types import SimpleNamespace

    captured: dict[str, object] = {}

    class ChatModel:
        async def astream(self, messages, **kwargs):  # noqa: ANN001, ANN003
            captured["messages"] = messages
            captured["stream"] = kwargs.get("stream")
            body = proposal().model_dump_json()
            yield SimpleNamespace(content=body, additional_kwargs={})

    def model_factory(**kwargs):
        captured["factory"] = kwargs
        return ChatModel()

    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "script": {"status": "ready", "content": "x" * 120},
                "provider_credentials": "secret-value",
                "global_assets": {"characters": [{"name": "安然"}]},
            },
        )
    )
    planning_context = context(
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={"provider_credentials": "secret-value"},
        ),
        workspace_digest=digest,
        operation_summaries=(
            {"job_id": "job-1", "stage": "scene", "status": "polling", "attempt": 1},
        ),
        intake_thinking={
            "intent": "patch_scene",
            "target_capability": "generate_scenes",
            "readiness": "ready",
            "current_state": {"scene_videos_available": True},
            "scene_ids": ["scene-2"],
            "constraints": {"dirty_scene_only": True},
        },
    )
    model = DeepSeekVideoPlanningModel(model_factory=model_factory)

    result = await model.propose(
        planning_context,
        VideoToolRegistry([InspectVideoWorkspaceTool()]).specs(),
        (),
        None,
    )

    assert result == proposal()
    assert captured["factory"]["name"] == "deepseek-v4-pro"
    assert captured["factory"]["thinking_enabled"] is False
    assert captured["factory"]["streaming"] is True
    assert captured["factory"]["app_config"] is None
    assert captured["stream"] is True
    payload = str(captured["messages"])
    assert "secret-value" not in payload
    assert "workspace_digest" in payload
    assert "operation_summaries" in payload
    assert "最多 3" in payload
    assert "output_example" in payload
    assert "import_script" in payload
    assert "target_capability" in payload
    assert "readiness" in payload
    assert "current_state" in payload
    assert "scene_ids" in payload
    assert "constraints" in payload
    assert "诊断证据" in payload


def test_workspace_digest_omits_secrets_and_counts_assets() -> None:
    digest = build_workspace_digest(
        VideoWorkspace(
            workspace_id="ws",
            conversation_id="c1",
            revision=3,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
            updated_at=datetime(2026, 8, 10, tzinfo=UTC),
            payload={
                "script": {
                    "status": "ready",
                    "content": "hello world",
                    "source": "intake_draft",
                    "missing_requirements": ["视频画幅"],
                },
                "awaiting_production_fields": True,
                "script_plan_confirmed": False,
                "script_pipeline": {"export": {"stage": "export", "content": "done"}},
                "global_assets": {
                    "characters": [{"name": "安然"}, {"name": "Yann"}],
                    "scenes": [{"name": "酒店"}],
                    "props": [{"name": "面霜"}],
                },
                "scenes": [{"id": "1"}, {"id": "2"}],
                "dirty_scene_ids": ["1"],
                "product_info": {"name": "面霜", "api_key": "leak"},
                "provider_credentials": "secret",
            },
        )
    )
    assert digest["has_script"] is True
    assert digest["script_source"] == "intake_draft"
    assert digest["awaiting_production_fields"] is True
    assert digest["script_plan_confirmed"] is False
    assert digest["script_missing_requirements"] == ["视频画幅"]
    assert digest["has_aspect_ratio"] is False
    assert digest["has_ending_cta"] is False
    assert digest["character_count"] == 2
    assert digest["scene_asset_count"] == 1
    assert digest["prop_count"] == 1
    assert digest["scene_count"] == 2
    assert digest["dirty_scene_ids"] == ["1"]
    assert digest["product_info"] == {"name": "面霜"}
    assert "provider_credentials" not in digest
    assert "api_key" not in str(digest)
