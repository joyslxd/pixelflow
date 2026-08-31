"""发布给 Harness 的 Capability Tool Catalog。"""

from __future__ import annotations

from .video import (
    AnalyzeVideoTool,
    CreateStoryboardTool,
    GenerateImageAssetsTool,
    InspectImageAssetsTool,
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
    SetVideoGenerationContractTool,
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
    image_generation_batch_operation_port: object | None = None,
) -> VideoToolRegistry:
    """构造当前可安全发布给 Sidecar 的非计费视频 Tool 集合。"""

    tools = [
            InspectVideoWorkspaceTool(),
            InspectImageAssetsTool(),
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
            SetVideoGenerationContractTool(),
            UpdateCreativeBriefTool(),
            SelectCreativeOptionTool(),
    ]
    # 三类外部能力始终发布到 Manifest，由 Agent 自主判断是否调用；未装配 Provider 时，
    # Handler 返回明确的不可执行观察，不伪造成功，也不影响基础只读 Tool 启动。
    tools.append(GenerateScenesTool(batch_operation_port=scene_generation_batch_operation_port))
    tools.append(GenerateImageAssetsTool(batch_operation_port=image_generation_batch_operation_port))
    tools.append(AnalyzeVideoTool(video_understanding_port))
    return VideoToolRegistry(tuple(tools))
