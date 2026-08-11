"""场景包准备 / 参考图生成 Operation Port。"""

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
from pixelflow.video_agent.tools.registry import (
    VideoToolContext,
    VideoToolExecutionError,
)
from pixelflow.video_agent.tools.scene_packages import ScenePackageOperationJob

_TERMINAL_FAILURES = frozenset(
    {
        ExternalJobStatus.FAILED,
        ExternalJobStatus.TIMEOUT,
        ExternalJobStatus.EXPIRED,
    }
)


class M06ScenePackageOperationPort:
    """启动 prepare_scene_packages / generate_scene_assets Operation。"""

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        prepare_adapter: ProviderJobAdapter,
        assets_adapter: ProviderJobAdapter,
        lease_owner: str,
        clock: Callable[[], datetime] | None = None,
        job_id_factory: Callable[[], str] | None = None,
        authorization_provider: Callable[[VideoToolContext], str] | None = None,
    ) -> None:
        if not isinstance(prepare_adapter, ProviderJobAdapter):
            raise TypeError("prepare_adapter 必须是 ProviderJobAdapter")
        if not isinstance(assets_adapter, ProviderJobAdapter):
            raise TypeError("assets_adapter 必须是 ProviderJobAdapter")
        owner = lease_owner.strip()
        if not owner or len(owner) > 128:
            raise ValueError("lease_owner 必须是1到128个字符")
        self._repository = repository
        self._prepare_adapter = prepare_adapter
        self._assets_adapter = assets_adapter
        self._lease_owner = owner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._job_id_factory = job_id_factory
        self._authorization_provider = authorization_provider or _context_authorization

    async def start_prepare_scene_packages(
        self,
        context: VideoToolContext,
        *,
        plan_markdown: str,
        form_values: dict[str, JsonValue],
        selected_direction: dict[str, JsonValue],
        materials: list[dict[str, JsonValue]],
        target_duration_ms: int,
        attempt: int,
    ) -> ScenePackageOperationJob:
        if context.plan_id is None or context.step_id is None:
            raise VideoToolExecutionError("场景包 Operation 缺少计划身份")
        digest = hashlib.sha256(plan_markdown.encode()).hexdigest()[:16]
        provider_request: dict[str, JsonValue] = {
            "plan_markdown": plan_markdown,
            "form_values": form_values,
            "selected_direction": selected_direction,
            "materials": materials,
            "target_duration_ms": target_duration_ms,
        }
        return await self._start(
            context,
            adapter=self._prepare_adapter,
            stage=f"prepare_scene_packages:{digest}",
            attempt=attempt,
            provider_request=provider_request,
        )

    async def start_generate_scene_assets(
        self,
        context: VideoToolContext,
        *,
        global_assets: dict[str, JsonValue],
        scene_packages: list[dict[str, JsonValue]],
        materials: list[dict[str, JsonValue]],
        image_model: str,
        image_ratio: str,
        image_size: str,
        reference_brief: str,
        target_assets: list[dict[str, JsonValue]],
        attempt: int,
    ) -> ScenePackageOperationJob:
        if context.plan_id is None or context.step_id is None:
            raise VideoToolExecutionError("参考图 Operation 缺少计划身份")
        fingerprint = hashlib.sha256(
            f"{image_model}|{image_ratio}|{image_size}|{len(target_assets)}".encode()
        ).hexdigest()[:16]
        provider_request: dict[str, JsonValue] = {
            "global_assets": global_assets,
            "scene_packages": scene_packages,
            "materials": materials,
            "image_model": image_model,
            "image_ratio": image_ratio,
            "image_size": image_size,
            "reference_brief": reference_brief,
            "target_assets": target_assets,
        }
        return await self._start(
            context,
            adapter=self._assets_adapter,
            stage=f"generate_scene_assets:{fingerprint}",
            attempt=attempt,
            provider_request=provider_request,
        )

    async def _start(
        self,
        context: VideoToolContext,
        *,
        adapter: ProviderJobAdapter,
        stage: str,
        attempt: int,
        provider_request: dict[str, JsonValue],
    ) -> ScenePackageOperationJob:
        request = build_operation_request(
            workflow_id=context.plan_id or "unknown-plan",
            stage=stage,
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
        )
        try:
            operation = await coordinator.start(
                request,
                provider_request=provider_request,
                authorization_provider=lambda: self._authorization_provider(context),
                lease_owner=self._lease_owner,
            )
        except OperationStartQuotaPausedError as exc:
            return ScenePackageOperationJob(
                job_id=exc.operation.job_id,
                status="start_paused_quota",
            )
        except OperationConflictError as exc:
            raise VideoToolExecutionError("场景包/参考图 Operation 启动失败") from exc

        if operation.status in {ExternalJobStatus.CREATED, ExternalJobStatus.POLLING}:
            return ScenePackageOperationJob(job_id=operation.job_id, status="polling")
        if operation.status in _TERMINAL_FAILURES:
            raise VideoToolExecutionError("场景包/参考图 Operation 执行失败")
        if operation.status is not ExternalJobStatus.SUCCEEDED:
            raise VideoToolExecutionError("场景包/参考图 Operation 状态不受支持")
        result = await self._completed_result(context, job_id=operation.job_id)
        return ScenePackageOperationJob(
            job_id=operation.job_id,
            status="succeeded",
            result=result,
        )

    async def _completed_result(
        self,
        context: VideoToolContext,
        *,
        job_id: str,
    ) -> dict[str, JsonValue]:
        events = await self._repository.list_events(
            context.user_id,
            context.workspace.conversation_id,
        )
        matches = [event for event in events if event.payload.get("job_id") == job_id]
        if len(matches) != 1:
            raise VideoToolExecutionError("场景包/参考图 Operation 完成事件不唯一")
        result = matches[0].payload.get("result")
        if not isinstance(result, Mapping):
            raise VideoToolExecutionError("场景包/参考图 Operation 缺少安全结果")
        return dict(result)


def _context_authorization(context: VideoToolContext) -> str:
    if context.credential is None:
        # 领域 Job 不依赖供应商 Authorization 时使用占位符。
        return "local-domain-job"
    try:
        return context.credential.borrow_authorization()
    except RuntimeError:
        return "local-domain-job"
