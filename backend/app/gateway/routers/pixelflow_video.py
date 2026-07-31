"""PixelFlow v2 视频生成与分析 API。"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.gateway.pixelflow_memory import concise_result_summary, current_user_id, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.creative.contract import VideoCreationContract
from pixelflow.generate.scene_asset_revision import revise_scene_package_asset
from pixelflow.generate.scene_assets import generate_scene_assets as run_generate_scene_assets
from pixelflow.generate.scene_packages import prepare_video_scene_packages_with_llm
from pixelflow.memory import semantic_memory_text, with_semantic_memory
from pixelflow.qc import VideoQCRequest, review_video_quality
from pixelflow.qc import VideoQCResponse as CoreVideoQCResponse
from pixelflow.skills import (
    get_image_analysis_skill,
    get_image_skill,
    get_media_link_extraction_skill,
    get_video_decompose_skill,
    get_video_quality_review_skill,
    get_video_skill,
)
from pixelflow.skills.base import is_quota_insufficient, quota_resume_message

router = APIRouter(prefix="/agent/flows/video", tags=["pixelflow-flows"])

_SCENE_VIDEO_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_VIDEO_JOBS = 100
_MERGE_VIDEO_JOBS: dict[str, dict[str, Any]] = {}
_MAX_MERGE_VIDEO_JOBS = 100
_QUALITY_REVIEW_JOBS: dict[str, dict[str, Any]] = {}
_MAX_QUALITY_REVIEW_JOBS = 100
_DIRECT_VIDEO_JOBS: dict[str, dict[str, Any]] = {}
_MAX_DIRECT_VIDEO_JOBS = 100
_SCENE_PACKAGE_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_PACKAGE_JOBS = 100
_SCENE_ASSET_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_ASSET_JOBS = 100
_SCENE_ASSET_REVISION_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_ASSET_REVISION_JOBS = 100
_MAX_REFERENCE_IMAGE_COUNT = 9
_SCENE_VIDEO_MAX_CONCURRENCY = 100
_SCENE_VIDEO_MAX_ATTEMPTS = 3

DirectVideoMode = Literal[
    "text_to_video",
    "image_to_video",
    "two_image_to_video",
    "reference_mode_video",
    "edit_video",
    "extend_video",
]


class SceneVideo(BaseModel):
    scene_id: str
    scene_index: int | None = None
    video_url: str


class SceneGenerationItem(BaseModel):
    scene_id: str
    scene_index: int
    duration_ms: int = Field(ge=4_000, le=15_000)
    prompt: str
    storyline: str = ""
    shot_description: dict[str, Any] = Field(default_factory=dict)
    narration: str = ""
    transition: str = ""
    generation_mode: DirectVideoMode | None = None
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    audio_urls: list[str] = Field(default_factory=list)


class GenerateSceneVideosRequest(BaseModel):
    scenes: list[SceneGenerationItem]
    ratio: str = "9:16"
    size: str = "720p"
    model: str | None = None
    sound: str = "on"
    creation_contract: VideoCreationContract | None = None


class PrepareScenePackagesRequest(BaseModel):
    form_values: dict[str, Any] = Field(default_factory=dict)
    plan_markdown: str = ""
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    target_duration_ms: int = 30_000
    intake_context: dict[str, Any] = Field(default_factory=dict)
    creation_contract: VideoCreationContract | None = None
    scene_blueprints: list[dict[str, Any]] = Field(default_factory=list)
    asset_manifest: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_final_plan_asset_manifest(self) -> PrepareScenePackagesRequest:
        if self.scene_blueprints and self.asset_manifest is None:
            raise ValueError("最终 Plan 缺少 asset_manifest，请先重新生成或修订 plan.md 后再生成场景包")
        return self


class PrepareScenePackagesResponse(BaseModel):
    ok: bool
    message: str = ""
    requires_confirmation: bool = True
    review_timeout_sec: int | None = None
    target_duration_ms: int
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    creation_contract: dict[str, Any] | None = None


class SceneAssetTarget(BaseModel):
    asset_id: str = Field(min_length=1)
    asset_type: Literal["character", "scene_image", "prop_image"]


class GenerateSceneAssetsRequest(BaseModel):
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]]
    materials: list[dict[str, Any]] = Field(default_factory=list)
    image_ratio: str = "1:1"
    image_size: str = "1080p"
    model: str | None = None
    creation_contract: VideoCreationContract | None = None
    target_assets: list[SceneAssetTarget] | None = Field(default=None, min_length=1, max_length=100)


class GenerateSceneAssetsResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/picture/text_to_image"
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    failed_assets: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""
    quota_insufficient: bool = False


class PrepareScenePackagesJobResult(BaseModel):
    ok: bool
    videoScenePackages: PrepareScenePackagesResponse | None = None
    sceneAssetFailures: list[dict[str, Any]] = Field(default_factory=list)
    quota_insufficient: bool = False
    message: str = ""


class PrepareScenePackagesJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    stage: str = "prepare_scene_packages"
    message: str = ""


class PrepareScenePackagesJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    stage: str = "prepare_scene_packages"
    result: PrepareScenePackagesJobResult | None = None
    error: str | None = None
    message: str = ""


class GenerateSceneAssetsJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    stage: str = "generate_scene_assets"
    message: str = ""


class GenerateSceneAssetsJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    stage: str = "generate_scene_assets"
    result: GenerateSceneAssetsResponse | None = None
    error: str | None = None
    message: str = ""


class ScenePackageAssetRevisionRequest(BaseModel):
    operation: Literal["replace", "delete"]
    asset_id: str = Field(min_length=1)
    asset_group: Literal["characters", "scenes", "props"]
    asset_name: str = ""
    source_image_url: str = ""
    new_image_url: str | None = None
    generation_reference_url: str | None = None
    replacement_metadata: dict[str, Any] = Field(default_factory=dict)
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_images_for_replacement(self) -> ScenePackageAssetRevisionRequest:
        if self.operation == "replace" and not str(self.source_image_url or "").strip():
            raise ValueError("替换素材时 source_image_url 不能为空")
        if self.operation == "replace" and not str(self.new_image_url or "").strip():
            raise ValueError("替换素材时 new_image_url 不能为空")
        return self


class ScenePackageAssetRevisionResponse(BaseModel):
    ok: bool
    operation: Literal["replace", "delete"]
    asset_id: str
    asset_group: Literal["characters", "scenes", "props"]
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    affected_scene_ids: list[str] = Field(default_factory=list)
    image_analysis_markdown: str = ""
    quota_insufficient: bool = False
    message: str = ""


class ScenePackageAssetRevisionJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class ScenePackageAssetRevisionJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: ScenePackageAssetRevisionResponse | None = None
    error: str | None = None
    message: str = ""


class GeneratedSceneVideo(BaseModel):
    scene_id: str
    scene_index: int
    duration_ms: int
    mode: str = "reference_mode_video"
    endpoint: str = "/api/video/reference-mode-video"
    video_url: str
    task_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class GenerateSceneVideosResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/video/reference-mode-video"
    scene_videos: list[GeneratedSceneVideo] = Field(default_factory=list)
    failed_scenes: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""
    quota_insufficient: bool = False


class GenerateSceneVideosJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class GenerateSceneVideosJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: GenerateSceneVideosResponse | None = None
    error: str | None = None
    message: str = ""


class MergeSceneVideosRequest(BaseModel):
    scene_videos: list[SceneVideo]
    duration: int = 30
    size: str = "1080p"
    model: str | None = None


class MergeSceneVideosResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/video/merge"
    merged_video_url: str | None = None
    task_id: str | None = None
    scene_videos: list[SceneVideo] = Field(default_factory=list)
    error: str | None = None
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class MergeSceneVideosJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class MergeSceneVideosJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: MergeSceneVideosResponse | None = None
    error: str | None = None
    message: str = ""


class VideoQualityReviewRequest(BaseModel):
    merged_video_url: str = ""
    scene_videos: list[SceneVideo] = Field(default_factory=list)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    original_scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    form_values: dict[str, Any] = Field(default_factory=dict)
    intake_context: dict[str, Any] = Field(default_factory=dict)
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    platform: str = ""
    ratio: str = "9:16"
    size: str = ""
    expected_duration_sec: float | None = None
    user_feedback: str = ""
    checks: list[str] = Field(default_factory=list)


class VideoQualityReviewResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/creative/video_quality_review"
    task_id: str | None = None
    passed: bool = True
    score: float = 1.0
    summary_markdown: str = ""
    quality_report_markdown: str = ""
    issues: list[dict[str, Any]] = Field(default_factory=list)
    affected_scene_ids: list[str] = Field(default_factory=list)
    target_scene_ids: list[str] = Field(default_factory=list)
    excluded_scene_ids: list[str] = Field(default_factory=list)
    revision_prompt: str = ""
    check_results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class VideoQualityReviewJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class VideoQualityReviewJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: VideoQualityReviewResponse | None = None
    error: str | None = None
    message: str = ""


class AnalyzeStoryboardsRequest(BaseModel):
    prompt: str = ""
    materials: list[dict[str, Any]] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)


class AnalyzeStoryboardsResponse(BaseModel):
    ok: bool
    mode: str = ""
    extract_endpoint: str = "/api/creative/extractMediaLinks"
    endpoint: str = ""
    video_urls: list[str] = Field(default_factory=list)
    storyboards: list[dict[str, Any]] = Field(default_factory=list)
    task_id: str | None = None
    error: str | None = None
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class GenerateDirectVideoRequest(BaseModel):
    mode: DirectVideoMode
    prompt: str = ""
    image_url: str = ""
    first_frame_image_url: str = ""
    last_frame_image_url: str = ""
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    audio_urls: list[str] = Field(default_factory=list)
    video_url: str = ""
    ref_video: str = ""
    ref_image: str = ""
    duration: int = Field(default=5, gt=0, le=15)
    ratio: str = "9:16"
    size: str = "720p"
    model: str | None = None
    sound: str = "on"


class GenerateDirectVideoResponse(BaseModel):
    ok: bool
    mode: str
    endpoint: str
    video_url: str | None = None
    task_id: str | None = None
    error: str | None = None
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class GenerateDirectVideoJobStartResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    message: str = ""


class GenerateDirectVideoJobStatusResponse(BaseModel):
    ok: bool
    job_id: str
    status: str
    result: GenerateDirectVideoResponse | None = None
    error: str | None = None
    message: str = ""


@router.post("/prepare-scene-packages", response_model=PrepareScenePackagesResponse)
async def prepare_scene_packages(body: PrepareScenePackagesRequest, request: Request) -> PrepareScenePackagesResponse:
    user_id, memories = await search_power_mem(
        request,
        source_agent="video_scene_package_agent",
        query_values=[body.form_values, body.plan_markdown, body.selected_direction, body.materials, body.intake_context],
        categories=["preference", "brand", "skill", "experience"],
    )
    result = await _prepare_scene_packages_response(_with_video_memory(body, memories))
    record_power_mem_background(
        power_mem_service(request),
        user_id=user_id,
        content=concise_result_summary(
            "视频场景包 Agent 生成可编辑场景包",
            {"stage": "prepare_scene_packages", "message": f"scenes={len(result.scene_packages)} assets={_asset_count(result.global_assets)}", "ok": result.ok},
        ),
        category="experience",
        source_agent="video_scene_package_agent",
        metadata={"source": "video_prepare_scene_packages", "scene_count": len(result.scene_packages), "asset_count": _asset_count(result.global_assets)},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/prepare-scene-packages/start", response_model=PrepareScenePackagesJobStartResponse)
async def start_prepare_scene_packages(body: PrepareScenePackagesRequest, request: Request) -> PrepareScenePackagesJobStartResponse:
    _trim_scene_package_jobs()
    job_id = uuid.uuid4().hex
    _SCENE_PACKAGE_JOBS[job_id] = {
        "status": "running",
        "stage": "prepare_scene_packages",
        "result": None,
        "error": None,
    }
    user_id, memories = await search_power_mem(
        request,
        source_agent="video_scene_package_agent",
        query_values=[body.form_values, body.plan_markdown, body.selected_direction, body.materials, body.intake_context],
        categories=["preference", "brand", "skill", "experience"],
    )
    asyncio.create_task(_run_prepare_scene_package_job(job_id, _with_video_memory(body, memories), power_mem_service(request), user_id))
    return PrepareScenePackagesJobStartResponse(
        ok=True,
        job_id=job_id,
        status="running",
        stage="prepare_scene_packages",
        message="视频场景包生成任务已启动。",
    )


@router.get("/prepare-scene-packages/jobs/{job_id}", response_model=PrepareScenePackagesJobStatusResponse)
async def get_prepare_scene_packages_job(job_id: str) -> PrepareScenePackagesJobStatusResponse:
    job = _SCENE_PACKAGE_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="视频场景包生成任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, PrepareScenePackagesJobResult):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = PrepareScenePackagesJobResult(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    stage = str(job.get("stage") or "prepare_scene_packages")
    error = job.get("error")
    return PrepareScenePackagesJobStatusResponse(
        ok=status not in {"failed"},
        job_id=job_id,
        status=status,
        stage=stage,
        result=result_payload,
        error=str(error) if error else None,
        message=_scene_package_job_message(status, stage, result_payload, error),
    )


async def _prepare_scene_packages_response(body: PrepareScenePackagesRequest) -> PrepareScenePackagesResponse:
    contract = body.creation_contract
    form_values = dict(body.form_values)
    target_duration_ms = body.target_duration_ms
    if contract is not None:
        target_duration_ms = contract.video_duration_sec * 1000
        form_values.update(
            {
                "video_duration_sec": contract.video_duration_sec,
                "video_ratio": contract.video_ratio,
                "video_model": contract.video_model,
                "video_size": contract.video_size,
                "video_sound": contract.video_sound,
                "image_model": contract.image_model,
                "scene_image_ratio": contract.scene_image_ratio,
                "scene_image_size": contract.scene_image_size,
                "video_usage": contract.video_usage,
                "visual_style": contract.visual_style,
            }
        )
    result = await prepare_video_scene_packages_with_llm(
        form_values=form_values,
        plan_markdown=body.plan_markdown,
        selected_direction=body.selected_direction,
        materials=body.materials,
        target_duration_ms=target_duration_ms,
        scene_blueprints=body.scene_blueprints,
        asset_manifest=body.asset_manifest,
    )
    result["creation_contract"] = contract.model_dump() if contract is not None else None
    return PrepareScenePackagesResponse(**result)


@router.post("/analyze-storyboards", response_model=AnalyzeStoryboardsResponse)
async def analyze_storyboards(body: AnalyzeStoryboardsRequest, request: Request) -> AnalyzeStoryboardsResponse:
    result = await _analyze_storyboards_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("视频分析 Agent 完成 storyboard 拆解", {"stage": "analyze_storyboards", "message": result.message, "ok": result.ok, "quota_insufficient": result.quota_insufficient}),
        category="experience",
        source_agent="video_analysis_agent",
        metadata={"source": "video_analyze_storyboards", "mode": result.mode, "video_count": len(result.video_urls)},
        memory_type="experience",
        infer=False,
    )
    return result


async def _analyze_storyboards_response(body: AnalyzeStoryboardsRequest) -> AnalyzeStoryboardsResponse:
    video_urls = _dedupe_urls(body.video_urls)
    extraction_raw: dict[str, Any] = {}
    if not video_urls:
        extraction_text = _build_media_extraction_text(body.prompt, body.materials)
        extraction = await get_media_link_extraction_skill().extract_media_links(
            text=extraction_text,
            materials=body.materials,
        )
        extraction_raw = extraction.raw
        if not extraction.ok:
            quota_insufficient = is_quota_insufficient(extraction.raw) or is_quota_insufficient(extraction.error)
            return AnalyzeStoryboardsResponse(
                ok=False,
                endpoint="/api/creative/extractMediaLinks",
                error=extraction.error,
                message=quota_resume_message(extraction.error) if quota_insufficient else (extraction.error or "媒体链接识别失败。"),
                quota_insufficient=quota_insufficient,
                raw=extraction_raw,
            )
        video_urls = [url for url in _dedupe_urls(extraction.links) if _is_probable_video_url(url)]

    if not video_urls:
        return AnalyzeStoryboardsResponse(
            ok=False,
            endpoint="/api/creative/extractMediaLinks",
            error="未识别到可分析的视频链接",
            message="请提供至少一个视频链接后再进行视频分析。",
            raw=extraction_raw,
        )

    decompose_skill = get_video_decompose_skill()
    if len(video_urls) == 1:
        result = await decompose_skill.decompose_video_to_storyboard(video_urls[0])
        quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
        return AnalyzeStoryboardsResponse(
            ok=result.ok,
            mode="single",
            endpoint="/api/creative/decompose_video_to_storyboard",
            video_urls=video_urls,
            storyboards=[{"video_url": video_urls[0], "shots": result.shots}] if result.ok else [],
            error=result.error,
            message="视频分析完成。" if result.ok else (quota_resume_message(result.error) if quota_insufficient else (result.error or "视频分析失败。")),
            quota_insufficient=quota_insufficient,
            raw=result.raw,
        )

    result = await decompose_skill.batch_decompose_video_to_storyboard(video_urls)
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    return AnalyzeStoryboardsResponse(
        ok=result.ok,
        mode="batch",
        endpoint="/api/creative/batch_decompose_video_to_storyboard",
        video_urls=video_urls,
        storyboards=result.storyboards if result.ok else [],
        task_id=result.task_id,
        error=result.error,
        message="批量视频分析完成。" if result.ok else (quota_resume_message(result.error) if quota_insufficient else (result.error or "批量视频分析失败。")),
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


@router.post("/generate-scene-assets", response_model=GenerateSceneAssetsResponse)
async def generate_scene_assets(body: GenerateSceneAssetsRequest, request: Request) -> GenerateSceneAssetsResponse:
    result = await _generate_scene_assets_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("视频素材图 Agent 生成场景参考图", {"stage": "generate_scene_assets", "message": result.message, "ok": result.ok, "quota_insufficient": result.quota_insufficient}),
        category="experience",
        source_agent="video_scene_asset_agent",
        metadata={"source": "video_generate_scene_assets", "failed_count": len(result.failed_assets)},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/generate-scene-assets/start", response_model=GenerateSceneAssetsJobStartResponse)
async def start_generate_scene_assets(body: GenerateSceneAssetsRequest, request: Request) -> GenerateSceneAssetsJobStartResponse:
    if not body.scene_packages:
        raise HTTPException(status_code=400, detail="scene_packages不能为空")
    _trim_scene_asset_jobs()
    job_id = uuid.uuid4().hex
    _SCENE_ASSET_JOBS[job_id] = {
        "status": "running",
        "stage": "generate_scene_assets",
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_scene_asset_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return GenerateSceneAssetsJobStartResponse(
        ok=True,
        job_id=job_id,
        status="running",
        stage="generate_scene_assets",
        message="场景参考图生成任务已启动。",
    )


@router.get("/generate-scene-assets/jobs/{job_id}", response_model=GenerateSceneAssetsJobStatusResponse)
async def get_generate_scene_assets_job(job_id: str) -> GenerateSceneAssetsJobStatusResponse:
    job = _SCENE_ASSET_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="场景参考图生成任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, GenerateSceneAssetsResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = GenerateSceneAssetsResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    stage = str(job.get("stage") or "generate_scene_assets")
    error = job.get("error")
    return GenerateSceneAssetsJobStatusResponse(
        ok=status not in {"failed"},
        job_id=job_id,
        status=status,
        stage=stage,
        result=result_payload,
        error=str(error) if error else None,
        message=_scene_asset_job_message(status, result_payload, error),
    )


@router.post(
    "/update-scene-package-asset/start",
    response_model=ScenePackageAssetRevisionJobStartResponse,
)
async def start_update_scene_package_asset(
    body: ScenePackageAssetRevisionRequest,
    request: Request,
) -> ScenePackageAssetRevisionJobStartResponse:
    """启动全局素材变更后的图片分析和分镜定向修订任务。"""
    if not body.scene_packages:
        raise HTTPException(status_code=400, detail="scene_packages不能为空")
    _trim_scene_asset_revision_jobs()
    job_id = uuid.uuid4().hex
    _SCENE_ASSET_REVISION_JOBS[job_id] = {
        "status": "running",
        "result": None,
        "error": None,
    }
    asyncio.create_task(
        _run_scene_asset_revision_job(
            job_id,
            body,
            power_mem_service(request),
            await current_user_id(request),
        )
    )
    return ScenePackageAssetRevisionJobStartResponse(
        ok=True,
        job_id=job_id,
        status="running",
        message="正在分析素材并同步更新受影响的分镜内容。",
    )


@router.get(
    "/update-scene-package-asset/jobs/{job_id}",
    response_model=ScenePackageAssetRevisionJobStatusResponse,
)
async def get_update_scene_package_asset_job(
    job_id: str,
) -> ScenePackageAssetRevisionJobStatusResponse:
    """查询全局素材语义修订任务。"""
    job = _SCENE_ASSET_REVISION_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分镜素材修订任务不存在或已过期")
    raw_result = job.get("result")
    if isinstance(raw_result, ScenePackageAssetRevisionResponse):
        result = raw_result
    elif isinstance(raw_result, dict):
        result = ScenePackageAssetRevisionResponse(**raw_result)
    else:
        result = None
    status = str(job.get("status") or "running")
    error = str(job.get("error") or "") or None
    if status == "completed":
        message = result.message if result else "分镜素材修订完成。"
    elif status == "quota_paused":
        message = quota_resume_message(error)
    elif status == "failed":
        message = error or "分镜素材修订失败。"
    else:
        message = "正在分析素材并同步更新受影响的分镜内容。"
    return ScenePackageAssetRevisionJobStatusResponse(
        ok=status not in {"failed", "quota_paused"},
        job_id=job_id,
        status=status,
        result=result,
        error=error,
        message=message,
    )


async def _generate_scene_assets_response(body: GenerateSceneAssetsRequest) -> GenerateSceneAssetsResponse:
    if not body.scene_packages:
        raise HTTPException(status_code=400, detail="scene_packages不能为空")

    contract = body.creation_contract
    result = await run_generate_scene_assets(
        image_skill=get_image_skill(),
        global_assets=_clone_mapping(body.global_assets) if body.global_assets else {},
        scene_packages=[_clone_mapping(scene) for scene in body.scene_packages],
        materials=body.materials,
        image_ratio=_scene_image_ratio(contract) if contract is not None else body.image_ratio,
        image_size=_scene_image_size(contract) if contract is not None else body.image_size,
        model=contract.image_model if contract is not None else body.model,
        quota_checker=is_quota_insufficient,
        target_assets=[target.model_dump() for target in body.target_assets] if body.target_assets is not None else None,
    )
    if result.get("quota_insufficient"):
        return GenerateSceneAssetsResponse(
            ok=False,
            endpoint=str(result.get("endpoint") or "/api/picture/text_to_image"),
            global_assets=result.get("global_assets") or {},
            scene_packages=result.get("scene_packages") or [],
            failed_assets=result.get("failed_assets") or [],
            quota_insufficient=True,
            message=quota_resume_message((result.get("failed_assets") or [{}])[-1].get("error") if result.get("failed_assets") else None),
        )
    return GenerateSceneAssetsResponse(
        ok=bool(result.get("ok")),
        endpoint=str(result.get("endpoint") or "/api/picture/text_to_image"),
        global_assets=result.get("global_assets") or {},
        scene_packages=result.get("scene_packages") or [],
        failed_assets=result.get("failed_assets") or [],
        message="场景资产图生成完成。" if result.get("ok") else "部分场景资产图生成失败，请查看 failed_assets。",
    )


async def _generate_direct_video_response(body: GenerateDirectVideoRequest) -> GenerateDirectVideoResponse:
    skill = get_video_skill()
    duration = _provider_video_duration_seconds(body.duration * 1000, body.model)

    if body.mode == "text_to_video":
        result = await skill.text_to_video(
            prompt=body.prompt,
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )
    elif body.mode == "image_to_video":
        if not body.image_url:
            raise HTTPException(status_code=400, detail="image_url不能为空")
        result = await skill.image_to_video(
            image_url=body.image_url,
            prompt=body.prompt,
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )
    elif body.mode == "two_image_to_video":
        if not body.first_frame_image_url or not body.last_frame_image_url:
            raise HTTPException(status_code=400, detail="first_frame_image_url和last_frame_image_url不能为空")
        result = await skill.two_image_to_video(
            first_frame_image_url=body.first_frame_image_url,
            last_frame_image_url=body.last_frame_image_url,
            prompt=body.prompt,
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )
    elif body.mode == "reference_mode_video":
        result = await skill.reference_mode_video(
            prompt=body.prompt,
            image_urls=body.image_urls,
            video_urls=body.video_urls,
            audio_urls=body.audio_urls,
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )
    elif body.mode == "edit_video":
        if not body.ref_video:
            raise HTTPException(status_code=400, detail="ref_video不能为空")
        result = await skill.edit_video(
            ref_video=body.ref_video,
            prompt=body.prompt,
            ref_image=body.ref_image or None,
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )
    else:
        if not body.video_url:
            raise HTTPException(status_code=400, detail="video_url不能为空")
        result = await skill.extend_video(
            video_url=body.video_url,
            prompt=body.prompt,
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )

    endpoint = _direct_video_endpoint(body.mode, result.raw)
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    message = "视频生成完成。" if result.ok else (result.error or "视频生成失败。")
    if quota_insufficient:
        message = quota_resume_message(result.error)
    return GenerateDirectVideoResponse(
        ok=result.ok,
        mode=body.mode,
        endpoint=endpoint,
        video_url=result.url,
        task_id=result.task_id,
        error=result.error,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


@router.post("/generate-direct", response_model=GenerateDirectVideoResponse)
async def generate_direct_video(body: GenerateDirectVideoRequest, request: Request) -> GenerateDirectVideoResponse:
    result = await _generate_direct_video_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("直接视频生成 Agent 完成同步生成", result.model_dump()),
        category="experience",
        source_agent="direct_video_generation_agent",
        metadata={"source": "video_generate_direct", "mode": body.mode},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/generate-direct/start", response_model=GenerateDirectVideoJobStartResponse)
async def start_generate_direct_video(body: GenerateDirectVideoRequest, request: Request) -> GenerateDirectVideoJobStartResponse:
    _trim_direct_video_jobs()
    job_id = uuid.uuid4().hex
    _DIRECT_VIDEO_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_direct_video_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return GenerateDirectVideoJobStartResponse(ok=True, job_id=job_id, status="running", message="直接视频生成任务已启动。")


@router.get("/generate-direct/jobs/{job_id}", response_model=GenerateDirectVideoJobStatusResponse)
async def get_generate_direct_video_job(job_id: str) -> GenerateDirectVideoJobStatusResponse:
    job = _DIRECT_VIDEO_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="直接视频生成任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, GenerateDirectVideoResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = GenerateDirectVideoResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return GenerateDirectVideoJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message="直接视频生成完成。" if status == "completed" else ("直接视频生成失败。" if status == "failed" else "直接视频生成中。"),
    )


async def _generate_scene_videos_response(body: GenerateSceneVideosRequest) -> GenerateSceneVideosResponse:
    if not body.scenes:
        raise HTTPException(status_code=400, detail="scenes不能为空")

    skill = get_video_skill()
    contract = body.creation_contract
    video_ratio = contract.video_ratio if contract is not None else body.ratio
    video_size = contract.video_size if contract is not None else body.size
    video_model = contract.video_model if contract is not None else body.model
    video_sound = contract.video_sound if contract is not None else body.sound
    supported_generation_types = _video_generation_types(contract)
    semaphore = asyncio.Semaphore(max(1, min(_SCENE_VIDEO_MAX_CONCURRENCY, len(body.scenes))))

    async def run_scene_once(
        scene: SceneGenerationItem,
        attempt: int,
        mode_override: DirectVideoMode | None = None,
    ) -> GeneratedSceneVideo | dict[str, Any]:
        image_urls = _scene_reference_image_urls(scene)
        if len(image_urls) > _MAX_REFERENCE_IMAGE_COUNT:
            return {
                "scene_id": scene.scene_id,
                "scene_index": scene.scene_index,
                "error": f"最多只能选择9张参考图，当前选择了{len(image_urls)}张。",
                "image_count": len(image_urls),
                "attempts": attempt,
            }
        mode = mode_override or _select_scene_video_mode(scene, image_urls, creation_contract=contract)
        prompt = _build_scene_video_prompt(
            scene,
            visual_style=contract.visual_style if contract is not None else "",
        )
        duration = _provider_video_duration_seconds(scene.duration_ms, video_model)
        _validate_scene_video_request(
            mode=mode,
            image_urls=image_urls,
            video_urls=_dedupe_urls(scene.video_urls),
            audio_urls=_dedupe_urls(scene.audio_urls),
            duration=duration,
            creation_contract=contract,
        )
        result = await _run_scene_video_generation(
            skill=skill,
            mode=mode,
            prompt=prompt,
            image_urls=image_urls,
            video_urls=_dedupe_urls(scene.video_urls),
            audio_urls=_dedupe_urls(scene.audio_urls),
            duration=duration,
            ratio=video_ratio,
            size=video_size,
            model=video_model,
            sound=video_sound,
        )
        endpoint = _direct_video_endpoint(mode, result.raw)
        if not result.ok or not result.url:
            quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
            return {
                "scene_id": scene.scene_id,
                "scene_index": scene.scene_index,
                "mode": mode,
                "endpoint": endpoint,
                "error": result.error or "场景视频生成失败",
                "quota_insufficient": quota_insufficient,
                "raw": result.raw,
                "attempts": attempt,
            }
        return GeneratedSceneVideo(
            scene_id=scene.scene_id,
            scene_index=scene.scene_index,
            duration_ms=scene.duration_ms,
            mode=mode,
            endpoint=endpoint,
            video_url=result.url,
            task_id=result.task_id,
            raw=result.raw,
        )

    async def run_scene(scene: SceneGenerationItem) -> GeneratedSceneVideo | dict[str, Any]:
        async with semaphore:
            last_failure: dict[str, Any] | None = None
            mode_override: DirectVideoMode | None = None
            for attempt in range(1, _SCENE_VIDEO_MAX_ATTEMPTS + 1):
                try:
                    item = await run_scene_once(scene, attempt, mode_override)
                except SceneVideoCapabilityError as exc:
                    return {
                        "scene_id": scene.scene_id,
                        "scene_index": scene.scene_index,
                        "error": str(exc),
                        "attempts": attempt,
                        "capability_mismatch": True,
                    }
                except Exception as exc:  # noqa: BLE001 - per-scene vendor failures must not abort sibling scenes.
                    last_failure = {
                        "scene_id": scene.scene_id,
                        "scene_index": scene.scene_index,
                        "error": str(exc) or exc.__class__.__name__,
                        "attempts": attempt,
                    }
                    continue
                if isinstance(item, GeneratedSceneVideo):
                    return item
                last_failure = item
                if is_quota_insufficient(item):
                    return item
                if _is_unsupported_task_type_failure(item):
                    # 旧合同没有实时能力快照时保留 legacy 首次选择；供应商明确拒绝 task_type 后，
                    # 自动场景只改试一次语义安全的文生视频，不再重复同一个无效 r2v。
                    if scene.generation_mode is None and mode_override is None and item.get("mode") == "reference_mode_video" and (supported_generation_types is None or _video_mode_is_supported("text_to_video", supported_generation_types)):
                        mode_override = "text_to_video"
                        continue
                    return item
                if _is_non_retryable_scene_failure(item):
                    return item
            return last_failure or {
                "scene_id": scene.scene_id,
                "scene_index": scene.scene_index,
                "error": "场景视频生成失败",
                "attempts": _SCENE_VIDEO_MAX_ATTEMPTS,
            }

    results = await asyncio.gather(*(run_scene(scene) for scene in sorted(body.scenes, key=lambda item: item.scene_index)))
    scene_videos = sorted((item for item in results if isinstance(item, GeneratedSceneVideo)), key=lambda item: item.scene_index)
    failed_scenes = [item for item in results if isinstance(item, dict)]
    quota_insufficient = any(is_quota_insufficient(item) for item in failed_scenes)
    endpoints = {scene.endpoint for scene in scene_videos}
    if failed_scenes and quota_insufficient:
        non_quota_failed = [item for item in failed_scenes if not is_quota_insufficient(item)]
        message = quota_resume_message("场景视频生成额度不足")
        if non_quota_failed:
            message = f"{message} 另有 {len(non_quota_failed)} 个分镜生成异常，请展开 failed_scenes 查看原因。"
    elif failed_scenes:
        message = "部分场景视频生成失败，请查看 failed_scenes。"
    else:
        message = "场景视频生成完成。"
    return GenerateSceneVideosResponse(
        ok=not failed_scenes,
        endpoint=next(iter(endpoints)) if len(endpoints) == 1 else "/api/video/mixed",
        scene_videos=scene_videos,
        failed_scenes=failed_scenes,
        quota_insufficient=quota_insufficient,
        message=message,
    )


@router.post("/generate-scenes", response_model=GenerateSceneVideosResponse)
async def generate_scene_videos(body: GenerateSceneVideosRequest, request: Request) -> GenerateSceneVideosResponse:
    result = await _generate_scene_videos_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("场景视频生成 Agent 完成同步生成", {"stage": "generate_scenes", "message": result.message, "ok": result.ok, "quota_insufficient": result.quota_insufficient}),
        category="experience",
        source_agent="scene_video_generation_agent",
        metadata={"source": "video_generate_scenes", "scene_count": len(body.scenes), "failed_count": len(result.failed_scenes)},
        memory_type="experience",
        infer=False,
    )
    return result


@router.post("/generate-scenes/start", response_model=GenerateSceneVideosJobStartResponse)
async def start_generate_scene_videos(body: GenerateSceneVideosRequest, request: Request) -> GenerateSceneVideosJobStartResponse:
    if not body.scenes:
        raise HTTPException(status_code=400, detail="scenes不能为空")
    _trim_scene_video_jobs()
    job_id = uuid.uuid4().hex
    _SCENE_VIDEO_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_scene_video_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return GenerateSceneVideosJobStartResponse(ok=True, job_id=job_id, status="running", message="场景视频生成任务已启动。")


@router.get("/generate-scenes/jobs/{job_id}", response_model=GenerateSceneVideosJobStatusResponse)
async def get_generate_scene_video_job(job_id: str) -> GenerateSceneVideosJobStatusResponse:
    job = _SCENE_VIDEO_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="场景视频生成任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, GenerateSceneVideosResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = GenerateSceneVideosResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return GenerateSceneVideosJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message="场景视频生成完成。" if status == "completed" else ("场景视频生成失败。" if status == "failed" else "场景视频生成中。"),
    )


async def _run_scene_video_job(job_id: str, body: GenerateSceneVideosRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _generate_scene_videos_response(body)
        _SCENE_VIDEO_JOBS[job_id] = {"status": "completed", "result": result, "error": None}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("场景视频生成 Agent 完成异步生成", {"stage": "generate_scenes", "message": result.message, "ok": result.ok, "quota_insufficient": result.quota_insufficient}),
            category="experience",
            source_agent="scene_video_generation_agent",
            metadata={"source": "video_generate_scenes_job", "job_id": job_id, "scene_count": len(body.scenes), "failed_count": len(result.failed_scenes)},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _SCENE_VIDEO_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        _record_video_job_failure(power_mem, user_id, job_id, "scene_video_generation_agent", "video_generate_scenes_job", exc)


async def _run_direct_video_job(job_id: str, body: GenerateDirectVideoRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _generate_direct_video_response(body)
        _DIRECT_VIDEO_JOBS[job_id] = {"status": "completed", "result": result, "error": None}
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("直接视频生成 Agent 完成异步生成", result.model_dump()),
            category="experience",
            source_agent="direct_video_generation_agent",
            metadata={"source": "video_generate_direct_job", "job_id": job_id, "mode": body.mode},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _DIRECT_VIDEO_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        _record_video_job_failure(power_mem, user_id, job_id, "direct_video_generation_agent", "video_generate_direct_job", exc)


async def _run_prepare_scene_package_job(job_id: str, body: PrepareScenePackagesRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        _SCENE_PACKAGE_JOBS[job_id] = {
            "status": "running",
            "stage": "prepare_scene_packages",
            "result": None,
            "error": None,
        }
        video_scene_packages = await _prepare_scene_packages_response(body)
        if not video_scene_packages.ok:
            _SCENE_PACKAGE_JOBS[job_id] = {
                "status": "completed",
                "stage": "completed",
                "result": PrepareScenePackagesJobResult(
                    ok=False,
                    videoScenePackages=video_scene_packages,
                    sceneAssetFailures=[],
                    message=video_scene_packages.message,
                ),
                "error": None,
            }
            record_power_mem_background(
                power_mem,
                user_id=user_id,
                content=concise_result_summary("视频场景包 Agent 异步生成结束", {"stage": "prepare_scene_packages", "message": video_scene_packages.message, "ok": False}),
                category="experience",
                source_agent="video_scene_package_agent",
                metadata={"source": "video_prepare_scene_packages_job", "job_id": job_id, "scene_count": len(video_scene_packages.scene_packages)},
                memory_type="experience",
                run_id=job_id,
                infer=False,
            )
            return

        _SCENE_PACKAGE_JOBS[job_id] = {
            "status": "running",
            "stage": "generate_scene_assets",
            "result": PrepareScenePackagesJobResult(
                ok=True,
                videoScenePackages=video_scene_packages,
                sceneAssetFailures=[],
                message="视频场景包已生成，正在生成场景参考图。",
            ),
            "error": None,
        }
        scene_assets = await _generate_scene_assets_response(
            GenerateSceneAssetsRequest(
                global_assets=video_scene_packages.global_assets,
                scene_packages=video_scene_packages.scene_packages,
                materials=body.materials,
                image_ratio=_scene_image_ratio(body.creation_contract),
                image_size=_scene_image_size(body.creation_contract),
                model=body.creation_contract.image_model if body.creation_contract is not None else None,
                creation_contract=body.creation_contract,
            )
        )
        scene_packages_for_review = PrepareScenePackagesResponse(
            **{
                **video_scene_packages.model_dump(),
                "global_assets": scene_assets.global_assets or video_scene_packages.global_assets,
                "scene_packages": scene_assets.scene_packages,
                "message": video_scene_packages.message if scene_assets.ok else scene_assets.message,
            }
        )
        quota_insufficient = bool(scene_assets.quota_insufficient)
        _SCENE_PACKAGE_JOBS[job_id] = {
            "status": "quota_paused" if quota_insufficient else "completed",
            "stage": "completed",
            "result": PrepareScenePackagesJobResult(
                ok=scene_packages_for_review.ok and not quota_insufficient,
                videoScenePackages=scene_packages_for_review,
                sceneAssetFailures=scene_assets.failed_assets,
                quota_insufficient=quota_insufficient,
                message=scene_assets.message if scene_assets.message else scene_packages_for_review.message,
            ),
            "error": None,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary(
                "视频场景包 Agent 异步生成场景包和参考图",
                {"stage": "completed", "message": scene_assets.message or scene_packages_for_review.message, "ok": scene_packages_for_review.ok and not quota_insufficient, "quota_insufficient": quota_insufficient},
            ),
            category="experience",
            source_agent="video_scene_package_agent",
            metadata={
                "source": "video_prepare_scene_packages_job",
                "job_id": job_id,
                "scene_count": len(scene_packages_for_review.scene_packages),
                "asset_failure_count": len(scene_assets.failed_assets),
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _SCENE_PACKAGE_JOBS[job_id] = {
            "status": "failed",
            "stage": str(_SCENE_PACKAGE_JOBS.get(job_id, {}).get("stage") or "prepare_scene_packages"),
            "result": None,
            "error": str(exc),
        }
        _record_video_job_failure(power_mem, user_id, job_id, "video_scene_package_agent", "video_prepare_scene_packages_job", exc)


async def _run_scene_asset_job(job_id: str, body: GenerateSceneAssetsRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _generate_scene_assets_response(body)
        _SCENE_ASSET_JOBS[job_id] = {
            "status": "quota_paused" if result.quota_insufficient else "completed",
            "stage": "completed",
            "result": result,
            "error": None,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("视频素材图 Agent 完成异步参考图生成", {"stage": "generate_scene_assets", "message": result.message, "ok": result.ok, "quota_insufficient": result.quota_insufficient}),
            category="experience",
            source_agent="video_scene_asset_agent",
            metadata={"source": "video_generate_scene_assets_job", "job_id": job_id, "failed_count": len(result.failed_assets)},
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _SCENE_ASSET_JOBS[job_id] = {
            "status": "failed",
            "stage": str(_SCENE_ASSET_JOBS.get(job_id, {}).get("stage") or "generate_scene_assets"),
            "result": None,
            "error": str(exc),
        }
        _record_video_job_failure(power_mem, user_id, job_id, "video_scene_asset_agent", "video_generate_scene_assets_job", exc)


async def _run_scene_asset_revision_job(
    job_id: str,
    body: ScenePackageAssetRevisionRequest,
    power_mem: Any = None,
    user_id: str | None = None,
) -> None:
    """执行图片分析和分镜精确补丁，并把终态保留给前端轮询。"""
    try:
        raw_result = await revise_scene_package_asset(
            operation=body.operation,
            asset_id=body.asset_id,
            asset_group=body.asset_group,
            asset_name=body.asset_name,
            source_image_url=body.source_image_url,
            new_image_url=body.new_image_url,
            generation_reference_url=body.generation_reference_url,
            replacement_metadata=body.replacement_metadata,
            global_assets=body.global_assets,
            scene_packages=body.scene_packages,
            image_analysis_skill=get_image_analysis_skill() if body.operation == "replace" else None,
        )
        result = ScenePackageAssetRevisionResponse(**raw_result)
        _SCENE_ASSET_REVISION_JOBS[job_id] = {
            "status": "completed",
            "result": result,
            "error": None,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary(
                "视频场景包 Agent 完成全局素材语义修订",
                {
                    "stage": "update_scene_package_asset",
                    "message": result.message,
                    "ok": result.ok,
                    "operation": body.operation,
                    "affected_scene_count": len(result.affected_scene_ids),
                },
            ),
            category="experience",
            source_agent="video_scene_package_agent",
            metadata={
                "source": "video_update_scene_package_asset",
                "job_id": job_id,
                "operation": body.operation,
                "affected_scene_count": len(result.affected_scene_ids),
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - 异步任务边界必须把失败保留给轮询端
        quota_insufficient = is_quota_insufficient(exc)
        _SCENE_ASSET_REVISION_JOBS[job_id] = {
            "status": "quota_paused" if quota_insufficient else "failed",
            "result": None,
            "error": str(exc),
        }
        _record_video_job_failure(
            power_mem,
            user_id,
            job_id,
            "video_scene_package_agent",
            "video_update_scene_package_asset",
            exc,
        )


def _trim_scene_video_jobs() -> None:
    if len(_SCENE_VIDEO_JOBS) < _MAX_SCENE_VIDEO_JOBS:
        return
    for job_id in list(_SCENE_VIDEO_JOBS.keys())[: len(_SCENE_VIDEO_JOBS) - _MAX_SCENE_VIDEO_JOBS + 1]:
        _SCENE_VIDEO_JOBS.pop(job_id, None)


def _trim_merge_video_jobs() -> None:
    if len(_MERGE_VIDEO_JOBS) < _MAX_MERGE_VIDEO_JOBS:
        return
    for job_id in list(_MERGE_VIDEO_JOBS.keys())[: len(_MERGE_VIDEO_JOBS) - _MAX_MERGE_VIDEO_JOBS + 1]:
        _MERGE_VIDEO_JOBS.pop(job_id, None)


def _trim_quality_review_jobs() -> None:
    if len(_QUALITY_REVIEW_JOBS) < _MAX_QUALITY_REVIEW_JOBS:
        return
    for job_id in list(_QUALITY_REVIEW_JOBS.keys())[: len(_QUALITY_REVIEW_JOBS) - _MAX_QUALITY_REVIEW_JOBS + 1]:
        _QUALITY_REVIEW_JOBS.pop(job_id, None)


def _trim_direct_video_jobs() -> None:
    if len(_DIRECT_VIDEO_JOBS) < _MAX_DIRECT_VIDEO_JOBS:
        return
    for job_id in list(_DIRECT_VIDEO_JOBS.keys())[: len(_DIRECT_VIDEO_JOBS) - _MAX_DIRECT_VIDEO_JOBS + 1]:
        _DIRECT_VIDEO_JOBS.pop(job_id, None)


def _trim_scene_package_jobs() -> None:
    if len(_SCENE_PACKAGE_JOBS) < _MAX_SCENE_PACKAGE_JOBS:
        return
    for job_id in list(_SCENE_PACKAGE_JOBS.keys())[: len(_SCENE_PACKAGE_JOBS) - _MAX_SCENE_PACKAGE_JOBS + 1]:
        _SCENE_PACKAGE_JOBS.pop(job_id, None)


def _trim_scene_asset_jobs() -> None:
    if len(_SCENE_ASSET_JOBS) < _MAX_SCENE_ASSET_JOBS:
        return
    for job_id in list(_SCENE_ASSET_JOBS.keys())[: len(_SCENE_ASSET_JOBS) - _MAX_SCENE_ASSET_JOBS + 1]:
        _SCENE_ASSET_JOBS.pop(job_id, None)


def _trim_scene_asset_revision_jobs() -> None:
    if len(_SCENE_ASSET_REVISION_JOBS) < _MAX_SCENE_ASSET_REVISION_JOBS:
        return
    overflow = len(_SCENE_ASSET_REVISION_JOBS) - _MAX_SCENE_ASSET_REVISION_JOBS + 1
    for job_id in list(_SCENE_ASSET_REVISION_JOBS.keys())[:overflow]:
        _SCENE_ASSET_REVISION_JOBS.pop(job_id, None)


def _scene_package_job_message(
    status: str,
    stage: str,
    result: PrepareScenePackagesJobResult | None,
    error: Any,
) -> str:
    if status == "completed":
        return result.message if result and result.message else "视频场景包和参考图已准备完成。"
    if status == "quota_paused":
        return quota_resume_message(result.message if result else None)
    if status == "failed":
        return str(error or "视频场景包生成失败。")
    if stage == "generate_scene_assets":
        return "视频场景包已生成，正在生成场景参考图。"
    return "视频场景包生成中。"


def _scene_asset_job_message(status: str, result: GenerateSceneAssetsResponse | None, error: Any) -> str:
    if status == "completed":
        return result.message if result and result.message else "场景参考图生成完成。"
    if status == "quota_paused":
        return quota_resume_message(result.message if result else None)
    if status == "failed":
        return str(error or "场景参考图生成失败。")
    return "场景参考图生成中。"


_VIDEO_MODE_CAPABILITY_ALIASES: dict[DirectVideoMode, tuple[str, ...]] = {
    "text_to_video": ("文生视频", "text_to_video", "t2v"),
    "image_to_video": ("图生视频", "首帧", "image_to_video", "i2v"),
    "two_image_to_video": ("首尾帧", "two_image_to_video", "first_last_frame", "flf2v"),
    "reference_mode_video": ("全能参考", "reference_mode_video", "omni_reference", "r2v"),
    "edit_video": ("编辑视频", "edit_video"),
    "extend_video": ("延伸视频", "extend_video"),
}


class SceneVideoCapabilityError(ValueError):
    """实时模型能力与请求模式不匹配；属于不可重试的业务错误。"""


def _select_scene_video_mode(
    scene: SceneGenerationItem,
    image_urls: list[str],
    *,
    creation_contract: VideoCreationContract | None = None,
) -> DirectVideoMode:
    requested_mode = scene.generation_mode
    text = f"{scene.prompt}\n{scene.storyline}\n{scene.narration}\n{scene.shot_description}".lower()
    video_urls = _dedupe_urls(scene.video_urls)
    if requested_mode is None:
        if video_urls and any(keyword in text for keyword in ("延伸", "续写", "extend")):
            requested_mode = "extend_video"
        elif video_urls and any(keyword in text for keyword in ("编辑", "修改", "调整", "edit")):
            requested_mode = "edit_video"
        elif video_urls or image_urls or scene.audio_urls:
            requested_mode = "reference_mode_video"
        else:
            requested_mode = "text_to_video"

    supported_types = _video_generation_types(creation_contract)
    if supported_types is None or _video_mode_is_supported(requested_mode, supported_types):
        _validate_scene_video_mode_materials(requested_mode, image_urls, video_urls)
        return requested_mode

    # 自动场景中的图片是角色/场景/道具参考，不是首尾帧。模型没有全能参考能力时，
    # 只能在实时配置明确支持文生视频时使用同一 Seedance Skill 提示词降级。
    if scene.generation_mode is None and requested_mode == "reference_mode_video" and _video_mode_is_supported("text_to_video", supported_types):
        return "text_to_video"
    raise SceneVideoCapabilityError(f"视频模型不支持当前生成模式 {requested_mode}；实时能力={sorted(supported_types)}")


def _video_generation_types(
    creation_contract: VideoCreationContract | None,
) -> set[str] | None:
    configured = creation_contract.video_model_capabilities.generation_types if creation_contract is not None else []
    if configured:
        return {_normalize_video_capability(item) for item in configured if _normalize_video_capability(item)}

    # 空能力代表旧合同 unknown，保持 legacy 选择；绝不根据型号名称猜测能力。
    return None


def _video_mode_is_supported(mode: DirectVideoMode, supported_types: set[str]) -> bool:
    return any(_normalize_video_capability(alias) in supported_types for alias in _VIDEO_MODE_CAPABILITY_ALIASES[mode])


def _normalize_video_capability(value: str) -> str:
    return "".join(character for character in str(value or "").strip().lower() if character.isalnum() or "\u4e00" <= character <= "\u9fff")


def _validate_scene_video_mode_materials(
    mode: DirectVideoMode,
    image_urls: list[str],
    video_urls: list[str],
) -> None:
    if mode == "image_to_video" and not image_urls:
        raise SceneVideoCapabilityError("image_to_video 至少需要 1 张首帧图片")
    if mode == "two_image_to_video" and len(image_urls) < 2:
        raise SceneVideoCapabilityError("two_image_to_video 至少需要首帧和尾帧 2 张图片")
    if mode == "reference_mode_video" and not image_urls and not video_urls:
        raise SceneVideoCapabilityError("reference_mode_video 至少需要 1 张参考图或 1 个参考视频")
    if mode in {"edit_video", "extend_video"} and not video_urls:
        raise SceneVideoCapabilityError(f"{mode} 至少需要 1 个参考视频")


def _validate_scene_video_request(
    *,
    mode: DirectVideoMode,
    image_urls: list[str],
    video_urls: list[str],
    audio_urls: list[str],
    duration: int,
    creation_contract: VideoCreationContract | None,
) -> None:
    """调用 content-app 前按当前合同完成一次可解释校验。"""
    if len(image_urls) > _MAX_REFERENCE_IMAGE_COUNT:
        raise SceneVideoCapabilityError(f"最多只能选择{_MAX_REFERENCE_IMAGE_COUNT}张参考图，当前选择了{len(image_urls)}张")
    if len(video_urls) > 3:
        raise SceneVideoCapabilityError(f"参考视频最多3个，当前为{len(video_urls)}个")
    if len(audio_urls) > 3:
        raise SceneVideoCapabilityError(f"参考音频最多3个，当前为{len(audio_urls)}个")
    _validate_scene_video_mode_materials(mode, image_urls, video_urls)
    if creation_contract is None:
        return
    supported_durations = creation_contract.video_model_capabilities.durations_sec
    if supported_durations and duration not in supported_durations:
        raise SceneVideoCapabilityError(f"视频模型 {creation_contract.video_model} 不支持 {duration} 秒分镜；实时支持时长={supported_durations}")


def _is_unsupported_task_type_failure(item: dict[str, Any]) -> bool:
    text = f"{item.get('error') or ''} {item.get('raw') or ''}".lower()
    return "task_type" in text and any(token in text for token in ("does not support", "not valid", "unsupported", "不支持"))


def _is_non_retryable_scene_failure(item: dict[str, Any]) -> bool:
    raw = item.get("raw")
    status_code = raw.get("status_code") if isinstance(raw, dict) else None
    if isinstance(status_code, int) and 400 <= status_code < 500 and status_code not in {408, 425, 429}:
        return True
    text = f"{item.get('error') or ''} {raw or ''}".lower()
    return any(
        marker in text
        for marker in (
            "参数验证失败",
            "模型价格配置不存在",
            "validation failed",
            "unsupported ratio",
            "unsupported image quality",
            "input image may contain real person",
            "参考图可能包含真人",
        )
    )


def _provider_video_duration_seconds(duration_ms: int, model: str | None) -> int:
    del model
    if duration_ms % 1000 != 0:
        raise ValueError("video duration must use integer seconds")
    seconds = duration_ms // 1000
    if seconds < 4 or seconds > 15:
        raise ValueError("video duration must be between 4-15 seconds")
    return seconds


_SCENE_PROMPT_FIELD_LABELS = ("视觉风格", "故事线", "镜头描述", "旁白", "转场")


def _build_scene_video_prompt(
    scene: SceneGenerationItem,
    *,
    visual_style: str = "",
) -> str:
    """按结构化字段生成唯一的分镜视频提示词，避免重复拼接历史复合 prompt。"""
    shot_text = _shot_description_text(scene.shot_description)
    if not any((scene.storyline, shot_text, scene.narration, scene.transition)):
        return str(scene.prompt or "").strip()
    style = _normalize_scene_prompt_field(visual_style, "视觉风格")
    if not style:
        style = _extract_legacy_visual_style(scene.prompt)
    fields = (
        ("视觉风格", style),
        ("故事线", scene.storyline),
        ("镜头描述", shot_text),
        ("旁白", scene.narration),
        ("转场", scene.transition),
    )
    pieces: list[str] = []
    seen_non_shot_values: set[str] = set()
    for label, value in fields:
        normalized = _normalize_scene_prompt_field(value, label)
        if not normalized:
            continue
        if label != "镜头描述":
            dedupe_key = re.sub(r"\s+", "", normalized)
            if dedupe_key in seen_non_shot_values:
                continue
            seen_non_shot_values.add(dedupe_key)
        pieces.append(f"{label}：{normalized}")
    return "\n".join(pieces)


def _normalize_scene_prompt_field(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    label_pattern = rf"^(?:{re.escape(label)}\s*[：:]\s*)+"
    text = re.sub(label_pattern, "", text).strip()
    normalized_lines: list[str] = []
    for line in text.splitlines():
        normalized_line = re.sub(r"[ \t]+", " ", line).strip()
        if normalized_line and (not normalized_lines or normalized_lines[-1] != normalized_line):
            normalized_lines.append(normalized_line)
    return "\n".join(normalized_lines)


def _extract_legacy_visual_style(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return ""
    labels_pattern = "|".join(re.escape(label) for label in _SCENE_PROMPT_FIELD_LABELS)
    match = re.search(
        rf"(?:^|[\n；;])\s*视觉风格\s*[：:]\s*(.*?)(?=(?:[\n；;]\s*(?:{labels_pattern})\s*[：:])|$)",
        text,
        flags=re.DOTALL,
    )
    if match:
        return _normalize_scene_prompt_field(match.group(1), "视觉风格")
    if re.search(rf"(?:^|[\n；;])\s*(?:{labels_pattern})\s*[：:]", text):
        return ""
    return _normalize_scene_prompt_field(text, "视觉风格")


def _shot_description_text(shot_description: dict[str, Any]) -> str:
    text = shot_description.get("text") or shot_description.get("description_text") or shot_description.get("shotText")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return "；".join(
        part
        for part in (
            f"时间范围={shot_description.get('time_range') or shot_description.get('timeRange') or ''}",
            f"地点标注={shot_description.get('location') or ''}",
            f"角色标注={_join_shot_value(shot_description.get('characters'))}",
            f"道具标注={_join_shot_value(shot_description.get('props'))}",
            f"景别={shot_description.get('shot_size') or shot_description.get('shotSize') or ''}",
            f"镜头描述={shot_description.get('description') or ''}",
            f"视觉风格={shot_description.get('visual_style') or ''}",
        )
        if not part.endswith("=")
    )


def _join_shot_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value if item)
    return str(value or "")


def _scene_reference_image_urls(scene: SceneGenerationItem) -> list[str]:
    urls = list(scene.image_urls)
    mentions = scene.shot_description.get("mentions")
    if isinstance(mentions, list):
        for mention in mentions:
            urls.extend(_urls_from_value(mention))
    return _dedupe_urls(urls)


def _urls_from_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if _is_reference_asset_url(text) else []
    if isinstance(value, dict):
        urls: list[str] = []
        for key in ("generation_reference_url", "generationReferenceUrl", "asset_reference", "assetReference"):
            item = value.get(key)
            if isinstance(item, str) and _is_reference_asset_url(item.strip()):
                urls.append(item.strip())
        if urls:
            return urls
        for key in ("image_url", "imageUrl", "url", "download_url", "downloadUrl", "src"):
            item = value.get(key)
            if isinstance(item, str) and _is_reference_asset_url(item.strip()):
                urls.append(item.strip())
        for key in ("images", "image_urls", "imageUrls", "reference_image_urls", "referenceImageUrls"):
            urls.extend(_urls_from_value(value.get(key)))
        return urls
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_urls_from_value(item))
        return urls
    return []


def _is_reference_asset_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "asset://"))


async def _run_scene_video_generation(
    *,
    skill: Any,
    mode: DirectVideoMode,
    prompt: str,
    image_urls: list[str],
    video_urls: list[str],
    audio_urls: list[str],
    duration: int,
    ratio: str,
    size: str,
    model: str | None,
    sound: str,
):
    if mode == "text_to_video":
        return await skill.text_to_video(prompt=prompt, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
    if mode == "image_to_video":
        if not image_urls:
            raise SceneVideoCapabilityError("image_to_video 至少需要 1 张首帧图片")
        return await skill.image_to_video(image_url=image_urls[0], prompt=prompt, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
    if mode == "two_image_to_video":
        if len(image_urls) < 2:
            raise SceneVideoCapabilityError("two_image_to_video 至少需要首帧和尾帧 2 张图片")
        return await skill.two_image_to_video(
            first_frame_image_url=image_urls[0],
            last_frame_image_url=image_urls[1],
            prompt=prompt,
            duration=duration,
            ratio=ratio,
            size=size,
            model=model,
            sound=sound,
        )
    if mode == "edit_video":
        if not video_urls:
            raise SceneVideoCapabilityError("edit_video 至少需要 1 个参考视频")
        return await skill.edit_video(
            ref_video=video_urls[0],
            prompt=prompt,
            ref_image=image_urls[0] if image_urls else None,
            duration=duration,
            ratio=ratio,
            size=size,
            model=model,
            sound=sound,
        )
    if mode == "extend_video":
        if not video_urls:
            raise SceneVideoCapabilityError("extend_video 至少需要 1 个参考视频")
        return await skill.extend_video(video_url=video_urls[0], prompt=prompt, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
    return await skill.reference_mode_video(
        prompt=prompt,
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
        duration=duration,
        ratio=ratio,
        size=size,
        model=model,
        sound=sound,
    )


def _direct_video_endpoint(mode: str, raw: dict[str, Any]) -> str:
    endpoint = raw.get("endpoint") if isinstance(raw, dict) else None
    if isinstance(endpoint, str) and endpoint:
        return endpoint
    return {
        "text_to_video": "/api/video/text-to-video",
        "image_to_video": "/api/video/image-to-video",
        "two_image_to_video": "/api/video/two-image-to-video",
        "reference_mode_video": "/api/video/reference-mode-video",
        "edit_video": "/api/video/edit-video",
        "extend_video": "/api/video/extend-video",
    }.get(mode, "/api/video/reference-mode-video")


def _with_video_memory(body: PrepareScenePackagesRequest, memories: list[Any]) -> PrepareScenePackagesRequest:
    intake_context, profile = with_semantic_memory(body.intake_context, memories)
    selected_direction = dict(body.selected_direction)
    memory_summary = semantic_memory_text(intake_context.get("semantic_memory"))
    if memory_summary:
        selected_direction["semantic_memory"] = intake_context["semantic_memory"]
        selected_direction.setdefault("product_creative_profile", profile)
    # 记忆只作为内部决策上下文，不能改写用户已审核的 plan.md；基于原模型更新也能保留新增合同字段。
    return body.model_copy(
        update={
            "selected_direction": selected_direction,
            "intake_context": intake_context,
        }
    )


def _scene_image_ratio(contract: VideoCreationContract | None) -> str:
    if contract is None:
        return "9:16"
    if not contract.scene_image_ratio:
        raise ValueError("video creation contract is missing scene_image_ratio")
    return contract.scene_image_ratio


def _scene_image_size(contract: VideoCreationContract | None) -> str:
    if contract is None:
        return "1080p"
    if not contract.scene_image_size:
        raise ValueError("video creation contract is missing scene_image_size")
    return contract.scene_image_size


def _asset_count(global_assets: dict[str, Any]) -> int:
    return len(_list_of_dicts(global_assets.get("characters"))) + len(_list_of_dicts(global_assets.get("scenes"))) + len(_list_of_dicts(global_assets.get("props")))


def _record_video_job_failure(
    power_mem: Any,
    user_id: str | None,
    job_id: str,
    source_agent: str,
    source: str,
    exc: Exception,
) -> None:
    record_power_mem_background(
        power_mem,
        user_id=user_id,
        content=f"视频 Agent job 失败；source={source}；error={str(exc)[:300]}",
        category="experience",
        source_agent=source_agent,
        metadata={"source": source, "job_id": job_id, "status": "failed"},
        memory_type="experience",
        run_id=job_id,
        infer=False,
    )


def _merge_video_job_status(result: MergeSceneVideosResponse) -> str:
    if result.quota_insufficient:
        return "quota_paused"
    return "completed" if result.ok else "failed"


def _merge_video_job_error(result: MergeSceneVideosResponse | None, error: Any = None) -> str | None:
    if result and not result.ok and not result.quota_insufficient:
        return result.error or result.message or "视频合并失败。"
    if error:
        return str(error)
    return None


def _merge_video_job_message(status: str, result: MergeSceneVideosResponse | None, error: Any = None) -> str:
    if result and result.message:
        return result.message
    if status == "completed":
        return "视频合并完成。"
    if status == "quota_paused":
        return "视频合并额度不足，已暂停。"
    if status == "failed":
        return str(error or "视频合并失败。")
    return "视频合并中。"


@router.post("/merge", response_model=MergeSceneVideosResponse)
async def merge_scene_videos(body: MergeSceneVideosRequest, request: Request) -> MergeSceneVideosResponse:
    response = await _merge_scene_videos_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("视频合并 Agent 同步合并", response.model_dump()),
        category="experience",
        source_agent="video_merge_agent",
        metadata={
            "source": "video_merge",
            "scene_count": len(response.scene_videos),
            "task_id": response.task_id,
            "passthrough": bool(response.raw.get("passthrough")),
        },
        memory_type="experience",
        run_id=response.task_id,
        infer=False,
    )
    return response


@router.post("/merge/start", response_model=MergeSceneVideosJobStartResponse)
async def start_merge_scene_videos(body: MergeSceneVideosRequest, request: Request) -> MergeSceneVideosJobStartResponse:
    if not any(scene.video_url for scene in body.scene_videos):
        raise HTTPException(status_code=400, detail="至少需要1个场景视频才能合并")
    _trim_merge_video_jobs()
    job_id = uuid.uuid4().hex
    _MERGE_VIDEO_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_merge_video_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return MergeSceneVideosJobStartResponse(ok=True, job_id=job_id, status="running", message="视频合并任务已启动。")


@router.get("/merge/jobs/{job_id}", response_model=MergeSceneVideosJobStatusResponse)
async def get_merge_scene_video_job(job_id: str) -> MergeSceneVideosJobStatusResponse:
    job = _MERGE_VIDEO_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="视频合并任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, MergeSceneVideosResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = MergeSceneVideosResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return MergeSceneVideosJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_merge_video_job_message(status, result_payload, error),
    )


async def _merge_scene_videos_response(body: MergeSceneVideosRequest) -> MergeSceneVideosResponse:
    ordered_scenes = sorted(body.scene_videos, key=lambda scene: scene.scene_index if scene.scene_index is not None else 0)
    video_urls = [scene.video_url for scene in ordered_scenes if scene.video_url]
    if not video_urls:
        raise HTTPException(status_code=400, detail="至少需要1个场景视频才能合并")
    if len(video_urls) == 1:
        return MergeSceneVideosResponse(
            ok=True,
            endpoint="/api/video/merge",
            merged_video_url=video_urls[0],
            scene_videos=ordered_scenes,
            message="只有一个场景视频，已直接作为合成视频返回。",
            raw={"passthrough": True, "reason": "single_scene"},
        )

    result = await get_video_skill().merge_videos(
        video_urls=video_urls,
        duration=body.duration,
        size=body.size,
        model=body.model,
    )
    endpoint = result.raw.get("endpoint")
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    message = "视频合并完成。" if result.ok else (result.error or "视频合并失败。")
    if quota_insufficient:
        message = quota_resume_message(result.error)
    response = MergeSceneVideosResponse(
        ok=result.ok,
        endpoint=endpoint if isinstance(endpoint, str) and endpoint else "/api/video/merge",
        merged_video_url=result.url,
        task_id=result.task_id,
        scene_videos=ordered_scenes,
        error=result.error,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )
    return response


async def _run_merge_video_job(job_id: str, body: MergeSceneVideosRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _merge_scene_videos_response(body)
        status = _merge_video_job_status(result)
        error = _merge_video_job_error(result)
        _MERGE_VIDEO_JOBS[job_id] = {
            "status": status,
            "result": result,
            "error": error,
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary("视频合并 Agent 异步合并结束", result.model_dump()),
            category="experience",
            source_agent="video_merge_agent",
            metadata={
                "source": "video_merge_job",
                "job_id": job_id,
                "status": status,
                "scene_count": len(result.scene_videos),
                "task_id": result.task_id,
                "passthrough": bool(result.raw.get("passthrough")),
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _MERGE_VIDEO_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        _record_video_job_failure(power_mem, user_id, job_id, "video_merge_agent", "video_merge_job", exc)


def _quality_response_from_core(result: CoreVideoQCResponse, *, success_message: str = "视频质检完成。") -> VideoQualityReviewResponse:
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    message = success_message if result.ok else (result.error or "视频质检失败。")
    if quota_insufficient:
        message = quota_resume_message(result.error)
    return VideoQualityReviewResponse(
        ok=result.ok,
        endpoint=result.endpoint,
        task_id=result.task_id,
        passed=result.passed,
        score=result.score,
        summary_markdown=result.summary_markdown,
        quality_report_markdown=result.quality_report_markdown,
        issues=[issue.model_dump() for issue in result.issues],
        affected_scene_ids=result.affected_scene_ids,
        target_scene_ids=result.target_scene_ids,
        excluded_scene_ids=result.excluded_scene_ids,
        revision_prompt=result.revision_prompt,
        check_results=[item.model_dump() for item in result.check_results],
        error=result.error,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


async def _quality_review_response(body: VideoQualityReviewRequest) -> VideoQualityReviewResponse:
    brief = {
        **body.brief,
        "plan": body.plan,
        "form_values": body.form_values,
        "intake_context": body.intake_context,
        "selected_direction": body.selected_direction,
    }
    result = await review_video_quality(
        VideoQCRequest(
            merged_video_url=body.merged_video_url,
            scene_videos=[scene.model_dump() for scene in body.scene_videos],
            scene_packages=body.scene_packages,
            original_scene_packages=body.original_scene_packages,
            brief=brief,
            materials=body.materials,
            platform=body.platform,
            ratio=body.ratio,
            size=body.size,
            expected_duration_sec=body.expected_duration_sec,
            user_feedback=body.user_feedback,
            checks=body.checks or list(VideoQCRequest().checks),
        ),
        skill=get_video_quality_review_skill(),
    )
    return _quality_response_from_core(result)


def _quality_review_job_message(status: str, result: VideoQualityReviewResponse | None, error: Any) -> str:
    if status == "completed":
        return result.message if result and result.message else "视频质检完成。"
    if status == "quota_paused":
        return result.message if result and result.message else "视频质检额度不足，已暂停。"
    if status == "failed":
        return str(error or (result.message if result and result.message else None) or "视频质检失败。")
    return "视频质检中。"


def _quality_review_job_status(result: VideoQualityReviewResponse) -> str:
    if result.quota_insufficient:
        return "quota_paused"
    return "completed" if result.ok else "failed"


def _quality_review_job_error(result: VideoQualityReviewResponse, fallback: Any = None) -> str | None:
    if result.ok and not result.quota_insufficient:
        return None
    return str(result.error or result.message or fallback or "视频质检失败。")


@router.post("/quality-review/start", response_model=VideoQualityReviewJobStartResponse)
async def start_quality_review(body: VideoQualityReviewRequest, request: Request) -> VideoQualityReviewJobStartResponse:
    if not body.merged_video_url:
        raise HTTPException(status_code=400, detail="merged_video_url不能为空")
    _trim_quality_review_jobs()
    job_id = uuid.uuid4().hex
    _QUALITY_REVIEW_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_quality_review_job(job_id, body, power_mem_service(request), await current_user_id(request)))
    return VideoQualityReviewJobStartResponse(ok=True, job_id=job_id, status="running", message="视频质检任务已启动。")


@router.get("/quality-review/jobs/{job_id}", response_model=VideoQualityReviewJobStatusResponse)
async def get_quality_review_job(job_id: str) -> VideoQualityReviewJobStatusResponse:
    job = _QUALITY_REVIEW_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="视频质检任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, VideoQualityReviewResponse):
        result_payload = result
    elif isinstance(result, dict):
        result_payload = VideoQualityReviewResponse(**result)
    else:
        result_payload = None
    status = str(job.get("status") or "running")
    error = job.get("error")
    return VideoQualityReviewJobStatusResponse(
        ok=status != "failed",
        job_id=job_id,
        status=status,
        result=result_payload,
        error=str(error) if error else None,
        message=_quality_review_job_message(status, result_payload, error),
    )


async def _run_quality_review_job(job_id: str, body: VideoQualityReviewRequest, power_mem: Any = None, user_id: str | None = None) -> None:
    try:
        result = await _quality_review_response(body)
        status = _quality_review_job_status(result)
        _QUALITY_REVIEW_JOBS[job_id] = {
            "status": status,
            "result": result,
            "error": _quality_review_job_error(result),
        }
        record_power_mem_background(
            power_mem,
            user_id=user_id,
            content=concise_result_summary(
                "视频质检 Agent 完成异步质量评审",
                {"stage": "quality_review", "message": result.message, "ok": result.ok, "quota_insufficient": result.quota_insufficient},
            ),
            category="experience",
            source_agent="video_quality_review_agent",
            metadata={
                "source": "video_quality_review_job",
                "job_id": job_id,
                "passed": result.passed,
                "score": result.score,
                "issue_count": len(result.issues),
            },
            memory_type="experience",
            run_id=job_id,
            infer=False,
        )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _QUALITY_REVIEW_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}
        _record_video_job_failure(power_mem, user_id, job_id, "video_quality_review_agent", "video_quality_review_job", exc)


@router.post("/quality-review", response_model=VideoQualityReviewResponse)
async def quality_review(body: VideoQualityReviewRequest, request: Request) -> VideoQualityReviewResponse:
    response = await _quality_review_response(body)
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("视频质检 Agent 完成质量评审", {"stage": "quality_review", "message": response.message, "ok": response.ok, "quota_insufficient": response.quota_insufficient}),
        category="experience",
        source_agent="video_quality_review_agent",
        metadata={"source": "video_quality_review", "passed": response.passed, "score": response.score, "issue_count": len(response.issues)},
        memory_type="experience",
        run_id=response.task_id,
        infer=False,
    )
    return response


def _clone_mapping(value: dict[str, Any]) -> dict[str, Any]:
    cloned: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, list):
            cloned[key] = [_clone_mapping(child) if isinstance(child, dict) else child for child in item]
        elif isinstance(item, dict):
            cloned[key] = _clone_mapping(item)
        else:
            cloned[key] = item
    return cloned


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _build_media_extraction_text(prompt: str, materials: list[dict[str, Any]]) -> str:
    pieces = [prompt]
    for material in materials:
        pieces.extend(_collect_strings(material))
    return "\n".join(piece for piece in pieces if piece)


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_collect_strings(item))
        return collected
    if isinstance(value, dict):
        collected: list[str] = []
        for item in value.values():
            collected.extend(_collect_strings(item))
        return collected
    return []


def _dedupe_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = str(url).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _is_probable_video_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    video_extensions = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".m3u8")
    image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")
    if path.endswith(video_extensions):
        return True
    if path.endswith(image_extensions):
        return False
    video_hosts = ("douyin.com", "xhslink.com", "xiaohongshu.com", "bilibili.com", "b23.tv", "kuaishou.com")
    return any(host == domain or host.endswith(f".{domain}") for domain in video_hosts) or bool(parsed.scheme and parsed.netloc)
