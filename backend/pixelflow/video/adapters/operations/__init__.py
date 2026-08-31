"""视频领域的外部 Operation 结果投影适配器。"""

from .images import (
    ImageGenerationJob,
    M06ImageGenerationBatchDispatcher,
    M06ImageGenerationBatchDispatcherWorker,
    M06ImageGenerationBatchOperationPort,
    M06ImageGenerationOperationPort,
)
from .projector import (
    ScenePackageCompletionProjector,
    build_image_asset_failure_patch,
    build_image_asset_success_patch,
    build_scene_generation_failure_patch,
    build_scene_generation_success_patch,
    count_polling_scene_generation_jobs,
    scene_package_result_from_events,
    workspace_has_scene_packages,
)
from .scenes import (
    M06SceneGenerationBatchDispatcher,
    M06SceneGenerationBatchDispatcherWorker,
    M06SceneGenerationBatchOperationPort,
    M06SceneGenerationOperationPort,
)

__all__ = [
    "ScenePackageCompletionProjector",
    "M06SceneGenerationOperationPort",
    "M06SceneGenerationBatchDispatcher",
    "M06SceneGenerationBatchDispatcherWorker",
    "M06SceneGenerationBatchOperationPort",
    "ImageGenerationJob",
    "M06ImageGenerationOperationPort",
    "M06ImageGenerationBatchDispatcher",
    "M06ImageGenerationBatchDispatcherWorker",
    "M06ImageGenerationBatchOperationPort",
    "build_image_asset_failure_patch",
    "build_image_asset_success_patch",
    "build_scene_generation_failure_patch",
    "build_scene_generation_success_patch",
    "count_polling_scene_generation_jobs",
    "scene_package_result_from_events",
    "workspace_has_scene_packages",
]
