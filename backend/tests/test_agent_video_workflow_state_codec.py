from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy

import pytest
import test_agent_video_workflow_delivery as delivery_tests
import test_agent_video_workflow_generation as generation_tests
import test_agent_video_workflow_planning as planning_tests
import test_agent_video_workflow_postproduction as postproduction_tests

from pixelflow.agent_workflows.video import (
    VideoDeliveryWorkflowService,
    VideoPlanningWorkflowService,
    VideoWorkflowState,
    VideoWorkflowStateEnvelope,
    canonical_payload_sha256,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)

StateFactory = Callable[[], Awaitable[VideoWorkflowState]]


async def _planning_state() -> VideoWorkflowState:
    return planning_tests._advance_to_plan_review(VideoPlanningWorkflowService())


async def _scene_package_state() -> VideoWorkflowState:
    return generation_tests._reviewed_scene_package_state()


async def _scene_generation_state() -> VideoWorkflowState:
    state, _, _ = await postproduction_tests._complete_generation()
    return state


async def _postproduction_state() -> VideoWorkflowState:
    state, _, _, _ = await delivery_tests._video_review_state()
    return state


async def _delivery_state() -> VideoWorkflowState:
    state, operation_port, _, _ = await delivery_tests._video_review_state()
    return await VideoDeliveryWorkflowService(operation_port).initialize(state)


STATE_FACTORIES: tuple[StateFactory, ...] = (
    _planning_state,
    _scene_package_state,
    _scene_generation_state,
    _postproduction_state,
    _delivery_state,
)


def _payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _replace_payload(
    envelope: VideoWorkflowStateEnvelope,
    payload: object,
) -> VideoWorkflowStateEnvelope:
    return envelope.model_copy(
        update={
            "payload": payload,
            "payload_sha256": _payload_sha256(payload),
        }
    )


def _first_frozen_list(value: object) -> tuple[object, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, dict):
        children = value.values()
    elif hasattr(value, "values"):
        children = value.values()
    else:
        children = ()
    for child in children:
        try:
            return _first_frozen_list(child)
        except LookupError:
            continue
    raise LookupError("测试状态中没有 JSON 数组")


@pytest.mark.asyncio
@pytest.mark.parametrize("state_factory", STATE_FACTORIES)
async def test_video_state_codec_round_trips_without_mutable_alias(
    state_factory: StateFactory,
) -> None:
    state = await state_factory()
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=state,
        workflow_version=4,
        last_turn_id="turn-4",
        last_action_key="decision:turn-4",
    )

    serialized_envelope = VideoWorkflowStateEnvelope.model_validate_json(envelope.model_dump_json())
    restored = decode_video_workflow_state(serialized_envelope)

    assert project_video_workflow_state(restored).model_dump(mode="json") == (
        project_video_workflow_state(state).model_dump(mode="json")
    )
    payload = envelope.model_dump(mode="python")["payload"]
    payload["workflow_id"] = "attacker"
    assert project_video_workflow_state(restored).workflow_id == state.workflow_id
    with pytest.raises(TypeError):
        envelope.payload["workflow_id"] = "tampered"
    with pytest.raises((AttributeError, TypeError)):
        _first_frozen_list(envelope.payload).append("tampered")
    json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
    json.loads(envelope.model_dump_json())


@pytest.mark.asyncio
async def test_video_state_codec_rejects_checksum_schema_and_envelope_version_tampering() -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _planning_state(),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )

    with pytest.raises(ValueError, match="摘要"):
        decode_video_workflow_state(
            envelope.model_copy(update={"payload_sha256": "sha256:" + "0" * 64})
        )
    with pytest.raises(ValueError, match="schema_version"):
        decode_video_workflow_state(envelope.model_copy(update={"schema_version": 2}))
    with pytest.raises(ValueError, match="workflow_version"):
        decode_video_workflow_state(envelope.model_copy(update={"workflow_version": 0}))


@pytest.mark.asyncio
@pytest.mark.parametrize("state_factory", STATE_FACTORIES)
async def test_video_state_codec_rejects_unknown_or_missing_payload_keys(
    state_factory: StateFactory,
) -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await state_factory(),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    payload = envelope.model_dump(mode="json")["payload"]
    with_unknown = {**payload, "supplier_private": "unexpected"}
    without_status = {key: value for key, value in payload.items() if key != "status"}

    with pytest.raises(ValueError, match="字段"):
        decode_video_workflow_state(_replace_payload(envelope, with_unknown))
    with pytest.raises(ValueError, match="字段"):
        decode_video_workflow_state(_replace_payload(envelope, without_status))


@pytest.mark.asyncio
@pytest.mark.parametrize("non_finite_value", [float("nan"), float("inf"), float("-inf")])
async def test_video_state_codec_rejects_non_json_and_non_finite_payloads(
    non_finite_value: float,
) -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _planning_state(),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    non_json = dict(envelope.payload)
    non_json["intake_context"] = {"invalid": {"set"}}
    non_finite = envelope.model_dump(mode="json")["payload"]
    non_finite["intake_context"] = {"invalid": non_finite_value}

    with pytest.raises(ValueError, match="JSON"):
        decode_video_workflow_state(envelope.model_copy(update={"payload": non_json}))
    with pytest.raises(ValueError, match="JSON"):
        decode_video_workflow_state(envelope.model_copy(update={"payload": non_finite}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("current_stage", "supplier_stage", "current_stage"),
        ("status", "supplier_status", "status"),
        ("updated_at", "2020-01-01T00:00:00+00:00", "时间"),
    ],
)
async def test_video_state_codec_rejects_enum_or_time_tampering(
    field_name: str,
    value: object,
    message: str,
) -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _planning_state(),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    payload = envelope.model_dump(mode="json")["payload"]
    payload[field_name] = value

    with pytest.raises(ValueError, match=message):
        decode_video_workflow_state(_replace_payload(envelope, payload))


@pytest.mark.asyncio
async def test_video_state_codec_rejects_envelope_context_and_nested_identity_tampering() -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _scene_generation_state(),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    context_payload = envelope.model_dump(mode="json")["payload"]
    context_payload["context_version"] += 1
    nested_payload = deepcopy(envelope.model_dump(mode="json")["payload"])
    nested_payload["source_scene_package"]["workflow_id"] = "wf-attacker"

    with pytest.raises(ValueError, match="context_version"):
        decode_video_workflow_state(_replace_payload(envelope, context_payload))
    with pytest.raises(ValueError, match="Workflow"):
        decode_video_workflow_state(_replace_payload(envelope, nested_payload))


def test_video_state_payload_sha256_is_stable_for_key_order() -> None:
    left = {"outer": {"b": 2, "a": 1}, "items": [3, 4]}
    right = {"items": [3, 4], "outer": {"a": 1, "b": 2}}

    assert canonical_payload_sha256(left) == canonical_payload_sha256(right)
    assert canonical_payload_sha256(left) == _payload_sha256(left)
