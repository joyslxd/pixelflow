"""发布给 Harness 的 Capability Tool Catalog。"""

from __future__ import annotations

from .video import (
    AnalyzeVideoTool,
    ComposeOrExportVideoTool,
    CreateStoryboardTool,
    CreateVideoTool,
    GenerateImageAssetsTool,
    GenerateScenesTool,
    InspectCreativeBriefTool,
    InspectImageAssetsTool,
    InspectSceneTool,
    InspectScriptTool,
    InspectVideoPlanTool,
    InspectVideoResultsTool,
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    PrepareScenePackagesTool,
    ReplaceSceneAssetTool,
    RetryFailedImageAssetsTool,
    ReviewGeneratedScenesTool,
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
    generation_job_service: object | None = None,
    video_understanding_port: object | None = None,
    delivery_operation_port: object | None = None,
) -> VideoToolRegistry:
    """构造当前可安全发布给 Sidecar 的视频 Capability Tool 集合。"""

    tools = [
            InspectVideoWorkspaceTool(),
            InspectVideoResultsTool(),
            InspectImageAssetsTool(),
            RetryFailedImageAssetsTool(),
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
            ReviewGeneratedScenesTool(),
            ComposeOrExportVideoTool(operation_port=delivery_operation_port),
    ]
    # 三类外部能力始终发布到 Manifest，由 Agent 自主判断是否调用；未装配 Provider 时，
    # Handler 返回明确的不可执行观察，不伪造成功，也不影响基础只读 Tool 启动。
    tools.append(GenerateScenesTool(generation_job_service=generation_job_service))
    tools.append(CreateVideoTool(generation_job_service=generation_job_service))
    tools.append(GenerateImageAssetsTool(generation_job_service=generation_job_service))
    tools.append(AnalyzeVideoTool(video_understanding_port))
    return VideoToolRegistry(tuple(tools))
