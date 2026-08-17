"""把VideoAgent视频交付接入M06 External Job Operation。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from pydantic import JsonValue, ValidationError

from pixelflow.agent_runtime.contracts import ExternalJobStatus
from pixelflow.agent_runtime.jobs import (
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    ProviderJobCallError,
    build_operation_request,
)
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.jianying_draft import (
    JianyingDraftRequest,
    JianyingDraftScene,
    compute_storyboard_version_id,
)
from pixelflow.video_agent.tools.delivery import (
    DeliveryOperationJob,
    DeliveryOutputType,
)
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
)

logger = logging.getLogger(__name__)

# content-app `/api/video/merge` 为同步终态；14 镜常需数分钟。
# start 租约必须覆盖整段 HTTP，否则合并成功后 finalize 会因「租约无效」丢结果。
_DEFAULT_DELIVERY_START_LEASE = timedelta(hours=1)

_TERMINAL_FAILURES = frozenset(
    {
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }
)


class M06DeliveryOperationPort:
    """按交付类型幂等启动合成或剪映任务，并恢复安全产物。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        merge_adapter: ProviderJobAdapter,
        jianying_adapter: ProviderJobAdapter | None = None,
        lease_owner: str,
        authorization_provider: Callable[[VideoToolContext], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
        start_lease_duration: timedelta | None = None,
    ) -> None:
        if not isinstance(merge_adapter, ProviderJobAdapter):
            raise TypeError("merge_adapter必须是ProviderJobAdapter")
        if jianying_adapter is not None and not isinstance(
            jianying_adapter,
            ProviderJobAdapter,
        ):
            raise TypeError("jianying_adapter必须是ProviderJobAdapter")
        if authorization_provider is not None and not callable(
            authorization_provider
        ):
            raise TypeError("authorization_provider必须可调用")
        normalized_owner = lease_owner.strip()
        if not normalized_owner or len(normalized_owner) > 128:
            raise ValueError("lease_owner必须是1到128个字符")
        self._repository = repository
        self._adapters: dict[DeliveryOutputType, ProviderJobAdapter] = {
            "mp4": merge_adapter,
        }
        if jianying_adapter is not None:
            self._adapters["jianying_package"] = jianying_adapter
        self._authorization_provider = (
            authorization_provider or _context_authorization
        )
        self._lease_owner = normalized_owner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory
        self._start_lease_duration = (
            start_lease_duration
            if start_lease_duration is not None
            else _DEFAULT_DELIVERY_START_LEASE
        )

    async def start_delivery(
        self,
        context: VideoToolContext,
        *,
        output_type: DeliveryOutputType,
        scenes: Sequence[Mapping[str, JsonValue]],
        attempt: int,
    ) -> DeliveryOperationJob:
        """启动或回读同一交付Operation，凭据只存在于当前调用栈。"""

        if context.plan_id is None or context.step_id is None:
            raise VideoToolExecutionError("视频交付Operation缺少计划身份")
        adapter = self._adapters.get(output_type)
        if adapter is None:
            raise VideoToolExecutionError("剪映交付能力尚未配置")
        provider_request = _provider_request(context, output_type, scenes)
        request = build_operation_request(
            workflow_id=context.plan_id,
            stage=f"deliver:{output_type}",
            stage_version=1,
            attempt=attempt,
            provider_request=provider_request,
        )
        coordinator = OperationStartCoordinator(
            self._repository,
            adapter=adapter,
            user_id=context.user_id,
            conversation_id=context.workspace.conversation_id,
            clock=self._clock,
            job_id_factory=self._job_id_factory,
            lease_duration=self._start_lease_duration,
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
            return DeliveryOperationJob(
                job_id=exc.operation.job_id,
                output_type=output_type,
                status="start_paused_quota",
            )
        except OperationConflictError as exc:
            detail = str(exc).strip()[:200] or "未知冲突"
            logger.warning(
                "delivery operation start conflict output_type=%s detail=%s",
                output_type,
                detail,
            )
            raise VideoToolExecutionError(
                f"视频交付Operation启动失败：{detail}"
            ) from exc
        except ProviderJobCallError as exc:
            logger.warning(
                "delivery provider call failed output_type=%s",
                output_type,
                exc_info=True,
            )
            raise VideoToolExecutionError(
                "视频交付合并连接中断：成片可能已在服务端生成，请稍后重试；"
                "若约5分钟必断，需运维将 /api/video/merge 的 proxy_read_timeout 调至≥3600秒"
            ) from exc

        if operation.status in {
            ExternalJobStatus.CREATED,
            ExternalJobStatus.POLLING,
        }:
            return DeliveryOperationJob(
                job_id=operation.job_id,
                output_type=output_type,
                status="polling",
            )
        if operation.status is ExternalJobStatus.TIMEOUT:
            raise VideoToolExecutionError(
                "视频交付合并超时：网关可能在约5分钟切断长连接，成片或已在服务端生成，请重试；"
                "持续失败时需将 /api/video/merge 的 proxy_read_timeout 调至≥3600秒"
            )
        if operation.status in _TERMINAL_FAILURES:
            raise VideoToolExecutionError(
                "视频交付合并失败，请稍后重试或检查分镜视频后重新发起"
            )
        if operation.status is not ExternalJobStatus.SUCCEEDED:
            raise VideoToolExecutionError("视频交付Operation状态不受支持")
        return await self._completed_job(
            context,
            operation_job_id=operation.job_id,
            output_type=output_type,
        )

    async def _completed_job(
        self,
        context: VideoToolContext,
        *,
        operation_job_id: str,
        output_type: DeliveryOutputType,
    ) -> DeliveryOperationJob:
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
            raise VideoToolExecutionError("视频交付Operation完成事件不唯一")
        result = matches[0].payload.get("result")
        if not isinstance(result, Mapping):
            raise VideoToolExecutionError("视频交付Operation缺少安全结果")
        url_field = "video_url" if output_type == "mp4" else "download_url"
        delivery_url = _safe_https_url(result.get(url_field))
        artifact_digest = hashlib.sha256(
            f"{output_type}:{operation_job_id}:{delivery_url}".encode()
        ).hexdigest()[:32]
        try:
            return DeliveryOperationJob.model_validate(
                {
                    "job_id": operation_job_id,
                    "output_type": output_type,
                    "status": "succeeded",
                    "artifact_ref": f"artifact:video-delivery-{artifact_digest}",
                    "delivery_url": delivery_url,
                }
            )
        except ValidationError as exc:
            raise VideoToolExecutionError("视频交付Operation结果无效") from exc


def _provider_request(
    context: VideoToolContext,
    output_type: DeliveryOutputType,
    scenes: Sequence[Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    if not scenes:
        raise VideoToolExecutionError("视频交付Operation缺少镜头")
    resolved_scenes = _resolve_workspace_scenes(
        context.workspace.payload,
        scenes,
    )
    if output_type == "mp4":
        video_params = context.workspace.payload.get("video_params")
        if not isinstance(video_params, Mapping):
            video_params = {}
        contract = context.workspace.payload.get("creation_contract")
        contract_map = contract if isinstance(contract, Mapping) else {}
        model = str(
            video_params.get("model")
            or video_params.get("video_model")
            or contract_map.get("video_model")
            or ""
        ).strip()
        size = str(
            video_params.get("size")
            or video_params.get("video_size")
            or contract_map.get("video_size")
            or ""
        ).strip()
        duration = video_params.get("duration_sec")
        if duration is None:
            duration = video_params.get("video_duration_sec")
        if duration is None:
            duration = contract_map.get("video_duration_sec")
        if (
            not model
            or not size
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration <= 0
        ):
            raise VideoToolExecutionError("视频合并参数无效")
        return {
            "video_urls": [
                scene["video_url"] for scene in resolved_scenes
            ],
            "model": model,
            "size": size,
            "duration": duration,
        }

    try:
        jianying_scenes = [
            JianyingDraftScene(
                scene_id=str(scene.get("scene_id") or ""),
                scene_index=int(scene.get("scene_index") or 0),
                task_id=str(scene.get("task_id") or "") or None,
                video_url=_safe_https_url(scene.get("video_url")),
            )
            for scene in resolved_scenes
        ]
        request = JianyingDraftRequest(
            conversation_id=context.workspace.conversation_id,
            storyboard_version_id=compute_storyboard_version_id(
                jianying_scenes
            ),
            scenes=jianying_scenes,
            project_name=_project_name(context.workspace.payload),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise VideoToolExecutionError("剪映交付参数无效") from exc
    return {"request": request.model_dump(mode="json"), "retry_failed": False}


def _project_name(payload: Mapping[str, object]) -> str:
    for key in ("project_name", "title", "product_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
    return "PixelFlow视频项目"


def _resolve_workspace_scenes(
    payload: Mapping[str, object],
    selected_scenes: Sequence[Mapping[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    """只在Adapter内部把Artifact解析为Provider所需URL，不扩大工具DTO。"""

    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, (list, tuple)):
        raise VideoToolExecutionError("视频交付工作区缺少镜头")
    workspace_scenes = [
        item for item in raw_scenes if isinstance(item, Mapping)
    ]
    resolved: list[dict[str, JsonValue]] = []
    for selected in selected_scenes:
        scene_id = str(selected.get("scene_id") or "").strip()
        variant_id = str(selected.get("variant_id") or "").strip()
        artifact_ref = str(selected.get("artifact_ref") or "").strip()
        source_scene = next(
            (
                item
                for item in workspace_scenes
                if item.get("scene_id") == scene_id
            ),
            None,
        )
        variants = source_scene.get("variants") if source_scene else None
        source_variant = next(
            (
                item
                for item in variants
                if isinstance(item, Mapping)
                and item.get("variant_id") == variant_id
                and item.get("artifact_ref") == artifact_ref
            ),
            None,
        ) if isinstance(variants, (list, tuple)) else None
        if source_variant is None:
            raise VideoToolExecutionError("视频交付Artifact无法解析")
        resolved.append(
            {
                "scene_id": scene_id,
                "scene_index": source_scene.get("scene_index"),
                "variant_id": variant_id,
                "artifact_ref": artifact_ref,
                "task_id": str(
                    source_variant.get("source_job_id") or variant_id
                ),
                "video_url": _safe_https_url(
                    source_variant.get("video_url")
                ),
            }
        )
    return resolved


def _safe_https_url(value: object) -> str:
    if not isinstance(value, str):
        raise VideoToolExecutionError("视频交付地址无效")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise VideoToolExecutionError("视频交付地址无效")
    return value


def _context_authorization(context: VideoToolContext) -> str:
    """从当前执行上下文借用凭据，不在交付Adapter中缓存。"""

    if context.credential is None:
        raise VideoToolExecutionError("视频交付Operation缺少临时授权")
    try:
        return context.credential.borrow_authorization()
    except RuntimeError as exc:
        raise VideoToolExecutionError("视频交付Operation缺少临时授权") from exc
