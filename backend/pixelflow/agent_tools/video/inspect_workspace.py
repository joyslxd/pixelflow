"""提供 Sidecar 可调用的只读 VideoWorkspace 证据 Tool。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from pixelflow.video.contracts import VideoToolResult

from .contracts import (
    VideoToolContext,
    VideoToolCostLevel,
    VideoToolIdempotencyMode,
    VideoToolRecoveryMode,
    VideoToolSpec,
)


class InspectVideoWorkspaceInput(BaseModel):
    """只读 Tool 不接受模型参数，Workspace 归属只能由 Gateway binding 确定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _collection_size(value: object) -> int:
    """计算公开集合计数，不返回其中的业务正文。"""

    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value)
    return int(value is not None and value != "")


def _presence_count(value: object) -> int:
    """把内容存在性映射为公开的 0/1 计数。"""

    return int(value is not None and value not in ("", [], {}))


def _safe_artifact_refs(payload: Mapping[str, object]) -> tuple[str, ...]:
    """只返回格式合法的内部 Artifact 引用，拒绝 URL 与任意供应商字段。"""

    raw_refs = payload.get("artifact_refs")
    if not isinstance(raw_refs, list):
        return ()
    refs = (
        value
        for value in raw_refs
        if isinstance(value, str) and value.startswith("artifact:") and 9 < len(value) <= 256
    )
    return tuple(dict.fromkeys(refs))


def _public_inspect_summary(payload: Mapping[str, object]) -> str:
    """生成安全工作区摘要，只投影计数与公开进度，不携带脚本或素材原文。"""

    from pixelflow.video.workspace.digest import summarize_scene_video_status

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
    """只读 Workspace Handler；只接受 Gateway 从权威 binding 注入的上下文。"""

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
        """从权威 Workspace 投影结果；入参为空且不参与查询范围。"""

        del arguments
        payload = context.workspace.payload
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=_public_inspect_summary(payload),
            artifact_refs=_safe_artifact_refs(payload),
        )


__all__ = ["InspectVideoWorkspaceInput", "InspectVideoWorkspaceTool"]
