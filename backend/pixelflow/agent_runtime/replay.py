"""M13 会话 Supervisor 的非付费回放与 Shadow 执行边界。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .config import AgentRuntimeConfig
from .context import (
    ContextBudgetPolicyProvider,
    ModelContextProfile,
    TokenMeter,
    estimate_context_tokens,
)
from .contracts import ActionDecision, AgentAction, AgentIntent, ContextBudgetReport
from .graph import supervisor_namespace


class SupervisorReplayDisposition(StrEnum):
    """描述一次回放是否拥有业务副作用执行权。"""

    DISABLED = "disabled"
    DELEGATED = "delegated"
    SHADOW = "shadow"
    PRIMARY = "primary"


class WorkflowCommandPreview(BaseModel):
    """Shadow 只记录的标准命令 DTO，不包含凭据或供应商请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    action: AgentAction
    intent: AgentIntent
    target_workflow_id: str | None = Field(default=None, min_length=1)
    target_stage: str | None = Field(default=None, min_length=1)
    target_artifact_ref: str | None = Field(default=None, min_length=1)
    current_input: str = Field(min_length=1)
    materials: list[dict[str, JsonValue]] = Field(default_factory=list)
    reply_to_message_id: str | None = Field(default=None, min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class SupervisorReplayResult:
    """保存可审计决策、统一预算和可选的主执行结果。"""

    disposition: SupervisorReplayDisposition
    decision: ActionDecision
    command: WorkflowCommandPreview | None
    budget_report: ContextBudgetReport | None
    output_state: Mapping[str, Any] | None


class SupervisorReplayRuntime:
    """在副作用边界前实现 kill switch、Shadow 和 primary 分流。"""

    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        graph: Any,
        model_name: str,
        model_profiles: Mapping[str, ModelContextProfile],
        budget_policy_provider: ContextBudgetPolicyProvider | None = None,
        clock: Callable[[], datetime] | None = None,
        node_name: str = "supervisor_replay",
    ) -> None:
        self._config = AgentRuntimeConfig.model_validate(
            config.model_dump(mode="python"),
        )
        self._graph = graph
        self._model_name = _required_text(model_name, "model_name")
        self._model_profiles = dict(model_profiles)
        self._budget_policy_provider = (
            budget_policy_provider
            or ContextBudgetPolicyProvider(self._config.context_budget)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._node_name = _required_text(node_name, "node_name")

    async def replay(
        self,
        state: Mapping[str, Any],
    ) -> SupervisorReplayResult:
        """回放一条冻结决策；Shadow 永远不会进入 Graph Handler。"""

        frozen_state = deepcopy(dict(state))
        decision = ActionDecision.model_validate(
            frozen_state.get("decision"),
        ).model_copy(deep=True)
        disposition = self._disposition(decision)
        if disposition in {
            SupervisorReplayDisposition.DISABLED,
            SupervisorReplayDisposition.DELEGATED,
        }:
            return SupervisorReplayResult(
                disposition=disposition,
                decision=decision,
                command=None,
                budget_report=None,
                output_state=None,
            )

        command = _command_preview(frozen_state, decision)
        budget_report = self._measure(command)
        if disposition is SupervisorReplayDisposition.SHADOW:
            # Shadow 在 Handler、Operation 和 PowerMem record 之前结束，只保留
            # 决策及标准 DTO 供对比；任何副作用必须由 primary 路径显式拥有。
            return SupervisorReplayResult(
                disposition=disposition,
                decision=decision,
                command=command,
                budget_report=budget_report,
                output_state=None,
            )

        output = await self._graph.ainvoke(
            frozen_state,
            supervisor_namespace(command.conversation_id).as_runnable_config(),
        )
        return SupervisorReplayResult(
            disposition=disposition,
            decision=decision,
            command=command,
            budget_report=budget_report,
            output_state=deepcopy(output),
        )

    def _disposition(
        self,
        decision: ActionDecision,
    ) -> SupervisorReplayDisposition:
        if self._config.mode in {"off", "assist"}:
            return SupervisorReplayDisposition.DISABLED
        if (
            decision.intent is not AgentIntent.GENERAL
            and decision.intent.value not in self._config.enabled_intents
        ):
            return SupervisorReplayDisposition.DELEGATED
        if self._config.mode == "shadow":
            return SupervisorReplayDisposition.SHADOW
        if self._config.mode == "primary":
            return SupervisorReplayDisposition.PRIMARY
        return SupervisorReplayDisposition.DISABLED

    def _measure(
        self,
        command: WorkflowCommandPreview,
    ) -> ContextBudgetReport:
        """从共享 Provider 读取预算，并严格验证实际模型档案。"""

        profile = self._budget_policy_provider.resolve_model_profile(
            self._model_name,
            self._model_profiles,
            now=self._clock(),
        )
        policy = self._budget_policy_provider.policy_for(self._node_name)
        payload = command.model_dump(mode="json")
        return TokenMeter().measure(
            estimated_input_tokens=estimate_context_tokens(payload),
            profile=profile,
            policy=policy,
        )


def _command_preview(
    state: Mapping[str, Any],
    decision: ActionDecision,
) -> WorkflowCommandPreview:
    """把同一份 Turn 输入投影为 Shadow 和 primary 共用的命令 DTO。"""

    return WorkflowCommandPreview(
        conversation_id=_state_text(state, "conversation_id"),
        turn_id=_state_text(state, "turn_id"),
        action=decision.action,
        intent=decision.intent,
        target_workflow_id=decision.target_workflow_id,
        target_stage=decision.target_stage,
        target_artifact_ref=decision.target_artifact_ref,
        current_input=_state_text(state, "current_input"),
        materials=_materials(state.get("materials", [])),
        reply_to_message_id=_optional_state_text(
            state.get("reply_to_message_id"),
            "reply_to_message_id",
        ),
        artifact_refs=_artifact_refs(state.get("artifact_refs", [])),
        idempotency_key=decision.idempotency_key,
    )


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _state_text(state: Mapping[str, Any], field_name: str) -> str:
    return _required_text(state.get(field_name), field_name)


def _optional_state_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _materials(value: Any) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("materials 必须是对象数组")
    return deepcopy(value)


def _artifact_refs(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip()
        for item in value
    ):
        raise ValueError("artifact_refs 必须是非空字符串数组")
    return list(value)


__all__ = [
    "SupervisorReplayDisposition",
    "SupervisorReplayResult",
    "SupervisorReplayRuntime",
    "WorkflowCommandPreview",
]
