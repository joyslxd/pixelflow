"""M13.1 / R1 assist 会话集成合同测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from deerflow.persistence.base import Base
from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.context import (
    CompactionBatch,
    CompactionSegment,
    CompactionStageRequest,
    CompactionStageResult,
    ContextBudgetPolicy,
    ContextCompactionCoordinator,
    ContextCompactionRequest,
    ConversationCompactionRuntime,
    ModelContextProfile,
    RepositoryCompactionEventOutbox,
    SummaryBuilder,
    SummarySemanticSnapshot,
    SummaryVerificationError,
    TokenMeter,
    estimate_context_tokens,
)
from pixelflow.agent_runtime.context.externalizer import (
    ContextPayloadExternalizer,
)
from pixelflow.agent_runtime.contracts import (
    AgentEventType,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.persistence import (
    AGENT_RUNTIME_SUPPORT_TABLES,
    AGENT_RUNTIME_TABLES,
    MemoryCompactionQueueRepository,
    MemoryContextPayloadStore,
    SQLCompactionQueueRepository,
    SQLContextPayloadStore,
)
from pixelflow.agent_runtime.runtime_compaction import (
    AutomaticConversationCompactor,
    ContextBudgetGuard,
    RuntimeCompactionStageExecutor,
)
from pixelflow.agent_runtime.service import (
    AgentRuntimeContextConflictError,
    AgentRuntimeService,
)
from pixelflow.tasks import (
    AGENT_RUNTIME_CONTEXT_KEY,
    MemoryPixelFlowTaskStore,
    PixelFlowConversationMessageRecord,
    SQLPixelFlowTaskStore,
)
from pixelflow.tasks.model import (
    PixelFlowConversationMessageRow,
    PixelFlowConversationRow,
)
from tests._router_auth_helpers import make_authed_test_app

USER_ID = UUID("00000000-0000-0000-0000-000000000131")
CLIENT_INPUT_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)
BACKEND_ROOT = Path(__file__).resolve().parents[1]
R1_TEST_MODEL = "r1-context-test-model"


def _stable_user() -> User:
    return User(
        email="m13-r1@example.com",
        password_hash="x",
        system_role="user",
        id=USER_ID,
    )


def _assist_config() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        mode="assist",
        enabled_intents=(),
        new_conversation_rollout_percent=100,
        context_compaction_enabled=True,
    )


class _RecordingBudgetGuard:
    """记录真实 Guard 产出的预算，再交给自动压缩编排。"""

    def __init__(self, delegate: ContextBudgetGuard) -> None:
        self.delegate = delegate
        self.requests = []

    async def build_request(self, **kwargs):
        request = await self.delegate.build_request(**kwargs)
        self.requests.append(request)
        return request


class _ThresholdStageExecutor:
    """让阈值动作可观测，并避免测试调用任何真实摘要模型。"""

    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        if request.action in {"hard_gate_summary", "minimal_safe_context"}:
            next_tokens = request.target_input_tokens
        else:
            next_tokens = request.current_estimated_input_tokens
        return CompactionStageResult(estimated_input_tokens=next_tokens)


class _FailOnceStageExecutor:
    """首次压缩失败，后续 worker 接管时降到目标预算。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("首次压缩失败")
        return CompactionStageResult(
            estimated_input_tokens=request.target_input_tokens,
        )


class _MutableClock:
    """允许集成测试精确推进到压缩重试边界。"""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class _StaticBudgetGuard:
    """让排队集成测试稳定触发 level-1，而不改任何生产阈值。"""

    async def build_request(
        self,
        *,
        user_id: str,
        conversation_id: str,
        current_message_id: str,
    ):
        del current_message_id, user_id
        return ContextCompactionRequest(
            conversation_id=conversation_id,
            budget_report=TokenMeter().measure(
                estimated_input_tokens=60,
                profile=ModelContextProfile(
                    model_name="r1-static-profile",
                    max_context_tokens=200,
                    max_output_tokens=50,
                    tokenizer_strategy="static_test",
                    verified_at=NOW - timedelta(days=1),
                    source="M13.1 排队测试",
                ),
                policy=ContextBudgetPolicy(
                    effective_context_cap_tokens=200,
                    output_reserve_tokens=50,
                    safety_reserve_tokens=50,
                ),
            ),
        )


class _SafeSummaryEngine:
    """用确定性结构化结果替代付费模型，验证真实 SummaryBuilder 接线。"""

    model_name = R1_TEST_MODEL

    def count_tokens(self, source) -> int:
        return sum(len(str(message.content).encode("utf-8")) for message in source.new_messages)

    async def summarize(self, source) -> SummarySemanticSnapshot:
        previous = source.previous_summary or SummarySemanticSnapshot()
        message_ids = tuple(message.message_id for message in source.new_messages)
        return SummarySemanticSnapshot(
            user_goals=(*previous.user_goals,
                *(f"保留目标:{message_id}" for message_id in message_ids),
            ),
            confirmed_decisions=previous.confirmed_decisions,
            negative_constraints=(*previous.negative_constraints,
                *(f"禁止删除:{message_id}" for message_id in message_ids),
            ),
            workflow_states=dict(previous.workflow_states),
            unresolved_questions=previous.unresolved_questions,
            artifact_evidence_refs=previous.artifact_evidence_refs,
        )


class _OmittingSummaryEngine:
    """模拟摘要模型遗漏全部事实，验证 Runtime 必须失败关闭。"""

    model_name = R1_TEST_MODEL

    def count_tokens(self, source) -> int:
        return len(source.new_messages)

    async def summarize(self, source) -> SummarySemanticSnapshot:
        del source
        return SummarySemanticSnapshot()


class _CapturingSummaryEngine:
    """记录真实摘要输入，证明大型载荷已经替换为外置引用。"""

    model_name = R1_TEST_MODEL

    def __init__(self) -> None:
        self.sources = []

    def count_tokens(self, source) -> int:
        self.sources.append(source)
        return len(
            json.dumps(
                source.model_dump(mode="json"),
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    async def summarize(self, source) -> SummarySemanticSnapshot:
        self.sources.append(source)
        previous = source.previous_summary
        return previous or SummarySemanticSnapshot()


class _BlockingStageExecutor:
    """把压缩停在 started 之后，观察真实并发 Turn 的 queued 状态。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def execute(self, request):
        self.started.set()
        await self.resume.wait()
        return CompactionStageResult(
            estimated_input_tokens=request.target_input_tokens,
        )


def _r1_test_profile() -> ModelContextProfile:
    return ModelContextProfile(
        model_name=R1_TEST_MODEL,
        max_context_tokens=400_000,
        max_output_tokens=1_000,
        tokenizer_strategy="utf8_test_estimate",
        verified_at=NOW - timedelta(days=1),
        source="M13.1 非付费阈值集成测试",
    )


def _r1_app(
    *,
    config: AgentRuntimeConfig | None = None,
) -> tuple[object, MemoryPixelFlowTaskStore, MemoryCompactionQueueRepository]:
    from app.gateway.routers import pixelflow_conversations

    app = make_authed_test_app(user_factory=_stable_user)
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    app.state.pixelflow_task_store = task_store
    app.state.pixelflow_agent_runtime_service = AgentRuntimeService(
        config=config or _assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    app.include_router(pixelflow_conversations.router)
    return app, task_store, repository


def test_r1_assist_assigns_runtime_to_all_new_conversations_without_taking_business_owner() -> None:
    """assist 只接管会话基础设施，旧 v2 仍保留业务阶段推进权。"""

    app, _, _ = _r1_app()

    with TestClient(app) as client:
        created = client.post(
            "/agent/conversations",
            json={
                "title": "R1 新对话",
                "context": {
                    AGENT_RUNTIME_CONTEXT_KEY: {"mode": "primary"},
                    "business_field": "保留",
                },
            },
        )

    assert created.status_code == 200
    payload = created.json()
    assert payload["orchestration_mode"] == "frontend_v2"
    assert payload["orchestration_version"] == 1
    assert payload["context"]["business_field"] == "保留"
    assert payload["context"][AGENT_RUNTIME_CONTEXT_KEY] == {
        "mode": "assist",
        "enabled_intents": [],
        "context_compaction_enabled": True,
        "context_version": 0,
    }


def test_r1_candidate_only_enables_assist_in_test_profile() -> None:
    """测试候选覆盖全部新对话，生产 profile 继续隐式采用 off+0%。"""

    dev_profile = yaml.safe_load(
        (BACKEND_ROOT / "config.dev.yml").read_text(encoding="utf-8"),
    )
    prod_profile = yaml.safe_load(
        (BACKEND_ROOT / "config.prod.yml").read_text(encoding="utf-8"),
    )

    assert dev_profile["pixelflow"]["agent_runtime"] == {
        "mode": "assist",
        "enabled_intents": [],
        "new_conversation_rollout_percent": 100,
        "context_compaction_enabled": True,
    }
    assert "agent_runtime" not in prod_profile["pixelflow"]
    assert AgentRuntimeConfig().model_dump(mode="python") == {
        "mode": "off",
        "enabled_intents": (),
        "new_conversation_rollout_percent": 0,
        "context_compaction_enabled": False,
    }


def test_r1_assist_100_percent_covers_every_new_test_conversation() -> None:
    """测试环境连续新建的全部对话都冻结为 assist，不使用随机抽样或名单。"""

    app, _, _ = _r1_app()

    with TestClient(app) as client:
        created = [
            client.post(
                "/agent/conversations",
                json={"title": f"R1 全量覆盖 {index}"},
            ).json()
            for index in range(32)
        ]

    assert {item["context"][AGENT_RUNTIME_CONTEXT_KEY]["mode"] for item in created} == {"assist"}
    assert all(item["orchestration_mode"] == "frontend_v2" and item["orchestration_version"] == 1 for item in created)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("percentage", "expected_level", "expected_actions"),
    [
        (60, 1, ["externalize_payloads"]),
        (
            72,
            2,
            ["externalize_payloads", "incremental_summary"],
        ),
        (
            85,
            3,
            ["externalize_payloads", "incremental_summary"],
        ),
        (
            92,
            4,
            [
                "externalize_payloads",
                "incremental_summary",
                "hard_gate_summary",
            ],
        ),
    ],
)
async def test_r1_real_turn_path_automatically_triggers_all_context_thresholds(
    percentage: int,
    expected_level: int,
    expected_actions: list[str],
) -> None:
    """真实 start_turn 先统一计量，再自动进入 M04 租约与 Coordinator。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    profile = _r1_test_profile()
    budget = TokenMeter().measure(
        estimated_input_tokens=0,
        profile=profile,
        policy=ContextBudgetPolicy(
            effective_context_cap_tokens=256 * 1024,
            output_reserve_tokens=8 * 1024,
            safety_reserve_tokens=32 * 1024,
        ),
    )
    recording_guard = _RecordingBudgetGuard(
        ContextBudgetGuard(
            task_store=task_store,
            repository=repository,
            model_name=R1_TEST_MODEL,
            model_profiles={R1_TEST_MODEL: profile},
            clock=lambda: NOW,
        ),
    )
    executor = _ThresholdStageExecutor()
    runtime = ConversationCompactionRuntime(
        coordinator=ContextCompactionCoordinator(
            executor=executor,
            summary_model_name=R1_TEST_MODEL,
            model_profiles={R1_TEST_MODEL: profile},
            clock=lambda: NOW,
        ),
        repository=repository,
        lease_owner=f"r1-threshold-{percentage}",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(
            repository=repository,
        ),
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        context_compactor=AutomaticConversationCompactor(
            budget_guard=recording_guard,
            runtime=runtime,
        ),
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id=f"r1-threshold-{percentage}",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    fixed_payload = {
        "current_input": "继续",
        "recent_messages": [
            {
                "message_id": "threshold-prior-message",
                "role": "user",
                "content": "",
                "payload": {},
            },
        ],
        "conversation_summary": None,
        "business_context": {},
    }
    fixed_tokens = estimate_context_tokens(fixed_payload)
    threshold_tokens = (budget.usable_input_tokens * percentage + 99) // 100
    prior_content = "x" * max(1, threshold_tokens - fixed_tokens)
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="threshold-prior-message",
            conversation_id=conversation.conversation_id,
            user_id=str(USER_ID),
            role="user",
            content=prior_content,
            payload={},
            created_at=NOW.isoformat(),
        ),
    )

    started = await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(CLIENT_INPUT_ID),
            "content": "继续",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 0,
        },
    )

    assert len(recording_guard.requests) == 1
    report = recording_guard.requests[0].budget_report
    assert report.compaction_level == expected_level
    assert report.estimated_input_tokens * 100 >= (report.usable_input_tokens * percentage)
    assert [segment.segment_id for segment in recording_guard.requests[0].incremental_segments] == ["threshold-prior-message"]
    assert [request.action for request in executor.requests] == expected_actions
    events = await repository.list_events(
        str(USER_ID),
        conversation.conversation_id,
    )
    event_types = [event.type for event in events]
    assert AgentEventType.CONTEXT_COMPRESSION_STARTED in event_types
    assert AgentEventType.CONTEXT_COMPRESSION_COMPLETED in event_types
    assert started.status == "accepted"
    stored_turn = await repository.get_turn(
        str(USER_ID),
        started.turn_id,
    )
    assert stored_turn is not None
    assert stored_turn.status.value == "processing"


@pytest.mark.asyncio
async def test_r1_real_summary_stage_preserves_authoritative_business_state() -> None:
    """真实 SummaryBuilder 只新增摘要，不删除消息或改写业务合同与当前输入。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    profile = ModelContextProfile(
        model_name=R1_TEST_MODEL,
        max_context_tokens=100_000,
        max_output_tokens=1_000,
        tokenizer_strategy="utf8_test_estimate",
        verified_at=NOW - timedelta(days=1),
        source="M13.1 非付费真实摘要接线测试",
    )
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=SummaryBuilder(
            engine=_SafeSummaryEngine(),
            clock=lambda: NOW,
        ),
    )
    runtime = ConversationCompactionRuntime(
        coordinator=ContextCompactionCoordinator(
            executor=executor,
            summary_model_name=R1_TEST_MODEL,
            model_profiles={R1_TEST_MODEL: profile},
            clock=lambda: NOW,
        ),
        repository=repository,
        lease_owner="r1-real-summary",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(
            repository=repository,
        ),
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        context_compactor=AutomaticConversationCompactor(
            budget_guard=ContextBudgetGuard(
                task_store=task_store,
                repository=repository,
                model_name=R1_TEST_MODEL,
                model_profiles={R1_TEST_MODEL: profile},
                clock=lambda: NOW,
            ),
            runtime=runtime,
        ),
        clock=lambda: NOW,
    )
    business_context = {
        "creationContract": {
            "ratio": "9:16",
            "duration_seconds": 10,
            "negative_constraints": ["不要改变商品颜色"],
        },
        "pendingVideoJob": {
            "job_id": "job-r1-authoritative",
            "status": "running",
        },
    }
    assignment = service.assignment_for_new_conversation(business_context)
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-real-summary",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    old_message_ids = ("r1-old-1", "r1-old-2")
    old_contents = {message_id: f"{message_id}:" + ("旧上下文" * 3_000) for message_id in old_message_ids}
    for message_id in old_message_ids:
        await task_store.append_conversation_message(
            PixelFlowConversationMessageRecord(
                message_id=message_id,
                conversation_id=conversation.conversation_id,
                user_id=str(USER_ID),
                role="user",
                content=old_contents[message_id],
                payload={},
                created_at=(NOW - timedelta(minutes=1)).isoformat(),
            ),
        )

    started = await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(CLIENT_INPUT_ID),
            "content": "当前输入必须保留",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 0,
        },
    )

    summaries = await repository.list_summaries(
        str(USER_ID),
        conversation.conversation_id,
    )
    restored = await task_store.get_conversation(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )
    messages = await task_store.list_conversation_messages(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )
    assert started.status == "accepted"
    assert len(summaries) == 2
    assert summaries[-1].covered_message_ids == list(old_message_ids)
    assert summaries[-1].negative_constraints == [f"禁止删除:{message_id}" for message_id in old_message_ids]
    assert restored is not None
    assert {key: value for key, value in restored.context.items() if key != AGENT_RUNTIME_CONTEXT_KEY} == business_context
    assert {message.message_id: message.content for message in messages} == {**old_contents,
        messages[-1].message_id: "当前输入必须保留",
    }


@pytest.mark.asyncio
async def test_r1_externalizes_payload_before_summary_and_keeps_full_current_input() -> None:
    """60% 真实落库脱水，72% 摘要只读引用，当前输入材料完整参与预算。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    payload_store = MemoryContextPayloadStore()
    engine = _CapturingSummaryEngine()
    profile = ModelContextProfile(
        model_name=R1_TEST_MODEL,
        max_context_tokens=100_000,
        max_output_tokens=1_000,
        tokenizer_strategy="utf8_test_estimate",
        verified_at=NOW - timedelta(days=1),
        source="M13.1 外置到摘要非付费测试",
    )
    recording_guard = _RecordingBudgetGuard(
        ContextBudgetGuard(
            task_store=task_store,
            repository=repository,
            model_name=R1_TEST_MODEL,
            model_profiles={R1_TEST_MODEL: profile},
            clock=lambda: NOW,
        ),
    )
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=SummaryBuilder(
            engine=engine,
            clock=lambda: NOW,
        ),
        externalizer=ContextPayloadExternalizer(
            store=payload_store,
        ),
    )
    runtime = ConversationCompactionRuntime(
        coordinator=ContextCompactionCoordinator(
            executor=executor,
            summary_model_name=R1_TEST_MODEL,
            model_profiles={R1_TEST_MODEL: profile},
            clock=lambda: NOW,
        ),
        repository=repository,
        lease_owner="r1-externalize-summary",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(
            repository=repository,
        ),
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        context_compactor=AutomaticConversationCompactor(
            budget_guard=recording_guard,
            runtime=runtime,
        ),
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-externalize-summary",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    full_artifact = "旧工具完整载荷:" + ("A" * 30_000)
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="r1-large-artifact",
            conversation_id=conversation.conversation_id,
            user_id=str(USER_ID),
            role="assistant",
            content="工具结果已生成。",
            payload={
                "artifact": {
                    "artifact_ref": "artifact:r1-large",
                    "status": "ready",
                    "content": full_artifact,
                },
            },
            created_at=(NOW - timedelta(minutes=1)).isoformat(),
        ),
    )
    current_material = {
        "url": "material://" + ("B" * 30_000),
        "name": "当前输入大材料",
    }

    await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(CLIENT_INPUT_ID),
            "content": "当前输入文本必须保留",
            "materials": [current_material],
            "reply_to_message_id": "reply-r1-001",
            "artifact_refs": ["artifact:r1-current"],
            "expected_context_version": 0,
        },
    )

    assert recording_guard.requests[0].budget_report.compaction_level >= 2
    assert engine.sources
    serialized_source = json.dumps(
        engine.sources[-1].model_dump(mode="json"),
        ensure_ascii=False,
    )
    assert full_artifact not in serialized_source
    assert "context_externalized" in serialized_source
    assert "context-payload:" in serialized_source
    assert "artifact:r1-large" in serialized_source
    messages = await task_store.list_conversation_messages(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )
    current = next(message for message in messages if message.payload.get("client_message_id") == str(CLIENT_INPUT_ID))
    assert current.content == "当前输入文本必须保留"
    assert current.payload["materials"] == [current_material]
    assert current.payload["reply_to_message_id"] == "reply-r1-001"
    assert current.payload["artifact_refs"] == ["artifact:r1-current"]


@pytest.mark.asyncio
async def test_r1_budget_excludes_legacy_revision_snapshot_but_keeps_store() -> None:
    """旧前端修订快照只用于恢复，不重复进入 Agent 提示词预算。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    large_snapshot = {
        "conversationId": "r1-legacy-revision",
        "artifact": {
            "plan": "完整 Plan 恢复数据" * 10_000,
            "intent": "video",
        },
    }
    assignment = service.assignment_for_new_conversation(
        {
            "creation_contract": {
                "duration_seconds": 15,
                "ratio": "9:16",
            },
            "pendingVideoRevision": large_snapshot,
            "pending_video_revision": large_snapshot,
        },
    )
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-legacy-revision",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    current_message = PixelFlowConversationMessageRecord(
        message_id="r1-legacy-revision-current",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content="只修改第二个分镜，保持总时长不变",
        payload={},
        created_at=NOW.isoformat(),
    )
    await task_store.append_conversation_message(current_message)
    guard = ContextBudgetGuard(
        task_store=task_store,
        repository=repository,
        model_name=R1_TEST_MODEL,
        model_profiles={R1_TEST_MODEL: _r1_test_profile()},
        clock=lambda: NOW,
    )

    request = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current_message.message_id,
    )
    restored = await task_store.get_conversation(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )

    assert request.budget_report.estimated_input_tokens < 5_000
    assert restored is not None
    assert restored.context["pendingVideoRevision"] == large_snapshot
    assert restored.context["pending_video_revision"] == large_snapshot


@pytest.mark.asyncio
async def test_r1_budget_excludes_plan_revision_request_but_keeps_store() -> None:
    """Plan 修订请求只用于恢复，不得把完整 Plan 重复送入模型预算。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    revision_request = {
        "conversationId": "r1-plan-revision-request",
        "artifact": {
            "plan": "完整 Plan 修订恢复数据" * 12_000,
            "intent": "video",
        },
    }
    assignment = service.assignment_for_new_conversation(
        {
            "creation_contract": {
                "duration_seconds": 30,
                "ratio": "9:16",
            },
            "pendingPlanRevisionRequest": revision_request,
            "pending_plan_revision_request": revision_request,
        },
    )
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-plan-revision-request",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    current_message = PixelFlowConversationMessageRecord(
        message_id="r1-plan-revision-current",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content="只调整开头氛围，创作合同保持不变",
        payload={},
        created_at=NOW.isoformat(),
    )
    await task_store.append_conversation_message(current_message)
    guard = ContextBudgetGuard(
        task_store=task_store,
        repository=repository,
        model_name=R1_TEST_MODEL,
        model_profiles={R1_TEST_MODEL: _r1_test_profile()},
        clock=lambda: NOW,
    )

    request = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current_message.message_id,
    )
    restored = await task_store.get_conversation(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )

    assert request.budget_report.estimated_input_tokens < 10_000
    assert restored is not None
    assert restored.context["pendingPlanRevisionRequest"] == revision_request
    assert restored.context["pending_plan_revision_request"] == revision_request


@pytest.mark.asyncio
async def test_r1_hierarchical_summary_skips_unchanged_workflow_on_next_turn() -> None:
    """85% 汇总持久化 Workflow 版本证据，下一 Turn 不重复计入未变版本。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    profile = _r1_test_profile()
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-workflow-hierarchy",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    workflow = WorkflowRecord(
        workflow_id="wf-r1-001",
        conversation_id=conversation.conversation_id,
        kind=WorkflowKind.VIDEO,
        status=WorkflowStatus.RUNNING,
        current_stage="generate_scenes",
        stage_version=3,
        creation_contract_snapshot={
            "plan": "大型 Workflow 摘要来源:" + ("W" * 5_000),
        },
        latest_artifact_refs=["artifact:r1-workflow"],
        context_version=7,
        created_at=NOW,
        updated_at=NOW,
    )
    await repository.create_workflow(str(USER_ID), workflow)
    contract_hash = sha256(
        json.dumps(
            workflow.creation_contract_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    current_message = PixelFlowConversationMessageRecord(
        message_id="r1-hierarchy-current",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content="继续当前工作流",
        payload={},
        created_at=NOW.isoformat(),
    )
    await task_store.append_conversation_message(current_message)
    guard = ContextBudgetGuard(
        task_store=task_store,
        repository=repository,
        model_name=R1_TEST_MODEL,
        model_profiles={R1_TEST_MODEL: profile},
        clock=lambda: NOW,
    )
    before = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current_message.message_id,
    )
    assert [item.segment_id for item in before.workflow_summary_segments] == [
        workflow.workflow_id,
    ]
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=SummaryBuilder(
            engine=_SafeSummaryEngine(),
            clock=lambda: NOW,
        ),
    )
    await executor.execute(
        CompactionStageRequest(
            conversation_id=conversation.conversation_id,
            action="hierarchical_summary",
            target_input_tokens=1,
            current_estimated_input_tokens=(before.budget_report.estimated_input_tokens),
            batch=CompactionBatch(
                scope="workflow_summaries",
                batch_index=1,
                batch_count=1,
                segments=before.workflow_summary_segments,
            ),
        ),
    )
    after = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current_message.message_id,
    )

    assert after.workflow_summary_segments == ()
    assert after.budget_report.estimated_input_tokens < before.budget_report.estimated_input_tokens
    summaries = await repository.list_summaries(
        str(USER_ID),
        conversation.conversation_id,
    )
    assert summaries[-1].workflow_states == {
        workflow.workflow_id: ("generate_scenes|stage_version=3|context_version=7"),
    }
    stored_workflow = next(
        item
        for item in await repository.list_workflows(
            str(USER_ID),
            conversation.conversation_id,
        )
        if item.workflow_id == workflow.workflow_id
    )
    stored_contract_hash = sha256(
        json.dumps(
            stored_workflow.creation_contract_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    assert stored_contract_hash == contract_hash


@pytest.mark.asyncio
async def test_r1_hierarchy_does_not_persist_when_summary_would_expand_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小 Workflow 被结构摘要放大时保持原输入，不伪造 token 降幅。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-small-workflow-hierarchy",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    workflow = WorkflowRecord(
        workflow_id="wf-r1-small-001",
        conversation_id=conversation.conversation_id,
        kind=WorkflowKind.IMAGE,
        status=WorkflowStatus.RUNNING,
        current_stage="generate_image",
        stage_version=1,
        creation_contract_snapshot={},
        latest_artifact_refs=[],
        context_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    await repository.create_workflow(str(USER_ID), workflow)
    current = PixelFlowConversationMessageRecord(
        message_id="r1-small-hierarchy-current",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content="继续",
        payload={},
        created_at=NOW.isoformat(),
    )
    await task_store.append_conversation_message(current)
    guard = ContextBudgetGuard(
        task_store=task_store,
        repository=repository,
        model_name=R1_TEST_MODEL,
        model_profiles={R1_TEST_MODEL: _r1_test_profile()},
        clock=lambda: NOW,
    )
    before = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current.message_id,
    )
    from pixelflow.agent_runtime import runtime_compaction

    def expansion_estimator(payload) -> int:
        return 200 if payload.get("conversation_summary") else 100

    monkeypatch.setattr(
        runtime_compaction,
        "estimate_context_tokens",
        expansion_estimator,
    )
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=SummaryBuilder(
            engine=_SafeSummaryEngine(),
            clock=lambda: NOW,
        ),
    )
    result = await executor.execute(
        CompactionStageRequest(
            conversation_id=conversation.conversation_id,
            action="hierarchical_summary",
            target_input_tokens=1,
            current_estimated_input_tokens=(before.budget_report.estimated_input_tokens),
            batch=CompactionBatch(
                scope="workflow_summaries",
                batch_index=1,
                batch_count=1,
                segments=before.workflow_summary_segments,
            ),
        ),
    )
    after = await guard.build_request(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        current_message_id=current.message_id,
    )

    assert result.estimated_input_tokens == (before.budget_report.estimated_input_tokens)
    assert [item.segment_id for item in after.workflow_summary_segments] == [item.segment_id for item in before.workflow_summary_segments]
    assert (
        await repository.list_summaries(
            str(USER_ID),
            conversation.conversation_id,
        )
        == []
    )


@pytest.mark.asyncio
async def test_r1_first_summary_fails_closed_when_model_omits_critical_facts() -> None:
    """首版摘要遗漏否定约束或稳定 ID 时不得把原消息标记为已覆盖。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    assignment = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    ).assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-summary-fail-closed",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    source_message = PixelFlowConversationMessageRecord(
        message_id="r1-critical-message",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content="请生成蓝色书包，但不要改变商品颜色，合同 ID contract-123。",
        payload={"artifact_refs": ["artifact-r1-123"]},
        created_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    await task_store.append_conversation_message(source_message)
    await task_store.append_conversation_message(
        PixelFlowConversationMessageRecord(
            message_id="r1-current-message",
            conversation_id=conversation.conversation_id,
            user_id=str(USER_ID),
            role="user",
            content="当前输入",
            payload={},
            created_at=NOW.isoformat(),
        ),
    )
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=SummaryBuilder(
            engine=_OmittingSummaryEngine(),
            clock=lambda: NOW,
        ),
    )

    with pytest.raises(
        SummaryVerificationError,
        match="missing_negative_constraint",
    ):
        await executor.execute(
            CompactionStageRequest(
                conversation_id=conversation.conversation_id,
                action="incremental_summary",
                target_input_tokens=10,
                current_estimated_input_tokens=1_000,
                batch=CompactionBatch(
                    scope="messages",
                    batch_index=1,
                    batch_count=1,
                    segments=(
                        CompactionSegment(
                            segment_id=source_message.message_id,
                            estimated_tokens=500,
                        ),
                    ),
                ),
            ),
        )

    assert (
        await repository.list_summaries(
            str(USER_ID),
            conversation.conversation_id,
        )
        == []
    )
    assert [
        message.message_id
        for message in await task_store.list_conversation_messages(
            conversation.conversation_id,
            user_id=str(USER_ID),
        )
    ] == ["r1-critical-message", "r1-current-message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "payload", "expected_error"),
    [
        (
            "商品主色为蓝色，材质为牛津布，视频时长为10秒。",
            {},
            "missing_confirmed_decision",
        ),
        (
            "这是一次普通任务结果。",
            {"job_id": "job-r1-payload-001"},
            "missing_identifier",
        ),
    ],
)
async def test_r1_first_summary_rejects_omitted_business_facts_and_payload_ids(
    content: str,
    payload: dict,
    expected_error: str,
) -> None:
    """关键业务事实和只存在 payload 的稳定 ID 都必须达到100%保留。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id=f"r1-fact-{expected_error}",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    source = PixelFlowConversationMessageRecord(
        message_id=f"r1-source-{expected_error}",
        conversation_id=conversation.conversation_id,
        user_id=str(USER_ID),
        role="user",
        content=content,
        payload=payload,
        created_at=NOW.isoformat(),
    )
    await task_store.append_conversation_message(source)
    executor = RuntimeCompactionStageExecutor(
        task_store=task_store,
        repository=repository,
        summary_builder=SummaryBuilder(
            engine=_OmittingSummaryEngine(),
            clock=lambda: NOW,
        ),
    )

    with pytest.raises(
        SummaryVerificationError,
        match=expected_error,
    ):
        await executor.execute(
            CompactionStageRequest(
                conversation_id=conversation.conversation_id,
                action="incremental_summary",
                target_input_tokens=10,
                current_estimated_input_tokens=1_000,
                batch=CompactionBatch(
                    scope="messages",
                    batch_index=1,
                    batch_count=1,
                    segments=(
                        CompactionSegment(
                            segment_id=source.message_id,
                            estimated_tokens=500,
                        ),
                    ),
                ),
            ),
        )

    assert (
        await repository.list_summaries(
            str(USER_ID),
            conversation.conversation_id,
        )
        == []
    )


def test_r1_openapi_exposes_only_frozen_runtime_endpoints() -> None:
    """OpenAPI 暴露冻结的新入口，旧消息和对话入口继续并存。"""

    app, _, _ = _r1_app()
    paths = app.openapi()["paths"]

    expected_methods = {
        "/agent/conversations/{conversation_id}/turns/start": "post",
        "/agent/conversations/{conversation_id}/agent-snapshot": "get",
        "/agent/conversations/{conversation_id}/agent-events": "get",
        ("/agent/conversations/{conversation_id}/interrupts/{interrupt_id}/responses"): "post",
        "/agent/conversations/{conversation_id}/turns/jobs/{run_id}": "get",
    }
    for path, method in expected_methods.items():
        assert method in paths[path]
    assert "requestBody" in paths[("/agent/conversations/{conversation_id}/interrupts/{interrupt_id}/responses")]["post"]
    assert "post" in paths["/agent/conversations/{conversation_id}/messages"]


def test_r1_turn_start_is_idempotent_and_projects_snapshot_without_duplicate_message() -> None:
    """Turn 入口先持久化同一 client_input_id，再供旧流程复用可见消息。"""

    app, _, repository = _r1_app()
    request_payload = {
        "client_input_id": str(CLIENT_INPUT_ID),
        "content": "生成一张书包宣传图",
        "materials": [{"url": "https://cdn.example.test/bag.png"}],
        "reply_to_message_id": None,
        "artifact_refs": [],
        "expected_context_version": 0,
    }

    with TestClient(app) as client:
        conversation_id = client.post(
            "/agent/conversations",
            json={"title": "Turn 幂等"},
        ).json()["conversation_id"]
        first = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json=request_payload,
        )
        retried = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json=request_payload,
        )
        legacy_saved = client.post(
            f"/agent/conversations/{conversation_id}/messages",
            json={
                "role": "user",
                "content": request_payload["content"],
                "payload": {
                    "client_message_id": str(CLIENT_INPUT_ID),
                    "materials": request_payload["materials"],
                },
            },
        )
        snapshot = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )
        run_status = client.get(
            f"/agent/conversations/{conversation_id}/turns/jobs/{first.json()['run_id']}",
        )
        detail = client.get(f"/agent/conversations/{conversation_id}")

    assert first.status_code == retried.status_code == legacy_saved.status_code == snapshot.status_code == run_status.status_code == 200
    assert retried.json() == first.json()
    assert run_status.json() == first.json()
    assert first.json()["status"] == "accepted"
    assert first.json()["context_version"] == 1
    assert len(detail.json()["messages"]) == 1
    message = detail.json()["messages"][0]
    assert message["payload"]["client_message_id"] == str(CLIENT_INPUT_ID)
    assert message["payload"]["materials"] == request_payload["materials"]

    projection = snapshot.json()
    assert projection["conversationId"] == conversation_id
    assert projection["run"]["runId"] == first.json()["run_id"]
    assert projection["run"]["status"] == "running"
    assert projection["inputQueue"] == [
        {
            "clientInputId": str(CLIENT_INPUT_ID),
            "turnId": first.json()["turn_id"],
            "status": "accepted",
            "queuePosition": None,
            "updatedAt": NOW.isoformat().replace("+00:00", "Z"),
        }
    ]
    assert projection["context_version"] == 1
    assert len(projection["messages"]) == 1
    assert projection["workflows"] == []
    assert projection["interrupt"] is None
    assert projection["resume"]["sequence"] == 2
    assert len(asyncio.run(repository.list_events(str(USER_ID), conversation_id))) == 2
    assert (
        asyncio.run(
            repository.list_events_after_cursor(
                str(USER_ID),
                conversation_id,
                cursor="cursor-does-not-exist",
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_r1_only_one_turn_is_executable_without_compaction() -> None:
    """无压缩时同会话也只能有一个执行 owner，后续输入必须按顺序排队。"""

    from app.gateway.routers import pixelflow_conversations

    _, task_store, repository = _r1_app()
    service = AgentRuntimeService(
        config=_assist_config().model_copy(
            update={"context_compaction_enabled": False},
        ),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-single-execution-owner",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )
    first = await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(CLIENT_INPUT_ID),
            "content": "第一条输入",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 0,
        },
    )
    second_input_id = UUID("22222222-2222-4222-8222-222222222222")
    second = await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(second_input_id),
            "content": "第二条并发输入",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 1,
        },
    )

    before_handoff = await service.snapshot(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
    )
    assert first.status == "accepted"
    assert second.status == "queued"
    assert [(item.client_input_id, item.status, item.queue_position) for item in before_handoff.input_queue] == [
        (str(CLIENT_INPUT_ID), "accepted", None),
        (str(second_input_id), "queued", 1),
    ]
    current_conversation = await task_store.get_conversation(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )
    assert current_conversation is not None
    await task_store.patch_agent_runtime_conversation_context(
        conversation.conversation_id,
        user_id=str(USER_ID),
        expected_revision=current_conversation.revision,
        runtime_patch={
            "legacy_handoff": {
                "client_input_id": str(second_input_id),
                "job_id": "historical-invalid-marker",
                "status": "pending_ack",
            },
        },
    )
    after_invalid_marker = await service.snapshot(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
    )
    repaired_conversation = await task_store.get_conversation(
        conversation.conversation_id,
        user_id=str(USER_ID),
    )
    assert repaired_conversation is not None
    assert [(item.client_input_id, item.status, item.queue_position) for item in after_invalid_marker.input_queue] == [
        (str(CLIENT_INPUT_ID), "accepted", None),
        (str(second_input_id), "queued", 1),
    ]
    assert repaired_conversation.context[AGENT_RUNTIME_CONTEXT_KEY]["legacy_handoff"] is None

    await service.acknowledge_legacy_handoff(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        client_input_id=CLIENT_INPUT_ID,
    )
    after_handoff = await service.snapshot(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
    )
    assert [(item.client_input_id, item.status) for item in after_handoff.input_queue] == [(str(second_input_id), "processing")]


def test_r1_legacy_message_handoff_is_recoverable_and_advances_runtime_turn() -> None:
    """旧消息 job 可幂等复用，pending job 落库后 Runtime 才完成 Turn。"""

    app, _, _ = _r1_app()
    turn_request = {
        "client_input_id": str(CLIENT_INPUT_ID),
        "content": "生成一张书包宣传图",
        "materials": [],
        "reply_to_message_id": None,
        "artifact_refs": [],
        "expected_context_version": 0,
    }
    message_request = {
        "role": "user",
        "content": turn_request["content"],
        "payload": {
            "client_message_id": str(CLIENT_INPUT_ID),
            "materials": [],
        },
    }

    with TestClient(app) as client:
        conversation_id = client.post(
            "/agent/conversations",
            json={"title": "旧流程接力"},
        ).json()["conversation_id"]
        started_turn = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json=turn_request,
        )
        first_job = client.post(
            f"/agent/conversations/{conversation_id}/messages/start",
            json=message_request,
        )
        retried_job = client.post(
            f"/agent/conversations/{conversation_id}/messages/start",
            json=message_request,
        )
        pending_job = {
            "job_id": first_job.json()["job_id"],
            "conversation_id": conversation_id,
            "source_message_id": str(CLIENT_INPUT_ID),
            "kind": "conversation_message",
            "started_at": NOW.isoformat(),
            "request": message_request,
            "message": {
                "id": str(CLIENT_INPUT_ID),
                "role": "user",
                "content": turn_request["content"],
                "materials": [],
            },
            "continue_after_save": {
                "type": "handle_send",
                "content": turn_request["content"],
                "materials": [],
            },
        }
        handoff = client.put(
            f"/agent/conversations/{conversation_id}",
            json={
                "last_phase": "message_save_running",
                "context": {
                    "pendingMessageJob": pending_job,
                    "pending_message_job": pending_job,
                    "pendingAgentRuntimeTurns": [],
                    "pending_agent_runtime_turns": [],
                },
            },
        )
        run = client.get(
            f"/agent/conversations/{conversation_id}/turns/jobs/{started_turn.json()['run_id']}",
        )
        snapshot = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )

    assert first_job.status_code == retried_job.status_code == 200
    assert first_job.json()["job_id"] == retried_job.json()["job_id"]
    assert handoff.status_code == 200
    assert run.json()["status"] == "completed"
    assert snapshot.json()["inputQueue"] == []


def test_r1_handoff_failure_keeps_marker_and_snapshot_reconciles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ack 中断不会留下不可恢复半状态，快照会按 marker 幂等补偿。"""

    app, _, _ = _r1_app()
    service = app.state.pixelflow_agent_runtime_service
    original_reconcile = service.reconcile_pending_legacy_handoff
    message_request = {
        "role": "user",
        "content": "接力失败恢复",
        "payload": {
            "client_message_id": str(CLIENT_INPUT_ID),
            "materials": [],
        },
    }

    with TestClient(app) as client:
        conversation_id = client.post(
            "/agent/conversations",
            json={"title": "接力补偿"},
        ).json()["conversation_id"]
        started = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": str(CLIENT_INPUT_ID),
                "content": message_request["content"],
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 0,
            },
        )
        job = client.post(
            f"/agent/conversations/{conversation_id}/messages/start",
            json=message_request,
        ).json()
        pending_job = {
            "job_id": job["job_id"],
            "conversation_id": conversation_id,
            "source_message_id": str(CLIENT_INPUT_ID),
            "kind": "conversation_message",
            "request": message_request,
        }

        async def fail_once(**kwargs):
            del kwargs
            raise RuntimeError("注入接力中断")

        monkeypatch.setattr(
            service,
            "reconcile_pending_legacy_handoff",
            fail_once,
        )
        persisted = client.put(
            f"/agent/conversations/{conversation_id}",
            json={
                "context": {
                    "pendingMessageJob": pending_job,
                    "pending_message_job": pending_job,
                },
            },
        )
        detail = client.get(
            f"/agent/conversations/{conversation_id}",
        )
        run_before = client.get(
            f"/agent/conversations/{conversation_id}/turns/jobs/{started.json()['run_id']}",
        )
        monkeypatch.setattr(
            service,
            "reconcile_pending_legacy_handoff",
            original_reconcile,
        )
        recovered = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )
        detail_after = client.get(
            f"/agent/conversations/{conversation_id}",
        )

    assert persisted.status_code == 200
    marker = detail.json()["conversation"]["context"][AGENT_RUNTIME_CONTEXT_KEY]["legacy_handoff"]
    assert marker["client_input_id"] == str(CLIENT_INPUT_ID)
    assert marker["status"] == "pending_ack"
    assert run_before.json()["status"] == "accepted"
    assert recovered.status_code == 200
    assert recovered.json()["inputQueue"] == []
    assert detail_after.json()["conversation"]["context"][AGENT_RUNTIME_CONTEXT_KEY]["legacy_handoff"] is None


def test_r1_forged_pending_message_job_cannot_complete_runtime_turn() -> None:
    """客户端伪造 pending job 不得获得旧流程接力权限。"""

    app, _, _ = _r1_app()
    with TestClient(app) as client:
        conversation_id = client.post(
            "/agent/conversations",
            json={"title": "拒绝伪造接力"},
        ).json()["conversation_id"]
        started = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": str(CLIENT_INPUT_ID),
                "content": "不能被伪造接力完成",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 0,
            },
        )
        updated = client.put(
            f"/agent/conversations/{conversation_id}",
            json={
                "context": {
                    "pendingMessageJob": {
                        "job_id": "forged-job",
                        "conversation_id": conversation_id,
                        "source_message_id": str(CLIENT_INPUT_ID),
                        "kind": "conversation_message",
                    },
                },
            },
        )
        run = client.get(
            f"/agent/conversations/{conversation_id}/turns/jobs/{started.json()['run_id']}",
        )
        detail = client.get(
            f"/agent/conversations/{conversation_id}",
        )

    assert updated.status_code == 200
    assert run.json()["status"] == "accepted"
    assert "legacy_handoff" not in detail.json()["conversation"]["context"][AGENT_RUNTIME_CONTEXT_KEY]


def test_r1_real_message_job_cannot_handoff_a_queued_turn() -> None:
    """真实消息 job 也不能越过当前 Turn 接力 queued 输入。"""

    app, _, _ = _r1_app()
    queued_input_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    with TestClient(app) as client:
        conversation_id = client.post(
            "/agent/conversations",
            json={"title": "拒绝 queued 越序接力"},
        ).json()["conversation_id"]
        first = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": str(CLIENT_INPUT_ID),
                "content": "当前执行输入",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 0,
            },
        )
        second = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": str(queued_input_id),
                "content": "必须保持排队的输入",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 1,
            },
        )
        message_request = {
            "role": "user",
            "content": "必须保持排队的输入",
            "payload": {
                "client_message_id": str(queued_input_id),
                "materials": [],
            },
        }
        message_job = client.post(
            f"/agent/conversations/{conversation_id}/messages/start",
            json=message_request,
        )
        pending_job = {
            "job_id": message_job.json()["job_id"],
            "conversation_id": conversation_id,
            "source_message_id": str(queued_input_id),
            "kind": "conversation_message",
            "started_at": NOW.isoformat(),
            "request": message_request,
        }
        updated = client.put(
            f"/agent/conversations/{conversation_id}",
            json={
                "context": {
                    "pendingMessageJob": pending_job,
                    "pending_message_job": pending_job,
                },
            },
        )
        snapshot = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )
        detail = client.get(
            f"/agent/conversations/{conversation_id}",
        )

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "queued"
    assert message_job.status_code == updated.status_code == 200
    assert snapshot.status_code == 200
    assert [
        (item["clientInputId"], item["status"], item["queuePosition"])
        for item in snapshot.json()["inputQueue"]
    ] == [
        (str(CLIENT_INPUT_ID), "accepted", None),
        (str(queued_input_id), "queued", 1),
    ]
    assert "legacy_handoff" not in detail.json()["conversation"]["context"][AGENT_RUNTIME_CONTEXT_KEY]


@pytest.mark.asyncio
async def test_r1_compaction_queue_and_recovery_snapshot_share_one_repository() -> None:
    """自动压缩期间并发输入只排队，完成后从同一 Snapshot 恢复。"""

    from app.gateway.routers import pixelflow_conversations

    app, task_store, repository = _r1_app()
    executor = _BlockingStageExecutor()
    runtime = ConversationCompactionRuntime(
        coordinator=ContextCompactionCoordinator(
            executor=executor,
            summary_model_name="r1-static-profile",
            model_profiles={},
            clock=lambda: NOW,
        ),
        repository=repository,
        lease_owner="m13-r1-worker",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(
            repository=repository,
        ),
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        context_compactor=AutomaticConversationCompactor(
            budget_guard=_StaticBudgetGuard(),
            runtime=runtime,
        ),
        clock=lambda: NOW,
    )
    app.state.pixelflow_agent_runtime_service = service
    assignment = service.assignment_for_new_conversation({"business_field": "保留"})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-compaction",
            user_id=str(USER_ID),
            context=assignment.context,
            orchestration_mode=assignment.orchestration_mode,
            orchestration_version=assignment.orchestration_version,
        )
    )
    first_task = asyncio.create_task(
        service.start_turn(
            user_id=str(USER_ID),
            conversation_id=conversation.conversation_id,
            request={
                "client_input_id": str(CLIENT_INPUT_ID),
                "content": "触发自动压缩",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 0,
            },
        ),
    )
    await executor.started.wait()
    queued_input_id = UUID("22222222-2222-4222-8222-222222222222")
    queued = await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(queued_input_id),
            "content": "压缩时继续输入",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 1,
        },
    )
    during = await service.snapshot(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
    )
    assert queued.status == "queued"
    assert during.compression.status == "compacting"
    assert [item.status for item in during.input_queue] == [
        "queued",
        "queued",
    ]
    assert [item.queue_position for item in during.input_queue] == [1, 2]

    executor.resume.set()
    started = await first_task
    assert started.status == "accepted"
    processing = await service.get_run(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        run_id=started.run_id,
    )
    assert processing is not None
    assert processing.status == "processing"
    recovered = await service.snapshot(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
    )
    assert recovered.compression.status == "idle"
    assert recovered.compression.last_outcome == "completed"
    assert recovered.input_queue[0].status == "processing"
    assert recovered.input_queue[1].status == "queued"
    assert recovered.input_queue[1].queue_position == 1
    assert recovered.resume.sequence >= 7

    await service.acknowledge_legacy_handoff(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        client_input_id=CLIENT_INPUT_ID,
    )
    handed_off = await service.snapshot(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
    )
    assert len(handed_off.input_queue) == 1
    assert handed_off.input_queue[0].client_input_id == str(queued_input_id)
    assert handed_off.input_queue[0].status == "processing"


@pytest.mark.asyncio
async def test_r1_failed_compaction_is_recovered_by_snapshot_or_event_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退避期内读取不重试，到期后只恢复一次且原 Turn 不重发。"""

    from app.gateway.routers import pixelflow_conversations

    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    executor = _FailOnceStageExecutor()
    clock = _MutableClock(NOW)
    runtime = ConversationCompactionRuntime(
        coordinator=ContextCompactionCoordinator(
            executor=executor,
            summary_model_name="r1-static-profile",
            model_profiles={},
            clock=clock,
        ),
        repository=repository,
        lease_owner="m13-r1-recovery-worker",
        lease_ttl=timedelta(minutes=5),
        event_sink=RepositoryCompactionEventOutbox(
            repository=repository,
        ),
        clock=clock,
    )
    service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        context_compactor=AutomaticConversationCompactor(
            budget_guard=_StaticBudgetGuard(),
            runtime=runtime,
        ),
        clock=clock,
    )
    scheduled_recoveries: list[tuple[str, str]] = []
    schedule_recovery = service._schedule_compaction_recovery

    def record_recovery_schedule(user_id: str, conversation_id: str) -> None:
        scheduled_recoveries.append((user_id, conversation_id))
        schedule_recovery(user_id, conversation_id)

    monkeypatch.setattr(
        service,
        "_schedule_compaction_recovery",
        record_recovery_schedule,
    )
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-compaction-retry",
            user_id=str(USER_ID),
            context=assignment.context,
        ),
    )

    started = await service.start_turn(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        request={
            "client_input_id": str(CLIENT_INPUT_ID),
            "content": "失败后继续原请求",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 0,
        },
    )
    assert started.status == "queued"

    events_after_failure = await repository.list_events(
        str(USER_ID),
        conversation.conversation_id,
    )
    assert executor.calls == 1
    assert scheduled_recoveries == []
    for _ in range(3):
        await service.snapshot(
            user_id=str(USER_ID),
            conversation_id=conversation.conversation_id,
        )
        await service.events_after(
            user_id=str(USER_ID),
            conversation_id=conversation.conversation_id,
            cursor=None,
        )
        polled = await service.get_run(
            user_id=str(USER_ID),
            conversation_id=conversation.conversation_id,
            run_id=started.run_id,
        )
        assert polled is not None
        assert polled.status == "queued"
        await asyncio.sleep(0)
    assert executor.calls == 1
    assert scheduled_recoveries == []
    assert await repository.list_events(
        str(USER_ID),
        conversation.conversation_id,
    ) == events_after_failure

    clock.current = NOW + timedelta(seconds=30)
    await service.events_after(
        user_id=str(USER_ID),
        conversation_id=conversation.conversation_id,
        cursor=None,
    )
    assert scheduled_recoveries == [
        (str(USER_ID), conversation.conversation_id),
    ]
    for _ in range(20):
        recovered = await repository.get_turn(
            str(USER_ID),
            started.turn_id,
        )
        if recovered is not None and recovered.status.value == "processing":
            break
        await asyncio.sleep(0)

    assert recovered is not None
    assert recovered.status.value == "processing"
    assert executor.calls == 2
    events = await repository.list_events(
        str(USER_ID),
        conversation.conversation_id,
    )
    assert [event.type for event in events][-2:] == [
        AgentEventType.CONTEXT_COMPRESSION_COMPLETED,
        AgentEventType.INPUT_STATE_CHANGED,
    ]
    assert events[-1].payload["status"] == "processing"
    await service.aclose()


@pytest.mark.asyncio
async def test_r1_concurrent_turns_use_idempotency_and_context_cas() -> None:
    """同键并发只创建一次，不同键同版本并发只允许一个 Turn 获得版本。"""

    from app.gateway.routers import pixelflow_conversations

    app, task_store, repository = _r1_app()
    service: AgentRuntimeService = app.state.pixelflow_agent_runtime_service
    assignment = service.assignment_for_new_conversation({})
    conversation = await task_store.create_conversation(
        pixelflow_conversations.PixelFlowConversationRecord(
            conversation_id="r1-concurrent",
            user_id=str(USER_ID),
            context=assignment.context,
        )
    )
    first_request = {
        "client_input_id": str(CLIENT_INPUT_ID),
        "content": "并发幂等输入",
        "materials": [],
        "reply_to_message_id": None,
        "artifact_refs": [],
        "expected_context_version": 0,
    }
    same_key = await asyncio.gather(
        service.start_turn(
            user_id=str(USER_ID),
            conversation_id=conversation.conversation_id,
            request=first_request,
        ),
        service.start_turn(
            user_id=str(USER_ID),
            conversation_id=conversation.conversation_id,
            request=first_request,
        ),
    )
    assert same_key[0] == same_key[1]
    assert (
        len(
            await repository.list_turns(
                str(USER_ID),
                conversation.conversation_id,
            )
        )
        == 1
    )

    next_ids = (
        UUID("22222222-2222-4222-8222-222222222222"),
        UUID("33333333-3333-4333-8333-333333333333"),
    )
    results = await asyncio.gather(
        *(
            service.start_turn(
                user_id=str(USER_ID),
                conversation_id=conversation.conversation_id,
                request={**first_request,
                    "client_input_id": str(client_input_id),
                    "content": f"并发 CAS {client_input_id}",
                    "expected_context_version": 1,
                },
            )
            for client_input_id in next_ids
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 1
    conflicts = [item for item in results if isinstance(item, AgentRuntimeContextConflictError)]
    assert len(conflicts) == 1
    assert conflicts[0].current_context_version == 2
    assert (
        len(
            await repository.list_turns(
                str(USER_ID),
                conversation.conversation_id,
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_r1_sql_turn_registration_is_atomic_across_service_instances() -> None:
    """两个 Service 竞争时，409 输入不能留下消息、Turn 或事件半成品。"""

    from app.gateway.routers import pixelflow_conversations

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[*AGENT_RUNTIME_TABLES,
                    PixelFlowConversationRow.__table__,
                    PixelFlowConversationMessageRow.__table__,
                ],
            ),
        )
    try:
        task_store = SQLPixelFlowTaskStore(session_factory)
        repository = SQLCompactionQueueRepository(session_factory)
        services = (
            AgentRuntimeService(
                config=_assist_config(),
                repository=repository,
                task_store=task_store,
                clock=lambda: NOW,
            ),
            AgentRuntimeService(
                config=_assist_config(),
                repository=repository,
                task_store=task_store,
                clock=lambda: NOW,
            ),
        )
        assignment = services[0].assignment_for_new_conversation({})
        conversation = await task_store.create_conversation(
            pixelflow_conversations.PixelFlowConversationRecord(
                conversation_id="r1-sql-atomic",
                user_id=str(USER_ID),
                context=assignment.context,
            ),
        )
        base_request = {
            "content": "跨 Service 原子登记",
            "materials": [],
            "reply_to_message_id": None,
            "artifact_refs": [],
            "expected_context_version": 0,
        }

        same_key = await asyncio.gather(
            *(
                service.start_turn(
                    user_id=str(USER_ID),
                    conversation_id=conversation.conversation_id,
                    request={**base_request,
                        "client_input_id": str(CLIENT_INPUT_ID),
                    },
                )
                for service in services
            ),
        )
        assert same_key[0] == same_key[1]

        competing_ids = (
            UUID("22222222-2222-4222-8222-222222222222"),
            UUID("33333333-3333-4333-8333-333333333333"),
        )
        results = await asyncio.gather(
            *(
                service.start_turn(
                    user_id=str(USER_ID),
                    conversation_id=conversation.conversation_id,
                    request={**base_request,
                        "client_input_id": str(client_input_id),
                        "expected_context_version": 1,
                    },
                )
                for service, client_input_id in zip(
                    services,
                    competing_ids,
                    strict=True,
                )
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(item, Exception) for item in results) == 1
        assert sum(isinstance(item, AgentRuntimeContextConflictError) for item in results) == 1
        accepted_result = next(item for item in results if not isinstance(item, Exception))
        assert accepted_result.status == "queued"
        turns = await repository.list_turns(
            str(USER_ID),
            conversation.conversation_id,
        )
        messages = await task_store.list_conversation_messages(
            conversation.conversation_id,
            user_id=str(USER_ID),
        )
        events = await repository.list_events(
            str(USER_ID),
            conversation.conversation_id,
        )
        restored = await task_store.get_conversation(
            conversation.conversation_id,
            user_id=str(USER_ID),
        )
        assert len(turns) == len(messages) == 2
        assert [turn.status.value for turn in turns] == [
            "accepted",
            "queued",
        ]
        assert len(events) == 4
        assert restored is not None
        assert restored.context[AGENT_RUNTIME_CONTEXT_KEY]["context_version"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r1_sql_context_payload_store_is_idempotent_and_owner_scoped() -> None:
    """生产 SQL Store 可恢复完整外置载荷，重复写入不产生第二份。"""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=list(AGENT_RUNTIME_SUPPORT_TABLES),
            ),
        )
    try:
        store = SQLContextPayloadStore(session_factory)
        externalizer = ContextPayloadExternalizer(
            store=store,
            externalize_min_bytes=1_000,
        )
        payload = {
            "current_input": {
                "content": "当前输入",
                "materials": ["保持完整"],
            },
            "recent_messages": [
                {
                    "message_id": "r1-sql-payload",
                    "role": "assistant",
                    "artifact": {
                        "artifact_ref": "artifact:r1-sql",
                        "content": "完整载荷:" + ("S" * 5_000),
                    },
                },
            ],
        }
        first = await externalizer.externalize(
            user_id=str(USER_ID),
            conversation_id="r1-sql-payload",
            payload=payload,
        )
        second = await externalizer.externalize(
            user_id=str(USER_ID),
            conversation_id="r1-sql-payload",
            payload=payload,
        )
        assert first.externalized == second.externalized
        payload_id = first.externalized[0].external_ref.removeprefix(
            "context-payload:",
        )
        restored = await store.get_context_payload(
            payload_id,
            user_id=str(USER_ID),
            conversation_id="r1-sql-payload",
        )
        hidden = await store.get_context_payload(
            payload_id,
            user_id="other-user",
            conversation_id="r1-sql-payload",
        )

        assert restored is not None
        assert restored.payload["artifact_ref"] == "artifact:r1-sql"
        assert restored.payload["content"].endswith("S" * 5_000)
        assert hidden is None
    finally:
        await engine.dispose()


def test_flag_off_keeps_legacy_conversation_and_rejects_runtime_endpoints() -> None:
    """Feature Flag 关闭时不写 Runtime 命名空间，也不改变旧消息 API。"""

    app, _, _ = _r1_app(config=AgentRuntimeConfig())

    with TestClient(app) as client:
        created = client.post(
            "/agent/conversations",
            json={"title": "旧流程", "context": {"legacy": True}},
        )
        conversation_id = created.json()["conversation_id"]
        message = client.post(
            f"/agent/conversations/{conversation_id}/messages",
            json={
                "role": "user",
                "content": "仍走旧入口",
                "payload": {"client_message_id": "legacy-message"},
            },
        )
        snapshot = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )

    assert created.status_code == message.status_code == 200
    assert created.json()["orchestration_mode"] == "frontend_v2"
    assert AGENT_RUNTIME_CONTEXT_KEY not in created.json()["context"]
    assert message.json()["content"] == "仍走旧入口"
    assert snapshot.status_code == 409


def test_r1_runtime_endpoints_keep_owner_isolation_and_context_conflict_contract() -> None:
    """新入口沿用对话 owner 隔离，版本冲突只返回安全的结构化元数据。"""

    app, task_store, repository = _r1_app()
    with TestClient(app) as client:
        conversation_id = client.post(
            "/agent/conversations",
            json={"title": "归属隔离"},
        ).json()["conversation_id"]
        conflict = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": str(CLIENT_INPUT_ID),
                "content": "使用错误版本",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 9,
            },
        )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "agent_runtime_context_conflict",
        "expected_context_version": 9,
        "current_context_version": 0,
    }

    other_user_id = UUID("99999999-9999-4999-8999-999999999999")

    def other_user() -> User:
        return User(
            email="m13-other@example.com",
            password_hash="x",
            system_role="user",
            id=other_user_id,
        )

    from app.gateway.routers import pixelflow_conversations

    other_app = make_authed_test_app(user_factory=other_user)
    other_app.state.pixelflow_task_store = task_store
    other_app.state.pixelflow_agent_runtime_service = AgentRuntimeService(
        config=_assist_config(),
        repository=repository,
        task_store=task_store,
        clock=lambda: NOW,
    )
    other_app.include_router(pixelflow_conversations.router)
    with TestClient(other_app) as client:
        hidden_snapshot = client.get(
            f"/agent/conversations/{conversation_id}/agent-snapshot",
        )
        hidden_turn = client.post(
            f"/agent/conversations/{conversation_id}/turns/start",
            json={
                "client_input_id": str(CLIENT_INPUT_ID),
                "content": "跨用户读取",
                "materials": [],
                "reply_to_message_id": None,
                "artifact_refs": [],
                "expected_context_version": 0,
            },
        )

    assert hidden_snapshot.status_code == 404
    assert hidden_turn.status_code == 404
