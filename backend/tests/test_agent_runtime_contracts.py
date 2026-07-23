from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentEvent,
    AgentEventType,
    AgentIntent,
    ContextBudgetReport,
    ContextEnvelope,
    ContextRequest,
    ContextSummary,
    ConversationOrchestration,
    ExternalJobRef,
    ExternalJobStatus,
    OperationRequest,
    OrchestrationMode,
    TurnRecord,
    TurnStartRequest,
    TurnStatus,
    WorkflowKind,
    WorkflowRecord,
    WorkflowStatus,
)
from pixelflow.agent_runtime.fakes import FakeContextPort, FakeOperationPort
from pixelflow.agent_runtime.ports import ContextPort, OperationConflictError, OperationPort

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "agent_runtime" / "contracts-v1.json"


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
    assert {item.value for item in OrchestrationMode} == {
        "frontend_v2",
        "supervisor_v1",
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
        "interrupt.closed",
        "external_job.state_changed",
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
                "orchestration_mode": "supervisor_v1",
                "orchestration_version": 1,
                "owner": "frontend",
            }
        )


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
