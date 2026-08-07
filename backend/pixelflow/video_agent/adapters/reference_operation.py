"""把参考视频工具接入M06 External Job Operation。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import JsonValue

from pixelflow.agent_runtime.contracts import ExternalJobStatus
from pixelflow.agent_runtime.jobs import (
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.video_agent.tools.reference import (
    ReferenceAnalysisOperationJob,
)
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
)

_TERMINAL_FAILURES = frozenset(
    {
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }
)


class M06ReferenceAnalysisOperationPort:
    """用M06幂等启动参考视频任务，并从完成事件恢复安全分镜。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        adapter: ProviderJobAdapter,
        authorization_provider: Callable[[VideoToolContext], str] | None = None,
        lease_owner: str,
        clock: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(adapter, ProviderJobAdapter):
            raise TypeError("adapter 必须是 ProviderJobAdapter")
        if authorization_provider is not None and not callable(
            authorization_provider
        ):
            raise TypeError("authorization_provider 必须可调用")
        normalized_owner = lease_owner.strip()
        if not normalized_owner or len(normalized_owner) > 128:
            raise ValueError("lease_owner 必须是1到128个字符")
        self._repository = repository
        self._adapter = adapter
        self._authorization_provider = (
            authorization_provider or _context_authorization
        )
        self._lease_owner = normalized_owner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory

    async def start_reference_analysis(
        self,
        context: VideoToolContext,
        *,
        artifact_ref: str,
        video_url: str,
        attempt: int,
    ) -> ReferenceAnalysisOperationJob:
        """启动或回读同一Operation，凭据只存在于本次调用栈。"""

        if context.plan_id is None or context.step_id is None:
            raise VideoToolExecutionError("参考视频Operation缺少计划身份")
        provider_request: dict[str, JsonValue] = {"video_url": video_url}
        stage_digest = hashlib.sha256(artifact_ref.encode()).hexdigest()[:16]
        request = build_operation_request(
            workflow_id=context.plan_id,
            stage=f"analyze_reference:{stage_digest}",
            stage_version=1,
            attempt=attempt,
            provider_request=provider_request,
        )
        coordinator = OperationStartCoordinator(
            self._repository,
            adapter=self._adapter,
            user_id=context.user_id,
            conversation_id=context.workspace.conversation_id,
            clock=self._clock,
            job_id_factory=self._job_id_factory,
        )
        try:
            operation = await coordinator.start(
                request,
                provider_request=provider_request,
                authorization_provider=lambda: self._authorization_provider(
                    context
                ),
                lease_owner=self._lease_owner,
            )
        except OperationStartQuotaPausedError as exc:
            return ReferenceAnalysisOperationJob(
                job_id=exc.operation.job_id,
                artifact_ref=artifact_ref,
                status="start_paused_quota",
            )
        except OperationConflictError as exc:
            raise VideoToolExecutionError("参考视频Operation启动失败") from exc

        if operation.status in {ExternalJobStatus.CREATED, ExternalJobStatus.POLLING}:
            return ReferenceAnalysisOperationJob(
                job_id=operation.job_id,
                artifact_ref=artifact_ref,
                status="polling",
            )
        if operation.status in _TERMINAL_FAILURES:
            raise VideoToolExecutionError("参考视频Operation执行失败")
        if operation.status is not ExternalJobStatus.SUCCEEDED:
            raise VideoToolExecutionError("参考视频Operation状态不受支持")
        storyboard = await self._completed_storyboard(
            context,
            job_id=operation.job_id,
        )
        return ReferenceAnalysisOperationJob(
            job_id=operation.job_id,
            artifact_ref=artifact_ref,
            status="succeeded",
            storyboard=storyboard,
        )

    async def _completed_storyboard(
        self,
        context: VideoToolContext,
        *,
        job_id: str,
    ) -> tuple[dict[str, JsonValue], ...]:
        events = await self._repository.list_events(
            context.user_id,
            context.workspace.conversation_id,
        )
        matches = [event for event in events if event.payload.get("job_id") == job_id]
        if len(matches) != 1:
            raise VideoToolExecutionError("参考视频Operation完成事件不唯一")
        result = matches[0].payload.get("result")
        if not isinstance(result, Mapping):
            raise VideoToolExecutionError("参考视频Operation缺少安全结果")
        raw_storyboard = result.get("storyboard", result.get("shots"))
        if not isinstance(raw_storyboard, (list, tuple)):
            raise VideoToolExecutionError("参考视频Operation结果缺少分镜")
        storyboard = tuple(
            dict(item) for item in raw_storyboard if isinstance(item, Mapping)
        )
        if not storyboard or len(storyboard) != len(raw_storyboard):
            raise VideoToolExecutionError("参考视频Operation分镜格式无效")
        return storyboard


def _context_authorization(context: VideoToolContext) -> str:
    """从当前执行上下文借用凭据，不在Operation Adapter中缓存。"""

    if context.credential is None:
        raise VideoToolExecutionError("参考视频Operation缺少临时授权")
    try:
        return context.credential.borrow_authorization()
    except RuntimeError as exc:
        raise VideoToolExecutionError("参考视频Operation缺少临时授权") from exc
