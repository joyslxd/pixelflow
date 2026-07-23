"""统一计算模型调用的上下文预算与压缩等级。"""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import ContextBudgetReport
from .profiles import ModelContextProfile

ContextBudgetNode = Literal[
    "supervisor",
    "image",
    "image_edit",
    "video",
    "ppt",
    "video_analysis",
    "summary",
]


class ContextBudgetPolicy(BaseModel):
    """记录业务节点对有效窗口、输出和安全空间的统一预留。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effective_context_cap_tokens: int = Field(ge=1)
    output_reserve_tokens: int = Field(ge=1)
    safety_reserve_tokens: int = Field(ge=0)


_CONTEXT_BUDGET_POLICIES = MappingProxyType(
    {
        "supervisor": ContextBudgetPolicy(
            effective_context_cap_tokens=256 * 1024,
            output_reserve_tokens=8 * 1024,
            safety_reserve_tokens=32 * 1024,
        ),
        "image": ContextBudgetPolicy(
            effective_context_cap_tokens=256 * 1024,
            output_reserve_tokens=16 * 1024,
            safety_reserve_tokens=32 * 1024,
        ),
        "image_edit": ContextBudgetPolicy(
            effective_context_cap_tokens=256 * 1024,
            output_reserve_tokens=16 * 1024,
            safety_reserve_tokens=32 * 1024,
        ),
        "video": ContextBudgetPolicy(
            effective_context_cap_tokens=384 * 1024,
            output_reserve_tokens=32 * 1024,
            safety_reserve_tokens=48 * 1024,
        ),
        "ppt": ContextBudgetPolicy(
            effective_context_cap_tokens=384 * 1024,
            output_reserve_tokens=32 * 1024,
            safety_reserve_tokens=48 * 1024,
        ),
        "video_analysis": ContextBudgetPolicy(
            effective_context_cap_tokens=512 * 1024,
            output_reserve_tokens=48 * 1024,
            safety_reserve_tokens=64 * 1024,
        ),
        "summary": ContextBudgetPolicy(
            effective_context_cap_tokens=384 * 1024,
            output_reserve_tokens=24 * 1024,
            safety_reserve_tokens=48 * 1024,
        ),
    },
)

_COMPACTION_THRESHOLDS = (
    (92, 4),
    (85, 3),
    (72, 2),
    (60, 1),
)


def get_context_budget_policy(node: str) -> ContextBudgetPolicy:
    """按统一节点名返回不可变预算策略。"""

    try:
        return _CONTEXT_BUDGET_POLICIES[node]
    except KeyError:
        raise ValueError(f"未知的上下文预算节点：{node}") from None


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

    def measure(
        self,
        *,
        estimated_input_tokens: int,
        profile: ModelContextProfile,
        policy: ContextBudgetPolicy,
    ) -> ContextBudgetReport:
        """按模型能力和业务上限取较小值，并保留实际输出空间。"""

        if (
            isinstance(estimated_input_tokens, bool)
            or not isinstance(estimated_input_tokens, int)
            or estimated_input_tokens < 0
        ):
            raise ValueError("estimated_input_tokens 必须是非负整数")

        effective_context_tokens = min(
            profile.max_context_tokens,
            policy.effective_context_cap_tokens,
        )
        max_output_tokens = min(
            profile.max_output_tokens,
            policy.output_reserve_tokens,
        )
        usable_input_tokens = (
            effective_context_tokens
            - max_output_tokens
            - policy.safety_reserve_tokens
        )
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
    "TokenMeter",
    "get_context_budget_policy",
]
