"""把已规划图片资产改回 planned 并可选更新提示词，不调用 Provider。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

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
from .image_asset_retry import _reset_failed_asset

_RESETTABLE_STATES = {"ready", "failed", "timeout", "expired"}
_KEEP_STATES = {"planned"}


class ReviseImageAssetItem(BaseModel):
    """单条修订：必须点名 asset_id；提示词缺省表示沿用当前 generation_prompt。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    generation_prompt: str | None = Field(default=None, min_length=1, max_length=20_000)

    @field_validator("asset_id", "generation_prompt")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class ReviseImageAssetsInput(BaseModel):
    """只接受用户点名的待生成资产，禁止整表重排。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assets: tuple[ReviseImageAssetItem, ...] = Field(min_length=1, max_length=120)


class ReviseImageAssetsTool:
    """修订 planned_generation 资产的提示词并重置为 planned，供后续计费生图。"""

    spec = VideoToolSpec(
        name="revise_image_assets",
        description=(
            "按用户点名的 asset_id 修订 origin=planned_generation 的图片资产："
            "可更新 generation_prompt，并把 ready/failed 重置为 planned，保留 asset_id 与分镜引用。"
            "不接受用户上传素材，也不接受正在生成的资产。本 Tool 不计费；真正出图必须再调用 "
            "generate_image_assets，由该 Tool 弹出确认卡，不要在对话里再要一次确认。"
        ),
        input_model=ReviseImageAssetsInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.REQUEST,
        recovery_mode=VideoToolRecoveryMode.REPLAY,
        workspace_mutations=("asset_registry",),
        model_observation_keys=(
            "status",
            "asset_ids",
            "reset_count",
            "prompt_updated_count",
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

        request = ReviseImageAssetsInput.model_validate(dict(arguments))
        payload = migrate_workspace_payload(context.workspace.payload)
        next_registry, reset_ids, prompt_ids, kept_ids = _revise_registry(
            _registry_rows(payload),
            request.assets,
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=_public_summary(len(reset_ids), len(prompt_ids), len(kept_ids)),
            workspace_patch={"asset_registry": next_registry},
            model_observation={
                "status": "revised",
                "asset_ids": [item.asset_id for item in request.assets],
                "reset_count": len(reset_ids),
                "prompt_updated_count": len(prompt_ids),
                "already_planned_count": len(kept_ids),
                "workspace_revision_required": True,
            },
        )


def _revise_registry(
    registry: list[dict[str, Any]],
    items: tuple[ReviseImageAssetItem, ...],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """按点名列表修订资产表，拒绝重复 id。"""

    index_by_id = _index_by_asset_id(registry)
    reset_ids: list[str] = []
    prompt_ids: list[str] = []
    kept_ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        asset_id = item.asset_id
        if asset_id in seen:
            raise VideoToolValidationError(f"图片资产 {asset_id} 在本次修订中重复")
        seen.add(asset_id)
        action = _revise_action(registry, index_by_id, asset_id)
        next_asset, prompt_changed = _apply_revision(
            registry[index_by_id[asset_id]],
            item.generation_prompt,
            action,
        )
        registry[index_by_id[asset_id]] = next_asset
        if action == "reset":
            reset_ids.append(asset_id)
        else:
            kept_ids.append(asset_id)
        if prompt_changed:
            prompt_ids.append(asset_id)
    return registry, reset_ids, prompt_ids, kept_ids


def _registry_rows(payload: Mapping[str, JsonValue]) -> list[dict[str, Any]]:
    registry = payload.get("asset_registry")
    if not isinstance(registry, list):
        return []
    return [dict(item) for item in registry if isinstance(item, Mapping)]


def _index_by_asset_id(registry: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(item.get("asset_id") or "").strip(): index
        for index, item in enumerate(registry)
        if str(item.get("asset_id") or "").strip()
    }


def _revise_action(
    registry: list[dict[str, Any]],
    index_by_id: Mapping[str, int],
    asset_id: str,
) -> Literal["reset", "keep"]:
    """只允许待生成资产修订；正在生成或上传素材一律拒绝。"""

    if asset_id not in index_by_id:
        raise VideoToolValidationError(f"图片资产 {asset_id} 不在当前 Workspace 资产表")
    asset = registry[index_by_id[asset_id]]
    origin = str(asset.get("origin") or "").strip()
    state = str(asset.get("state") or "").strip()
    prompt = str(asset.get("generation_prompt") or "").strip()
    if origin != "planned_generation":
        raise VideoToolValidationError(f"图片资产 {asset_id} 不是可修订的待生成资产")
    if not prompt:
        raise VideoToolValidationError(f"图片资产 {asset_id} 缺少 generation_prompt")
    if state == "generating":
        raise VideoToolValidationError(f"图片资产 {asset_id} 正在生成，不能修订")
    if state in _KEEP_STATES:
        return "keep"
    if state not in _RESETTABLE_STATES:
        raise VideoToolValidationError(f"图片资产 {asset_id} 当前不是可修订的图片资产")
    return "reset"


def _apply_revision(
    asset: Mapping[str, object],
    generation_prompt: str | None,
    action: Literal["reset", "keep"],
) -> tuple[dict[str, Any], bool]:
    """重置运行态后按需覆盖提示词，不改 asset_id。"""

    next_asset = _reset_failed_asset(asset) if action == "reset" else dict(asset)
    prompt_changed = False
    if generation_prompt and generation_prompt != str(next_asset.get("generation_prompt") or ""):
        next_asset["generation_prompt"] = generation_prompt
        prompt_changed = True
    return next_asset, prompt_changed


def _public_summary(reset_count: int, prompt_updated_count: int, kept_count: int) -> str:
    parts = [f"已将 {reset_count} 个图片资产重新登记为待生成"]
    if prompt_updated_count:
        parts.append(f"更新了 {prompt_updated_count} 条生成提示词")
    if kept_count and not reset_count:
        parts.append(f"另有 {kept_count} 个本就是待生成")
    return "，".join(parts) + "；请再调用 generate_image_assets。"


__all__ = ["ReviseImageAssetItem", "ReviseImageAssetsInput", "ReviseImageAssetsTool"]
