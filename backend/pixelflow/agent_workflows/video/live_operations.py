"""连接视频 live Workflow 与 M06 External Job Coordinator。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import TYPE_CHECKING, Any

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
)

if TYPE_CHECKING:
    from pixelflow.agent_runtime.contracts import AgentEvent, ExternalJobStatus
    from pixelflow.agent_runtime.graph import GraphExecutionNamespace
    from pixelflow.agent_runtime.jobs.completion import WorkflowGraphResumePort
    from pixelflow.agent_runtime.jobs.providers import ProviderJobAdapter
    from pixelflow.agent_runtime.jobs.recovery import OperationRecoveryRuntime
    from pixelflow.agent_runtime.persistence import VideoRuntimeRepository
    from pixelflow.agent_runtime.persistence.repositories import EventDeliveryClaim


class VideoOperationStartRequest(ContractModel):
    """保存 Operation 身份与规范供应商请求，不允许携带临时鉴权。"""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    operation_request: OperationRequest
    provider_request: dict[str, JsonValue]

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
    """只把明确注册的视频 stage 解析为 Provider Adapter。"""

    def __init__(self, adapters: Mapping[str, ProviderJobAdapter]) -> None:
        from pixelflow.agent_runtime.jobs import MappingProviderJobAdapterResolver

        self._resolver = MappingProviderJobAdapterResolver(adapters)

    def resolve(self, stage: str) -> ProviderJobAdapter:
        return self._resolver.resolve(stage)


@dataclass(frozen=True, slots=True)
class _OwnedCompletionClaim:
    """把完成事件租约与原用户、会话和 Operation 身份绑定。"""

    user_id: str
    conversation_id: str
    job_id: str
    claim: EventDeliveryClaim


class _CompletionClaimRegistry:
    """仅暂存 Dispatcher 已领取的不可变 claim，不执行第二次领取。"""

    def __init__(self, *, maximum: int = 1000) -> None:
        self._lock = RLock()
        self._maximum = maximum
        self._claims: dict[str, _OwnedCompletionClaim] = {}

    def remember(
        self,
        *,
        user_id: str,
        conversation_id: str,
        job_id: str,
        claim: EventDeliveryClaim,
        now: datetime,
    ) -> None:
        from pixelflow.agent_runtime.persistence.repositories import (
            EventDeliveryClaim,
        )

        normalized = EventDeliveryClaim.model_validate(
            claim.model_dump(mode="python")
        )
        entry = _OwnedCompletionClaim(
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
                    raise OperationConflictError("完成事件仍绑定其他有效投递租约")
                self._claims.pop(event_id, None)
            if event_id not in self._claims and len(self._claims) >= self._maximum:
                raise OperationConflictError("完成事件临时 claim 已达到安全上限")
            self._claims[event_id] = entry

    def require(
        self,
        completion_event: AgentEvent,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> _OwnedCompletionClaim:
        event_id = _require_text("idempotency_key", idempotency_key, maximum=64)
        if completion_event.event_id != event_id:
            raise OperationConflictError("完成事件与 Graph 幂等键不一致")
        with self._lock:
            entry = self._claims.get(event_id)
            if entry is None:
                raise OperationConflictError("完成事件缺少 Dispatcher 原始 claim")
            if entry.claim.event != completion_event:
                raise OperationConflictError("完成事件与暂存 claim 不一致")
            if now >= entry.claim.lease_expires_at:
                self._claims.pop(event_id, None)
                raise OperationConflictError("完成事件投递租约已过期")
            return entry

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
                raise OperationConflictError("完成事件清理租约 owner 不一致")
            self._claims.pop(identity, None)

    def _purge_expired(self, now: datetime) -> None:
        expired = [
            event_id
            for event_id, entry in self._claims.items()
            if entry.claim.lease_expires_at <= now
        ]
        for event_id in expired:
            self._claims.pop(event_id, None)


class _CompletionClaimRepositoryProxy:
    """透明转发 M06 Repository，并截获唯一完成投递 claim。"""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        registry: _CompletionClaimRegistry,
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
    ) -> None:
        if not isinstance(resolver, VideoOperationAdapterResolver):
            raise TypeError("resolver 必须是 VideoOperationAdapterResolver")
        self._repository = repository
        self._resolver = resolver
        self._lease_owner = _require_text("lease_owner", lease_owner, maximum=128)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory
        self._completion_claims = _CompletionClaimRegistry()
        self._recovery_repository = _CompletionClaimRepositoryProxy(
            repository,
            self._completion_claims,
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

        normalized = VideoOperationStartRequest.model_validate(
            request.model_dump(mode="python")
        )
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
                    raise OperationConflictError(
                        "剪映草稿持久终态格式不受支持"
                    )
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
    ) -> _OwnedCompletionClaim:
        return self._completion_claims.require(
            completion_event,
            idempotency_key=idempotency_key,
            now=self._now(),
        )

    def _release_published_completion(
        self,
        completion: _OwnedCompletionClaim,
    ) -> None:
        self._completion_claims.release_published(
            completion.claim.event.event_id,
            lease_owner=completion.claim.lease_owner,
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
        completion: _OwnedCompletionClaim | None = None,
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
        if (
            completion.user_id != self._user_id
            or completion.conversation_id != self._conversation_id
            or completion.job_id != current.job_id
            or payload.get("job_id") != current.job_id
            or payload.get("status") != current.status.value
        ):
            raise OperationConflictError("视频终态与完成事件身份不一致")
        domain_job = current
        if (
            target_status is ExternalJobStatus.FAILED
            and current.status
            in {
                ExternalJobStatus.TIMEOUT,
                ExternalJobStatus.EXPIRED,
            }
        ):
            domain_job = current.model_copy(
                update={"status": ExternalJobStatus.FAILED}
            )
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


class VideoOperationCompletionHandler:
    """把 M06 完成 Outbox 事件原子回灌到 M11 权威状态。"""

    def __init__(
        self,
        *,
        repository: VideoRuntimeRepository,
        operations: VideoLiveOperationBridge,
        clock: Any,
    ) -> None:
        self._repository = repository
        self._operations = operations
        self._clock = clock

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
        payload = completion_event.payload
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
                    now=self._now(),
                )
            else:
                message = _completion_message(payload)
                retryable = status == "timeout"
                updated = await service.record_scene_failure(
                    state,
                    scene_id=scene_id,
                    error=message,
                    attempts=3 if retryable else 1,
                    retryable=retryable,
                    raw={},
                    operation_port=scoped,
                    now=self._now(),
                )
        elif isinstance(state, VideoPostProductionWorkflowState):
            post_service = VideoPostProductionWorkflowService(scoped)
            if (
                state.current_stage is VideoPostProductionStage.MERGE_VIDEO
                and stage == VideoPostProductionStage.MERGE_VIDEO.value
            ):
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
                        now=self._now(),
                    )
                else:
                    updated = await post_service.record_merge_failure(
                        state,
                        error=_completion_message(payload),
                        attempts=1,
                        retryable=status == "timeout",
                        raw={},
                        operation_port=scoped,
                        now=self._now(),
                    )
            elif (
                state.current_stage is VideoPostProductionStage.QUALITY_REVIEW
                and stage == VideoPostProductionStage.QUALITY_REVIEW.value
            ):
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
                            summary_markdown=str(
                                result.get("summary_markdown") or ""
                            ),
                            quality_report_markdown=str(
                                result.get("quality_report_markdown") or ""
                            ),
                            issues=list(result.get("issues") or []),
                            affected_scene_ids=list(
                                result.get("affected_scene_ids") or []
                            ),
                            revision_prompt=str(
                                result.get("revision_prompt") or ""
                            ),
                            task_id=_optional_text(
                                payload.get("provider_job_id")
                            ),
                            raw=dict(raw),
                        ),
                        operation_port=scoped,
                        now=self._now(),
                    )
                else:
                    updated = await post_service.record_quality_failure(
                        state,
                        error=_completion_message(payload),
                        attempts=1,
                        retryable=status == "timeout",
                        raw={},
                        operation_port=scoped,
                        now=self._now(),
                    )
            else:
                raise OperationConflictError("后处理完成事件与当前阶段不一致")
        elif (
            isinstance(state, VideoDeliveryWorkflowState)
            and stage == "jianying_draft"
        ):
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
                    "storyboard_version_id": request.get(
                        "storyboard_version_id"
                    ),
                    "download_url": result_data.get("download_url"),
                    "file_name": result_data.get("file_name"),
                    "expire_at": result_data.get("expire_at"),
                    "message": (
                        result_data.get("message")
                        if status == "succeeded"
                        else _completion_message(payload)
                    ),
                }
            )
            updated = await VideoDeliveryWorkflowService(scoped).record_jianying_result(
                state,
                normalized,
                operation_port=scoped,
                now=self._now(),
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
        await self._repository.commit_operation_completion(
            completion.claim,
            user_id=completion.user_id,
            workflow_state=updated_envelope,
            workflow=workflow,
            expected_workflow_version=envelope.workflow_version,
            messages=(),
            occurred_at=self._now(),
        )
        self._operations._release_published_completion(completion)

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
        identity = "\0".join(
            (workflow_id, str(stage_version), scene_id, str(attempt))
        )
        return f"pf:video-scene:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    if stage in {"merge_video", "quality_review"}:
        identity = "|".join(
            (workflow_id, stage, str(stage_version), str(attempt), request_hash)
        )
        return f"pf:video-post:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    if stage == "jianying_draft":
        return (
            f"video:{workflow_id}:jianying_draft:v{stage_version}:"
            f"a{attempt}:{request_hash}"
        )
    return build_operation_idempotency_key(
        workflow_id,
        stage,
        stage_version,
        attempt,
    )


def _hash_operation_request(provider_request: object) -> str:
    from pixelflow.agent_runtime.jobs.identity import hash_operation_request

    return hash_operation_request(provider_request)


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


__all__ = [
    "TransientCredentialVault",
    "VideoLiveOperationBridge",
    "VideoOperationAdapterResolver",
    "VideoOperationCompletionHandler",
    "VideoOperationStartRequest",
]
