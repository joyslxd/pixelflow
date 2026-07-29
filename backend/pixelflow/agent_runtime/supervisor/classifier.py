"""把自然语言与确定性证据分类为冻结的 ActionDecision。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pixelflow.agent_runtime.contracts import (
    ActionDecision,
    AgentAction,
    AgentIntent,
    WorkflowStatus,
)

from .resolver import DeterministicResolution, DeterministicResolutionStatus

_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_CLASSIFICATION_ATTEMPTS = 2
_SYSTEM_PROMPT = """你是 PixelFlow 会话 Supervisor 的结构化动作分类器。
你只能返回符合 ActionDecision schema 的完整 JSON 对象；不得输出 Markdown 或解释，不得输出思维链。

规则：
1. 确定性证据优先；不得改写其中已经解析出的 action、intent、workflow、stage 或 artifact。
2. target_workflow_id 只能来自候选 Workflow；目标不唯一时使用 clarify，不得猜测。
3. answer_only 和 clarify 不得携带 patch；clarify 必须给出可直接展示给用户的问题。
4. reason_code 只能使用不超过 64 字符的小写英文、数字和下划线规则码，不得写推理过程。
5. idempotency_key 必须与输入给出的固定值完全一致。
6. 本分类器不决定状态转换、版本或计费是否合法；后续 Validator 会独立校验。
"""


class _ClassifierModel(BaseModel):
    """为分类器边界提供严格、不可变的输入 DTO。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ActionClassificationTarget(_ClassifierModel):
    """保存一个可定位目标的 stage/artifact 组合证据。"""

    target_stage: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    target_artifact_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )

    @model_validator(mode="after")
    def require_stage_or_artifact(self) -> Self:
        """拒绝没有任何定位信息的空目标证据。"""

        if self.target_stage is None and self.target_artifact_ref is None:
            raise ValueError("目标证据必须包含 stage 或 artifact")
        return self


class ActionClassificationCandidate(_ClassifierModel):
    """向分类模型公开一个候选 Workflow 的最小状态摘要。"""

    workflow_id: str = Field(min_length=1, max_length=255)
    intent: AgentIntent
    status: WorkflowStatus
    current_stage: str = Field(min_length=1, max_length=255)
    stage_version: int = Field(ge=1)
    context_version: int = Field(ge=1)
    allowed_actions: tuple[AgentAction, ...] = Field(max_length=16)
    targets: tuple[ActionClassificationTarget, ...] = Field(
        default=(),
        max_length=64,
    )

    @model_validator(mode="after")
    def require_unique_actions_and_targets(self) -> Self:
        """拒绝重复动作和目标对，避免 Prompt 出现重复证据。"""

        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions 不能重复")
        target_pairs = tuple((target.target_stage, target.target_artifact_ref) for target in self.targets)
        if len(set(target_pairs)) != len(target_pairs):
            raise ValueError("targets 不能重复")
        return self


class ActionClassificationRequest(_ClassifierModel):
    """汇总单个 Turn 的文本、规则证据和候选 Workflow。"""

    turn_id: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=32768)
    deterministic_resolution: DeterministicResolution
    candidates: tuple[ActionClassificationCandidate, ...] = Field(
        default=(),
        max_length=32,
    )
    context_summary: str = Field(default="", max_length=32768)

    @model_validator(mode="after")
    def require_known_deterministic_targets(self) -> Self:
        """确保规则解析引用的 Workflow 确实存在于分类候选中。"""

        candidate_ids = tuple(candidate.workflow_id for candidate in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("候选 workflow_id 不能重复")
        known_ids = set(candidate_ids)
        resolution = self.deterministic_resolution
        if resolution.target_workflow_id is not None and resolution.target_workflow_id not in known_ids:
            raise ValueError("确定性 target_workflow_id 不在候选 Workflow 中")
        if not set(resolution.candidate_workflow_ids).issubset(known_ids):
            raise ValueError("确定性 candidate_workflow_ids 不在候选 Workflow 中")
        if resolution.target_workflow_id is None:
            if resolution.target_stage is not None or resolution.target_artifact_ref is not None:
                raise ValueError("确定性 stage/artifact 必须绑定 target_workflow_id")
            return self
        candidate = next(candidate for candidate in self.candidates if candidate.workflow_id == resolution.target_workflow_id)
        if (resolution.target_stage is not None or resolution.target_artifact_ref is not None) and not _matches_classification_target(
            candidate,
            target_stage=resolution.target_stage,
            target_artifact_ref=resolution.target_artifact_ref,
        ):
            raise ValueError("确定性 stage/artifact 不属于目标 Workflow 的同一证据")
        return self

    @property
    def idempotency_key(self) -> str:
        """从权威 Turn ID 派生稳定决策幂等键。"""

        return f"decision:{self.turn_id}"


class DecisionModel(Protocol):
    """隔离具体 LLM SDK 的异步消息调用边界。"""

    async def ainvoke(
        self,
        messages: Sequence[tuple[str, str]],
    ) -> object:
        """返回 JSON 文本、映射或已经解析的 ActionDecision。"""

        ...


class DecisionClassificationError(ValueError):
    """返回不包含模型原文或异常详情的分类失败摘要。"""

    def __init__(
        self,
        *,
        reason_code: str,
        attempts: int,
        error_codes: tuple[str, ...] = (),
    ) -> None:
        self.reason_code = reason_code
        self.attempts = attempts
        self.error_codes = error_codes
        super().__init__(f"Supervisor 结构化分类失败：{reason_code}")


class _DecisionOutputError(ValueError):
    def __init__(self, *error_codes: str) -> None:
        self.error_codes = tuple(dict.fromkeys(error_codes))
        super().__init__("ActionDecision 输出未通过结构化校验")


class LLMActionClassifier:
    """调用一次结构化模型，并在解析失败时最多请求一次完整修复。"""

    def __init__(self, model: DecisionModel) -> None:
        self._model = model

    async def classify(
        self,
        request: ActionClassificationRequest,
    ) -> ActionDecision:
        """返回合法 ActionDecision；模型或两次解析失败时安全终止。"""

        messages = _build_messages(request)
        last_error_codes: tuple[str, ...] = ()
        for attempt in range(1, _MAX_CLASSIFICATION_ATTEMPTS + 1):
            try:
                raw_output = await self._model.ainvoke(messages)
            except Exception:
                raise DecisionClassificationError(
                    reason_code="classifier_model_failed",
                    attempts=attempt,
                ) from None
            try:
                return _parse_and_validate_decision(raw_output, request)
            except _DecisionOutputError as exc:
                last_error_codes = exc.error_codes
                if attempt == _MAX_CLASSIFICATION_ATTEMPTS:
                    break
                messages = messages + (
                    (
                        "human",
                        _build_repair_prompt(
                            request=request,
                            error_codes=last_error_codes,
                        ),
                    ),
                )
        raise DecisionClassificationError(
            reason_code="classifier_output_invalid",
            attempts=_MAX_CLASSIFICATION_ATTEMPTS,
            error_codes=last_error_codes,
        )


def _build_messages(
    request: ActionClassificationRequest,
) -> tuple[tuple[str, str], ...]:
    payload = {
        "turn_id": request.turn_id,
        "content": request.content,
        "deterministic_resolution": request.deterministic_resolution.model_dump(
            mode="json",
        ),
        "candidates": [candidate.model_dump(mode="json") for candidate in request.candidates],
        "context_summary": request.context_summary,
        "idempotency_key": request.idempotency_key,
        "action_decision_schema": ActionDecision.model_json_schema(
            mode="validation",
        ),
    }
    return (
        ("system", _SYSTEM_PROMPT),
        (
            "human",
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
    )


def _build_repair_prompt(
    *,
    request: ActionClassificationRequest,
    error_codes: tuple[str, ...],
) -> str:
    payload = {
        "instruction": "上一次输出未通过校验，请根据原输入重新输出完整 JSON。",
        "error_codes": error_codes,
        "idempotency_key": request.idempotency_key,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_and_validate_decision(
    raw_output: object,
    request: ActionClassificationRequest,
) -> ActionDecision:
    payload: Any
    if isinstance(raw_output, ActionDecision):
        payload = raw_output.model_dump(mode="json")
    elif isinstance(raw_output, str):
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError:
            raise _DecisionOutputError("json_invalid") from None
    elif isinstance(raw_output, Mapping):
        payload = dict(raw_output)
    else:
        raise _DecisionOutputError("unsupported_output_type")
    if not isinstance(payload, dict):
        raise _DecisionOutputError("json_object_required")
    try:
        decision = ActionDecision.model_validate(payload)
    except ValidationError as exc:
        error_codes = tuple(str(item["type"]) for item in exc.errors(include_url=False, include_context=False))
        raise _DecisionOutputError(*error_codes) from None
    _validate_decision_evidence(decision, request)
    return decision.model_copy(deep=True)


def _validate_decision_evidence(
    decision: ActionDecision,
    request: ActionClassificationRequest,
) -> None:
    resolution = request.deterministic_resolution
    if resolution.status == DeterministicResolutionStatus.AMBIGUOUS:
        if decision.action != AgentAction.CLARIFY or decision.target_workflow_id is not None or decision.target_stage is not None or decision.target_artifact_ref is not None:
            raise _DecisionOutputError(
                "ambiguous_resolution_requires_clarify",
            )
    elif resolution.action is not None and decision.action != resolution.action:
        raise _DecisionOutputError("deterministic_action_conflict")
    if resolution.intent != AgentIntent.GENERAL and decision.intent != resolution.intent:
        raise _DecisionOutputError("deterministic_intent_conflict")
    deterministic_targets = (
        resolution.target_workflow_id,
        resolution.target_stage,
        resolution.target_artifact_ref,
    )
    decision_targets = (
        decision.target_workflow_id,
        decision.target_stage,
        decision.target_artifact_ref,
    )
    if any(
        expected is not None and actual != expected
        for expected, actual in zip(
            deterministic_targets,
            decision_targets,
            strict=True,
        )
    ):
        raise _DecisionOutputError("deterministic_target_conflict")
    candidates_by_id = {candidate.workflow_id: candidate for candidate in request.candidates}
    if decision.target_workflow_id is not None:
        candidate = candidates_by_id.get(decision.target_workflow_id)
        if candidate is None:
            raise _DecisionOutputError("unknown_target_workflow")
        if decision.intent != candidate.intent:
            raise _DecisionOutputError("target_intent_conflict")
        if decision.target_stage is not None and not any(target.target_stage == decision.target_stage for target in candidate.targets):
            raise _DecisionOutputError("target_stage_mismatch")
        if decision.target_artifact_ref is not None and not any(target.target_artifact_ref == decision.target_artifact_ref for target in candidate.targets):
            raise _DecisionOutputError("target_artifact_mismatch")
        if (decision.target_stage is not None or decision.target_artifact_ref is not None) and not _matches_classification_target(
            candidate,
            target_stage=decision.target_stage,
            target_artifact_ref=decision.target_artifact_ref,
        ):
            raise _DecisionOutputError("target_reference_mismatch")
    elif decision.target_stage is not None or decision.target_artifact_ref is not None:
        raise _DecisionOutputError("orphan_target_reference")
    if decision.idempotency_key != request.idempotency_key:
        raise _DecisionOutputError("idempotency_key_conflict")
    if not _REASON_CODE_PATTERN.fullmatch(decision.reason_code):
        raise _DecisionOutputError("invalid_reason_code")


def _matches_classification_target(
    candidate: ActionClassificationCandidate,
    *,
    target_stage: str | None,
    target_artifact_ref: str | None,
) -> bool:
    return any((target_stage is None or target.target_stage == target_stage) and (target_artifact_ref is None or target.target_artifact_ref == target_artifact_ref) for target in candidate.targets)


__all__ = [
    "ActionClassificationCandidate",
    "ActionClassificationRequest",
    "ActionClassificationTarget",
    "DecisionClassificationError",
    "DecisionModel",
    "LLMActionClassifier",
]
