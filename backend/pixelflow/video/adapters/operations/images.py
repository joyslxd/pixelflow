"""M06 图片资产生成批次：按资产槽位启动、轮询并回写 Workspace。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import JsonValue

from pixelflow.agent_control_plane.contracts import ExternalJobStatus
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolExecutionError
from pixelflow.agent_tools.video.credential_store import TransientBatchCredentialStore
from pixelflow.operations.jobs import (
    MAX_CHILD_OPERATIONS_PER_BATCH,
    OperationBatchRecord,
    OperationBatchRepository,
    OperationStartCoordinator,
    OperationStartQuotaPausedError,
    ProviderJobAdapter,
    build_operation_batch_plan,
    build_operation_request,
)
from pixelflow.operations.ports import OperationConflictError
from pixelflow.video.services.production_fields import workspace_resolved_aspect_ratio
from pixelflow.video.workspace.repository import VideoWorkspaceRepository

logger = logging.getLogger(__name__)


class ImageGenerationJob:
    def __init__(self, *, job_id: str, asset_id: str, status: str, artifact_ref: str | None = None, image_url: str | None = None) -> None:
        self.job_id, self.asset_id, self.status = job_id, asset_id, status
        self.artifact_ref, self.image_url = artifact_ref, image_url


class M06ImageGenerationOperationPort:
    """把一项图片资产转为通用 M06 Operation；授权仅在 start 时借用。"""

    def __init__(self, *, repository: AgentRuntimeRepository, adapter: ProviderJobAdapter, lease_owner: str, provider_request_transformer: Any = None) -> None:
        self._repository, self._adapter, self._lease_owner = repository, adapter, lease_owner
        self._transformer = provider_request_transformer

    async def start_asset(self, context: VideoToolContext, *, asset: Mapping[str, JsonValue], attempt: int, workflow_id: str, expected_operation_idempotency_key: str) -> ImageGenerationJob:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            raise VideoToolExecutionError("图片资产缺少 asset_id")
        request = _image_generation_request(context.workspace.payload, asset)
        if self._transformer is not None:
            request = self._transformer(request)
        stage = _image_operation_stage(asset_id)
        operation_request = build_operation_request(
            workflow_id=workflow_id,
            stage=stage,
            stage_version=1,
            attempt=attempt,
            provider_request=request,
        )
        if operation_request.idempotency_key != expected_operation_idempotency_key:
            raise VideoToolExecutionError("图片资产批次子项幂等身份不一致")
        coordinator = OperationStartCoordinator(self._repository, adapter=self._adapter, user_id=context.user_id, conversation_id=context.workspace.conversation_id)
        try:
            operation = await coordinator.start(
                operation_request,
                provider_request=request,
                authorization_provider=lambda: context.credential.borrow_authorization() if context.credential else "",
                lease_owner=self._lease_owner,
            )
        except OperationStartQuotaPausedError as exc:
            return ImageGenerationJob(job_id=exc.operation.job_id, asset_id=asset_id, status="paused_quota")
        except (OperationConflictError, ValueError) as exc:
            raise VideoToolExecutionError("图片资产 Operation 启动失败") from exc
        if operation.status in {ExternalJobStatus.CREATED, ExternalJobStatus.POLLING}:
            return ImageGenerationJob(job_id=operation.job_id, asset_id=asset_id, status="polling")
        if operation.status is ExternalJobStatus.SUCCEEDED:
            return ImageGenerationJob(job_id=operation.job_id, asset_id=asset_id, status="succeeded")
        return ImageGenerationJob(job_id=operation.job_id, asset_id=asset_id, status="failed")


def _image_generation_request(
    workspace_payload: Mapping[str, object],
    asset: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """构造图片 Provider 请求：画幅归属工作区，Prompt 归属资产注册表。"""

    asset_id = str(asset.get("asset_id") or "").strip()
    prompt = str(asset.get("generation_prompt") or "").strip()
    if not asset_id or not prompt:
        raise VideoToolExecutionError("图片资产缺少 asset_id 或 generation_prompt")
    # 图片需要与创意/视频生产的画幅一致；未确认时保守回退为方图，不沿用资产侧旧 ratio。
    ratio = workspace_resolved_aspect_ratio(workspace_payload) or "1:1"
    return {
        "generation_mode": str(asset.get("generation_mode") or "text_to_image"),
        "asset_id": asset_id,
        # 资产注册表是唯一 Prompt 来源，创意 Brief 和生产合同不得拼接到图片 Prompt。
        "prompt": prompt,
        "model": str(asset.get("model") or "seeddream-5.0"),
        # 默认 2K；只有未来接入模型能力档案后才可将已声明支持 4K 的模型提升到 4K。
        "size": "2k",
        "ratio": ratio,
        "reference_image_urls": asset.get("reference_image_urls") or [],
    }


def _image_operation_stage(asset_id: str) -> str:
    """图片子项与 M5 批次计划共用同一 Operation stage 身份。"""

    digest = hashlib.sha256(asset_id.encode()).hexdigest()[:12]
    return f"generate_image_asset:{digest}:v1"


class M06ImageGenerationBatchDispatcher:
    def __init__(self, *, batch_repository: OperationBatchRepository, operation_port: M06ImageGenerationOperationPort, max_concurrent: int = 6) -> None:
        if not 1 <= max_concurrent <= 6:
            raise ValueError("图片批次并发槽位必须在 1 到 6 之间")
        self._batches, self._operations, self._max = batch_repository, operation_port, max_concurrent

    async def dispatch_start_slots(self, *, batch: OperationBatchRecord, context: VideoToolContext, assets_by_id: Mapping[str, Mapping[str, JsonValue]], attempt: int) -> dict[str, ImageGenerationJob]:
        jobs: dict[str, ImageGenerationJob] = {}
        for child in await self._batches.claim_children(batch_id=batch.batch_id, max_concurrent=self._max):
            try:
                asset = assets_by_id.get(child.scene_id)
                if asset is None:
                    job = ImageGenerationJob(job_id=child.operation_idempotency_key, asset_id=child.scene_id, status="failed")
                else:
                    job = await self._operations.start_asset(context, asset=asset, attempt=attempt, workflow_id=batch.batch_id, expected_operation_idempotency_key=child.operation_idempotency_key)
            except Exception as exc:  # noqa: BLE001 - 子项失败必须收口，不能遗留 starting。
                logger.warning(
                    "image_batch_child_start_failed batch_id=%s child_key=%s error_type=%s",
                    batch.batch_id,
                    child.operation_idempotency_key,
                    type(exc).__name__,
                )
                job = ImageGenerationJob(
                    job_id=child.operation_idempotency_key,
                    asset_id=child.scene_id,
                    status="failed",
                )
            if job.status == "succeeded":
                await self._batches.mark_child_terminal(batch_id=batch.batch_id, child_key=child.operation_idempotency_key, status="succeeded", job_id=job.job_id)
            elif job.status == "polling":
                await self._batches.mark_child_polling(batch_id=batch.batch_id, child_key=child.operation_idempotency_key, job_id=job.job_id)
            else:
                await self._batches.mark_child_terminal(batch_id=batch.batch_id, child_key=child.operation_idempotency_key, status="failed", job_id=job.job_id)
            jobs[child.operation_idempotency_key] = job
        return jobs


class M06ImageGenerationBatchOperationPort:
    def __init__(self, *, batch_repository: OperationBatchRepository, credential_store: TransientBatchCredentialStore | None = None) -> None:
        self._batches, self._credentials = batch_repository, credential_store

    async def create_or_read_batches(self, context: VideoToolContext, *, assets: list[Mapping[str, JsonValue]], attempt: int) -> tuple[tuple[str, tuple[ImageGenerationJob, ...]], ...]:
        if context.run_id is None or context.tool_call_id is None:
            raise VideoToolExecutionError("图片生成批次缺少冻结 Run 绑定")
        results = []
        stage = "batch_plan"
        try:
            for index in range(0, len(assets), MAX_CHILD_OPERATIONS_PER_BATCH):
                group = assets[index:index + MAX_CHILD_OPERATIONS_PER_BATCH]
                plan = build_operation_batch_plan(
                    run_id=context.run_id,
                    tool_call_id=context.tool_call_id,
                    scene_ids=tuple(str(x.get("asset_id") or "") for x in group),
                    variant_count=1,
                    attempt=attempt,
                    batch_index=index // MAX_CHILD_OPERATIONS_PER_BATCH + 1,
                    stage_prefix="generate_image_asset",
                )
                stage = "batch_persist"
                batch = await self._batches.create_or_read(
                    user_id=context.user_id,
                    conversation_id=context.workspace.conversation_id,
                    workspace_id=context.workspace.workspace_id,
                    plan=plan,
                    run_id=context.run_id,
                    tool_call_id=context.tool_call_id,
                    attempt=attempt,
                    source_workspace_revision=context.workspace.revision,
                )
                stage = "credential_handoff"
                if self._credentials and context.credential:
                    await self._credentials.put(batch_id=batch.batch_id, authorization=context.credential.borrow_authorization())
                results.append((batch.batch_id, tuple(ImageGenerationJob(job_id=c.job_id or c.operation_idempotency_key, asset_id=c.scene_id, status="polling" if c.job_id else "queued") for c in batch.children)))
        except ValueError as error:
            # 仅记录受控阶段与异常类型；提示词、资产身份、授权和 Provider 数据不进日志。
            logger.warning(
                "image_batch_submission_failed run_id=%s stage=%s error_type=%s",
                context.run_id,
                stage,
                type(error).__name__,
            )
            raise
        return tuple(results)


class M06ImageGenerationBatchDispatcherWorker:
    """Gateway 生命周期 Worker：重启后继续领取 queued 图片资产。"""

    def __init__(
        self,
        *,
        batch_repository: OperationBatchRepository,
        video_repository: VideoWorkspaceRepository,
        dispatcher: M06ImageGenerationBatchDispatcher,
        credential_store: TransientBatchCredentialStore,
        worker_id: str,
        scan_interval: timedelta = timedelta(seconds=1),
    ) -> None:
        self._batches, self._videos, self._dispatcher, self._credentials = batch_repository, video_repository, dispatcher, credential_store
        self._worker_id, self._scan_interval = worker_id, scan_interval
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def run_once(self) -> int:
        dispatched = 0
        for batch in await self._batches.list_dispatchable_batches(limit=100):
            credential = await self._credentials.get(batch_id=batch.batch_id)
            if credential is None:
                # 授权仅允许进程内短暂保存。进程重启后，已领取但尚未启动的
                # 子项无法安全重放，必须转失败而不是永久卡在 starting。
                stale_children = tuple(child for child in batch.children if child.status == "starting")
                for child in stale_children:
                    await self._batches.mark_child_terminal(
                        batch_id=batch.batch_id,
                        child_key=child.operation_idempotency_key,
                        status="failed",
                        job_id=child.job_id or child.operation_idempotency_key,
                    )
                if stale_children:
                    logger.warning(
                        "image_batch_starting_credential_missing batch_id=%s children=%s",
                        batch.batch_id,
                        len(stale_children),
                    )
                continue
            if batch.run_id is None or batch.tool_call_id is None or batch.attempt is None:
                logger.warning("image_batch_dispatch_identity_missing batch_id=%s", batch.batch_id)
                continue
            workspace = await self._videos.get_workspace(batch.user_id, batch.workspace_id)
            if workspace is None or workspace.conversation_id != batch.conversation_id:
                continue
            payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
            assets = {str(item.get("asset_id") or "").strip(): item for item in payload.get("asset_registry", []) if isinstance(item, Mapping)}
            try:
                await self._dispatcher.dispatch_start_slots(
                    batch=batch,
                    context=VideoToolContext(
                        user_id=batch.user_id,
                        workspace=workspace,
                        run_id=batch.run_id,
                        tool_call_id=batch.tool_call_id,
                        credential=credential,
                    ),
                    assets_by_id=assets,
                    attempt=batch.attempt,
                )
                dispatched += 1
            except Exception as exc:  # noqa: BLE001 - 单批次异常不得中断其它批次扫描。
                logger.warning(
                    "image_batch_dispatch_failed batch_id=%s error_type=%s",
                    batch.batch_id,
                    type(exc).__name__,
                )
        return dispatched

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"image-batch-dispatch:{self._worker_id}")

    async def aclose(self) -> None:
        self._closed = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while not self._closed:
            try:
                await self.run_once()
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._scan_interval.total_seconds())
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(self._scan_interval.total_seconds())


__all__ = ["ImageGenerationJob", "M06ImageGenerationOperationPort", "M06ImageGenerationBatchDispatcher", "M06ImageGenerationBatchOperationPort", "M06ImageGenerationBatchDispatcherWorker"]
