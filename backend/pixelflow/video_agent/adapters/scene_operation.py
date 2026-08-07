"""把VideoAgent定向镜头生成接入M06 External Job Operation。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import JsonValue, ValidationError

from pixelflow.agent_runtime.contracts import ExternalJobStatus
from pixelflow.agent_runtime.jobs import (
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
)
from pixelflow.video_agent.tools.scene import SceneGenerationJob

SceneProviderRequestBuilder = Callable[
    [Mapping[str, JsonValue], int],
    Mapping[str, JsonValue],
]
_TERMINAL_FAILURES = frozenset(
    {
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }
)


class M06SceneGenerationOperationPort:
    """按镜头和版本幂等启动M06任务，并从完成事件恢复产物。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        adapter: ProviderJobAdapter,
        authorization_provider: Callable[[VideoToolContext], str] | None = None,
        lease_owner: str,
        provider_request_builder: SceneProviderRequestBuilder | None = None,
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
        self._provider_request_builder = (
            provider_request_builder or _default_provider_request
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory

    async def start_scene_variant(
        self,
        context: VideoToolContext,
        *,
        scene: Mapping[str, JsonValue],
        variant_index: int,
        attempt: int,
    ) -> SceneGenerationJob:
        """启动或回读同一镜头版本，Authorization不进入对象或持久层。"""

        if context.plan_id is None or context.step_id is None:
            raise VideoToolExecutionError("镜头生成Operation缺少计划身份")
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            raise VideoToolExecutionError("镜头生成Operation缺少镜头身份")
        provider_request = dict(
            self._provider_request_builder(scene, variant_index)
        )
        scene_digest = hashlib.sha256(scene_id.encode()).hexdigest()[:12]
        request = build_operation_request(
            workflow_id=context.plan_id,
            stage=f"generate_scene:{scene_digest}:v{variant_index}",
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
            return SceneGenerationJob(
                job_id=exc.operation.job_id,
                scene_id=scene_id,
                variant_index=variant_index,
                status="start_paused_quota",
            )
        except OperationConflictError as exc:
            raise VideoToolExecutionError("镜头生成Operation启动失败") from exc

        if operation.status in {ExternalJobStatus.CREATED, ExternalJobStatus.POLLING}:
            return SceneGenerationJob(
                job_id=operation.job_id,
                scene_id=scene_id,
                variant_index=variant_index,
                status="polling",
            )
        if operation.status in _TERMINAL_FAILURES:
            raise VideoToolExecutionError("镜头生成Operation执行失败")
        if operation.status is not ExternalJobStatus.SUCCEEDED:
            raise VideoToolExecutionError("镜头生成Operation状态不受支持")
        return await self._completed_job(
            context,
            operation_job_id=operation.job_id,
            scene_id=scene_id,
            variant_index=variant_index,
        )

    async def _completed_job(
        self,
        context: VideoToolContext,
        *,
        operation_job_id: str,
        scene_id: str,
        variant_index: int,
    ) -> SceneGenerationJob:
        events = await self._repository.list_events(
            context.user_id,
            context.workspace.conversation_id,
        )
        matches = [
            event
            for event in events
            if event.payload.get("job_id") == operation_job_id
        ]
        if len(matches) != 1:
            raise VideoToolExecutionError("镜头生成Operation完成事件不唯一")
        result = matches[0].payload.get("result")
        if not isinstance(result, Mapping):
            raise VideoToolExecutionError("镜头生成Operation缺少安全结果")
        try:
            job = SceneGenerationJob.model_validate(
                {
                    "job_id": operation_job_id,
                    "scene_id": scene_id,
                    "variant_index": variant_index,
                    "status": "succeeded",
                    "variant_id": result.get("variant_id"),
                    "artifact_ref": result.get("artifact_ref"),
                    "video_url": result.get("video_url"),
                    "completed_at": (
                        result.get("completed_at") or matches[0].occurred_at
                    ),
                }
            )
        except ValidationError as exc:
            raise VideoToolExecutionError("镜头生成Operation结果无效") from exc
        return job


def _default_provider_request(
    scene: Mapping[str, JsonValue],
    variant_index: int,
) -> Mapping[str, JsonValue]:
    """只把生成所需公开字段交给供应商Client，排除历史版本和质检证据。"""

    scene_id = str(scene.get("scene_id") or "").strip()
    prompt = str(scene.get("prompt") or scene.get("storyline") or "").strip()
    if not scene_id or not prompt:
        raise VideoToolExecutionError("镜头生成请求缺少镜头或提示词")
    request: dict[str, JsonValue] = {
        "scene_id": scene_id,
        "variant_index": variant_index,
        "prompt": prompt,
    }
    for key in (
        "duration_sec",
        "shot_type",
        "camera_movement",
        "narration",
        "narration_text",
        "onscreen_text",
        "asset_refs",
        "generation_mode",
        "model",
        "ratio",
        "size",
        "sound",
        "image_urls",
        "video_urls",
        "audio_urls",
    ):
        value = scene.get(key)
        if value is not None:
            request[key] = value
    return request


def _context_authorization(context: VideoToolContext) -> str:
    """从当前执行上下文借用凭据，不在Operation Adapter中缓存。"""

    if context.credential is None:
        raise VideoToolExecutionError("镜头生成Operation缺少临时授权")
    try:
        return context.credential.borrow_authorization()
    except RuntimeError as exc:
        raise VideoToolExecutionError("镜头生成Operation缺少临时授权") from exc
