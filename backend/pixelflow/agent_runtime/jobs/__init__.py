"""持久化 External Job Operation 领域能力。"""

from .coordinator import OperationCoordinator
from .identity import (
    build_operation_idempotency_key,
    build_operation_request,
    hash_operation_request,
)
from .state_machine import (
    OPERATION_STATE_TRANSITIONS,
    TERMINAL_OPERATION_STATUSES,
    OperationStateConflictError,
    ensure_operation_transition,
)

__all__ = [
    "OPERATION_STATE_TRANSITIONS",
    "TERMINAL_OPERATION_STATUSES",
    "OperationCoordinator",
    "OperationStateConflictError",
    "build_operation_idempotency_key",
    "build_operation_request",
    "ensure_operation_transition",
    "hash_operation_request",
]
