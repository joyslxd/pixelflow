"""查询当前工作区的视频生成结果；不暴露供应商 URL 或原始响应。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.video.contracts import VideoToolResult

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
    VideoToolValidationError,
)


class InspectVideoResultsInput(BaseModel):
    """仅按当前 Workspace 的镜头身份筛选，不能传入外部任务或 URL。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_ids: tuple[str, ...] = Field(default=(), max_length=120)


class InspectVideoResultsTool:
    """提供视频结果的安全投影，作为 M06 异步生成后的查询入口。"""

    spec = VideoToolSpec(
        name="inspect_video_results",
        description=(
            "查询当前 Workspace 分镜视频的结果和进度；返回镜头身份、状态、已选版本和内部"
            "Artifact 引用，不返回供应商 URL 或原始响应。"
        ),
        input_model=InspectVideoResultsInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
        model_observation_keys=("status", "total", "ready", "running", "failed", "pending", "results"),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        request = InspectVideoResultsInput.model_validate(dict(arguments))
        payload = context.workspace.payload
        raw_scenes = payload.get("scenes") or payload.get("scene_packages") or []
        scenes = [item for item in raw_scenes if isinstance(item, Mapping)] if isinstance(raw_scenes, list) else []
        by_id = {
            str(scene.get("scene_id") or "").strip(): scene
            for scene in scenes
            if str(scene.get("scene_id") or "").strip()
        }
        requested = list(request.scene_ids)
        if requested:
            unknown = [scene_id for scene_id in requested if scene_id not in by_id]
            if unknown:
                raise VideoToolValidationError("当前 Workspace 中不存在指定镜头")
            target_ids = requested
        else:
            target_ids = list(by_id)

        results: list[dict[str, object]] = []
        artifact_refs: list[str] = []
        counts = {key: 0 for key in ("ready", "running", "failed", "pending")}
        for scene_id in target_ids:
            scene = by_id[scene_id]
            variants = scene.get("variants") if isinstance(scene.get("variants"), list) else []
            jobs = scene.get("generation_jobs") if isinstance(scene.get("generation_jobs"), list) else []
            selected_variant_id = str(scene.get("approved_variant_id") or "").strip() or None
            ready_variant = next(
                (
                    item
                    for item in variants
                    if isinstance(item, Mapping)
                    and str(item.get("video_url") or "").strip().startswith("https://")
                    and (item.get("selected") is True or item.get("variant_id") == selected_variant_id)
                ),
                None,
            )
            if ready_variant is None:
                ready_variant = next(
                    (
                        item
                        for item in variants
                        if isinstance(item, Mapping)
                        and str(item.get("video_url") or "").strip().startswith("https://")
                    ),
                    None,
                )
            statuses = {
                str(item.get("status") or "").strip().casefold()
                for item in jobs
                if isinstance(item, Mapping)
            }
            if ready_variant is not None:
                status = "ready"
                selected_variant_id = str(ready_variant.get("variant_id") or "").strip() or selected_variant_id
                artifact_ref = str(ready_variant.get("artifact_ref") or "").strip()
                if artifact_ref.startswith("artifact:"):
                    artifact_refs.append(artifact_ref)
            elif statuses.intersection({"queued", "created", "starting", "polling", "start_paused_quota"}):
                status = "running"
            elif statuses and statuses.issubset({"failed", "timeout", "expired"}):
                status = "failed"
            else:
                status = "pending"
            counts[status] += 1
            results.append(
                {
                    "scene_id": scene_id,
                    "status": status,
                    "selected_variant_id": selected_variant_id,
                }
            )

        overall = "ready" if counts["ready"] == len(target_ids) and target_ids else ("running" if counts["running"] else ("failed" if counts["failed"] else "pending"))
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=(
                f"视频结果：共 {len(target_ids)} 个镜头，已就绪 {counts['ready']} 个，"
                f"生成中 {counts['running']} 个，失败 {counts['failed']} 个，待提交 {counts['pending']} 个。"
            ),
            artifact_refs=tuple(dict.fromkeys(artifact_refs)),
            model_observation={"status": overall, "total": len(target_ids), **counts, "results": results},
        )


__all__ = ["InspectVideoResultsInput", "InspectVideoResultsTool"]
