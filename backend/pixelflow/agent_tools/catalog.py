"""发布给 Harness 的 Capability Tool Catalog。"""

from __future__ import annotations

from .video import (
    AnalyzeVideoTool,
    CreateStoryboardTool,
    GenerateScenesTool,
    InspectCreativeBriefTool,
    InspectSceneTool,
    InspectScriptTool,
    InspectVideoPlanTool,
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    PrepareScenePackagesTool,
    ReplaceSceneAssetTool,
    ReviseStoryboardTool,
    SelectCreativeOptionTool,
    UpdateCreativeBriefTool,
    UpdateScriptTool,
    UpdateVideoPlanTool,
    VideoToolRegistry,
)


def runtime_video_tool_registry(
    *,
    plan_repository: object | None = None,
    scene_generation_batch_operation_port: object | None = None,
    video_understanding_port: object | None = None,
) -> VideoToolRegistry:
    """构造当前可安全发布给 Sidecar 的非计费视频 Tool 集合。"""

    tools = [
            InspectVideoWorkspaceTool(),
            InspectCreativeBriefTool(),
            InspectScriptTool(),
            UpdateScriptTool(),
            InspectVideoPlanTool(plan_repository=plan_repository),
            UpdateVideoPlanTool(plan_repository=plan_repository),
            InspectSceneTool(),
            PatchSceneTool(),
            ReplaceSceneAssetTool(),
            PrepareScenePackagesTool(),
            CreateStoryboardTool(),
            ReviseStoryboardTool(),
            UpdateCreativeBriefTool(),
            SelectCreativeOptionTool(),
    ]
    if scene_generation_batch_operation_port is not None:
        # 仅在 Gateway 已装配真实 Provider/M06 Port 时发布计费 Tool，避免空实现被模型选择。
        tools.append(
            GenerateScenesTool(
                batch_operation_port=scene_generation_batch_operation_port,  # type: ignore[arg-type]
            )
        )
    if video_understanding_port is not None:
        tools.append(AnalyzeVideoTool(video_understanding_port))
    return VideoToolRegistry(tuple(tools))
