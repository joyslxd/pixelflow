"""提供图片资产生成状态的只读 Harness Tool。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from pixelflow.video.contracts import VideoToolResult
from pixelflow.video.workspace.digest import _asset_has_image_url, summarize_scene_asset_status
from pixelflow.video.workspace.payload import migrate_workspace_payload

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
)


class InspectImageAssetsInput(BaseModel):
    """占位输入类型；只读查询不接受模型参数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _asset_rows(payload: Mapping[str, Any]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    registry = payload.get("asset_registry")
    if not isinstance(registry, list):
        return rows
    for item in registry[:120]:
        if not isinstance(item, Mapping):
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not asset_id:
            continue
        state = str(item.get("state") or "planned").strip()[:32]
        ready = state == "ready" or item.get("usable_for_video") is True or _asset_has_image_url(item)
        if ready:
            state = "ready"
        rows.append(
            {
                "asset_id": asset_id[:128],
                "kind": kind,
                "state": state,
                "usable_for_video": item.get("usable_for_video") is True,
            }
        )
    return rows


class InspectImageAssetsTool:
    """读取图片资产的安全状态，不返回 URL、Provider 响应或授权信息。"""

    spec = VideoToolSpec(
        name="inspect_image_assets",
        description=(
            "查询当前 Workspace 中角色、场景和道具参考图的生成状态、失败数和可用性；"
            "在 generate_image_assets 返回后、retry_failed_image_assets 之前，或调用 generate_scenes 前使用。"
        ),
        input_model=InspectImageAssetsInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
        model_observation_keys=("status", "total", "ready", "running", "failed", "assets", "can_generate_scenes"),
    )

    async def execute(self, context: VideoToolContext, arguments: Mapping[str, object]) -> VideoToolResult:
        del arguments
        payload = migrate_workspace_payload(context.workspace.payload)
        rows = _asset_rows(payload)
        total = len(rows)
        ready = sum(row["state"] == "ready" for row in rows)
        running = sum(row["state"] in {"queued", "running", "polling", "starting"} for row in rows)
        failed = sum(row["state"] in {"failed", "timeout", "expired"} for row in rows)
        # 兼容旧 global_assets 投影：其状态可能比 asset_registry 更完整。
        summary = summarize_scene_asset_status(payload)
        if summary["scene_asset_required_count"] > total:
            total = int(summary["scene_asset_required_count"])
            ready = max(ready, int(summary["scene_asset_ready_count"]))
            failed = max(failed, int(summary["scene_asset_failed_count"]))
            running = max(running, total - ready - failed)
        status = "empty" if total == 0 else "ready" if ready == total else "failed" if failed and running == 0 else "running" if running else "partial"
        can_generate = total > 0 and ready == total and failed == 0
        public_summary = (
            f"图片资产：共 {total} 个，"
            f"已就绪 {ready} 个，生成中 {running} 个，失败 {failed} 个；"
            f"{'可以' if can_generate else '暂不可'}生成分镜视频。"
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=public_summary,
            model_observation={
                "status": status,
                "total": total,
                "ready": ready,
                "running": running,
                "failed": failed,
                "assets": rows,
                "can_generate_scenes": can_generate,
            },
        )


__all__ = ["InspectImageAssetsInput", "InspectImageAssetsTool"]
