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
from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowConversationRecord
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
    assert [step.tool_name for step in steps] == [
        "inspect_video_workspace",
        "brainstorm_script",
    ]
    assert steps[1].title == "生成带货脚本草稿"
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
    assert [step.tool_name for step in steps] == [
        "inspect_video_workspace",
        "brainstorm_script",
    ]
    assert steps[1].arguments["product_info"]["name"] == "鞋子"


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
    assert [step.tool_name for step in steps] == [
        "inspect_video_workspace",
        "brainstorm_script",
    ]
    assert steps[1].title == "生成广告脚本草稿"
    assert submission.plan.public_goal.startswith("处理视频创作请求")

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
    }


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
