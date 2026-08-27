"""持久化 External Job Operation 领域能力。"""

from .batch import (
    MAX_CHILD_OPERATIONS_PER_BATCH,
    OperationBatchChild,
    OperationBatchPlan,
    build_operation_batch_completion_event_id,
    build_operation_batch_plan,
)
from .batch_repository import (
    MemoryOperationBatchRepository,
    OperationBatchChildRecord,
    OperationBatchRecord,
    OperationBatchRepository,
    SQLOperationBatchRepository,
)
from .batch_resume import OperationBatchResumeDispatcher, OperationBatchResumePort
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
from .quota import (
    OperationQuotaAuthorizedResume,
    OperationQuotaCoordinator,
    OperationQuotaDispatcher,
    OperationQuotaEventPayload,
    OperationQuotaState,
    OperationQuotaTransitionRecord,
    WorkflowGraphQuotaStatePort,
    build_operation_quota_event_id,
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
    "MAX_CHILD_OPERATIONS_PER_BATCH",
    "MemoryOperationBatchRepository",
    "OperationBatchChild",
    "OperationBatchChildRecord",
    "OperationBatchPlan",
    "OperationBatchRecord",
    "OperationBatchRepository",
    "OperationBatchResumeDispatcher",
    "OperationBatchResumePort",
    "OperationLeaseCoordinator",
    "OperationQuotaAuthorizedResume",
    "OperationQuotaCoordinator",
    "OperationQuotaDispatcher",
    "OperationQuotaEventPayload",
    "OperationQuotaState",
    "OperationQuotaTransitionRecord",
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
    "WorkflowGraphQuotaStatePort",
    "build_operation_completion_event_id",
    "build_operation_batch_completion_event_id",
    "build_operation_batch_plan",
    "build_operation_idempotency_key",
    "build_operation_quota_event_id",
    "build_operation_request",
    "ensure_operation_transition",
    "hash_operation_request",
    "SQLOperationBatchRepository",
]
