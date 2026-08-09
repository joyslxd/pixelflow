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
from pixelflow.video_agent.entrypoint import VideoAgentEntrypoint
from pixelflow.video_agent.workspace.repository import MemoryVideoAgentRepository


@pytest.mark.asyncio
async def test_entrypoint_creates_recoverable_workspace_plan_and_public_event() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
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
    assert submission.plan.public_goal.startswith("处理视频创作请求")
    assert [step.tool_name for step in steps] == ["run_script_skill_stage"] * 8
    assert [step.arguments["stage"] for step in steps] == [
        "start",
        "plan",
        "characters",
        "outline",
        "episode",
        "review",
        "compliance",
        "export",
    ]
    assert steps[0].title == "选题与创作目标 /start"
    assert events[-1].type is AgentEventType.AGENT_PLAN_CREATED
    assert events[-1].payload["plan_id"] == submission.plan.plan_id


@pytest.mark.asyncio
async def test_entrypoint_seeds_product_info_from_image_materials() -> None:
    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
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
    assert [step.tool_name for step in steps] == ["run_script_skill_stage"] * 8
    assert steps[0].arguments == {"stage": "start", "creative_direction": ""}


@pytest.mark.asyncio
async def test_continue_generation_after_script_ready_does_not_reseed_skill_plan() -> None:
    """脚本就绪后「继续生成视频」不得再开 8 步脚本 Plan，也不得覆盖 latest_input。"""

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


@pytest.mark.asyncio
async def test_path_b_polish_seeds_review_compliance_export_and_user_episode() -> None:
    """路径 B：明确成稿意图 → 只种子 review/compliance/export，并注入用户 episode。"""

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
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
async def test_entrypoint_does_not_await_llm_planner_on_hot_path() -> None:
    """turns/start 必须立刻落确定性计划，不能被模型规划拖住。"""

    class SlowPlanner:
        async def plan_turn(self, context):  # noqa: ANN001, ARG002
            await asyncio.sleep(30)
            raise AssertionError("entrypoint 不应等待 LLM planner")

    runtime_repository = MemoryAgentRuntimeRepository()
    video_repository = MemoryVideoAgentRepository()
    entrypoint = VideoAgentEntrypoint(
        runtime_repository=runtime_repository,
        video_repository=video_repository,
        planner=SlowPlanner(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
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
    assert [step.tool_name for step in steps] == ["run_script_skill_stage"] * 8
    assert steps[4].title == "生成剧本正文 /episode"
    assert submission.plan.public_goal.startswith("处理视频创作请求")


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
    assert len(await runtime_repository.list_events("user-1", "conversation-1")) == 1


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
    assert workspace.payload == {
        "latest_input": "根据护肤品脚本生成视频",
        "artifact_refs": ["artifact:product-1"],
        "materials": [],
        "product_info": {},
        "script_entry_path": "create",
    }


@pytest.mark.asyncio
async def test_start_turn_merges_prior_episode_when_followup_is_short_video_request() -> None:
    """澄清短句「生成带货视频」必须带回上文成稿，不能只种子空创意 8 步。"""

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
    replay = await service.start_turn(
        user_id="user-1",
        conversation_id="conversation-route-replay",
        request=request,
    )

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
    events = await runtime_repository.list_events(
        "user-1",
        "conversation-route-upgrade",
    )
    assert AgentEventType.AGENT_PLAN_CREATED in {event.type for event in events}

