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


class InspectVideoWorkspaceTool:
    spec = VideoToolSpec(
        name="inspect_video_workspace",
        description="读取当前视频项目的安全证据摘要和内部 Artifact 引用",
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
        summary = (
            f"项目资料：脚本 {_presence_count(payload.get('script'))} 份，"
            f"参考视频 {_collection_size(payload.get('reference_videos'))} 个，"
            f"素材 {_collection_size(payload.get('assets'))} 项，"
            f"分镜 {_collection_size(payload.get('scenes'))} 个，"
            f"输出 {_collection_size(payload.get('outputs'))} 项"
        )
        return VideoToolResult(
            tool_name=self.spec.name,
            public_summary=summary,
            artifact_refs=_safe_artifact_refs(payload),
        )
