"""统一计算模型调用的上下文预算与压缩等级。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..config import ContextBudgetConfig
from ..contracts import ContextBudgetReport
from .profiles import (
    ModelContextProfile,
    resolve_model_context_profile,
)

ContextBudgetNode = str


class VerifiedModelProfileUnavailableError(ValueError):
    """表示严格模式缺少当前有效且已验证的模型档案。"""

    def __init__(self, model_name: str) -> None:
        super().__init__(
            f"模型 {model_name} 缺少当前有效且已验证的 context_profile"
        )


class ContextBudgetPolicy(BaseModel):
    """记录业务节点对有效窗口、输出和安全空间的统一预留。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effective_context_cap_tokens: int = Field(ge=1)
    output_reserve_tokens: int = Field(ge=1)
    safety_reserve_tokens: int = Field(ge=0)


_COMPACTION_THRESHOLDS = (
    (92, 4),
    (85, 3),
    (72, 2),
    (60, 1),
)


class ContextBudgetPolicyProvider:
    """把一份启动配置统一提供给所有当前和未来 Agent 节点。"""

    def __init__(self, config: ContextBudgetConfig | None = None) -> None:
        self._config = config or ContextBudgetConfig()
        self._policy = ContextBudgetPolicy(
            effective_context_cap_tokens=self._config.effective_context_tokens,
            output_reserve_tokens=self._config.output_reserve_tokens,
            safety_reserve_tokens=self._config.safety_reserve_tokens,
        )

    @property
    def require_verified_model_profile(self) -> bool:
        return self._config.require_verified_model_profile

    def policy_for(self, node: str) -> ContextBudgetPolicy:
        """节点名只用于审计，预算始终来自同一份配置。"""

        if not isinstance(node, str) or not node.strip():
            raise ValueError("上下文预算节点名不能为空")
        return self._policy

    def resolve_model_profile(
        self,
        model_name: str,
        profiles: Mapping[str, ModelContextProfile],
        *,
        now: datetime,
    ) -> ModelContextProfile:
        """严格模式拒绝未验证档案，兼容模式仍保留底层保守解析。"""

        resolution = resolve_model_context_profile(
            model_name,
            profiles,
            now=now,
        )
        if (
            self.require_verified_model_profile
            and resolution.status != "verified"
        ):
            raise VerifiedModelProfileUnavailableError(model_name)
        return resolution.profile


_DEFAULT_POLICY_PROVIDER = ContextBudgetPolicyProvider()


def get_context_budget_policy(node: str) -> ContextBudgetPolicy:
    """兼容入口：所有节点均返回默认统一预算。"""

    return _DEFAULT_POLICY_PROVIDER.policy_for(node)


def _compaction_level(
    estimated_input_tokens: int,
    usable_input_tokens: int,
) -> int:
    """使用整数乘法判定边界，避免浮点误差改变压缩等级。"""

    for threshold_percentage, level in _COMPACTION_THRESHOLDS:
        if estimated_input_tokens * 100 >= usable_input_tokens * threshold_percentage:
            return level
    return 0


class TokenMeter:
    """把调用前的输入估算转换为冻结的上下文预算报告。"""

    def remeasure(
        self,
        *,
        estimated_input_tokens: int,
        baseline: ContextBudgetReport,
    ) -> ContextBudgetReport:
        """复用既有窗口和预留值，对压缩后的输入重新计算统一等级。"""

        if isinstance(estimated_input_tokens, bool) or not isinstance(estimated_input_tokens, int) or estimated_input_tokens < 0:
            raise ValueError("estimated_input_tokens 必须是非负整数")
        return ContextBudgetReport(
            estimated_input_tokens=estimated_input_tokens,
            effective_context_tokens=baseline.effective_context_tokens,
            usable_input_tokens=baseline.usable_input_tokens,
            max_output_tokens=baseline.max_output_tokens,
            safety_reserve_tokens=baseline.safety_reserve_tokens,
            utilization=estimated_input_tokens / baseline.usable_input_tokens,
            compaction_level=_compaction_level(
                estimated_input_tokens,
                baseline.usable_input_tokens,
            ),
        )

    def measure(
        self,
        *,
        estimated_input_tokens: int,
        profile: ModelContextProfile,
        policy: ContextBudgetPolicy,
    ) -> ContextBudgetReport:
        """按模型能力和业务上限取较小值，并保留实际输出空间。"""

        if isinstance(estimated_input_tokens, bool) or not isinstance(estimated_input_tokens, int) or estimated_input_tokens < 0:
            raise ValueError("estimated_input_tokens 必须是非负整数")

        effective_context_tokens = min(
            profile.max_context_tokens,
            policy.effective_context_cap_tokens,
        )
        max_output_tokens = min(
            profile.max_output_tokens,
            policy.output_reserve_tokens,
        )
        usable_input_tokens = effective_context_tokens - max_output_tokens - policy.safety_reserve_tokens
        if usable_input_tokens <= 0:
            raise ValueError("usable_input 必须在输出和安全预留后仍大于零")

        return ContextBudgetReport(
            estimated_input_tokens=estimated_input_tokens,
            effective_context_tokens=effective_context_tokens,
            usable_input_tokens=usable_input_tokens,
            max_output_tokens=max_output_tokens,
            safety_reserve_tokens=policy.safety_reserve_tokens,
            utilization=estimated_input_tokens / usable_input_tokens,
            compaction_level=_compaction_level(
                estimated_input_tokens,
                usable_input_tokens,
            ),
        )


__all__ = [
    "ContextBudgetNode",
    "ContextBudgetPolicy",
    "ContextBudgetPolicyProvider",
    "TokenMeter",
    "VerifiedModelProfileUnavailableError",
    "get_context_budget_policy",
]
