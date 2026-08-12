"""方案 1：入口路径 LLM 选择器测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
from pixelflow.video_agent.planner.entry_path import (
    EntryPathProposal,
    sanitize_entry_path_proposal,
    select_entry_path_with_llm,
    should_ask_entry_path_llm,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


def _workspace(**payload: object) -> VideoWorkspace:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    return VideoWorkspace(
        workspace_id="ws-1",
        conversation_id="c-1",
        payload=dict(payload),
        created_at=now,
        updated_at=now,
    )


def test_should_ask_entry_path_llm_only_when_rule_is_inspect_with_signal() -> None:
    empty = _workspace()
    assert (
        should_ask_entry_path_llm(
            rule_path="create",
            content="随便",
            materials=[],
            workspace=empty,
        )
        is False
    )
    assert (
        should_ask_entry_path_llm(
            rule_path="inspect",
            content="短",
            materials=[],
            workspace=empty,
        )
        is False
    )
    assert (
        should_ask_entry_path_llm(
            rule_path="inspect",
            content="小伍手里的拍立得吐出相纸，要在画面里加上戏剧化转折，例如从前是五个人，现在变成四个人了",
            materials=[],
            workspace=empty,
        )
        is True
    )
    assert (
        should_ask_entry_path_llm(
            rule_path="inspect",
            content="短",
            materials=[],
            workspace=_workspace(
                script_pipeline={"start": {"content": "蓝妹友谊主题"}},
            ),
        )
        is True
    )


def test_sanitize_blocks_continue_without_confirmation() -> None:
    ws = _workspace(
        script={"content": "成稿 " * 40},
        script_plan_confirmed=False,
    )
    assert (
        sanitize_entry_path_proposal(
            "continue",
            content="继续生成视频",
            workspace=ws,
            is_complete_script=lambda _c: False,
            has_generatable_script=lambda _w: True,
        )
        == "inspect"
    )
    ws_ok = _workspace(
        script={"content": "成稿 " * 40},
        script_plan_confirmed=True,
    )
    assert (
        sanitize_entry_path_proposal(
            "continue",
            content="继续生成视频",
            workspace=ws_ok,
            is_complete_script=lambda _c: False,
            has_generatable_script=lambda _w: True,
        )
        == "continue"
    )


def test_sanitize_polish_without_complete_script_falls_to_create() -> None:
    assert (
        sanitize_entry_path_proposal(
            "polish",
            content="加个拍立得转折",
            workspace=_workspace(),
            is_complete_script=lambda _c: False,
            has_generatable_script=lambda _w: False,
        )
        == "create"
    )


@pytest.mark.asyncio
async def test_select_entry_path_uses_model_when_rule_is_inspect() -> None:
    class FakeModel:
        async def propose(self, evidence):  # noqa: ANN001, ARG002
            return EntryPathProposal(entry_path="create", reason="改创意跟进")

    path = await select_entry_path_with_llm(
        content="小伍拍立得要加戏剧转折，五人变四人",
        materials=[],
        workspace=_workspace(latest_input="我想拍蓝妹视频讲友谊"),
        rule_path="inspect",
        model=FakeModel(),
        is_complete_script=lambda _c: False,
        has_generatable_script=lambda _w: False,
    )
    assert path == "create"


@pytest.mark.asyncio
async def test_select_entry_path_skips_model_when_rule_already_create() -> None:
    class BoomModel:
        async def propose(self, evidence):  # noqa: ANN001, ARG002
            raise AssertionError("明确 create 不应再问 LLM")

    path = await select_entry_path_with_llm(
        content="我想拍一个蓝妹视频",
        materials=[],
        workspace=_workspace(),
        rule_path="create",
        model=BoomModel(),
        is_complete_script=lambda _c: False,
        has_generatable_script=lambda _w: False,
    )
    assert path == "create"


@pytest.mark.asyncio
async def test_entrypoint_llm_can_override_inspect_to_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """entry_path LLM 可把规则 inspect 抬成 create；步骤仍由 Planner 选出。"""

    from pixelflow.video_agent.contracts import AgentPlan, AgentPlanStatus, AgentPlanStep, PlanStepStatus
    from pixelflow.video_agent.entrypoint import video_agent_plan_id
    from pixelflow.video_agent.planner.model import (
        VideoAgentPlanningContext,
        VideoPlanProposal,
        VideoPlanStepProposal,
    )
    from pixelflow.video_agent.thinking_stream import IntakeThinkingResult

    class FakeModel:
        async def propose(self, evidence):  # noqa: ANN001, ARG002
            return EntryPathProposal(entry_path="create", reason="跟进创作")

    class StubPlanner:
        def __init__(self) -> None:
            self.calls: list[VideoAgentPlanningContext] = []

        async def plan_turn(self, context: VideoAgentPlanningContext) -> AgentPlan:
            self.calls.append(context)
            now = datetime(2026, 8, 10, tzinfo=UTC)
            plan_id = video_agent_plan_id(context.conversation_id, context.turn_id)
            proposal = VideoPlanProposal(
                public_goal="处理视频创作请求",
                steps=(
                    VideoPlanStepProposal(
                        tool_name="run_script_skill_stage",
                        title="选题与创作目标 /start",
                        arguments={"stage": "start", "creative_direction": ""},
                    ),
                    VideoPlanStepProposal(
                        tool_name="confirm_script_creative",
                        title="确认选题创意",
                        arguments={},
                    ),
                ),
            )
            steps = tuple(
                AgentPlanStep(
                    step_id=f"{plan_id}-step-{index}",
                    plan_id=plan_id,
                    sequence=index,
                    tool_name=step.tool_name,
                    title=step.title,
                    status=PlanStepStatus.PENDING,
                    arguments=dict(step.arguments),
                    confirmation_required=step.tool_name == "confirm_script_creative",
                )
                for index, step in enumerate(proposal.steps, start=1)
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

    async def _fake_thinking(**_kwargs):  # noqa: ANN001, ARG001
        return IntakeThinkingResult(
            user_message="可继续创作。",
            entry_path=None,
            intent="create",
            needs_user_reply=False,
            missing_requirements=(),
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.stream_intake_thinking",
        _fake_thinking,
    )
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    now = datetime(2026, 8, 10, tzinfo=UTC)
    planner = StubPlanner()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        entry_path_model=FakeModel(),
        clock=lambda: now,
    )
    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-entry-llm",
        turn_id="turn-1",
        content="我想拍一个蓝妹视频，友谊天长地久",
        artifact_refs=(),
    )
    # 去掉规则种子词，保留选题产物，迫使第二轮规则落 inspect。
    await video_repository.apply_workspace_patch(
        "user-1",
        first.workspace.workspace_id,
        {
            "latest_input": "友谊天长地久，多年前后聚餐",
            "script_pipeline": {
                "start": {"content": "创意摘要：友谊穿越时空"},
            },
        },
        expected_revision=first.workspace.revision,
        now=now,
    )

    second = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-entry-llm",
        turn_id="turn-2",
        content="把结尾改得更有余味一些，并强调重逢时桌上那瓶酒",
        artifact_refs=(),
    )
    steps = await video_repository.list_plan_steps("user-1", second.plan.plan_id)
    assert second.workspace.payload["script_entry_path"] == "create"
    assert len(planner.calls) >= 1
    assert steps[0].arguments.get("stage") == "start"
    assert steps[1].tool_name == "confirm_script_creative"
