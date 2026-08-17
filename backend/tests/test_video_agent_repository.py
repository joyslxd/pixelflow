from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
    SQLAgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.workspace.repository import (
    MemoryVideoAgentRepository,
    SQLVideoAgentRepository,
    VideoAgentRepository,
)

RepositoryKind = Literal["memory", "sql"]
T0 = datetime(2026, 8, 4, tzinfo=UTC)
T3 = datetime(2026, 8, 4, 0, 0, 3, tzinfo=UTC)


@asynccontextmanager
async def repository(
    kind: RepositoryKind,
) -> AsyncIterator[tuple[VideoAgentRepository, MemoryAgentRuntimeRepository | SQLAgentRuntimeRepository]]:
    if kind == "memory":
        event_repository = MemoryAgentRuntimeRepository()
        yield MemoryVideoAgentRepository(event_repository=event_repository), event_repository
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        yield SQLVideoAgentRepository(session_factory), SQLAgentRuntimeRepository(session_factory)
    finally:
        await engine.dispose()


def workspace() -> VideoWorkspace:
    return VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        payload={"script": {"content": "展示商品"}},
        created_at=T0,
        updated_at=T0,
    )


def plan() -> AgentPlan:
    return AgentPlan(
        plan_id="plan-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=AgentPlanStatus.PLANNING,
        public_goal="生成商品视频",
        created_at=T0,
        updated_at=T0,
    )


def pending_step() -> AgentPlanStep:
    return AgentPlanStep(
        step_id="step-1",
        plan_id="plan-1",
        sequence=1,
        tool_name="inspect_video_workspace",
        title="读取项目",
        status=PlanStepStatus.PENDING,
    )


def confirmation_step() -> AgentPlanStep:
    return AgentPlanStep(
        step_id="step-confirmation",
        plan_id="plan-1",
        sequence=1,
        tool_name="generate_scenes",
        title="生成镜头",
        status=PlanStepStatus.PENDING,
        confirmation_required=True,
    )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_cancel_confirmation_atomically_skips_step_and_plan(
    kind: RepositoryKind,
) -> None:
    """取消确认后Memory与SQL都不能留下可再次提交的等待步骤。"""

    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [confirmation_step()])
        await store.update_plan_status(
            "user-a",
            "plan-1",
            AgentPlanStatus.RUNNING,
            now=T0,
        )
        await store.request_step_confirmation(
            "user-a",
            "plan-1",
            "step-confirmation",
        )
        await store.update_plan_status(
            "user-a",
            "plan-1",
            AgentPlanStatus.AWAITING_CONFIRMATION,
            now=T0,
        )

        cancelled = await store.cancel_step_confirmation(
            "user-a",
            "plan-1",
            "step-confirmation",
            now=T3,
        )
        replayed = await store.cancel_step_confirmation(
            "user-a",
            "plan-1",
            "step-confirmation",
            now=T3,
        )

        assert cancelled.status is AgentPlanStatus.CANCELLED
        assert cancelled.steps[0].status is PlanStepStatus.SKIPPED
        assert cancelled.steps[0].public_summary == "用户已取消执行"
        assert cancelled.steps[0].duration_ms == 0
        assert replayed == cancelled


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_cancel_quota_atomically_skips_step_plan_and_workspace_card(
    kind: RepositoryKind,
) -> None:
    """额度取消必须同步更新Plan与右侧Workspace快照，并支持幂等重放。"""

    quota_workspace = workspace().model_copy(
        update={
            "payload": {
                "quota_interrupt": {
                    "quota_interrupt_id": "quota-1",
                    "plan_id": "plan-1",
                    "step_id": "step-quota",
                    "job_id": "job-1",
                    "quota_pause_revision": 2,
                }
            }
        }
    )
    running_plan = plan().model_copy(update={"status": AgentPlanStatus.RUNNING})
    running_step = AgentPlanStep(
        step_id="step-quota",
        plan_id="plan-1",
        sequence=1,
        tool_name="generate_scenes",
        title="生成镜头",
        status=PlanStepStatus.RUNNING,
        started_at=T0,
    )
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", quota_workspace)
        await store.save_plan("user-a", running_plan, [running_step])

        cancelled = await store.cancel_quota_interrupted_plan(
            "user-a",
            "plan-1",
            "step-quota",
            quota_interrupt_id="quota-1",
            job_id="job-1",
            quota_pause_revision=2,
            now=T3,
        )
        replayed = await store.cancel_quota_interrupted_plan(
            "user-a",
            "plan-1",
            "step-quota",
            quota_interrupt_id="quota-1",
            job_id="job-1",
            quota_pause_revision=2,
            now=T3,
        )
        restored_workspace = await store.get_workspace(
            "user-a",
            "workspace-1",
        )

        assert cancelled.status is AgentPlanStatus.CANCELLED
        assert cancelled.steps[0].status is PlanStepStatus.SKIPPED
        assert replayed == cancelled
        assert restored_workspace is not None
        assert restored_workspace.payload["quota_interrupt"] is None
        assert (
            restored_workspace.payload["last_quota_resolution"]["state"]
            == "cancelled"
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_complete_step_persists_duration_and_owner_isolation(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        started = await store.start_step("user-a", "plan-1", "step-1", now=T0)
        completed = await store.complete_step(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
                artifact_refs=("artifact:workspace-1",),
            ),
            now=T3,
        )

        assert started.status is PlanStepStatus.RUNNING
        assert completed.status is PlanStepStatus.COMPLETED
        assert completed.duration_ms == 3000
        assert completed.artifact_refs == ("artifact:workspace-1",)
        assert await store.get_workspace("user-b", "workspace-1") is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_workspace_patch_uses_revision_and_replays_same_snapshot(
    kind: RepositoryKind,
) -> None:
    patch = {
        "script": {
            "source": "user_import",
            "version": 1,
            "content": "展示商品",
        }
    }
    async with repository(kind) as (store, _):
        created = await store.create_workspace("user-a", workspace())

        updated = await store.apply_workspace_patch(
            "user-a",
            created.workspace_id,
            patch,
            expected_revision=created.revision,
            now=T3,
        )
        replay = await store.apply_workspace_patch(
            "user-a",
            created.workspace_id,
            patch,
            expected_revision=created.revision,
            now=T3,
        )

        assert updated.revision == 2
        assert updated.payload["script"] == patch["script"]
        assert replay == updated
        with pytest.raises(AgentRuntimeRecordConflictError, match="revision"):
            await store.apply_workspace_patch(
                "user-a",
                created.workspace_id,
                {"script": {"source": "different"}},
                expected_revision=created.revision,
                now=T3,
            )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_apply_workspace_patch_merges_scenes_by_id_for_concurrent_generate(
    kind: RepositoryKind,
) -> None:
    """并发生成：后到的单镜补丁不得整表覆盖另一镜已回填的 video_url。"""

    async with repository(kind) as (store, _):
        created = await store.create_workspace(
            "user-a",
            workspace().model_copy(
                update={
                    "payload": {
                        "scenes": [
                            {
                                "scene_id": "scene-5",
                                "scene_index": 5,
                                "edit_status": "重新生成完成",
                                "video_url": "https://cdn.example.invalid/5.mp4",
                                "variants": [
                                    {
                                        "variant_id": "v5",
                                        "artifact_ref": "artifact:5",
                                        "video_url": "https://cdn.example.invalid/5.mp4",
                                        "selected": True,
                                    }
                                ],
                            },
                            {
                                "scene_id": "scene-6",
                                "scene_index": 6,
                                "edit_status": "重新生成中",
                                "generation_jobs": [{"job_id": "j6", "status": "polling"}],
                                "variants": [],
                            },
                        ],
                        "scene_packages": [
                            {"scene_id": "scene-5", "scene_index": 5},
                            {"scene_id": "scene-6", "scene_index": 6},
                        ],
                    }
                }
            ),
        )
        updated = await store.apply_workspace_patch(
            "user-a",
            created.workspace_id,
            {
                "scenes": [
                    {
                        "scene_id": "scene-6",
                        "scene_index": 6,
                        "edit_status": "重新生成完成",
                        "video_url": "https://cdn.example.invalid/6.mp4",
                        "variants": [
                            {
                                "variant_id": "v6",
                                "artifact_ref": "artifact:6",
                                "video_url": "https://cdn.example.invalid/6.mp4",
                                "selected": True,
                            }
                        ],
                    }
                ],
                "scene_packages": [
                    {
                        "scene_id": "scene-6",
                        "scene_index": 6,
                        "edit_status": "重新生成完成",
                        "video_url": "https://cdn.example.invalid/6.mp4",
                    }
                ],
            },
            expected_revision=created.revision,
            now=T3,
        )
        scenes = updated.payload["scenes"]
        assert isinstance(scenes, list)
        assert len(scenes) == 2
        by_id = {item["scene_id"]: item for item in scenes if isinstance(item, dict)}
        assert by_id["scene-5"]["video_url"].endswith("5.mp4")
        assert by_id["scene-6"]["video_url"].endswith("6.mp4")


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_load_conversation_state_returns_latest_plan_with_ordered_steps(
    kind: RepositoryKind,
) -> None:
    """Snapshot Repository 只返回当前用户、当前会话的权威工作区和最新计划。"""

    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        first = plan()
        await store.save_plan("user-a", first, [pending_step()])
        second = AgentPlan(
            plan_id="plan-2",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.RUNNING,
            public_goal="按新指令修改商品视频",
            created_at=T3,
            updated_at=T3,
        )
        await store.save_plan(
            "user-a",
            second,
            [
                AgentPlanStep(
                    step_id="step-2b",
                    plan_id="plan-2",
                    sequence=2,
                    tool_name="update_scene",
                    title="更新分镜",
                    status=PlanStepStatus.PENDING,
                ),
                AgentPlanStep(
                    step_id="step-2a",
                    plan_id="plan-2",
                    sequence=1,
                    tool_name="inspect_video_workspace",
                    title="读取项目",
                    status=PlanStepStatus.PENDING,
                ),
            ],
        )

        state = await store.load_conversation_state("user-a", "conversation-1")

        assert state is not None
        loaded_workspace, loaded_plan = state
        assert loaded_workspace.workspace_id == "workspace-1"
        assert loaded_plan is not None and loaded_plan.plan_id == "plan-2"
        assert [step.step_id for step in loaded_plan.steps] == ["step-2a", "step-2b"]
        assert await store.load_conversation_state("user-b", "conversation-1") is None

        history = await store.list_conversation_plans("user-a", "conversation-1")
        assert [item.plan_id for item in history] == ["plan-1", "plan-2"]
        assert [step.step_id for step in history[1].steps] == ["step-2a", "step-2b"]
        assert await store.list_conversation_plans("user-b", "conversation-1") == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_load_conversation_state_heals_dual_workspace_orphans(
    kind: RepositoryKind,
) -> None:
    """历史升级与 Entrypoint 双写时，Snapshot 应择权威并删除无 Plan 孤儿。"""

    from pixelflow.video_agent.workspace.ids import video_workspace_id_for_conversation

    conversation_id = "conversation-dual-ws"
    preferred_id = video_workspace_id_for_conversation(conversation_id)
    async with repository(kind) as (store, _):
        await store.create_workspace(
            "user-a",
            VideoWorkspace(
                workspace_id="b7a44e5b-dead-beef-0000-orphanlegacy0001",
                conversation_id=conversation_id,
                payload={"legacy_upgrade": {"from": "frontend_v2"}},
                created_at=T0,
                updated_at=T0,
            ),
        )
        await store.create_workspace(
            "user-a",
            VideoWorkspace(
                workspace_id=preferred_id,
                conversation_id=conversation_id,
                payload={"latest_input": "录入脚本", "native_agent": True},
                created_at=T3,
                updated_at=T3,
            ),
        )
        await store.save_plan(
            "user-a",
            AgentPlan(
                plan_id="plan-dual-1",
                workspace_id=preferred_id,
                conversation_id=conversation_id,
                status=AgentPlanStatus.RUNNING,
                public_goal="处理视频请求",
                created_at=T3,
                updated_at=T3,
            ),
            [],
        )

        state = await store.load_conversation_state("user-a", conversation_id)
        assert state is not None
        loaded_workspace, loaded_plan = state
        assert loaded_workspace.workspace_id == preferred_id
        assert loaded_plan is not None and loaded_plan.plan_id == "plan-dual-1"
        assert await store.get_workspace(
            "user-a",
            "b7a44e5b-dead-beef-0000-orphanlegacy0001",
        ) is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_terminal_step_completion_is_idempotent_for_same_result(kind: RepositoryKind) -> None:
    result = VideoToolResult(
        tool_name="inspect_video_workspace",
        public_summary="项目资料已读取",
    )
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])
        await store.start_step("user-a", "plan-1", "step-1", now=T0)

        first = await store.complete_step("user-a", "plan-1", "step-1", result, now=T3)
        second = await store.complete_step("user-a", "plan-1", "step-1", result, now=T3)

        assert second == first
        assert len(await store.list_plan_steps("user-a", "plan-1")) == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_step_transitions_persist_their_public_events_in_order(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, event_repository):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        started, started_event = await store.start_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            run_id="turn-1",
            now=T0,
        )
        completed, completed_event = await store.complete_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
                artifact_refs=("artifact:workspace-1",),
            ),
            run_id="turn-1",
            now=T3,
        )

        events = await event_repository.list_events("user-a", "conversation-1")
        assert started.status is PlanStepStatus.RUNNING
        assert completed.status is PlanStepStatus.COMPLETED
        assert [event.type.value for event in events] == [
            "agent.step.started",
            "agent.step.completed",
        ]
        assert [event.sequence for event in events] == [1, 2]
        assert started_event == events[0]
        assert completed_event == events[1]
        assert completed_event.payload["duration_ms"] == 3000


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_event_conflict_rolls_back_step_transition(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, event_repository):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])
        event_id = f"evt_{uuid5(NAMESPACE_URL, 'pixelflow-video-agent:step-event:plan-1:step-1:started').hex}"
        await event_repository.create_event(
            "user-b",
            AgentEvent(
                event_id=event_id,
                sequence=1,
                cursor="cursor-existing",
                conversation_id="other-conversation",
                run_id="turn-other",
                occurred_at=T0,
                type=AgentEventType.AGENT_STEP_STARTED,
                payload={"plan_id": "other-plan"},
            ),
        )

        with pytest.raises(AgentRuntimeRecordConflictError):
            await store.start_step_with_event(
                "user-a",
                "plan-1",
                "step-1",
                run_id="turn-1",
                now=T0,
            )

        assert (await store.list_plan_steps("user-a", "plan-1"))[0].status is PlanStepStatus.PENDING
        assert await event_repository.list_events("user-a", "conversation-1") == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_step_transition_event_is_idempotent(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, event_repository):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        first = await store.start_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            run_id="turn-1",
            now=T0,
        )
        replay = await store.start_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            run_id="turn-1",
            now=T0,
        )
        completed = await store.complete_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
            ),
            run_id="turn-1",
            now=T3,
        )
        completed_replay = await store.complete_step_with_event(
            "user-a",
            "plan-1",
            "step-1",
            VideoToolResult(
                tool_name="inspect_video_workspace",
                public_summary="项目资料已读取",
            ),
            run_id="turn-1",
            now=T3,
        )

        assert replay == first
        assert completed_replay == completed
        assert len(await event_repository.list_events("user-a", "conversation-1")) == 2


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_plan_read_and_status_update_use_authoritative_steps(kind: RepositoryKind) -> None:
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [pending_step()])

        running = await store.update_plan_status(
            "user-a",
            "plan-1",
            AgentPlanStatus.RUNNING,
            now=T0,
        )
        restored = await store.get_plan("user-a", "plan-1")

        assert running.status is AgentPlanStatus.RUNNING
        assert restored is not None
        assert restored.status is AgentPlanStatus.RUNNING
        assert restored.steps == (pending_step(),)
        assert await store.get_plan("user-b", "plan-1") is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_confirmation_required_step_cannot_start_without_persisted_approval(
    kind: RepositoryKind,
) -> None:
    step = AgentPlanStep(
        step_id="step-confirm",
        plan_id="plan-1",
        sequence=1,
        tool_name="generate_scenes",
        title="生成分镜",
        status=PlanStepStatus.PENDING,
        arguments={"scene_ids": ["scene-3"], "variant_count": 3},
        confirmation_required=True,
    )
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", plan(), [step])

        with pytest.raises(AgentRuntimeRecordConflictError, match="确认"):
            await store.start_step("user-a", "plan-1", "step-confirm", now=T0)

        waiting = await store.request_step_confirmation(
            "user-a",
            "plan-1",
            "step-confirm",
        )
        running = await store.confirm_step(
            "user-a",
            "plan-1",
            "step-confirm",
            now=T3,
        )
        restored = (await store.list_plan_steps("user-a", "plan-1"))[0]

        assert waiting.status is PlanStepStatus.AWAITING_CONFIRMATION
        assert waiting.started_at is None
        assert running.status is PlanStepStatus.RUNNING
        assert running.started_at == T3
        assert restored.arguments == {"scene_ids": ["scene-3"], "variant_count": 3}
        assert restored.confirmation_required is True


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_cancel_active_script_skill_plans_skips_running_and_pending(
    kind: RepositoryKind,
) -> None:
    """确认成片后应取消仍在跑的脚本 Skill 计划，跳过未完成步骤。"""

    polish = plan().model_copy(
        update={
            "status": AgentPlanStatus.RUNNING,
            "public_goal": "成稿自检与导出",
        }
    )
    steps = [
        AgentPlanStep(
            step_id="step-review",
            plan_id="plan-1",
            sequence=1,
            tool_name="run_script_skill_stage",
            title="五维自检 /review",
            status=PlanStepStatus.COMPLETED,
            arguments={"stage": "review"},
            started_at=T0,
            completed_at=T0,
        ),
        AgentPlanStep(
            step_id="step-compliance",
            plan_id="plan-1",
            sequence=2,
            tool_name="run_script_skill_stage",
            title="合规检查 /compliance",
            status=PlanStepStatus.RUNNING,
            arguments={"stage": "compliance"},
            started_at=T0,
        ),
        AgentPlanStep(
            step_id="step-export",
            plan_id="plan-1",
            sequence=3,
            tool_name="run_script_skill_stage",
            title="导出脚本产物 /export",
            status=PlanStepStatus.PENDING,
            arguments={"stage": "export"},
        ),
    ]
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", polish, steps)

        cancelled = await store.cancel_active_script_skill_plans(
            "user-a",
            "conversation-1",
            now=T3,
        )
        replayed = await store.cancel_active_script_skill_plans(
            "user-a",
            "conversation-1",
            now=T3,
        )
        restored = await store.get_plan("user-a", "plan-1")

    assert len(cancelled) == 1
    assert cancelled[0].status is AgentPlanStatus.CANCELLED
    assert restored is not None
    assert restored.status is AgentPlanStatus.CANCELLED
    assert restored.steps[0].status is PlanStepStatus.COMPLETED
    assert restored.steps[1].status is PlanStepStatus.SKIPPED
    assert restored.steps[1].public_summary == "用户已确认脚本并开始生成资产包，本步已跳过"
    assert restored.steps[2].status is PlanStepStatus.SKIPPED
    assert replayed == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_cancel_active_script_skill_plans_ignores_non_script_plans(
    kind: RepositoryKind,
) -> None:
    running = plan().model_copy(update={"status": AgentPlanStatus.RUNNING})
    step = AgentPlanStep(
        step_id="step-scene",
        plan_id="plan-1",
        sequence=1,
        tool_name="generate_scenes",
        title="生成镜头",
        status=PlanStepStatus.RUNNING,
        started_at=T0,
    )
    async with repository(kind) as (store, _):
        await store.create_workspace("user-a", workspace())
        await store.save_plan("user-a", running, [step])
        cancelled = await store.cancel_active_script_skill_plans(
            "user-a",
            "conversation-1",
            now=T3,
        )
        restored = await store.get_plan("user-a", "plan-1")

    assert cancelled == []
    assert restored is not None
    assert restored.status is AgentPlanStatus.RUNNING
    assert restored.steps[0].status is PlanStepStatus.RUNNING
