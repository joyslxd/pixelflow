"""Task 11 VideoAgent Operation完成事件恢复桥测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.agent_runtime.contracts import AgentEvent, AgentEventType
from pixelflow.agent_runtime.operation_namespace import (
    workflow_operation_namespace,
)
from pixelflow.agent_runtime.jobs import (
    OperationQuotaState,
    build_operation_quota_event_id,
)
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    MemoryAgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoToolResult,
    VideoWorkspace,
)
from pixelflow.video_agent.operation_resume import (
    VideoAgentOperationResumer,
    VideoAgentQuotaResumer,
)
from pixelflow.video_agent.workspace import MemoryVideoAgentRepository


class CompletingNativeResume:
    def __init__(self, repository: MemoryVideoAgentRepository, now: datetime) -> None:
        self.repository = repository
        self.now = now
        self.calls = 0

    async def __call__(
        self,
        *,
        user_id: str,
        conversation_id: str,
        plan_id: str,
        job_id: str,
        status: str,
        completion_event_id: str,
    ) -> None:
        del conversation_id, job_id, status, completion_event_id
        self.calls += 1
        plan = await self.repository.get_plan(user_id, plan_id)
        assert plan is not None
        step = next(item for item in plan.steps if item.status is PlanStepStatus.RUNNING)
        await self.repository.complete_step_with_event(
            user_id,
            plan_id,
            step.step_id,
            VideoToolResult(
                tool_name=step.tool_name,
                public_summary="参考视频解析完成",
            ),
            run_id=plan_id,
            now=self.now,
        )
        await self.repository.update_plan_status(
            user_id,
            plan_id,
            AgentPlanStatus.COMPLETED,
            now=self.now,
        )


async def _runtime_state() -> tuple[
    MemoryAgentRuntimeRepository,
    MemoryVideoAgentRepository,
    datetime,
]:
    now = datetime(2026, 8, 6, 9, tzinfo=UTC)
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "reference_videos": [
                    {
                        "job_id": "job-1",
                        "plan_step_id": "step-1",
                        "status": "polling",
                    }
                ]
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.RUNNING,
            created_at=now,
            updated_at=now,
        ),
        [
            AgentPlanStep(
                step_id="step-1",
                plan_id="plan-1",
                sequence=1,
                tool_name="analyze_reference_video",
                title="解析参考视频",
                status=PlanStepStatus.RUNNING,
                started_at=now,
            )
        ],
    )
    return event_repository, repository, now


def _completion_event(status: str, now: datetime) -> AgentEvent:
    return AgentEvent(
        event_id="evt-completion-1",
        sequence=1,
        cursor="cursor-completion-1",
        conversation_id="conversation-1",
        run_id="run-completion-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-1",
            "workflow_id": "plan-1",
            "stage": "analyze_reference:0123456789abcdef",
            "status": status,
        },
    )


@pytest.mark.asyncio
async def test_success_completion_resumes_original_plan_without_credential() -> None:
    _events, repository, now = await _runtime_state()
    native_resume = CompletingNativeResume(repository, now)
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
        native_resume=native_resume,
    )

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("succeeded", now),
        idempotency_key="evt-completion-1",
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("succeeded", now),
        idempotency_key="evt-completion-1",
    )

    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    assert plan.status is AgentPlanStatus.COMPLETED
    assert native_resume.calls == 1


@pytest.mark.asyncio
async def test_failed_completion_atomically_fails_bound_step_and_plan() -> None:
    events, repository, now = await _runtime_state()
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
    )

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("timeout", now),
        idempotency_key="evt-completion-1",
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=_completion_event("timeout", now),
        idempotency_key="evt-completion-1",
    )

    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    assert plan.status is AgentPlanStatus.FAILED
    assert plan.steps[0].status is PlanStepStatus.FAILED
    failure_events = [
        event
        for event in await events.list_events("user-1", "conversation-1")
        if event.type is AgentEventType.AGENT_STEP_FAILED
    ]
    assert len(failure_events) == 1
    assert failure_events[0].payload["reason_code"] == "provider_timeout"


@pytest.mark.asyncio
async def test_completion_rejects_cross_owner_and_unbound_step() -> None:
    _events, repository, now = await _runtime_state()
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(AgentRuntimeRecordConflictError):
        await resumer.resume_external_job(
            workflow_operation_namespace("conversation-1", "plan-1"),
            user_id="user-other",
            conversation_id="conversation-1",
            completion_event=_completion_event("succeeded", now),
            idempotency_key="evt-completion-1",
        )


def _quota_event(
    state: OperationQuotaState,
    now: datetime,
) -> AgentEvent:
    event_id = build_operation_quota_event_id("job-1", 1, state)
    return AgentEvent(
        event_id=event_id,
        sequence=2 if state is OperationQuotaState.PAUSED else 3,
        cursor=f"cursor-{state.value}",
        conversation_id="conversation-1",
        run_id=f"run-{state.value}",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED,
        payload={
            "job_id": "job-1",
            "workflow_id": "plan-1",
            "stage": "analyze_reference:0123456789abcdef",
            "stage_version": 1,
            "attempt": 1,
            "quota_pause_revision": 1,
            "quota_state": state.value,
            "reason_code": (
                "provider_quota_insufficient"
                if state is OperationQuotaState.PAUSED
                else "provider_quota_resume_authorized"
            ),
        },
    )


@pytest.mark.asyncio
async def test_quota_pause_and_resume_project_same_bound_step() -> None:
    _events, repository, now = await _runtime_state()
    resumer = VideoAgentQuotaResumer(repository=repository)
    paused = _quota_event(OperationQuotaState.PAUSED, now)
    resumed = _quota_event(OperationQuotaState.RESUMED, now)

    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=paused,
        idempotency_key=paused.event_id,
    )
    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=paused,
        idempotency_key=paused.event_id,
    )
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    assert workspace.payload["quota_interrupt"] == {
        "quota_interrupt_id": paused.event_id,
        "plan_id": "plan-1",
        "step_id": "step-1",
        "job_id": "job-1",
        "quota_pause_revision": 1,
        "state": "paused",
        "reason_code": "provider_quota_insufficient",
    }

    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=resumed,
        idempotency_key=resumed.event_id,
    )
    await resumer.resume_external_job_quota(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        quota_event=resumed,
        idempotency_key=resumed.event_id,
    )
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    assert workspace.payload["quota_interrupt"] is None
    assert workspace.payload["last_quota_resolution"]["event_id"] == resumed.event_id


class RecordingNativeResume:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(
        self,
        *,
        user_id: str,
        conversation_id: str,
        plan_id: str,
        job_id: str,
        status: str,
        completion_event_id: str,
    ) -> None:
        del user_id, conversation_id, plan_id, status, completion_event_id
        self.calls.append(job_id)


@pytest.mark.asyncio
async def test_generate_scene_batch_projects_each_job_before_native_resume() -> None:
    now = datetime(2026, 8, 14, 9, tzinfo=UTC)
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "dirty_scene_ids": ["scene-1", "scene-2"],
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "edit_status": "重新生成中",
                        "generation_jobs": [
                            {
                                "job_id": "job-a",
                                "status": "polling",
                                "plan_step_id": "step-1",
                                "variant_index": 1,
                            }
                        ],
                        "variants": [],
                    },
                    {
                        "scene_id": "scene-2",
                        "scene_index": 2,
                        "edit_status": "重新生成中",
                        "generation_jobs": [
                            {
                                "job_id": "job-b",
                                "status": "polling",
                                "plan_step_id": "step-1",
                                "variant_index": 1,
                            }
                        ],
                        "variants": [],
                    },
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.RUNNING,
            created_at=now,
            updated_at=now,
        ),
        [
            AgentPlanStep(
                step_id="step-1",
                plan_id="plan-1",
                sequence=1,
                tool_name="generate_scenes",
                title="生成分镜视频",
                status=PlanStepStatus.RUNNING,
                started_at=now,
            )
        ],
    )
    native_resume = RecordingNativeResume()
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
        native_resume=native_resume,
    )

    first = AgentEvent(
        event_id="evt-scene-a",
        sequence=1,
        cursor="cursor-scene-a",
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-a",
            "workflow_id": "plan-1",
            "stage": "generate_scene:abcdef012345:v1",
            "status": "succeeded",
            "result": {
                "variant_id": "variant-a",
                "artifact_ref": "artifact:scene-a",
                "video_url": "https://cdn.example.invalid/a.mp4",
            },
        },
    )
    second = AgentEvent(
        event_id="evt-scene-b",
        sequence=2,
        cursor="cursor-scene-b",
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-b",
            "workflow_id": "plan-1",
            "stage": "generate_scene:fedcba543210:v1",
            "status": "succeeded",
            "result": {
                "variant_id": "variant-b",
                "artifact_ref": "artifact:scene-b",
                "video_url": "https://cdn.example.invalid/b.mp4",
            },
        },
    )

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=first,
        idempotency_key="evt-scene-a",
    )
    assert native_resume.calls == []
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    assert workspace.payload["scene_video_progress"]["completed"] == 1
    plan = await repository.get_plan("user-1", "plan-1")
    assert plan is not None
    assert plan.steps[0].status is PlanStepStatus.RUNNING

    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=second,
        idempotency_key="evt-scene-b",
    )
    assert native_resume.calls == ["job-b"]
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    assert workspace.payload["scene_video_progress"]["completed"] == 2
    assert workspace.payload["scene_video_progress"]["total"] == 2


@pytest.mark.asyncio
async def test_generate_scene_completion_hydrates_by_stage_digest_without_job_binding() -> None:
    """旧冲突：Workspace 无 generation_jobs 绑定时，仍按 stage digest 回填并 ack。"""
    import hashlib

    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    scene_id = "scene-unbound-1"
    digest = hashlib.sha256(scene_id.encode()).hexdigest()[:12]
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "scenes": [
                    {
                        "scene_id": scene_id,
                        "scene_index": 1,
                        "edit_status": "重新生成中",
                        "generation_jobs": [],
                        "variants": [],
                    }
                ]
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        ),
        [
            AgentPlanStep(
                step_id="step-1",
                plan_id="plan-1",
                sequence=1,
                tool_name="generate_scenes",
                title="生成分镜视频",
                status=PlanStepStatus.COMPLETED,
                started_at=now,
                completed_at=now,
            )
        ],
    )
    native_resume = RecordingNativeResume()
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
        native_resume=native_resume,
    )
    event = AgentEvent(
        event_id="evt-unbound",
        sequence=1,
        cursor="cursor-unbound",
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-unbound",
            "workflow_id": "plan-1",
            "stage": f"generate_scene:{digest}:v1",
            "status": "succeeded",
            "result": {
                "variant_id": "variant-u",
                "artifact_ref": "artifact:u",
                "video_url": "https://cdn.example.invalid/u.mp4",
            },
        },
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=event,
        idempotency_key="evt-unbound",
    )
    assert native_resume.calls == []
    workspace = await repository.get_workspace("user-1", "workspace-1")
    assert workspace is not None
    scene = workspace.payload["scenes"][0]
    assert scene["variants"][0]["video_url"].endswith("u.mp4")
    assert workspace.payload["scene_video_progress"]["completed"] == 1


@pytest.mark.asyncio
async def test_prepare_completion_acks_when_plan_missing() -> None:
    """Plan 已被覆盖时，旧 prepare 成功事件应直接 ack，不再冲突刷屏。"""
    now = datetime(2026, 8, 14, 11, tzinfo=UTC)
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "scene_package_job": {
                    "job_id": "job-prepare-old",
                    "plan_step_id": "step-missing",
                    "status": "succeeded",
                },
                "scene_packages": [{"scene_id": "scene-1", "scene_index": 1}],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
        native_resume=RecordingNativeResume(),
    )
    event = AgentEvent(
        event_id="evt-prepare-orphan",
        sequence=1,
        cursor="cursor-prepare-orphan",
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-prepare-old",
            "workflow_id": "plan-does-not-exist",
            "stage": "prepare_scene_packages:abcdef012345",
            "status": "succeeded",
        },
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-does-not-exist"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=event,
        idempotency_key="evt-prepare-orphan",
    )


@pytest.mark.asyncio
async def test_failed_assets_completion_acks_when_step_not_running() -> None:
    """旧 generate_scene_assets failed 事件在步骤已收口时必须 soft-ack，避免饿死分镜轮询。"""
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    event_repository = MemoryAgentRuntimeRepository()
    repository = MemoryVideoAgentRepository(event_repository=event_repository)
    await repository.create_workspace(
        "user-1",
        VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={
                "scene_asset_job": {
                    "job_id": "job-assets-old",
                    "plan_step_id": "step-assets",
                    "status": "failed",
                },
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await repository.save_plan(
        "user-1",
        AgentPlan(
            plan_id="plan-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=AgentPlanStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        ),
        [
            AgentPlanStep(
                step_id="step-assets",
                plan_id="plan-1",
                sequence=1,
                tool_name="generate_scene_assets",
                title="生成参考图",
                status=PlanStepStatus.FAILED,
                started_at=now,
                completed_at=now,
            )
        ],
    )
    native_resume = RecordingNativeResume()
    resumer = VideoAgentOperationResumer(
        repository=repository,
        executor=object(),  # type: ignore[arg-type]
        native_resume=native_resume,
    )
    event = AgentEvent(
        event_id="evt-assets-failed-stale",
        sequence=1,
        cursor="cursor-assets-failed-stale",
        conversation_id="conversation-1",
        run_id="run-1",
        occurred_at=now,
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={
            "job_id": "job-assets-old",
            "workflow_id": "plan-1",
            "stage": "generate_scene_assets:0d757be44d898922",
            "status": "failed",
            "reason_code": "provider_business_failed",
        },
    )
    await resumer.resume_external_job(
        workflow_operation_namespace("conversation-1", "plan-1"),
        user_id="user-1",
        conversation_id="conversation-1",
        completion_event=event,
        idempotency_key="evt-assets-failed-stale",
    )
    assert native_resume.calls == []
