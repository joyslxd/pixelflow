"""VideoAgent核心工具、Executor与Operation路由的受控装配。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from pixelflow.agent_runtime.jobs import ProviderJobAdapter
from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.agent_runtime.ports import OperationConflictError
from pixelflow.video_agent.adapters.delivery_operation import (
    M06DeliveryOperationPort,
)
from pixelflow.video_agent.adapters.domain_jobs import (
    GenerateSceneAssetsJobService,
    PrepareScenePackageJobService,
)
from pixelflow.video_agent.adapters.reference_operation import (
    M06ReferenceAnalysisOperationPort,
)
from pixelflow.video_agent.adapters.scene_operation import (
    M06SceneGenerationOperationPort,
)
from pixelflow.video_agent.adapters.scene_package_operation import (
    M06ScenePackageOperationPort,
)
from pixelflow.video_agent.executor import VideoAgentExecutor
from pixelflow.video_agent.tools import (
    AnalyzeReferenceVideoTool,
    BrainstormScriptTool,
    ComposeOrExportVideoTool,
    ConfirmScriptCreativeTool,
    GenerateSceneAssetsTool,
    GenerateScenesTool,
    ImportScriptTool,
    InspectSceneTool,
    InspectVideoWorkspaceTool,
    PatchSceneTool,
    PrepareScenePackagesTool,
    ReplaceProjectAssetsTool,
    ReviewGeneratedScenesTool,
    RunScriptSkillStageTool,
    VideoToolRegistry,
)
from pixelflow.video_agent.workspace import VideoAgentRepository

VIDEO_AGENT_RUNTIME_NOT_READY = "video_agent_runtime_not_ready"
_REFERENCE_STAGE = re.compile(r"^analyze_reference:[0-9a-f]{16}$")
_SCENE_STAGE = re.compile(r"^generate_scene:[0-9a-f]{12}:v[1-9][0-9]*$")
_PREPARE_STAGE = re.compile(r"^prepare_scene_packages:[0-9a-f]{16}$")
_ASSETS_STAGE = re.compile(r"^generate_scene_assets:[0-9a-f]{16}$")


class VideoAgentOperationAdapterResolver:
    """把V2动态Operation stage限定到显式注册的Provider Adapter。"""

    def __init__(
        self,
        *,
        reference_adapter: ProviderJobAdapter,
        scene_adapter: ProviderJobAdapter,
        merge_adapter: ProviderJobAdapter,
        prepare_adapter: ProviderJobAdapter,
        assets_adapter: ProviderJobAdapter,
        jianying_adapter: ProviderJobAdapter | None = None,
    ) -> None:
        for name, adapter in {
            "reference_adapter": reference_adapter,
            "scene_adapter": scene_adapter,
            "merge_adapter": merge_adapter,
            "prepare_adapter": prepare_adapter,
            "assets_adapter": assets_adapter,
        }.items():
            if not isinstance(adapter, ProviderJobAdapter):
                raise TypeError(f"{name}必须是ProviderJobAdapter")
        if jianying_adapter is not None and not isinstance(
            jianying_adapter,
            ProviderJobAdapter,
        ):
            raise TypeError("jianying_adapter必须是ProviderJobAdapter")
        self._adapters = {
            "reference": reference_adapter,
            "scene": scene_adapter,
            "mp4": merge_adapter,
            "prepare": prepare_adapter,
            "assets": assets_adapter,
        }
        if jianying_adapter is not None:
            self._adapters["jianying"] = jianying_adapter

    def resolve(self, stage: str) -> ProviderJobAdapter:
        """未知或未启用stage固定失败，不猜测供应商协议。"""

        if not isinstance(stage, str) or stage != stage.strip():
            raise OperationConflictError(
                "VideoAgent Operation stage未配置Provider Adapter"
            )
        if _REFERENCE_STAGE.fullmatch(stage):
            return self._adapters["reference"]
        if _SCENE_STAGE.fullmatch(stage):
            return self._adapters["scene"]
        if _PREPARE_STAGE.fullmatch(stage):
            return self._adapters["prepare"]
        if _ASSETS_STAGE.fullmatch(stage):
            return self._adapters["assets"]
        if stage == "deliver:mp4":
            return self._adapters["mp4"]
        if stage == "deliver:jianying_package" and "jianying" in self._adapters:
            return self._adapters["jianying"]
        raise OperationConflictError(
            "VideoAgent Operation stage未配置Provider Adapter"
        )


@dataclass(frozen=True, slots=True)
class VideoAgentRuntimeAssembly:
    """保存核心装配结果；剪映缺失不会阻塞MP4主链路。"""

    registry: VideoToolRegistry | None
    executor: VideoAgentExecutor | None
    operation_resolver: VideoAgentOperationAdapterResolver | None
    optional_capabilities: Mapping[str, bool]
    reason_code: str | None

    @property
    def ready(self) -> bool:
        return (
            self.reason_code is None
            and self.registry is not None
            and self.executor is not None
            and self.operation_resolver is not None
        )


def make_video_agent_runtime_assembly(
    *,
    operation_repository: AgentRuntimeRepository | None,
    video_repository: VideoAgentRepository | None,
    reference_adapter: ProviderJobAdapter | None,
    scene_adapter: ProviderJobAdapter | None,
    merge_adapter: ProviderJobAdapter | None,
    jianying_adapter: ProviderJobAdapter | None = None,
    prepare_adapter: ProviderJobAdapter | None = None,
    assets_adapter: ProviderJobAdapter | None = None,
    scene_assets_runner: Callable[..., object] | None = None,
    lease_owner: str,
    clock: Callable[[], datetime] | None = None,
) -> VideoAgentRuntimeAssembly:
    """只有参考解析、镜头生成和MP4合并齐备时构造核心Runtime。

    场景包/参考图 Adapter 默认使用进程内领域 Job；可由调用方覆盖。
    """

    optional_capabilities = MappingProxyType(
        {"jianying_package": jianying_adapter is not None}
    )
    required = (
        operation_repository,
        video_repository,
        reference_adapter,
        scene_adapter,
        merge_adapter,
    )
    if (
        any(item is None for item in required)
        or not isinstance(operation_repository, AgentRuntimeRepository)
        or not isinstance(video_repository, VideoAgentRepository)
        or not isinstance(reference_adapter, ProviderJobAdapter)
        or not isinstance(scene_adapter, ProviderJobAdapter)
        or not isinstance(merge_adapter, ProviderJobAdapter)
    ):
        return VideoAgentRuntimeAssembly(
            registry=None,
            executor=None,
            operation_resolver=None,
            optional_capabilities=optional_capabilities,
            reason_code=VIDEO_AGENT_RUNTIME_NOT_READY,
        )

    resolved_prepare = prepare_adapter or ProviderJobAdapter(
        PrepareScenePackageJobService(use_llm=True)
    )
    resolved_assets = assets_adapter or ProviderJobAdapter(
        GenerateSceneAssetsJobService(runner=scene_assets_runner)
    )
    resolver = VideoAgentOperationAdapterResolver(
        reference_adapter=reference_adapter,
        scene_adapter=scene_adapter,
        merge_adapter=merge_adapter,
        prepare_adapter=resolved_prepare,
        assets_adapter=resolved_assets,
        jianying_adapter=jianying_adapter,
    )
    reference_port = M06ReferenceAnalysisOperationPort(
        repository=operation_repository,
        adapter=reference_adapter,
        lease_owner=f"{lease_owner}:reference",
        clock=clock,
    )
    scene_port = M06SceneGenerationOperationPort(
        repository=operation_repository,
        adapter=scene_adapter,
        lease_owner=f"{lease_owner}:scene",
        clock=clock,
    )
    delivery_port = M06DeliveryOperationPort(
        repository=operation_repository,
        merge_adapter=merge_adapter,
        jianying_adapter=jianying_adapter,
        lease_owner=f"{lease_owner}:delivery",
        clock=clock,
    )
    package_port = M06ScenePackageOperationPort(
        repository=operation_repository,
        prepare_adapter=resolved_prepare,
        assets_adapter=resolved_assets,
        lease_owner=f"{lease_owner}:scene-packages",
        clock=clock,
    )
    registry = VideoToolRegistry(
        [
            InspectVideoWorkspaceTool(),
            ImportScriptTool(),
            BrainstormScriptTool(),
            RunScriptSkillStageTool(),
            ConfirmScriptCreativeTool(),
            AnalyzeReferenceVideoTool(operation_port=reference_port),
            PrepareScenePackagesTool(operation_port=package_port),
            GenerateSceneAssetsTool(operation_port=package_port),
            InspectSceneTool(),
            PatchSceneTool(),
            ReplaceProjectAssetsTool(),
            GenerateScenesTool(operation_port=scene_port),
            ReviewGeneratedScenesTool(clock=clock),
            ComposeOrExportVideoTool(operation_port=delivery_port),
        ]
    )
    return VideoAgentRuntimeAssembly(
        registry=registry,
        executor=VideoAgentExecutor(
            repository=video_repository,
            registry=registry,
            event_repository=operation_repository,
            clock=clock,
        ),
        operation_resolver=resolver,
        optional_capabilities=optional_capabilities,
        reason_code=None,
    )


__all__ = [
    "VIDEO_AGENT_RUNTIME_NOT_READY",
    "VideoAgentOperationAdapterResolver",
    "VideoAgentRuntimeAssembly",
    "make_video_agent_runtime_assembly",
]
