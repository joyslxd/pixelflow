from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pixelflow.agent_runtime.contracts import AgentAction, AgentIntent
from pixelflow.agent_runtime.supervisor import (
    SupervisorDecisionLabel,
    SupervisorGoldenCase,
    SupervisorGoldenDataset,
    evaluate_supervisor_cases,
    evaluate_supervisor_golden_dataset,
    load_supervisor_golden_dataset,
    render_supervisor_evaluation_report,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "supervisor_golden_cases.json"
REPORT_PATH = Path(__file__).parents[2] / "docs" / "agentization" / "test-reports" / "M05.5-supervisor-golden-evaluation.md"


def _label(
    action: AgentAction,
    *,
    workflow_id: str | None = None,
    artifact_ref: str | None = None,
) -> SupervisorDecisionLabel:
    return SupervisorDecisionLabel(
        action=action,
        intent=AgentIntent.VIDEO,
        target_workflow_id=workflow_id,
        target_artifact_ref=artifact_ref,
    )


def _case(
    case_id: str,
    *,
    expected: SupervisorDecisionLabel,
    observed: SupervisorDecisionLabel,
) -> SupervisorGoldenCase:
    return SupervisorGoldenCase(
        case_id=case_id,
        user_input="请处理这个中文测试请求",
        expected=expected,
        observed=observed,
    )


def test_黄金集覆盖全部动作并提供稳定评估分母() -> None:
    dataset = load_supervisor_golden_dataset(FIXTURE_PATH)

    assert dataset.schema_version == 1
    assert len(dataset.cases) >= 40
    assert {case.expected.action for case in dataset.cases} == set(AgentAction)
    assert sum(case.is_target_evaluable for case in dataset.cases) >= 20
    assert sum(case.is_ambiguous for case in dataset.cases) >= 10
    assert all(any("\u4e00" <= char <= "\u9fff" for char in case.user_input) for case in dataset.cases)


def test_离线评估达到模块四项门槛且报告可复现() -> None:
    dataset = load_supervisor_golden_dataset(FIXTURE_PATH)
    report = evaluate_supervisor_golden_dataset(dataset)

    assert report.action_accuracy >= 0.92
    assert report.target_accuracy >= 0.95
    assert report.clarification_recall >= 0.95
    assert report.billing_misexecutions == 0
    assert report.passed is True
    assert render_supervisor_evaluation_report(dataset, report) == REPORT_PATH.read_text(
        encoding="utf-8",
    )


def test_指标分母和误计费判断不被无目标样例稀释() -> None:
    cases = (
        _case(
            "metric-target-correct",
            expected=_label(
                AgentAction.MODIFY_WORKFLOW,
                workflow_id="wf-video",
                artifact_ref="artifact:scene:3",
            ),
            observed=_label(
                AgentAction.MODIFY_WORKFLOW,
                workflow_id="wf-video",
                artifact_ref="artifact:scene:3",
            ),
        ),
        _case(
            "metric-target-wrong",
            expected=_label(
                AgentAction.REGENERATE_STAGE,
                workflow_id="wf-video",
                artifact_ref="artifact:scene:4",
            ),
            observed=_label(
                AgentAction.REGENERATE_STAGE,
                workflow_id="wf-video",
                artifact_ref="artifact:scene:5",
            ),
        ),
        _case(
            "metric-clarify-missed-safely",
            expected=_label(AgentAction.CLARIFY),
            observed=_label(AgentAction.ANSWER_ONLY),
        ),
        _case(
            "metric-clarify-billing-misexecution",
            expected=_label(AgentAction.CLARIFY),
            observed=_label(AgentAction.START_WORKFLOW),
        ),
        _case(
            "metric-start-wrong-intent",
            expected=_label(AgentAction.START_WORKFLOW),
            observed=SupervisorDecisionLabel(
                action=AgentAction.START_WORKFLOW,
                intent=AgentIntent.IMAGE,
            ),
        ),
    )

    report = evaluate_supervisor_cases(
        dataset_id="metric-contract",
        cases=cases,
    )

    assert report.action_correct == 3
    assert report.action_total == 5
    assert report.target_correct == 1
    assert report.target_total == 2
    assert report.clarification_correct == 0
    assert report.clarification_total == 2
    assert report.billing_misexecutions == 3
    assert report.passed is False


def test_黄金集拒绝非中文输入和不完整目标() -> None:
    with pytest.raises(ValidationError):
        SupervisorGoldenCase(
            case_id="invalid-language",
            user_input="continue this workflow",
            expected=_label(AgentAction.CONTINUE_WORKFLOW, workflow_id="wf-video"),
            observed=_label(AgentAction.CONTINUE_WORKFLOW, workflow_id="wf-video"),
        )

    with pytest.raises(ValidationError):
        SupervisorDecisionLabel(
            action=AgentAction.MODIFY_WORKFLOW,
            intent=AgentIntent.VIDEO,
            target_artifact_ref="artifact:scene:3",
        )


def test_黄金集拒绝重复编号和动作覆盖不足() -> None:
    repeated = _case(
        "duplicate-case",
        expected=_label(AgentAction.ANSWER_ONLY),
        observed=_label(AgentAction.ANSWER_ONLY),
    )

    with pytest.raises(ValidationError):
        SupervisorGoldenDataset(
            schema_version=1,
            dataset_id="invalid-coverage",
            description="这个无效数据集用于验证重复编号和动作覆盖。",
            cases=(repeated,) * 40,
        )


def test_黄金集拒绝仅更换编号的同语义重复() -> None:
    dataset = load_supervisor_golden_dataset(FIXTURE_PATH)
    duplicate = dataset.cases[0].model_copy(
        update={"case_id": "semantic-duplicate"},
    )

    with pytest.raises(ValidationError):
        SupervisorGoldenDataset(
            schema_version=dataset.schema_version,
            dataset_id="invalid-semantic-duplicate",
            description="这个无效数据集复制同一输入和期望标签。",
            cases=dataset.cases + (duplicate,),
        )
