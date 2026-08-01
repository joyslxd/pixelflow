"""连接视频 live Workflow 与 M06 External Job Coordinator。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
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
    provider_request: dict[str, JsonValue] = Field(repr=False)

    @model_validator(mode="before")
    @classmethod
    def validate_provider_request_credentials(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _ensure_provider_request_has_no_credentials(value.get("provider_request"))
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

        normalized = EventDeliveryClaim.model_validate(claim.model_dump(mode="python"))
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
        expired = [event_id for event_id, entry in self._claims.items() if entry.claim.lease_expires_at <= now]
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
                retryable = status in {"timeout", "expired"}
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
                        now=self._now(),
                    )
                else:
                    updated = await post_service.record_merge_failure(
                        state,
                        error=_completion_message(payload),
                        attempts=1,
                        retryable=status in {"timeout", "expired"},
                        raw={},
                        operation_port=scoped,
                        now=self._now(),
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
                        now=self._now(),
                    )
                else:
                    updated = await post_service.record_quality_failure(
                        state,
                        error=_completion_message(payload),
                        attempts=1,
                        retryable=status in {"timeout", "expired"},
                        raw={},
                        operation_port=scoped,
                        now=self._now(),
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
                artifact.update(
                    {
                        "type": "video_result",
                        "title": "场景视频生成未完成",
                        "description": "部分场景视频生成失败，请查看原因后重试。",
                        "actionLabel": "重新生成场景视频",
                    }
                )
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
        await self._repository.commit_operation_completion(
            completion.claim,
            user_id=completion.user_id,
            workflow_state=updated_envelope,
            workflow=workflow,
            expected_workflow_version=envelope.workflow_version,
            messages=messages,
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


_CREDENTIAL_KEY_PARTS = frozenset({"authorization", "secret", "password", "credential"})
_DIRECT_CREDENTIAL_KEY_PARTS = frozenset({"auth", "bearer", "jwt"})
_DIRECT_CREDENTIAL_QUALIFIERS = frozenset({"header", "value", "credential", "token", "key"})
_TOKEN_CREDENTIAL_PREFIXES = frozenset({"access", "refresh", "auth", "bearer", "client", "session", "id", "api", "provider"})
_TOKEN_METADATA_SUFFIXES = frozenset({"count", "counts", "budget", "limit", "length", "usage", "estimate", "hint"})
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(?:\b(?:authorization|(?:access|refresh|auth|bearer|client|session|id|api|provider)"
    r"[\s_-]*token|token|api[\s_-]*key|secret|password|credential)\b\s*[:=]\s*"
    r"(?:bearer\s+)?\S+|\bbearer\s+[a-z0-9._~+/=-]{6,})",
    re.IGNORECASE,
)
_JWT_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_API_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"\b(?:sk|rk|pk|api)[-_][A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)


def _ensure_provider_request_has_no_credentials(value: object) -> None:
    """递归拒绝供应商规范请求中的凭据键和值，错误只返回固定摘要。"""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and _is_sensitive_provider_request_key(key):
                raise ValueError("Provider 请求包含敏感凭据")
            _ensure_provider_request_has_no_credentials(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _ensure_provider_request_has_no_credentials(child)
        return
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFKC", value)
        if (
            _CREDENTIAL_VALUE_PATTERN.search(normalized)
            or _JWT_CREDENTIAL_VALUE_PATTERN.search(normalized)
            or _API_CREDENTIAL_VALUE_PATTERN.search(normalized)
        ):
            raise ValueError("Provider 请求包含敏感凭据")


def _is_sensitive_provider_request_key(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    parts = re.findall(r"[a-z0-9]+", camel_split.casefold())
    if any(part in _CREDENTIAL_KEY_PARTS for part in parts):
        return True
    direct_parts = set(parts) & _DIRECT_CREDENTIAL_KEY_PARTS
    if direct_parts and (
        len(parts) == 1 or set(parts) & _DIRECT_CREDENTIAL_QUALIFIERS
    ):
        return True
    if "apikey" in parts or any(first == "api" and second == "key" for first, second in zip(parts, parts[1:], strict=False)):
        return True
    for index, part in enumerate(parts):
        if part != "token":
            continue
        previous = parts[index - 1] if index > 0 else None
        following = parts[index + 1] if index + 1 < len(parts) else None
        if previous in _TOKEN_CREDENTIAL_PREFIXES:
            return True
        if following not in _TOKEN_METADATA_SUFFIXES:
            return True
    collapsed = "".join(parts)
    direct = r"(?:auth|bearer|jwt)"
    qualifier = r"(?:header|value|credential|token|key)"
    if re.fullmatch(
        rf"(?:{direct}|{direct}{qualifier}|{qualifier}{direct})",
        collapsed,
    ):
        return True
    return bool(
        re.fullmatch(
            r"(?:authorization|api(?:key)|secret|password|credential|"
            r"(?:access|refresh|auth|bearer|client|session|id|api|provider)token(?:value)?)",
            collapsed,
        )
    )


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
        payload={"artifact": normalized_artifact},
        created_at=normalized_event.occurred_at,
    )


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
            return "场景视频生成未完成，请查看失败原因后重试。"
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
    "VideoOperationStartRequest",
]
