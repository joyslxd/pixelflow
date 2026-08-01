"""视频 live Runtime Repository 的 Memory/SQL 同构合同测试。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Literal
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import null
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import ORMExecuteState, Session

from deerflow.persistence.base import Base
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentEventType,
    AgentIntent,
    ExternalJobStatus,
    InterruptResponseRequest,
    TurnRecord,
    TurnStatus,
    WorkflowStatus,
)
from pixelflow.agent_runtime.graph import WorkflowCommand, workflow_namespace
from pixelflow.agent_runtime.identity import conversation_message_id
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryVideoRuntimeRepository,
    OperationRecord,
    PixelFlowAgentInterruptRow,
    SQLVideoRuntimeRepository,
    StoredAgentInterrupt,
    SupervisorProjectionMessage,
    TurnExecutionLeaseConflictError,
    VideoRuntimeRepository,
    VideoTurnCommit,
    VideoWorkflowStateConflictError,
)
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
)
from pixelflow.agent_workflows.video import (
    VideoLiveWorkflowHandler,
    VideoPlanningWorkflowService,
    encode_video_workflow_state,
    project_video_workflow_state,
)
from pixelflow.tasks import (
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    PixelFlowConversationRecord,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)

RepositoryKind = Literal["memory", "sql"]
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
OWNER = "user-1"
CONVERSATION = "conversation-1"
WORKFLOW = "workflow-1"


def _decision(
    *,
    action: AgentAction = AgentAction.START_WORKFLOW,
    action_key: str = "action-1",
) -> ActionDecision:
    return ActionDecision(
        action=action,
        intent=AgentIntent.VIDEO,
        target_workflow_id=WORKFLOW,
        target_stage="intake",
        confidence=1,
        clarification_question=("请确认是否继续？" if action is AgentAction.CLARIFY else None),
        reason_code="video_action_confirmed",
        idempotency_key=action_key,
    )


def _turn(
    index: int,
    *,
    user_suffix: str = "",
    conversation_id: str = CONVERSATION,
    status: TurnStatus = TurnStatus.ACCEPTED,
) -> TurnRecord:
    return TurnRecord(
        turn_id=f"turn-{index}{user_suffix}",
        conversation_id=conversation_id,
        client_input_id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
        status=status,
        target_workflow_id=None,
        decision=None,
        expected_context_version=0,
        created_at=NOW + timedelta(seconds=index),
    )


def _workflow_state(
    *,
    version: int = 1,
    action_key: str = "action-1",
    turn_id: str = "turn-1",
    workflow_id: str = WORKFLOW,
    conversation_id: str = CONVERSATION,
):
    state = VideoPlanningWorkflowService().start(
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        intent="video",
        intake_context={"nested": {"items": ["a", {"value": 1}]}},
        now=NOW,
    )
    envelope = encode_video_workflow_state(
        user_id=OWNER,
        state=state,
        workflow_version=version,
        last_turn_id=turn_id,
        last_action_key=action_key,
    )
    return envelope, project_video_workflow_state(state)


def _message(
    *,
    message_id: str = "message-1",
    run_id: str = "turn-1",
    conversation_id: str = CONVERSATION,
    content: str = "已完成视频需求登记。",
) -> SupervisorProjectionMessage:
    return SupervisorProjectionMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        run_id=run_id,
        role="assistant",
        content=content,
        payload={"artifact": {"refs": ["artifact-1"]}},
        created_at=NOW + timedelta(seconds=10),
    )


def _interrupt(
    *,
    turn_id: str = "turn-1",
    interrupt_id: str = "interrupt-1",
    status: Literal["open", "responded", "closed"] = "open",
) -> StoredAgentInterrupt:
    response_id = (
        UUID("10000000-0000-0000-0000-000000000001")
        if status == "responded"
        else None
    )
    response = (
        {
            "client_response_id": str(response_id),
            "value": {
                "content": "确认继续",
                "materials": [],
                "artifact_refs": [],
            },
        }
        if response_id is not None
        else None
    )
    return StoredAgentInterrupt(
        interrupt_id=interrupt_id,
        conversation_id=CONVERSATION,
        workflow_id=WORKFLOW,
        turn_id=turn_id,
        kind="confirmation",
        reason_code="plan_review_required",
        payload={"ui": {"buttons": ["confirm"]}},
        opened_at=NOW + timedelta(seconds=8),
        user_id=OWNER,
        thread_id=CONVERSATION,
        checkpoint_ns=f"pixelflow-supervisor:{CONVERSATION}",
        status=status,
        response_id=response_id,
        response=response,
    )


def _global_clarification_interrupt(
    *,
    status: Literal["open", "responded", "closed"] = "open",
) -> StoredAgentInterrupt:
    """构造不绑定 Workflow 的 Supervisor 全局追问。"""

    response_id = (
        UUID("20000000-0000-0000-0000-000000000001")
        if status == "responded"
        else None
    )
    return StoredAgentInterrupt(
        interrupt_id="interrupt-global-1",
        conversation_id=CONVERSATION,
        workflow_id=None,
        turn_id="turn-1",
        kind="clarification",
        reason_code="ambiguous_target",
        payload={"question": "请明确要创建什么视频。"},
        opened_at=NOW + timedelta(seconds=8),
        user_id=OWNER,
        thread_id=CONVERSATION,
        checkpoint_ns="",
        status=status,
        response_id=response_id,
        response=(
            {
                "client_response_id": str(response_id),
                "value": {
                    "content": "创建一条商品介绍视频",
                    "materials": [],
                    "artifact_refs": [],
                },
            }
            if response_id is not None
            else None
        ),
        closed_at=(NOW + timedelta(seconds=20) if status == "closed" else None),
    )


def _global_decision(
    *,
    action: AgentAction,
    action_key: str,
) -> ActionDecision:
    return ActionDecision(
        action=action,
        intent=AgentIntent.GENERAL,
        confidence=1,
        requires_confirmation=action is AgentAction.CLARIFY,
        clarification_question=(
            "请明确要创建什么视频。" if action is AgentAction.CLARIFY else None
        ),
        reason_code="ambiguous_target",
        idempotency_key=action_key,
    )


def completed_commit(
    *,
    version: int = 1,
    expected_version: int = 0,
    action_key: str = "action-1",
    turn_id: str = "turn-1",
    occurred_at: datetime = NOW + timedelta(seconds=20),
) -> VideoTurnCommit:
    envelope, workflow = _workflow_state(
        version=version,
        action_key=action_key,
        turn_id=turn_id,
    )
    return VideoTurnCommit(
        decision=_decision(action_key=action_key),
        turn_status=TurnStatus.COMPLETED,
        workflow_state=envelope,
        workflow=workflow,
        expected_workflow_version=expected_version,
        messages=(_message(run_id=turn_id),),
        update_active_workflow=True,
        active_workflow_id=WORKFLOW,
        occurred_at=occurred_at,
    )


@asynccontextmanager
async def _repository(
    kind: RepositoryKind,
) -> AsyncIterator[tuple[VideoRuntimeRepository, object]]:
    if kind == "memory":
        store = MemoryPixelFlowTaskStore()
        yield MemoryVideoRuntimeRepository(task_store=store), store
        return

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=(
                    *AGENT_RUNTIME_TABLES,  # 创建 Runtime 权威业务表。
                    *AGENT_RUNTIME_SUPPORT_TABLES,  # 创建 Runtime 辅助协调表。
                    PixelFlowConversationRow.__table__,
                    PixelFlowConversationMessageRow.__table__,
                ),
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SQLPixelFlowTaskStore(session_factory)
    try:
        yield SQLVideoRuntimeRepository(
            session_factory,
            task_store=store,
        ), store
    finally:
        await engine.dispose()


async def _seed_conversation(
    store,
    *,
    user_id: str = OWNER,
    conversation_id: str = CONVERSATION,
    orchestration_mode: str = "supervisor_v1",
    video_ready: bool = True,
) -> None:
    await store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            orchestration_mode=orchestration_mode,
            orchestration_version=1,
            context={
                "__agent_runtime": {
                    "mode": "primary",
                    "enabled_intents": ["video"] if video_ready else ["image"],
                    "primary_execution_ready": video_ready,
                    "context_compaction_enabled": True,
                    "context_version": 0,
                }
            },
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
        )
    )


async def seed_two_turns(repository: VideoRuntimeRepository) -> None:
    await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
    await repository.enqueue_turn_for_execution(OWNER, _turn(2), now=NOW)


async def claim(
    repository: VideoRuntimeRepository,
    *,
    owner: str = "worker-a",
    now: datetime = NOW + timedelta(seconds=3),
    expires: datetime = NOW + timedelta(seconds=33),
):
    result = await repository.claim_turn(
        OWNER,
        CONVERSATION,
        "turn-1",
        lease_owner=owner,
        now=now,
        lease_expires_at=expires,
    )
    assert result is not None
    return result


def _waiting_commit(*, message_content: str = "请选择是否继续。") -> VideoTurnCommit:
    """构造不推进视频状态、只等待人工确认的固定 Graph 结果。"""

    return VideoTurnCommit(
        decision=_decision(action=AgentAction.CLARIFY, action_key="waiting-action-1"),
        turn_status=TurnStatus.WAITING_USER,
        expected_workflow_version=0,
        messages=(
            _message(
                message_id="message-waiting-1",
                content=message_content,
            ),
        ),
        open_interrupt=_interrupt(),
        occurred_at=NOW + timedelta(seconds=20),
    )


def _interrupt_response_request(
    *,
    client_response_id: UUID = UUID(
        "10000000-0000-4000-8000-000000000001",
    ),
    content: str = "同意方案",
) -> InterruptResponseRequest:
    """构造包含显式人工动作的固定响应合同。"""

    return InterruptResponseRequest.model_validate(
        {
            "client_response_id": str(client_response_id),
            "value": {
                "content": content,
                "materials": [],
                "reply_to_message_id": "message-plan-v1",
                "artifact_refs": ["artifact:video-plan:wf-1:v1"],
                "explicit_action": {
                    "action": "continue_workflow",
                    "intent": "video",
                    "workflow_id": WORKFLOW,
                    "stage": "plan_review",
                    "artifact_ref": "artifact:video-plan:wf-1:v1",
                    "patch": {"approved": True},
                },
            },
        }
    )


def _interrupt_response_message(
    request: InterruptResponseRequest,
    *,
    interrupt_id: str = "interrupt-1",
) -> PixelFlowConversationMessageRecord:
    """按公开响应 ID 构造跨实现稳定的可见用户消息。"""

    value = request.value.model_dump(mode="json")
    return PixelFlowConversationMessageRecord(
        message_id=conversation_message_id(
            CONVERSATION,
            request.client_response_id,
        ),
        conversation_id=CONVERSATION,
        user_id=OWNER,
        role="user",
        content=request.value.content,
        payload={
            "client_message_id": str(request.client_response_id),
            "interrupt_id": interrupt_id,
            "value": value,
            "explicit_action": value["explicit_action"],
        },
        created_at=(NOW + timedelta(seconds=30)).isoformat(),
    )


async def _claim_operation_completion(repository: VideoRuntimeRepository):
    """建立并领取一个指向默认会话和 Workflow 的真实 M06 完成事件。"""

    operation = OperationRecord(
        job_id="job-binding-1",
        provider_job_id="provider-binding-1",
        workflow_id=WORKFLOW,
        conversation_id=CONVERSATION,
        stage="scene_generation",
        stage_version=1,
        status=ExternalJobStatus.SUCCEEDED,
        attempt=1,
        request_hash="sha256:" + "2" * 64,
        idempotency_key="operation-binding-1",
        created_at=NOW,
        updated_at=NOW,
    )
    await repository.create_operation(OWNER, operation)
    completion = AgentEvent(
        event_id="evt_binding_done_1",
        sequence=1,
        cursor="cursor-binding-done-1",
        conversation_id=CONVERSATION,
        run_id=operation.job_id,
        occurred_at=NOW + timedelta(seconds=1),
        type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
        payload={"job_id": operation.job_id, "status": "succeeded"},
    )
    await repository.create_event(OWNER, completion)
    delivery = await repository.claim_operation_completion_event(
        OWNER,
        CONVERSATION,
        completion.event_id,
        operation.job_id,
        lease_owner="worker-binding",
        now=NOW + timedelta(seconds=2),
        lease_expires_at=NOW + timedelta(seconds=32),
    )
    assert delivery is not None
    return operation, completion, delivery


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_only_one_worker_claims_oldest_turn(kind: RepositoryKind) -> None:
    """防止并发 worker 同时取得同一最早 Turn。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await seed_two_turns(repository)
        first, second = await asyncio.gather(
            repository.claim_turn(
                OWNER,
                CONVERSATION,
                "turn-1",
                lease_owner="worker-a",
                now=NOW + timedelta(seconds=3),
                lease_expires_at=NOW + timedelta(seconds=33),
            ),
            repository.claim_turn(
                OWNER,
                CONVERSATION,
                "turn-1",
                lease_owner="worker-b",
                now=NOW + timedelta(seconds=3),
                lease_expires_at=NOW + timedelta(seconds=33),
            ),
        )
        assert sum(item is not None for item in (first, second)) == 1
        assert await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-2",
            lease_owner="worker-c",
            now=NOW + timedelta(seconds=3),
            lease_expires_at=NOW + timedelta(seconds=33),
        ) is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_claim_isolated_and_rejects_non_video_or_frontend_conversations(
    kind: RepositoryKind,
) -> None:
    """防止越权、串会话或未注册意图进入 live 执行。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await _seed_conversation(
            store,
            conversation_id="conversation-frontend",
            orchestration_mode="frontend_v2",
        )
        await _seed_conversation(
            store,
            conversation_id="conversation-image",
            video_ready=False,
        )
        await _seed_conversation(
            store,
            user_id="user-2",
            conversation_id="conversation-user-2",
        )
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        await repository.enqueue_turn_for_execution(
            OWNER,
            _turn(2, conversation_id="conversation-frontend"),
            now=NOW,
        )
        await repository.enqueue_turn_for_execution(
            OWNER,
            _turn(3, conversation_id="conversation-image"),
            now=NOW,
        )
        await repository.enqueue_turn_for_execution(
            "user-2",
            _turn(4, user_suffix="-u2", conversation_id="conversation-user-2"),
            now=NOW,
        )

        assert await repository.claim_turn(
            "user-2",
            CONVERSATION,
            "turn-1",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=5),
            lease_expires_at=NOW + timedelta(seconds=35),
        ) is None
        assert await repository.claim_turn(
            OWNER,
            "conversation-frontend",
            "turn-2",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=5),
            lease_expires_at=NOW + timedelta(seconds=35),
        ) is None
        assert await repository.claim_turn(
            OWNER,
            "conversation-image",
            "turn-3",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=5),
            lease_expires_at=NOW + timedelta(seconds=35),
        ) is None
        due = await repository.list_due_turns(
            now=NOW + timedelta(seconds=5),
            limit=10,
        )
        assert [(item.user_id, item.turn.turn_id) for item in due] == [
            (OWNER, "turn-1"),
            ("user-2", "turn-4-u2"),
        ]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_same_worker_rereads_without_extending_and_takeover_fences_old_worker(
    kind: RepositoryKind,
) -> None:
    """防止重读暗续租，并阻止过期 worker 在接管后提交。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        old = await claim(
            repository,
            owner="old",
            expires=NOW + timedelta(seconds=8),
        )
        reread = await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-1",
            lease_owner="old",
            now=NOW + timedelta(seconds=4),
            lease_expires_at=NOW + timedelta(seconds=40),
        )
        assert reread == old
        assert await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-1",
            lease_owner="other",
            now=NOW + timedelta(seconds=4),
            lease_expires_at=NOW + timedelta(seconds=40),
        ) is None

        new = await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-1",
            lease_owner="new",
            now=NOW + timedelta(seconds=9),
            lease_expires_at=NOW + timedelta(seconds=39),
        )
        assert new is not None
        assert new.attempt == old.attempt + 1
        assert new.lease_token != old.lease_token
        with pytest.raises(TurnExecutionLeaseConflictError):
            await repository.commit_turn(old, completed_commit())


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_heartbeat_and_transient_reschedule_obey_strict_time_boundaries(
    kind: RepositoryKind,
) -> None:
    """防止 heartbeat 缩短租约或退避边界前过早恢复。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        current = await claim(repository)
        with pytest.raises(TurnExecutionLeaseConflictError):
            await repository.heartbeat_turn(
                current,
                now=NOW + timedelta(seconds=4),
                lease_expires_at=current.lease_expires_at,
            )
        renewed = await repository.heartbeat_turn(
            current,
            now=NOW + timedelta(seconds=4),
            lease_expires_at=current.lease_expires_at + timedelta(seconds=10),
        )
        scheduled = await repository.reschedule_turn(
            renewed,
            now=NOW + timedelta(seconds=5),
            next_attempt_at=NOW + timedelta(seconds=15),
            reason_code="model_temporarily_unavailable",
        )
        assert scheduled.status is TurnStatus.QUEUED
        assert await repository.list_due_turns(
            now=NOW + timedelta(seconds=14),
            limit=10,
        ) == []
        assert [item.turn.turn_id for item in await repository.list_due_turns(
            now=NOW + timedelta(seconds=15),
            limit=10,
        )] == ["turn-1"]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_context_compaction_backoff_blocks_recovery_until_boundary(
    kind: RepositoryKind,
) -> None:
    """防止压缩重试窗口内调度 live Turn。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        lease = await repository.acquire_compaction_lease(
            OWNER,
            CONVERSATION,
            lease_owner="compactor",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=7),
        )
        assert lease is not None
        await repository.finish_compaction(
            OWNER,
            CONVERSATION,
            lease_owner="compactor",
            lease_token=lease.lease_token,
            now=NOW + timedelta(seconds=3),
            claim_next=False,
            retry_not_before=NOW + timedelta(seconds=20),
        )
        assert await repository.list_due_turns(
            now=NOW + timedelta(seconds=19),
            limit=10,
        ) == []
        assert [item.turn.turn_id for item in await repository.list_due_turns(
            now=NOW + timedelta(seconds=20),
            limit=10,
        )] == ["turn-1"]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_responded_interrupt_reclaims_original_waiting_turn(
    kind: RepositoryKind,
) -> None:
    """防止人工响应登记新的 follow-up Turn。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        open_interrupt = _interrupt()
        waiting = VideoTurnCommit(
            decision=_decision(action=AgentAction.CLARIFY),
            turn_status=TurnStatus.WAITING_USER,
            expected_workflow_version=0,
            messages=(_message(),),
            open_interrupt=open_interrupt,
            occurred_at=NOW + timedelta(seconds=10),
        )
        await repository.commit_turn(initial, waiting)
        await repository.store_interrupt_response(
            OWNER,
            CONVERSATION,
            open_interrupt.interrupt_id,
            client_response_id=UUID("10000000-0000-0000-0000-000000000001"),
            response_value={
                "content": "确认继续",
                "materials": [],
                "artifact_refs": [],
            },
            responded_at=NOW + timedelta(seconds=12),
        )

        due = await repository.list_due_interrupt_responses(
            now=NOW + timedelta(seconds=13),
            limit=10,
        )
        assert [item.interrupt_id for item in due] == ["interrupt-1"]
        resumed = await repository.claim_interrupt_resume(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=13),
            lease_expires_at=NOW + timedelta(seconds=43),
        )
        assert resumed is not None
        assert resumed.turn.turn_id == "turn-1"
        assert len(await repository.list_turns(OWNER, CONVERSATION)) == 1
        responded = await repository.get_interrupt(OWNER, "interrupt-1")
        assert responded is not None
        assert responded.status == "responded"


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_interrupt_response_registration_is_atomic_and_stable(
    kind: RepositoryKind,
) -> None:
    """响应必须在原 Turn 上冻结响应前版本，并同批写入消息和四类事件。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        conversation = await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )
        assert conversation is not None
        await store.patch_agent_runtime_conversation_context(
            CONVERSATION,
            user_id=OWNER,
            expected_revision=conversation.revision,
            runtime_patch={"context_version": 7},
        )
        request = _interrupt_response_request()
        message = _interrupt_response_message(request)
        events_before = await repository.list_events(OWNER, CONVERSATION)

        first = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=request,
            message=message,
            responded_at=NOW + timedelta(seconds=30),
        )
        first_events = await repository.list_events(OWNER, CONVERSATION)
        replay = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=request,
            message=message,
            responded_at=NOW + timedelta(seconds=31),
        )

        assert first.created is True
        assert replay.created is False
        assert first.turn.turn_id == replay.turn.turn_id == "turn-1"
        assert first.turn.status is TurnStatus.WAITING_USER
        assert first.turn.expected_context_version == 7
        assert replay.turn == first.turn
        assert first.message == replay.message == message
        assert first.context_version == replay.context_version == 8
        assert len(await repository.list_turns(OWNER, CONVERSATION)) == 1
        stored_messages = await store.list_conversation_messages(
            CONVERSATION,
            user_id=OWNER,
        )
        assert stored_messages == [message]
        updated_conversation = await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )
        assert updated_conversation is not None
        assert updated_conversation.context["__agent_runtime"]["context_version"] == 8
        assert updated_conversation.revision == conversation.revision + 2

        appended = first_events[len(events_before) :]
        assert [event.type for event in appended] == [
            AgentEventType.INTERRUPT_RESPONDED,
            AgentEventType.MESSAGE_UPSERTED,
            AgentEventType.INPUT_STATE_CHANGED,
            AgentEventType.RUN_STATE_CHANGED,
        ]
        assert [event.sequence for event in appended] == list(
            range(events_before[-1].sequence + 1, events_before[-1].sequence + 5)
        )
        assert {event.run_id for event in appended} == {"turn-1"}
        assert await repository.list_events(OWNER, CONVERSATION) == first_events


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_closed_interrupt_replays_same_response_without_rewake(
    kind: RepositoryKind,
) -> None:
    """已关闭中断只稳定回读同一响应，不推进版本或再次追加事件。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        request = _interrupt_response_request()
        message = _interrupt_response_message(request)
        created = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=request,
            message=message,
            responded_at=NOW + timedelta(seconds=30),
        )
        resumed = await repository.claim_interrupt_resume(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            lease_owner="worker-response",
            now=NOW + timedelta(seconds=31),
            lease_expires_at=NOW + timedelta(seconds=61),
        )
        assert resumed is not None
        await repository.commit_turn(
            resumed,
            VideoTurnCommit(
                decision=_decision(
                    action=AgentAction.ANSWER_ONLY,
                    action_key="response-finished",
                ),
                turn_status=TurnStatus.COMPLETED,
                expected_workflow_version=0,
                close_interrupt_id="interrupt-1",
                occurred_at=NOW + timedelta(seconds=40),
            ),
        )
        events_before = await repository.list_events(OWNER, CONVERSATION)
        conversation_before = await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )

        replay = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=request,
            message=message,
            responded_at=NOW + timedelta(seconds=50),
        )

        assert replay.created is False
        assert replay.turn.turn_id == created.turn.turn_id == "turn-1"
        assert replay.context_version == created.context_version
        assert await repository.list_events(OWNER, CONVERSATION) == events_before
        assert await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        ) == conversation_before
        assert await repository.list_due_interrupt_responses(
            now=NOW + timedelta(seconds=51),
        ) == []

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.register_interrupt_response(
                OWNER,
                CONVERSATION,
                "interrupt-1",
                request=_interrupt_response_request(content="拒绝方案"),
                message=_interrupt_response_message(
                    _interrupt_response_request(content="拒绝方案"),
                ),
                responded_at=NOW + timedelta(seconds=51),
            )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_consecutive_interrupt_responses_advance_original_turn_snapshot_identity(
    kind: RepositoryKind,
) -> None:
    """同一原 Turn 的连续确认必须逐次冻结各自响应前版本。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        first_request = _interrupt_response_request()
        first = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=first_request,
            message=_interrupt_response_message(first_request),
            responded_at=NOW + timedelta(seconds=30),
        )
        resumed = await repository.claim_interrupt_resume(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            lease_owner="worker-second-review",
            now=NOW + timedelta(seconds=31),
            lease_expires_at=NOW + timedelta(seconds=61),
        )
        assert resumed is not None
        second_interrupt = _interrupt(interrupt_id="interrupt-2")
        await repository.commit_turn(
            resumed,
            VideoTurnCommit(
                decision=_decision(
                    action=AgentAction.CLARIFY,
                    action_key="waiting-action-2",
                ),
                turn_status=TurnStatus.WAITING_USER,
                expected_workflow_version=0,
                open_interrupt=second_interrupt,
                close_interrupt_id="interrupt-1",
                occurred_at=NOW + timedelta(seconds=40),
            ),
        )
        second_request = _interrupt_response_request(
            client_response_id=UUID(
                "20000000-0000-4000-8000-000000000002",
            ),
            content="确认第二轮方案",
        )
        second = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-2",
            request=second_request,
            message=_interrupt_response_message(
                second_request,
                interrupt_id="interrupt-2",
            ),
            responded_at=NOW + timedelta(seconds=50),
        )
        events_before_first_replay = await repository.list_events(
            OWNER,
            CONVERSATION,
        )
        first_replay = await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=first_request,
            message=_interrupt_response_message(first_request),
            responded_at=NOW + timedelta(seconds=60),
        )

        assert first.context_version == first_replay.context_version == 1
        assert second.context_version == 2
        assert second.turn.turn_id == first.turn.turn_id == "turn-1"
        assert second.turn.expected_context_version == 1
        assert len(await repository.list_turns(OWNER, CONVERSATION)) == 1
        assert await repository.list_events(
            OWNER,
            CONVERSATION,
        ) == events_before_first_replay


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize(
    ("user_id", "conversation_id", "interrupt_id"),
    [
        ("user-other", CONVERSATION, "interrupt-1"),
        (OWNER, "conversation-other", "interrupt-1"),
        (OWNER, CONVERSATION, "interrupt-other"),
    ],
)
@pytest.mark.asyncio
async def test_interrupt_response_registration_rejects_wrong_scope(
    kind: RepositoryKind,
    user_id: str,
    conversation_id: str,
    interrupt_id: str,
) -> None:
    """错误用户、对话或 interrupt 身份必须 fail-closed 且不留下半写入。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        before = (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json")
        request = _interrupt_response_request()

        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.register_interrupt_response(
                user_id,
                conversation_id,
                interrupt_id,
                request=request,
                message=_interrupt_response_message(request),
                responded_at=NOW + timedelta(seconds=30),
            )

        assert (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json") == before
        assert await store.list_conversation_messages(
            CONVERSATION,
            user_id=OWNER,
        ) == []


@pytest.mark.asyncio
async def test_memory_interrupt_response_rolls_back_when_task_store_exit_fails() -> None:
    """Memory 对话写单元退出失败时，Repository 与 TaskStore 必须整批回滚。"""

    class FailingMemoryTaskStore(MemoryPixelFlowTaskStore):
        @asynccontextmanager
        async def agent_runtime_interrupt_response_write(
            self,
            *,
            conversation_id: str,
            user_id: str,
            message: PixelFlowConversationMessageRecord,
            occurred_at: datetime | str,
        ) -> AsyncIterator[object]:
            async with super().agent_runtime_interrupt_response_write(
                conversation_id=conversation_id,
                user_id=user_id,
                message=message,
                occurred_at=occurred_at,
            ) as write:
                yield write
                raise RuntimeError("注入响应写入失败")

    store = FailingMemoryTaskStore()
    repository = MemoryVideoRuntimeRepository(task_store=store)
    await _seed_conversation(store)
    await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
    initial = await claim(repository)
    await repository.commit_turn(initial, _waiting_commit())
    runtime_before = (
        await repository.export_safe_snapshot(OWNER, CONVERSATION)
    ).model_dump(mode="json")
    conversation_before = await store.get_conversation(
        CONVERSATION,
        user_id=OWNER,
    )
    request = _interrupt_response_request()

    with pytest.raises(RuntimeError, match="注入响应写入失败"):
        await repository.register_interrupt_response(
            OWNER,
            CONVERSATION,
            "interrupt-1",
            request=request,
            message=_interrupt_response_message(request),
            responded_at=NOW + timedelta(seconds=30),
        )

    assert (
        await repository.export_safe_snapshot(OWNER, CONVERSATION)
    ).model_dump(mode="json") == runtime_before
    assert await store.get_conversation(
        CONVERSATION,
        user_id=OWNER,
    ) == conversation_before
    assert await store.list_conversation_messages(
        CONVERSATION,
        user_id=OWNER,
    ) == []


@pytest.mark.asyncio
async def test_sql_interrupt_response_rolls_back_when_flush_fails() -> None:
    """SQL flush 注入失败时，interrupt、Turn、消息、版本与事件必须同事务回滚。"""

    async with _repository("sql") as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        runtime_before = (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json")
        conversation_before = await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )
        request = _interrupt_response_request()

        def fail_response_flush(session, _flush_context, _instances) -> None:
            if any(
                isinstance(item, PixelFlowConversationMessageRow)
                and item.message_id
                == conversation_message_id(
                    CONVERSATION,
                    request.client_response_id,
                )
                for item in session.new
            ):
                raise RuntimeError("注入 SQL 响应写入失败")

        sqlalchemy_event.listen(Session, "before_flush", fail_response_flush)
        try:
            with pytest.raises(RuntimeError, match="注入 SQL 响应写入失败"):
                await repository.register_interrupt_response(
                    OWNER,
                    CONVERSATION,
                    "interrupt-1",
                    request=request,
                    message=_interrupt_response_message(request),
                    responded_at=NOW + timedelta(seconds=30),
                )
        finally:
            sqlalchemy_event.remove(Session, "before_flush", fail_response_flush)

        assert (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json") == runtime_before
        assert await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        ) == conversation_before
        assert await store.list_conversation_messages(
            CONVERSATION,
            user_id=OWNER,
        ) == []


@pytest.mark.asyncio
async def test_interrupt_response_event_ids_match_between_memory_and_sql() -> None:
    """同一响应在 Memory/SQLite 必须生成完全相同的稳定事件身份。"""

    event_id_sets: list[list[str]] = []
    for kind in ("memory", "sql"):
        async with _repository(kind) as (repository, store):
            await _seed_conversation(store)
            await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
            initial = await claim(repository)
            await repository.commit_turn(initial, _waiting_commit())
            request = _interrupt_response_request()
            await repository.register_interrupt_response(
                OWNER,
                CONVERSATION,
                "interrupt-1",
                request=request,
                message=_interrupt_response_message(request),
                responded_at=NOW + timedelta(seconds=30),
            )
            events = await repository.list_events(OWNER, CONVERSATION)
            event_id_sets.append([event.event_id for event in events[-4:]])

    assert event_id_sets[0] == event_id_sets[1]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_concurrent_same_interrupt_response_commits_once(
    kind: RepositoryKind,
) -> None:
    """同一响应并发重试只能递增一次版本并追加一组稳定事件。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        events_before = await repository.list_events(OWNER, CONVERSATION)
        request = _interrupt_response_request()
        message = _interrupt_response_message(request)

        first, second = await asyncio.gather(
            repository.register_interrupt_response(
                OWNER,
                CONVERSATION,
                "interrupt-1",
                request=request,
                message=message,
                responded_at=NOW + timedelta(seconds=30),
            ),
            repository.register_interrupt_response(
                OWNER,
                CONVERSATION,
                "interrupt-1",
                request=request,
                message=message,
                responded_at=NOW + timedelta(seconds=31),
            ),
        )

        assert sorted((first.created, second.created)) == [False, True]
        assert first.turn == second.turn
        assert first.context_version == second.context_version == 1
        assert len(
            (await repository.list_events(OWNER, CONVERSATION))[
                len(events_before) :
            ]
        ) == 4
        conversation = await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )
        assert conversation is not None
        assert conversation.context["__agent_runtime"]["context_version"] == 1
        assert len(
            await store.list_conversation_messages(
                CONVERSATION,
                user_id=OWNER,
            )
        ) == 1


@pytest.mark.asyncio
async def test_sql_multiple_open_interrupts_fail_closed_without_partial_write() -> None:
    """数据库损坏形成多个 open interrupt 时，读取和响应都不能任选一个。"""

    async with _repository("sql") as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        await repository.commit_turn(initial, _waiting_commit())
        async with store.session_factory() as session:
            session.add(
                PixelFlowAgentInterruptRow(
                    interrupt_id="interrupt-corrupted-second-open",
                    conversation_id=CONVERSATION,
                    user_id=OWNER,
                    workflow_id=WORKFLOW,
                    turn_id="turn-1",
                    thread_id=CONVERSATION,
                    checkpoint_ns=f"pixelflow-supervisor:{CONVERSATION}",
                    kind="confirmation",
                    reason_code="plan_review_required",
                    status="open",
                    payload_json={"corrupted": True},
                    response_id=None,
                    response_json=null(),
                    opened_at=NOW + timedelta(seconds=9),
                    closed_at=None,
                )
            )
            await session.commit()
        conversation_before = await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        )
        events_before = await repository.list_events(OWNER, CONVERSATION)
        request = _interrupt_response_request()

        with pytest.raises(AgentRuntimeRecordConflictError, match="多个 open"):
            await repository.get_open_interrupt(OWNER, CONVERSATION)
        with pytest.raises(AgentRuntimeRecordConflictError, match="状态非法"):
            await repository.register_interrupt_response(
                OWNER,
                CONVERSATION,
                "interrupt-1",
                request=request,
                message=_interrupt_response_message(request),
                responded_at=NOW + timedelta(seconds=30),
            )

        assert await store.get_conversation(
            CONVERSATION,
            user_id=OWNER,
        ) == conversation_before
        assert await store.list_conversation_messages(
            CONVERSATION,
            user_id=OWNER,
        ) == []
        assert await repository.list_events(OWNER, CONVERSATION) == events_before


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_global_clarification_interrupt_opens_responds_and_closes(
    kind: RepositoryKind,
) -> None:
    """Memory/SQL 必须同构保存无 Workflow 的全局追问并恢复原 Turn。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        initial = await claim(repository)
        opened = _global_clarification_interrupt()
        await repository.commit_turn(
            initial,
            VideoTurnCommit(
                decision=_global_decision(
                    action=AgentAction.CLARIFY,
                    action_key="decision:global-open",
                ),
                turn_status=TurnStatus.WAITING_USER,
                expected_workflow_version=0,
                open_interrupt=opened,
                occurred_at=NOW + timedelta(seconds=10),
            ),
        )
        responded = await repository.store_interrupt_response(
            OWNER,
            CONVERSATION,
            opened.interrupt_id,
            client_response_id=UUID("20000000-0000-0000-0000-000000000001"),
            response_value={
                "content": "创建一条商品介绍视频",
                "materials": [],
                "artifact_refs": [],
            },
            responded_at=NOW + timedelta(seconds=12),
        )
        assert responded.workflow_id is None
        resumed = await repository.claim_interrupt_resume(
            OWNER,
            CONVERSATION,
            opened.interrupt_id,
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=13),
            lease_expires_at=NOW + timedelta(seconds=43),
        )
        assert resumed is not None and resumed.turn.turn_id == "turn-1"
        await repository.commit_turn(
            resumed,
            VideoTurnCommit(
                decision=_global_decision(
                    action=AgentAction.ANSWER_ONLY,
                    action_key="decision:global-close",
                ),
                turn_status=TurnStatus.COMPLETED,
                expected_workflow_version=0,
                close_interrupt_id=opened.interrupt_id,
                occurred_at=NOW + timedelta(seconds=20),
            ),
        )
        closed = await repository.get_interrupt(OWNER, opened.interrupt_id)
        assert closed is not None
        assert closed.status == "closed"
        assert closed.workflow_id is None


def test_non_clarification_interrupt_still_requires_workflow_identity() -> None:
    """视频业务中断不能借全局追问例外绕过 Workflow owner。"""

    with pytest.raises(ValidationError, match="workflow_id"):
        StoredAgentInterrupt(
            interrupt_id="interrupt-video-without-workflow",
            conversation_id=CONVERSATION,
            workflow_id=None,
            turn_id="turn-1",
            kind="video_intake_form",
            reason_code="video_intake_required",
            payload={"stage": "intake"},
            opened_at=NOW,
            user_id=OWNER,
            thread_id=CONVERSATION,
            checkpoint_ns=f"pixelflow-supervisor:{CONVERSATION}",
        )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_same_original_turn_atomically_closes_and_reopens_same_reason_review(
    kind: RepositoryKind,
) -> None:
    """真实 Handler 的下一轮同原因审核可在原 Turn 内原子替换 interrupt。"""

    from test_agent_video_live_handler import (
        _FakeCapabilities,
        _FakeClock,
        _FakeCredentialProvider,
        _planning_state,
    )

    async with _repository(kind) as (repository, store):
        initial_state = _planning_state("plan_review")
        initial_workflow = project_video_workflow_state(initial_state)
        test_conversation = initial_workflow.conversation_id
        test_workflow = initial_workflow.workflow_id
        await _seed_conversation(
            store,
            conversation_id=test_conversation,
        )
        initial_envelope = encode_video_workflow_state(
            user_id=OWNER,
            state=initial_state,
            workflow_version=1,
            last_turn_id="turn-1",
            last_action_key="decision:seed-plan-review",
        )
        setup_turn = _turn(1, conversation_id=test_conversation)
        await repository.enqueue_turn_for_execution(OWNER, setup_turn, now=NOW)
        setup_claim = await repository.claim_turn(
            OWNER,
            test_conversation,
            setup_turn.turn_id,
            lease_owner="worker-setup",
            now=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=31),
        )
        assert setup_claim is not None
        setup_decision = ActionDecision(
            action=AgentAction.MODIFY_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=test_workflow,
            target_stage="plan_review",
            confidence=1,
            patch={"revision_feedback": "建立测试快照"},
            reason_code="test_setup",
            idempotency_key="decision:seed-plan-review",
        )
        await repository.commit_turn(
            setup_claim,
            VideoTurnCommit(
                decision=setup_decision,
                turn_status=TurnStatus.COMPLETED,
                workflow_state=initial_envelope,
                workflow=initial_workflow,
                expected_workflow_version=0,
                update_active_workflow=True,
                active_workflow_id=test_workflow,
                occurred_at=NOW + timedelta(seconds=4),
            ),
        )

        original_turn = _turn(2, conversation_id=test_conversation)
        await repository.enqueue_turn_for_execution(
            OWNER,
            original_turn,
            now=NOW + timedelta(seconds=5),
        )
        first_claim = await repository.claim_turn(
            OWNER,
            test_conversation,
            original_turn.turn_id,
            lease_owner="worker-review",
            now=NOW + timedelta(seconds=6),
            lease_expires_at=NOW + timedelta(seconds=36),
        )
        assert first_claim is not None
        handler = VideoLiveWorkflowHandler(
            repository=repository,
            capabilities=_FakeCapabilities(),
            credential_provider=_FakeCredentialProvider(),
            clock=_FakeClock(NOW + timedelta(seconds=7)),
        )

        def review_command(
            workflow,
            *,
            action_key: str,
            feedback: str,
        ) -> WorkflowCommand:
            decision = ActionDecision(
                action=AgentAction.MODIFY_WORKFLOW,
                intent=AgentIntent.VIDEO,
                target_workflow_id=test_workflow,
                target_stage="plan_review",
                confidence=1,
                patch={"revision_feedback": feedback},
                reason_code="explicit_interrupt_response",
                idempotency_key=action_key,
            )
            return WorkflowCommand(
                conversation_id=test_conversation,
                workflow_id=test_workflow,
                kind=initial_workflow.kind,
                decision=decision,
                workflow=workflow,
                namespace=workflow_namespace(
                    test_conversation,
                    test_workflow,
                ),
                user_id=OWNER,
                turn_id=original_turn.turn_id,
                current_input=feedback,
                materials=[],
                reply_to_message_id=None,
                artifact_refs=[],
            )

        first_command = review_command(
            initial_workflow,
            action_key="decision:review-1",
            feedback="第一轮加快节奏",
        )
        first = await handler.dispatch(first_command)
        assert first.interrupt is not None
        await repository.commit_turn(
            first_claim,
            VideoTurnCommit(
                decision=first_command.decision,
                turn_status=first.turn_status,
                workflow_state=first.state,
                workflow=first.workflow,
                expected_workflow_version=1,
                messages=first.messages,
                open_interrupt=first.interrupt,
                occurred_at=NOW + timedelta(seconds=8),
            ),
        )
        await repository.store_interrupt_response(
            OWNER,
            test_conversation,
            first.interrupt.interrupt_id,
            client_response_id=UUID(
                "30000000-0000-4000-8000-000000000001"
            ),
            response_value={
                "content": "继续修改",
                "materials": [],
                "artifact_refs": [],
            },
            responded_at=NOW + timedelta(seconds=9),
        )
        resumed_claim = await repository.claim_interrupt_resume(
            OWNER,
            test_conversation,
            first.interrupt.interrupt_id,
            lease_owner="worker-review",
            now=NOW + timedelta(seconds=10),
            lease_expires_at=NOW + timedelta(seconds=40),
        )
        assert resumed_claim is not None
        second_command = review_command(
            first.workflow,
            action_key="decision:review-2",
            feedback="第二轮强化卖点",
        )
        second = await handler.dispatch(second_command)
        assert second.interrupt is not None

        await repository.commit_turn(
            resumed_claim,
            VideoTurnCommit(
                decision=second_command.decision,
                turn_status=second.turn_status,
                workflow_state=second.state,
                workflow=second.workflow,
                expected_workflow_version=2,
                messages=second.messages,
                open_interrupt=second.interrupt,
                close_interrupt_id=first.interrupt.interrupt_id,
                occurred_at=NOW + timedelta(seconds=11),
            ),
        )

        closed = await repository.get_interrupt(
            OWNER,
            first.interrupt.interrupt_id,
        )
        opened = await repository.get_interrupt(
            OWNER,
            second.interrupt.interrupt_id,
        )
        assert closed is not None and closed.status == "closed"
        assert opened is not None and opened.status == "open"
        assert opened.reason_code == closed.reason_code
        assert opened.turn_id == closed.turn_id == original_turn.turn_id
        assert len(
            await repository.list_turns(OWNER, test_conversation)
        ) == 2


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_commit_is_atomic_on_cas_conflict_and_replay_is_idempotent(
    kind: RepositoryKind,
) -> None:
    """防止 CAS 失败留下半条消息、状态、interrupt 或事件。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await seed_two_turns(repository)
        first = await claim(repository)
        committed = completed_commit()
        result = await repository.commit_turn(first, committed)
        assert result.status is TurnStatus.COMPLETED
        events_before = await repository.list_events(OWNER, CONVERSATION)
        replayed = await repository.commit_turn(first, committed)
        assert replayed == result
        assert await repository.list_events(OWNER, CONVERSATION) == events_before

        second = await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-2",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=21),
            lease_expires_at=NOW + timedelta(seconds=51),
        )
        assert second is not None
        snapshot_before_conflict = (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json")
        conflicting = completed_commit(
            version=3,
            expected_version=2,
            action_key="action-conflict",
            turn_id="turn-2",
            occurred_at=NOW + timedelta(seconds=22),
        ).model_copy(
            update={
                "turn_status": TurnStatus.WAITING_USER,
                "messages": (_message(message_id="message-conflict", run_id="turn-2"),),
                "open_interrupt": _interrupt(turn_id="turn-2"),
            }
        )
        with pytest.raises(VideoWorkflowStateConflictError):
            await repository.commit_turn(second, conflicting)
        assert (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json") == snapshot_before_conflict
        assert (await repository.get_turn(OWNER, "turn-2")).status is TurnStatus.PROCESSING


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_cancel_commit_requires_domain_state_and_workflow_projection_to_match(
    kind: RepositoryKind,
) -> None:
    """取消不能只改 Workflow 投影，必须同步推进可恢复的领域状态。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await seed_two_turns(repository)
        first = await claim(repository)
        await repository.commit_turn(first, completed_commit())
        second = await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-2",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=21),
            lease_expires_at=NOW + timedelta(seconds=51),
        )
        assert second is not None
        planning = VideoPlanningWorkflowService()
        state = planning.start(
            workflow_id=WORKFLOW,
            conversation_id=CONVERSATION,
            intent="video",
            intake_context={"nested": {"items": ["a", {"value": 1}]}},
            now=NOW,
        )
        cancelled = planning.cancel(
            state,
            now=NOW + timedelta(seconds=22),
        )
        envelope = encode_video_workflow_state(
            user_id=OWNER,
            state=cancelled,
            workflow_version=2,
            last_turn_id="turn-2",
            last_action_key="cancel-action-2",
        )
        cancelled_projection = project_video_workflow_state(cancelled)
        committed = await repository.commit_turn(
            second,
            VideoTurnCommit(
                decision=_decision(
                    action=AgentAction.CANCEL_WORKFLOW,
                    action_key="cancel-action-2",
                ),
                turn_status=TurnStatus.COMPLETED,
                workflow_state=envelope,
                workflow=cancelled_projection,
                expected_workflow_version=1,
                update_active_workflow=True,
                active_workflow_id=None,
                occurred_at=NOW + timedelta(seconds=22),
            ),
        )

        assert committed.status is TurnStatus.COMPLETED
        snapshot = await repository.export_safe_snapshot(OWNER, CONVERSATION)
        assert snapshot.workflows[0].status is WorkflowStatus.CANCELLED


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_same_action_key_with_different_envelope_digest_fails_closed(
    kind: RepositoryKind,
) -> None:
    """防止同动作键用不同完整信封摘要覆盖权威状态。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await seed_two_turns(repository)
        first = await claim(repository)
        await repository.commit_turn(first, completed_commit())
        second = await repository.claim_turn(
            OWNER,
            CONVERSATION,
            "turn-2",
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=21),
            lease_expires_at=NOW + timedelta(seconds=51),
        )
        assert second is not None
        conflict = completed_commit(
            version=2,
            expected_version=1,
            action_key="action-1",
            turn_id="turn-2",
            occurred_at=NOW + timedelta(seconds=22),
        )
        with pytest.raises(VideoWorkflowStateConflictError):
            await repository.commit_turn(second, conflict)


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_active_workflow_must_belong_to_same_owner_and_conversation(
    kind: RepositoryKind,
) -> None:
    """防止 switch_workflow 把其他会话 Workflow 设为活动对象。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        current = await claim(repository)
        invalid = VideoTurnCommit(
            decision=_decision(action=AgentAction.SWITCH_WORKFLOW),
            turn_status=TurnStatus.COMPLETED,
            expected_workflow_version=0,
            update_active_workflow=True,
            active_workflow_id="workflow-other",
            occurred_at=NOW + timedelta(seconds=20),
        )
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.commit_turn(current, invalid)
        assert await repository.get_active_workflow_id(OWNER, CONVERSATION) is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_snapshot_is_deep_readonly_stable_and_json_serializable(
    kind: RepositoryKind,
) -> None:
    """防止 Snapshot 嵌套别名反向污染 Repository。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        current = await claim(repository)
        await repository.commit_turn(current, completed_commit())
        first = await repository.export_safe_snapshot(OWNER, CONVERSATION)
        second = await repository.export_safe_snapshot(OWNER, CONVERSATION)
        first_json = first.model_dump(mode="json")
        assert first_json == second.model_dump(mode="json")
        json.dumps(first_json, ensure_ascii=False)
        assert isinstance(first.workflow_states[0].payload, MappingProxyType)
        with pytest.raises(TypeError):
            first.workflow_states[0].payload["nested"] = {}  # type: ignore[index]
        with pytest.raises(TypeError):
            first.workflows[0].creation_contract_snapshot["intent"] = "image"
        with pytest.raises((AttributeError, TypeError)):
            first.workflows[0].latest_artifact_refs.append("polluted")
        with pytest.raises(TypeError):
            first.turns[0].turn.decision.patch["unexpected"] = True  # type: ignore[index,union-attr]
        with pytest.raises((AttributeError, TypeError)):
            first.messages[0].payload["artifact"]["refs"].append("polluted")  # type: ignore[union-attr]
        caller_copy = first.model_dump(mode="json")
        caller_copy["messages"][0]["payload"]["artifact"]["refs"].append("local")
        assert (
            await repository.export_safe_snapshot(OWNER, CONVERSATION)
        ).model_dump(mode="json") == first_json


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.parametrize("snapshot_first", [True, False])
@pytest.mark.asyncio
async def test_snapshot_and_commit_share_one_consistent_read_boundary(
    kind: RepositoryKind,
    snapshot_first: bool,
) -> None:
    """防止 Snapshot 与原子提交交错时拼出从未存在的投影组合。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        current = await claim(repository)
        guard = (
            repository._compaction_write_lock  # type: ignore[attr-defined]
            if kind == "memory"
            else repository._sqlite_write_lock  # type: ignore[attr-defined]
        )
        await guard.acquire()
        snapshot_started = asyncio.Event()
        commit_started = asyncio.Event()

        async def read_snapshot():
            snapshot_started.set()
            return await repository.export_safe_snapshot(OWNER, CONVERSATION)

        async def write_commit():
            commit_started.set()
            return await repository.commit_turn(current, completed_commit())

        try:
            if snapshot_first:
                snapshot_task = asyncio.create_task(read_snapshot())
                await snapshot_started.wait()
                assert not snapshot_task.done()
                commit_task = asyncio.create_task(write_commit())
                await commit_started.wait()
            else:
                commit_task = asyncio.create_task(write_commit())
                await commit_started.wait()
                snapshot_task = asyncio.create_task(read_snapshot())
                await snapshot_started.wait()
            assert not commit_task.done()
            assert not snapshot_task.done()
        finally:
            guard.release()

        snapshot = await snapshot_task
        await commit_task
        if snapshot_first:
            assert snapshot.workflow_states == ()
            assert snapshot.workflows == ()
            assert snapshot.messages == ()
            assert snapshot.active_workflow_id is None
            assert [item.turn.status for item in snapshot.turns] == [TurnStatus.PROCESSING]
        else:
            assert len(snapshot.workflow_states) == 1
            assert len(snapshot.workflows) == 1
            assert len(snapshot.messages) == 1
            assert snapshot.active_workflow_id == WORKFLOW
            assert [item.turn.status for item in snapshot.turns] == [TurnStatus.COMPLETED]


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_waiting_user_commit_replay_returns_existing_turn(kind: RepositoryKind) -> None:
    """防止首次等待确认提交成功但响应丢失后被误判为 lease 冲突。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        current = await claim(repository)
        commit = _waiting_commit()
        first = await repository.commit_turn(current, commit)
        replay = await repository.commit_turn(current, commit)
        assert first.status is TurnStatus.WAITING_USER
        assert replay == first
        assert len(await repository.list_turns(OWNER, CONVERSATION)) == 1


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_waiting_user_commit_replay_rejects_changed_projection(
    kind: RepositoryKind,
) -> None:
    """防止相同动作键用不同消息或 interrupt 投影冒充模糊提交重放。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await repository.enqueue_turn_for_execution(OWNER, _turn(1), now=NOW)
        current = await claim(repository)
        await repository.commit_turn(current, _waiting_commit())
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.commit_turn(
                current,
                _waiting_commit(message_content="已被替换的确认消息。"),
            )


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_operation_completion_requires_live_delivery_lease_and_commits_atomically(
    kind: RepositoryKind,
) -> None:
    """防止 Graph 返回时已过期的 M06 event lease 推进 Workflow。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        operation = OperationRecord(
            job_id="job-1",
            provider_job_id="provider-1",
            workflow_id=WORKFLOW,
            conversation_id=CONVERSATION,
            stage="scene_generation",
            stage_version=1,
            status=ExternalJobStatus.SUCCEEDED,
            attempt=1,
            request_hash="sha256:" + "1" * 64,
            idempotency_key="operation-1",
            created_at=NOW,
            updated_at=NOW,
        )
        await repository.create_operation(OWNER, operation)
        completion = AgentEvent(
            event_id="evt_job_done_1",
            sequence=1,
            cursor="cursor-job-done-1",
            conversation_id=CONVERSATION,
            run_id="job-1",
            occurred_at=NOW + timedelta(seconds=1),
            type=AgentEventType.EXTERNAL_JOB_STATE_CHANGED,
            payload={"job_id": "job-1", "status": "succeeded"},
        )
        await repository.create_event(OWNER, completion)
        expired_claim = await repository.claim_operation_completion_event(
            OWNER,
            CONVERSATION,
            completion.event_id,
            operation.job_id,
            lease_owner="worker-a",
            now=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=5),
        )
        assert expired_claim is not None
        envelope, workflow = _workflow_state(
            action_key=completion.event_id,
            turn_id="turn-operation",
        )
        with pytest.raises(TurnExecutionLeaseConflictError):
            await repository.commit_operation_completion(
                expired_claim,
                user_id=OWNER,
                workflow_state=envelope,
                workflow=workflow,
                expected_workflow_version=0,
                messages=(_message(message_id="message-operation", run_id="job-1"),),
                occurred_at=NOW + timedelta(seconds=6),
            )
        assert await repository.get_video_state(OWNER, WORKFLOW) is None
        assert (await repository.get_event(OWNER, completion.event_id)) == completion

        live_claim = await repository.claim_operation_completion_event(
            OWNER,
            CONVERSATION,
            completion.event_id,
            operation.job_id,
            lease_owner="worker-b",
            now=NOW + timedelta(seconds=6),
            lease_expires_at=NOW + timedelta(seconds=16),
        )
        assert live_claim is not None
        stored = await repository.commit_operation_completion(
            live_claim,
            user_id=OWNER,
            workflow_state=envelope,
            workflow=workflow,
            expected_workflow_version=0,
            messages=(_message(message_id="message-operation", run_id="job-1"),),
            occurred_at=NOW + timedelta(seconds=7),
        )
        assert stored.workflow_id == WORKFLOW
        assert await repository.get_video_state(OWNER, WORKFLOW) == envelope
        assert await repository.claim_operation_completion_event(
            OWNER,
            CONVERSATION,
            completion.event_id,
            operation.job_id,
            lease_owner="worker-c",
            now=NOW + timedelta(seconds=20),
            lease_expires_at=NOW + timedelta(seconds=30),
        ) is None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_operation_completion_rejects_cross_conversation_state(
    kind: RepositoryKind,
) -> None:
    """防止会话 A 的完成事件推进同一用户的会话 B。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        await _seed_conversation(store, conversation_id="conversation-2")
        operation, completion, delivery = await _claim_operation_completion(repository)
        envelope, workflow = _workflow_state(
            workflow_id="workflow-2",
            conversation_id="conversation-2",
            action_key=completion.event_id,
            turn_id="turn-operation-2",
        )
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.commit_operation_completion(
                delivery,
                user_id=OWNER,
                workflow_state=envelope,
                workflow=workflow,
                expected_workflow_version=0,
                messages=(
                    _message(
                        message_id="message-operation-2",
                        run_id="job-binding-1",
                        conversation_id="conversation-2",
                    ),
                ),
                occurred_at=NOW + timedelta(seconds=3),
            )
        assert await repository.get_video_state(OWNER, "workflow-2") is None
        assert await repository.get_event(OWNER, completion.event_id) == completion
        assert await repository.claim_operation_completion_event(
            OWNER,
            CONVERSATION,
            completion.event_id,
            operation.job_id,
            lease_owner="worker-binding-retry",
            now=NOW + timedelta(seconds=33),
            lease_expires_at=NOW + timedelta(seconds=63),
        ) is not None


@pytest.mark.parametrize("kind", ["memory", "sql"])
@pytest.mark.asyncio
async def test_operation_completion_rejects_other_workflow_in_same_conversation(
    kind: RepositoryKind,
) -> None:
    """防止完成事件在原会话内推进不属于 Operation 的其他 Workflow。"""

    async with _repository(kind) as (repository, store):
        await _seed_conversation(store)
        operation, completion, delivery = await _claim_operation_completion(repository)
        envelope, workflow = _workflow_state(
            workflow_id="workflow-other",
            action_key=completion.event_id,
            turn_id="turn-operation-other",
        )
        with pytest.raises(AgentRuntimeRecordConflictError):
            await repository.commit_operation_completion(
                delivery,
                user_id=OWNER,
                workflow_state=envelope,
                workflow=workflow,
                expected_workflow_version=0,
                messages=(
                    _message(
                        message_id="message-operation-other",
                        run_id="job-binding-1",
                    ),
                ),
                occurred_at=NOW + timedelta(seconds=3),
            )
        assert await repository.get_video_state(OWNER, "workflow-other") is None
        assert await repository.get_event(OWNER, completion.event_id) == completion
        assert await repository.claim_operation_completion_event(
            OWNER,
            CONVERSATION,
            completion.event_id,
            operation.job_id,
            lease_owner="worker-binding-retry",
            now=NOW + timedelta(seconds=33),
            lease_expires_at=NOW + timedelta(seconds=63),
        ) is not None


@pytest.mark.asyncio
async def test_sql_operation_completion_acquires_row_locks_in_global_order() -> None:
    """防止 completion 与普通 Turn 形成 event、state 反向行锁环。"""

    async with _repository("sql") as (repository, store):
        await _seed_conversation(store)
        _, completion, delivery = await _claim_operation_completion(repository)
        envelope, workflow = _workflow_state(
            action_key=completion.event_id,
            turn_id="turn-operation-lock-order",
        )
        lock_trace: list[str] = []

        def record_for_update(execute_state: ORMExecuteState) -> None:
            statement = execute_state.statement
            if getattr(statement, "_for_update_arg", None) is None:
                return
            sql = str(statement.compile(dialect=mysql.dialect()))
            if "pixelflow_agent_operations" in sql:
                lock_trace.append("operation")
            elif "pixelflow_agent_video_states" in sql:
                lock_trace.append("state")
            elif "pixelflow_agent_workflows" in sql:
                lock_trace.append("workflow")
            elif "pixelflow_agent_projection_messages" in sql:
                lock_trace.append("message")
            elif "pixelflow_agent_events.event_id =" in sql:
                lock_trace.append("completion_event")
            elif "pixelflow_agent_events" in sql:
                lock_trace.append("event_tail")

        sqlalchemy_event.listen(Session, "do_orm_execute", record_for_update)
        try:
            await repository.commit_operation_completion(
                delivery,
                user_id=OWNER,
                workflow_state=envelope,
                workflow=workflow,
                expected_workflow_version=0,
                messages=(
                    _message(
                        message_id="message-operation-lock-order",
                        run_id="job-binding-1",
                    ),
                ),
                occurred_at=NOW + timedelta(seconds=3),
            )
        finally:
            sqlalchemy_event.remove(Session, "do_orm_execute", record_for_update)

        assert lock_trace[:6] == [
            "operation",
            "state",
            "workflow",
            "message",
            "completion_event",
            "event_tail",
        ]


def test_commit_contract_rejects_inconsistent_interrupt_and_mutable_json_aliases() -> None:
    """防止非法终态组合或调用方可变别名进入 Repository。"""

    with pytest.raises(ValidationError):
        VideoTurnCommit(
            decision=_decision(),
            turn_status=TurnStatus.WAITING_USER,
            expected_workflow_version=0,
            occurred_at=NOW,
        )
    source = {"nested": {"items": [1]}}
    message = SupervisorProjectionMessage(
        message_id="message-freeze",
        conversation_id=CONVERSATION,
        run_id="turn-1",
        role="assistant",
        content="冻结",
        payload=source,
        created_at=NOW,
    )
    source["nested"]["items"].append(2)
    assert message.model_dump(mode="json")["payload"] == {
        "nested": {"items": [1]}
    }
    decision_source = {"generation": {"models": ["seedance"]}}
    commit = VideoTurnCommit(
        decision=ActionDecision(
            action=AgentAction.START_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_workflow_id=WORKFLOW,
            target_stage="intake",
            confidence=1,
            patch=decision_source,
            reason_code="video_action_confirmed",
            idempotency_key="freeze-action",
        ),
        turn_status=TurnStatus.COMPLETED,
        expected_workflow_version=0,
        occurred_at=NOW,
    )
    decision_source["generation"]["models"].append("polluted")
    assert commit.model_dump(mode="json")["decision"]["patch"] == {
        "generation": {"models": ["seedance"]}
    }
    with pytest.raises((AttributeError, TypeError)):
        commit.decision.patch["generation"]["models"].append("polluted")  # type: ignore[union-attr]
