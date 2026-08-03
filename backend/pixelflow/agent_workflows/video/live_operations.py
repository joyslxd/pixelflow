"""连接视频 live Workflow 与 M06 External Job Coordinator。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ConfigDict, Field, JsonValue, model_validator

from pixelflow.agent_runtime.contracts import ExternalJobRef, OperationRequest
from pixelflow.agent_runtime.contracts.base import ContractModel
from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRepository,
    OperationRecord,
)
from pixelflow.agent_runtime.ports import OperationConflictError

from .live_capabilities import (
    TransientTurnCredential,
    _borrow_authorization_for_operation_boundary,
    _consume_authorization_for_quota_resume_boundary,
    _is_sensitive_protocol_key,
    _normalize_protocol_key,
)

if TYPE_CHECKING:
    from pixelflow.agent_runtime.contracts import AgentEvent, ExternalJobStatus
    from pixelflow.agent_runtime.graph import GraphExecutionNamespace
    from pixelflow.agent_runtime.jobs.completion import WorkflowGraphResumePort
    from pixelflow.agent_runtime.jobs.providers import (
        ProviderJobAdapter,
        ProviderJobOutcome,
    )
    from pixelflow.agent_runtime.jobs.quota import (
        OperationQuotaAuthorizedResume,
        WorkflowGraphQuotaStatePort,
    )
    from pixelflow.agent_runtime.jobs.recovery import OperationRecoveryRuntime
    from pixelflow.agent_runtime.persistence import VideoRuntimeRepository
    from pixelflow.agent_runtime.persistence.repositories import EventDeliveryClaim


class VideoOperationStartRequest(ContractModel):
    """保存 Operation 身份与规范供应商请求，不允许携带临时鉴权。"""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    operation_request: OperationRequest
    provider_request: dict[str, JsonValue] = Field(repr=False)

    @model_validator(mode="before")
    @classmethod
    def validate_provider_request_credentials(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _validate_provider_request_boundary(
                operation_request=value.get("operation_request"),
                provider_request=value.get("provider_request"),
            )
        return value

    @model_validator(mode="after")
    def validate_request_digest(self):
        if _hash_operation_request(self.provider_request) != self.operation_request.request_hash:
            raise ValueError("Provider 请求与 Operation request_hash 不一致")
        return self

    @property
    def stage(self) -> str:
        """从唯一 Operation 身份返回待解析 stage。"""

        return self.operation_request.stage


class TransientCredentialVault:
    """用进程内加锁映射按 Turn 暂存不可序列化凭据。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._credentials: dict[str, TransientTurnCredential] = {}

    def put(self, turn_id: str, credential: TransientTurnCredential) -> None:
        key = _require_text("turn_id", turn_id, maximum=128)
        if not isinstance(credential, TransientTurnCredential):
            raise TypeError("credential 必须是 TransientTurnCredential")
        with self._lock:
            previous = self._credentials.get(key)
            self._credentials[key] = credential
        if previous is not None and previous is not credential:
            previous.discard()

    def get(self, turn_id: str) -> TransientTurnCredential | None:
        key = _require_text("turn_id", turn_id, maximum=128)
        with self._lock:
            return self._credentials.get(key)

    def pop(self, turn_id: str) -> None:
        key = _require_text("turn_id", turn_id, maximum=128)
        with self._lock:
            credential = self._credentials.pop(key, None)
        if credential is not None:
            credential.discard()

    def clear(self) -> None:
        with self._lock:
            credentials = tuple(self._credentials.values())
            self._credentials.clear()
        for credential in credentials:
            credential.discard()

    def __repr__(self) -> str:
        return "TransientCredentialVault()"


class VideoOperationAdapterResolver:
    """把动态分镜 stage 路由到明确注册的有限 Provider 能力。"""

    def __init__(self, adapters: Mapping[str, ProviderJobAdapter]) -> None:
        from pixelflow.agent_runtime.jobs import MappingProviderJobAdapterResolver

        self._resolver = MappingProviderJobAdapterResolver(adapters)

    def resolve(self, stage: str) -> ProviderJobAdapter:
        lookup_stage = stage
        if isinstance(stage, str) and stage.startswith("generate_scene_video:"):
            scene_id = stage.removeprefix("generate_scene_video:")
            if not scene_id or scene_id != scene_id.strip():
                raise OperationConflictError("Operation stage 未配置 Provider Job Adapter")
            lookup_stage = "generate_scene_video"
        return self._resolver.resolve(lookup_stage)


@dataclass(frozen=True, slots=True)
class _OwnedOperationEventClaim:
    """把 Operation 事件租约与原用户、会话和 job 身份绑定。"""

    user_id: str
    conversation_id: str
    job_id: str
    claim: EventDeliveryClaim


class _OperationEventClaimRegistry:
    """暂存 completion/quota Dispatcher 已领取的不可变 claim。"""

    def __init__(self, *, maximum: int = 1000) -> None:
        self._lock = RLock()
        self._maximum = maximum
        self._claims: dict[str, _OwnedOperationEventClaim] = {}

    def remember(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
        claim: EventDeliveryClaim,
        now: datetime,
    ) -> None:
        from pixelflow.agent_runtime.jobs.quota import _freeze_claim
        from pixelflow.agent_runtime.persistence.repositories import (
            EventDeliveryClaim,
        )

        normalized = _freeze_claim(
            EventDeliveryClaim.model_validate(claim.model_dump(mode="python")),
        )
        entry = _OwnedOperationEventClaim(
            user_id=_require_text("user_id", user_id, maximum=64),
            conversation_id=_require_text(
                "conversation_id",
                conversation_id,
                maximum=64,
            ),
            job_id=_require_text("job_id", job_id, maximum=64),
            claim=normalized,
        )
        event_id = normalized.event.event_id
        with self._lock:
            self._purge_expired(now)
            existing = self._claims.get(event_id)
            if existing is not None and existing != entry:
                if existing.claim.lease_expires_at > now:
                    raise OperationConflictError("Operation 事件仍绑定其他有效投递租约")
                self._claims.pop(event_id, None)
            if event_id not in self._claims and len(self._claims) >= self._maximum:
                raise OperationConflictError("Operation 事件临时 claim 已达到安全上限")
            self._claims[event_id] = entry

    def require(
        self,
        completion_event: AgentEvent,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> _OwnedOperationEventClaim:
        event_id = _require_text("idempotency_key", idempotency_key, maximum=64)
        if completion_event.event_id != event_id:
            raise OperationConflictError("Operation 事件与 Graph 幂等键不一致")
        with self._lock:
            entry = self._claims.get(event_id)
            if entry is None:
                raise OperationConflictError("Operation 事件缺少 Dispatcher 原始 claim")
            if entry.claim.event != completion_event:
                raise OperationConflictError("Operation 事件与暂存 claim 不一致")
            if now >= entry.claim.lease_expires_at:
                self._claims.pop(event_id, None)
                raise OperationConflictError("Operation 事件投递租约已过期")
            from pixelflow.agent_runtime.jobs.quota import _freeze_claim

            return _OwnedOperationEventClaim(
                user_id=entry.user_id,
                conversation_id=entry.conversation_id,
                job_id=entry.job_id,
                claim=_freeze_claim(entry.claim),
            )

    def release_published(
        self,
        event_id: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        identity = _require_text("event_id", event_id, maximum=64)
        with self._lock:
            entry = self._claims.get(identity)
            if entry is None:
                return
            if lease_owner is not None and entry.claim.lease_owner != lease_owner:
                raise OperationConflictError("Operation 事件清理租约 owner 不一致")
            self._claims.pop(identity, None)

    def _purge_expired(self, now: datetime) -> None:
        expired = [event_id for event_id, entry in self._claims.items() if entry.claim.lease_expires_at <= now]
        for event_id in expired:
            self._claims.pop(event_id, None)


class _OperationEventClaimRepositoryProxy:
    """透明转发 M06 Repository，并截获 completion/quota claim。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        registry: _OperationEventClaimRegistry,
    ) -> None:
        self._repository = repository
        self._registry = registry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    async def claim_operation_completion_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        claim = await self._repository.claim_operation_completion_event(
            user_id,
            conversation_id,
            event_id,
            job_id,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=lease_expires_at,
        )
        if claim is not None:
            self._registry.remember(
                user_id=user_id,
                conversation_id=conversation_id,
                job_id=job_id,
                claim=claim,
                now=now,
            )
        return claim

    async def claim_operation_quota_event(
        self,
        user_id: str,
        conversation_id: str,
        event_id: str,
        job_id: str,
        *,
        quota_pause_revision: int,
        quota_state: str,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> EventDeliveryClaim | None:
        """领取 quota Event，并把同一 claim 交给 Graph Handler。"""

        claim = await self._repository.claim_operation_quota_event(
            user_id,
            conversation_id,
            event_id,
            job_id,
            quota_pause_revision=quota_pause_revision,
            quota_state=quota_state,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=lease_expires_at,
        )
        if claim is not None:
            self._registry.remember(
                user_id=user_id,
                conversation_id=conversation_id,
                job_id=job_id,
                claim=claim,
                now=now,
            )
        return claim

    async def complete_event_delivery(
        self,
        user_id: str,
        event_id: str,
        *,
        lease_owner: str,
        published_at: datetime,
    ) -> AgentEvent | None:
        event = await self._repository.complete_event_delivery(
            user_id,
            event_id,
            lease_owner=lease_owner,
            published_at=published_at,
        )
        if event is not None:
            self._registry.release_published(
                event_id,
                lease_owner=lease_owner,
            )
        return event


class VideoLiveOperationBridge:
    """把 live 付费 start 委托给 M06 单次启动与租约 Service。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        resolver: VideoOperationAdapterResolver,
        lease_owner: str,
        clock: Callable[[], datetime] | Any | None = None,
        job_id_factory: Callable[[], str] | None = None,
        quota_lease_duration: timedelta = timedelta(seconds=30),
    ) -> None:
        if not isinstance(resolver, VideoOperationAdapterResolver):
            raise TypeError("resolver 必须是 VideoOperationAdapterResolver")
        self._repository = repository
        self._resolver = resolver
        self._lease_owner = _require_text("lease_owner", lease_owner, maximum=128)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory
        if quota_lease_duration <= timedelta(0):
            raise ValueError("quota_lease_duration 必须大于零")
        self._quota_lease_duration = quota_lease_duration
        self._operation_event_claims = _OperationEventClaimRegistry()
        self._recovery_repository = _OperationEventClaimRepositoryProxy(
            repository,
            self._operation_event_claims,
        )

    def bind(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> _ScopedVideoOperationPort:
        """为一个用户和对话创建不保存凭据的 M11 OperationPort 视图。"""

        return _ScopedVideoOperationPort(
            self,
            user_id=_require_text("user_id", user_id, maximum=64),
            conversation_id=_require_text(
                "conversation_id",
                conversation_id,
                maximum=64,
            ),
        )

    def start_request_from_claim(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job: ExternalJobRef,
        stage_version: int,
        provider_request: Mapping[str, JsonValue],
    ) -> VideoOperationStartRequest:
        """把 M11 外部任务引用恢复成 M06 start 所需的规范请求。"""

        operation_request = _canonical_m06_request(
            OperationRequest(
                workflow_id=job.workflow_id,
                stage=job.stage,
                stage_version=stage_version,
                attempt=job.attempt,
                request_hash=job.idempotency_key,
                idempotency_key=job.idempotency_key,
            ),
            provider_request=provider_request,
            expected_request_hash=job.idempotency_key,
        )
        return VideoOperationStartRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            operation_request=operation_request,
            provider_request=dict(provider_request),
        )

    async def start(
        self,
        request: VideoOperationStartRequest,
        *,
        credential: TransientTurnCredential,
    ) -> OperationRecord:
        """只在 M06 start 调用栈中持有原始 Authorization。"""

        normalized = VideoOperationStartRequest.model_validate(request.model_dump(mode="python"))
        adapter = self._resolver.resolve(normalized.stage)
        authorization = _borrow_authorization_for_operation_boundary(credential)
        from pixelflow.agent_runtime.jobs.recovery import OperationStartCoordinator

        coordinator = OperationStartCoordinator(
            self._repository,
            adapter=adapter,
            user_id=normalized.user_id,
            conversation_id=normalized.conversation_id,
            clock=self._now,
            job_id_factory=self._job_id_factory,
        )
        try:
            return await coordinator.start(
                normalized.operation_request,
                provider_request=normalized.provider_request,
                authorization=authorization,
                lease_owner=self._lease_owner,
            )
        finally:
            authorization = ""

    async def resume_paused_operation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        workflow_id: str,
        job_id: str,
        expected_revision: int,
        resume_request_key: str,
        credential: TransientTurnCredential,
    ) -> OperationQuotaAuthorizedResume:
        """消费当前凭据并原子恢复原 Provider job，不重新执行 start。"""

        from pixelflow.agent_runtime.jobs import OperationQuotaCoordinator

        authorization = _consume_authorization_for_quota_resume_boundary(credential)
        try:
            if not authorization.strip():
                raise OperationConflictError("quota_resume_authorization_required")
            if not isinstance(resume_request_key, str) or not resume_request_key.strip():
                raise OperationConflictError("quota_resume_request_key_required")
            claim_time = self._now()
            request_digest = hashlib.sha256(
                resume_request_key.strip().encode()
            ).hexdigest()[:16]
            authorized = await OperationQuotaCoordinator(
                self._repository,
                user_id=user_id,
                conversation_id=conversation_id,
            ).authorize_resume(
                job_id,
                workflow_id=workflow_id,
                expected_revision=expected_revision,
                delivery_lease_owner=(
                    f"{self._lease_owner}:quota-resume:{request_digest}"
                ),
                now=claim_time,
                delivery_lease_expires_at=(
                    claim_time + self._quota_lease_duration
                ),
            )
            self.remember_quota_resume_claim(
                user_id=user_id,
                conversation_id=conversation_id,
                job_id=job_id,
                claim=authorized.claim,
            )
            return authorized
        finally:
            authorization = ""

    async def _claim(
        self,
        user_id: str,
        conversation_id: str,
        request: OperationRequest,
    ) -> ExternalJobRef:
        from pixelflow.agent_runtime.jobs.coordinator import OperationCoordinator

        self._resolver.resolve(request.stage)
        normalized = _canonical_m06_request(request)
        operation = await OperationCoordinator(
            self._repository,
            user_id=user_id,
            conversation_id=conversation_id,
            now=self._now,
            job_id_factory=self._job_id_factory,
        ).claim(normalized)
        return _external_job_ref(operation)

    async def _get(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ) -> ExternalJobRef | None:
        operation = await self._repository.get_operation(user_id, job_id)
        if operation is None:
            return None
        if operation.conversation_id != conversation_id:
            return None
        return _external_job_ref(operation)

    async def _save(
        self,
        user_id: str,
        conversation_id: str,
        job: ExternalJobRef,
    ) -> ExternalJobRef:
        existing = await self._get(user_id, conversation_id, job.job_id)
        if existing is None or existing.model_dump(mode="json") != job.model_dump(mode="json"):
            raise OperationConflictError("M11 Operation 只能回读 M06 权威状态")
        return existing

    async def _get_video_terminal_claim(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ):
        """从 Task4 已提交的视频信封恢复可信 M11 后处理终态。"""

        from pixelflow.agent_workflows.video.delivery import (
            VideoDeliveryWorkflowState,
        )
        from pixelflow.agent_workflows.video.postproduction import (
            VideoOperationTerminalClaim,
            VideoPostProductionWorkflowState,
        )
        from pixelflow.agent_workflows.video.state_codec import (
            decode_video_workflow_state,
        )

        operation = await self.get_operation(
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
        )
        state_reader = getattr(self._repository, "get_video_state", None)
        if operation is None or not callable(state_reader):
            return None
        envelope = await state_reader(user_id, operation.workflow_id)
        if envelope is None or envelope.conversation_id != conversation_id:
            return None
        state = decode_video_workflow_state(envelope)
        if isinstance(state, VideoDeliveryWorkflowState):
            for record in state.jianying_draft_records.values():
                terminal = record.get("terminal_claim")
                if not isinstance(terminal, Mapping):
                    continue
                job = ExternalJobRef.model_validate(terminal.get("job"))
                if job.job_id != job_id:
                    continue
                payload = terminal.get("payload")
                stage_version = terminal.get("stage_version")
                if not isinstance(payload, Mapping) or not isinstance(
                    stage_version,
                    int,
                ):
                    raise OperationConflictError("剪映草稿持久终态格式不受支持")
                return VideoOperationTerminalClaim(
                    job=job,
                    result_hash=_require_text(
                        "result_hash",
                        terminal.get("result_hash"),
                        maximum=64,
                    ),
                    result_type=_require_text(
                        "result_type",
                        terminal.get("result_type"),
                        maximum=64,
                    ),
                    payload=dict(payload),
                    stage_version=stage_version,
                )
            postproduction = state.postproduction_state
        elif isinstance(state, VideoPostProductionWorkflowState):
            postproduction = state
        else:
            return None
        for entry in postproduction.terminal_claims:
            job = ExternalJobRef.model_validate(entry.get("job"))
            if job.job_id != job_id:
                continue
            return VideoOperationTerminalClaim(
                job=job,
                result_hash=_require_text(
                    "result_hash",
                    entry.get("result_hash"),
                    maximum=64,
                ),
                result_type=_require_text(
                    "result_type",
                    entry.get("result_type"),
                    maximum=64,
                ),
                payload=dict(entry.get("payload") or {}),
                stage_version=entry.get("stage_version"),
            )
        return None

    async def _get_scene_terminal_claim(
        self,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ):
        """从 Task4 已提交的视频信封恢复可信 M11 分镜终态。"""

        from pixelflow.agent_workflows.video.delivery import (
            VideoDeliveryWorkflowState,
        )
        from pixelflow.agent_workflows.video.postproduction import (
            VideoPostProductionWorkflowState,
        )
        from pixelflow.agent_workflows.video.state_codec import (
            decode_video_workflow_state,
        )
        from pixelflow.agent_workflows.video.video_generation import (
            VideoSceneGenerationWorkflowState,
            VideoSceneOperationTerminalClaim,
        )

        operation = await self.get_operation(
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
        )
        state_reader = getattr(self._repository, "get_video_state", None)
        if operation is None or not callable(state_reader):
            return None
        envelope = await state_reader(user_id, operation.workflow_id)
        if envelope is None or envelope.conversation_id != conversation_id:
            return None
        state = decode_video_workflow_state(envelope)
        if isinstance(state, VideoDeliveryWorkflowState):
            generation = state.postproduction_state.generation_state
        elif isinstance(state, VideoPostProductionWorkflowState):
            generation = state.generation_state
        elif isinstance(state, VideoSceneGenerationWorkflowState):
            generation = state
        else:
            return None
        for entry in generation.terminal_claims:
            job = ExternalJobRef.model_validate(entry.get("job"))
            if job.job_id != job_id:
                continue
            return VideoSceneOperationTerminalClaim(
                job=job,
                result_hash=_require_text(
                    "result_hash",
                    entry.get("result_hash"),
                    maximum=64,
                ),
            )
        return None

    async def safe_persistence_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> dict[str, JsonValue]:
        """导出仅含 M06 Operation 持久化字段的测试与诊断快照。"""

        owner = _require_text("user_id", user_id, maximum=64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            maximum=64,
        )
        operations = await self._repository.list_operations(owner, conversation)
        return {
            "operations": [item.model_dump(mode="json") for item in operations],
        }

    async def get_operation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
    ) -> OperationRecord | None:
        """按用户和对话边界回读 M06 Operation，拒绝跨会话暴露。"""

        owner = _require_text("user_id", user_id, maximum=64)
        conversation = _require_text(
            "conversation_id",
            conversation_id,
            maximum=64,
        )
        operation_id = _require_text("job_id", job_id, maximum=64)
        operation = await self._repository.get_operation(owner, operation_id)
        if operation is None or operation.conversation_id != conversation:
            return None
        return operation

    def build_recovery_runtime(
        self,
        *,
        resumer: WorkflowGraphResumePort,
        worker_id: str,
        quota_resumer: WorkflowGraphQuotaStatePort | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        poll_interval: timedelta = timedelta(seconds=2),
        scan_interval: timedelta = timedelta(seconds=1),
        scan_limit: int = 100,
    ) -> OperationRecoveryRuntime:
        """复用同一 Repository 与 stage 路由装配可关闭的 M06 恢复 Runtime。"""

        from pixelflow.agent_runtime.jobs.recovery import OperationRecoveryRuntime

        return OperationRecoveryRuntime(
            self._recovery_repository,
            resolver=self._resolver,
            resumer=resumer,
            worker_id=worker_id,
            quota_resumer=quota_resumer,
            clock=self._now,
            lease_duration=lease_duration,
            poll_interval=poll_interval,
            scan_interval=scan_interval,
            scan_limit=scan_limit,
        )

    def _require_completion_claim(
        self,
        completion_event: AgentEvent,
        *,
        idempotency_key: str,
    ) -> _OwnedOperationEventClaim:
        return self._operation_event_claims.require(
            completion_event,
            idempotency_key=idempotency_key,
            now=self._now(),
        )

    def remember_quota_resume_claim(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
        claim: EventDeliveryClaim,
    ) -> None:
        """登记当前授权事务已先领取的 resume Event claim。"""

        self._operation_event_claims.remember(
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
            claim=claim,
            now=self._now(),
        )

    def _require_quota_claim(
        self,
        quota_event: AgentEvent,
        *,
        idempotency_key: str,
    ) -> _OwnedOperationEventClaim:
        return self._operation_event_claims.require(
            quota_event,
            idempotency_key=idempotency_key,
            now=self._now(),
        )

    def _release_published_completion(
        self,
        completion: _OwnedOperationEventClaim,
    ) -> None:
        self._operation_event_claims.release_published(
            completion.claim.event.event_id,
            lease_owner=completion.claim.lease_owner,
        )

    def _release_published_quota(
        self,
        quota: _OwnedOperationEventClaim,
    ) -> None:
        self._operation_event_claims.release_published(
            quota.claim.event.event_id,
            lease_owner=quota.claim.lease_owner,
        )

    def _now(self) -> datetime:
        value = self._clock.now() if hasattr(self._clock, "now") else self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock 必须返回 datetime")
        return value


class _ScopedVideoOperationPort:
    """把无作用域的 M11 Port 调用绑定到当前用户和对话。"""

    def __init__(
        self,
        bridge: VideoLiveOperationBridge,
        *,
        user_id: str,
        conversation_id: str,
        completion: _OwnedOperationEventClaim | None = None,
    ) -> None:
        self._bridge = bridge
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._completion = completion

    async def claim(self, request: OperationRequest) -> ExternalJobRef:
        return await self._bridge._claim(
            self._user_id,
            self._conversation_id,
            request,
        )

    async def get(self, job_id: str) -> ExternalJobRef | None:
        return await self._bridge._get(
            self._user_id,
            self._conversation_id,
            job_id,
        )

    async def save(self, job: ExternalJobRef) -> ExternalJobRef:
        return await self._bridge._save(
            self._user_id,
            self._conversation_id,
            job,
        )

    async def finalize_scene_operation(
        self,
        *,
        expected: ExternalJobRef,
        target_status: ExternalJobStatus,
        provider_job_id: str | None,
        result_hash: str,
    ):
        """把 M11 终态摘要绑定到当前 Dispatcher claim，不另写 M06。"""

        from pixelflow.agent_runtime.contracts import ExternalJobStatus
        from pixelflow.agent_workflows.video.video_generation import (
            VideoSceneOperationTerminalClaim,
        )

        current = await self.get(expected.job_id)
        completion = self._completion
        if current is None or completion is None:
            raise OperationConflictError("视频终态缺少当前完成事件 claim")
        payload = completion.claim.event.payload
        if completion.user_id != self._user_id or completion.conversation_id != self._conversation_id or completion.job_id != current.job_id or payload.get("job_id") != current.job_id or payload.get("status") != current.status.value:
            raise OperationConflictError("视频终态与完成事件身份不一致")
        domain_job = current
        if target_status is ExternalJobStatus.FAILED and current.status in {
            ExternalJobStatus.TIMEOUT,
            ExternalJobStatus.EXPIRED,
        }:
            domain_job = current.model_copy(update={"status": ExternalJobStatus.FAILED})
        elif current.status is not target_status:
            raise OperationConflictError("视频领域终态与 M06 Operation 终态不一致")
        if provider_job_id is not None and current.provider_job_id != provider_job_id:
            raise OperationConflictError("视频终态绑定了其他 Provider Job")
        return VideoSceneOperationTerminalClaim(
            job=domain_job,
            result_hash=_require_text("result_hash", result_hash, maximum=64),
        )

    async def finalize_video_operation(
        self,
        *,
        expected: ExternalJobRef,
        target_status: ExternalJobStatus,
        provider_job_id: str | None,
        result_hash: str,
        result_type: str,
        payload: Mapping[str, Any],
        stage_version: int,
    ):
        """在同一完成事件 claim 上构造 M11 后处理终态。"""

        from pixelflow.agent_workflows.video.postproduction import (
            VideoOperationTerminalClaim,
        )

        scene_claim = await self.finalize_scene_operation(
            expected=expected,
            target_status=target_status,
            provider_job_id=provider_job_id,
            result_hash=result_hash,
        )
        return VideoOperationTerminalClaim(
            job=scene_claim.job,
            result_hash=scene_claim.result_hash,
            result_type=_require_text(
                "result_type",
                result_type,
                maximum=64,
            ),
            payload=dict(payload),
            stage_version=stage_version,
        )

    async def get_scene_operation_terminal_claim(self, *, job_id: str):
        """从 Task4 权威信封回读已提交的分镜终态。"""

        return await self._bridge._get_scene_terminal_claim(
            self._user_id,
            self._conversation_id,
            job_id,
        )

    async def get_video_operation_terminal_claim(self, *, job_id: str):
        """从 Task4 权威信封回读已提交的后处理终态。"""

        return await self._bridge._get_video_terminal_claim(
            self._user_id,
            self._conversation_id,
            job_id,
        )

    async def claim_video_operation_start(
        self,
        *,
        expected: ExternalJobRef,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ):
        """拒绝旧 direct-skill 两阶段入口，付费 start 统一走 M06 Coordinator。"""

        del owner, now, lease_seconds
        from pixelflow.agent_workflows.video.postproduction import (
            VideoOperationStartClaim,
        )

        current = await self.get(expected.job_id)
        if current is None:
            raise OperationConflictError("视频 Operation 不存在或不属于当前会话")
        return VideoOperationStartClaim(job=current, acquired=False)

    async def mark_video_operation_call_started(
        self,
        *,
        expected: ExternalJobRef,
        owner: str,
        now: datetime,
    ) -> ExternalJobRef:
        """禁止绕过 M06 OperationStartCoordinator 标记供应商调用。"""

        del expected, owner, now
        raise OperationConflictError("live 视频付费调用必须通过 M06 start 协调器")


class ExternalJobStateObserver(Protocol):
    """接收经过 M06 或 Workflow 权威边界确认的有限外部任务状态。"""

    def observe_external_job_state(self, state: ProviderJobOutcome) -> None:
        """记录一个有限六态观察值。"""


class OperationCompletionCheckpointGraph(Protocol):
    """约束 Operation 完成桥写入并校验 Supervisor checkpoint 的最小接口。"""

    async def aget_state(self, config: dict[str, Any]) -> Any: ...

    async def aupdate_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any],
        *,
        as_node: str,
    ) -> Any: ...

    async def ainvoke(self, input: Any, config: dict[str, Any]) -> Any: ...


class VideoOperationQuotaStateHandler:
    """把 quota Outbox 投影到独立 Graph checkpoint 与原视频 Turn。"""

    def __init__(
        self,
        *,
        repository: VideoRuntimeRepository,
        operations: VideoLiveOperationBridge,
        clock: Any,
        graph: OperationCompletionCheckpointGraph,
    ) -> None:
        self._repository = repository
        self._operations = operations
        self._clock = clock
        self._graph = graph

    async def resume_external_job_quota(
        self,
        namespace: GraphExecutionNamespace,
        *,
        quota_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        """只消费 Dispatcher 原 claim，并以同一 Event 投影 pause/resume。"""

        from pixelflow.agent_runtime.graph import workflow_namespace
        from pixelflow.agent_runtime.jobs import OperationQuotaEventPayload
        from pixelflow.agent_workflows.video.live_quota import (
            VideoOperationQuotaProjectionService,
            strict_quota_agent_event,
        )

        event = strict_quota_agent_event(quota_event)
        quota = self._operations._require_quota_claim(
            event,
            idempotency_key=idempotency_key,
        )
        payload = OperationQuotaEventPayload.model_validate(event.payload)
        if namespace != workflow_namespace(
            quota.conversation_id,
            payload.workflow_id,
        ):
            raise OperationConflictError("quota Event Graph namespace 不一致")
        operation = await self._repository.get_operation(
            quota.user_id,
            quota.job_id,
        )
        if operation is None or operation.conversation_id != quota.conversation_id:
            raise OperationConflictError("quota Event 对应 Operation 不存在")
        envelope = await self._repository.get_video_state(
            quota.user_id,
            payload.workflow_id,
        )
        if envelope is None:
            raise OperationConflictError("quota Event 对应视频权威状态不存在")
        projection = VideoOperationQuotaProjectionService().build(
            user_id=quota.user_id,
            envelope=envelope,
            operation=operation,
            quota_event=event,
        )
        if projection.open_interrupt is not None:
            snapshot = await self._repository.export_safe_snapshot(
                quota.user_id,
                quota.conversation_id,
            )
            blockers = [
                item
                for item in snapshot.interrupts
                if item.status != "closed"
                and item.interrupt_id
                != projection.open_interrupt.interrupt_id
            ]
            if blockers:
                raise OperationConflictError(
                    "quota pause 等待当前授权中断关闭",
                )
        await self._checkpoint_quota_projection(
            event=event,
            quota_state=payload.quota_state.value,
            user_id=quota.user_id,
            projection=projection,
        )
        await self._repository.commit_operation_quota_state(
            quota.claim,
            user_id=quota.user_id,
            workflow_state=projection.workflow_state,
            workflow=projection.workflow,
            expected_workflow_version=envelope.workflow_version,
            open_interrupt=projection.open_interrupt,
            close_interrupt_revision=projection.close_interrupt_revision,
            occurred_at=self._now(),
        )
        self._operations._release_published_quota(quota)

    async def _checkpoint_quota_projection(
        self,
        *,
        event: AgentEvent,
        quota_state: str,
        user_id: str,
        projection: Any,
    ) -> None:
        """幂等建立 quota 独立 checkpoint，拒绝同线程出现第二种投影。"""

        from pixelflow.agent_runtime.contracts import TurnStatus
        from pixelflow.agent_runtime.graph import GraphExecutionNamespace
        from pixelflow.agent_runtime.graph.composition import (
            DISPATCH_WORKFLOW_NODE,
            OPERATION_COMPLETION_INTERRUPT_NODE,
            WORKFLOW_INTERRUPT_NODE,
        )
        from pixelflow.agent_workflows.video.live_handler import (
            WorkflowDispatchResult,
        )
        from pixelflow.agent_workflows.video.live_quota import (
            quota_checkpoint_thread_id,
        )

        is_pause = quota_state == "paused"
        desired = WorkflowDispatchResult(
            state=projection.workflow_state,
            workflow=projection.workflow,
            interrupt=projection.open_interrupt,
            turn_status=(
                TurnStatus.WAITING_USER
                if is_pause
                else TurnStatus.COMPLETED
            ),
        )
        graph_namespace = GraphExecutionNamespace(
            thread_id=quota_checkpoint_thread_id(
                event_id=event.event_id,
                workflow_version=projection.workflow_state.workflow_version,
                paused=is_pause,
            ),
            checkpoint_ns="",
        )
        config = graph_namespace.as_runnable_config()
        snapshot = await self._graph.aget_state(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        interrupts = tuple(getattr(snapshot, "interrupts", ()) or ())
        raw_dispatch = values.get("workflow_dispatch_result")
        stored_dispatch = None
        if raw_dispatch is not None:
            try:
                stored_dispatch = WorkflowDispatchResult.model_validate(
                    raw_dispatch
                )
            except Exception:
                stored_dispatch = None

        if stored_dispatch is not None:
            if (
                stored_dispatch != desired
                or values.get("conversation_id")
                != projection.workflow_state.conversation_id
                or values.get("user_id") != user_id
                or values.get("turn_id")
                != projection.workflow_state.last_turn_id
            ):
                raise OperationConflictError("quota checkpoint 投影内容冲突")
            if is_pause and _checkpoint_has_exact_quota_interrupt(
                snapshot,
                projection.open_interrupt,
            ):
                return
            if not is_pause:
                if next_nodes or interrupts:
                    raise OperationConflictError("quota resume checkpoint 不是终止态")
                return
            if next_nodes != (WORKFLOW_INTERRUPT_NODE,) or interrupts:
                raise OperationConflictError("quota pause checkpoint 暂存状态冲突")
        else:
            if raw_dispatch is not None or next_nodes or interrupts:
                raise OperationConflictError("quota checkpoint 已存在未知状态")
            await self._graph.aupdate_state(
                config,
                {
                    "conversation_id": projection.workflow_state.conversation_id,
                    "user_id": user_id,
                    "turn_id": projection.workflow_state.last_turn_id,
                    "run_id": projection.workflow_state.last_turn_id,
                    "workflows": {
                        projection.workflow.workflow_id: projection.workflow,
                    },
                    "active_workflow_id": projection.workflow.workflow_id,
                    "workflow_dispatch_result": desired.model_dump(mode="json"),
                },
                as_node=(
                    OPERATION_COMPLETION_INTERRUPT_NODE
                    if is_pause
                    else DISPATCH_WORKFLOW_NODE
                ),
            )
            if not is_pause:
                terminal = await self._graph.aget_state(config)
                _require_exact_quota_checkpoint(
                    terminal,
                    desired=desired,
                    user_id=user_id,
                )
                if tuple(getattr(terminal, "next", ()) or ()) or tuple(
                    getattr(terminal, "interrupts", ()) or ()
                ):
                    raise OperationConflictError(
                        "quota resume checkpoint 未进入终止态",
                    )
                return

        await self._graph.ainvoke(None, config)
        paused = await self._graph.aget_state(config)
        _require_exact_quota_checkpoint(
            paused,
            desired=desired,
            user_id=user_id,
        )
        if not _checkpoint_has_exact_quota_interrupt(
            paused,
            projection.open_interrupt,
        ):
            raise OperationConflictError("quota pause 未建立唯一 Graph 中断")

    def _now(self) -> datetime:
        value = self._clock.now() if hasattr(self._clock, "now") else self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock 必须返回 datetime")
        return value


class VideoOperationCompletionHandler:
    """把 M06 完成 Outbox 事件原子回灌到 M11 权威状态。"""

    def __init__(
        self,
        *,
        repository: VideoRuntimeRepository,
        operations: VideoLiveOperationBridge,
        clock: Any,
        graph: OperationCompletionCheckpointGraph | None = None,
        external_job_observer: ExternalJobStateObserver | None = None,
    ) -> None:
        self._repository = repository
        self._operations = operations
        self._clock = clock
        self._graph = graph
        self._external_job_observer = external_job_observer

    async def resume_external_job(
        self,
        namespace: GraphExecutionNamespace,
        *,
        completion_event: AgentEvent,
        idempotency_key: str,
    ) -> None:
        """只消费 Dispatcher 已领取的同一事件，不重新调用供应商。"""

        from pixelflow.agent_runtime.graph import workflow_namespace
        from pixelflow.agent_workflows.video.delivery import (
            VideoDeliveryWorkflowService,
            VideoDeliveryWorkflowState,
        )
        from pixelflow.agent_workflows.video.postproduction import (
            VideoPostProductionStage,
            VideoPostProductionWorkflowService,
            VideoPostProductionWorkflowState,
            VideoQualityReviewWorkflowResult,
        )
        from pixelflow.agent_workflows.video.state_codec import (
            decode_video_workflow_state,
            encode_video_workflow_state,
            project_video_workflow_state,
        )
        from pixelflow.agent_workflows.video.video_generation import (
            VideoSceneGenerationWorkflowService,
            VideoSceneGenerationWorkflowState,
        )
        from pixelflow.jianying_draft.models import (
            JianyingDraftResult,
            JianyingDraftStatus,
        )

        completion = self._operations._require_completion_claim(
            completion_event,
            idempotency_key=idempotency_key,
        )
        payload = completion_event.model_dump(
            mode="json",
            serialize_as_any=True,
        )["payload"]
        completion_time = completion_event.occurred_at
        workflow_id = payload.get("workflow_id")
        if not isinstance(workflow_id, str) or namespace != workflow_namespace(
            completion.conversation_id,
            workflow_id,
        ):
            raise OperationConflictError("完成事件 Graph namespace 不一致")
        envelope = await self._repository.get_video_state(
            completion.user_id,
            workflow_id,
        )
        if envelope is None:
            raise OperationConflictError("完成事件对应的视频权威状态不存在")
        state = decode_video_workflow_state(envelope)
        stage = payload.get("stage")
        status = payload.get("status")
        if not isinstance(stage, str) or status not in {
            "succeeded",
            "failed",
            "timeout",
            "expired",
        }:
            raise OperationConflictError("完成事件不是可回灌的视频终态")
        scoped = _ScopedVideoOperationPort(
            self._operations,
            user_id=completion.user_id,
            conversation_id=completion.conversation_id,
            completion=completion,
        )
        if isinstance(state, VideoSceneGenerationWorkflowState):
            scene_id = stage.partition(":")[2]
            if not scene_id:
                raise OperationConflictError("分镜完成事件缺少 scene_id")
            service = VideoSceneGenerationWorkflowService(scoped)
            if status == "succeeded":
                result = payload.get("result")
                if not isinstance(result, Mapping):
                    raise OperationConflictError("分镜成功事件缺少安全结果")
                video_url = result.get("video_url")
                if not isinstance(video_url, str):
                    raise OperationConflictError("分镜成功事件缺少视频 URL")
                raw = result.get("raw", {})
                if not isinstance(raw, Mapping):
                    raise OperationConflictError("分镜成功事件 raw 不是对象")
                updated = await service.record_scene_success(
                    state,
                    scene_id=scene_id,
                    video_url=video_url,
                    provider_job_id=_optional_text(payload.get("provider_job_id")),
                    raw=raw,
                    operation_port=scoped,
                    now=completion_time,
                )
            else:
                message = _completion_message(payload)
                retryable = status in {"timeout", "expired"}
                updated = await service.record_scene_failure(
                    state,
                    scene_id=scene_id,
                    error=message,
                    attempts=3 if retryable else 1,
                    retryable=retryable,
                    raw={},
                    operation_port=scoped,
                    now=completion_time,
                )
        elif isinstance(state, VideoPostProductionWorkflowState):
            post_service = VideoPostProductionWorkflowService(scoped)
            if state.current_stage is VideoPostProductionStage.MERGE_VIDEO and stage == VideoPostProductionStage.MERGE_VIDEO.value:
                if status == "succeeded":
                    result = payload.get("result")
                    if not isinstance(result, Mapping):
                        raise OperationConflictError("合并成功事件缺少安全结果")
                    video_url = result.get("video_url")
                    raw = result.get("raw", {})
                    if not isinstance(video_url, str) or not isinstance(raw, Mapping):
                        raise OperationConflictError("合并成功事件字段不完整")
                    updated = await post_service.record_merge_success(
                        state,
                        merged_video_url=video_url,
                        provider_job_id=_optional_text(payload.get("provider_job_id")),
                        raw=raw,
                        operation_port=scoped,
                        now=completion_time,
                    )
                else:
                    updated = await post_service.record_merge_failure(
                        state,
                        error=_completion_message(payload),
                        attempts=1,
                        retryable=status in {"timeout", "expired"},
                        raw={},
                        operation_port=scoped,
                        now=completion_time,
                    )
            elif state.current_stage is VideoPostProductionStage.QUALITY_REVIEW and stage == VideoPostProductionStage.QUALITY_REVIEW.value:
                if status == "succeeded":
                    result = payload.get("result")
                    if not isinstance(result, Mapping):
                        raise OperationConflictError("质检成功事件缺少安全结果")
                    raw = result.get("raw", {})
                    if not isinstance(raw, Mapping):
                        raise OperationConflictError("质检成功事件 raw 不是对象")
                    updated = await post_service.record_quality_success(
                        state,
                        result=VideoQualityReviewWorkflowResult(
                            ok=True,
                            passed=bool(result.get("passed")),
                            summary_markdown=str(result.get("summary_markdown") or ""),
                            quality_report_markdown=str(result.get("quality_report_markdown") or ""),
                            issues=list(result.get("issues") or []),
                            affected_scene_ids=list(result.get("affected_scene_ids") or []),
                            revision_prompt=str(result.get("revision_prompt") or ""),
                            task_id=_optional_text(payload.get("provider_job_id")),
                            raw=dict(raw),
                        ),
                        operation_port=scoped,
                        now=completion_time,
                    )
                else:
                    updated = await post_service.record_quality_failure(
                        state,
                        error=_completion_message(payload),
                        attempts=1,
                        retryable=status in {"timeout", "expired"},
                        raw={},
                        operation_port=scoped,
                        now=completion_time,
                    )
            else:
                raise OperationConflictError("后处理完成事件与当前阶段不一致")
        elif isinstance(state, VideoDeliveryWorkflowState) and stage == "jianying_draft":
            request = state.pending_jianying_request
            if request is None:
                raise OperationConflictError("剪映完成事件缺少权威请求")
            result_payload = payload.get("result")
            if result_payload is not None and not isinstance(
                result_payload,
                Mapping,
            ):
                raise OperationConflictError("剪映完成事件结果不是对象")
            result_data = dict(result_payload or {})
            domain_status = {
                "succeeded": JianyingDraftStatus.SUCCEEDED,
                "failed": JianyingDraftStatus.FAILED,
                "timeout": JianyingDraftStatus.TIMEOUT,
                "expired": JianyingDraftStatus.FAILED,
            }[status]
            normalized = JianyingDraftResult.model_validate(
                {
                    "status": domain_status.value,
                    "job_id": payload.get("job_id"),
                    "provider_task_id": payload.get("provider_job_id"),
                    "conversation_id": completion.conversation_id,
                    "storyboard_version_id": request.get("storyboard_version_id"),
                    "download_url": result_data.get("download_url"),
                    "file_name": result_data.get("file_name"),
                    "expire_at": result_data.get("expire_at"),
                    "message": (result_data.get("message") if status == "succeeded" else _completion_message(payload)),
                }
            )
            updated = await VideoDeliveryWorkflowService(scoped).record_jianying_result(
                state,
                normalized,
                operation_port=scoped,
                now=completion_time,
            )
        else:
            raise OperationConflictError("完成事件与当前视频阶段不一致")
        workflow = project_video_workflow_state(updated)
        updated_envelope = encode_video_workflow_state(
            user_id=completion.user_id,
            state=updated,
            workflow_version=envelope.workflow_version + 1,
            last_turn_id=envelope.last_turn_id,
            last_action_key=completion_event.event_id,
        )
        messages = ()
        artifact = None
        if (
            isinstance(updated, VideoSceneGenerationWorkflowState)
            and not updated.pending_operations
        ):
            from pixelflow.agent_workflows.video.delivery import (
                VideoWebArtifactAdapter,
            )

            artifact = VideoWebArtifactAdapter(VideoDeliveryWorkflowService(scoped)).project(updated)
            if updated.failed_scenes:
                failure_projection = _scene_failure_projection(updated.failed_scenes)
                artifact.update(failure_projection)
                if failure_projection["nonRetryableSceneIds"]:
                    artifact.pop("videoScenePackages", None)
        elif isinstance(updated, VideoPostProductionWorkflowState) and stage == VideoPostProductionStage.MERGE_VIDEO.value:
            from pixelflow.agent_workflows.video.delivery import VideoWebArtifactAdapter

            delivery_service = VideoDeliveryWorkflowService(scoped)
            adapter = VideoWebArtifactAdapter(delivery_service)
            scene_artifact = adapter.project(updated.generation_state)
            if status == "succeeded":
                projection_state = VideoDeliveryWorkflowState(
                    workflow_id=updated.workflow_id,
                    conversation_id=updated.conversation_id,
                    current_stage=updated.current_stage,
                    status=updated.status,
                    stage_version=updated.stage_version,
                    context_version=updated.context_version,
                    created_at=updated.created_at,
                    updated_at=updated.updated_at,
                    _postproduction_state=updated,
                    _jianying_draft_records_json="{}",
                    _operation_attempts_json="{}",
                    _pending_operation=None,
                    _pending_jianying_operation_json="null",
                    _final_video_delivery_json="null",
                )
                artifact = adapter.project(projection_state)
            else:
                failure = updated.merge_error or {}
                artifact = {
                    "type": "video_result",
                    "title": "视频合并未完成",
                    "description": str(failure.get("message") or "视频合并未完成，请重试。"),
                    "actionLabel": ("重新生成" if failure.get("retryable") is True else "查看结果"),
                    "videoScenePackages": scene_artifact["videoScenePackages"],
                    "generatedSceneVideos": scene_artifact["generatedSceneVideos"],
                    "mergedVideo": {
                        "ok": False,
                        "endpoint": "/api/video/merge",
                        "merged_video_url": None,
                        "task_id": None,
                        "scene_videos": updated.generation_state.scene_videos,
                        "error": failure.get("error"),
                        "message": failure.get("message"),
                        "quota_insufficient": failure.get("quota_insufficient") is True,
                        "raw": {},
                    },
                    "videoAccepted": False,
                }
        elif isinstance(updated, VideoPostProductionWorkflowState) and stage == VideoPostProductionStage.QUALITY_REVIEW.value:
            from pixelflow.agent_workflows.video.delivery import VideoWebArtifactAdapter

            delivery_service = VideoDeliveryWorkflowService(scoped)
            adapter = VideoWebArtifactAdapter(delivery_service)
            scene_artifact = adapter.project(updated.generation_state)
            quality_review = _quality_review_projection(updated.quality_review or {})
            merged = updated.merged_video
            if not isinstance(merged, Mapping):
                raise OperationConflictError("质检完成事件缺少权威合并视频")
            artifact = {
                "type": "video_quality_review",
                "title": "QAAgent QC 质检",
                "description": ("视频质检已通过，请确认最终成片。" if quality_review.get("passed") is True else "视频质检未完成，请按提示重试或修改。" if status != "succeeded" else "视频质检已定位需修改分镜，请选择修改范围。"),
                "actionLabel": (
                    "重新质检"
                    if status != "succeeded" and quality_review.get("retryable") is True
                    else "查看失败原因"
                    if status != "succeeded"
                    else "选择"
                ),
                "videoQualityReview": quality_review,
                "videoRevisionFeedback": updated.quality_feedback or "",
                "videoScenePackages": scene_artifact["videoScenePackages"],
                "generatedSceneVideos": scene_artifact["generatedSceneVideos"],
                "mergedVideo": {
                    "ok": True,
                    "endpoint": merged["endpoint"],
                    "merged_video_url": merged["video_url"],
                    "task_id": merged["task_id"],
                    "scene_videos": merged.get("scene_videos") or [],
                    "error": None,
                    "message": "视频合并完成。",
                    "quota_insufficient": False,
                    "raw": {},
                },
            }
        elif isinstance(updated, VideoDeliveryWorkflowState):
            from pixelflow.agent_runtime.identity import projection_message_id
            from pixelflow.agent_workflows.video.delivery import (
                VideoWebArtifactAdapter,
            )

            delivery_artifact = VideoWebArtifactAdapter(VideoDeliveryWorkflowService(scoped)).project(updated)
            draft = delivery_artifact.get("jianyingDraft")
            if not isinstance(draft, Mapping):
                raise OperationConflictError("剪映完成事件缺少 Web 结果记录")
            scenes = request.get("scenes")
            if not isinstance(scenes, list):
                raise OperationConflictError("剪映完成事件缺少权威分镜请求")
            message_id = projection_message_id(
                workflow.workflow_id,
                workflow.current_stage,
                workflow.stage_version,
                completion_event.event_id,
            )
            succeeded = status == "succeeded"
            artifact = {
                "type": "jianying_draft",
                "title": "剪映草稿已生成" if succeeded else "剪映草稿生成失败",
                "description": str(draft.get("message") or "剪映草稿处理完成。"),
                "actionLabel": "下载" if succeeded else "重新生成",
                "jianyingDraft": dict(draft),
                "pendingJianyingDraftJob": {
                    "job_id": payload.get("job_id"),
                    "conversation_id": completion.conversation_id,
                    "source_message_id": message_id,
                    "storyboard_version_id": updated.current_storyboard_version_id,
                    "started_at": state.updated_at.isoformat(),
                    "request": dict(request),
                },
                "jianyingDraftSceneCount": len(scenes),
            }
        if artifact is not None:
            messages = (
                _completion_projection_message(
                    workflow=workflow,
                    completion_event=completion_event,
                    artifact=artifact,
                ),
            )
        opened_interrupt = None
        if artifact is not None and self._graph is not None:
            opened_interrupt = _operation_completion_interrupt(
                user_id=completion.user_id,
                turn_id=updated_envelope.last_turn_id,
                workflow=workflow,
                workflow_version=updated_envelope.workflow_version,
                scene_generation=isinstance(
                    updated,
                    VideoSceneGenerationWorkflowState,
                ),
                opened_at=completion_time,
            )
            opened_interrupt = await self._checkpoint_completion_interrupt(
                updated_envelope=updated_envelope,
                workflow=workflow,
                messages=messages,
                interrupt=opened_interrupt,
            )
        await self._repository.commit_operation_completion(
            completion.claim,
            user_id=completion.user_id,
            workflow_state=updated_envelope,
            workflow=workflow,
            expected_workflow_version=envelope.workflow_version,
            messages=messages,
            open_interrupt=opened_interrupt,
            occurred_at=self._now(),
        )
        from pixelflow.agent_runtime.jobs.providers import ProviderJobOutcome

        self._observe_external_job_state(ProviderJobOutcome(status))
        self._operations._release_published_completion(completion)

    async def _checkpoint_completion_interrupt(
        self,
        *,
        updated_envelope: Any,
        workflow: Any,
        messages: tuple[Any, ...],
        interrupt: Any,
    ) -> Any:
        """先幂等建立真实 Graph pause，成功后才允许公开 Repository 投影。"""

        from pixelflow.agent_runtime.contracts import TurnStatus, WorkflowRecord
        from pixelflow.agent_runtime.graph import supervisor_namespace
        from pixelflow.agent_runtime.graph.composition import (
            OPERATION_COMPLETION_INTERRUPT_NODE,
            WORKFLOW_INTERRUPT_NODE,
        )
        from pixelflow.agent_workflows.video.live_handler import (
            WorkflowDispatchResult,
        )

        graph = self._graph
        if graph is None:
            raise OperationConflictError("Operation 完成中断缺少 Graph checkpoint")
        normalized_workflow = WorkflowRecord.model_validate(workflow)
        desired = WorkflowDispatchResult(
            state=updated_envelope,
            workflow=normalized_workflow,
            messages=messages,
            interrupt=interrupt,
            turn_status=TurnStatus.WAITING_USER,
        )
        namespace = supervisor_namespace(normalized_workflow.conversation_id)
        config = namespace.as_runnable_config()
        snapshot = await graph.aget_state(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        if (
            values.get("conversation_id") != normalized_workflow.conversation_id
            or values.get("turn_id") != interrupt.turn_id
        ):
            raise OperationConflictError("Operation 完成中断与 Supervisor checkpoint 不一致")

        existing_dispatch = values.get("workflow_dispatch_result")
        stored_dispatch = None
        if existing_dispatch is not None:
            try:
                stored_dispatch = WorkflowDispatchResult.model_validate(
                    existing_dispatch
                )
            except Exception:
                stored_dispatch = None
        if (
            stored_dispatch is not None
            and stored_dispatch.interrupt is not None
            and _same_completion_interrupt_occurrence(
                stored_dispatch.interrupt,
                interrupt,
            )
        ):
            interrupt = stored_dispatch.interrupt
            desired = desired.model_copy(
                update={"interrupt": interrupt},
            )

        expected_value = {
            "type": interrupt.kind,
            "interrupt_id": interrupt.interrupt_id,
            "reason_code": interrupt.reason_code,
            "payload": interrupt.model_dump(mode="json")["payload"],
        }
        if _checkpoint_has_completion_interrupt(snapshot, expected_value):
            _require_checkpoint_dispatch(values, desired)
            return interrupt
        if tuple(getattr(snapshot, "interrupts", ()) or ()):
            raise OperationConflictError("Supervisor checkpoint 已存在其他开放中断")

        matches_desired = stored_dispatch == desired
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if matches_desired:
            if next_nodes != (WORKFLOW_INTERRUPT_NODE,):
                raise OperationConflictError("Operation 完成 checkpoint 暂存状态不一致")
        else:
            if next_nodes:
                raise OperationConflictError("Supervisor checkpoint 仍有未完成节点")
            await graph.aupdate_state(
                config,
                {
                    "workflows": {
                        normalized_workflow.workflow_id: normalized_workflow,
                    },
                    "workflow_dispatch_result": desired.model_dump(mode="json"),
                },
                as_node=OPERATION_COMPLETION_INTERRUPT_NODE,
            )
        await graph.ainvoke(None, config)
        paused = await graph.aget_state(config)
        if not _checkpoint_has_completion_interrupt(paused, expected_value):
            raise OperationConflictError("Operation 完成未建立唯一 Graph 中断")
        _require_checkpoint_dispatch(
            dict(getattr(paused, "values", {}) or {}),
            desired,
        )
        return interrupt

    def _observe_external_job_state(self, state: ProviderJobOutcome) -> None:
        """记录本进程已确认交付的终态；不承诺跨崩溃的精确一次语义。"""

        observer = self._external_job_observer
        if observer is None:
            return
        try:
            observer.observe_external_job_state(state)
        except Exception:
            # 指标旁路失败不得阻塞已提交完成事件的 claim 释放。
            return

    def _now(self) -> datetime:
        value = self._clock.now() if hasattr(self._clock, "now") else self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock 必须返回 datetime")
        return value


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _canonical_m06_request(
    request: OperationRequest,
    *,
    provider_request: Mapping[str, JsonValue] | None = None,
    expected_request_hash: str | None = None,
) -> OperationRequest:
    """校验 M11 领域身份并转换成 M06 的带版本规范身份。"""

    from pixelflow.agent_runtime.jobs.identity import (
        build_operation_idempotency_key,
    )

    request_hash = request.request_hash
    if provider_request is not None:
        canonical_hash = _hash_operation_request(provider_request)
        raw_hash = canonical_hash.removeprefix("sha256:")
        external_hash = expected_request_hash or request_hash
        if external_hash not in {raw_hash, canonical_hash, request.idempotency_key}:
            raise OperationConflictError("M11 Provider 请求摘要与 Operation 不一致")
    elif request_hash.startswith("sha256:"):
        canonical_hash = request_hash
        raw_hash = request_hash.removeprefix("sha256:")
    elif _SHA256_PATTERN.fullmatch(request_hash) is not None:
        raw_hash = request_hash
        canonical_hash = f"sha256:{request_hash}"
    else:
        raise OperationConflictError("M11 Operation request_hash 不是规范 SHA-256")

    canonical_key = build_operation_idempotency_key(
        request.workflow_id,
        request.stage,
        request.stage_version,
        request.attempt,
    )
    expected_external_key = _m11_idempotency_key(
        workflow_id=request.workflow_id,
        stage=request.stage,
        stage_version=request.stage_version,
        attempt=request.attempt,
        request_hash=raw_hash,
    )
    if request.idempotency_key not in {canonical_key, expected_external_key}:
        raise OperationConflictError("M11 Operation 幂等键与阶段身份不一致")
    return OperationRequest(
        workflow_id=request.workflow_id,
        stage=request.stage,
        stage_version=request.stage_version,
        attempt=request.attempt,
        request_hash=canonical_hash,
        idempotency_key=canonical_key,
    )


def _m11_idempotency_key(
    *,
    workflow_id: str,
    stage: str,
    stage_version: int,
    attempt: int,
    request_hash: str,
) -> str:
    from pixelflow.agent_runtime.jobs.identity import (
        build_operation_idempotency_key,
    )

    if stage.startswith("generate_scene_video:"):
        scene_id = stage.partition(":")[2]
        identity = "\0".join((workflow_id, str(stage_version), scene_id, str(attempt)))
        return f"pf:video-scene:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    if stage in {"merge_video", "quality_review"}:
        identity = "|".join((workflow_id, stage, str(stage_version), str(attempt), request_hash))
        return f"pf:video-post:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    if stage == "jianying_draft":
        return f"video:{workflow_id}:jianying_draft:v{stage_version}:a{attempt}:{request_hash}"
    return build_operation_idempotency_key(
        workflow_id,
        stage,
        stage_version,
        attempt,
    )


def _hash_operation_request(provider_request: object) -> str:
    from pixelflow.agent_runtime.jobs.identity import hash_operation_request

    return hash_operation_request(provider_request)


_SCENE_VIDEO_PROVIDER_FIELDS = frozenset(
    {
        "scene_id",
        "scene_index",
        "duration",
        "duration_ms",
        "prompt",
        "storyline",
        "shot_description",
        "narration",
        "transition",
        "generation_mode",
        "image_urls",
        "video_urls",
        "audio_urls",
        "model",
        "ratio",
        "size",
        "sound",
    }
)
_MERGE_VIDEO_PROVIDER_FIELDS = frozenset(
    {"video_urls", "scene_videos", "duration", "size", "model"}
)
_QUALITY_REVIEW_PROVIDER_FIELDS = frozenset(
    {
        "merged_video_url",
        "scene_videos",
        "scene_packages",
        "brief",
        "materials",
        "user_feedback",
        "ratio",
        "size",
    }
)
_JIANYING_DRAFT_PROVIDER_FIELDS = frozenset({"request", "retry_failed"})
_SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {"authmode", "tokenbudget", "tokencount", "tokencounthint", "tokenhint"}
)
_ESCAPED_ASSIGNMENT_QUOTE_PATTERN = re.compile(
    r"\\(?:u(?:0022|0027|0060)|[\"'`])",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(?:[\"'`]?\b(?:authorization|"
    r"(?:access|refresh|auth|bearer|client|session|id|api|provider)"
    r"[\s_-]*token|token|(?:api[\s_-]*)?key|secret|password|credential)\b"
    r"[\"'`]?\s*\]?\s*[:=]\s*"
    r"(?:bearer\s+)?\S+|\bbearer\s+[a-z0-9._~+/=-]{6,})",
    re.IGNORECASE,
)
_JWT_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


def _validate_provider_request_boundary(
    *,
    operation_request: object,
    provider_request: object,
) -> None:
    """按真实 M11 stage 限定顶层字段，再递归执行统一凭据键检查。"""

    if isinstance(operation_request, OperationRequest):
        stage = operation_request.stage
    elif isinstance(operation_request, Mapping):
        stage = operation_request.get("stage")
    else:
        stage = None
    allowed_fields = _provider_fields_for_stage(stage)
    if not isinstance(provider_request, Mapping):
        raise ValueError("Provider 请求必须是对象")
    for key in provider_request:
        if type(key) is not str or key not in allowed_fields:
            raise ValueError("Provider 请求包含 stage 未声明字段")
    _ensure_provider_request_has_no_credentials(provider_request)


def _provider_fields_for_stage(stage: object) -> frozenset[str]:
    if isinstance(stage, str) and stage.startswith("generate_scene_video:"):
        scene_id = stage.removeprefix("generate_scene_video:")
        if scene_id and scene_id == scene_id.strip():
            return _SCENE_VIDEO_PROVIDER_FIELDS
    elif stage == "merge_video":
        return _MERGE_VIDEO_PROVIDER_FIELDS
    elif stage == "quality_review":
        return _QUALITY_REVIEW_PROVIDER_FIELDS
    elif stage == "jianying_draft":
        return _JIANYING_DRAFT_PROVIDER_FIELDS
    raise ValueError("Provider 请求 stage 不受支持")


def _ensure_provider_request_has_no_credentials(value: object) -> None:
    """递归拒绝统一协议分类器命中的凭据键与明确凭据值。"""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("Provider 请求包含非法字段")
            try:
                normalized_key = _normalize_protocol_key(key)
            except ValueError:
                raise ValueError("Provider 请求包含非法字段") from None
            compact_key = re.sub(r"[ _-]+", "", normalized_key).casefold()
            if (
                compact_key not in _SAFE_PROVIDER_METADATA_KEYS
                and _is_sensitive_protocol_key(normalized_key)
            ):
                raise ValueError("Provider 请求包含敏感凭据")
            _ensure_provider_request_has_no_credentials(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _ensure_provider_request_has_no_credentials(child)
        return
    if isinstance(value, str):
        normalized = _normalize_credential_assignment_syntax(value)
        if (
            _CREDENTIAL_VALUE_PATTERN.search(normalized)
            or _JWT_CREDENTIAL_VALUE_PATTERN.search(normalized)
        ):
            raise ValueError("Provider 请求包含敏感凭据")


def _normalize_credential_assignment_syntax(value: str) -> str:
    """只为安全识别展开常见引号转义，不改写原业务值。"""

    normalized = unicodedata.normalize("NFKC", value)

    def replace_quote(match: re.Match[str]) -> str:
        escaped = match.group(0)
        if escaped[1].casefold() == "u":
            return chr(int(escaped[-4:], 16))
        return escaped[-1]

    return _ESCAPED_ASSIGNMENT_QUOTE_PATTERN.sub(replace_quote, normalized)


def _external_job_ref(operation: OperationRecord) -> ExternalJobRef:
    raw_hash = operation.request_hash.removeprefix("sha256:")
    return ExternalJobRef(
        job_id=operation.job_id,
        provider_job_id=operation.provider_job_id,
        workflow_id=operation.workflow_id,
        stage=operation.stage,
        status=operation.status,
        attempt=operation.attempt,
        idempotency_key=_m11_idempotency_key(
            workflow_id=operation.workflow_id,
            stage=operation.stage,
            stage_version=operation.stage_version,
            attempt=operation.attempt,
            request_hash=raw_hash,
        ),
        next_poll_at=operation.next_poll_at,
        lease_owner=operation.lease_owner,
        lease_expires_at=operation.lease_expires_at,
    )


def _require_text(field_name: str, value: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > maximum:
        raise ValueError(f"{field_name} 必须是 1 到 {maximum} 个无首尾空白的字符")
    return normalized


def _optional_text(value: object) -> str | None:
    """把可选外部标识收敛为无首尾空白的非空字符串。"""

    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise OperationConflictError("完成事件包含非法外部标识")
    return value


def _completion_message(payload: Mapping[str, object]) -> str:
    """只读取 M06 已固定化的安全完成文案。"""

    message = payload.get("message")
    if not isinstance(message, str) or not message:
        raise OperationConflictError("视频失败事件缺少安全原因")
    return message


def _completion_projection_message(
    *,
    workflow: object,
    completion_event: object,
    artifact: Mapping[str, object],
):
    """用完成事件和更新后 Workflow 派生稳定 Web 消息。"""

    from pixelflow.agent_runtime.contracts import AgentEvent, WorkflowRecord
    from pixelflow.agent_runtime.identity import projection_message_id
    from pixelflow.agent_runtime.persistence import SupervisorProjectionMessage
    from pixelflow.agent_workflows.video.live_handler import (
        artifact_summary,
        deepcopy_json,
    )

    normalized_workflow = WorkflowRecord.model_validate(workflow)
    normalized_event = AgentEvent.model_validate(completion_event)
    normalized_artifact = deepcopy_json(_clear_raw_provider_payloads(dict(artifact)))
    if not isinstance(normalized_artifact, dict):
        raise TypeError("完成事件 Artifact 必须是对象")
    return SupervisorProjectionMessage(
        message_id=projection_message_id(
            normalized_workflow.workflow_id,
            normalized_workflow.current_stage,
            normalized_workflow.stage_version,
            normalized_event.event_id,
        ),
        conversation_id=normalized_workflow.conversation_id,
        run_id=normalized_workflow.workflow_id,
        role="assistant",
        content=_completion_artifact_summary(
            normalized_artifact,
            success_summary=artifact_summary,
        ),
        payload={
            "workflow_id": normalized_workflow.workflow_id,
            "artifact_ref": (
                normalized_workflow.latest_artifact_refs[-1]
                if normalized_workflow.latest_artifact_refs
                else None
            ),
            "artifact": normalized_artifact,
        },
        created_at=normalized_event.occurred_at,
    )


def _operation_completion_interrupt(
    *,
    user_id: str,
    turn_id: str,
    workflow: object,
    workflow_version: int,
    scene_generation: bool,
    opened_at: datetime,
):
    """把最终可操作完成 Artifact 映射到原 Turn 的稳定人工中断。"""

    from pixelflow.agent_runtime.contracts import WorkflowRecord
    from pixelflow.agent_runtime.graph import supervisor_namespace
    from pixelflow.agent_runtime.persistence import StoredAgentInterrupt
    from pixelflow.agent_workflows.video.live_handler import (
        video_interrupt_occurrence_id,
    )

    normalized = WorkflowRecord.model_validate(workflow)
    if not normalized.latest_artifact_refs:
        raise OperationConflictError("Operation 完成中断缺少权威 Artifact 引用")
    artifact_ref = normalized.latest_artifact_refs[-1]
    kind = "video_scene_video_review" if scene_generation else "video_result_review"
    reason_code = (
        "video_scene_video_review_required"
        if scene_generation
        else "video_result_review_required"
    )
    namespace = supervisor_namespace(normalized.conversation_id)
    return StoredAgentInterrupt(
        interrupt_id=video_interrupt_occurrence_id(
            turn_id=turn_id,
            reason_code=reason_code,
            workflow=normalized,
            workflow_version=workflow_version,
        ),
        conversation_id=normalized.conversation_id,
        workflow_id=normalized.workflow_id,
        turn_id=turn_id,
        kind=kind,
        reason_code=reason_code,
        payload={
            "workflow_id": normalized.workflow_id,
            "stage": normalized.current_stage,
            "artifact_ref": artifact_ref,
            "ui_kind": "video_result_review",
        },
        opened_at=opened_at,
        user_id=user_id,
        thread_id=namespace.thread_id,
        checkpoint_ns="root",
    )


def _checkpoint_has_completion_interrupt(
    snapshot: object,
    expected_value: Mapping[str, object],
) -> bool:
    """只接受与业务中断公开值完全一致的唯一 LangGraph pause。"""

    matches = [
        item
        for item in tuple(getattr(snapshot, "interrupts", ()) or ())
        if getattr(item, "value", None) == expected_value
    ]
    return len(matches) == 1


def _checkpoint_has_exact_quota_interrupt(
    snapshot: object,
    interrupt: object,
) -> bool:
    """quota checkpoint 只能包含目标授权中断这一条 pause。"""

    if interrupt is None:
        return False
    expected = {
        "type": interrupt.kind,
        "interrupt_id": interrupt.interrupt_id,
        "reason_code": interrupt.reason_code,
        "payload": interrupt.model_dump(mode="json")["payload"],
    }
    interrupts = tuple(getattr(snapshot, "interrupts", ()) or ())
    return len(interrupts) == 1 and getattr(
        interrupts[0],
        "value",
        None,
    ) == expected


def _require_exact_quota_checkpoint(
    snapshot: object,
    *,
    desired: object,
    user_id: str,
) -> None:
    """精确比较 quota checkpoint 的事件投影、用户和原 Turn。"""

    from pixelflow.agent_workflows.video.live_handler import WorkflowDispatchResult

    values = dict(getattr(snapshot, "values", {}) or {})
    try:
        stored = WorkflowDispatchResult.model_validate(
            values.get("workflow_dispatch_result")
        )
    except Exception as exc:
        raise OperationConflictError("quota checkpoint 缺少权威派发结果") from exc
    if (
        stored != desired
        or values.get("conversation_id") != stored.state.conversation_id
        or values.get("user_id") != user_id
        or values.get("turn_id") != stored.state.last_turn_id
    ):
        raise OperationConflictError("quota checkpoint 派发结果不一致")


def _same_completion_interrupt_occurrence(
    stored: object,
    expected: object,
) -> bool:
    """重放时只复用同一业务中断，首次 checkpoint 的打开时间保持不变。"""

    from pixelflow.agent_runtime.persistence import StoredAgentInterrupt

    try:
        stored_document = StoredAgentInterrupt.model_validate(stored).model_dump(
            mode="json"
        )
        expected_document = StoredAgentInterrupt.model_validate(expected).model_dump(
            mode="json"
        )
    except Exception:
        return False
    stored_document.pop("opened_at", None)
    expected_document.pop("opened_at", None)
    return stored_document == expected_document


def _require_checkpoint_dispatch(values: Mapping[str, object], desired: object) -> None:
    """拒绝 Graph pause 与待提交业务状态不一致的半恢复 checkpoint。"""

    from pixelflow.agent_workflows.video.live_handler import WorkflowDispatchResult

    try:
        stored = WorkflowDispatchResult.model_validate(
            values.get("workflow_dispatch_result")
        )
    except Exception as exc:
        raise OperationConflictError("Operation 完成 checkpoint 缺少权威派发结果") from exc
    if stored != desired:
        raise OperationConflictError("Operation 完成 checkpoint 派发结果不一致")


def _quality_review_projection(value: Mapping[str, object]) -> dict[str, object]:
    """
    补齐 Web 质检 DTO，同时保留领域层安全失败字段。
    """

    ok = value.get("ok") is True
    message = value.get("message")
    if not isinstance(message, str) or not message:
        message = "视频质检完成。" if ok else "视频质检未完成，请重试。"
    score = value.get("score")
    projected = dict(value)
    projected.update({
        "ok": ok,
        "endpoint": (
            value.get("endpoint")
            if isinstance(value.get("endpoint"), str) and value.get("endpoint")
            else "/api/creative/video_quality_review"
        ),
        "task_id": value.get("task_id") if isinstance(value.get("task_id"), str) else None,
        "passed": value.get("passed") is True,
        "score": score if type(score) in {int, float} else 0,
        "summary_markdown": value.get("summary_markdown") if isinstance(value.get("summary_markdown"), str) else "",
        "quality_report_markdown": value.get("quality_report_markdown") if isinstance(value.get("quality_report_markdown"), str) else "",
        "issues": list(value.get("issues")) if isinstance(value.get("issues"), list) else [],
        "affected_scene_ids": list(value.get("affected_scene_ids")) if isinstance(value.get("affected_scene_ids"), list) else [],
        "revision_prompt": value.get("revision_prompt") if isinstance(value.get("revision_prompt"), str) else "",
        "check_results": list(value.get("check_results")) if isinstance(value.get("check_results"), list) else [],
        "error": value.get("error") if isinstance(value.get("error"), str) else None,
        "message": message,
        "quota_insufficient": value.get("quota_insufficient") is True,
        "raw": {},
    })
    return projected


def _completion_artifact_summary(
    artifact: Mapping[str, object],
    *,
    success_summary: Callable[[Mapping[str, JsonValue]], str],
) -> str:
    """
    按 Artifact 的真实终态生成摘要，失败消息不得复用成功文案。
    """

    artifact_type = artifact.get("type")
    if artifact_type == "video_result":
        generated = artifact.get("generatedSceneVideos")
        if isinstance(generated, Mapping) and generated.get("ok") is False:
            description = artifact.get("description")
            if isinstance(description, str) and description:
                return description
            return "场景视频生成未完成，请查看失败原因。"
        merged = artifact.get("mergedVideo")
        if isinstance(merged, Mapping) and merged.get("ok") is False:
            return "视频合并未完成，请按提示重试。"
    elif artifact_type == "video_quality_review":
        quality = artifact.get("videoQualityReview")
        if isinstance(quality, Mapping) and quality.get("ok") is False:
            return "视频质检未完成，请按提示重试或修改。"
    elif artifact_type == "jianying_draft":
        draft = artifact.get("jianyingDraft")
        if isinstance(draft, Mapping) and draft.get("status") != "succeeded":
            return "剪映草稿生成未完成，请按提示重试。"
    return success_summary(artifact)  # type: ignore[arg-type]


def _scene_failure_projection(
    failed_scenes: list[dict[str, Any]],
) -> dict[str, JsonValue]:
    """按权威失败标记派生可执行动作和分镜集合。"""

    retryable_scene_ids = [
        str(item["scene_id"])
        for item in failed_scenes
        if item.get("retryable") is True
    ]
    quota_retryable_scene_ids = [
        str(item["scene_id"])
        for item in failed_scenes
        if item.get("quota_insufficient") is True
    ]
    non_retryable_scene_ids = [
        str(item["scene_id"])
        for item in failed_scenes
        if item.get("retryable") is not True
        and item.get("quota_insufficient") is not True
    ]
    retryable_text = "、".join(retryable_scene_ids)
    quota_text = "、".join(quota_retryable_scene_ids)
    non_retryable_text = "、".join(non_retryable_scene_ids)
    if non_retryable_scene_ids:
        if retryable_scene_ids or quota_retryable_scene_ids:
            recoverable_parts = []
            if retryable_scene_ids:
                recoverable_parts.append(f"可重试分镜：{retryable_text}")
            if quota_retryable_scene_ids:
                recoverable_parts.append(f"恢复额度后可重试分镜：{quota_text}")
            description = (
                "场景视频生成未完成。"
                + "；".join(recoverable_parts)
                + f"；不可直接重试分镜：{non_retryable_text}。"
                "请先修改不可重试分镜的输入或内容。"
            )
        else:
            description = (
                f"场景视频生成未完成。不可直接重试分镜：{non_retryable_text}。"
                "请修改输入或分镜后重新执行。"
            )
        action_label = "查看失败原因"
    elif quota_retryable_scene_ids and not retryable_scene_ids:
        description = f"场景视频生成额度不足。恢复额度后可重试分镜：{quota_text}。"
        action_label = "恢复额度后重试"
    else:
        recoverable_ids = retryable_scene_ids + quota_retryable_scene_ids
        description = f"场景视频生成未完成。可重试分镜：{'、'.join(recoverable_ids)}。"
        action_label = "重新生成场景视频"
    return {
        "type": "video_result",
        "title": "场景视频生成未完成",
        "description": description,
        "actionLabel": action_label,
        "retryableSceneIds": retryable_scene_ids,
        "quotaRetryableSceneIds": quota_retryable_scene_ids,
        "nonRetryableSceneIds": non_retryable_scene_ids,
    }


def _clear_raw_provider_payloads(value: object) -> object:
    """保留 Web DTO 的 raw 占位字段，但绝不投影供应商原始内容。"""

    if isinstance(value, Mapping):
        return {str(key): ({} if str(key) == "raw" else _clear_raw_provider_payloads(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_clear_raw_provider_payloads(item) for item in value]
    return value


__all__ = [
    "TransientCredentialVault",
    "VideoLiveOperationBridge",
    "VideoOperationAdapterResolver",
    "VideoOperationCompletionHandler",
    "VideoOperationQuotaStateHandler",
    "VideoOperationStartRequest",
]
