"""把失败的待生成图片资产重新登记为 planned，不调用 Provider。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.payload import migrate_workspace_payload

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)

_FAILED_STATES = {"failed", "timeout", "expired"}
_STALE_RESULT_KEYS = (
    "failure_status",
    "failure_reason_code",
    "failed_at",
    "generation_job_id",
    "generation_job_status",
    "image_url",
    "provider_artifact_ref",
    "completed_at",
)


class RetryFailedImageAssetsInput(BaseModel):
    """只接受当前 Workspace 中已失败的待生成资产身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_ids: tuple[str, ...] = Field(min_length=1, max_length=120)


class RetryFailedImageAssetsTool:
    """清掉失败投影并改回 planned，供后续 generate_image_assets 重新提交。"""

    spec = VideoToolSpec(
        name="retry_failed_image_assets",
        description=(
            "把当前 Workspace 中 origin=planned_generation 且已失败的图片资产重新登记为 "
            "state=planned，保留 generation_prompt 与 asset_id，不新建资产、不改分镜引用。"
            "ready 或已上传素材不能调用本 Tool。完成后必须再调用 generate_image_assets 才会真正生图。"
        ),
        input_model=RetryFailedImageAssetsInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("asset_registry",),
        model_observation_keys=(
            "status",
            "asset_ids",
            "reset_count",
            "already_planned_count",
            "workspace_revision_required",
        ),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        """校验入参后只改 asset_registry 运行态，不创建 GenerationJob。"""

        request = RetryFailedImageAssetsInput.model_validate(dict(arguments))
        payload = migrate_workspace_payload(context.workspace.payload)
        registry = _registry_rows(payload)
        by_id = {
            str(item.get("asset_id") or "").strip(): item
            for item in registry
            if str(item.get("asset_id") or "").strip()
        }
        reset_ids: list[str] = []
        kept_ids: list[str] = []
        next_registry = [dict(item) for item in registry]
        index_by_id = {
            str(item.get("asset_id") or "").strip(): index
            for index, item in enumerate(next_registry)
            if str(item.get("asset_id") or "").strip()
        }
        for asset_id in request.asset_ids:
            action = _retry_action(by_id.get(asset_id), asset_id)
            index = index_by_id[asset_id]
            if action == "reset":
                next_registry[index] = _reset_failed_asset(next_registry[index])
                reset_ids.append(asset_id)
            else:
                kept_ids.append(asset_id)
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=(
                f"已将 {len(reset_ids)} 个失败图片资产重新登记为待生成"
                + (f"，另有 {len(kept_ids)} 个本就是待生成" if kept_ids else "")
                + "；请再调用 generate_image_assets。"
            ),
            workspace_patch={"asset_registry": next_registry},
            model_observation={
                "status": "reset",
                "asset_ids": list(request.asset_ids),
                "reset_count": len(reset_ids),
                "already_planned_count": len(kept_ids),
                "workspace_revision_required": True,
            },
        )


def _registry_rows(payload: Mapping[str, JsonValue]) -> list[dict[str, Any]]:
    registry = payload.get("asset_registry")
    if not isinstance(registry, list):
        return []
    return [dict(item) for item in registry if isinstance(item, Mapping)]


def _retry_action(asset: Mapping[str, object] | None, asset_id: str) -> Literal["reset", "keep"]:
    """只允许失败待生成资产重置；已是 planned 则幂等保留。"""

    if asset is None:
        raise VideoToolValidationError(f"图片资产 {asset_id} 不在当前 Workspace 资产表")
    origin = str(asset.get("origin") or "").strip()
    state = str(asset.get("state") or "").strip()
    prompt = str(asset.get("generation_prompt") or "").strip()
    if origin != "planned_generation":
        raise VideoToolValidationError(f"图片资产 {asset_id} 不是可重试的待生成资产")
    if not prompt:
        raise VideoToolValidationError(f"图片资产 {asset_id} 缺少 generation_prompt")
    if state == "planned":
        return "keep"
    if state not in _FAILED_STATES:
        raise VideoToolValidationError(f"图片资产 {asset_id} 当前不是可重试的失败资产")
    return "reset"


def _reset_failed_asset(asset: Mapping[str, object]) -> dict[str, Any]:
    """清掉失败投影与旧结果引用，保留提示词和稳定身份。"""

    next_asset = {
        key: value
        for key, value in asset.items()
        if key not in _STALE_RESULT_KEYS
    }
    next_asset["state"] = "planned"
    next_asset["usable_for_video"] = False
    return next_asset


__all__ = ["RetryFailedImageAssetsInput", "RetryFailedImageAssetsTool"]
