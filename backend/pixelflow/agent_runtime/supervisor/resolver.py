"""在调用 LLM 前解析用户输入里的确定性动作与目标证据。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.agent_runtime.contracts import AgentAction, AgentIntent

_QUESTION_PATTERN = re.compile(r"^\s*(?:为什么|为何|怎么|如何|是什么|请问|能否说明|解释一下)")
_TEXT_MENTION_PATTERN = re.compile(r"@([^\s，。！？、,;；:：]+)")
_ACTION_PATTERNS: tuple[tuple[AgentAction, tuple[re.Pattern[str], ...]], ...] = (
    (
        AgentAction.CANCEL_WORKFLOW,
        (
            re.compile(r"取消"),
            re.compile(r"终止"),
            re.compile(r"结束(?:这个|当前).*(?:任务|流程)"),
        ),
    ),
    (
        AgentAction.SWITCH_WORKFLOW,
        (
            re.compile(r"切换到"),
            re.compile(r"转到"),
        ),
    ),
    (
        AgentAction.RETRY_FAILED,
        (
            re.compile(r"重试"),
            re.compile(r"再试一次"),
        ),
    ),
    (
        AgentAction.START_WORKFLOW,
        (
            re.compile(r"另做一个"),
            re.compile(r"再做一个"),
            re.compile(r"新建(?:一个)?"),
            re.compile(r"另外(?:做|生成)"),
        ),
    ),
    (
        AgentAction.REGENERATE_STAGE,
        (
            re.compile(r"重新生成"),
            re.compile(r"重做"),
            re.compile(r"再生成一次"),
            re.compile(r"再生成一版"),
        ),
    ),
    (
        AgentAction.MODIFY_WORKFLOW,
        (
            re.compile(r"修改"),
            re.compile(r"调整"),
            re.compile(r"改成"),
            re.compile(r"换成"),
            re.compile(r"编辑"),
            re.compile(r"修复"),
        ),
    ),
    (
        AgentAction.CONTINUE_WORKFLOW,
        (
            re.compile(r"继续"),
            re.compile(r"同意"),
            re.compile(r"确认"),
            re.compile(r"下一步"),
        ),
    ),
)
_NEGATION_SUFFIXES = ("不要", "无需", "不需要", "不用", "别")
_INTENT_PATTERNS: tuple[tuple[AgentIntent, re.Pattern[str]], ...] = (
    (
        AgentIntent.VIDEO_ANALYSIS,
        re.compile(r"视频分析|分析.{0,8}视频|拆解.{0,8}视频"),
    ),
    (
        AgentIntent.PPT,
        re.compile(r"(?i:ppt)|演示文稿|幻灯片"),
    ),
    (
        AgentIntent.IMAGE,
        re.compile(r"图片|图像|主图|海报"),
    ),
    (
        AgentIntent.VIDEO,
        re.compile(r"视频|短片|影片"),
    ),
)


class _ResolverModel(BaseModel):
    """为解析器内部 DTO 提供严格、不可变的字段合同。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class DeterministicResolutionStatus(StrEnum):
    """描述规则解析是否足以直接形成后续分类证据。"""

    RESOLVED = "resolved"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class ResolverCandidate(_ResolverModel):
    """把消息、产物或 mention 证据映射到一个 Workflow 目标。"""

    workflow_id: str = Field(min_length=1)
    intent: AgentIntent
    stage: str | None = Field(default=None, min_length=1)
    message_id: str | None = Field(default=None, min_length=1)
    artifact_ref: str | None = Field(default=None, min_length=1)
    mention_ref: str | None = Field(default=None, min_length=1)


class ExplicitActionSignal(_ResolverModel):
    """保存按钮或人工决策控件提交的结构化动作。"""

    action: AgentAction
    workflow_id: str | None = Field(default=None, min_length=1)
    intent: AgentIntent | None = None
    stage: str | None = Field(default=None, min_length=1)
    artifact_ref: str | None = Field(default=None, min_length=1)


class DeterministicResolutionRequest(_ResolverModel):
    """汇总单个 Turn 可用于纯规则解析的公开证据。"""

    content: str = Field(min_length=1)
    candidates: tuple[ResolverCandidate, ...] = ()
    explicit_action: ExplicitActionSignal | None = None
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: tuple[str, ...] = ()
    mention_refs: tuple[str, ...] = ()
    active_workflow_id: str | None = Field(default=None, min_length=1)


class DeterministicResolution(_ResolverModel):
    """返回给结构化分类器和 Validator 的确定性证据摘要。"""

    status: DeterministicResolutionStatus
    action: AgentAction | None = None
    intent: AgentIntent = AgentIntent.GENERAL
    target_workflow_id: str | None = Field(default=None, min_length=1)
    target_stage: str | None = Field(default=None, min_length=1)
    target_artifact_ref: str | None = Field(default=None, min_length=1)
    reason_code: str = Field(min_length=1)
    candidate_workflow_ids: tuple[str, ...] = ()


class DeterministicTargetResolver:
    """按显式证据优先级解析动作与目标，不执行状态变更。"""

    def resolve(
        self,
        request: DeterministicResolutionRequest,
    ) -> DeterministicResolution:
        """返回纯规则证据；未覆盖的自然语言交给后续 LLM 分类器。"""

        if request.explicit_action is not None:
            return self._resolve_explicit_action(request)
        request = _with_text_mentions(request)
        action_inference = _infer_action(request.content)
        if action_inference.conflicting:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                action=action_inference.action,
                intent=action_inference.intent,
                reason_code=action_inference.reason_code,
            )

        if _has_explicit_target_signal(request):
            target_resolution = self._resolve_explicit_targets(request)
            return _combine_action_and_target(
                target_resolution,
                action_inference,
            )
        return self._resolve_workflow_fallback(request, action_inference)

    def _resolve_explicit_action(
        self,
        request: DeterministicResolutionRequest,
    ) -> DeterministicResolution:
        signal = request.explicit_action
        assert signal is not None
        has_embedded_target = any(
            (
                signal.workflow_id is not None,
                signal.stage is not None,
                signal.artifact_ref is not None,
            )
        )
        if not has_embedded_target:
            request = _with_text_mentions(request)
            action_inference = _ActionInference(
                action=signal.action,
                intent=signal.intent or AgentIntent.GENERAL,
                reason_code="explicit_action",
            )
            if _has_explicit_target_signal(request):
                return _combine_explicit_action_and_target(
                    self._resolve_explicit_targets(request),
                    signal,
                )
            return self._resolve_workflow_fallback(request, action_inference)

        matches = tuple(candidate for candidate in request.candidates if _matches_explicit_signal(candidate, signal))
        if not matches:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.UNRESOLVED,
                action=signal.action,
                intent=signal.intent or AgentIntent.GENERAL,
                reason_code="explicit_action_target_missing",
            )
        workflow_ids = tuple(dict.fromkeys(candidate.workflow_id for candidate in matches))
        if len(workflow_ids) != 1:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                action=signal.action,
                intent=signal.intent or AgentIntent.GENERAL,
                reason_code="conflicting_explicit_targets",
                candidate_workflow_ids=workflow_ids,
            )
        candidate_intent = _single_candidate_intent(matches)
        if candidate_intent is None:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                action=signal.action,
                intent=signal.intent or AgentIntent.GENERAL,
                reason_code="conflicting_workflow_intents",
                candidate_workflow_ids=workflow_ids,
            )
        if signal.intent is not None and signal.intent != candidate_intent:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                action=signal.action,
                reason_code="conflicting_workflow_intents",
                candidate_workflow_ids=workflow_ids,
            )

        request = _with_text_mentions(request)
        if _has_explicit_target_signal(request):
            external_target = self._resolve_explicit_targets(request)
            if external_target.status != DeterministicResolutionStatus.PARTIAL:
                return external_target.model_copy(
                    update={
                        "action": signal.action,
                        "intent": signal.intent or external_target.intent,
                    }
                )
            target_conflicts = any(
                (
                    external_target.target_workflow_id != workflow_ids[0],
                    signal.stage is not None and external_target.target_stage != signal.stage,
                    signal.artifact_ref is not None and external_target.target_artifact_ref != signal.artifact_ref,
                    external_target.intent != candidate_intent,
                )
            )
            if target_conflicts:
                candidate_workflow_ids = tuple(dict.fromkeys((*workflow_ids, *external_target.candidate_workflow_ids)))
                return DeterministicResolution(
                    status=DeterministicResolutionStatus.AMBIGUOUS,
                    action=signal.action,
                    reason_code="conflicting_explicit_targets",
                    candidate_workflow_ids=candidate_workflow_ids,
                )
            return _combine_explicit_action_and_target(external_target, signal)

        return DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=signal.action,
            intent=signal.intent or candidate_intent,
            target_workflow_id=workflow_ids[0],
            target_stage=signal.stage,
            target_artifact_ref=signal.artifact_ref,
            reason_code="explicit_action_target",
            candidate_workflow_ids=workflow_ids,
        )

    def _resolve_workflow_fallback(
        self,
        request: DeterministicResolutionRequest,
        action_inference: _ActionInference,
    ) -> DeterministicResolution:
        action = action_inference.action
        intent = action_inference.intent
        if action is None:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.UNRESOLVED,
                intent=intent,
                reason_code="no_deterministic_signal",
            )

        if action == AgentAction.START_WORKFLOW:
            status = DeterministicResolutionStatus.RESOLVED if intent != AgentIntent.GENERAL else DeterministicResolutionStatus.PARTIAL
            return DeterministicResolution(
                status=status,
                action=action,
                intent=intent,
                reason_code=action_inference.reason_code,
            )

        candidates = _candidates_for_intent(request.candidates, intent)
        workflow_ids = tuple(dict.fromkeys(candidate.workflow_id for candidate in candidates))
        target_workflow_id: str | None = None
        target_reason: str | None = None
        if request.active_workflow_id is not None and action != AgentAction.SWITCH_WORKFLOW:
            if request.active_workflow_id in workflow_ids:
                target_workflow_id = request.active_workflow_id
                target_reason = "active_target"
            else:
                return DeterministicResolution(
                    status=DeterministicResolutionStatus.UNRESOLVED,
                    action=action,
                    intent=intent,
                    reason_code="active_workflow_missing",
                    candidate_workflow_ids=workflow_ids,
                )
        elif len(workflow_ids) == 1:
            target_workflow_id = workflow_ids[0]
            target_reason = "unique_target"
        elif len(workflow_ids) > 1:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                action=action,
                intent=intent,
                reason_code="ambiguous_workflow_target",
                candidate_workflow_ids=workflow_ids,
            )

        if target_workflow_id is None:
            if action == AgentAction.ANSWER_ONLY:
                return DeterministicResolution(
                    status=DeterministicResolutionStatus.RESOLVED,
                    action=action,
                    intent=intent,
                    reason_code=action_inference.reason_code,
                )
            return DeterministicResolution(
                status=DeterministicResolutionStatus.PARTIAL,
                action=action,
                intent=intent,
                reason_code=f"{action_inference.reason_code}_target_missing",
            )

        target_candidates = tuple(candidate for candidate in candidates if candidate.workflow_id == target_workflow_id)
        target_intent = _single_candidate_intent(target_candidates)
        if target_intent is None:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                action=action,
                reason_code="conflicting_workflow_intents",
                candidate_workflow_ids=(target_workflow_id,),
            )
        return DeterministicResolution(
            status=DeterministicResolutionStatus.RESOLVED,
            action=action,
            intent=target_intent,
            target_workflow_id=target_workflow_id,
            reason_code=f"{action_inference.reason_code}_{target_reason}",
            candidate_workflow_ids=(target_workflow_id,),
        )

    def _resolve_explicit_targets(
        self,
        request: DeterministicResolutionRequest,
    ) -> DeterministicResolution:
        matches, missing = _collect_explicit_matches(request)
        if missing or not matches:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.UNRESOLVED,
                reason_code="explicit_target_missing",
            )

        target_keys = tuple(
            dict.fromkeys(
                (
                    match.candidate.workflow_id,
                    match.candidate.stage,
                    match.target_artifact_ref,
                )
                for match in matches
            )
        )
        workflow_ids = tuple(dict.fromkeys(match.candidate.workflow_id for match in matches))
        if len(target_keys) != 1:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                reason_code="conflicting_explicit_targets",
                candidate_workflow_ids=workflow_ids,
            )
        candidate_intent = _single_candidate_intent(tuple(match.candidate for match in matches))
        if candidate_intent is None:
            return DeterministicResolution(
                status=DeterministicResolutionStatus.AMBIGUOUS,
                reason_code="conflicting_workflow_intents",
                candidate_workflow_ids=workflow_ids,
            )

        candidate = matches[0].candidate
        sources = tuple(dict.fromkeys(match.source for match in matches))
        return DeterministicResolution(
            status=DeterministicResolutionStatus.PARTIAL,
            intent=candidate_intent,
            target_workflow_id=candidate.workflow_id,
            target_stage=candidate.stage,
            target_artifact_ref=target_keys[0][2],
            reason_code=f"{'_'.join(sources)}_target",
            candidate_workflow_ids=workflow_ids,
        )


@dataclass(frozen=True, slots=True)
class _CandidateMatch:
    """保存一次显式证据命中的目标和证据类型。"""

    candidate: ResolverCandidate
    source: str
    target_artifact_ref: str | None


@dataclass(frozen=True, slots=True)
class _ActionInference:
    """保存动词和业务名词形成的最小规则结论。"""

    action: AgentAction | None
    intent: AgentIntent
    reason_code: str
    conflicting: bool = False


def _with_text_mentions(
    request: DeterministicResolutionRequest,
) -> DeterministicResolutionRequest:
    mention_refs = list(request.mention_refs)
    known_refs = tuple(
        sorted(
            {candidate.mention_ref for candidate in request.candidates if candidate.mention_ref is not None},
            key=len,
            reverse=True,
        )
    )
    mention_refs.extend(_normalize_text_mention(match.group(1), known_refs) for match in _TEXT_MENTION_PATTERN.finditer(request.content))
    return request.model_copy(update={"mention_refs": tuple(dict.fromkeys(mention_refs))})


def _normalize_text_mention(token: str, known_refs: tuple[str, ...]) -> str:
    for mention_ref in known_refs:
        if token == mention_ref:
            return mention_ref
        if token.startswith(mention_ref):
            suffix = token[len(mention_ref) :]
            if suffix.startswith("的"):
                return mention_ref
    return token


def _infer_action(content: str) -> _ActionInference:
    intent, intent_conflicting = _infer_intent(content)
    if _QUESTION_PATTERN.search(content):
        return _ActionInference(
            action=AgentAction.ANSWER_ONLY,
            intent=intent,
            reason_code="verb_answer_only",
        )

    actions: list[AgentAction] = []
    for action, patterns in _ACTION_PATTERNS:
        if any(_has_non_negated_match(content, pattern) for pattern in patterns):
            actions.append(action)
    actions = list(dict.fromkeys(actions))
    if set(actions) == {AgentAction.CONTINUE_WORKFLOW, AgentAction.MODIFY_WORKFLOW} and re.search(r"继续(?:修改|调整|编辑)", content):
        actions = [AgentAction.MODIFY_WORKFLOW]
    if len(actions) > 1:
        return _ActionInference(
            action=None,
            intent=intent,
            reason_code="conflicting_action_verbs",
            conflicting=True,
        )
    if actions and intent_conflicting:
        return _ActionInference(
            action=actions[0],
            intent=AgentIntent.GENERAL,
            reason_code="conflicting_intents",
            conflicting=True,
        )
    if not actions:
        return _ActionInference(
            action=None,
            intent=intent,
            reason_code="no_action_verb",
        )
    action = actions[0]
    return _ActionInference(
        action=action,
        intent=intent,
        reason_code=f"verb_{action.value}",
    )


def _has_non_negated_match(
    content: str,
    pattern: re.Pattern[str],
) -> bool:
    for match in pattern.finditer(content):
        prefix = content[max(0, match.start() - 5) : match.start()].rstrip()
        if not any(prefix.endswith(negation) for negation in _NEGATION_SUFFIXES):
            return True
    return False


def _infer_intent(content: str) -> tuple[AgentIntent, bool]:
    intents = tuple(intent for intent, pattern in _INTENT_PATTERNS if pattern.search(content))
    if AgentIntent.VIDEO_ANALYSIS in intents and AgentIntent.VIDEO in intents:
        intents = tuple(intent for intent in intents if intent != AgentIntent.VIDEO)
    if len(intents) == 1:
        return intents[0], False
    return AgentIntent.GENERAL, len(intents) > 1


def _candidates_for_intent(
    candidates: tuple[ResolverCandidate, ...],
    intent: AgentIntent,
) -> tuple[ResolverCandidate, ...]:
    if intent == AgentIntent.GENERAL:
        return candidates
    return tuple(candidate for candidate in candidates if candidate.intent == intent)


def _single_candidate_intent(
    candidates: tuple[ResolverCandidate, ...],
) -> AgentIntent | None:
    intents = tuple(dict.fromkeys(candidate.intent for candidate in candidates))
    if len(intents) != 1:
        return None
    return intents[0]


def _combine_action_and_target(
    target: DeterministicResolution,
    action: _ActionInference,
) -> DeterministicResolution:
    if action.action is None:
        return target
    if target.intent != AgentIntent.GENERAL and action.intent != AgentIntent.GENERAL and target.intent != action.intent:
        return DeterministicResolution(
            status=DeterministicResolutionStatus.AMBIGUOUS,
            action=action.action,
            reason_code="conflicting_workflow_intents",
            candidate_workflow_ids=target.candidate_workflow_ids,
        )
    intent = target.intent if target.intent != AgentIntent.GENERAL else action.intent
    status = target.status
    if status == DeterministicResolutionStatus.PARTIAL:
        status = DeterministicResolutionStatus.RESOLVED
    return target.model_copy(
        update={
            "status": status,
            "action": action.action,
            "intent": intent,
            "reason_code": (f"{action.reason_code}_{target.reason_code}" if status == DeterministicResolutionStatus.RESOLVED else target.reason_code),
        }
    )


def _combine_explicit_action_and_target(
    target: DeterministicResolution,
    signal: ExplicitActionSignal,
) -> DeterministicResolution:
    if signal.intent is not None and target.intent != AgentIntent.GENERAL and signal.intent != target.intent:
        return DeterministicResolution(
            status=DeterministicResolutionStatus.AMBIGUOUS,
            action=signal.action,
            reason_code="conflicting_workflow_intents",
            candidate_workflow_ids=target.candidate_workflow_ids,
        )
    if target.status != DeterministicResolutionStatus.PARTIAL:
        return target.model_copy(
            update={
                "action": signal.action,
                "intent": signal.intent or target.intent,
            }
        )
    return target.model_copy(
        update={
            "status": DeterministicResolutionStatus.RESOLVED,
            "action": signal.action,
            "intent": signal.intent or target.intent,
            "reason_code": f"explicit_action_{target.reason_code}",
        }
    )


def _has_explicit_target_signal(
    request: DeterministicResolutionRequest,
) -> bool:
    return any(
        (
            request.reply_to_message_id is not None,
            bool(request.artifact_refs),
            bool(request.mention_refs),
        )
    )


def _collect_explicit_matches(
    request: DeterministicResolutionRequest,
) -> tuple[tuple[_CandidateMatch, ...], bool]:
    matches: list[_CandidateMatch] = []
    missing = False

    if request.reply_to_message_id is not None:
        reply_matches = [
            _CandidateMatch(
                candidate=candidate,
                source="reply",
                target_artifact_ref=candidate.artifact_ref,
            )
            for candidate in request.candidates
            if candidate.message_id == request.reply_to_message_id
        ]
        missing = missing or not reply_matches
        matches.extend(reply_matches)

    for artifact_ref in dict.fromkeys(request.artifact_refs):
        artifact_matches = [
            _CandidateMatch(
                candidate=candidate,
                source="artifact",
                target_artifact_ref=artifact_ref,
            )
            for candidate in request.candidates
            if candidate.artifact_ref == artifact_ref
        ]
        missing = missing or not artifact_matches
        matches.extend(artifact_matches)

    for mention_ref in dict.fromkeys(request.mention_refs):
        mention_matches = [
            _CandidateMatch(
                candidate=candidate,
                source="mention",
                target_artifact_ref=candidate.artifact_ref,
            )
            for candidate in request.candidates
            if candidate.mention_ref == mention_ref
        ]
        missing = missing or not mention_matches
        matches.extend(mention_matches)

    return tuple(matches), missing


def _matches_explicit_signal(
    candidate: ResolverCandidate,
    signal: ExplicitActionSignal,
) -> bool:
    if signal.workflow_id is not None and candidate.workflow_id != signal.workflow_id:
        return False
    if signal.stage is not None and candidate.stage != signal.stage:
        return False
    if signal.artifact_ref is not None and candidate.artifact_ref != signal.artifact_ref:
        return False
    return True
