from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
import test_agent_video_workflow_delivery as delivery_tests
import test_agent_video_workflow_generation as generation_tests
import test_agent_video_workflow_planning as planning_tests
import test_agent_video_workflow_postproduction as postproduction_tests

from pixelflow.agent_workflows.video import (
    VideoDeliveryWorkflowService,
    VideoPlanningWorkflowService,
    VideoPostProductionWorkflowService,
    VideoSceneGenerationWorkflowService,
    VideoWorkflowState,
    VideoWorkflowStateEnvelope,
    canonical_payload_sha256,
    canonical_video_workflow_envelope_sha256,
    decode_video_workflow_state,
    encode_video_workflow_state,
    project_video_workflow_state,
)

StateFactory = Callable[[], Awaitable[VideoWorkflowState]]


async def _planning_state() -> VideoWorkflowState:
    return planning_tests._advance_to_plan_review(VideoPlanningWorkflowService())


async def _approved_planning_state() -> VideoWorkflowState:
    service = VideoPlanningWorkflowService()
    state = planning_tests._advance_to_plan_review(service)
    return service.approve_plan(state, now=state.updated_at)


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


async def _generation_pending_state() -> VideoWorkflowState:
    package = generation_tests._reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(generation_tests._AtomicFakeOperationPort())
    return await service.start_from_reviewed_scene_package(package)


async def _generation_failed_state() -> VideoWorkflowState:
    package = generation_tests._reviewed_scene_package_state()
    service = VideoSceneGenerationWorkflowService(generation_tests._AtomicFakeOperationPort())
    state = await service.start_from_reviewed_scene_package(package)
    return await service.record_scene_failure(
        state,
        scene_id=state.scene_packages[0]["scene_id"],
        error="额度不足，请充值后继续",
        attempts=1,
        quota_insufficient=True,
        retryable=False,
    )


async def _generation_edited_dirty_state() -> VideoWorkflowState:
    state, _, service = await postproduction_tests._complete_generation()
    target = state.scene_packages[1]
    return service.modify_scene(
        state,
        scene_id=target["scene_id"],
        patch={
            "storyline": "第二镜增加智能戒指健康趋势特写。",
            "narration": "全天趋势，一眼掌握。",
        },
    )


async def _postproduction_merge_error_state() -> VideoWorkflowState:
    generation, operation_port, _ = await postproduction_tests._complete_generation()
    service = VideoPostProductionWorkflowService(operation_port)
    state = await service.start_merge(generation)
    state = await postproduction_tests._claim_started(state, operation_port)
    return await service.record_merge_failure(
        state,
        error="额度不足，请充值后继续",
        attempts=1,
        quota_insufficient=True,
    )


async def _postproduction_quality_feedback_state() -> VideoWorkflowState:
    state, _, _, service = await delivery_tests._video_review_state()
    return await service.start_quality_review(
        state,
        user_feedback="请检查商品露出并保留当前用户意见",
    )


async def _postproduction_finalized_state() -> VideoWorkflowState:
    state, _, _, service = await delivery_tests._video_review_state()
    return await service.finish(state)


async def _delivery_pending_state() -> VideoWorkflowState:
    state, operation_port, _, _ = await delivery_tests._video_review_state()
    service = VideoDeliveryWorkflowService(operation_port)
    delivery = await service.initialize(state)
    return await service.start_jianying_draft(
        delivery,
        project_name="智能戒指新品广告",
    )


async def _delivery_completed_downloads_state() -> VideoWorkflowState:
    state, operation_port, _, _ = await delivery_tests._video_review_state()
    service = VideoDeliveryWorkflowService(operation_port)
    delivery = await service.initialize(state)
    delivery = await service.generate_jianying_with_skill(
        delivery,
        skill=delivery_tests._FakeJianyingDraftSkill([delivery_tests._succeeded_result()]),
    )
    version_id = delivery.current_storyboard_version_id
    draft_url = delivery.jianying_draft_records[version_id]["download_url"]
    delivery = await service.record_jianying_download(
        delivery,
        storyboard_version_id=version_id,
        download_url=draft_url,
        downloaded_at=delivery.updated_at + timedelta(seconds=1),
    )
    return await service.record_final_video_download(
        delivery,
        download_url=state.merged_video["video_url"],
        downloaded_at=delivery.updated_at + timedelta(seconds=1),
    )


STATE_FACTORIES: tuple[StateFactory, ...] = (
    _planning_state,
    _scene_package_state,
    _scene_generation_state,
    _postproduction_state,
    _delivery_state,
)

BRANCH_STATE_FACTORIES: tuple[tuple[str, StateFactory], ...] = (
    ("generation_pending", _generation_pending_state),
    ("generation_failed", _generation_failed_state),
    ("generation_edited_dirty", _generation_edited_dirty_state),
    ("postproduction_merge_error", _postproduction_merge_error_state),
    ("postproduction_quality_feedback", _postproduction_quality_feedback_state),
    ("postproduction_finalized", _postproduction_finalized_state),
    ("delivery_pending", _delivery_pending_state),
    ("delivery_completed_downloads", _delivery_completed_downloads_state),
)


def _envelope_sha256(envelope: VideoWorkflowStateEnvelope) -> str:
    signature_body = envelope.model_dump(mode="json", exclude={"payload_sha256"})
    encoded = json.dumps(
        signature_body,
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
    candidate = envelope.model_copy(update={"payload": payload})
    return candidate.model_copy(update={"payload_sha256": _envelope_sha256(candidate)})


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

    assert restored == state
    assert project_video_workflow_state(restored).model_dump(mode="json") == (
        project_video_workflow_state(state).model_dump(mode="json")
    )
    restored_envelope = encode_video_workflow_state(
        user_id="user-1",
        state=restored,
        workflow_version=4,
        last_turn_id="turn-4",
        last_action_key="decision:turn-4",
    )
    assert restored_envelope.model_dump(mode="json")["payload"] == envelope.model_dump(mode="json")["payload"]
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
@pytest.mark.parametrize(("branch_name", "state_factory"), BRANCH_STATE_FACTORIES)
async def test_video_state_codec_round_trips_nonempty_mutually_exclusive_branches(
    branch_name: str,
    state_factory: StateFactory,
) -> None:
    state = await state_factory()
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=state,
        workflow_version=7,
        last_turn_id="turn-7",
        last_action_key="decision:turn-7",
    )

    restored = decode_video_workflow_state(
        VideoWorkflowStateEnvelope.model_validate_json(envelope.model_dump_json())
    )

    assert restored == state
    assert encode_video_workflow_state(
        user_id="user-1",
        state=restored,
        workflow_version=7,
        last_turn_id="turn-7",
        last_action_key="decision:turn-7",
    ).model_dump(mode="json")["payload"] == envelope.model_dump(mode="json")["payload"]
    if branch_name == "generation_pending":
        assert restored.pending_operations
    elif branch_name == "generation_failed":
        assert restored.failed_scenes
    elif branch_name == "generation_edited_dirty":
        assert restored.edited_scene_ids and restored.dirty_scene_ids
    elif branch_name == "postproduction_merge_error":
        assert restored.merge_error
    elif branch_name == "postproduction_quality_feedback":
        assert restored.quality_feedback and restored.pending_operation
    elif branch_name == "postproduction_finalized":
        assert restored.finalized_by_user
    elif branch_name == "delivery_pending":
        assert restored.pending_jianying_request and restored.pending_operation
    else:
        version_id = restored.current_storyboard_version_id
        assert restored.jianying_draft_records[version_id]["draftDownloadedAt"]
        assert restored.final_video_delivery


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
@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("user_id", "user-attacker"),
        ("workflow_version", 5),
        ("last_turn_id", "turn-attacker"),
        ("last_action_key", "decision:attacker"),
    ],
)
async def test_video_state_codec_rejects_valid_envelope_metadata_tampering(
    field_name: str,
    tampered_value: object,
) -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _planning_state(),
        workflow_version=4,
        last_turn_id="turn-4",
        last_action_key="decision:turn-4",
    )

    with pytest.raises(ValueError, match="摘要"):
        decode_video_workflow_state(envelope.model_copy(update={field_name: tampered_value}))


@pytest.mark.asyncio
async def test_video_state_envelope_revalidation_copies_and_refreezes_mutable_payload() -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _planning_state(),
        workflow_version=4,
        last_turn_id="turn-4",
        last_action_key="decision:turn-4",
    )
    mutable_payload = envelope.model_dump(mode="json")["payload"]
    copied = envelope.model_copy(update={"payload": mutable_payload})

    validated = VideoWorkflowStateEnvelope.model_validate(copied)
    mutable_payload["workflow_id"] = "wf-mutated-after-validation"

    assert validated is not copied
    assert validated.payload["workflow_id"] == envelope.workflow_id
    with pytest.raises(TypeError):
        validated.payload["workflow_id"] = "wf-second-mutation"


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
@pytest.mark.parametrize(
    ("state_factory", "payload_updates"),
    [
        (_planning_state, {"status": "running"}),
        (_planning_state, {"active_plan": None}),
        (_approved_planning_state, {"status": "awaiting_user"}),
        (_approved_planning_state, {"active_plan": None}),
    ],
)
async def test_video_state_codec_rejects_illegal_planning_review_authority(
    state_factory: StateFactory,
    payload_updates: dict[str, object],
) -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await state_factory(),
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    payload = envelope.model_dump(mode="json")["payload"]
    payload.update(payload_updates)

    with pytest.raises(ValueError, match="规划状态|Plan"):
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper_case",
    [
        "generation_source_future_version",
        "generation_source_created_at_drift",
        "postproduction_generation_future_version",
        "postproduction_generation_future_time",
        "delivery_postproduction_created_at_drift",
    ],
)
async def test_video_state_codec_rejects_nested_lineage_tampering(
    tamper_case: str,
) -> None:
    if tamper_case.startswith("generation_source"):
        state = await _scene_generation_state()
    elif tamper_case.startswith("postproduction_generation"):
        state = await _postproduction_state()
    else:
        state = await _delivery_state()
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=state,
        workflow_version=1,
        last_turn_id="turn-1",
        last_action_key="decision:turn-1",
    )
    payload = deepcopy(envelope.model_dump(mode="json")["payload"])

    if tamper_case == "generation_source_future_version":
        source = payload["source_scene_package"]
        source["stage_version"] = payload["stage_version"] + 100
        source["context_version"] = payload["context_version"] + 100
    elif tamper_case == "generation_source_created_at_drift":
        source = payload["source_scene_package"]
        source["created_at"] = (datetime.fromisoformat(source["created_at"]) + timedelta(seconds=1)).isoformat()
    elif tamper_case == "postproduction_generation_future_version":
        generation = payload["generation_state"]
        generation["stage_version"] = payload["stage_version"] + 100
        generation["context_version"] = payload["context_version"] + 100
    elif tamper_case == "postproduction_generation_future_time":
        generation = payload["generation_state"]
        generation["updated_at"] = (datetime.fromisoformat(payload["updated_at"]) + timedelta(seconds=1)).isoformat()
    else:
        postproduction = payload["postproduction_state"]
        postproduction["created_at"] = (
            datetime.fromisoformat(postproduction["created_at"]) + timedelta(seconds=1)
        ).isoformat()

    with pytest.raises(ValueError, match="谱系"):
        decode_video_workflow_state(_replace_payload(envelope, payload))


def test_video_state_payload_sha256_is_stable_for_key_order() -> None:
    left = {"outer": {"b": 2, "a": 1}, "items": [3, 4]}
    right = {"items": [3, 4], "outer": {"a": 1, "b": 2}}

    assert canonical_payload_sha256(left) == canonical_payload_sha256(right)
    assert canonical_payload_sha256(left) == f"sha256:{hashlib.sha256(json.dumps(left, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()}"


@pytest.mark.asyncio
async def test_video_state_envelope_sha256_is_stable_for_payload_key_order_and_json_time_round_trip() -> None:
    envelope = encode_video_workflow_state(
        user_id="user-1",
        state=await _planning_state(),
        workflow_version=4,
        last_turn_id="turn-4",
        last_action_key="decision:turn-4",
    )
    reordered_payload = dict(reversed(list(envelope.model_dump(mode="json")["payload"].items())))
    reordered = envelope.model_copy(update={"payload": reordered_payload})
    serialized = VideoWorkflowStateEnvelope.model_validate_json(envelope.model_dump_json())

    assert canonical_video_workflow_envelope_sha256(envelope) == canonical_video_workflow_envelope_sha256(reordered)
    assert canonical_video_workflow_envelope_sha256(envelope) == canonical_video_workflow_envelope_sha256(serialized)
