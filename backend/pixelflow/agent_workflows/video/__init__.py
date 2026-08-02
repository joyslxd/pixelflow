"""视频生成 Workflow 的阶段 Service 与权威业务快照。"""

from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Any, Final

_PUBLIC_MODULES: Final[dict[str, str]] = {
    "VideoLiveStateConflictError": "live_handler",
    "VideoLiveWorkflowHandler": "live_handler",
    "VideoOperationQuotaProjection": "live_quota",
    "VideoOperationQuotaProjectionService": "live_quota",
    "VideoOperationQuotaStateHandler": "live_operations",
    "VideoDeliveryWorkflowService": "delivery",
    "VideoDeliveryWorkflowState": "delivery",
    "VideoPlanAuthoritySnapshot": "planning",
    "VideoPlanningStage": "planning",
    "VideoPlanningWorkflowService": "planning",
    "VideoPlanningWorkflowState": "planning",
    "VideoScenePackageAuthoritySnapshot": "scene_packages",
    "VideoScenePackageStage": "scene_packages",
    "VideoScenePackageWorkflowService": "scene_packages",
    "VideoScenePackageWorkflowState": "scene_packages",
    "VideoSceneGenerationStage": "video_generation",
    "VideoSceneGenerationWorkflowService": "video_generation",
    "VideoSceneGenerationWorkflowState": "video_generation",
    "VideoSceneAtomicOperationPort": "video_generation",
    "VideoSceneOperationTerminalClaim": "video_generation",
    "VideoSceneVideoStage": "video_generation",
    "VideoSceneVideoWorkflowService": "video_generation",
    "VideoSceneVideoWorkflowState": "video_generation",
    "VideoWorkflowState": "state_codec",
    "VideoWorkflowStateEnvelope": "state_codec",
    "VideoWorkflowStateKind": "state_codec",
    "WorkflowDispatchResult": "live_handler",
    "VideoMergeSkillPort": "postproduction",
    "VideoOperationStartClaim": "postproduction",
    "VideoOperationTerminalClaim": "postproduction",
    "VideoPostProductionAtomicOperationPort": "postproduction",
    "VideoPostProductionStage": "postproduction",
    "VideoPostProductionWorkflowService": "postproduction",
    "VideoPostProductionWorkflowState": "postproduction",
    "VideoQualityReviewSkillPort": "postproduction",
    "VideoQualityReviewWorkflowResult": "postproduction",
    "canonical_payload_sha256": "state_codec",
    "canonical_video_workflow_envelope_sha256": "state_codec",
    "decode_video_workflow_state": "state_codec",
    "encode_video_workflow_state": "state_codec",
    "project_video_workflow_state": "state_codec",
}
_PUBLIC_IMPORT_LOCK: Final[RLock] = RLock()
_MISSING: Final[object] = object()

__all__ = [
    "VideoLiveStateConflictError",
    "VideoLiveWorkflowHandler",
    "VideoOperationQuotaProjection",
    "VideoOperationQuotaProjectionService",
    "VideoOperationQuotaStateHandler",
    "VideoDeliveryWorkflowService",
    "VideoDeliveryWorkflowState",
    "VideoPlanAuthoritySnapshot",
    "VideoPlanningStage",
    "VideoPlanningWorkflowService",
    "VideoPlanningWorkflowState",
    "VideoScenePackageAuthoritySnapshot",
    "VideoScenePackageStage",
    "VideoScenePackageWorkflowService",
    "VideoScenePackageWorkflowState",
    "VideoSceneGenerationStage",
    "VideoSceneGenerationWorkflowService",
    "VideoSceneGenerationWorkflowState",
    "VideoSceneAtomicOperationPort",
    "VideoSceneOperationTerminalClaim",
    "VideoSceneVideoStage",
    "VideoSceneVideoWorkflowService",
    "VideoSceneVideoWorkflowState",
    "VideoWorkflowState",
    "VideoWorkflowStateEnvelope",
    "VideoWorkflowStateKind",
    "WorkflowDispatchResult",
    "VideoMergeSkillPort",
    "VideoOperationStartClaim",
    "VideoOperationTerminalClaim",
    "VideoPostProductionAtomicOperationPort",
    "VideoPostProductionStage",
    "VideoPostProductionWorkflowService",
    "VideoPostProductionWorkflowState",
    "VideoQualityReviewSkillPort",
    "VideoQualityReviewWorkflowResult",
    "canonical_payload_sha256",
    "canonical_video_workflow_envelope_sha256",
    "decode_video_workflow_state",
    "encode_video_workflow_state",
    "project_video_workflow_state",
]


def __getattr__(name: str) -> Any:
    """按公开符号所在模块惰性加载；真实导入异常保持原样向上传递。"""

    module_name = _PUBLIC_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"模块 {__name__!r} 没有属性 {name!r}")
    with _PUBLIC_IMPORT_LOCK:
        existing = globals().get(name, _MISSING)
        if existing is not _MISSING:
            return existing
        module = import_module(f"{__name__}.{module_name}")
        value = getattr(module, name)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """让交互式检查继续展示全部稳定公开符号。"""

    return sorted({*globals(), *__all__})
