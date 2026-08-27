"""视频领域的外部 Operation 结果投影适配器。"""

from .projector import (
    ScenePackageCompletionProjector,
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
    "build_scene_generation_failure_patch",
    "build_scene_generation_success_patch",
    "count_polling_scene_generation_jobs",
    "scene_package_result_from_events",
    "workspace_has_scene_packages",
]
