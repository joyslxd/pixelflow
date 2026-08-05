"""读取 VideoWorkspace 的最小公开证据。"""

from __future__ import annotations

from pixelflow.video_agent.contracts import VideoToolResult

from .registry import EmptyToolArguments, VideoToolContext, VideoToolValidationError


class InspectVideoWorkspaceTool:
    name = "inspect_video_workspace"
    input_model = EmptyToolArguments

    async def execute(
        self,
        context: VideoToolContext,
        arguments: dict[str, object],
    ) -> VideoToolResult:
        if context.workspace is None:
            raise VideoToolValidationError("当前对话尚未创建视频项目")
        payload = context.workspace.payload
        refs = tuple(
            item for item in payload.get("artifact_refs", [])
            if isinstance(item, str) and item.startswith("artifact:")
        )
        script = payload.get("script")
        has_script = isinstance(script, dict) and isinstance(script.get("content"), str) and bool(script["content"].strip())
        summary = f"已读取项目资料：{len(refs)} 个素材，{'已提供脚本' if has_script else '暂无脚本'}"
        return VideoToolResult(
            tool_name=self.name,
            public_summary=summary,
            workspace_patch={"workspace_id": context.workspace.workspace_id},
            artifact_refs=refs,
        )
