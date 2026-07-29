"""持久化 External Job Operation 领域能力。"""

from .completion import (
    OperationCompletionConflictError,
    OperationCompletionCoordinator,
    OperationCompletionDispatcher,
    OperationCompletionDispatchError,
    OperationCompletionRecord,
    WorkflowGraphResumePort,
    build_operation_completion_event_id,
)
from .coordinator import OperationCoordinator
from .identity import (
    build_operation_idempotency_key,
    build_operation_request,
    hash_operation_request,
)
from .leases import OperationLeaseCoordinator
from .providers import (
    ExistingJobService,
    ProviderJobAdapter,
    ProviderJobCallError,
    ProviderJobMappingError,
    ProviderJobOutcome,
    ProviderJobSnapshot,
)
from .recovery import (
    MappingProviderJobAdapterResolver,
    OperationManualRecoveryAction,
    OperationManualRecoveryResult,
    OperationRecoveryRuntime,
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapterResolver,
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
    "OperationCompletionConflictError",
    "OperationCompletionCoordinator",
    "OperationCompletionDispatchError",
    "OperationCompletionDispatcher",
    "OperationCompletionRecord",
    "OperationCoordinator",
    "OperationLeaseCoordinator",
    "OperationStateConflictError",
    "ExistingJobService",
    "MappingProviderJobAdapterResolver",
    "OperationManualRecoveryAction",
    "OperationManualRecoveryResult",
    "OperationRecoveryRuntime",
    "OperationStartCoordinator",
    "OperationStartQuotaPausedError",
    "ProviderJobAdapter",
    "ProviderJobAdapterResolver",
    "ProviderJobCallError",
    "ProviderJobMappingError",
    "ProviderJobOutcome",
    "ProviderJobSnapshot",
    "WorkflowGraphResumePort",
    "build_operation_completion_event_id",
    "build_operation_idempotency_key",
    "build_operation_request",
    "ensure_operation_transition",
    "hash_operation_request",
]
