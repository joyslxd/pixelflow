"""视频生成 Workflow 的阶段 Service 与权威业务快照。"""

from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Any, Final

_PUBLIC_MODULES: Final[dict[str, str]] = {
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
_runtime_ready = False
_runtime_bootstrapping = False

__all__ = [
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
        _ensure_runtime_ready()
        module = import_module(f"{__name__}.{module_name}")
        value = getattr(module, name)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """让交互式检查继续展示全部稳定公开符号。"""

    return sorted({*globals(), *__all__})


def _ensure_runtime_ready() -> None:
    """首次公开导入前完成 Runtime 初始化，并允许同线程的状态编解码回入。"""

    global _runtime_bootstrapping, _runtime_ready
    if _runtime_ready or _runtime_bootstrapping:
        return
    _runtime_bootstrapping = True
    try:
        import_module("pixelflow.agent_runtime.contracts")
        _runtime_ready = True
    finally:
        _runtime_bootstrapping = False


# 子模块会反向经过 Runtime 读取公开状态编解码符号，因此在任何子模块开始初始化前固定导入顺序。
_ensure_runtime_ready()
