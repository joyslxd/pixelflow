"""安全读取 VideoWorkspace 证据摘要的只读工具。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from pixelflow.video_agent.contracts import VideoToolResult

from .registry import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
)


class InspectVideoWorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _collection_size(value: object) -> int:
    if isinstance(value, (list, tuple, set, frozenset)):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return int(value is not None and value != "")


def _presence_count(value: object) -> int:
    return int(value is not None and value not in ("", [], {}))


def _safe_artifact_refs(payload: Mapping[str, object]) -> tuple[str, ...]:
    raw_refs = payload.get("artifact_refs")
    if not isinstance(raw_refs, list):
        return ()
    refs = (
        value
        for value in raw_refs
        if isinstance(value, str)
        and value.startswith("artifact:")
        and 9 < len(value) <= 256
    )
    return tuple(dict.fromkeys(refs))


def _public_inspect_summary(payload: Mapping[str, object]) -> str:
    # 延迟导入：避免 tools ↔ workspace.repository ↔ executor 循环依赖
    from pixelflow.video_agent.workspace.digest import summarize_scene_video_status

    video = summarize_scene_video_status(payload)
    progress_bits: list[str] = []
    completed = video.get("scene_video_progress_completed")
    total = video.get("scene_video_progress_total")
    if isinstance(completed, int) and isinstance(total, int) and total > 0:
        progress_bits.append(f"进度板 {completed}/{total}")
    progress_bits.append(
        "成片就绪 {ready}、轮询中 {polling}、失败 {failed}、未启动 {idle}".format(
            ready=video.get("scene_videos_ready_count", 0),
            polling=video.get("scene_videos_polling_count", 0),
            failed=video.get("scene_videos_failed_count", 0),
            idle=video.get("scene_videos_idle_count", 0),
        )
    )
    return (
        f"项目资料：脚本 {_presence_count(payload.get('script'))} 份，"
        f"参考视频 {_collection_size(payload.get('reference_videos'))} 个，"
        f"素材 {_collection_size(payload.get('assets'))} 项，"
        f"分镜 {_collection_size(payload.get('scenes') or payload.get('scene_packages'))} 个，"
        f"输出 {_collection_size(payload.get('outputs'))} 项；"
        f"分镜视频：{'；'.join(progress_bits)}"
    )


class InspectVideoWorkspaceTool:
    spec = VideoToolSpec(
        name="inspect_video_workspace",
        description=(
            "读取当前视频项目的安全证据摘要（含分镜视频就绪/轮询/失败计数）"
            "和内部 Artifact 引用；查询成片进度时优先调用。"
        ),
        input_model=InspectVideoWorkspaceInput,
        cost_level=VideoToolCostLevel.NONE,
        confirmation_required=False,
        idempotency_mode=VideoToolIdempotencyMode.READ_ONLY,
        recovery_mode=VideoToolRecoveryMode.INLINE,
        workspace_mutations=(),
    )

    async def execute(
        self,
        context: VideoToolContext,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        payload = context.workspace.payload
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=_public_inspect_summary(payload),
            artifact_refs=_safe_artifact_refs(payload),
        )
