"""把VideoAgent定向镜头生成接入M06 External Job Operation。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from pydantic import JsonValue, ValidationError

from pixelflow.agent_control_plane.contracts import ExternalJobStatus
from pixelflow.agent_control_plane.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_tools.video.contracts import VideoToolContext, VideoToolExecutionError
from pixelflow.agent_tools.video.credential_store import TransientBatchCredentialStore
from pixelflow.agent_tools.video.scene import SceneGenerationBatchResult, SceneGenerationJob
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
from pixelflow.operations.jobs.providers import ProviderJobCallError
from pixelflow.operations.ports import OperationConflictError
from pixelflow.video.workspace.repository import VideoWorkspaceRepository

logger = logging.getLogger(__name__)

_SEEDANCE_ASSET_REFERENCE_MODELS = frozenset(
    {"seedance-2.0", "seedance-2.0-fast", "seedance-2.0-mini"}
)

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
        provider_request_transformer: Callable[
            [Mapping[str, JsonValue]], Mapping[str, JsonValue]
        ]
        | None = None,
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
        # 未注入自定义 builder 时，从 Workspace creation_contract + 分镜 mentions 组装。
        self._provider_request_builder = provider_request_builder
        self._provider_request_transformer = provider_request_transformer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory

    async def start_scene_variant(
        self,
        context: VideoToolContext,
        *,
        scene: Mapping[str, JsonValue],
        variant_index: int,
        attempt: int,
        workflow_id: str | None = None,
        expected_operation_idempotency_key: str | None = None,
    ) -> SceneGenerationJob:
        """启动或回读同一镜头版本，Authorization不进入对象或持久层。"""

        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            raise VideoToolExecutionError("镜头生成Operation缺少镜头身份")
        if self._provider_request_builder is not None:
            provider_request = dict(
                self._provider_request_builder(scene, variant_index)
            )
        else:
            provider_request = dict(
                build_scene_provider_request(context, scene, variant_index)
            )
        if self._provider_request_transformer is not None:
            provider_request = dict(
                self._provider_request_transformer(provider_request)
            )
        scene_digest = hashlib.sha256(scene_id.encode()).hexdigest()[:12]
        request = build_operation_request(
            workflow_id=workflow_id or context.plan_id or context.run_id or "",
            stage=f"generate_scene:{scene_digest}:v{variant_index}",
            stage_version=1,
            attempt=attempt,
            provider_request=provider_request,
        )
        if (
            expected_operation_idempotency_key is not None
            and request.idempotency_key != expected_operation_idempotency_key
        ):
            raise VideoToolExecutionError("镜头生成批次子项幂等身份不一致")
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
        except ProviderJobCallError as exc:
            raise VideoToolExecutionError(
                "镜头生成供应商调用失败，请检查创作合同、分镜提示词与参考图后重试"
            ) from exc

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
            return SceneGenerationJob.model_validate(
                {
                    "job_id": operation_job_id,
                    "scene_id": scene_id,
                    "variant_index": variant_index,
                    "status": "succeeded",
                    "variant_id": result.get("variant_id"),
                    "artifact_ref": result.get("artifact_ref"),
                    "video_url": result.get("video_url"),
                    "completed_at": result.get("completed_at") or matches[0].occurred_at,
                }
            )
        except ValidationError as exc:
            raise VideoToolExecutionError("镜头生成Operation结果无效") from exc


class M06SceneGenerationBatchDispatcher:
    """领取一个批次内的 start 槽位，并委托既有 M06 单 Operation Port 启动。

    这里是批次与 Provider Adapter 的唯一接线点：Tool 只创建/回读批次，
    Dispatcher 才能取得槽位并接触 Provider。单个批次由构造参数硬限制为最多 6
    个并发子 Operation，未领取的子项保持 queued，等待下一轮 Dispatcher。
    """

    def __init__(
        self,
        *,
        batch_repository: OperationBatchRepository,
        operation_port: M06SceneGenerationOperationPort,
        max_concurrent_child_operations_per_batch: int = 6,
    ) -> None:
        if not 1 <= max_concurrent_child_operations_per_batch <= 6:
            raise ValueError("每批次并发子 Operation 必须在 1 到 6 之间")
        self._batch_repository = batch_repository
        self._operation_port = operation_port
        self._max_concurrent = max_concurrent_child_operations_per_batch

    async def dispatch_start_slots(
        self,
        *,
        batch: OperationBatchRecord,
        context: VideoToolContext,
        scenes_by_id: Mapping[str, Mapping[str, JsonValue]],
        attempt: int,
    ) -> dict[str, SceneGenerationJob]:
        """按槽位启动子 Operation，并把 Job 身份或终态原子回写批次。"""

        claimed = await self._batch_repository.claim_children(
            batch_id=batch.batch_id,
            max_concurrent=self._max_concurrent,
        )
        jobs: dict[str, SceneGenerationJob] = {}
        for child in claimed:
            scene = scenes_by_id.get(child.scene_id)
            if scene is None:
                await self._batch_repository.mark_child_terminal(
                    batch_id=batch.batch_id,
                    child_key=child.operation_idempotency_key,
                    status="failed",
                    job_id=child.operation_idempotency_key,
                )
                jobs[child.operation_idempotency_key] = SceneGenerationJob(
                    job_id=child.operation_idempotency_key,
                    scene_id=child.scene_id,
                    variant_index=child.variant_index,
                    status="failed",
                )
                continue
            try:
                job = await self._operation_port.start_scene_variant(
                    context,
                    scene=scene,
                    variant_index=child.variant_index,
                    attempt=attempt,
                    workflow_id=batch.batch_id,
                    expected_operation_idempotency_key=child.operation_idempotency_key,
                )
            except VideoToolExecutionError:
                await self._batch_repository.mark_child_terminal(
                    batch_id=batch.batch_id,
                    child_key=child.operation_idempotency_key,
                    status="failed",
                    job_id=child.operation_idempotency_key,
                )
                jobs[child.operation_idempotency_key] = SceneGenerationJob(
                    job_id=child.operation_idempotency_key,
                    scene_id=child.scene_id,
                    variant_index=child.variant_index,
                    status="failed",
                )
                continue
            if (
                job.scene_id != child.scene_id
                or job.variant_index != child.variant_index
            ):
                raise VideoToolExecutionError("镜头生成Operation结果身份不一致")
            if job.status == "succeeded":
                await self._batch_repository.mark_child_terminal(
                    batch_id=batch.batch_id,
                    child_key=child.operation_idempotency_key,
                    status="succeeded",
                    job_id=job.job_id,
                )
            else:
                await self._batch_repository.mark_child_polling(
                    batch_id=batch.batch_id,
                    child_key=child.operation_idempotency_key,
                    job_id=job.job_id,
                )
            jobs[child.operation_idempotency_key] = job
        return jobs


class M06SceneGenerationBatchOperationPort:
    """给 generate_scenes 使用的批次 Port，建立双重幂等后交由 M06 Dispatcher。"""

    def __init__(
        self,
        *,
        batch_repository: OperationBatchRepository,
        dispatcher: M06SceneGenerationBatchDispatcher,
        credential_store: TransientBatchCredentialStore | None = None,
    ) -> None:
        self._batch_repository = batch_repository
        self._dispatcher = dispatcher
        self._credential_store = credential_store

    async def create_or_read_batch(
        self,
        context: VideoToolContext,
        *,
        scenes: Sequence[Mapping[str, JsonValue]],
        variant_count: int,
        attempt: int,
    ) -> tuple[str, tuple[SceneGenerationJob, ...]]:
        """创建或回读一个 M06 批次；兼容只选择一个批次的既有调用方。"""

        return await self._create_or_read_batch(
            context,
            scenes=scenes,
            variant_count=variant_count,
            attempt=attempt,
            batch_index=1,
        )

    async def create_or_read_batches(
        self,
        context: VideoToolContext,
        *,
        scenes: Sequence[Mapping[str, JsonValue]],
        variant_count: int,
        attempt: int,
    ) -> tuple[SceneGenerationBatchResult, ...]:
        """按 M06 子 Operation 上限拆批；Agent 不必知道批次数或拆分规则。"""

        if not scenes:
            raise VideoToolExecutionError("镜头生成批次缺少镜头")
        batch_scene_capacity = MAX_CHILD_OPERATIONS_PER_BATCH // variant_count
        if batch_scene_capacity < 1:
            raise VideoToolExecutionError("镜头生成批次版本数无效")
        results: list[SceneGenerationBatchResult] = []
        for offset in range(0, len(scenes), batch_scene_capacity):
            batch_id, jobs = await self._create_or_read_batch(
                context,
                scenes=scenes[offset : offset + batch_scene_capacity],
                variant_count=variant_count,
                attempt=attempt,
                batch_index=(offset // batch_scene_capacity) + 1,
            )
            results.append(SceneGenerationBatchResult(batch_id=batch_id, jobs=jobs))
        return tuple(results)

    async def _create_or_read_batch(
        self,
        context: VideoToolContext,
        *,
        scenes: Sequence[Mapping[str, JsonValue]],
        variant_count: int,
        attempt: int,
        batch_index: int,
    ) -> tuple[str, tuple[SceneGenerationJob, ...]]:
        """创建或回读一个已按上层稳定顺序切分的 M06 批次。"""

        if context.run_id is None or context.tool_call_id is None:
            raise VideoToolExecutionError("镜头生成批次缺少冻结 Run 绑定")
        scene_ids = tuple(str(scene.get("scene_id") or "").strip() for scene in scenes)
        try:
            plan = build_operation_batch_plan(
                run_id=context.run_id,
                tool_call_id=context.tool_call_id,
                scene_ids=scene_ids,
                variant_count=variant_count,
                attempt=attempt,
                batch_index=batch_index,
            )
        except (ValueError, OperationConflictError) as exc:
            raise VideoToolExecutionError("镜头生成批次参数无效") from exc
        batch = await self._batch_repository.create_or_read(
            user_id=context.user_id,
            conversation_id=context.workspace.conversation_id,
            workspace_id=context.workspace.workspace_id,
            plan=plan,
            run_id=context.run_id,
            tool_call_id=context.tool_call_id,
            attempt=attempt,
            source_workspace_revision=context.workspace.revision,
        )
        if self._credential_store is not None and context.credential is not None:
            # Broker 会在 Tool 返回后清理原凭据；批次凭据是独立的进程内短租约，
            # 只用于本批次尚未领取的槽位，绝不写入数据库。
            await self._credential_store.put(
                batch_id=batch.batch_id,
                authorization=context.credential.borrow_authorization(),
            )
        scenes_by_id = {str(scene.get("scene_id") or "").strip(): scene for scene in scenes}
        started = await self._dispatcher.dispatch_start_slots(
            batch=batch,
            context=context,
            scenes_by_id=scenes_by_id,
            attempt=attempt,
        )
        jobs = tuple(
            started.get(
                child.operation_idempotency_key,
                SceneGenerationJob(
                    job_id=child.job_id or child.operation_idempotency_key,
                    scene_id=child.scene_id,
                    variant_index=child.variant_index,
                    status="polling" if child.job_id else "queued",
                ),
            )
            for child in batch.children
        )
        return batch.batch_id, jobs


class M06SceneGenerationBatchDispatcherWorker:
    """Gateway 重启后扫描持久化 queued 子项，并在仍有瞬时授权时补领槽位。"""

    def __init__(
        self,
        *,
        batch_repository: OperationBatchRepository,
        video_repository: VideoWorkspaceRepository,
        dispatcher: M06SceneGenerationBatchDispatcher,
        credential_store: TransientBatchCredentialStore,
        worker_id: str,
        scan_interval: timedelta = timedelta(seconds=1),
        scan_limit: int = 100,
    ) -> None:
        if not worker_id.strip() or scan_interval <= timedelta(0) or scan_limit < 1:
            raise ValueError("批次 Dispatcher Worker 配置无效")
        self._batches = batch_repository
        self._videos = video_repository
        self._dispatcher = dispatcher
        self._credentials = credential_store
        self._worker_id = worker_id.strip()
        self._scan_interval = scan_interval
        self._scan_limit = scan_limit
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """子项终态后立即尝试领取下一槽位，不等待下一次轮询。"""

        self._wake.set()

    async def run_once(self) -> int:
        """处理所有当前可安全启动的批次；无授权只保留队列，不伪造 Provider start。"""

        dispatched = 0
        for batch in await self._batches.list_dispatchable_batches(limit=self._scan_limit):
            credential = await self._credentials.get(batch_id=batch.batch_id)
            if credential is None:
                # 用户 Authorization 不得持久化。重启或租约过期后，等待新的显式授权。
                continue
            if batch.run_id is None or batch.tool_call_id is None or batch.attempt is None:
                logger.warning("operation_batch_dispatch_missing_identity batch_id=%s", batch.batch_id)
                continue
            workspace = await self._videos.get_workspace(batch.user_id, batch.workspace_id)
            if workspace is None or workspace.conversation_id != batch.conversation_id:
                logger.warning("operation_batch_dispatch_workspace_unavailable batch_id=%s", batch.batch_id)
                continue
            if (
                batch.source_workspace_revision is not None
                and workspace.revision > batch.source_workspace_revision + 1
            ):
                logger.warning("operation_batch_dispatch_workspace_stale batch_id=%s", batch.batch_id)
                continue
            payload = workspace.payload if isinstance(workspace.payload, Mapping) else {}
            raw_scenes = payload.get("scenes")
            scenes = raw_scenes if isinstance(raw_scenes, list) else []
            scenes_by_id = {
                str(scene.get("scene_id") or "").strip(): scene
                for scene in scenes
                if isinstance(scene, Mapping) and str(scene.get("scene_id") or "").strip()
            }
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
                    scenes_by_id=scenes_by_id,
                    attempt=batch.attempt,
                )
                dispatched += 1
            except Exception as exc:  # noqa: BLE001 - 保留状态给下一轮与 M06 lease 恢复。
                logger.warning(
                    "operation_batch_dispatch_failed batch_id=%s error_type=%s",
                    batch.batch_id,
                    type(exc).__name__,
                )
        return dispatched

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("批次 Dispatcher Worker 已关闭")
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(),
                name=f"operation-batch-dispatch:{self._worker_id}",
            )

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
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self._scan_interval.total_seconds(),
                    )
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 周期性任务必须在单轮失败后继续。
                logger.warning(
                    "operation_batch_dispatch_worker_failed error_type=%s",
                    type(exc).__name__,
                )
                await asyncio.sleep(self._scan_interval.total_seconds())

def build_scene_provider_request(
    context: VideoToolContext,
    scene: Mapping[str, JsonValue],
    variant_index: int,
) -> Mapping[str, JsonValue]:
    """从 Workspace creation_contract + 分镜组装 content-app 镜头生成请求。

    场景包镜头通常只有 prompt/duration_ms/image mentions，不含 model/ratio/size；
    这些必须从 creation_contract 补齐，否则 Provider 会因缺字段直接失败。
    """

    scene_id = str(scene.get("scene_id") or "").strip()
    prompt = _resolve_scene_prompt(scene)
    if not scene_id or not prompt:
        raise VideoToolExecutionError("镜头生成请求缺少镜头或提示词")

    payload = (
        context.workspace.payload
        if isinstance(context.workspace.payload, Mapping)
        else {}
    )
    contract = payload.get("creation_contract")
    contract_map = contract if isinstance(contract, Mapping) else {}
    global_assets = payload.get("global_assets")
    # V2 Prompt Package 的结构化引用是生成入口的权威边界：不能因旧的 materials 全量拼接
    # 而把未登记或尚未生成的素材静默带入某段视频。
    referenced_material_ids = _validate_v2_scene_asset_references(payload, scene)

    model = _first_text(scene.get("model"), contract_map.get("video_model"))
    ratio = _first_text(scene.get("ratio"), contract_map.get("video_ratio"))
    size = _first_text(scene.get("size"), contract_map.get("video_size"), "1080p")
    sound = _first_text(scene.get("sound"), contract_map.get("video_sound"), "on")
    if not model or not ratio or not size:
        raise VideoToolExecutionError(
            "镜头生成缺少视频模型参数：请确认创作合同中的 video_model / video_ratio / video_size"
        )

    duration_sec = _resolve_duration_sec(scene)
    if duration_sec is None:
        raise VideoToolExecutionError("镜头生成请求缺少有效时长（4-15 秒）")

    # 绑定顺序就是 Provider image_urls 顺序。正文保留稳定 @asset_id，避免人物名称
    # 与参考图位置脱节；Seedance 2.0 的数字人资产优先传 asset:// 引用。
    bound_image_urls, bindings = _ordered_scene_asset_references(
        scene,
        global_assets=global_assets,
        model=model,
    )
    image_urls = _collect_image_references(
        _workspace_image_material_urls(
            payload.get("materials"),
            only_material_ids=referenced_material_ids,
        ),
        _workspace_reference_image_urls(payload.get("reference_images")),
        bound_image_urls,
        scene.get("image_urls"),
        _mention_image_urls(
            scene.get("shot_description"),
            excluded_asset_ids={asset_id for asset_id, _name in bindings},
        ),
        allow_asset_uri=_supports_asset_reference(model),
    )
    prompt = _prepend_reference_bindings(prompt, bindings)
    video_urls = _collect_https_urls(scene.get("video_urls"))
    audio_urls = _collect_https_urls(scene.get("audio_urls"))
    if len(image_urls) > 9:
        raise VideoToolExecutionError(
            f"单分镜最多允许 9 张参考图，当前为 {len(image_urls)} 张"
        )

    generation_mode = _first_text(scene.get("generation_mode")) or _infer_generation_mode(
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        scene=scene,
    )

    request: dict[str, JsonValue] = {
        "scene_id": scene_id,
        "variant_index": variant_index,
        "prompt": prompt,
        "duration": duration_sec,
        "duration_sec": duration_sec,
        "model": model,
        "ratio": ratio,
        "size": size,
        "sound": sound if sound in {"on", "off"} else "on",
        "generation_mode": generation_mode,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "audio_urls": audio_urls,
    }
    for key in (
        "shot_type",
        "camera_movement",
        "narration",
        "narration_text",
        "onscreen_text",
        "asset_refs",
    ):
        value = scene.get(key)
        if value is not None:
            request[key] = value
    return request


def _workspace_reference_image_urls(value: object) -> list[str]:
    """读取 Workspace 已验证的用户参考图 TOS URL，最多由上游公共命令写入九张。"""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return _collect_https_urls(
        item.get("url") for item in value if isinstance(item, Mapping)
    )


def _validate_v2_scene_asset_references(
    payload: Mapping[str, object],
    scene: Mapping[str, object],
) -> set[str] | None:
    """校验 V2 分镜只消费已登记且可用于视频的资产，返回已选用户材料身份。"""

    raw_registry = payload.get("asset_registry")
    if not isinstance(raw_registry, list):
        return None
    registry = {
        str(item.get("asset_id") or "").strip(): item
        for item in raw_registry
        if isinstance(item, Mapping) and str(item.get("asset_id") or "").strip()
    }
    # 空注册表属于尚未迁移的历史 Workspace，保持兼容；非空注册表则严格执行 V2 合同。
    if not registry:
        return None
    raw_references = scene.get("reference_asset_ids")
    references = (
        [str(item).strip() for item in raw_references if str(item).strip()]
        if isinstance(raw_references, (list, tuple))
        else []
    )
    if not references:
        raise VideoToolExecutionError("分镜尚未声明已登记资产，不能开始生成")
    material_ids: set[str] = set()
    for asset_id in references:
        asset = registry.get(asset_id)
        if asset is None:
            raise VideoToolExecutionError("分镜引用了未登记资产，不能开始生成")
        if asset.get("state") != "ready" or asset.get("usable_for_video") is not True:
            raise VideoToolExecutionError("分镜引用资产尚未就绪，需先完成素材生成")
        material_id = str(asset.get("source_material_id") or "").strip()
        if material_id:
            material_ids.add(material_id)
    return material_ids


def _workspace_image_material_urls(
    value: object,
    *,
    only_material_ids: set[str] | None = None,
) -> list[str]:
    """把用户在 Composer 上传并持久化的指定图片材料作为本次视频的参考图。"""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    urls: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("kind") != "image":
            continue
        material_id = str(item.get("material_id") or "").strip()
        if only_material_ids is not None and material_id not in only_material_ids:
            continue
        raw_url = item.get("url")
        # content-app 测试环境曾保存 HTTP TOS 地址；只在 Composer 材料边界升级为 HTTPS，
        # 既兼容已写入的记录，也不放宽其他 Workspace 外部引用的安全协议约束。
        if isinstance(raw_url, str) and raw_url.strip().lower().startswith("http://"):
            raw_url = f"https://{raw_url.strip()[len('http://'):]}"
        url = _safe_https_url(raw_url)
        if url and url not in urls:
            urls.append(url)
    return urls


def _default_provider_request(
    scene: Mapping[str, JsonValue],
    variant_index: int,
) -> Mapping[str, JsonValue]:
    """兼容旧注入签名：仅使用镜头自身字段，不含创作合同补齐。"""

    scene_id = str(scene.get("scene_id") or "").strip()
    prompt = _resolve_scene_prompt(scene)
    if not scene_id or not prompt:
        raise VideoToolExecutionError("镜头生成请求缺少镜头或提示词")
    request: dict[str, JsonValue] = {
        "scene_id": scene_id,
        "variant_index": variant_index,
        "prompt": prompt,
    }
    for key in (
        "duration_sec",
        "duration",
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


def _resolve_scene_prompt(scene: Mapping[str, Any]) -> str:
    """优先用分镜面板镜头描述正文；prompt 可能是「故事线+镜头描述」拼接脏字段。"""

    shot = scene.get("shot_description")
    if isinstance(shot, Mapping):
        text = str(shot.get("text") or "").strip()
        if text:
            return text
    prompt = str(scene.get("prompt") or "").strip()
    if prompt:
        return prompt
    return str(scene.get("storyline") or "").strip()


def _prepend_reference_bindings(
    prompt: str,
    bindings: Sequence[tuple[str, str]],
) -> str:
    if not bindings:
        return prompt
    lines = [
        f"@图片{index} = @{asset_id}（{name}）"
        for index, (asset_id, name) in enumerate(bindings, start=1)
    ]
    return "【参考素材绑定】\n" + "\n".join(lines) + "\n\n【镜头内容】\n" + prompt


def _resolve_duration_sec(scene: Mapping[str, Any]) -> int | None:
    raw = scene.get("duration_sec")
    if raw is None:
        raw = scene.get("duration")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if 4 <= value <= 30 else None
    duration_ms = scene.get("duration_ms")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    if int(duration_ms) % 1000 != 0:
        return None
    value = int(duration_ms) // 1000
    return value if 4 <= value <= 30 else None


def _infer_generation_mode(
    *,
    image_urls: Sequence[str],
    video_urls: Sequence[str],
    audio_urls: Sequence[str],
    scene: Mapping[str, Any],
) -> str:
    text = "\n".join(
        str(item or "")
        for item in (
            scene.get("prompt"),
            scene.get("storyline"),
            scene.get("narration"),
            scene.get("shot_description"),
        )
    ).lower()
    if video_urls and any(token in text for token in ("延伸", "续写", "extend")):
        return "extend_video"
    if video_urls and any(token in text for token in ("编辑", "修改", "调整", "edit")):
        return "edit_video"
    if image_urls or video_urls or audio_urls:
        return "reference_mode_video"
    return "text_to_video"


def _mention_image_urls(
    shot_description: object,
    *,
    excluded_asset_ids: set[str] | None = None,
) -> list[str]:
    if not isinstance(shot_description, Mapping):
        return []
    mentions = shot_description.get("mentions")
    if not isinstance(mentions, list):
        return []
    urls: list[str] = []
    for item in mentions:
        if isinstance(item, Mapping):
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            if excluded_asset_ids and asset_id in excluded_asset_ids:
                continue
            url = item.get("image_url") or item.get("url")
            if isinstance(url, str):
                urls.append(url)
    return urls


def _ordered_scene_asset_references(
    scene: Mapping[str, Any],
    *,
    global_assets: object,
    model: str,
) -> tuple[list[str], list[tuple[str, str]]]:
    """按镜头引用顺序返回素材值及与之同序的 asset_id/name 绑定。"""

    asset_lookup = _global_asset_record_lookup(global_assets)
    mention_lookup = _scene_mention_lookup(scene)
    asset_ids: list[str] = []
    raw_ids = scene.get("reference_asset_ids")
    if isinstance(raw_ids, (list, tuple)):
        for item in raw_ids:
            asset_id = str(item or "").strip()
            if asset_id:
                asset_ids.append(asset_id)
    shot = scene.get("shot_description")
    if isinstance(shot, Mapping):
        mentions = shot.get("mentions")
        if isinstance(mentions, list):
            for item in mentions:
                if not isinstance(item, Mapping):
                    continue
                asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
                if asset_id:
                    asset_ids.append(asset_id)

    urls: list[str] = []
    bindings: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for asset_id in asset_ids:
        if asset_id in seen_ids:
            continue
        seen_ids.add(asset_id)
        record = asset_lookup.get(asset_id)
        mention = mention_lookup.get(asset_id)
        url = _asset_record_reference(
            record,
            allow_asset_uri=_supports_asset_reference(model),
        )
        if url is None and mention is not None:
            url = _asset_record_reference(
                mention,
                allow_asset_uri=_supports_asset_reference(model),
            )
        if not url or url in urls:
            continue
        name = _asset_display_name(record) or _asset_display_name(mention) or asset_id
        urls.append(url)
        bindings.append((asset_id, name))
        if len(urls) >= 9:
            break
    return urls, bindings


def _global_asset_record_lookup(
    value: object,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for key in ("characters", "scenes", "props"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            if not asset_id or asset_id in result:
                continue
            result[asset_id] = item
    return result


def _scene_mention_lookup(
    scene: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    shot = scene.get("shot_description")
    if not isinstance(shot, Mapping):
        return {}
    mentions = shot.get("mentions")
    if not isinstance(mentions, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in mentions:
        if not isinstance(item, Mapping):
            continue
        asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
        if asset_id and asset_id not in result:
            result[asset_id] = item
    return result


def _asset_display_name(item: Mapping[str, Any] | None) -> str:
    if item is None:
        return ""
    name = str(item.get("name") or "").strip()
    return name if "@" not in name else ""


def _asset_record_reference(
    item: Mapping[str, Any] | None,
    *,
    allow_asset_uri: bool,
) -> str | None:
    if item is None:
        return None
    if allow_asset_uri:
        asset_uri = _safe_asset_uri(item.get("generation_reference_url"))
        if asset_uri:
            return asset_uri
    for key in ("image_url", "url", "generation_reference_url"):
        url = _safe_https_url(item.get(key))
        if url:
            return url
    for key in ("images", "three_view_images", "image_urls"):
        values = item.get(key)
        if isinstance(values, str):
            url = _safe_https_url(values)
            if url:
                return url
            continue
        if not isinstance(values, (list, tuple)):
            continue
        for entry in values:
            if isinstance(entry, Mapping):
                url = _safe_https_url(
                    entry.get("url") or entry.get("image_url") or entry.get("src")
                )
            else:
                url = _safe_https_url(entry)
            if url:
                return url
    return None


def _supports_asset_reference(model: str) -> bool:
    return model.strip().lower() in _SEEDANCE_ASSET_REFERENCE_MODELS


def _collect_https_urls(*groups: object) -> list[str]:
    result: list[str] = []
    for group in groups:
        values: Sequence[object]
        if group is None:
            continue
        if isinstance(group, (str, bytes)):
            values = [group]
        elif isinstance(group, Sequence):
            values = group
        else:
            continue
        for item in values:
            url = _safe_https_url(item)
            if url and url not in result:
                result.append(url)
    return result


def _collect_image_references(
    *groups: object,
    allow_asset_uri: bool,
) -> list[str]:
    result: list[str] = []
    for group in groups:
        values: Sequence[object]
        if group is None:
            continue
        if isinstance(group, (str, bytes)):
            values = [group]
        elif isinstance(group, Sequence):
            values = group
        else:
            continue
        for item in values:
            reference = _safe_https_url(item)
            if reference is None and allow_asset_uri:
                reference = _safe_asset_uri(item)
            if reference and reference not in result:
                result.append(reference)
    return result


def _safe_asset_uri(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized.startswith("asset://"):
        return None
    asset_id = normalized.removeprefix("asset://")
    if not asset_id or any(character.isspace() for character in asset_id):
        return None
    return normalized


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return url


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _context_authorization(context: VideoToolContext) -> str:
    """从当前执行上下文借用凭据，不在Operation Adapter中缓存。"""

    if context.credential is None:
        raise VideoToolExecutionError("镜头生成Operation缺少临时授权")
    try:
        return context.credential.borrow_authorization()
    except RuntimeError as exc:
        raise VideoToolExecutionError("镜头生成Operation缺少临时授权") from exc
