"""GenerationJob 提交 Service：一次 Tool 调用对应一个或多个独立任务。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import JsonValue

from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolExecutionError
from pixelflow.capabilities.image_generation.port import ImageGenerationProvider
from pixelflow.capabilities.video_generation.port import VideoGenerationProvider
from pixelflow.generation_jobs.providers import ProviderJobMappingError
from pixelflow.video.services.production_fields import workspace_resolved_aspect_ratio

from .contracts import GenerationJobKind, GenerationJobRecord, GenerationJobStatus
from .credentials import TransientGenerationJobCredentialStore
from .repository import GenerationJobRepository
from .requests import build_scene_generation_request


@dataclass(frozen=True, slots=True)
class GenerationJobSubmission:
    """Tool 可见的安全任务引用，不包含用户授权或 Provider 原始响应。"""

    job_id: str
    item_id: str
    kind: GenerationJobKind
    status: GenerationJobStatus
    provider_job_id: str | None = None
    variant_index: int = 1


class GenerationJobService:
    """把 Workspace 生成意图落为单一 GenerationJob，不再创建批次或子任务编排。"""

    def __init__(
        self,
        *,
        repository: GenerationJobRepository,
        credential_store: TransientGenerationJobCredentialStore,
        image_provider: ImageGenerationProvider | None = None,
        video_provider: VideoGenerationProvider | None = None,
        video_request_builder: Callable[
            [VideoToolContext, Mapping[str, JsonValue], int], Mapping[str, JsonValue]
        ]
        | None = None,
        job_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._credentials = credential_store
        self._image_provider = image_provider
        self._video_provider = video_provider
        self._video_request_builder = video_request_builder
        self._job_id_factory = job_id_factory or (lambda: uuid.uuid4().hex)

    @property
    def image_available(self) -> bool:
        """返回图片 Provider 是否已装配。"""

        return self._image_provider is not None

    @property
    def video_available(self) -> bool:
        """返回视频 Provider 是否已装配。"""

        return self._video_provider is not None

    async def submit_images(
        self,
        context: VideoToolContext,
        *,
        assets: Sequence[Mapping[str, object]],
        attempt: int,
    ) -> tuple[GenerationJobSubmission, ...]:
        """为每个图片资产创建一个独立 GenerationJob。"""

        if self._image_provider is None:
            raise VideoToolExecutionError("图片生成 Provider 尚未装配")
        self._require_billable_context(context)
        return await self._submit_many(
            context,
            kind=GenerationJobKind.IMAGE,
            items=assets,
            variant_count=1,
            attempt=attempt,
            item_key="asset_id",
            request_builder=lambda item, _index: _image_request(
                context.workspace.payload,
                item,
            ),
            provider=self._image_provider,
        )

    async def submit_videos(
        self,
        context: VideoToolContext,
        *,
        scenes: Sequence[Mapping[str, object]],
        variant_count: int,
        attempt: int,
    ) -> tuple[GenerationJobSubmission, ...]:
        """为每个镜头版本创建一个独立 GenerationJob。"""

        if self._video_provider is None:
            raise VideoToolExecutionError("视频生成 Provider 尚未装配")
        if not 1 <= variant_count <= 3:
            raise VideoToolExecutionError("视频生成版本数必须在 1 到 3 之间")
        self._require_billable_context(context)
        builder = self._video_request_builder or build_scene_generation_request
        pairs = tuple(
            (scene, variant)
            for scene in scenes
            for variant in range(1, variant_count + 1)
        )
        return await self._submit_many(
            context,
            kind=GenerationJobKind.VIDEO,
            items=pairs,
            variant_count=variant_count,
            attempt=attempt,
            item_key="scene_id",
            request_builder=lambda pair, _index: builder(
                context,
                pair[0],
                pair[1],
            ),
            provider=self._video_provider,
            paired_items=True,
        )

    async def _submit_many(
        self,
        context: VideoToolContext,
        *,
        kind: GenerationJobKind,
        items: Sequence[object],
        variant_count: int,
        attempt: int,
        item_key: str,
        request_builder: Callable[[object, int], Mapping[str, JsonValue]],
        provider: ImageGenerationProvider | VideoGenerationProvider,
        paired_items: bool = False,
    ) -> tuple[GenerationJobSubmission, ...]:
        if not items:
            raise VideoToolExecutionError("生成请求缺少待处理项目")
        if not 1 <= attempt <= 10:
            raise VideoToolExecutionError("生成请求 attempt 必须在 1 到 10 之间")
        now = _now()
        submissions: list[GenerationJobSubmission] = []
        for index, item in enumerate(items):
            source, variant_index = (item, 1)
            if paired_items:
                source, variant_index = item  # type: ignore[misc]
            if not isinstance(source, Mapping):
                raise VideoToolExecutionError("生成项目身份无效")
            item_id = str(source.get(item_key) or "").strip()
            if not item_id:
                raise VideoToolExecutionError("生成项目缺少稳定身份")
            request = dict(request_builder(item, index))
            try:
                normalized = dict(provider.prepare_operation_request(request))
            except ProviderJobMappingError as exc:
                raise VideoToolExecutionError("视频生成请求未能映射到供应商接口") from exc
            provider_id = str(normalized.get("provider_id") or provider.provider_id).strip()
            profile_version = str(
                normalized.get("provider_profile_version") or provider.profile_version
            ).strip()
            normalized["provider_id"] = provider_id
            normalized["provider_profile_version"] = profile_version
            request_hash = _request_hash(normalized)
            idempotency_key = _idempotency_key(
                context,
                kind=kind,
                item_id=item_id,
                variant_index=variant_index,
                attempt=attempt,
            )
            candidate = GenerationJobRecord(
                generation_job_id="generation-job-" + self._job_id_factory(),
                user_id=context.user_id,
                conversation_id=context.workspace.conversation_id,
                workspace_id=context.workspace.workspace_id,
                kind=kind,
                item_id=item_id,
                variant_index=variant_index,
                status=GenerationJobStatus.QUEUED,
                request_json=normalized,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
                provider_id=provider_id,
                created_at=now,
                updated_at=now,
            )
            record = await self._repository.create_or_read(candidate)
            await self._credentials.put(
                generation_job_id=record.generation_job_id,
                authorization=context.credential.borrow_authorization(),
            )
            submissions.append(
                GenerationJobSubmission(
                    job_id=record.generation_job_id,
                    item_id=record.item_id,
                    kind=record.kind,
                    status=record.status,
                    provider_job_id=record.provider_job_id,
                    variant_index=record.variant_index,
                )
            )
        return tuple(submissions)

    @staticmethod
    def _require_billable_context(context: VideoToolContext) -> None:
        if context.run_id is None or context.tool_call_id is None:
            raise VideoToolExecutionError("生成请求缺少冻结 Run 绑定")
        if context.credential is None:
            raise ValueError("生成请求缺少临时授权")


def _image_request(
    payload: Mapping[str, JsonValue],
    asset: Mapping[str, object],
) -> dict[str, JsonValue]:
    asset_id = str(asset.get("asset_id") or "").strip()
    prompt = str(asset.get("generation_prompt") or "").strip()
    if not asset_id or not prompt:
        raise VideoToolExecutionError("图片资产缺少 asset_id 或 generation_prompt")
    return {
        "generation_mode": str(asset.get("generation_mode") or "text_to_image"),
        "asset_id": asset_id,
        "prompt": prompt,
        "model": str(asset.get("model") or "seeddream-5.0"),
        "size": str(asset.get("size") or "1080p"),
        "ratio": workspace_resolved_aspect_ratio(payload) or "1:1",
        "reference_image_urls": asset.get("reference_image_urls") or [],
    }


def _request_hash(request: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _idempotency_key(
    context: VideoToolContext,
    *,
    kind: GenerationJobKind,
    item_id: str,
    variant_index: int,
    attempt: int,
) -> str:
    source = ":".join(
        (
            "generation:v1",
            context.user_id,
            context.workspace.workspace_id,
            context.run_id or "",
            context.tool_call_id or "",
            kind.value,
            item_id,
            str(variant_index),
            str(attempt),
        )
    )
    return "generation:v1:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


__all__ = ["GenerationJobService", "GenerationJobSubmission"]
