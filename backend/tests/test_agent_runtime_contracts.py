from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.config import AgentRuntimeConfig
from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentEventType,
    AgentIntent,
    AgentInterruptProjection,
    ContextBudgetReport,
    ContextEnvelope,
    ContextRequest,
    ContextSummary,
    ConversationOrchestration,
    ExplicitActionSignal,
    ExternalJobRef,
    ExternalJobStatus,
    InterruptResponseRequest,
    OperationRequest,
    OrchestrationMode,
    TurnRecord,
    TurnStartRequest,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.conversation_router import ConversationRouteService
from pixelflow.agent_runtime.fakes import FakeContextPort, FakeOperationPort
from pixelflow.agent_runtime.identity import (
    conversation_message_id,
    interrupt_id,
    projection_message_id,
    turn_id,
    workflow_id,
)
from pixelflow.agent_runtime.persistence import MemoryCompactionQueueRepository
from pixelflow.agent_runtime.ports import ContextPort, OperationConflictError, OperationPort
from pixelflow.agent_runtime.service import (
    AgentRuntimeService,
    AgentRuntimeUnavailableError,
)
from pixelflow.tasks import MemoryPixelFlowTaskStore, PixelFlowConversationRecord

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "agent_runtime" / "contracts-v1.json"


class _NonJsonPayload:
    """用于证明自定义类实例不能越过 JSON 合同边界。"""

    def __init__(self) -> None:
        self.value = "not-json"


def _cyclic_json_candidate() -> dict[str, object]:
    value: dict[str, object] = {}
    value["self"] = value
    return value


@pytest.fixture(scope="module")
def contract_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_normative_fixture_root_is_frozen(contract_fixture: dict[str, object]) -> None:
    assert contract_fixture["schema_version"] == 1
    assert set(contract_fixture) == {
        "schema_version",
        "orchestration",
        "action_decision",
        "external_job_ref",
        "workflow_record",
        "turn_record",
        "context_summary",
        "context_envelope",
        "event",
        "turn_start_request",
        "interrupt_response_request",
        "interrupt_projection",
        "operation_request",
        "context_request",
    }


@pytest.mark.parametrize(
    ("fixture_key", "model_type"),
    [
        ("orchestration", ConversationOrchestration),
        ("action_decision", ActionDecision),
        ("external_job_ref", ExternalJobRef),
        ("workflow_record", WorkflowRecord),
        ("turn_record", TurnRecord),
        ("context_summary", ContextSummary),
        ("context_envelope", ContextEnvelope),
        ("event", AgentEvent),
        ("turn_start_request", TurnStartRequest),
        ("interrupt_response_request", InterruptResponseRequest),
        ("interrupt_projection", AgentInterruptProjection),
        ("operation_request", OperationRequest),
        ("context_request", ContextRequest),
    ],
)
def test_normative_fixture_round_trips_without_contract_drift(
    contract_fixture: dict[str, object],
    fixture_key: str,
    model_type: type,
) -> None:
    payload = contract_fixture[fixture_key]

    parsed = model_type.model_validate(payload)

    assert parsed.model_dump(mode="json") == payload


def test_contract_enums_match_the_frozen_wire_values() -> None:
    assert AgentEventType.EXTERNAL_JOB_QUOTA_STATE_CHANGED.value == (
        "external_job.quota_state_changed"
    )
    assert {item.value for item in OrchestrationMode} == {
        "frontend_v2",
        "video_agent_v2",
    }
    assert {item.value for item in AgentAction} == {
        "answer_only",
        "continue_workflow",
        "modify_workflow",
        "regenerate_stage",
        "retry_failed",
        "start_workflow",
        "switch_workflow",
        "cancel_workflow",
        "clarify",
    }
    assert {item.value for item in AgentEventType} == {
        "run.state_changed",
        "context.compression_started",
        "context.compression_progressed",
        "context.compression_completed",
        "context.compression_failed",
        "input.state_changed",
        "message.upserted",
        "workflow.progressed",
        "interrupt.opened",
        "interrupt.responded",
        "interrupt.closed",
        "external_job.state_changed",
        "external_job.quota_state_changed",
        "agent.plan.created",
        "agent.plan.updated",
        "agent.step.started",
        "agent.step.progressed",
        "agent.step.completed",
        "agent.step.failed",
        "agent.thinking.started",
        "agent.thinking.delta",
        "agent.thinking.completed",
        "agent.reasoning_summary.delta",
        "agent.reasoning_summary.completed",
        "agent.tool.started",
        "agent.tool.progress",
        "agent.tool.completed",
        "agent.tool.failed",
        "agent.operation.updated",
        "agent.artifact.updated",
        "agent.response.delta",
        "agent.response.completed",
        "agent.confirmation.requested",
        "agent.route.decided",
        "error.raised",
    }
    assert {item.value for item in AgentIntent} == {
        "image",
        "video",
        "ppt",
        "video_analysis",
        "general",
    }
    assert {item.value for item in WorkflowKind} == {
        "image",
        "video",
        "ppt",
        "video_analysis",
    }
    assert {item.value for item in WorkflowStatus} == {
        "draft",
        "awaiting_user",
        "running",
        "paused_quota",
        "failed",
        "completed",
        "cancelled",
    }
    assert {item.value for item in TurnStatus} == {
        "accepted",
        "queued",
        "processing",
        "waiting_user",
        "completed",
        "failed",
    }
    assert {item.value for item in ExternalJobStatus} == {
        "created",
        "polling",
        "succeeded",
        "failed",
        "timeout",
        "expired",
    }


def test_contracts_reject_unknown_fields_and_invalid_clarification() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ConversationOrchestration.model_validate(
            {
                "orchestration_mode": "video_agent_v2",
                "orchestration_version": 1,
                "owner": "frontend",
            }
        )


def test_turn_start_accepts_strict_explicit_action() -> None:
    request = TurnStartRequest.model_validate(
        {
            "client_input_id": "11111111-1111-4111-8111-111111111111",
            "content": "确认这个方案",
            "materials": [],
            "reply_to_message_id": "message-plan-v1",
            "artifact_refs": ["artifact:video-plan:wf-1:v1"],
            "expected_context_version": 3,
            "explicit_action": {
                "action": "continue_workflow",
                "intent": "video",
                "workflow_id": "wf-1",
                "stage": "plan_review",
                "artifact_ref": "artifact:video-plan:wf-1:v1",
                "patch": {"approved": True},
            },
        }
    )

    assert request.explicit_action is not None
    assert request.explicit_action.action is AgentAction.CONTINUE_WORKFLOW
    assert request.explicit_action.patch == {"approved": True}


def test_live_action_and_interrupt_contracts_fail_closed() -> None:
    valid_action = {
        "action": "continue_workflow",
        "intent": "video",
        "workflow_id": "wf-1",
        "stage": "plan_review",
        "artifact_ref": "artifact:video-plan:wf-1:v1",
        "patch": {"approved": True},
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExplicitActionSignal.model_validate({**valid_action, "unknown": True})
    with pytest.raises(ValidationError, match="workflow_id"):
        ExplicitActionSignal.model_validate({**valid_action, "workflow_id": ""})
    with pytest.raises(ValidationError, match="patch"):
        ExplicitActionSignal.model_validate({**valid_action, "patch": {"invalid": {1, 2}}})
    with pytest.raises(ValidationError, match="client_response_id"):
        InterruptResponseRequest.model_validate(
            {
                "client_response_id": "not-a-uuid",
                "value": {
                    "content": "确认这个方案",
                    "explicit_action": valid_action,
                },
            }
        )


@pytest.mark.parametrize(
    "invalid_value",
    [
        math.nan,
        math.inf,
        -math.inf,
        datetime(2026, 7, 31, tzinfo=UTC),
        MappingProxyType({"value": "map-like"}),
        _NonJsonPayload(),
        pytest.param(None, id="cyclic-reference"),
    ],
)
def test_live_patch_rejects_every_non_json_value(invalid_value: object) -> None:
    candidate = _cyclic_json_candidate() if invalid_value is None else invalid_value

    with pytest.raises(ValidationError):
        ExplicitActionSignal.model_validate(
            {
                "action": "continue_workflow",
                "intent": "video",
                "workflow_id": "wf-1",
                "stage": "plan_review",
                "artifact_ref": "artifact:video-plan:wf-1:v1",
                "patch": {"invalid": candidate},
            }
        )


def test_live_materials_and_projection_payload_reject_non_json_values() -> None:
    with pytest.raises(ValidationError, match="materials"):
        TurnStartRequest.model_validate(
            {
                "client_input_id": "11111111-1111-4111-8111-111111111111",
                "content": "继续",
                "materials": [{"score": math.nan}],
                "expected_context_version": 0,
            }
        )

    with pytest.raises(ValidationError, match="payload"):
        AgentInterruptProjection.model_validate(
            {
                "interrupt_id": "interrupt-1",
                "conversation_id": "conversation-1",
                "workflow_id": "wf-1",
                "turn_id": "turn-1",
                "kind": "plan_review",
                "reason_code": "plan_review_required",
                "payload": {"opened": math.inf},
                "opened_at": "2026-07-31T12:00:00Z",
            }
        )


def test_live_ids_are_stable_and_scope_sensitive() -> None:
    client_id = UUID("11111111-1111-4111-8111-111111111111")

    assert conversation_message_id("conversation-1", client_id) == (
        "d9616369ac9f5a0f84ac41699101992e"
    )
    assert turn_id("conversation-1", client_id) == (
        "turn_8c827631fce25ab1a3cd64b522fbe185"
    )
    assert workflow_id("conversation-1", client_id) == (
        "wf_4e5b3467cfd35d1ba6ec681b8d51d2ff"
    )
    assert workflow_id("conversation-1", client_id) != workflow_id(
        "conversation-2",
        client_id,
    )
    assert interrupt_id("turn-1", "plan_review_required") == (
        "interrupt_c3e25c1142c658aabdb322aa795aeefd"
    )
    assert projection_message_id("workflow-1", "plan_review", 1, "approve") == (
        "001b1b0d3cb05fcead80be648a671dab"
    )


def test_live_ids_preserve_component_boundaries_with_colons() -> None:
    client_id = UUID("11111111-1111-4111-8111-111111111111")

    assert interrupt_id("turn:a", "reason") != interrupt_id("turn", "a:reason")
    assert projection_message_id("wf:a", "plan", 1, "approve") != (
        projection_message_id("wf", "a:plan", 1, "approve")
    )
    assert conversation_message_id("conversation:1", client_id) != (
        conversation_message_id("conversation", client_id)
    )
    assert turn_id("conversation:1", client_id) != turn_id("conversation", client_id)
    assert workflow_id("conversation:1", client_id) != workflow_id(
        "conversation",
        client_id,
    )


@pytest.mark.asyncio
async def test_turn_registration_copies_explicit_action_into_authoritative_message() -> None:
    task_store = MemoryPixelFlowTaskStore()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="assist",
            new_conversation_rollout_percent=100,
        ),
        repository=MemoryCompactionQueueRepository(),
        task_store=task_store,
    )
    assignment = service.assignment_for_new_conversation({})
    user_id = "user-contract"
    action = {
        "action": "continue_workflow",
        "intent": "video",
        "workflow_id": "wf-1",
        "stage": "plan_review",
        "artifact_ref": "artifact:video-plan:wf-1:v1",
        "patch": {"approved": True},
    }
    for conversation_id in ("conversation-with-action", "conversation-without-action"):
        await task_store.create_conversation(
            PixelFlowConversationRecord(
                conversation_id=conversation_id,
                user_id=user_id,
                context=assignment.context,
            )
        )

    await service.start_turn(
        user_id=user_id,
        conversation_id="conversation-with-action",
        request={
            "client_input_id": "11111111-1111-4111-8111-111111111111",
            "content": "确认这个方案",
            "expected_context_version": 0,
            "explicit_action": action,
        },
    )
    await service.start_turn(
        user_id=user_id,
        conversation_id="conversation-without-action",
        request={
            "client_input_id": "22222222-2222-4222-8222-222222222222",
            "content": "继续聊聊",
            "expected_context_version": 0,
        },
    )
    action["patch"]["approved"] = False

    with_action = await task_store.list_conversation_messages(
        "conversation-with-action",
        user_id=user_id,
    )
    without_action = await task_store.list_conversation_messages(
        "conversation-without-action",
        user_id=user_id,
    )
    assert with_action[0].payload["explicit_action"] == {
        "action": "continue_workflow",
        "intent": "video",
        "workflow_id": "wf-1",
        "stage": "plan_review",
        "artifact_ref": "artifact:video-plan:wf-1:v1",
        "patch": {"approved": True},
    }
    assert without_action[0].payload["explicit_action"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("polluted_field", ["materials", "explicit_action.patch"])
async def test_start_turn_revalidates_mutated_dto_before_registration(
    polluted_field: str,
) -> None:
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="assist",
            new_conversation_rollout_percent=100,
        ),
        repository=repository,
        task_store=task_store,
    )
    assignment = service.assignment_for_new_conversation({})
    user_id = "user-mutated-dto"
    conversation_id = f"conversation-mutated-{polluted_field}"
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            context=assignment.context,
        )
    )
    request = TurnStartRequest.model_validate(
        {
            "client_input_id": "11111111-1111-4111-8111-111111111111",
            "content": "确认这个方案",
            "materials": [],
            "expected_context_version": 0,
            "explicit_action": {
                "action": "continue_workflow",
                "intent": "video",
                "workflow_id": "wf-1",
                "stage": "plan_review",
                "artifact_ref": "artifact:video-plan:wf-1:v1",
                "patch": {"approved": True},
            },
        }
    )

    if polluted_field == "materials":
        request.materials.append({"score": math.inf})
    else:
        assert request.explicit_action is not None
        request.explicit_action.patch["invalid"] = math.nan

    with pytest.raises(ValidationError):
        await service.start_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            request=request,
        )

    assert await task_store.list_conversation_messages(
        conversation_id,
        user_id=user_id,
    ) == []
    assert await repository.list_turns(user_id, conversation_id) == []


@pytest.mark.asyncio
async def test_primary_video_turn_fails_before_registration_when_native_runtime_is_unavailable() -> None:
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=repository,
        task_store=task_store,
        conversation_router=ConversationRouteService(),
        primary_execution_intents=(),
    )
    assignment = service.assignment_for_new_conversation({})
    user_id = "user-native-runtime-unavailable"
    conversation_id = "conversation-native-runtime-unavailable"
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            context=assignment.context,
            orchestration_mode=assignment.orchestration_mode,
            orchestration_version=assignment.orchestration_version,
        )
    )

    with pytest.raises(AgentRuntimeUnavailableError):
        await service.start_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            request={
                "client_input_id": "33333333-3333-4333-8333-333333333333",
                "content": "生成一条商品视频",
                "expected_context_version": 0,
            },
        )

    assert await task_store.list_conversation_messages(
        conversation_id,
        user_id=user_id,
    ) == []
    assert await repository.list_turns(user_id, conversation_id) == []


@pytest.mark.asyncio
async def test_route_clarification_completes_turn_instead_of_blocking_queue() -> None:
    task_store = MemoryPixelFlowTaskStore()
    repository = MemoryCompactionQueueRepository()
    service = AgentRuntimeService(
        config=AgentRuntimeConfig(
            mode="primary",
            enabled_intents=("video",),
            new_conversation_rollout_percent=100,
        ),
        repository=repository,
        task_store=task_store,
        conversation_router=ConversationRouteService(
            llm_classifier=lambda *_args: (_ for _ in ()).throw(RuntimeError("不可用")),
        ),
        primary_execution_intents=(),
    )
    assignment = service.assignment_for_new_conversation({})
    user_id = "user-route-clarification"
    conversation_id = "conversation-route-clarification"
    await task_store.create_conversation(
        PixelFlowConversationRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            context=assignment.context,
            orchestration_mode=assignment.orchestration_mode,
            orchestration_version=assignment.orchestration_version,
        )
    )

    await service.start_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        request={
            "client_input_id": "44444444-4444-4444-8444-444444444444",
            "content": "照这个做一版",
            "expected_context_version": 0,
        },
    )

    turns = await repository.list_turns(user_id, conversation_id)
    assert len(turns) == 1
    assert turns[0].status is TurnStatus.COMPLETED


def test_non_mutating_decision_and_nested_job_fail_closed(
    contract_fixture: dict[str, object],
) -> None:
    decision_payload = dict(contract_fixture["action_decision"])
    decision_payload.update(
        action="answer_only",
        requires_confirmation=False,
        target_stage=None,
    )
    with pytest.raises(ValidationError, match="patch"):
        ActionDecision.model_validate(decision_payload)

    workflow_payload = dict(contract_fixture["workflow_record"])
    pending_job = dict(workflow_payload["pending_external_job"])
    pending_job["workflow_id"] = "wf_other"
    workflow_payload["pending_external_job"] = pending_job
    with pytest.raises(ValidationError, match="pending_external_job"):
        WorkflowRecord.model_validate(workflow_payload)


def test_context_budget_requires_the_frozen_usable_input_formula(
    contract_fixture: dict[str, object],
) -> None:
    budget_payload = dict(contract_fixture["context_envelope"]["budget_report"])
    budget_payload["usable_input_tokens"] += 1

    with pytest.raises(ValidationError, match="usable_input_tokens"):
        ContextBudgetReport.model_validate(budget_payload)

    utilization_payload = dict(contract_fixture["context_envelope"]["budget_report"])
    utilization_payload["utilization"] = 0.01
    with pytest.raises(ValidationError, match="utilization"):
        ContextBudgetReport.model_validate(utilization_payload)

    overflow_payload = dict(contract_fixture["context_envelope"]["budget_report"])
    overflow_payload.update(estimated_input_tokens=180224, utilization=2.0)
    assert ContextBudgetReport.model_validate(overflow_payload).utilization == 2.0

    with pytest.raises(ValidationError, match="clarification_question"):
        ActionDecision.model_validate(
            {
                "action": "clarify",
                "intent": "general",
                "confidence": 0.5,
                "requires_confirmation": False,
                "patch": {},
                "reason_code": "ambiguous_target",
                "idempotency_key": "decision:turn_002",
            }
        )


@pytest.mark.asyncio
async def test_fake_operation_port_reuses_claim_and_rejects_payload_conflicts(
    contract_fixture: dict[str, object],
) -> None:
    operation_port = FakeOperationPort()
    assert isinstance(operation_port, OperationPort)
    request = OperationRequest.model_validate(contract_fixture["operation_request"])

    first = await operation_port.claim(request)
    repeated = await operation_port.claim(request)

    assert repeated == first
    assert repeated.job_id == first.job_id
    conflicting = request.model_copy(update={"request_hash": "sha256:different-body"})
    with pytest.raises(OperationConflictError, match="idempotency_key"):
        await operation_port.claim(conflicting)

    for field, value in (
        ("workflow_id", "wf_other"),
        ("stage", "image_review"),
        ("stage_version", 2),
        ("attempt", 2),
    ):
        with pytest.raises(OperationConflictError, match="idempotency_key"):
            await operation_port.claim(request.model_copy(update={field: value}))

    saved = first.model_copy(update={"status": ExternalJobStatus.SUCCEEDED})
    assert await operation_port.save(saved) == saved
    assert await operation_port.get(first.job_id) == saved
    assert await operation_port.get("missing-job") is None

    for field, value in (
        ("workflow_id", "wf_other"),
        ("stage", "image_review"),
        ("attempt", 2),
        ("idempotency_key", "operation:other"),
    ):
        with pytest.raises(OperationConflictError, match="identity"):
            await operation_port.save(saved.model_copy(update={field: value}))


@pytest.mark.asyncio
async def test_fake_context_port_returns_an_isolated_envelope_for_current_input(
    contract_fixture: dict[str, object],
) -> None:
    template = ContextEnvelope.model_validate(contract_fixture["context_envelope"])
    context_port = FakeContextPort({("user_001", "conv_001"): template})
    assert isinstance(context_port, ContextPort)
    request = ContextRequest.model_validate(contract_fixture["context_request"])

    first = await context_port.assemble(request)
    first.recent_messages.append({"message_id": "mutated"})
    second = await context_port.assemble(request.model_copy(update={"current_input": "继续"}))

    assert second.current_input == "继续"
    assert second.recent_messages == contract_fixture["context_envelope"]["recent_messages"]

    with pytest.raises(KeyError, match="unknown-conversation"):
        await context_port.assemble(request.model_copy(update={"conversation_id": "unknown-conversation"}))
    with pytest.raises(KeyError, match="another-user"):
        await context_port.assemble(request.model_copy(update={"user_id": "another-user"}))
    with pytest.raises(KeyError, match="wf_other"):
        await context_port.assemble(request.model_copy(update={"target_workflow_id": "wf_other"}))


def test_fake_context_port_rejects_cross_conversation_templates(
    contract_fixture: dict[str, object],
) -> None:
    template = ContextEnvelope.model_validate(contract_fixture["context_envelope"])

    wrong_workflow = template.model_copy(deep=True)
    assert wrong_workflow.active_or_target_workflow is not None
    wrong_workflow.active_or_target_workflow.conversation_id = "conv_other"
    with pytest.raises(ValueError, match="active_or_target_workflow"):
        FakeContextPort({("user_001", "conv_001"): wrong_workflow})

    wrong_summary = template.model_copy(deep=True)
    assert wrong_summary.conversation_summary is not None
    wrong_summary.conversation_summary.conversation_id = "conv_other"
    with pytest.raises(ValueError, match="conversation_summary"):
        FakeContextPort({("user_001", "conv_001"): wrong_summary})

    wrong_related = template.model_copy(deep=True)
    assert wrong_related.conversation_summary is not None
    wrong_related.related_workflow_summaries = [wrong_related.conversation_summary.model_copy(deep=True)]
    wrong_related.related_workflow_summaries[0].conversation_id = "conv_other"
    with pytest.raises(ValueError, match="related_workflow_summaries"):
        FakeContextPort({("user_001", "conv_001"): wrong_related})
