"""场景包准备与参考图生成的受控 V2 Tool。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from pixelflow.video_agent.contracts import VideoToolResult
from pixelflow.video_agent.contracts.plan import VideoAgentContract
from pixelflow.video_agent.quota import build_start_quota_interrupt_id

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolExecutionError,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class PrepareScenePackagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_markdown: str = Field(default="", max_length=200_000)
    target_duration_ms: int = Field(default=30_000, ge=1_000, le=600_000)
    attempt: int = Field(default=1, ge=1, le=10)


class GenerateSceneAssetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_model: str = Field(default="seeddream-5.0", min_length=1, max_length=128)
    image_ratio: str = Field(default="9:16", min_length=1, max_length=32)
    image_size: str = Field(default="2K", min_length=1, max_length=32)
    reference_brief: str = Field(default="", max_length=4_000)
    attempt: int = Field(default=1, ge=1, le=10)
    target_assets: tuple[dict[str, JsonValue], ...] = ()


class ScenePackageOperationJob(VideoAgentContract):
    job_id: str = Field(min_length=1, max_length=64)
    status: Literal["polling", "start_paused_quota", "succeeded"]
    result: dict[str, JsonValue] = Field(default_factory=dict)


class ScenePackageOperationPort(Protocol):
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
    ) -> ScenePackageOperationJob: ...


class SceneAssetOperationPort(Protocol):
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
    ) -> ScenePackageOperationJob: ...


def _as_mapping(value: Any) -> dict[str, JsonValue]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _script_markdown(payload: Mapping[str, Any]) -> str:
    script = payload.get("script")
    if isinstance(script, Mapping):
        content = script.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    pipeline = payload.get("script_pipeline")
    if isinstance(pipeline, Mapping):
        for stage in ("export", "episode", "outline", "characters"):
            item = pipeline.get(stage)
            if isinstance(item, Mapping):
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    latest = payload.get("latest_input")
    return str(latest).strip() if isinstance(latest, str) else ""


class PrepareScenePackagesTool:
    spec = VideoToolSpec(
        name="prepare_scene_packages",
        description="从已确认脚本生成结构化视频资产包（角色/场景/道具与分镜包）",
        input_model=PrepareScenePackagesInput,
        cost_level=VideoToolCostLevel.EXTERNAL_READ,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("global_assets", "scenes", "scene_packages", "creation_contract"),
    )

    def __init__(self, *, operation_port: ScenePackageOperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = PrepareScenePackagesInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("prepare_scene_packages 参数无效") from exc
        if self._operation_port is None:
            raise VideoToolExecutionError("场景包 Operation 尚未装配")

        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        plan_markdown = request.plan_markdown.strip() or _script_markdown(payload)
        if not plan_markdown:
            raise VideoToolValidationError("当前工作区没有可生成资产包的脚本")

        form_values = _as_mapping(payload.get("form_values") or payload.get("product_info"))
        if "product_info" not in form_values and isinstance(payload.get("product_info"), Mapping):
            form_values = {
                **form_values,
                "product_info": dict(payload["product_info"]),  # type: ignore[arg-type]
            }
        selected_direction = _as_mapping(payload.get("selected_direction"))
        materials = _as_list(payload.get("materials"))

        try:
            job = await self._operation_port.start_prepare_scene_packages(
                context,
                plan_markdown=plan_markdown,
                form_values=form_values,
                selected_direction=selected_direction,
                materials=materials,
                target_duration_ms=request.target_duration_ms,
                attempt=request.attempt,
            )
        except VideoToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoToolExecutionError("场景包准备失败") from exc

        if job.status in {"polling", "start_paused_quota"}:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="场景包准备任务已启动",
                workspace_patch={
                    "scene_package_job": {
                        "job_id": job.job_id,
                        "plan_step_id": context.step_id,
                        "status": job.status,
                    },
                    **(
                        {
                            "quota_interrupt": {
                                "quota_interrupt_id": build_start_quota_interrupt_id(job.job_id),
                                "plan_id": context.plan_id,
                                "step_id": context.step_id,
                                "job_id": job.job_id,
                                "quota_pause_revision": 0,
                                "phase": "start",
                                "state": "paused",
                                "reason_code": "provider_quota_insufficient",
                            }
                        }
                        if job.status == "start_paused_quota"
                        else {"quota_interrupt": None}
                    ),
                },
                pending_operation_job_ids=(job.job_id,),
            )

        result = job.result if isinstance(job.result, Mapping) else {}
        global_assets = result.get("global_assets") if isinstance(result.get("global_assets"), Mapping) else {}
        scene_packages = result.get("scene_packages") if isinstance(result.get("scene_packages"), list) else []
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=str(result.get("message") or f"已生成 {len(scene_packages)} 个分镜包"),
            workspace_patch={
                "global_assets": dict(global_assets),
                "scenes": list(scene_packages),
                "scene_packages": list(scene_packages),
                "creation_contract": result.get("creation_contract"),
                "script_plan_confirmed": True,
                "scene_package_job": {
                    "job_id": job.job_id,
                    "plan_step_id": context.step_id,
                    "status": "succeeded",
                },
                "quota_interrupt": None,
            },
            pending_operation_job_ids=(),
        )


class GenerateSceneAssetsTool:
    spec = VideoToolSpec(
        name="generate_scene_assets",
        description="为资产包生成角色/场景/道具参考图，并更新资产版本",
        input_model=GenerateSceneAssetsInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=("global_assets", "scenes", "scene_packages", "asset_versions"),
    )

    def __init__(self, *, operation_port: SceneAssetOperationPort | None = None) -> None:
        self._operation_port = operation_port

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        try:
            request = GenerateSceneAssetsInput.model_validate(dict(arguments))
        except ValidationError as exc:
            raise VideoToolValidationError("generate_scene_assets 参数无效") from exc
        if self._operation_port is None:
            raise VideoToolExecutionError("参考图 Operation 尚未装配")

        payload = context.workspace.payload if isinstance(context.workspace.payload, Mapping) else {}
        global_assets = _as_mapping(payload.get("global_assets"))
        scene_packages = _as_list(payload.get("scene_packages") or payload.get("scenes"))
        if not global_assets and not scene_packages:
            raise VideoToolValidationError("当前工作区没有可生成参考图的资产包")

        try:
            job = await self._operation_port.start_generate_scene_assets(
                context,
                global_assets=global_assets,
                scene_packages=scene_packages,
                materials=_as_list(payload.get("materials")),
                image_model=request.image_model,
                image_ratio=request.image_ratio,
                image_size=request.image_size,
                reference_brief=request.reference_brief,
                target_assets=[dict(item) for item in request.target_assets],
                attempt=request.attempt,
            )
        except VideoToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoToolExecutionError("参考图生成失败") from exc

        if job.status in {"polling", "start_paused_quota"}:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="参考图生成任务已启动",
                workspace_patch={
                    "scene_asset_job": {
                        "job_id": job.job_id,
                        "plan_step_id": context.step_id,
                        "status": job.status,
                        "image_model": request.image_model,
                    },
                    **(
                        {
                            "quota_interrupt": {
                                "quota_interrupt_id": build_start_quota_interrupt_id(job.job_id),
                                "plan_id": context.plan_id,
                                "step_id": context.step_id,
                                "job_id": job.job_id,
                                "quota_pause_revision": 0,
                                "phase": "start",
                                "state": "paused",
                                "reason_code": "provider_quota_insufficient",
                            }
                        }
                        if job.status == "start_paused_quota"
                        else {"quota_interrupt": None}
                    ),
                },
                pending_operation_job_ids=(job.job_id,),
            )

        result = job.result if isinstance(job.result, Mapping) else {}
        next_global = result.get("global_assets") if isinstance(result.get("global_assets"), Mapping) else global_assets
        next_scenes = result.get("scene_packages") if isinstance(result.get("scene_packages"), list) else scene_packages
        failed = result.get("failed_assets") if isinstance(result.get("failed_assets"), list) else []
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=str(result.get("message") or "参考图生成完成"),
            workspace_patch={
                "global_assets": dict(next_global),
                "scenes": list(next_scenes),
                "scene_packages": list(next_scenes),
                "scene_asset_failures": list(failed),
                "scene_asset_job": {
                    "job_id": job.job_id,
                    "plan_step_id": context.step_id,
                    "status": "succeeded",
                    "image_model": request.image_model,
                },
                "quota_interrupt": None,
            },
            pending_operation_job_ids=(),
        )
