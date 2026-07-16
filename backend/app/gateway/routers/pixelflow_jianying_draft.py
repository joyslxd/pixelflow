"""PixelFlow 剪映草稿生成 API。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.gateway.pixelflow_memory import (
    concise_result_summary,
    current_user_id,
    power_mem_service,
    record_power_mem_background,
)
from pixelflow.jianying_draft import (
    JianyingDraftCapability,
    JianyingDraftResult,
    JianyingDraftScene,
    JianyingDraftStartRequest,
    JianyingDraftStatus,
    compute_storyboard_version_id,
)

router = APIRouter(prefix="/agent/flows/video/jianying-draft", tags=["pixelflow-flows"])

_TERMINAL_STATUSES = {
    JianyingDraftStatus.SUCCEEDED,
    JianyingDraftStatus.FAILED,
    JianyingDraftStatus.TIMEOUT,
    JianyingDraftStatus.NOT_CONFIGURED,
}
_CURRENT_STORYBOARD_NOT_READY = "当前视频分镜状态尚未就绪或已发生变化，请刷新后重试"


def _jianying_draft_service(request: Request) -> Any:
    """从当前应用取得剪映草稿 Service，缺失时统一返回服务不可用。"""

    service = getattr(request.app.state, "pixelflow_jianying_draft_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="剪映草稿服务暂不可用")
    return service


async def _require_owned_conversation(
    request: Request,
    conversation_id: str | None,
    user_id: str | None,
) -> Any:
    """统一隐藏不存在和无权访问的对话，避免越权枚举。"""

    store = getattr(request.app.state, "pixelflow_task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="对话服务暂不可用")
    conversation = None
    if conversation_id and user_id is not None:
        conversation = await store.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="对话不存在或无访问权限")
    return conversation


def _mapping_value(context: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in context:
            return context[key]
    return None


def _current_scene_packages(context: Mapping[str, Any]) -> Sequence[Any] | None:
    value = _mapping_value(context, "scene_packages", "videoScenePackages", "video_scene_packages")
    if isinstance(value, Mapping):
        if value.get("ok") is not True:
            return None
        value = value.get("scene_packages")
    return value if isinstance(value, list) else None


def _current_generated_scene_videos(context: Mapping[str, Any]) -> Sequence[Any] | None:
    value = _mapping_value(context, "generated_scene_videos", "generatedSceneVideos")
    if isinstance(value, Mapping):
        if value.get("ok") is not True:
            return None
        failed_scenes = value.get("failed_scenes", [])
        if not isinstance(failed_scenes, list) or failed_scenes:
            return None
        value = value.get("scene_videos")
    else:
        failed_scenes = context.get("failed_scenes", [])
        if not isinstance(failed_scenes, list) or failed_scenes:
            return None
    return value if isinstance(value, list) else None


def _normalize_context_scenes(scenes: Sequence[Any]) -> list[JianyingDraftScene] | None:
    try:
        normalized = [JianyingDraftScene.model_validate(scene) for scene in scenes]
    except (TypeError, ValueError):
        return None
    if not normalized:
        return None
    if len({scene.scene_id for scene in normalized}) != len(normalized):
        return None
    if len({scene.scene_index for scene in normalized}) != len(normalized):
        return None
    return normalized


def _package_scene_indexes(scene_packages: Sequence[Any]) -> dict[str, int] | None:
    indexes: dict[str, int] = {}
    for package in scene_packages:
        if not isinstance(package, Mapping):
            return None
        scene_id = package.get("scene_id")
        scene_index = package.get("scene_index")
        if not isinstance(scene_id, str) or not scene_id or isinstance(scene_index, bool) or not isinstance(scene_index, int):
            return None
        if scene_id in indexes:
            return None
        indexes[scene_id] = scene_index
    return indexes or None


def _matches_current_storyboard(conversation: Any, request: JianyingDraftStartRequest) -> bool:
    """仅接受已持久化且完整成功的当前视频分镜版本。"""

    context = getattr(conversation, "context", None)
    if not isinstance(context, Mapping):
        return False

    merged_video = _mapping_value(context, "merged_video", "mergedVideo")
    if not isinstance(merged_video, Mapping) or merged_video.get("ok") is not True:
        return False

    scene_packages = _current_scene_packages(context)
    generated_scene_videos = _current_generated_scene_videos(context)
    if scene_packages is None or generated_scene_videos is None:
        return False

    package_indexes = _package_scene_indexes(scene_packages)
    current_scenes = _normalize_context_scenes(generated_scene_videos)
    if package_indexes is None or current_scenes is None:
        return False

    current_indexes = {scene.scene_id: scene.scene_index for scene in current_scenes}
    if package_indexes != current_indexes:
        return False

    expected_version_id = compute_storyboard_version_id(current_scenes)
    if request.storyboard_version_id != expected_version_id:
        return False

    current_identity = sorted((scene.scene_id, scene.scene_index, scene.task_id or "", str(scene.video_url)) for scene in current_scenes)
    request_identity = sorted((scene.scene_id, scene.scene_index, scene.task_id or "", str(scene.video_url)) for scene in request.scenes)
    return request_identity == current_identity


@router.get("/capability", response_model=JianyingDraftCapability)
async def get_jianying_draft_capability(request: Request) -> JianyingDraftCapability:
    """查询剪映草稿 Provider 的当前可用性。"""

    try:
        return await _jianying_draft_service(request).capability()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Provider 边界不可泄露内部异常
        raise HTTPException(status_code=503, detail="剪映草稿服务暂不可用") from None


@router.post("/start", response_model=JianyingDraftResult)
async def start_jianying_draft(
    body: JianyingDraftStartRequest,
    request: Request,
) -> JianyingDraftResult:
    """验证对话归属后，启动或复用当前分镜版本的剪映草稿任务。"""

    service = _jianying_draft_service(request)
    user_id = await current_user_id(request)
    conversation = await _require_owned_conversation(request, body.conversation_id, user_id)
    if not _matches_current_storyboard(conversation, body):
        raise HTTPException(status_code=409, detail=_CURRENT_STORYBOARD_NOT_READY)
    result = await service.start(body, retry_failed=body.retry_failed)
    if result.status == JianyingDraftStatus.NOT_CONFIGURED:
        raise HTTPException(status_code=503, detail=result.model_dump(mode="json"))
    return result


@router.get("/jobs/{job_id}", response_model=JianyingDraftResult)
async def get_jianying_draft_job(job_id: str, request: Request) -> JianyingDraftResult:
    """验证任务所属对话后读取状态，并原子写入一次安全经验摘要。"""

    service = _jianying_draft_service(request)
    result = await service.get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="剪映草稿任务不存在或已过期")

    user_id = await current_user_id(request)
    await _require_owned_conversation(request, result.conversation_id, user_id)
    if result.status in _TERMINAL_STATUSES and await service.claim_terminal_experience(job_id):
        record_power_mem_background(
            power_mem_service(request),
            user_id=user_id,
            content=concise_result_summary(
                "剪映草稿 Agent 异步任务结束",
                {
                    "stage": "jianying_draft",
                    "ok": result.status == JianyingDraftStatus.SUCCEEDED,
                },
            ),
            category="experience",
            source_agent="jianying_draft_agent",
            metadata={
                "source": "video_jianying_draft_job",
                "job_id": job_id,
                "status": result.status.value,
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    return result
