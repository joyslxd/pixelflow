"""生成已规划图片资产的 Harness Tool；只创建 GenerationJob，不同步等待 Provider。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.generation_jobs.service import GenerationJobService
from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.payload import migrate_workspace_payload

from .contracts import VideoToolContext, VideoToolCostLevel, VideoToolIdempotencyMode, VideoToolRecoveryMode, VideoToolSpec, VideoToolValidationError


class GenerateImageAssetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_ids: tuple[str, ...] = Field(min_length=1, max_length=120)
    attempt: int = Field(default=1, ge=1, le=10)


class GenerateImageAssetsTool:
    """根据 Workspace 资产表创建或回读图片 GenerationJob。"""

    spec = VideoToolSpec(
        name="generate_image_assets",
        description=(
            "为已规划的角色、场景、道具等图片资产创建 GenerationJob；只能选择当前 Workspace "
            "中 state=planned 且有 generation_prompt 的资产。failed 资产必须先调用 "
            "retry_failed_image_assets。Tool 返回后由 Gateway Worker 负责启动与轮询，"
            "完成时 Gateway 将资产原子回写为 ready。此操作可能计费，需用户确认与瞬时授权。"
        ),
        input_model=GenerateImageAssetsInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.GENERATION_JOB,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("asset_registry",),
        model_observation_keys=("status", "generation_job_ids", "asset_ids", "workspace_revision_required"),
    )

    def __init__(self, *, generation_job_service: GenerationJobService | None = None) -> None:
        self._service = generation_job_service

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = GenerateImageAssetsInput.model_validate(dict(arguments))
        if self._service is None or not self._service.image_available:
            return VideoToolResult(
                tool_name=self.spec.name,
                public_summary="图片生成能力当前未装配，请改用规划或检查 Tool，或等待 Gateway 配置 Provider。",
                model_observation={"status": "unavailable", "asset_ids": list(request.asset_ids)},
            )
        payload = migrate_workspace_payload(context.workspace.payload)
        registry = {
            str(item.get("asset_id") or "").strip(): item
            for item in payload.get("asset_registry", [])
            if isinstance(item, Mapping) and str(item.get("asset_id") or "").strip()
        }
        selected: list[Mapping[str, object]] = []
        for asset_id in request.asset_ids:
            asset = registry.get(asset_id)
            if asset is None:
                raise VideoToolValidationError(f"图片资产 {asset_id} 不在当前 Workspace 资产表")
            if asset.get("origin") != "planned_generation" or asset.get("state") != "planned":
                raise VideoToolValidationError(f"图片资产 {asset_id} 当前不是可生成的 planned 资产")
            if not str(asset.get("generation_prompt") or "").strip():
                raise VideoToolValidationError(f"图片资产 {asset_id} 缺少 generation_prompt")
            selected.append(asset)
        submissions = await self._service.submit_images(
            context,
            assets=tuple(selected),
            attempt=request.attempt,
        )
        next_registry = []
        selected_ids = set(request.asset_ids)
        for item in payload.get("asset_registry", []):
            if not isinstance(item, Mapping) or str(item.get("asset_id") or "") not in selected_ids:
                next_registry.append(item)
                continue
            submission = next(
                job for job in submissions if job.item_id == item.get("asset_id")
            )
            next_registry.append(
                {
                    **dict(item),
                    "state": "generating",
                    "generation_job_id": submission.job_id,
                    "generation_job_status": submission.status.value,
                }
            )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已创建 {len(submissions)} 个图片生成任务，包含 {len(selected)} 个资产；等待 Gateway Worker 启动。",
            workspace_patch={"asset_registry": next_registry},
            pending_generation_job_ids=tuple(item.job_id for item in submissions),
            requires_confirmation=True,
            model_observation={"status": "submitted", "generation_job_ids": [item.job_id for item in submissions], "asset_ids": list(request.asset_ids), "workspace_revision_required": True},
        )


__all__ = ["GenerateImageAssetsInput", "GenerateImageAssetsTool"]
