from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.contracts import AgentEventType
from pixelflow.agent_runtime.conversation_router import ConversationRouteService
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.agent_runtime.service import AgentRuntimeService
from pixelflow.intake.llm import IntentRecognitionResult
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
)
from pixelflow.video_agent.contracts import AgentPlan, AgentPlanStatus, AgentPlanStep, PlanStepStatus
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
from pixelflow.video_agent.planner.model import (
    VideoAgentPlanningContext,
    VideoPlanProposal,
    VideoPlanStepProposal,
)
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository


class StubPlanner:
    """按队列返回固定短计划，记录 plan_turn 调用。"""

    def __init__(self, proposals: list[VideoPlanProposal] | None = None) -> None:
        self.calls: list[VideoAgentPlanningContext] = []
        self._proposals = list(proposals or [])

    def enqueue(self, proposal: VideoPlanProposal) -> None:
        self._proposals.append(proposal)

    async def plan_turn(self, context: VideoAgentPlanningContext) -> AgentPlan:
        self.calls.append(context)
        proposal = self._proposals.pop(0) if self._proposals else VideoPlanProposal(
            public_goal="读取项目资料",
            steps=(
                VideoPlanStepProposal(
                    tool_name="inspect_video_workspace",
                    title="读取项目资料",
                    arguments={},
                ),
            ),
        )
        now = datetime(2026, 8, 5, tzinfo=UTC)
        from pixelflow.video_agent.entrypoint import video_agent_plan_id

        plan_id = video_agent_plan_id(context.conversation_id, context.turn_id)
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


async def _finish_deferred_video_submit(service: AgentRuntimeService, turn_id: str) -> None:
    """HTTP 返回后的后台提交：测试里需显式唤醒并排空。"""

    service.notify_registered_turn(turn_id, None)
    await service.drain_executor_notifications()


def _creative_short_plan() -> VideoPlanProposal:
    return VideoPlanProposal(
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


def _polish_short_plan() -> VideoPlanProposal:
    return VideoPlanProposal(
        public_goal="成稿自检与导出",
        steps=(
            VideoPlanStepProposal(
                tool_name="run_script_skill_stage",
                title="五维自检 /review",
                arguments={"stage": "review", "creative_direction": ""},
            ),
            VideoPlanStepProposal(
                tool_name="run_script_skill_stage",
                title="合规检查 /compliance",
                arguments={"stage": "compliance", "creative_direction": ""},
            ),
            VideoPlanStepProposal(
                tool_name="run_script_skill_stage",
                title="导出脚本产物 /export",
                arguments={"stage": "export", "creative_direction": ""},
            ),
        ),
    )


@pytest.mark.asyncio
async def test_entrypoint_creates_recoverable_workspace_plan_and_public_event() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_creative_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )

    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="我有一个护肤品脚本，帮我生成视频",
        artifact_refs=("artifact:product-1",),
    )

    workspace = await video_repository.get_workspace("user-1", submission.workspace.workspace_id)
    steps = await video_repository.list_plan_steps("user-1", submission.plan.plan_id)
    events = await runtime_repository.list_events("user-1", "conversation-1")

    assert workspace == submission.workspace
    assert workspace.payload["latest_input"] == "我有一个护肤品脚本，帮我生成视频"
    assert workspace.payload["artifact_refs"] == ["artifact:product-1"]
    assert len(planner.calls) == 1
    assert planner.calls[0].workspace_digest.get("workspace_id") == workspace.workspace_id
    assert [step.tool_name for step in steps] == [
        "run_script_skill_stage",
        "confirm_script_creative",
    ]
    assert steps[0].arguments.get("stage") == "start"
    assert steps[1].confirmation_required is True
    assert len(steps) <= 3
    assert AgentEventType.AGENT_PLAN_CREATED in {event.type for event in events}
    assert AgentEventType.AGENT_PLAN_UPDATED in {event.type for event in events}
    # 先思考后规划：不再先发空的「规划中」脚手架。
    assert not any(
        event.type is AgentEventType.AGENT_PLAN_CREATED
        and event.payload.get("public_goal") == "规划中"
        for event in events
    )
    updated = next(
        event for event in events if event.type is AgentEventType.AGENT_PLAN_UPDATED
    )
    assert updated.payload["plan_id"] == submission.plan.plan_id
    assert isinstance(updated.payload.get("steps"), list)
    assert len(updated.payload["steps"]) == len(steps)


@pytest.mark.asyncio
async def test_entrypoint_seeds_product_info_from_image_materials() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_creative_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )

    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-materials",
        turn_id="turn-materials",
        content="生成带货视频",
        artifact_refs=(),
        materials=[
            {
                "name": "鞋子.jpg",
                "type": "image",
                "mimeType": "image/jpeg",
                "url": "https://example.com/shoes.jpg",
            }
        ],
    )

    workspace = await video_repository.get_workspace(
        "user-1",
        submission.workspace.workspace_id,
    )
    steps = await video_repository.list_plan_steps("user-1", submission.plan.plan_id)

    assert workspace is not None
    assert workspace.payload["product_info"]["name"] == "鞋子"
    assert workspace.payload["product_info"]["images"][0]["url"] == (
        "https://example.com/shoes.jpg"
    )
    assert [step.tool_name for step in steps] == [
        "run_script_skill_stage",
        "confirm_script_creative",
    ]
    assert steps[0].arguments == {"stage": "start", "creative_direction": ""}
    assert steps[1].tool_name == "confirm_script_creative"
    assert steps[1].confirmation_required is True


@pytest.mark.asyncio
async def test_continue_generation_after_script_ready_does_not_reseed_skill_plan() -> None:
    """脚本就绪后「继续生成视频」不得再开长脚本 Plan，也不得覆盖 latest_input。"""

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-continue",
        turn_id="turn-script",
        content="帮我生成一分钟广告",
        artifact_refs=(),
    )
    await video_repository.apply_workspace_patch(
        "user-1",
        first.workspace.workspace_id,
        {
            "script": {
                "artifact_ref": "artifact:video-script-export-ready",
                "version": 1,
                "status": "ready",
                "content": "# 成片脚本\n镜头1",
                "review_required": False,
                "source": "skill_export",
                "missing_requirements": [],
            },
            "script_plan_confirmed": True,
        },
        expected_revision=first.workspace.revision,
        now=datetime(2026, 8, 8, tzinfo=UTC),
    )

    second = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-continue",
        turn_id="turn-continue",
        content="继续生成视频",
        artifact_refs=(),
    )

    assert second.plan.public_goal == "准备视频资产包"
    assert [step.tool_name for step in second.plan.steps] == ["inspect_video_workspace"]
    assert len(second.plan.steps) == 1
    assert second.workspace.payload["latest_input"] == "帮我生成一分钟广告"
    assert second.workspace.payload["pending_generation_request"] == "继续生成视频"
    assert second.workspace.payload["script_entry_path"] == "continue"


def test_merge_short_followup_reuses_prior_episode_script() -> None:
    from pixelflow.video_agent.entrypoint import merge_video_turn_content_with_history

    prior = (
        "# 剧本正文 /episode\n"
        "**片名**：十年之约\n**时长**：60秒\n"
        "### 镜头 01\n- **时间**：00:00-00:04\n- **景别**：特写\n"
        "- **运镜**：俯拍\n- **画面**：旧照片与蓝妹啤酒\n- **旁白**：十年后\n"
        "### 镜头 02\n- **时间**：00:04-00:08\n- **景别**：中景\n"
        "- **运镜**：固定\n- **画面**：圆桌聚会\n- **旁白**：无\n"
        "### 镜头 03\n- **时间**：00:08-00:15\n- **景别**：特写\n"
        "- **运镜**：推镜\n- **画面**：开瓶泡沫\n- **旁白**：如约而至\n"
        "### 镜头 04\n- **时间**：00:15-00:25\n- **景别**：全景\n"
        "- **运镜**：缓推\n- **画面**：碰杯 CTA\n- **行动引导**：点击购买\n"
    )
    merged = merge_video_turn_content_with_history("生成带货视频", [prior])
    assert prior in merged
    assert "【本轮指令】生成带货视频" in merged
    assert merge_video_turn_content_with_history(prior, []) == prior.strip()


def test_merge_creative_followup_reuses_fuzzy_video_brief() -> None:
    from pixelflow.video_agent.entrypoint import merge_video_turn_content_with_history

    prior = (
        "我想拍一个蓝妹视频，就是讲友谊天长地久那种，"
        "很多年以前朋友们聚餐喝蓝妹，多年以后还是喝蓝妹，但是故事要有意思点"
    )
    followup = (
        "小伍手里的拍立得吐出相纸，相纸上正是四人碰杯的瞬间，和桌上蓝妹。"
        "--这个镜头里面要加上戏剧化的转折，例如那个时候是 5 个人，现在变 4 个人了"
    )
    merged = merge_video_turn_content_with_history(followup, [prior])
    assert prior in merged
    assert "【本轮指令】" in merged
    assert "拍立得" in merged


@pytest.mark.asyncio
async def test_creative_followup_after_start_reseeds_path_a_instead_of_inspect() -> None:
    """取消创意确认后补镜头/加转折，应合并上文并由 Planner 给出短创作计划。"""

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_creative_short_plan(), _creative_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 10, tzinfo=UTC),
    )
    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-creative-revise",
        turn_id="turn-1",
        content=(
            "我想拍一个蓝妹视频，就是讲友谊天长地久那种，"
            "很多年以前朋友们聚餐喝蓝妹，多年以后还是喝蓝妹，但是故事要有意思点"
        ),
        artifact_refs=(),
    )
    workspace = first.workspace.model_copy(
        update={
            "revision": first.workspace.revision,
            "payload": {
                **first.workspace.payload,
                "script_pipeline": {
                    "start": {
                        "stage": "start",
                        "title": "选题与创作目标 /start",
                        "content": "可确认的创意方向摘要：蓝妹友谊穿越时空。",
                    }
                },
            },
        }
    )
    await video_repository.apply_workspace_patch(
        "user-1",
        workspace.workspace_id,
        {"script_pipeline": workspace.payload["script_pipeline"]},
        expected_revision=first.workspace.revision,
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )

    second = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-creative-revise",
        turn_id="turn-2",
        content=(
            "小伍手里的拍立得吐出相纸，相纸上正是四人碰杯的瞬间，和桌上蓝妹。"
            "--这个镜头里面要加上戏剧化的转折，例如那个时候是 5 个人，现在变 4 个人了"
        ),
        artifact_refs=(),
    )
    steps = await video_repository.list_plan_steps("user-1", second.plan.plan_id)

    assert second.workspace.payload["script_entry_path"] == "create"
    assert "蓝妹视频" in second.workspace.payload["latest_input"]
    assert "【本轮指令】" in second.workspace.payload["latest_input"]
    assert "拍立得" in second.workspace.payload["latest_input"]
    assert steps[0].tool_name == "run_script_skill_stage"
    assert steps[0].arguments["stage"] == "start"
    assert steps[1].tool_name == "confirm_script_creative"
    assert len(steps) <= 3
    assert len(planner.calls) == 2


@pytest.mark.asyncio
async def test_production_field_reply_while_awaiting_confirm_replans_with_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待确认期间补字段：合并脚本上下文；思考无裁决时落 inspect，不再调 Planner。"""

    from pixelflow.video_agent.thinking_stream import IntakeThinkingResult

    async def _fake_thinking(**_kwargs):  # noqa: ANN001, ARG001
        # 模拟思考流失败/无裁决，走补字段确定性降级。
        return IntakeThinkingResult(
            user_message="已完成初步判断，继续生成执行方案。",
            entry_path=None,
            needs_user_reply=False,
        )

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        assert "【本轮指令】" in text or "180" in text
        return ProductionFieldsAnalysis(
            duration_sec=180,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.stream_intake_thinking",
        _fake_thinking,
    )
    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_creative_short_plan(), _creative_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    script = (
        "# 剧本正文 /episode\n"
        "**片名**：防晒妆前\n**时长**：待确认\n"
        "### 镜头 01\n- **时间**：00:00-00:10\n- **景别**：特写\n"
        "- **运镜**：固定\n- **画面**：涂防晒\n- **旁白**：妆前第一步\n"
        "### 镜头 02\n- **时间**：00:10-00:20\n- **景别**：中景\n"
        "- **运镜**：缓推\n- **画面**：上底妆\n- **旁白**：底妆在线\n"
    )
    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-field-followup",
        turn_id="turn-1",
        content=script,
        artifact_refs=(),
    )
    await video_repository.apply_workspace_patch(
        "user-1",
        first.workspace.workspace_id,
        {
            "script": {
                "content": script,
                "missing_requirements": ["视频画幅", "结尾行动引导"],
            },
            "latest_input": script,
        },
        expected_revision=first.workspace.revision,
        now=datetime(2026, 8, 11, tzinfo=UTC),
    )
    confirm_step = next(
        step
        for step in await video_repository.list_plan_steps("user-1", first.plan.plan_id)
        if step.confirmation_required
    )
    await video_repository.request_step_confirmation(
        "user-1",
        first.plan.plan_id,
        confirm_step.step_id,
    )
    await video_repository.update_plan_status(
        "user-1",
        first.plan.plan_id,
        AgentPlanStatus.AWAITING_CONFIRMATION,
        now=datetime(2026, 8, 11, tzinfo=UTC),
    )

    second = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-field-followup",
        turn_id="turn-2",
        content="180s 9：16 结尾不需要引导",
        artifact_refs=(),
    )

    # 补字段跟进不得再调 Planner 出 import；只落 inspect + 更新 missing。
    assert len(planner.calls) == 1
    latest_input = str(second.workspace.payload.get("latest_input") or "")
    assert "【本轮指令】180s 9:16 结尾不需要引导" in latest_input
    assert "防晒妆前" in latest_input
    assert second.workspace.payload["script"]["missing_requirements"] == []
    assert "已更新" in second.plan.public_goal
    assert [step.tool_name for step in second.plan.steps] == ["inspect_video_workspace"]
    assert second.plan.public_goal != "当前有待确认步骤，请先确认或取消后再继续"


@pytest.mark.asyncio
async def test_intake_thinking_verdict_drives_inspect_without_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """思考流裁决缺字段时只追问、不落 Plan，避免与气泡文案重复。"""

    from pixelflow.video_agent.thinking_stream import IntakeThinkingResult

    async def _fake_thinking(**_kwargs):  # noqa: ANN001, ARG001
        return IntakeThinkingResult(
            user_message="脚本已收到，缺少画幅与结尾行动引导，请确认。",
            entry_path="polish",
            missing_requirements=("视频画幅", "结尾行动引导"),
            duration_sec=180,
            needs_user_reply=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.stream_intake_thinking",
        _fake_thinking,
    )
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_creative_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    result = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-thinking-verdict",
        turn_id="turn-1",
        content=(
            "# 剧本正文 /episode\n"
            "**片名**：防晒妆前\n**时长**：180秒\n"
            "### 镜头 01\n- **时间**：00:00-00:10\n- **画面**：涂防晒\n"
            "### 镜头 02\n- **时间**：00:10-00:20\n- **画面**：上底妆\n"
        ),
        artifact_refs=(),
    )
    assert len(planner.calls) == 0
    assert result.plan is None
    assert result.workspace.payload.get("awaiting_production_fields") is True
    script = result.workspace.payload.get("script")
    assert isinstance(script, dict)
    assert "防晒妆前" in str(script.get("content") or "")
    assert script.get("source") == "intake_draft"
    assert script.get("missing_requirements") == ["视频画幅", "结尾行动引导"]
    digest = result.workspace.payload.get("last_intake_thinking")
    assert isinstance(digest, dict)
    assert digest.get("needs_user_reply") is True
    assert "缺少画幅" in str(result.workspace.payload.get("last_production_fields_notice") or "")


@pytest.mark.asyncio
async def test_continue_assets_followup_goes_through_thinking_then_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「继续资产/开始生图」由 Planner 根据 Intake 上下文选择工具。"""

    from pixelflow.video_agent.thinking_stream import IntakeThinkingResult

    calls = {"n": 0}

    async def _fake_thinking(**_kwargs):  # noqa: ANN001, ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            # 首轮：先落草稿脚本，不调 Planner。
            return IntakeThinkingResult(
                user_message="脚本已收到，请确认画幅与结尾引导。",
                entry_path="polish",
                missing_requirements=("视频画幅", "结尾行动引导"),
                duration_sec=180,
                needs_user_reply=True,
            )
        return IntakeThinkingResult(
            user_message="生产字段已齐，开始准备视频资产包。",
            entry_path="continue",
            intent="continue_assets",
            missing_requirements=(),
            duration_sec=180,
            needs_user_reply=False,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.stream_intake_thinking",
        _fake_thinking,
    )
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([
        VideoPlanProposal(
            public_goal="准备视频资产包",
            steps=(
                VideoPlanStepProposal(
                    tool_name="prepare_scene_packages",
                    title="生成视频资产包",
                    arguments={},
                ),
            ),
        ),
    ])
    now = datetime(2026, 8, 11, tzinfo=UTC)
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: now,
    )
    script = (
        "# 剧本正文 /episode\n"
        "**片名**：防晒妆前\n"
        "### 镜头 01\n- **时间**：00:00-00:10\n- **画面**：涂防晒\n"
        "### 镜头 02\n- **时间**：00:10-00:20\n- **画面**：上底妆\n"
    )
    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-continue-assets",
        turn_id="turn-seed",
        content=script,
        artifact_refs=(),
    )
    assert first.plan is None
    assert isinstance(first.workspace.payload.get("script"), dict)

    await video_repository.apply_workspace_patch(
        "user-1",
        first.workspace.workspace_id,
        {
            "script": {
                **dict(first.workspace.payload["script"]),
                "missing_requirements": [],
            },
            "script_plan_confirmed": True,
            "awaiting_production_fields": False,
        },
        expected_revision=first.workspace.revision,
        now=now,
    )

    result = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-continue-assets",
        turn_id="turn-continue",
        content="继续资产吧",
        artifact_refs=(),
    )
    assert len(planner.calls) == 1
    assert result.plan is not None
    assert result.plan.public_goal == "准备视频资产包"
    assert [step.tool_name for step in result.plan.steps] == ["prepare_scene_packages"]


@pytest.mark.asyncio
async def test_intake_context_never_skips_planner_or_selects_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intake 只提供上下文；唯一 Planner 决定最终工具和计划标题。"""

    from pixelflow.video_agent.thinking_stream import IntakeThinkingResult

    answer = "字段已齐，下一步先导入成稿。"

    async def _fake_thinking(**_kwargs):  # noqa: ANN001, ARG001
        return IntakeThinkingResult(
            user_message=answer,
            entry_path="polish",
            intent="polish",
            missing_requirements=(),
            duration_sec=180,
            aspect_ratio="9:16",
            ending_cta="keep",
            needs_user_reply=False,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.entrypoint.stream_intake_thinking",
        _fake_thinking,
    )
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_polish_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    result = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-intake-plan-steps",
        turn_id="turn-1",
        content=(
            "# 剧本正文 /episode\n"
            "**片名**：防晒妆前\n**时长**：180秒\n**画幅**：9:16\n"
            "### 镜头 01\n- **时间**：00:00-00:10\n- **画面**：涂防晒\n"
            "### 镜头 02\n- **时间**：00:10-00:20\n- **画面**：上底妆\n"
            "结尾引导：进直播间下单\n"
        ),
        artifact_refs=(),
    )
    assert len(planner.calls) == 1
    assert result.plan is not None
    assert result.plan.public_goal == "成稿自检与导出"
    assert [step.tool_name for step in result.plan.steps] == [
        "run_script_skill_stage",
        "run_script_skill_stage",
        "run_script_skill_stage",
    ]
    digest = result.workspace.payload.get("last_intake_thinking")
    assert isinstance(digest, dict)
    assert digest.get("public_goal") == answer
    assert "steps" not in digest


def test_merge_turn_with_workspace_prefers_script_for_field_reply() -> None:
    from pixelflow.video_agent.contracts import VideoWorkspace
    from pixelflow.video_agent.entrypoint import _merge_turn_with_workspace_context

    now = datetime(2026, 8, 11, tzinfo=UTC)
    workspace = VideoWorkspace(
        workspace_id="ws-1",
        conversation_id="c-1",
        payload={
            "latest_input": "旧 brief",
            "script": {"content": "完整脚本正文：防晒妆前第一步"},
        },
        created_at=now,
        updated_at=now,
    )
    merged = _merge_turn_with_workspace_context(
        "180s 9:16 结尾不变",
        workspace,
    )
    assert "完整脚本正文" in merged
    assert "【本轮指令】180s 9:16 结尾不变" in merged


@pytest.mark.asyncio
async def test_path_b_polish_seeds_review_compliance_export_and_user_episode() -> None:
    """路径 B：成稿意图写入 episode 种子；Planner 给出短润色计划。"""

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_polish_short_plan()])
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=planner,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    script = (
        "这是完整脚本，请自检后导出。\n"
        "时长 60s，画幅 9:16。\n"
        "镜头1 00:00-00:08 特写 景别近景 运镜推镜 画面：手拿精华瓶 旁白：熬夜救急\n"
        "镜头2 00:08-00:20 中景 画面：涂抹面部 台词：三秒吸收\n"
        "镜头3 00:20-00:35 全景 运镜摇镜 旁白：今晚就试试\n"
        "镜头4 00:35-00:50 近景 画面：产品特写 CTA：点击购买\n"
        "镜头5 00:50-01:00 行动引导：下方小黄车\n"
    )
    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-polish",
        turn_id="turn-polish",
        content=script,
        artifact_refs=(),
    )
    steps = await video_repository.list_plan_steps("user-1", submission.plan.plan_id)
    episode = submission.workspace.payload["script_pipeline"]["episode"]

    assert submission.plan.public_goal == "成稿自检与导出"
    assert submission.workspace.payload["script_entry_path"] == "polish"
    assert [step.arguments["stage"] for step in steps] == [
        "review",
        "compliance",
        "export",
    ]
    assert len(steps) <= 3
    assert episode["source"] == "user_complete_script"
    assert "这是完整脚本" in episode["content"]


def test_structural_complete_script_routes_to_polish_without_explicit_marker() -> None:
    from pixelflow.video_agent.entrypoint import (
        _is_complete_script_polish,
        _should_seed_script_draft,
        _structural_complete_script_score,
    )

    creative = "帮我写一个护肤品带货视频脚本，一分钟左右"
    structural = (
        "60秒竖屏广告分镜脚本如下，可直接拍摄。\n"
        "镜头1 00:00-00:10 特写 景别近景 运镜推镜 画面瓶身反光 旁白熬夜急救精华\n"
        "镜头2 00:10-00:25 中景 画面涂抹脸颊 台词三秒吸收不油腻 行动引导轻拍\n"
        "镜头3 00:25-00:40 全景 运镜摇镜 旁白今晚就试试这瓶\n"
        "镜头4 00:40-00:55 近景 产品特写 CTA点击购买小黄车\n"
        "镜头5 00:55-01:00 行动引导 下方小黄车下单领取赠品\n"
        "补充：屏幕文案「熬夜急救」、品牌露出瓶身正面 logo。\n"
    )
    assert _should_seed_script_draft(creative, [])
    assert not _is_complete_script_polish(creative)
    assert _structural_complete_script_score(structural) >= 4
    assert _is_complete_script_polish(structural)


def test_continue_markers_exclude_bare_generate_video() -> None:
    from pixelflow.video_agent.entrypoint import _is_continue_video_generation

    assert _is_continue_video_generation("继续生成视频")
    assert _is_continue_video_generation("确认脚本")
    assert not _is_continue_video_generation("根据这个脚本生成视频")
    assert not _is_continue_video_generation("生成视频")


def test_multi_person_script_without_character_section_needs_full_plan() -> None:
    from pixelflow.video_agent.entrypoint import (
        analyze_script_character_readiness,
        script_needs_full_character_plan,
    )

    script = (
        "# 蓝妹啤酒十年之约\n"
        "四个朋友围坐圆桌，男1阿杰、女1程岚、男2、女2碰杯。\n"
        "镜头1 00:00-00:10 特写 景别近景 运镜推镜 画面旧照片 旁白十年后\n"
        "镜头2 00:10-00:25 中景 运镜固定 画面圆桌聚会 台词阿杰调侃\n"
        "镜头3 00:25-00:40 全景 运镜缓推 旁白如约而至 CTA点击购买\n"
        "镜头4 00:40-00:55 近景 产品特写 行动引导下方小黄车\n"
    )
    readiness = analyze_script_character_readiness(script)
    assert readiness["multi_person_cue"] is True
    assert readiness["ready"] is False
    assert script_needs_full_character_plan(script)


def test_characters_stage_with_settings_is_ready() -> None:
    from pixelflow.video_agent.entrypoint import (
        analyze_script_character_readiness,
        script_needs_full_character_plan,
    )
    from pixelflow.video_agent.contracts import VideoWorkspace
    from datetime import UTC, datetime

    characters = (
        "## 角色设定\n"
        "### 阿杰（男1）\n- 视觉形象：浅灰衬衫\n- 身份：老友\n"
        "### 程岚（女1）\n- 视觉形象：深蓝Polo\n- 身份：女主\n"
        "### 老周（男2）\n- 视觉形象：夹克\n- 身份：配角\n"
        "### 小夏（女2）\n- 视觉形象：针织衫\n- 身份：配角\n"
        "## 场景设定\n### 中餐厅\n暖光圆桌\n"
        "## 道具与产品设定\n### 蓝妹啤酒\n瓶身绿色\n"
    )
    episode = (
        "四个朋友围坐圆桌，男1阿杰、女1程岚碰杯。\n"
        "镜头1 00:00-00:10 特写 景别近景 运镜推镜 旁白十年后\n"
    )
    workspace = VideoWorkspace(
        workspace_id="ws-1",
        conversation_id="c-1",
        payload={
            "script_pipeline": {
                "characters": {"stage": "characters", "content": characters},
                "episode": {"stage": "episode", "content": episode},
            },
            "script": {
                "content": episode,
                "artifact_ref": "artifact:video-script-export-x",
                "version": 1,
                "status": "ready",
            },
        },
        created_at=datetime(2026, 8, 8, tzinfo=UTC),
        updated_at=datetime(2026, 8, 8, tzinfo=UTC),
    )
    readiness = analyze_script_character_readiness(episode, workspace=workspace)
    assert readiness["ready"] is True
    assert readiness["has_character_section"] is True
    assert not script_needs_full_character_plan(episode, workspace=workspace)


@pytest.mark.asyncio
async def test_continue_without_confirmation_does_not_enter_asset_path() -> None:
    """未确认脚本时「继续生成视频」不得走 C 成片单步。"""

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-unconfirmed",
        turn_id="turn-script",
        content="帮我生成一分钟广告",
        artifact_refs=(),
    )
    await video_repository.apply_workspace_patch(
        "user-1",
        first.workspace.workspace_id,
        {
            "script": {
                "artifact_ref": "artifact:video-script-export-ready",
                "version": 1,
                "status": "ready",
                "content": "# 成片脚本\n镜头1 单人主播讲解产品",
                "review_required": False,
                "source": "skill_export",
                "missing_requirements": [],
            }
        },
        expected_revision=first.workspace.revision,
        now=datetime(2026, 8, 8, tzinfo=UTC),
    )
    second = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-unconfirmed",
        turn_id="turn-continue",
        content="继续生成视频",
        artifact_refs=(),
    )
    assert second.workspace.payload.get("script_entry_path") == "inspect"
    assert [step.tool_name for step in second.plan.steps] == ["inspect_video_workspace"]
    assert second.plan.public_goal != "准备视频资产包"


@pytest.mark.asyncio
async def test_entrypoint_awaits_planner_with_timeout_and_falls_back_to_inspect() -> None:
    """V2.1：热路径调用 Planner；超时后仅 inspect，不得展开完整流水线。"""

    class SlowPlanner:
        async def plan_turn(self, context):  # noqa: ANN001, ARG002
            await asyncio.Event().wait()
            raise AssertionError("超时后不应继续执行")

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=SlowPlanner(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
        planning_timeout_sec=0.2,
    )

    started = datetime.now(UTC)
    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-fast",
        turn_id="turn-fast",
        content="帮我根据以上故事情节生成 60s 广告",
        artifact_refs=(),
    )
    elapsed = (datetime.now(UTC) - started).total_seconds()

    steps = await video_repository.list_plan_steps("user-1", submission.plan.plan_id)
    assert elapsed < 2
    assert [step.tool_name for step in steps] == ["inspect_video_workspace"]
    assert submission.plan.public_goal == "规划超时，先读取项目资料"


@pytest.mark.asyncio
async def test_entrypoint_without_planner_falls_back_to_inspect() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )
    submission = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-no-planner",
        turn_id="turn-1",
        content="帮我拍一支广告",
        artifact_refs=(),
    )
    assert [step.tool_name for step in submission.plan.steps] == ["inspect_video_workspace"]
    assert submission.workspace.payload.get("script_entry_path") == "create"


@pytest.mark.asyncio
async def test_entrypoint_replay_returns_existing_plan_without_duplicate_event() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )

    first = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成视频",
        artifact_refs=(),
    )
    replay = await entrypoint.submit_turn(
        user_id="user-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        content="生成视频",
        artifact_refs=(),
    )

    assert replay == first
    events = await runtime_repository.list_events("user-1", "conversation-1")
    assert [
        event.type for event in events
    ].count(AgentEventType.AGENT_PLAN_CREATED) == 1


@pytest.mark.asyncio
async def test_runtime_routes_primary_video_turn_to_v2_entrypoint_without_live_executor() -> None:
    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_entrypoint=entrypoint,
        conversation_router=ConversationRouteService(),
        primary_execution_intents=("video",),
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )
    assignment = service.assignment_for_new_conversation({})
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-v2-entry",
            user_id="user-1",
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=assignment.orchestration_version,
            context=assignment.context,
        ),
    )

    started = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-v2-entry",
        request={
            "client_input_id": "11111111-1111-4111-8111-111111111111",
            "content": "根据护肤品脚本生成视频",
            "materials": [],
            "artifact_refs": ["artifact:product-1"],
            "expected_context_version": 0,
        },
    )
    await _finish_deferred_video_submit(service, started.turn_id)

    events = await runtime_repository.list_events("user-1", "conversation-v2-entry")
    plan_event = next(event for event in events if event.type is AgentEventType.AGENT_PLAN_CREATED)
    workspace = await video_repository.get_workspace(
        "user-1",
        plan_event.payload["workspace_id"],
    )
    assert started.status == "accepted"
    assert started.orchestration_mode.value == "video_agent_v2"
    assert started.route_decision is not None
    assert started.route_decision.intent.value == "video"
    assert workspace.payload.get("latest_input") == "根据护肤品脚本生成视频"
    assert workspace.payload.get("artifact_refs") == ["artifact:product-1"]
    assert workspace.payload.get("materials") == []
    assert workspace.payload.get("product_info") == {}
    assert workspace.payload.get("script_entry_path") == "create"
    assert workspace.payload.get("active_turn_id") == started.turn_id


@pytest.mark.asyncio
async def test_start_turn_merges_prior_episode_when_followup_is_short_video_request() -> None:
    """澄清短句「生成带货视频」必须带回上文成稿，并由 Planner 给出短润色计划。"""

    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository()
    planner = StubPlanner([_polish_short_plan()])
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_entrypoint=VideoAgentEntrypoint(
            runtime_repository=runtime_repository,
            video_repository=video_repository,
            planner=planner,  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
        ),
        conversation_router=ConversationRouteService(),
        primary_execution_intents=("video",),
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    assignment = service.assignment_for_new_conversation({})
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-merge-history",
            user_id="user-1",
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=assignment.orchestration_version,
            context=assignment.context,
        ),
    )
    prior = (
        "# 剧本正文 /episode\n**片名**：十年之约\n**时长**：60秒\n"
        "### 镜头 01\n- **时间**：00:00-00:04\n- **景别**：特写\n"
        "- **运镜**：俯拍\n- **画面**：旧照片与蓝妹\n- **旁白**：十年后\n"
        "### 镜头 02\n- **时间**：00:04-00:10\n- **景别**：中景\n"
        "- **运镜**：固定\n- **画面**：圆桌聚会\n- **旁白**：无\n"
        "### 镜头 03\n- **时间**：00:10-00:20\n- **景别**：特写\n"
        "- **运镜**：推镜\n- **画面**：开瓶泡沫\n- **旁白**：如约\n"
        "### 镜头 04\n- **时间**：00:20-00:35\n- **景别**：全景\n"
        "- **运镜**：缓推\n- **画面**：碰杯 CTA\n- **行动引导**：购买\n"
    )
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="prior-episode",
            conversation_id="conversation-merge-history",
            user_id="user-1",
            role="user",
            content=prior,
            payload={},
        )
    )

    started = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-merge-history",
        request={
            "client_input_id": "22222222-2222-4222-8222-222222222222",
            "content": "生成带货视频",
            "materials": [],
            "artifact_refs": [],
            "expected_context_version": 0,
        },
    )
    await _finish_deferred_video_submit(service, started.turn_id)
    events = await runtime_repository.list_events(
        "user-1",
        "conversation-merge-history",
    )
    plan_event = next(
        event for event in events if event.type is AgentEventType.AGENT_PLAN_CREATED
    )
    workspace = await video_repository.get_workspace(
        "user-1",
        plan_event.payload["workspace_id"],
    )
    steps = await video_repository.list_plan_steps(
        "user-1",
        plan_event.payload["plan_id"],
    )

    assert started.route_decision is not None
    assert started.route_decision.intent.value == "video"
    assert workspace is not None
    assert "十年之约" in workspace.payload["latest_input"]
    assert "【本轮指令】生成带货视频" in workspace.payload["latest_input"]
    assert workspace.payload["script_entry_path"] == "polish"
    assert [step.arguments.get("stage") for step in steps] == [
        "review",
        "compliance",
        "export",
    ]
    assert len(planner.calls) == 1


@pytest.mark.asyncio
async def test_first_turn_replay_reuses_atomic_route_without_reclassifying() -> None:
    """相同客户端输入重试只能回读同一路由事件，不能再次调用模型。"""

    calls = 0

    async def classify(
        _content: str,
        _materials: list[dict[str, object]],
    ) -> IntentRecognitionResult:
        nonlocal calls
        calls += 1
        return IntentRecognitionResult(
            intent="video",
            confidence=0.9,
            llm_used=True,
        )

    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_entrypoint=VideoAgentEntrypoint(
            runtime_repository=runtime_repository,
            video_repository=video_repository,
        ),
        conversation_router=ConversationRouteService(
            llm_classifier=classify,
        ),
        primary_execution_intents=("video",),
    )
    assignment = service.assignment_for_new_conversation({})
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-route-replay",
            user_id="user-1",
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=assignment.orchestration_version,
            context=assignment.context,
        ),
    )
    request = {
        "client_input_id": "22222222-2222-4222-8222-222222222222",
        "content": "照这个做一版",
        "materials": [{"artifact_ref": "artifact:reference-1"}],
        "expected_context_version": 0,
    }

    first = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-route-replay",
        request=request,
    )
    await _finish_deferred_video_submit(service, first.turn_id)
    replay = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-route-replay",
        request=request,
    )
    await _finish_deferred_video_submit(service, replay.turn_id)

    events = await runtime_repository.list_events(
        "user-1",
        "conversation-route-replay",
    )
    assert replay == first
    assert calls == 1
    assert [
        event.type for event in events
    ].count(AgentEventType.AGENT_ROUTE_DECIDED) == 1
    assert [
        event.type for event in events
    ].count(AgentEventType.AGENT_PLAN_CREATED) == 1


@pytest.mark.asyncio
async def test_unknown_route_persists_turn_without_creating_video_plan() -> None:
    """路由失败只登记可恢复输入和澄清决定，不得创建视频业务方案。"""

    async def unavailable_classifier(
        _content: str,
        _materials: list[dict[str, object]],
    ) -> IntentRecognitionResult:
        raise RuntimeError("分类服务不可用")

    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_entrypoint=VideoAgentEntrypoint(
            runtime_repository=runtime_repository,
            video_repository=MemoryVideoAgentRepository(),
        ),
        conversation_router=ConversationRouteService(
            llm_classifier=unavailable_classifier,
        ),
        primary_execution_intents=("video",),
    )
    assignment = service.assignment_for_new_conversation({})
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-route-unknown",
            user_id="user-1",
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=assignment.orchestration_version,
            context=assignment.context,
        ),
    )

    started = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-route-unknown",
        request={
            "client_input_id": "44444444-4444-4444-8444-444444444444",
            "content": "照这个做一版",
            "materials": [{"artifact_ref": "artifact:reference-1"}],
            "expected_context_version": 0,
        },
    )

    events = await runtime_repository.list_events(
        "user-1",
        "conversation-route-unknown",
    )
    assert started.orchestration_mode.value == "frontend_v2"
    assert started.route_decision is not None
    assert started.route_decision.intent.value == "unknown"
    assert AgentEventType.AGENT_ROUTE_DECIDED in {event.type for event in events}
    assert AgentEventType.AGENT_PLAN_CREATED not in {event.type for event in events}


@pytest.mark.asyncio
async def test_unknown_route_can_upgrade_on_followup_video_request() -> None:
    """首轮澄清未知后，后续明确视频请求必须重新路由并进入 VideoAgent。"""

    async def unavailable_classifier(
        _content: str,
        _materials: list[dict[str, object]],
    ) -> IntentRecognitionResult:
        raise RuntimeError("分类服务不可用")

    task_store = MemoryPixelFlowTaskStore()
    runtime_repository = MemoryCompactionQueueRepository()
    video_repository = MemoryVideoAgentRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=runtime_repository,
        task_store=task_store,
        video_agent_entrypoint=VideoAgentEntrypoint(
            runtime_repository=runtime_repository,
            video_repository=video_repository,
        ),
        conversation_router=ConversationRouteService(
            llm_classifier=unavailable_classifier,
        ),
        primary_execution_intents=("video",),
    )
    assignment = service.assignment_for_new_conversation({})
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id="conversation-route-upgrade",
            user_id="user-1",
            orchestration_mode=assignment.orchestration_mode.value,
            orchestration_version=assignment.orchestration_version,
            context=assignment.context,
        ),
    )

    first = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-route-upgrade",
        request={
            "client_input_id": "55555555-5555-4555-8555-555555555555",
            "content": "照这个做一版",
            "materials": [],
            "expected_context_version": 0,
        },
    )
    assert first.route_decision is not None
    assert first.route_decision.intent.value == "unknown"
    assert first.orchestration_mode.value == "frontend_v2"

    second = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-route-upgrade",
        request={
            "client_input_id": "66666666-6666-4666-8666-666666666666",
            "content": "帮我生成一分钟广告",
            "materials": [],
            "expected_context_version": first.context_version,
        },
    )
    assert second.route_decision is not None
    assert second.route_decision.intent.value == "video"
    assert second.orchestration_mode.value == "video_agent_v2"
    await _finish_deferred_video_submit(service, second.turn_id)
    events = await runtime_repository.list_events(
        "user-1",
        "conversation-route-upgrade",
    )
    assert AgentEventType.AGENT_PLAN_CREATED in {event.type for event in events}
