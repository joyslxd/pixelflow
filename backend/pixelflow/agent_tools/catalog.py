"""发布给 Harness 的 Capability Tool Catalog。"""

from __future__ import annotations

from .video import (
    InspectSceneTool,
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    ReplaceSceneAssetTool,
    VideoToolRegistry,
)


def runtime_video_tool_registry() -> VideoToolRegistry:
    """构造当前可安全发布给 Sidecar 的非计费视频 Tool 集合。"""

    return VideoToolRegistry(
        (
            InspectVideoWorkspaceTool(),
            InspectSceneTool(),
            PatchSceneTool(),
            ReplaceSceneAssetTool(),
        )
    )
