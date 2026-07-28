"""提供 Supervisor 中文黄金集的严格 schema、离线指标和可复现报告。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixelflow.agent_runtime.contracts import AgentAction, AgentIntent

_CHINESE_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_TARGET_REQUIRED_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
    AgentAction.SWITCH_WORKFLOW,
    AgentAction.CANCEL_WORKFLOW,
}
_TARGET_FORBIDDEN_ACTIONS = {
    AgentAction.ANSWER_ONLY,
    AgentAction.CLARIFY,
    AgentAction.START_WORKFLOW,
}
_POTENTIALLY_BILLING_ACTIONS = {
    AgentAction.CONTINUE_WORKFLOW,
    AgentAction.MODIFY_WORKFLOW,
    AgentAction.REGENERATE_STAGE,
    AgentAction.RETRY_FAILED,
    AgentAction.START_WORKFLOW,
}

ACTION_ACCURACY_THRESHOLD = 0.92
TARGET_ACCURACY_THRESHOLD = 0.95
CLARIFICATION_RECALL_THRESHOLD = 0.95
BILLING_MISEXECUTION_THRESHOLD = 0


class _EvaluationModel(BaseModel):
    """为离线评估数据提供严格、不可变的 DTO 边界。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class SupervisorDecisionLabel(_EvaluationModel):
    """保存评估所需的公开决策标签，不保存推理过程或用户敏感载荷。"""

    action: AgentAction
    intent: AgentIntent
    target_workflow_id: str | None = Field(default=None, min_length=1, max_length=255)
    target_artifact_ref: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def require_action_target_contract(self) -> Self:
        """拒绝孤立 Artifact、缺少目标的业务动作和带目标的全局动作。"""

        if self.target_artifact_ref is not None and self.target_workflow_id is None:
            raise ValueError("target_artifact_ref 必须绑定 target_workflow_id")
        if self.action in _TARGET_REQUIRED_ACTIONS and self.target_workflow_id is None:
            raise ValueError("该动作必须携带 target_workflow_id")
        if self.action in _TARGET_FORBIDDEN_ACTIONS and (self.target_workflow_id is not None or self.target_artifact_ref is not None):
            raise ValueError("该动作不得携带既有 Workflow 或 Artifact 目标")
        if self.action == AgentAction.START_WORKFLOW and self.intent == AgentIntent.GENERAL:
            raise ValueError("start_workflow 必须使用业务 intent")
        return self


class SupervisorGoldenCase(_EvaluationModel):
    """保存一个中文输入及对应的期望/离线回放决策。"""

    case_id: str = Field(min_length=3, max_length=64)
    user_input: str = Field(min_length=1, max_length=2048)
    expected: SupervisorDecisionLabel
    observed: SupervisorDecisionLabel

    @model_validator(mode="after")
    def require_auditable_chinese_case(self) -> Self:
        """确保样例可稳定引用，并且确实包含中文业务表达。"""

        if not _CASE_ID_PATTERN.fullmatch(self.case_id):
            raise ValueError("case_id 只能使用小写英文、数字、连字符和下划线")
        if not _CHINESE_TEXT_PATTERN.search(self.user_input):
            raise ValueError("user_input 必须包含中文业务表达")
        return self

    @property
    def is_target_evaluable(self) -> bool:
        """仅把期望携带 Workflow 或 Artifact 的样例纳入目标准确率。"""

        return self.expected.target_workflow_id is not None

    @property
    def is_ambiguous(self) -> bool:
        """以期望追问作为歧义样例的权威标签。"""

        return self.expected.action == AgentAction.CLARIFY


class SupervisorGoldenDataset(_EvaluationModel):
    """冻结一版覆盖全部动作且分母充分的中文黄金集。"""

    schema_version: int = Field(ge=1, le=1)
    dataset_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    cases: tuple[SupervisorGoldenCase, ...] = Field(min_length=40, max_length=500)

    @model_validator(mode="after")
    def require_metric_coverage(self) -> Self:
        """阻止通过重复编号、缺动作或过小分母虚高指标。"""

        case_ids = tuple(case.case_id for case in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("黄金集 case_id 不能重复")
        semantic_keys = tuple(_semantic_case_key(case) for case in self.cases)
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError("黄金集不能通过更换 case_id 重复同一输入和期望标签")
        if {case.expected.action for case in self.cases} != set(AgentAction):
            raise ValueError("黄金集必须覆盖全部 AgentAction")
        if sum(case.is_target_evaluable for case in self.cases) < 20:
            raise ValueError("目标准确率至少需要 20 个有效样例")
        if sum(case.is_ambiguous for case in self.cases) < 10:
            raise ValueError("歧义追问召回至少需要 10 个有效样例")
        return self


class SupervisorEvaluationReport(_EvaluationModel):
    """保存四项模块门槛的计数、比率和最终结论。"""

    dataset_id: str
    case_total: int = Field(ge=1)
    action_correct: int = Field(ge=0)
    action_total: int = Field(ge=1)
    target_correct: int = Field(ge=0)
    target_total: int = Field(ge=1)
    clarification_correct: int = Field(ge=0)
    clarification_total: int = Field(ge=1)
    billing_misexecutions: int = Field(ge=0)
    action_accuracy: float = Field(ge=0, le=1)
    target_accuracy: float = Field(ge=0, le=1)
    clarification_recall: float = Field(ge=0, le=1)
    passed: bool


def load_supervisor_golden_dataset(path: str | Path) -> SupervisorGoldenDataset:
    """从 UTF-8 JSON 读取并严格校验唯一权威黄金集。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return SupervisorGoldenDataset.model_validate(payload)


def evaluate_supervisor_golden_dataset(
    dataset: SupervisorGoldenDataset,
) -> SupervisorEvaluationReport:
    """对已校验黄金集计算模块四项验收指标。"""

    return evaluate_supervisor_cases(
        dataset_id=dataset.dataset_id,
        cases=dataset.cases,
    )


def evaluate_supervisor_cases(
    *,
    dataset_id: str,
    cases: Sequence[SupervisorGoldenCase],
) -> SupervisorEvaluationReport:
    """使用公开标签计算指标；缺少目标或歧义分母时拒绝给出结论。"""

    frozen_cases = tuple(cases)
    if not frozen_cases:
        raise ValueError("离线评估至少需要一个样例")
    target_cases = tuple(case for case in frozen_cases if case.is_target_evaluable)
    ambiguous_cases = tuple(case for case in frozen_cases if case.is_ambiguous)
    if not target_cases:
        raise ValueError("离线评估缺少目标准确率分母")
    if not ambiguous_cases:
        raise ValueError("离线评估缺少歧义追问召回分母")

    action_correct = sum(case.observed.action == case.expected.action for case in frozen_cases)
    target_correct = sum(_target_matches(case) for case in target_cases)
    clarification_correct = sum(case.observed.action == AgentAction.CLARIFY for case in ambiguous_cases)
    billing_misexecutions = sum(_is_billing_misexecution(case) for case in frozen_cases)
    action_accuracy = action_correct / len(frozen_cases)
    target_accuracy = target_correct / len(target_cases)
    clarification_recall = clarification_correct / len(ambiguous_cases)
    passed = action_accuracy >= ACTION_ACCURACY_THRESHOLD and target_accuracy >= TARGET_ACCURACY_THRESHOLD and clarification_recall >= CLARIFICATION_RECALL_THRESHOLD and billing_misexecutions <= BILLING_MISEXECUTION_THRESHOLD
    return SupervisorEvaluationReport(
        dataset_id=dataset_id,
        case_total=len(frozen_cases),
        action_correct=action_correct,
        action_total=len(frozen_cases),
        target_correct=target_correct,
        target_total=len(target_cases),
        clarification_correct=clarification_correct,
        clarification_total=len(ambiguous_cases),
        billing_misexecutions=billing_misexecutions,
        action_accuracy=action_accuracy,
        target_accuracy=target_accuracy,
        clarification_recall=clarification_recall,
        passed=passed,
    )


def render_supervisor_evaluation_report(
    dataset: SupervisorGoldenDataset,
    report: SupervisorEvaluationReport,
) -> str:
    """生成不含动态时间的稳定 Markdown，便于门禁逐字复算。"""

    conclusion = "通过" if report.passed else "未通过"
    return "\n".join(
        (
            "# M05.5 Supervisor 中文黄金集离线评估报告",
            "",
            f"- 数据集：`{dataset.dataset_id}`（schema v{dataset.schema_version}）",
            f"- 样例数：`{report.case_total}`",
            "- 执行方式：固定 fake/mock 回放快照，仅比较公开 Action/Target 标签；未调用 LLM、供应商或付费 API。",
            f"- 结论：`{conclusion}`",
            "",
            "| 指标 | 结果 | 门槛 | 结论 |",
            "| --- | ---: | ---: | --- |",
            _metric_row(
                "action 准确率",
                report.action_correct,
                report.action_total,
                report.action_accuracy,
                ACTION_ACCURACY_THRESHOLD,
            ),
            _metric_row(
                "target Workflow/Artifact 准确率",
                report.target_correct,
                report.target_total,
                report.target_accuracy,
                TARGET_ACCURACY_THRESHOLD,
            ),
            _metric_row(
                "歧义追问召回率",
                report.clarification_correct,
                report.clarification_total,
                report.clarification_recall,
                CLARIFICATION_RECALL_THRESHOLD,
            ),
            (f"| 计费动作误执行 | {report.billing_misexecutions} | = {BILLING_MISEXECUTION_THRESHOLD} | {'通过' if report.billing_misexecutions == BILLING_MISEXECUTION_THRESHOLD else '未通过'} |"),
            "",
            "## 口径",
            "",
            "- action 准确率以全部样例为分母。",
            "- target 准确率只统计期望携带 Workflow 目标的样例，并同时精确比较 Workflow 与 Artifact。",
            "- 歧义追问召回率只统计期望动作是 `clarify` 的样例。",
            "- 如果回放结果执行了潜在计费动作，但动作、intent 或目标与期望不一致，即计为一次误计费。",
            "- 报告只证明 M05 离线门槛，不代表 R2 已集成、已发布或已获得真实付费测试授权。",
            "",
        )
    )


def _target_matches(case: SupervisorGoldenCase) -> bool:
    """精确比较 Workflow 与 Artifact，防止只命中工作流却改错产物。"""

    return case.observed.target_workflow_id == case.expected.target_workflow_id and case.observed.target_artifact_ref == case.expected.target_artifact_ref


def _is_billing_misexecution(case: SupervisorGoldenCase) -> bool:
    """识别动作、intent 或目标不符合期望却仍执行潜在计费动作的样例。"""

    if case.observed.action not in _POTENTIALLY_BILLING_ACTIONS:
        return False
    return case.observed.action != case.expected.action or case.observed.intent != case.expected.intent or (case.is_target_evaluable and not _target_matches(case))


def _semantic_case_key(case: SupervisorGoldenCase) -> tuple[object, ...]:
    """用规范化中文输入和期望公开标签识别换 ID 的语义重复。"""

    expected = case.expected
    return (
        " ".join(case.user_input.split()),
        expected.action,
        expected.intent,
        expected.target_workflow_id,
        expected.target_artifact_ref,
    )


def _metric_row(
    name: str,
    correct: int,
    total: int,
    value: float,
    threshold: float,
) -> str:
    """使用统一百分比格式输出可审计指标行。"""

    passed = value >= threshold
    return f"| {name} | {correct}/{total}（{value:.2%}） | ≥ {threshold:.0%} | {'通过' if passed else '未通过'} |"


__all__ = [
    "ACTION_ACCURACY_THRESHOLD",
    "BILLING_MISEXECUTION_THRESHOLD",
    "CLARIFICATION_RECALL_THRESHOLD",
    "TARGET_ACCURACY_THRESHOLD",
    "SupervisorDecisionLabel",
    "SupervisorEvaluationReport",
    "SupervisorGoldenCase",
    "SupervisorGoldenDataset",
    "evaluate_supervisor_cases",
    "evaluate_supervisor_golden_dataset",
    "load_supervisor_golden_dataset",
    "render_supervisor_evaluation_report",
]
