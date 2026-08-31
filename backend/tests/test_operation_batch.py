"""验证 M5 OperationBatch 的双重幂等身份与批次上限。"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pixelflow.agent_control_plane.contracts import AgentEvent, AgentEventType
from pixelflow.agent_control_plane.persistence.models import PixelFlowOperationBatchOutboxRow
from pixelflow.agent_control_plane.persistence.repositories import MemoryAgentRuntimeRepository
from pixelflow.agent_harness.operation_batch_resume import GatewayOperationBatchResumePort
from pixelflow.agent_tools.video.contracts import VideoToolContext
from pixelflow.agent_tools.video.credential_store import TransientBatchCredentialStore
from pixelflow.agent_tools.video.credentials import TransientVideoAgentCredential
from pixelflow.agent_tools.video.scene import SceneGenerationJob
from pixelflow.operations.jobs.batch import (
    build_operation_batch_completion_event_id,
    build_operation_batch_plan,
)
from pixelflow.operations.jobs.batch_callback import (
    OperationBatchTerminalCallback,
    OperationBatchTerminalWorker,
)
from pixelflow.operations.jobs.batch_repository import MemoryOperationBatchRepository, OperationBatchOutboxRecord, SQLOperationBatchRepository
from pixelflow.operations.jobs.batch_resume import OperationBatchResumeDispatcher
from pixelflow.operations.jobs.identity import build_operation_request
from pixelflow.operations.jobs.providers import ProviderJobAdapter
from pixelflow.operations.jobs.recovery import OperationStartCoordinator
from pixelflow.operations.namespace import workflow_operation_namespace
from pixelflow.operations.ports import OperationConflictError
from pixelflow.platform.persistence import Base
from pixelflow.tasks import PixelFlowConversationRecord
from pixelflow.video.adapters.operations.scenes import (
    M06SceneGenerationBatchDispatcher,
    M06SceneGenerationBatchDispatcherWorker,
    M06SceneGenerationBatchOperationPort,
)
from pixelflow.video.contracts import VideoWorkspace
from pixelflow.video.workspace import MemoryVideoAgentRepository


def test_batch_and_child_identities_are_stable_and_distinct() -> None:
    """同一 Tool Call 重试回读同一批次，子任务仍按 scene × variant 独立区分。"""

    first = build_operation_batch_plan(
        run_id="hrun_" + "a" * 32,
        tool_call_id="tool-call-1",
        scene_ids=("scene-1", "scene-2"),
        variant_count=2,
        attempt=1,
    )
    replay = build_operation_batch_plan(
        run_id="hrun_" + "a" * 32,
        tool_call_id="tool-call-1",
        scene_ids=("scene-1", "scene-2"),
        variant_count=2,
        attempt=1,
    )

    assert first == replay
    assert len({child.operation_idempotency_key for child in first.children}) == 4
    assert build_operation_batch_completion_event_id(first.batch_id).startswith("evt_operation_batch_done_")
    assert first.children[0].operation_idempotency_key == build_operation_request(
        workflow_id=first.batch_id,
        stage=f"generate_scene:{hashlib.sha256(b'scene-1').hexdigest()[:12]}:v1",
        stage_version=1,
        attempt=1,
        provider_request={"scene_id": "scene-1"},
    ).idempotency_key
    assert build_operation_batch_plan(
        run_id="hrun_" + "a" * 32,
        tool_call_id="tool-call-1",
        scene_ids=("scene-1",),
        variant_count=1,
        attempt=1,
        batch_index=1,
    ).batch_id == build_operation_batch_plan(
        run_id="hrun_" + "a" * 32,
        tool_call_id="tool-call-1",
        scene_ids=("scene-1",),
        variant_count=1,
        attempt=1,
    ).batch_id


@pytest.mark.asyncio
async def test_dispatcher_worker_reclaims_persisted_queued_child_after_restart() -> None:
    """批次重读后只在重新获得瞬时授权时领取下一槽位，且沿用原子项幂等键。"""

    class FakeSceneOperationPort:
        def __init__(self) -> None:
            self.started: list[str] = []

        async def start_scene_variant(self, context, *, scene, variant_index, attempt, workflow_id=None, expected_operation_idempotency_key=None):
            assert context.credential is not None
            assert workflow_id is not None
            assert expected_operation_idempotency_key is not None
            self.started.append(expected_operation_idempotency_key)
            return SceneGenerationJob(
                job_id=f"job-{scene['scene_id']}-{variant_index}",
                scene_id=str(scene["scene_id"]),
                variant_index=variant_index,
                status="polling",
            )

    batches = MemoryOperationBatchRepository()
    videos = MemoryVideoAgentRepository()
    workspace = VideoWorkspace(
        workspace_id="workspace",
        conversation_id="conversation",
        revision=1,
        payload={"scenes": [{"scene_id": "scene-1"}, {"scene_id": "scene-2"}]},
    )
    await videos.create_workspace("user", workspace)
    operation_port = FakeSceneOperationPort()
    dispatcher = M06SceneGenerationBatchDispatcher(
        batch_repository=batches,
        operation_port=operation_port,  # type: ignore[arg-type] - 验证持久化 Dispatcher 边界。
        max_concurrent_child_operations_per_batch=1,
    )
    credentials = TransientBatchCredentialStore()
    batch_port = M06SceneGenerationBatchOperationPort(
        batch_repository=batches,
        dispatcher=dispatcher,
        credential_store=credentials,
    )
    context = VideoToolContext(
        user_id="user",
        workspace=workspace,
        run_id="hrun_" + "q" * 32,
        tool_call_id="tool-call-restart",
        credential=TransientVideoAgentCredential("Bearer test-only"),
    )
    batch_id, initial_jobs = await batch_port.create_or_read_batch(
        context,
        scenes=tuple(workspace.payload["scenes"]),
        variant_count=1,
        attempt=1,
    )
    assert [job.status for job in initial_jobs] == ["queued", "queued"]
    worker = M06SceneGenerationBatchDispatcherWorker(
        batch_repository=batches,
        video_repository=videos,
        dispatcher=dispatcher,
        credential_store=credentials,
        worker_id="test-restart-dispatcher",
    )
    assert await worker.run_once() == 1
    first = (await batches.get_batch_for_child_job(user_id="user", conversation_id="conversation", job_id="job-scene-1-1"))
    assert first is not None
    first_child = next(child for child in first.children if child.job_id == "job-scene-1-1")
    await batches.mark_child_terminal(
        batch_id=batch_id,
        child_key=first_child.operation_idempotency_key,
        status="succeeded",
        job_id="job-scene-1-1",
    )
    # 模拟 Gateway 重启后未重新授权：已持久化 queued 子项不被丢弃，也不越权 start。
    await credentials.aclose()
    restarted_credentials = TransientBatchCredentialStore()
    restarted_worker = M06SceneGenerationBatchDispatcherWorker(
        batch_repository=batches,
        video_repository=videos,
        dispatcher=dispatcher,
        credential_store=restarted_credentials,
        worker_id="test-restarted-dispatcher",
    )
    assert await restarted_worker.run_once() == 0
    queued = await batches.list_dispatchable_batches(limit=10)
    assert [item.batch_id for item in queued] == [batch_id]
    await restarted_credentials.put(batch_id=batch_id, authorization="Bearer fresh-user-token")
    assert await restarted_worker.run_once() == 1
    resumed = await batches.get_batch_for_child_job(user_id="user", conversation_id="conversation", job_id="job-scene-2-1")
    await restarted_credentials.aclose()
    assert resumed is not None
    assert len(operation_port.started) == 2


@pytest.mark.asyncio
async def test_sql_batch_writes_one_outbox_record_only_after_last_child(tmp_path) -> None:
    """批次唯一 Outbox 与最后子项终态同事务写入。"""

    plan = build_operation_batch_plan(run_id="hrun_" + "e" * 32, tool_call_id="tool-call-5", scene_ids=("scene-1", "scene-2"), variant_count=1, attempt=1)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLOperationBatchRepository(factory)
    try:
        batch = await repository.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
        claimed = await repository.claim_children(batch_id=batch.batch_id, max_concurrent=2)
        await repository.mark_child_terminal(batch_id=batch.batch_id, child_key=claimed[0].operation_idempotency_key, status="succeeded", job_id="job-1")
        async with factory() as session:
            assert (await session.scalars(select(PixelFlowOperationBatchOutboxRow))).all() == []
        completed = await repository.mark_child_terminal(batch_id=batch.batch_id, child_key=claimed[1].operation_idempotency_key, status="succeeded", job_id="job-2")
        async with factory() as session:
            outbox = (await session.scalars(select(PixelFlowOperationBatchOutboxRow))).all()
    finally:
        await engine.dispose()
    assert completed.completion_event_id is not None
    assert [event.completion_event_id for event in outbox] == [completed.completion_event_id]


@pytest.mark.asyncio
async def test_sql_outbox_lease_and_acknowledgement_are_idempotent(tmp_path) -> None:
    """同一批次完成事件只能被一个 Worker 确认到同一个恢复 Run。"""

    plan = build_operation_batch_plan(run_id="hrun_" + "f" * 32, tool_call_id="tool-call-6", scene_ids=("scene-1",), variant_count=1, attempt=1)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLOperationBatchRepository(factory)
    now = datetime.now(UTC)
    try:
        batch = await repository.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
        child = (await repository.claim_children(batch_id=batch.batch_id, max_concurrent=1))[0]
        await repository.mark_child_terminal(batch_id=batch.batch_id, child_key=child.operation_idempotency_key, status="succeeded", job_id="job")
        claimed = await repository.claim_completion(worker_id="worker-a", now=now, lease_duration=timedelta(seconds=30))
        assert claimed is not None
        assert await repository.claim_completion(worker_id="worker-b", now=now, lease_duration=timedelta(seconds=30)) is None
        delivered = await repository.acknowledge_completion(completion_event_id=claimed.completion_event_id, worker_id="worker-a", resume_run_id="hrun_" + "1" * 32, now=now)
        replay = await repository.acknowledge_completion(completion_event_id=claimed.completion_event_id, worker_id="worker-a", resume_run_id="hrun_" + "1" * 32, now=now)
    finally:
        await engine.dispose()
    assert delivered == replay


def test_batch_rejects_more_than_six_children() -> None:
    """6 镜头 × 1 版本是上限，超出时必须在接触 Provider 前拒绝。"""

    with pytest.raises(OperationConflictError):
        build_operation_batch_plan(
            run_id="hrun_" + "b" * 32,
            tool_call_id="tool-call-2",
            scene_ids=("1", "2", "3", "4", "5", "6", "7"),
            variant_count=1,
            attempt=1,
        )


@pytest.mark.asyncio
async def test_memory_and_sql_repositories_replay_the_same_batch(tmp_path) -> None:
    """两种 Repository 都必须按批次键回读同一子项集合。"""

    plan = build_operation_batch_plan(
        run_id="hrun_" + "c" * 32,
        tool_call_id="tool-call-3",
        scene_ids=("scene-1",),
        variant_count=2,
        attempt=1,
    )
    memory = MemoryOperationBatchRepository()
    assert await memory.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan) == await memory.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'batches.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLOperationBatchRepository(factory)
    try:
        first = await repository.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
        replay = await repository.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
    finally:
        await engine.dispose()
    assert first == replay


@pytest.mark.asyncio
async def test_batch_claim_limit_and_terminal_aggregation() -> None:
    """未取得槽位的子项保持 queued，最后一个终态才生成完成事件。"""

    plan = build_operation_batch_plan(
        run_id="hrun_" + "d" * 32,
        tool_call_id="tool-call-4",
        scene_ids=("scene-1", "scene-2", "scene-3"),
        variant_count=1,
        attempt=1,
    )
    repository = MemoryOperationBatchRepository()
    batch = await repository.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
    claimed = await repository.claim_children(batch_id=batch.batch_id, max_concurrent=2)
    assert len(claimed) == 2
    for index, child in enumerate(claimed):
        batch = await repository.mark_child_terminal(
            batch_id=batch.batch_id,
            child_key=child.operation_idempotency_key,
            status="succeeded",
            job_id=f"operation-{index}",
        )
        assert batch.completion_event_id is None
    last = (await repository.claim_children(batch_id=batch.batch_id, max_concurrent=2))[0]
    batch = await repository.mark_child_terminal(
        batch_id=batch.batch_id,
        child_key=last.operation_idempotency_key,
        status="succeeded",
        job_id="operation-last",
    )
    assert batch.status == "completed"
    assert batch.completion_event_id is not None
    replay = await repository.mark_child_terminal(
        batch_id=batch.batch_id,
        child_key=last.operation_idempotency_key,
        status="succeeded",
        job_id="operation-last",
    )
    assert replay == batch


@pytest.mark.asyncio
async def test_scene_batch_port_only_persists_queued_children_for_dispatcher_worker() -> None:
    """generate_scenes 只落库队列，Provider start 必须交给生命周期 Dispatcher Worker。"""

    class FakeSceneOperationPort:
        def __init__(self) -> None:
            self.started: list[tuple[str, int]] = []

        async def start_scene_variant(self, context, *, scene, variant_index, attempt, workflow_id=None, expected_operation_idempotency_key=None):
            del context, attempt, workflow_id, expected_operation_idempotency_key
            scene_id = str(scene["scene_id"])
            self.started.append((scene_id, variant_index))
            return SceneGenerationJob(
                job_id=f"job-{scene_id}-v{variant_index}",
                scene_id=scene_id,
                variant_index=variant_index,
                status="polling",
            )

    repository = MemoryOperationBatchRepository()
    child_port = FakeSceneOperationPort()
    dispatcher = M06SceneGenerationBatchDispatcher(
        batch_repository=repository,
        operation_port=child_port,  # type: ignore[arg-type] - 验证 Dispatcher 的稳定 Port 边界。
        max_concurrent_child_operations_per_batch=2,
    )
    port = M06SceneGenerationBatchOperationPort(
        batch_repository=repository,
        dispatcher=dispatcher,
    )
    workspace = VideoWorkspace(
        workspace_id="workspace-1",
        conversation_id="conversation",
        revision=1,
        payload={},
    )
    context = VideoToolContext(
        user_id="user",
        workspace=workspace,
        run_id="hrun_" + "z" * 32,
        tool_call_id="tool-call-batch",
        plan_id="plan-1",
        step_id="step-1",
    )
    batch_id, jobs = await port.create_or_read_batch(
        context,
        scenes=(
            {"scene_id": "scene-1"},
            {"scene_id": "scene-2"},
        ),
        variant_count=2,
        attempt=1,
    )

    assert batch_id.startswith("operation-batch-")
    assert len(jobs) == 4
    assert child_port.started == []
    assert sum(job.status == "queued" for job in jobs) == 4


@pytest.mark.asyncio
async def test_scene_batch_port_splits_arbitrary_selection_into_m06_batches() -> None:
    """Agent 可一次选择任意镜头，Gateway 按 6 个子项在 M06 内部拆批。"""

    class FakeSceneOperationPort:
        async def start_scene_variant(
            self,
            context,
            *,
            scene,
            variant_index,
            attempt,
            workflow_id=None,
            expected_operation_idempotency_key=None,
        ):
            del context, attempt, workflow_id, expected_operation_idempotency_key
            return SceneGenerationJob(
                job_id=f"job-{scene['scene_id']}-v{variant_index}",
                scene_id=str(scene["scene_id"]),
                variant_index=variant_index,
                status="polling",
            )

    repository = MemoryOperationBatchRepository()
    dispatcher = M06SceneGenerationBatchDispatcher(
        batch_repository=repository,
        operation_port=FakeSceneOperationPort(),  # type: ignore[arg-type]
        max_concurrent_child_operations_per_batch=6,
    )
    port = M06SceneGenerationBatchOperationPort(
        batch_repository=repository,
        dispatcher=dispatcher,
    )
    workspace = VideoWorkspace(
        workspace_id="workspace-long",
        conversation_id="conversation-long",
        revision=1,
        payload={},
    )
    context = VideoToolContext(
        user_id="user",
        workspace=workspace,
        run_id="hrun_" + "m" * 32,
        tool_call_id="tool-call-long",
    )
    results = await port.create_or_read_batches(
        context,
        scenes=tuple({"scene_id": str(index)} for index in range(1, 18)),
        variant_count=1,
        attempt=1,
    )

    assert [len(result.jobs) for result in results] == [6, 6, 5]
    assert len({result.batch_id for result in results}) == 3
    assert [job.scene_id for result in results for job in result.jobs] == [
        str(index) for index in range(1, 18)
    ]
    replay = await port.create_or_read_batches(
        context,
        scenes=tuple({"scene_id": str(index)} for index in range(1, 18)),
        variant_count=1,
        attempt=1,
    )
    assert replay == results


@pytest.mark.asyncio
async def test_batch_terminal_callback_projects_children_before_unique_resume_event() -> None:
    """每个子项先投影到 Workspace，最后一个终态才写批次恢复事件。"""

    plan = build_operation_batch_plan(
        run_id="hrun_" + "h" * 32,
        tool_call_id="tool-call-8",
        scene_ids=("scene-1", "scene-2"),
        variant_count=1,
        attempt=1,
    )
    batches = MemoryOperationBatchRepository()
    batch = await batches.create_or_read(
        user_id="user",
        conversation_id="conversation",
        workspace_id="workspace",
        plan=plan,
    )
    claimed = await batches.claim_children(batch_id=batch.batch_id, max_concurrent=2)
    for index, child in enumerate(claimed, start=1):
        await batches.mark_child_polling(
            batch_id=batch.batch_id,
            child_key=child.operation_idempotency_key,
            job_id=f"job-{index}",
        )
    video_repository = MemoryVideoAgentRepository()
    await video_repository.create_workspace(
        "user",
        VideoWorkspace(
            workspace_id="workspace",
            conversation_id="conversation",
            revision=1,
            payload={
                "dirty_scene_ids": ["scene-1", "scene-2"],
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "generation_jobs": [{"job_id": "job-1", "status": "polling"}],
                        "variants": [],
                    },
                    {
                        "scene_id": "scene-2",
                        "generation_jobs": [{"job_id": "job-2", "status": "polling"}],
                        "variants": [],
                    },
                ],
            },
        ),
    )
    callback = OperationBatchTerminalCallback(
        batch_repository=batches,
        video_repository=video_repository,
    )

    async def complete(job_id: str, scene_id: str, sequence: int) -> None:
        digest = hashlib.sha256(scene_id.encode()).hexdigest()[:12]
        event = AgentEvent(
            event_id=f"evt-{job_id}",
            sequence=sequence,
            cursor=f"cursor-{job_id}",
            conversation_id="conversation",
            run_id="run-operation",
            occurred_at=datetime.now(UTC),
            type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
            payload={
                "job_id": job_id,
                "workflow_id": "plan-1",
                "stage": f"generate_scene:{digest}:v1",
                "status": "succeeded",
                "result": {
                    "variant_id": f"variant-{scene_id}",
                    "artifact_ref": f"artifact:{scene_id}",
                    "video_url": f"https://cdn.example.invalid/{scene_id}.mp4",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            },
        )
        await callback.resume_external_job(
            workflow_operation_namespace("conversation", "plan-1"),
            user_id="user",
            conversation_id="conversation",
            completion_event=event,
            idempotency_key=event.event_id,
        )

    await complete("job-1", "scene-1", 1)
    middle = await batches.get_batch_for_child_job(
        user_id="user",
        conversation_id="conversation",
        job_id="job-1",
    )
    assert middle is not None
    assert middle.completion_event_id is None
    await complete("job-2", "scene-2", 2)
    completed = await batches.get_batch_for_child_job(
        user_id="user",
        conversation_id="conversation",
        job_id="job-2",
    )
    workspace = await video_repository.get_workspace("user", "workspace")
    assert completed is not None and completed.completion_event_id is not None
    assert workspace is not None
    assert all(scene["generation_jobs"][0]["status"] == "succeeded" for scene in workspace.payload["scenes"])


@pytest.mark.asyncio
async def test_batch_terminal_worker_claims_only_bound_completion_event() -> None:
    """Gateway Worker 领取已终态子项，回填工作区后只留下批次级恢复事件。"""

    class SucceededService:
        async def start(self, request, *, authorization, idempotency_key):
            del request, authorization, idempotency_key
            return {
                "job_id": "provider-job-1",
                "status": "succeeded",
                "result": {
                    "variant_id": "variant-1",
                    "artifact_ref": "artifact:scene-1",
                    "video_url": "https://cdn.example.invalid/scene-1.mp4",
                },
            }

        async def status(self, provider_job_id):
            raise AssertionError(f"同步成功 Operation 不应轮询: {provider_job_id}")

    operation_repository = MemoryAgentRuntimeRepository()
    provider_request = {"scene_id": "scene-1", "prompt": "测试镜头"}
    operation = await OperationStartCoordinator(
        operation_repository,
        adapter=ProviderJobAdapter(SucceededService()),
        user_id="user",
        conversation_id="conversation",
        job_id_factory=lambda: "job-1",
    ).start(
        build_operation_request(
            workflow_id="plan-1",
            stage=f"generate_scene:{hashlib.sha256(b'scene-1').hexdigest()[:12]}:v1",
            stage_version=1,
            attempt=1,
            provider_request=provider_request,
        ),
        provider_request=provider_request,
        authorization="Bearer test-only",
        lease_owner="test-worker",
    )
    assert operation.job_id == "job-1"
    batches = MemoryOperationBatchRepository()
    batch = await batches.create_or_read(
        user_id="user",
        conversation_id="conversation",
        workspace_id="workspace",
        plan=build_operation_batch_plan(
            run_id="hrun_" + "i" * 32,
            tool_call_id="tool-call-9",
            scene_ids=("scene-1",),
            variant_count=1,
            attempt=1,
        ),
    )
    child = (await batches.claim_children(batch_id=batch.batch_id, max_concurrent=1))[0]
    await batches.mark_child_polling(
        batch_id=batch.batch_id,
        child_key=child.operation_idempotency_key,
        job_id=operation.job_id,
    )
    video_repository = MemoryVideoAgentRepository()
    await video_repository.create_workspace(
        "user",
        VideoWorkspace(
            workspace_id="workspace",
            conversation_id="conversation",
            revision=1,
            payload={
                "dirty_scene_ids": ["scene-1"],
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "generation_jobs": [{"job_id": "job-1", "status": "polling"}],
                        "variants": [],
                    }
                ],
            },
        ),
    )
    worker = OperationBatchTerminalWorker(
        operation_repository=operation_repository,
        callback=OperationBatchTerminalCallback(
            batch_repository=batches,
            video_repository=video_repository,
        ),
        worker_id="test-batch-terminal-worker",
    )

    assert await worker.run_once() == 1
    assert await worker.run_once() == 0
    completed = await batches.get_batch_for_child_job(
        user_id="user",
        conversation_id="conversation",
        job_id="job-1",
    )
    assert completed is not None and completed.completion_event_id is not None


@pytest.mark.asyncio
async def test_resume_dispatcher_creates_one_run_for_one_batch_event(tmp_path) -> None:
    """批次完成事件是唯一恢复触发，重复扫描不得重复调用 Gateway Port。"""

    class ResumePort:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def create_operation_resume(self, event) -> str:
            self.events.append(event.completion_event_id)
            return "hrun_" + "2" * 32

    plan = build_operation_batch_plan(
        run_id="hrun_" + "g" * 32,
        tool_call_id="tool-call-7",
        scene_ids=("scene-1",),
        variant_count=1,
        attempt=1,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dispatcher.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLOperationBatchRepository(factory)
    port = ResumePort()
    now = datetime.now(UTC)
    try:
        batch = await repository.create_or_read(user_id="user", conversation_id="conversation", workspace_id="workspace", plan=plan)
        child = (await repository.claim_children(batch_id=batch.batch_id, max_concurrent=1))[0]
        await repository.mark_child_terminal(
            batch_id=batch.batch_id,
            child_key=child.operation_idempotency_key,
            status="succeeded",
            job_id="job",
        )
        dispatcher = OperationBatchResumeDispatcher(repository=repository, resume_port=port, worker_id="worker")
        delivered = await dispatcher.deliver_next(now=now)
        replay = await dispatcher.deliver_next(now=now)
    finally:
        await engine.dispose()
    assert delivered is not None
    assert replay is None
    assert port.events == [delivered.completion_event_id]


@pytest.mark.asyncio
async def test_gateway_resume_port_freezes_operation_resume_profile(monkeypatch) -> None:
    """Gateway 必须以批次 completion_event_id 创建新的 operation_resume_v1 Run。"""

    import json
    from types import SimpleNamespace

    profiles = {
        "video_interactive_v1": {"deadline_seconds": 90, "max_model_steps": 8, "max_business_tools": 3, "max_billable_batch_starts": 1},
        "operation_resume_v1": {"deadline_seconds": 150, "max_model_steps": 10, "max_business_tools": 5, "max_billable_batch_starts": 1},
        "confirmation_resume_v1": {"deadline_seconds": 150, "max_model_steps": 10, "max_business_tools": 5, "max_billable_batch_starts": 1},
        "run_recovery_v1": {"deadline_seconds": 90, "max_model_steps": 4, "max_business_tools": 0, "max_billable_batch_starts": 0},
    }
    monkeypatch.setenv("PIXELFLOW_HARNESS_RUN_LIMIT_PROFILES", json.dumps(profiles))

    class Store:
        async def get_conversation(self, conversation_id, *, user_id):
            return PixelFlowConversationRecord(conversation_id=conversation_id, user_id=user_id, title="测试会话")

    class Repository:
        async def get_workspace(self, _user_id, workspace_id):
            return VideoWorkspace(workspace_id=workspace_id, conversation_id="conversation", revision=3, payload={"scenes": []})

    class Bridge:
        request = None

        async def start(self, request):
            self.request = request
            return SimpleNamespace(run_id="hrun_" + "3" * 32)

    bridge = Bridge()
    port = GatewayOperationBatchResumePort(task_store=Store(), video_repository=Repository(), bridge=bridge)
    run_id = await port.create_operation_resume(OperationBatchOutboxRecord("evt_operation_batch_done_test", "batch", "user", "conversation", "workspace", None))

    assert run_id == "hrun_" + "3" * 32
    assert bridge.request.trigger_type == "operation_resume"
    assert bridge.request.trigger_id == "evt_operation_batch_done_test"
    assert bridge.request.limit_profile == "operation_resume_v1"
    assert bridge.request.deadline_seconds == 150
    assert bridge.request.max_output_tokens == 32_768
