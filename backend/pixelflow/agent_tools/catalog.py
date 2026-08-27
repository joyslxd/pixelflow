"""发布给 Harness 的 Capability Tool Catalog。"""

from __future__ import annotations

from .video import (
    InspectSceneTool,
    InspectScriptTool,
    InspectVideoPlanTool,
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    ReplaceSceneAssetTool,
    UpdateScriptTool,
    UpdateVideoPlanTool,
    VideoToolRegistry,
)


def runtime_video_tool_registry(*, plan_repository: object | None = None) -> VideoToolRegistry:
    """构造当前可安全发布给 Sidecar 的非计费视频 Tool 集合。"""

    return VideoToolRegistry(
        (
            InspectVideoWorkspaceTool(),
            InspectScriptTool(),
            UpdateScriptTool(),
            InspectVideoPlanTool(plan_repository=plan_repository),
            UpdateVideoPlanTool(plan_repository=plan_repository),
            InspectSceneTool(),
            PatchSceneTool(),
            ReplaceSceneAssetTool(),
        )
    )
