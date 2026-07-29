import pytest

from pixelflow.agent_runtime.contracts import AgentAction, AgentIntent


def test_explicit_action_resolves_its_exact_target_without_text_guessing() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ExplicitActionSignal,
        ResolverCandidate,
    )

    image = ResolverCandidate(
        workflow_id="wf-image",
        intent=AgentIntent.IMAGE,
        stage="image_review",
        message_id="msg-image",
        artifact_ref="artifact:image:3",
        mention_ref="scene-3",
    )
    request = DeterministicResolutionRequest(
        content="为什么选择这个模型？",
        candidates=(image,),
        explicit_action=ExplicitActionSignal(
            action=AgentAction.MODIFY_WORKFLOW,
            workflow_id=image.workflow_id,
            stage=image.stage,
            artifact_ref=image.artifact_ref,
        ),
    )

    resolution = DeterministicTargetResolver().resolve(request)

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.MODIFY_WORKFLOW
    assert resolution.intent == AgentIntent.IMAGE
    assert resolution.target_workflow_id == "wf-image"
    assert resolution.target_stage == "image_review"
    assert resolution.target_artifact_ref == "artifact:image:3"
    assert resolution.reason_code == "explicit_action_target"
    assert resolution.candidate_workflow_ids == ("wf-image",)


def test_reply_resolves_message_target_as_partial_evidence() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    request = DeterministicResolutionRequest(
        content="按这个处理",
        reply_to_message_id="msg-plan-v2",
        candidates=(
            ResolverCandidate(
                workflow_id="wf-video",
                intent=AgentIntent.VIDEO,
                stage="plan_review",
                message_id="msg-plan-v2",
                artifact_ref="artifact:plan:v2",
            ),
        ),
    )

    resolution = DeterministicTargetResolver().resolve(request)

    assert resolution.status == DeterministicResolutionStatus.PARTIAL
    assert resolution.action is None
    assert resolution.intent == AgentIntent.VIDEO
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.target_stage == "plan_review"
    assert resolution.target_artifact_ref == "artifact:plan:v2"
    assert resolution.reason_code == "reply_target"


def test_artifact_and_mention_resolve_the_same_exact_target() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    request = DeterministicResolutionRequest(
        content="请处理 @scene-3",
        artifact_refs=("artifact:scene:3",),
        mention_refs=("scene-3",),
        candidates=(
            ResolverCandidate(
                workflow_id="wf-video",
                intent=AgentIntent.VIDEO,
                stage="scene_review",
                artifact_ref="artifact:scene:3",
                mention_ref="scene-3",
            ),
        ),
    )

    resolution = DeterministicTargetResolver().resolve(request)

    assert resolution.status == DeterministicResolutionStatus.PARTIAL
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.target_stage == "scene_review"
    assert resolution.target_artifact_ref == "artifact:scene:3"
    assert resolution.reason_code == "artifact_mention_target"
    assert resolution.candidate_workflow_ids == ("wf-video",)


def test_conflicting_explicit_references_are_ambiguous_without_precedence_guess() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    request = DeterministicResolutionRequest(
        content="继续",
        reply_to_message_id="msg-image",
        artifact_refs=("artifact:video:1",),
        candidates=(
            ResolverCandidate(
                workflow_id="wf-image",
                intent=AgentIntent.IMAGE,
                message_id="msg-image",
            ),
            ResolverCandidate(
                workflow_id="wf-video",
                intent=AgentIntent.VIDEO,
                artifact_ref="artifact:video:1",
            ),
        ),
        active_workflow_id="wf-image",
    )

    resolution = DeterministicTargetResolver().resolve(request)

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.target_workflow_id is None
    assert resolution.reason_code == "conflicting_explicit_targets"
    assert resolution.candidate_workflow_ids == ("wf-image", "wf-video")


def test_unknown_explicit_reference_never_falls_back_to_active_workflow() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    request = DeterministicResolutionRequest(
        content="继续",
        artifact_refs=("artifact:missing",),
        active_workflow_id="wf-image",
        candidates=(
            ResolverCandidate(
                workflow_id="wf-image",
                intent=AgentIntent.IMAGE,
            ),
        ),
    )

    resolution = DeterministicTargetResolver().resolve(request)

    assert resolution.status == DeterministicResolutionStatus.UNRESOLVED
    assert resolution.target_workflow_id is None
    assert resolution.reason_code == "explicit_target_missing"


@pytest.mark.parametrize(
    ("content", "expected_action"),
    [
        ("继续当前任务", AgentAction.CONTINUE_WORKFLOW),
        ("把背景换成白色", AgentAction.MODIFY_WORKFLOW),
        ("重新生成这一版", AgentAction.REGENERATE_STAGE),
        ("重试失败的分镜", AgentAction.RETRY_FAILED),
        ("切换到图片任务", AgentAction.SWITCH_WORKFLOW),
        ("取消当前任务", AgentAction.CANCEL_WORKFLOW),
    ],
)
def test_explicit_chinese_verbs_resolve_existing_workflow_actions(
    content: str,
    expected_action: AgentAction,
) -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content=content,
            active_workflow_id="wf-image",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image",
                    intent=AgentIntent.IMAGE,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == expected_action
    assert resolution.target_workflow_id == "wf-image"
    assert resolution.intent == AgentIntent.IMAGE


def test_text_mention_and_negative_verb_select_modify_instead_of_regenerate() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="不要重新生成，只修改 @scene-3 的旁白",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                    stage="scene_review",
                    artifact_ref="artifact:scene:3",
                    mention_ref="scene-3",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.MODIFY_WORKFLOW
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.target_stage == "scene_review"
    assert resolution.target_artifact_ref == "artifact:scene:3"


def test_question_is_answer_only_even_when_it_contains_continue_verb() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="为什么现在不能继续生成？",
            active_workflow_id="wf-video",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.ANSWER_ONLY
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.reason_code == "verb_answer_only_active_target"


def test_start_verb_and_business_noun_resolve_new_video_workflow_intent() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="按这个风格再做一个30秒视频",
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.START_WORKFLOW
    assert resolution.intent == AgentIntent.VIDEO
    assert resolution.target_workflow_id is None
    assert resolution.reason_code == "verb_start_workflow"


def test_mutating_verb_without_unique_or_active_target_stays_ambiguous() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="再生成一次",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image-a",
                    intent=AgentIntent.IMAGE,
                ),
                ResolverCandidate(
                    workflow_id="wf-image-b",
                    intent=AgentIntent.IMAGE,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action == AgentAction.REGENERATE_STAGE
    assert resolution.target_workflow_id is None
    assert resolution.reason_code == "ambiguous_workflow_target"
    assert resolution.candidate_workflow_ids == ("wf-image-a", "wf-image-b")


def test_conflicting_mutating_verbs_are_not_guessed() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="取消后再重试这个任务",
            active_workflow_id="wf-video",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action is None
    assert resolution.reason_code == "conflicting_action_verbs"


def test_workflow_only_button_does_not_pick_an_arbitrary_historical_artifact() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ExplicitActionSignal,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="继续",
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                workflow_id="wf-video",
            ),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                    stage="plan_review",
                    artifact_ref="artifact:plan:v1",
                ),
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                    stage="video_review",
                    artifact_ref="artifact:video:v2",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.target_stage is None
    assert resolution.target_artifact_ref is None
    assert resolution.candidate_workflow_ids == ("wf-video",)


def test_button_without_embedded_target_uses_exact_artifact_reference() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ExplicitActionSignal,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="确认修改",
            explicit_action=ExplicitActionSignal(
                action=AgentAction.MODIFY_WORKFLOW,
            ),
            artifact_refs=("artifact:image:2",),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image",
                    intent=AgentIntent.IMAGE,
                    stage="image_review",
                    artifact_ref="artifact:image:2",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.MODIFY_WORKFLOW
    assert resolution.target_workflow_id == "wf-image"
    assert resolution.target_artifact_ref == "artifact:image:2"
    assert resolution.reason_code == "explicit_action_artifact_target"


def test_unknown_text_mention_never_falls_back_to_active_workflow() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="重新生成 @scene-missing",
            active_workflow_id="wf-video",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.UNRESOLVED
    assert resolution.action == AgentAction.REGENERATE_STAGE
    assert resolution.target_workflow_id is None
    assert resolution.reason_code == "explicit_target_missing"


def test_conflicting_candidate_intents_for_same_workflow_are_ambiguous() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ExplicitActionSignal,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="继续",
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                workflow_id="wf-conflict",
            ),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-conflict",
                    intent=AgentIntent.IMAGE,
                ),
                ResolverCandidate(
                    workflow_id="wf-conflict",
                    intent=AgentIntent.VIDEO,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action == AgentAction.CONTINUE_WORKFLOW
    assert resolution.reason_code == "conflicting_workflow_intents"


def test_multiple_business_intents_are_ambiguous_instead_of_using_pattern_order() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="参考这张图片再做一个视频",
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action == AgentAction.START_WORKFLOW
    assert resolution.intent == AgentIntent.GENERAL
    assert resolution.reason_code == "conflicting_intents"


def test_button_target_must_agree_with_other_explicit_references() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ExplicitActionSignal,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="确认修改",
            explicit_action=ExplicitActionSignal(
                action=AgentAction.MODIFY_WORKFLOW,
                workflow_id="wf-image",
            ),
            artifact_refs=("artifact:video:1",),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image",
                    intent=AgentIntent.IMAGE,
                ),
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                    artifact_ref="artifact:video:1",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action == AgentAction.MODIFY_WORKFLOW
    assert resolution.reason_code == "conflicting_explicit_targets"
    assert resolution.candidate_workflow_ids == ("wf-image", "wf-video")


def test_button_intent_must_agree_with_its_target_candidate() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ExplicitActionSignal,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="继续",
            explicit_action=ExplicitActionSignal(
                action=AgentAction.CONTINUE_WORKFLOW,
                workflow_id="wf-image",
                intent=AgentIntent.VIDEO,
            ),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image",
                    intent=AgentIntent.IMAGE,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action == AgentAction.CONTINUE_WORKFLOW
    assert resolution.reason_code == "conflicting_workflow_intents"
    assert resolution.candidate_workflow_ids == ("wf-image",)


def test_explicit_reference_with_conflicting_candidate_intents_is_ambiguous() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="按这个处理",
            artifact_refs=("artifact:shared",),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-shared",
                    intent=AgentIntent.IMAGE,
                    stage="review",
                    artifact_ref="artifact:shared",
                ),
                ResolverCandidate(
                    workflow_id="wf-shared",
                    intent=AgentIntent.VIDEO,
                    stage="review",
                    artifact_ref="artifact:shared",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.intent == AgentIntent.GENERAL
    assert resolution.reason_code == "conflicting_workflow_intents"
    assert resolution.candidate_workflow_ids == ("wf-shared",)


def test_switch_verb_selects_unique_destination_instead_of_active_workflow() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="切换到视频任务",
            active_workflow_id="wf-image",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image",
                    intent=AgentIntent.IMAGE,
                ),
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.SWITCH_WORKFLOW
    assert resolution.intent == AgentIntent.VIDEO
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.reason_code == "verb_switch_workflow_unique_target"


def test_text_mention_resolves_before_adjacent_chinese_suffix() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="修改@scene-3的旁白",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                    stage="scene_review",
                    mention_ref="scene-3",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.MODIFY_WORKFLOW
    assert resolution.target_workflow_id == "wf-video"
    assert resolution.target_stage == "scene_review"
    assert resolution.reason_code == "verb_modify_workflow_mention_target"


def test_video_analysis_is_a_specialized_video_intent_instead_of_a_conflict() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="新建一个视频分析任务",
        )
    )

    assert resolution.status == DeterministicResolutionStatus.RESOLVED
    assert resolution.action == AgentAction.START_WORKFLOW
    assert resolution.intent == AgentIntent.VIDEO_ANALYSIS
    assert resolution.reason_code == "verb_start_workflow"


def test_text_intent_must_agree_with_explicit_artifact_target() -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content="重新生成视频",
            artifact_refs=("artifact:image:1",),
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-image",
                    intent=AgentIntent.IMAGE,
                    artifact_ref="artifact:image:1",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.AMBIGUOUS
    assert resolution.action == AgentAction.REGENERATE_STAGE
    assert resolution.intent == AgentIntent.GENERAL
    assert resolution.reason_code == "conflicting_workflow_intents"
    assert resolution.candidate_workflow_ids == ("wf-image",)


@pytest.mark.parametrize("mention_token", ("scene-3.1", "scene-3/voice"))
def test_unknown_mention_suffix_is_not_guessed_as_a_known_prefix(
    mention_token: str,
) -> None:
    from pixelflow.agent_runtime.supervisor import (
        DeterministicResolutionRequest,
        DeterministicResolutionStatus,
        DeterministicTargetResolver,
        ResolverCandidate,
    )

    resolution = DeterministicTargetResolver().resolve(
        DeterministicResolutionRequest(
            content=f"修改@{mention_token}",
            active_workflow_id="wf-video",
            candidates=(
                ResolverCandidate(
                    workflow_id="wf-video",
                    intent=AgentIntent.VIDEO,
                    mention_ref="scene-3",
                ),
            ),
        )
    )

    assert resolution.status == DeterministicResolutionStatus.UNRESOLVED
    assert resolution.action == AgentAction.MODIFY_WORKFLOW
    assert resolution.target_workflow_id is None
    assert resolution.reason_code == "explicit_target_missing"
