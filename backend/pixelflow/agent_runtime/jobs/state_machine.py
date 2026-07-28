"""External Job Operation 的显式状态迁移合同。"""

from __future__ import annotations

from ..contracts import ExternalJobStatus
from ..ports import OperationConflictError


class OperationStateConflictError(OperationConflictError):
    """拒绝终态重开或其他未声明的 operation 状态迁移。"""


TERMINAL_OPERATION_STATUSES = frozenset(
    {
        ExternalJobStatus.SUCCEEDED,
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }
)

OPERATION_STATE_TRANSITIONS: dict[
    ExternalJobStatus,
    frozenset[ExternalJobStatus],
] = {
    ExternalJobStatus.CREATED: frozenset(
        {
            ExternalJobStatus.CREATED,
            ExternalJobStatus.POLLING,
        }
    ).union(TERMINAL_OPERATION_STATUSES),
    ExternalJobStatus.POLLING: frozenset(
        {
            ExternalJobStatus.POLLING,
        }
    ).union(TERMINAL_OPERATION_STATUSES),
    ExternalJobStatus.SUCCEEDED: frozenset({ExternalJobStatus.SUCCEEDED}),
    ExternalJobStatus.FAILED: frozenset({ExternalJobStatus.FAILED}),
    ExternalJobStatus.TIMEOUT: frozenset({ExternalJobStatus.TIMEOUT}),
    ExternalJobStatus.EXPIRED: frozenset({ExternalJobStatus.EXPIRED}),
}


def ensure_operation_transition(
    current: ExternalJobStatus,
    target: ExternalJobStatus,
) -> None:
    """校验 operation 状态变化；同状态重放视为幂等。"""

    allowed = OPERATION_STATE_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        current_value = current.value if isinstance(current, ExternalJobStatus) else str(current)
        target_value = target.value if isinstance(target, ExternalJobStatus) else str(target)
        raise OperationStateConflictError(f"Operation 状态不允许从 {current_value} 迁移到 {target_value}")
