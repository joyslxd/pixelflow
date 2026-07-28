from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.agent_runtime.contracts import WorkflowKind, WorkflowStatus
from pixelflow.agent_workflows.video import (
    VideoPlanningStage,
    VideoPlanningWorkflowService,
)
from pixelflow.creative.plan_markdown import (
    PlanMarkdownResult,
    build_plan_markdown,
    restore_plan_version,
)
from pixelflow.intake.forms import draft_creative_directions, validate_form

VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 30,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_model_capabilities": {
        "generation_types": ["text_to_video", "image_to_video"],
        "upload_file_types": ["image"],
        "aspect_ratios": ["9:16", "16:9"],
        "sizes": ["1080p"],
        "sound_options": ["on", "off"],
        "durations_sec": [4, 5, 6, 8, 10, 12, 15],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}


@pytest.fixture
def service() -> VideoPlanningWorkflowService:
    return VideoPlanningWorkflowService()


def _directions():
    return draft_creative_directions("video", VIDEO_FORM)


def _plan(direction: dict | None = None) -> PlanMarkdownResult:
    selected = direction or _directions()[0].to_dict()
    return build_plan_markdown("video", VIDEO_FORM, selected)


def _with_scene_image_spec(
    result: PlanMarkdownResult,
    *,
    ratio: str,
    size: str,
) -> PlanMarkdownResult:
    contract = copy.deepcopy(result.creation_contract)
    contract.update(
        {
            "scene_image_ratio": ratio,
            "scene_image_size": size,
            "scene_image_spec_source": "plan_llm",
        }
    )
    history = copy.deepcopy(result.plan_history)
    history[-1]["creation_contract"] = copy.deepcopy(contract)
    return replace(result, creation_contract=contract, plan_history=history)


def _with_contract_updates(
    result: PlanMarkdownResult,
    updates: dict,
) -> PlanMarkdownResult:
    contract = {**copy.deepcopy(result.creation_contract), **copy.deepcopy(updates)}
    history = copy.deepcopy(result.plan_history)
    history[-1]["creation_contract"] = copy.deepcopy(contract)
    return replace(result, creation_contract=contract, plan_history=history)


def _advance_to_plan_generation(service: VideoPlanningWorkflowService):
    started_at = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    state = service.start(
        workflow_id="wf-video-1",
        conversation_id="conv-1",
        intent="video",
        intake_context={"source_prompt": "生成一条智能戒指广告"},
        now=started_at,
    )
    validation = validate_form("video", VIDEO_FORM)
    state = service.confirm_intake(state, validation, now=started_at + timedelta(seconds=1))
    state = service.publish_directions(state, _directions(), now=started_at + timedelta(seconds=2))
    state = service.select_direction(state, "direction_1", now=started_at + timedelta(seconds=3))
    return state


def _advance_to_plan_review(service: VideoPlanningWorkflowService):
    state = _advance_to_plan_generation(service)
    return service.publish_initial_plan(
        state,
        _plan(state.selected_direction),
        now=state.updated_at + timedelta(seconds=1),
    )


def test_video_planning_workflow_projects_each_review_boundary(service: VideoPlanningWorkflowService):
    started_at = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    state = service.start(
        workflow_id="wf-video-1",
        conversation_id="conv-1",
        intent="video",
        intake_context={"source_prompt": "生成一条智能戒指广告"},
        now=started_at,
    )

    assert state.current_stage is VideoPlanningStage.INTAKE
    assert state.status is WorkflowStatus.DRAFT

    validation = validate_form("video", VIDEO_FORM)
    state = service.confirm_intake(state, validation, now=started_at + timedelta(seconds=1))
    assert state.current_stage is VideoPlanningStage.DIRECTION_GENERATION
    assert state.status is WorkflowStatus.RUNNING
    assert state.form_values["video_duration_sec"] == 30

    state = service.publish_directions(state, _directions(), now=started_at + timedelta(seconds=2))
    assert state.current_stage is VideoPlanningStage.DIRECTION_REVIEW
    assert state.status is WorkflowStatus.AWAITING_USER
    assert [item["direction_id"] for item in state.creative_directions] == [
        "direction_1",
        "direction_2",
        "direction_3",
    ]

    state = service.select_direction(state, "direction_1", now=started_at + timedelta(seconds=3))
    assert state.current_stage is VideoPlanningStage.PLAN_GENERATION
    assert state.status is WorkflowStatus.RUNNING
    assert state.selected_direction["direction_id"] == "direction_1"

    result = _plan(state.selected_direction)
    state = service.publish_initial_plan(state, result, now=started_at + timedelta(seconds=4))
    projection = service.to_workflow_record(state)

    assert state.current_stage is VideoPlanningStage.PLAN_REVIEW
    assert state.status is WorkflowStatus.AWAITING_USER
    assert state.active_plan is not None
    assert state.active_plan.plan_version == 1
    assert projection.kind is WorkflowKind.VIDEO
    assert projection.current_stage == "plan_review"
    assert projection.creation_contract_snapshot == result.creation_contract
    assert "plan_markdown" not in projection.creation_contract_snapshot
    assert projection.latest_artifact_refs == [state.active_plan_artifact_ref]
    assert projection.stage_version == 5
    assert projection.context_version == 5


def test_intake_and_direction_contracts_fail_closed(service: VideoPlanningWorkflowService):
    now = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="video"):
        service.start(
            workflow_id="wf-image-1",
            conversation_id="conv-1",
            intent="image",
            now=now,
        )

    state = service.start(
        workflow_id="wf-video-1",
        conversation_id="conv-1",
        intent="video",
        now=now,
    )
    with pytest.raises(ValueError, match="完整"):
        service.confirm_intake(
            state,
            validate_form("video", {"product_info": "智能戒指"}),
            now=now + timedelta(seconds=1),
        )

    forged_confirmation = replace(
        validate_form("video", VIDEO_FORM),
        values={**validate_form("video", VIDEO_FORM).values, "confirmed_by_user": False},
    )
    with pytest.raises(ValueError, match="用户确认"):
        service.confirm_intake(state, forged_confirmation, now=now + timedelta(seconds=1))

    state = service.confirm_intake(
        state,
        validate_form("video", VIDEO_FORM),
        now=now + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="3 个"):
        service.publish_directions(state, _directions()[:2], now=now + timedelta(seconds=2))

    duplicated = [item.to_dict() for item in _directions()]
    duplicated[2]["direction_id"] = "direction_1"
    with pytest.raises(ValueError, match="唯一"):
        service.publish_directions(state, duplicated, now=now + timedelta(seconds=2))

    state = service.publish_directions(state, _directions(), now=now + timedelta(seconds=2))
    with pytest.raises(ValueError, match="不存在"):
        service.select_direction(state, "direction-404", now=now + timedelta(seconds=3))


def test_direction_regeneration_and_form_cancellation_are_explicit(service: VideoPlanningWorkflowService):
    now = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    intake = service.start(
        workflow_id="wf-video-1",
        conversation_id="conv-1",
        intent="video",
        now=now,
    )
    cancelled = service.cancel_intake(intake, now=now + timedelta(seconds=1))

    assert cancelled.current_stage is VideoPlanningStage.FORM_CANCELLED
    assert cancelled.status is WorkflowStatus.CANCELLED
    assert service.to_workflow_record(cancelled).current_stage == "form_cancelled"
    with pytest.raises(ValueError, match="阶段"):
        service.confirm_intake(
            cancelled,
            validate_form("video", VIDEO_FORM),
            now=now + timedelta(seconds=2),
        )

    state = service.confirm_intake(
        intake,
        validate_form("video", VIDEO_FORM),
        now=now + timedelta(seconds=1),
    )
    state = service.publish_directions(state, _directions(), now=now + timedelta(seconds=2))
    regenerated = service.regenerate_directions(state, now=now + timedelta(seconds=3))

    assert regenerated.current_stage is VideoPlanningStage.DIRECTION_GENERATION
    assert regenerated.status is WorkflowStatus.RUNNING
    assert regenerated.creative_directions == []
    assert regenerated.selected_direction == {}


def test_initial_plan_inherits_confirmed_intake_contract_and_model_capabilities(
    service: VideoPlanningWorkflowService,
):
    state = _advance_to_plan_generation(service)
    drifted_form = {**VIDEO_FORM, "video_duration_sec": 60}
    drifted = build_plan_markdown("video", drifted_form, state.selected_direction)

    with pytest.raises(ValueError, match="采集"):
        service.publish_initial_plan(state, drifted)

    unsupported_spec = _with_scene_image_spec(
        _plan(state.selected_direction),
        ratio="3:2",
        size="16K",
    )
    with pytest.raises(ValueError, match="场景图规格"):
        service.publish_initial_plan(state, unsupported_spec)

    supported_spec = _with_scene_image_spec(
        _plan(state.selected_direction),
        ratio="9:16",
        size="4K",
    )
    published = service.publish_initial_plan(state, supported_spec)

    assert published.active_plan is not None
    assert published.active_plan.creation_contract["scene_image_ratio"] == "9:16"
    assert published.active_plan.creation_contract["scene_image_size"] == "4K"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda result: replace(
                result,
                scene_durations_sec=[result.scene_durations_sec[0] + 1, *result.scene_durations_sec[1:]],
            ),
            "时长",
        ),
        (
            lambda result: replace(
                result,
                scene_blueprints=[{**result.scene_blueprints[0], "start_sec": 1}]
                + result.scene_blueprints[1:],
            ),
            "时间线",
        ),
        (
            lambda result: replace(
                result,
                asset_manifest={**result.asset_manifest, "props": []},
            ),
            "资产清单",
        ),
        (
            lambda result: replace(
                result,
                plan_history=[
                    {**result.plan_history[0], "plan_markdown": "被篡改的历史 Plan"},
                ],
            ),
            "历史",
        ),
    ],
)
def test_authoritative_plan_rejects_inconsistent_payloads(
    service: VideoPlanningWorkflowService,
    mutate,
    message: str,
):
    state = _advance_to_plan_generation(service)

    with pytest.raises(ValueError, match=message):
        service.publish_initial_plan(state, mutate(_plan(state.selected_direction)))

    assert state.current_stage is VideoPlanningStage.PLAN_GENERATION
    assert state.active_plan is None


def test_authoritative_snapshot_is_isolated_from_all_mutable_inputs(service: VideoPlanningWorkflowService):
    state = _advance_to_plan_generation(service)
    result = _plan(state.selected_direction)
    expected_markdown = result.plan_markdown
    expected_contract = result.creation_contract.copy()
    state = service.publish_initial_plan(state, result)

    result.plan_history[0]["plan_markdown"] = "调用方事后污染"
    result.creation_contract["video_duration_sec"] = 60
    result.scene_blueprints[0]["title"] = "调用方事后污染"
    state.form_values["video_duration_sec"] = 90
    state.creative_directions[0]["title"] = "调用方事后污染"
    state.active_plan.plan_history[0]["plan_markdown"] = "读取方污染"
    state.active_plan.creation_contract["video_duration_sec"] = 120

    projection = service.to_workflow_record(state)
    projection.creation_contract_snapshot["video_duration_sec"] = 180
    projection.latest_artifact_refs.append("artifact:polluted")

    assert state.form_values["video_duration_sec"] == 30
    assert state.creative_directions[0]["title"] == "痛点开场 + 产品解决"
    assert state.active_plan.plan_markdown == expected_markdown
    assert state.active_plan.plan_history[0]["plan_markdown"] == expected_markdown
    assert state.active_plan.creation_contract == expected_contract
    assert service.to_workflow_record(state).creation_contract_snapshot == expected_contract
    assert service.to_workflow_record(state).latest_artifact_refs == [state.active_plan_artifact_ref]


def test_revision_appends_one_version_without_rewriting_history(service: VideoPlanningWorkflowService):
    state = _advance_to_plan_review(service)
    previous = state.active_plan
    assert previous is not None
    revised = _plan(state.selected_direction).next_version(
        plan_markdown=f"{previous.plan_markdown}\n\n## 用户修订\n强化产品续航证明。",
        plan_history=previous.plan_history,
        current_version=previous.plan_version,
        change_source="user_feedback",
    )

    revised_state = service.publish_revision(state, revised)

    assert revised_state.active_plan is not None
    assert revised_state.active_plan.plan_version == 2
    assert revised_state.active_plan.plan_history[:1] == previous.plan_history
    assert revised_state.active_plan.plan_history[1]["version"] == 2
    assert revised_state.stage_version == state.stage_version + 1
    assert revised_state.context_version == state.context_version + 1
    assert revised_state.active_plan_artifact_ref != state.active_plan_artifact_ref


def test_failed_or_forged_revision_cannot_replace_current_authority(service: VideoPlanningWorkflowService):
    state = _advance_to_plan_review(service)
    current = state.active_plan
    assert current is not None
    candidate = _plan(state.selected_direction).next_version(
        plan_markdown=f"{current.plan_markdown}\n修订版",
        plan_history=current.plan_history,
        current_version=current.plan_version,
    )

    with pytest.raises(ValueError, match="失败"):
        service.publish_revision(state, replace(candidate, error="模型修订失败"))

    forged_history = [
        {**candidate.plan_history[0], "plan_markdown": "偷偷覆盖旧版本"},
        candidate.plan_history[1],
    ]
    with pytest.raises(ValueError, match="历史"):
        service.publish_revision(state, replace(candidate, plan_history=forged_history))

    normalized_forgery = copy.deepcopy(candidate.plan_history)
    normalized_forgery[0]["scene_blueprints"][0]["scene_id"] = "forged-scene-id"
    with pytest.raises(ValueError, match="历史"):
        service.publish_revision(state, replace(candidate, plan_history=normalized_forgery))

    with pytest.raises(ValueError, match="连续"):
        service.publish_revision(
            state,
            replace(
                candidate,
                plan_version=3,
                plan_history=[candidate.plan_history[0], {**candidate.plan_history[1], "version": 3}],
            ),
        )

    assert state.active_plan.checksum == current.checksum


@pytest.mark.parametrize(
    ("contract_updates", "message"),
    [
        (
            {
                "scene_image_ratio": "3:2",
                "scene_image_size": "16K",
                "scene_image_spec_source": "plan_llm",
            },
            "场景图规格",
        ),
        ({"confirmed_by_user": False}, "用户确认"),
        ({"version": 99}, "不可变合同"),
        ({"video_model_mode": "manual"}, "不可变合同"),
        ({"video_model": "seedance-1.0"}, "模型及能力快照"),
        (
            {
                "video_model_capabilities": dict(
                    VIDEO_FORM["video_model_capabilities"],
                    sizes=["720p", "1080p"],
                )
            },
            "模型及能力快照",
        ),
        ({"image_model": "gpt-image-1"}, "模型及能力快照"),
        (
            {
                "image_model_capabilities": dict(
                    VIDEO_FORM["image_model_capabilities"],
                    sizes=["1080p", "2K", "4K", "8K"],
                )
            },
            "模型及能力快照",
        ),
    ],
)
def test_revision_cannot_bypass_confirmed_model_contract(
    service: VideoPlanningWorkflowService,
    contract_updates: dict,
    message: str,
):
    state = _advance_to_plan_review(service)
    current = state.active_plan
    assert current is not None
    candidate = _plan(state.selected_direction).next_version(
        plan_markdown=f"{current.plan_markdown}\n修订版",
        plan_history=current.plan_history,
        current_version=current.plan_version,
    )
    forged = _with_contract_updates(candidate, contract_updates)

    with pytest.raises(ValueError, match=message):
        service.publish_revision(state, forged)

    assert state.active_plan.checksum == current.checksum


def test_restore_switches_authority_without_appending_duplicate_history(service: VideoPlanningWorkflowService):
    state = _advance_to_plan_review(service)
    first = state.active_plan
    assert first is not None
    revised = _plan(state.selected_direction).next_version(
        plan_markdown=f"{first.plan_markdown}\n修订版",
        plan_history=first.plan_history,
        current_version=first.plan_version,
    )
    state = service.publish_revision(state, revised)
    current = state.active_plan
    assert current is not None
    restored = restore_plan_version(
        intent="video",
        current_plan_markdown=current.plan_markdown,
        current_plan_version=current.plan_version,
        plan_history=current.plan_history,
        restore_version=1,
        creation_contract=current.creation_contract,
        scene_durations_sec=current.scene_durations_sec,
        scene_blueprints=current.scene_blueprints,
        asset_manifest=current.asset_manifest,
    )

    restored_state = service.restore_plan(state, restored)

    assert restored_state.active_plan is not None
    assert restored_state.active_plan.plan_version == 1
    assert restored_state.active_plan.restored_from_version == 1
    assert restored_state.active_plan.plan_markdown == first.plan_markdown
    assert restored_state.active_plan.plan_history == current.plan_history
    assert len(restored_state.active_plan.plan_history) == 2
    assert restored_state.active_plan_artifact_ref != state.active_plan_artifact_ref


def test_transition_order_and_time_are_enforced(service: VideoPlanningWorkflowService):
    now = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    state = service.start(
        workflow_id="wf-video-1",
        conversation_id="conv-1",
        intent="video",
        now=now,
    )

    with pytest.raises(ValueError, match="阶段"):
        service.publish_initial_plan(state, _plan(), now=now + timedelta(seconds=1))
    with pytest.raises(ValueError, match="时区"):
        service.confirm_intake(state, validate_form("video", VIDEO_FORM), now=datetime(2026, 7, 28, 2, 0))
    with pytest.raises(ValueError, match="早于"):
        service.confirm_intake(state, validate_form("video", VIDEO_FORM), now=now - timedelta(seconds=1))
