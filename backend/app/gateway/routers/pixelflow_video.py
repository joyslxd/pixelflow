"""PixelFlow v2 视频生成与分析 API。"""

from __future__ import annotations

import asyncio
import math
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.pixelflow_memory import concise_result_summary, current_user_id, power_mem_service, record_power_mem_background, search_power_mem
from pixelflow.generate.scene_packages import prepare_video_scene_packages_with_llm
from pixelflow.memory import semantic_memory_text, with_semantic_memory
from pixelflow.qc import VideoQCRequest, review_video_quality
from pixelflow.qc import VideoQCResponse as CoreVideoQCResponse
from pixelflow.skills import (
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
_DIRECT_VIDEO_JOBS: dict[str, dict[str, Any]] = {}
_MAX_DIRECT_VIDEO_JOBS = 100
_SCENE_PACKAGE_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_PACKAGE_JOBS = 100
_SCENE_ASSET_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_ASSET_JOBS = 100
_MAX_REFERENCE_IMAGE_COUNT = 9
_SCENE_VIDEO_MAX_CONCURRENCY = 100
_SCENE_VIDEO_MAX_ATTEMPTS = 3
_SEEDANCE_MIN_SINGLE_CALL_DURATION = 5
_SEEDANCE_MAX_SINGLE_CALL_DURATION = 10

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
    duration_ms: int = Field(gt=0, le=15_000)
    prompt: str
    storyline: str = ""
    shot_description: dict[str, Any] = Field(default_factory=dict)
    narration: str = ""
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


class PrepareScenePackagesRequest(BaseModel):
    form_values: dict[str, Any] = Field(default_factory=dict)
    plan_markdown: str = ""
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    target_duration_ms: int = 30_000
    intake_context: dict[str, Any] = Field(default_factory=dict)


class PrepareScenePackagesResponse(BaseModel):
    ok: bool
    message: str = ""
    requires_confirmation: bool = True
    review_timeout_sec: int | None = None
    target_duration_ms: int
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)


class GenerateSceneAssetsRequest(BaseModel):
    global_assets: dict[str, Any] = Field(default_factory=dict)
    scene_packages: list[dict[str, Any]]
    image_size: str = "1080p"
    model: str | None = None


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


class VideoFlawAnalysisRequest(BaseModel):
    merged_video_url: str
    scene_videos: list[SceneVideo]
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    original_scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    form_values: dict[str, Any] = Field(default_factory=dict)
    intake_context: dict[str, Any] = Field(default_factory=dict)
    selected_direction: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    user_feedback: str | None = None


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


class VideoFlawAnalysisResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/creative/analyze_video_flaws"
    task_id: str | None = None
    passed: bool = True
    score: float = 1.0
    summary_markdown: str = ""
    flaw_analysis_markdown: str = ""
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


class VideoQualityReviewResponse(VideoFlawAnalysisResponse):
    pass


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
    result = await prepare_video_scene_packages_with_llm(
        form_values=body.form_values,
        plan_markdown=body.plan_markdown,
        selected_direction=body.selected_direction,
        materials=body.materials,
        target_duration_ms=body.target_duration_ms,
    )
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


async def _generate_scene_assets_response(body: GenerateSceneAssetsRequest) -> GenerateSceneAssetsResponse:
    if not body.scene_packages:
        raise HTTPException(status_code=400, detail="scene_packages不能为空")

    image_skill = get_image_skill()
    enriched = [_clone_mapping(scene) for scene in body.scene_packages]
    global_assets = _clone_mapping(body.global_assets) if body.global_assets else {}
    failed_assets: list[dict[str, Any]] = []

    async def generate_asset(prompt: str, ratio: str, context: dict[str, Any]) -> tuple[list[str], bool]:
        result = await image_skill.text_to_image(
            prompt=prompt,
            ratio=ratio,
            size=body.image_size,
            model=body.model,
            num_images=1,
        )
        if not result.ok:
            quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
            failed_assets.append({
                **context,
                "error": result.error or "图片生成失败",
                "quota_insufficient": quota_insufficient,
                "raw": result.raw,
            })
            return [], quota_insufficient
        urls = [str(image.get("url") or image.get("download_url")) for image in result.images if image.get("url") or image.get("download_url")]
        if not urls:
            failed_assets.append({**context, "error": "图片生成结果没有URL", "raw": result.raw})
        return urls, False

    asset_jobs: list[tuple[dict[str, Any], str, str, str, dict[str, Any]]] = []
    if global_assets:
        for character in _list_of_dicts(global_assets.get("characters")):
            prompt = str(character.get("three_view_prompt") or character.get("image_prompt") or character.get("description") or "").strip()
            if prompt:
                asset_jobs.append((character, "three_view_images", prompt, "1:1", {"asset_id": character.get("asset_id"), "asset_type": "character"}))
        for scene_image in _list_of_dicts(global_assets.get("scenes")):
            prompt = str(scene_image.get("image_prompt") or scene_image.get("description") or "").strip()
            if prompt:
                asset_jobs.append((scene_image, "images", prompt, "9:16", {"asset_id": scene_image.get("asset_id"), "asset_type": "scene_image"}))
        for prop_image in _list_of_dicts(global_assets.get("props")):
            prompt = str(prop_image.get("image_prompt") or prop_image.get("description") or prop_image.get("name") or "").strip()
            if prompt:
                asset_jobs.append((prop_image, "images", prompt, "1:1", {"asset_id": prop_image.get("asset_id"), "asset_type": "prop_image"}))
    else:
        for scene in enriched:
            scene_id = str(scene.get("scene_id") or "")
            for character in _list_of_dicts(scene.get("characters")):
                prompt = str(character.get("three_view_prompt") or character.get("image_prompt") or character.get("description") or "").strip()
                if prompt:
                    asset_jobs.append((character, "three_view_images", prompt, "1:1", {"scene_id": scene_id, "asset_type": "character"}))
            for scene_image in _list_of_dicts(scene.get("scene_images")):
                prompt = str(scene_image.get("image_prompt") or scene_image.get("description") or "").strip()
                if prompt:
                    asset_jobs.append((scene_image, "images", prompt, "9:16", {"scene_id": scene_id, "asset_type": "scene_image"}))
            for prop_image in _list_of_dicts(scene.get("prop_images")):
                prompt = str(prop_image.get("image_prompt") or prop_image.get("description") or prop_image.get("name") or "").strip()
                if prompt:
                    asset_jobs.append((prop_image, "images", prompt, "1:1", {"scene_id": scene_id, "asset_type": "prop_image"}))

    for target, field_name, prompt, ratio, context in asset_jobs:
        urls, quota_insufficient = await generate_asset(prompt, ratio, context)
        target[field_name] = urls
        if quota_insufficient:
            return GenerateSceneAssetsResponse(
                ok=False,
                global_assets=global_assets,
                scene_packages=enriched,
                failed_assets=failed_assets,
                quota_insufficient=True,
                message=quota_resume_message(failed_assets[-1].get("error") if failed_assets else None),
            )

    return GenerateSceneAssetsResponse(
        ok=not failed_assets,
        global_assets=global_assets,
        scene_packages=enriched,
        failed_assets=failed_assets,
        message="场景资产图生成完成。" if not failed_assets else "部分场景资产图生成失败，请查看 failed_assets。",
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
    semaphore = asyncio.Semaphore(max(1, min(_SCENE_VIDEO_MAX_CONCURRENCY, len(body.scenes))))

    async def run_scene_once(scene: SceneGenerationItem, attempt: int) -> GeneratedSceneVideo | dict[str, Any]:
        image_urls = _scene_reference_image_urls(scene)
        if len(image_urls) > _MAX_REFERENCE_IMAGE_COUNT:
            return {
                "scene_id": scene.scene_id,
                "scene_index": scene.scene_index,
                "error": f"最多只能选择9张参考图，当前选择了{len(image_urls)}张。",
                "image_count": len(image_urls),
                "attempts": attempt,
            }
        mode = _select_scene_video_mode(scene, image_urls)
        prompt = _build_scene_video_prompt(scene)
        duration = _provider_video_duration_seconds(scene.duration_ms, body.model)
        result = await _run_scene_video_generation(
            skill=skill,
            mode=mode,
            prompt=prompt,
            image_urls=image_urls,
            video_urls=_dedupe_urls(scene.video_urls),
            audio_urls=_dedupe_urls(scene.audio_urls),
            duration=duration,
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
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
            for attempt in range(1, _SCENE_VIDEO_MAX_ATTEMPTS + 1):
                try:
                    item = await run_scene_once(scene, attempt)
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
                image_size="1080p",
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


def _trim_scene_video_jobs() -> None:
    if len(_SCENE_VIDEO_JOBS) < _MAX_SCENE_VIDEO_JOBS:
        return
    for job_id in list(_SCENE_VIDEO_JOBS.keys())[: len(_SCENE_VIDEO_JOBS) - _MAX_SCENE_VIDEO_JOBS + 1]:
        _SCENE_VIDEO_JOBS.pop(job_id, None)


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


def _select_scene_video_mode(scene: SceneGenerationItem, image_urls: list[str]) -> DirectVideoMode:
    if scene.generation_mode:
        return scene.generation_mode
    text = f"{scene.prompt}\n{scene.storyline}\n{scene.narration}\n{scene.shot_description}".lower()
    video_urls = _dedupe_urls(scene.video_urls)
    if video_urls and any(keyword in text for keyword in ("延伸", "续写", "extend")):
        return "extend_video"
    if video_urls and any(keyword in text for keyword in ("编辑", "修改", "调整", "edit")):
        return "edit_video"
    if video_urls or image_urls or scene.audio_urls:
        return "reference_mode_video"
    return "text_to_video"


def _provider_video_duration_seconds(duration_ms: int, model: str | None) -> int:
    seconds = max(1, math.ceil(duration_ms / 1000))
    if model is None or model == "seedance-2.0":
        return max(_SEEDANCE_MIN_SINGLE_CALL_DURATION, min(_SEEDANCE_MAX_SINGLE_CALL_DURATION, seconds))
    return max(1, min(15, seconds))


def _build_scene_video_prompt(scene: SceneGenerationItem) -> str:
    pieces = [scene.prompt]
    if scene.storyline:
        pieces.append(f"故事线：{scene.storyline}")
    if scene.shot_description:
        pieces.append(f"镜头描述：{_shot_description_text(scene.shot_description)}")
    if scene.narration:
        pieces.append(f"旁白：{scene.narration}")
    return "\n".join(piece for piece in pieces if piece).strip()


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
        return [text] if text.startswith(("http://", "https://")) else []
    if isinstance(value, dict):
        urls: list[str] = []
        for key in ("image_url", "imageUrl", "url", "download_url", "downloadUrl", "src"):
            item = value.get(key)
            if isinstance(item, str) and item.strip().startswith(("http://", "https://")):
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
            return await skill.reference_mode_video(prompt=prompt, image_urls=image_urls, video_urls=video_urls, audio_urls=audio_urls, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
        return await skill.image_to_video(image_url=image_urls[0], prompt=prompt, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
    if mode == "two_image_to_video":
        if len(image_urls) < 2:
            return await skill.reference_mode_video(prompt=prompt, image_urls=image_urls, video_urls=video_urls, audio_urls=audio_urls, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
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
            return await skill.reference_mode_video(prompt=prompt, image_urls=image_urls, video_urls=video_urls, audio_urls=audio_urls, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
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
            return await skill.reference_mode_video(prompt=prompt, image_urls=image_urls, video_urls=video_urls, audio_urls=audio_urls, duration=duration, ratio=ratio, size=size, model=model, sound=sound)
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
    plan_markdown = body.plan_markdown
    if memory_summary and memory_summary not in plan_markdown:
        plan_markdown = f"{plan_markdown}\n\n长期记忆约束：{memory_summary}".strip()
    return PrepareScenePackagesRequest(
        form_values=body.form_values,
        plan_markdown=plan_markdown,
        selected_direction=selected_direction,
        materials=body.materials,
        target_duration_ms=body.target_duration_ms,
        intake_context=intake_context,
    )


def _asset_count(global_assets: dict[str, Any]) -> int:
    return (
        len(_list_of_dicts(global_assets.get("characters")))
        + len(_list_of_dicts(global_assets.get("scenes")))
        + len(_list_of_dicts(global_assets.get("props")))
    )


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


@router.post("/merge", response_model=MergeSceneVideosResponse)
async def merge_scene_videos(body: MergeSceneVideosRequest, request: Request) -> MergeSceneVideosResponse:
    ordered_scenes = sorted(body.scene_videos, key=lambda scene: scene.scene_index if scene.scene_index is not None else 0)
    video_urls = [scene.video_url for scene in ordered_scenes if scene.video_url]
    if not video_urls:
        raise HTTPException(status_code=400, detail="至少需要1个场景视频才能合并")
    if len(video_urls) == 1:
        response = MergeSceneVideosResponse(
            ok=True,
            endpoint="/api/video/merge",
            merged_video_url=video_urls[0],
            scene_videos=ordered_scenes,
            message="只有一个场景视频，已直接作为合成视频返回。",
            raw={"passthrough": True, "reason": "single_scene"},
        )
        record_power_mem_background(
            power_mem_service(request),
            user_id=await current_user_id(request),
            content=concise_result_summary("视频合并 Agent 单分镜直返", response.model_dump()),
            category="experience",
            source_agent="video_merge_agent",
            metadata={"source": "video_merge", "scene_count": len(video_urls), "passthrough": True},
            memory_type="experience",
            infer=False,
        )
        return response

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
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("视频合并 Agent 完成合并", response.model_dump()),
        category="experience",
        source_agent="video_merge_agent",
        metadata={"source": "video_merge", "scene_count": len(video_urls), "task_id": result.task_id},
        memory_type="experience",
        run_id=result.task_id,
        infer=False,
    )
    return response


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
        flaw_analysis_markdown=result.flaw_analysis_markdown,
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


def _is_product_consistency_issue(issue: dict[str, Any]) -> bool:
    category = str(issue.get("category") or "")
    if category:
        return category == "product_consistency"
    code = str(issue.get("code") or "")
    return code in {"product_consistency", "flaw", "visual_flaw"} or not code


def _legacy_flaw_issues(result: CoreVideoQCResponse) -> list[dict[str, Any]]:
    raw_issues = result.raw.get("issues") if isinstance(result.raw, dict) else None
    if isinstance(raw_issues, list):
        return [issue for issue in raw_issues if isinstance(issue, dict) and _is_product_consistency_issue(issue)]
    return [
        issue.model_dump()
        for issue in result.issues
        if issue.category == "product_consistency"
    ]


def _legacy_flaw_affected_scene_ids(issues: list[dict[str, Any]], fallback: list[str]) -> list[str]:
    scene_ids: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        scene_id = issue.get("scene_id")
        if not scene_id or str(scene_id) in seen:
            continue
        seen.add(str(scene_id))
        scene_ids.append(str(scene_id))
    return scene_ids or fallback


def _filter_issues_to_scene_ids(issues: list[dict[str, Any]], scene_ids: list[str]) -> list[dict[str, Any]]:
    if not scene_ids:
        return issues
    allowed = set(scene_ids)
    return [issue for issue in issues if str(issue.get("scene_id") or "") in allowed]


@router.post("/quality-review", response_model=VideoQualityReviewResponse)
async def quality_review(body: VideoQualityReviewRequest, request: Request) -> VideoQualityReviewResponse:
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
    response = _quality_response_from_core(result)
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


@router.post("/analyze-flaws", response_model=VideoFlawAnalysisResponse)
async def analyze_video_flaws(body: VideoFlawAnalysisRequest, request: Request) -> VideoFlawAnalysisResponse:
    result = await review_video_quality(
        VideoQCRequest(
            merged_video_url=body.merged_video_url,
            scene_videos=[scene.model_dump() for scene in body.scene_videos],
            scene_packages=body.scene_packages,
            original_scene_packages=body.original_scene_packages,
            brief={
                "plan": body.plan,
                "form_values": body.form_values,
                "intake_context": body.intake_context,
                "selected_direction": body.selected_direction,
            },
            materials=body.materials,
            user_feedback=body.user_feedback or "",
            checks=["product_consistency"],
        ),
        skill=get_video_quality_review_skill(),
    )
    endpoint = result.endpoint
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    message = "视频穿帮分析完成。" if result.ok else (result.error or "视频穿帮分析失败。")
    if quota_insufficient:
        message = quota_resume_message(result.error)
    legacy_issues = _legacy_flaw_issues(result)
    scoped_scene_ids = result.target_scene_ids
    if scoped_scene_ids:
        legacy_issues = _filter_issues_to_scene_ids(legacy_issues, scoped_scene_ids)
        if not legacy_issues:
            legacy_issues = [
                {
                    "scene_id": scene_id,
                    "category": "product_consistency",
                    "message": "用户意见明确要求只修复该分镜",
                }
                for scene_id in scoped_scene_ids
            ]
    affected_scene_ids = scoped_scene_ids or _legacy_flaw_affected_scene_ids(legacy_issues, result.affected_scene_ids)
    revision_prompt = result.revision_prompt
    if scoped_scene_ids:
        allowed = set(scoped_scene_ids)
        scene_labels = "、".join(f"第{scene.scene_index}个分镜" for scene in body.scene_videos if scene.scene_id in allowed) or "指定分镜"
        revision_prompt = f"请只重生成{scene_labels}，恢复为原方案要求的产品一致性画面；其他分镜复用原视频，不要重新生成。"
    response = VideoFlawAnalysisResponse(
        ok=result.ok,
        endpoint=endpoint if isinstance(endpoint, str) and endpoint else "/api/creative/analyze_video_flaws",
        task_id=result.task_id,
        passed=result.passed,
        score=result.score,
        summary_markdown=result.summary_markdown,
        flaw_analysis_markdown=result.flaw_analysis_markdown,
        issues=legacy_issues,
        affected_scene_ids=affected_scene_ids,
        target_scene_ids=scoped_scene_ids,
        excluded_scene_ids=result.excluded_scene_ids,
        revision_prompt=revision_prompt,
        check_results=[item.model_dump() for item in result.check_results],
        error=result.error,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )
    record_power_mem_background(
        power_mem_service(request),
        user_id=await current_user_id(request),
        content=concise_result_summary("视频穿帮分析 Agent 完成局部质检", {"stage": "analyze_flaws", "message": response.message, "ok": response.ok, "quota_insufficient": response.quota_insufficient}),
        category="experience",
        source_agent="video_flaw_analysis_agent",
        metadata={"source": "video_analyze_flaws", "passed": response.passed, "score": response.score, "affected_scene_count": len(response.affected_scene_ids)},
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
