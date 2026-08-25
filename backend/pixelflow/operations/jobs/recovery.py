"""External Job 的单次启动、进程恢复扫描与人工恢复入口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import ConfigDict, JsonValue

from pixelflow.agent_control_plane.contracts import ExternalJobStatus, OperationRequest
from pixelflow.agent_control_plane.contracts.base import ContractModel
from pixelflow.agent_control_plane.persistence.repositories import (
    AgentRuntimeRepository,
    OperationRecord,
    OwnedOperationQuotaEvent,
)

from ..ports import OperationConflictError
from .completion import (
    OperationCompletionCoordinator,
    OperationCompletionDispatcher,
    WorkflowGraphResumePort,
)
from .coordinator import OperationCoordinator
from .identity import hash_operation_request
from .providers import (
    ProviderJobAdapter,
    ProviderJobCallError,
    ProviderJobMappingError,
    ProviderJobOutcome,
)
from .quota import (
    OperationQuotaCoordinator,
    OperationQuotaDispatcher,
    WorkflowGraphQuotaStatePort,
)

logger = logging.getLogger(__name__)


def _positive_duration(field: str, value: timedelta) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise ValueError(f"{field} 必须是大于零的 timedelta")
    return value


def _scope_text(field: str, value: str, *, maximum: int = 64) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > maximum:
        raise ValueError(f"{field} 必须是 1 到 {maximum} 个无首尾空白的字符")
    return normalized


@runtime_checkable
class ProviderJobAdapterResolver(Protocol):
    """按持久化 stage 选择既有 Provider Job Adapter。"""

    def resolve(self, stage: str) -> ProviderJobAdapter: ...


class MappingProviderJobAdapterResolver:
    """用冻结映射实现 stage 到 Adapter 的显式路由。"""

    def __init__(
        self,
        adapters: Mapping[str, ProviderJobAdapter],
    ) -> None:
        if not isinstance(adapters, Mapping) or not adapters:
            raise ValueError("adapters 必须是非空映射")
        normalized: dict[str, ProviderJobAdapter] = {}
        for stage, adapter in adapters.items():
            stage_name = _scope_text("stage", stage)
            if not isinstance(adapter, ProviderJobAdapter):
                raise TypeError("adapter 必须是 ProviderJobAdapter")
            if stage_name in normalized:
                raise ValueError("stage 不得重复")
            normalized[stage_name] = adapter
        self._adapters = normalized

    def resolve(self, stage: str) -> ProviderJobAdapter:
        """未知 stage 必须 fail-closed，不能猜测供应商协议。"""

        stage_name = _scope_text("stage", stage)
        try:
            return self._adapters[stage_name]
        except KeyError:
            raise OperationConflictError("Operation stage 未配置 Provider Job Adapter") from None


class OperationManualRecoveryAction(StrEnum):
    """人工恢复只允许继续原任务或要求创建新 attempt。"""

    RESUMED_ORIGINAL_JOB = "resumed_original_job"
    NEW_ATTEMPT_REQUIRED = "new_attempt_required"


class OperationManualRecoveryResult(ContractModel):
    """人工恢复动作及其权威 Operation 快照。"""

    model_config = ConfigDict(frozen=True)

    action: OperationManualRecoveryAction
    operation: OperationRecord


class OperationStartQuotaPausedError(OperationConflictError):
    """Provider 尚未创建任务时返回固定、可重试的额度暂停结果。"""

    reason_code = "provider_quota_insufficient"
    message = "额度不足，当前任务尚未启动，可在充值后重试。"

    def __init__(self, operation: OperationRecord) -> None:
        self.operation = OperationRecord.model_validate(operation.model_dump(mode="python"))
        super().__init__(self.message)


class OperationStartCoordinator:
    """用数据库 start lease 保证并发请求只调用一次 Provider start。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        adapter: ProviderJobAdapter,
        user_id: str,
        conversation_id: str,
        clock: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        first_poll_delay: timedelta = timedelta(seconds=2),
    ) -> None:
        if not isinstance(adapter, ProviderJobAdapter):
            raise TypeError("adapter 必须是 ProviderJobAdapter")
        self._repository = repository
        self._adapter = adapter
        self._user_id = _scope_text("user_id", user_id)
        self._conversation_id = _scope_text(
            "conversation_id",
            conversation_id,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory
        self._lease_duration = _positive_duration(
            "lease_duration",
            lease_duration,
        )
        self._first_poll_delay = _positive_duration(
            "first_poll_delay",
            first_poll_delay,
        )

    async def start(
        self,
        request: OperationRequest,
        *,
        provider_request: Mapping[str, JsonValue],
        authorization: str | None = None,
        authorization_provider: Callable[[], str] | None = None,
        lease_owner: str,
    ) -> OperationRecord:
        """只在持有 start lease 时透传本次请求与凭据，持久层仅保存摘要。"""

        if (authorization is None) == (authorization_provider is None):
            raise ValueError(
                "authorization与authorization_provider必须且只能提供一个"
            )
        if authorization_provider is not None and not callable(
            authorization_provider
        ):
            raise TypeError("authorization_provider必须可调用")

        normalized_request = OperationRequest.model_validate(request.model_dump(mode="python"))
        if hash_operation_request(provider_request) != normalized_request.request_hash:
            raise OperationConflictError("Provider 请求与 Operation request_hash 不一致")
        worker = _scope_text("lease_owner", lease_owner, maximum=128)
        coordinator = OperationCoordinator(
            self._repository,
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            now=self._clock,
            job_id_factory=self._job_id_factory,
        )
        operation = await coordinator.claim(normalized_request)
        if operation.status is not ExternalJobStatus.CREATED:
            return operation

        claim_time = self._clock()
        claimed = await self._repository.claim_operation_start(
            self._user_id,
            self._conversation_id,
            operation.job_id,
            lease_owner=worker,
            now=claim_time,
            lease_expires_at=claim_time + self._lease_duration,
        )
        if claimed is None:
            winner = await self._repository.get_operation(
                self._user_id,
                operation.job_id,
            )
            if winner is None or winner.conversation_id != self._conversation_id:
                raise OperationConflictError("Operation start 竞争结果不可见")
            return winner

        try:
            start_authorization = (
                authorization
                if authorization_provider is None
                else authorization_provider()
            )
            if (
                not isinstance(start_authorization, str)
                or not start_authorization.strip()
            ):
                raise ValueError("Provider start缺少临时Authorization")
        except Exception as exc:
            await self._repository.release_operation_start(
                self._user_id,
                self._conversation_id,
                operation.job_id,
                lease_owner=worker,
                now=self._clock(),
            )
            raise OperationConflictError(
                "Provider start临时Authorization不可用"
            ) from exc

        try:
            snapshot = await self._adapter.start(
                provider_request,
                authorization=start_authorization,
                idempotency_key=normalized_request.idempotency_key,
            )
        finally:
            start_authorization = ""
        if snapshot.provider_job_id is None:
            if snapshot.outcome is ProviderJobOutcome.PAUSED_QUOTA:
                released = await self._repository.release_operation_start(
                    self._user_id,
                    self._conversation_id,
                    operation.job_id,
                    lease_owner=worker,
                    now=self._clock(),
                )
                if released is not None:
                    raise OperationStartQuotaPausedError(released)
            if snapshot.outcome is ProviderJobOutcome.EXPIRED:
                await self._repository.release_operation_start(
                    self._user_id,
                    self._conversation_id,
                    operation.job_id,
                    lease_owner=worker,
                    now=self._clock(),
                )
            raise OperationConflictError("Provider start 结果不确定，保留租约以避免重复启动")

        if snapshot.outcome in {
            ProviderJobOutcome.SUCCEEDED,
            ProviderJobOutcome.FAILED,
            ProviderJobOutcome.TIMEOUT,
            ProviderJobOutcome.EXPIRED,
        }:
            completed_at = self._clock()
            completion = await OperationCompletionCoordinator(
                self._repository,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
            ).record_start_terminal(
                operation.job_id,
                snapshot,
                lease_owner=worker,
                now=completed_at,
            )
            return completion.operation

        started_at = self._clock()
        started = await self._repository.complete_operation_start(
            self._user_id,
            self._conversation_id,
            operation.job_id,
            provider_job_id=snapshot.provider_job_id,
            lease_owner=worker,
            now=started_at,
            next_poll_at=started_at + self._first_poll_delay,
        )
        if started is None:
            raise OperationConflictError("Provider start 结果绑定发生并发冲突")
        return started


class OperationRecoveryRuntime:
    """扫描数据库租约并在重启后继续查询原 provider job。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        resolver: ProviderJobAdapterResolver,
        resumer: WorkflowGraphResumePort,
        worker_id: str,
        quota_resumer: WorkflowGraphQuotaStatePort | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        poll_interval: timedelta = timedelta(seconds=2),
        scan_interval: timedelta = timedelta(seconds=1),
        scan_limit: int = 100,
    ) -> None:
        if not isinstance(resolver, ProviderJobAdapterResolver):
            raise TypeError("resolver 必须实现 ProviderJobAdapterResolver")
        if not isinstance(resumer, WorkflowGraphResumePort):
            raise TypeError("resumer 必须实现 WorkflowGraphResumePort")
        if quota_resumer is not None and not isinstance(
            quota_resumer,
            WorkflowGraphQuotaStatePort,
        ):
            raise TypeError("quota_resumer 必须实现 WorkflowGraphQuotaStatePort")
        if isinstance(scan_limit, bool) or not isinstance(scan_limit, int) or scan_limit < 1 or scan_limit > 1000:
            raise ValueError("scan_limit 必须是 1 到 1000 的整数")
        self._repository = repository
        self._resolver = resolver
        self._resumer = resumer
        self._quota_resumer = quota_resumer
        self._worker_id = _scope_text("worker_id", worker_id, maximum=96)
        self._delivery_worker_id = f"{self._worker_id}:completion"
        self._quota_delivery_worker_id = f"{self._worker_id}:quota"
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_duration = _positive_duration(
            "lease_duration",
            lease_duration,
        )
        self._poll_interval = _positive_duration(
            "poll_interval",
            poll_interval,
        )
        self._scan_interval = _positive_duration(
            "scan_interval",
            scan_interval,
        )
        self._scan_limit = scan_limit
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def quota_resumer(self) -> WorkflowGraphQuotaStatePort | None:
        """只读暴露配额恢复处理器身份，供 Gateway 就绪检查使用。"""

        return self._quota_resumer

    async def run_once(self) -> None:
        """先投递 quota、再投递终态，最后查询到期的原 job。"""

        scan_time = self._clock()
        pending_quota = await self._repository.list_pending_operation_quota_events(
            now=scan_time,
            limit=self._scan_limit,
        )
        for candidate in pending_quota:
            try:
                await self._dispatch_quota(candidate, now=self._clock())
            except Exception as exc:
                self._log_candidate_failure("quota_dispatch", exc)

        pending_completion = await self._repository.list_pending_operation_completions(
            now=self._clock(),
            limit=self._scan_limit,
        )
        for candidate in pending_completion:
            try:
                await self._dispatch_completion(
                    candidate.user_id,
                    candidate.operation,
                    now=self._clock(),
                )
            except Exception as exc:
                self._log_candidate_failure("completion_dispatch", exc)

        due = await self._repository.list_due_operations(
            now=self._clock(),
            limit=self._scan_limit,
        )
        for candidate in due:
            try:
                operation = candidate.operation
                adapter = self._resolver.resolve(operation.stage)
                claim_time = self._clock()
                claimed = await self._repository.claim_operation_lease(
                    candidate.user_id,
                    operation.conversation_id,
                    operation.job_id,
                    lease_owner=self._worker_id,
                    now=claim_time,
                    lease_expires_at=claim_time + self._lease_duration,
                )
                if claimed is None:
                    continue
                await self._poll_claimed(
                    candidate.user_id,
                    claimed,
                    adapter=adapter,
                )
            except Exception as exc:
                self._log_candidate_failure("operation_poll", exc)

    @staticmethod
    def _log_candidate_failure(phase: str, exc: Exception) -> None:
        """只记录固定阶段和异常类型，不泄露供应商或用户内容。"""

        logger.warning(
            "Operation 恢复候选失败：phase=%s error_type=%s",
            phase,
            type(exc).__name__,
        )

    async def _poll_claimed(
        self,
        user_id: str,
        operation: OperationRecord,
        *,
        adapter: ProviderJobAdapter,
    ) -> None:
        provider_job_id = operation.provider_job_id
        if provider_job_id is None:
            raise OperationConflictError("已领取 Operation 缺少 provider job ID")
        try:
            snapshot = await adapter.status(
                provider_job_id,
                user_id=user_id,
                conversation_id=operation.conversation_id,
            )
        except (ProviderJobCallError, ProviderJobMappingError):
            observed_at = self._clock()
            scheduled = await self._repository.schedule_operation_poll(
                user_id,
                operation.conversation_id,
                operation.job_id,
                lease_owner=self._worker_id,
                now=observed_at,
                next_poll_at=observed_at + self._poll_interval,
            )
            if scheduled is None:
                raise OperationConflictError("Provider 查询失败后的轮询租约已失效") from None
            return

        observed_at = self._clock()
        if snapshot.outcome is ProviderJobOutcome.POLLING:
            scheduled = await self._repository.schedule_operation_poll(
                user_id,
                operation.conversation_id,
                operation.job_id,
                lease_owner=self._worker_id,
                now=observed_at,
                next_poll_at=observed_at + self._poll_interval,
            )
            if scheduled is None:
                raise OperationConflictError("Operation 下一轮轮询计划发生冲突")
            return
        if snapshot.outcome is ProviderJobOutcome.PAUSED_QUOTA:
            transition = await OperationQuotaCoordinator(
                self._repository,
                user_id=user_id,
                conversation_id=operation.conversation_id,
            ).record_pause(
                operation.job_id,
                lease_owner=self._worker_id,
                now=observed_at,
            )
            try:
                await self._dispatch_quota(
                    OwnedOperationQuotaEvent(
                        user_id=user_id,
                        operation=transition.operation,
                        event=transition.event,
                    ),
                    now=self._clock(),
                )
            except Exception as exc:
                self._log_candidate_failure("quota_dispatch", exc)
            return

        completion = await OperationCompletionCoordinator(
            self._repository,
            user_id=user_id,
            conversation_id=operation.conversation_id,
        ).record_terminal(
            operation.job_id,
            snapshot,
            lease_owner=self._worker_id,
            now=observed_at,
        )
        await self._dispatch_completion(
            user_id,
            completion.operation,
            now=self._clock(),
        )

    async def _dispatch_completion(
        self,
        user_id: str,
        operation: OperationRecord,
        *,
        now: datetime,
    ) -> None:
        await OperationCompletionDispatcher(
            self._repository,
            resumer=self._resumer,
            user_id=user_id,
            conversation_id=operation.conversation_id,
            clock=self._clock,
        ).dispatch(
            operation.job_id,
            lease_owner=self._delivery_worker_id,
            now=now,
            lease_expires_at=now + self._lease_duration,
        )

    async def _dispatch_quota(
        self,
        candidate: OwnedOperationQuotaEvent,
        *,
        now: datetime,
    ) -> None:
        if self._quota_resumer is None:
            raise OperationConflictError("quota_resumer 未装配")
        await OperationQuotaDispatcher(
            self._repository,
            quota_resumer=self._quota_resumer,
            clock=self._clock,
        ).dispatch(
            candidate,
            lease_owner=self._quota_delivery_worker_id,
            now=now,
            lease_expires_at=now + self._lease_duration,
        )

    async def recover_manually(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ) -> OperationManualRecoveryResult:
        """已暂停 Operation 只允许授权处理器恢复；终态要求新 attempt。"""

        owner = _scope_text("user_id", user_id)
        conversation = _scope_text("conversation_id", conversation_id)
        operation_id = _scope_text("job_id", job_id)
        operation = await self._repository.get_operation(owner, operation_id)
        if operation is None or operation.conversation_id != conversation:
            raise OperationConflictError("Operation 不存在或不属于当前会话")
        if operation.status is ExternalJobStatus.POLLING and operation.provider_job_id is not None and operation.next_poll_at is None:
            raise OperationConflictError("quota_resume_requires_authorized_handler")
        if operation.status in {
            ExternalJobStatus.SUCCEEDED,
            ExternalJobStatus.FAILED,
            ExternalJobStatus.TIMEOUT,
            ExternalJobStatus.EXPIRED,
        }:
            return OperationManualRecoveryResult(
                action=OperationManualRecoveryAction.NEW_ATTEMPT_REQUIRED,
                operation=operation,
            )
        raise OperationConflictError("当前 Operation 不允许人工恢复")

    async def authorize_quota_resume(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workflow_id: str,
        job_id: str,
        expected_revision: int,
    ) -> OperationRecord:
        """用当前V2用户动作恢复原Provider job，并立即投递同一resume事件。"""

        if self._quota_resumer is None:
            raise OperationConflictError("quota_resumer 未装配")
        now = self._clock()
        authorized = await OperationQuotaCoordinator(
            self._repository,
            user_id=user_id,
            conversation_id=conversation_id,
        ).authorize_resume(
            job_id,
            workflow_id=workflow_id,
            expected_revision=expected_revision,
            delivery_lease_owner=self._quota_delivery_worker_id,
            now=now,
            delivery_lease_expires_at=now + self._lease_duration,
        )
        await self._dispatch_quota(
            OwnedOperationQuotaEvent(
                user_id=user_id,
                operation=authorized.operation,
                event=authorized.claim.event,
            ),
            now=self._clock(),
        )
        return authorized.operation

    async def start(self) -> None:
        """启动单个进程级扫描任务；重复调用不会创建第二个任务。"""

        if self._closed:
            raise RuntimeError("OperationRecoveryRuntime 已关闭")
        if self._task is None:
            self._task = asyncio.create_task(
                self._run_loop(),
                name=f"operation-recovery:{self._worker_id}",
            )
            await asyncio.sleep(0)

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                self._log_candidate_failure("runtime_scan", exc)
            await asyncio.sleep(self._scan_interval.total_seconds())

    async def aclose(self) -> None:
        """取消进程内扫描；正在执行的数据库租约由过期机制接管。"""

        if self._closed:
            return
        self._closed = True
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


__all__ = [
    "MappingProviderJobAdapterResolver",
    "OperationManualRecoveryAction",
    "OperationManualRecoveryResult",
    "OperationRecoveryRuntime",
    "OperationStartCoordinator",
    "OperationStartQuotaPausedError",
    "ProviderJobAdapterResolver",
]
