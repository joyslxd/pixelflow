"""生成已规划图片资产的 Harness Tool；只创建 M06 Operation，不同步等待 Provider。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.payload import migrate_workspace_payload

from .contracts import VideoToolContext, VideoToolCostLevel, VideoToolIdempotencyMode, VideoToolRecoveryMode, VideoToolSpec, VideoToolValidationError


class GenerateImageAssetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_ids: tuple[str, ...] = Field(min_length=1, max_length=120)
    attempt: int = Field(default=1, ge=1, le=10)


class GenerateImageAssetsTool:
    """根据 Workspace 资产表创建/回读图片 M06 批次。"""

    spec = VideoToolSpec(
        name="generate_image_assets",
        description=(
            "为已规划的角色、场景、道具等图片资产创建 M06 生成批次；只能选择当前 Workspace "
            "中 state=planned 且有 generation_prompt 的资产。Tool 返回后由 Dispatcher 启动与轮询，"
            "完成时 Gateway 将资产原子回写为 ready。此操作可能计费，需用户确认与瞬时授权。"
        ),
        input_model=GenerateImageAssetsInput,
        cost_level=VideoToolCostLevel.BILLABLE,
        confirmation_required=True,
        idempotency_mode=VideoToolIdempotencyMode.OPERATION,
        recovery_mode=VideoToolRecoveryMode.OPERATION,
        workspace_mutations=(),
        model_observation_keys=("status", "batch_ids", "asset_ids", "workspace_revision_required"),
    )

    def __init__(self, *, batch_operation_port: object | None = None) -> None:
        self._port = batch_operation_port

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        request = GenerateImageAssetsInput.model_validate(dict(arguments))
        if self._port is None:
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
        create = getattr(self._port, "create_or_read_batches", None)
        if not callable(create):
            raise VideoToolValidationError("图片生成 M06 Port 尚未装配")
        batches = await create(context, assets=selected, attempt=request.attempt)
        batch_ids = [str(batch[0]) for batch in batches]
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=f"已创建 {len(batch_ids)} 个图片资产生成批次，包含 {len(selected)} 个资产；等待 M06 Dispatcher 启动。",
            model_observation={"batch_ids": batch_ids, "asset_ids": list(request.asset_ids), "workspace_revision_required": True},
        )


__all__ = ["GenerateImageAssetsInput", "GenerateImageAssetsTool"]
