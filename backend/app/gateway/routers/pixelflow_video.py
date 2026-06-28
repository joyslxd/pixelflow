"""PixelFlow v2 视频生成与分析 API。"""

from __future__ import annotations

import asyncio
import math
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pixelflow.generate.scene_packages import prepare_video_scene_packages_with_llm
from pixelflow.skills import (
    get_image_skill,
    get_media_link_extraction_skill,
    get_video_decompose_skill,
    get_video_flaw_analysis_skill,
    get_video_skill,
)
from pixelflow.skills.base import is_quota_insufficient, quota_resume_message

router = APIRouter(prefix="/agent/flows/video", tags=["pixelflow-flows"])

_SCENE_VIDEO_JOBS: dict[str, dict[str, Any]] = {}
_MAX_SCENE_VIDEO_JOBS = 100
_DIRECT_VIDEO_JOBS: dict[str, dict[str, Any]] = {}
_MAX_DIRECT_VIDEO_JOBS = 100
_MAX_REFERENCE_IMAGE_COUNT = 9
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
    materials: list[dict[str, Any]] = Field(default_factory=list)
    user_feedback: str | None = None


class VideoFlawAnalysisResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/creative/analyze_video_flaws"
    task_id: str | None = None
    flaw_analysis_markdown: str = ""
    issues: list[dict[str, Any]] = Field(default_factory=list)
    affected_scene_ids: list[str] = Field(default_factory=list)
    revision_prompt: str = ""
    error: str | None = None
    message: str = ""
    quota_insufficient: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


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
async def prepare_scene_packages(body: PrepareScenePackagesRequest) -> PrepareScenePackagesResponse:
    result = await prepare_video_scene_packages_with_llm(
        form_values=body.form_values,
        plan_markdown=body.plan_markdown,
        selected_direction=body.selected_direction,
        materials=body.materials,
        target_duration_ms=body.target_duration_ms,
    )
    return PrepareScenePackagesResponse(**result)


@router.post("/analyze-storyboards", response_model=AnalyzeStoryboardsResponse)
async def analyze_storyboards(body: AnalyzeStoryboardsRequest) -> AnalyzeStoryboardsResponse:
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
async def generate_scene_assets(body: GenerateSceneAssetsRequest) -> GenerateSceneAssetsResponse:
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
            prompt = str(character.get("image_prompt") or character.get("three_view_prompt") or character.get("description") or "").strip()
            if prompt:
                asset_jobs.append((character, "images", prompt, "1:1", {"asset_id": character.get("asset_id"), "asset_type": "character"}))
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
                prompt = str(character.get("image_prompt") or character.get("three_view_prompt") or character.get("description") or "").strip()
                if prompt:
                    asset_jobs.append((character, "images", prompt, "1:1", {"scene_id": scene_id, "asset_type": "character"}))
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
async def generate_direct_video(body: GenerateDirectVideoRequest) -> GenerateDirectVideoResponse:
    return await _generate_direct_video_response(body)


@router.post("/generate-direct/start", response_model=GenerateDirectVideoJobStartResponse)
async def start_generate_direct_video(body: GenerateDirectVideoRequest) -> GenerateDirectVideoJobStartResponse:
    _trim_direct_video_jobs()
    job_id = uuid.uuid4().hex
    _DIRECT_VIDEO_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_direct_video_job(job_id, body))
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

    async def run_scene(scene: SceneGenerationItem) -> GeneratedSceneVideo | dict[str, Any]:
        image_urls = _scene_reference_image_urls(scene)
        if len(image_urls) > _MAX_REFERENCE_IMAGE_COUNT:
            return {
                "scene_id": scene.scene_id,
                "scene_index": scene.scene_index,
                "error": f"最多只能选择9张参考图，当前选择了{len(image_urls)}张。",
                "image_count": len(image_urls),
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

    results: list[GeneratedSceneVideo | dict[str, Any]] = []
    for scene in sorted(body.scenes, key=lambda item: item.scene_index):
        item = await run_scene(scene)
        results.append(item)
        if isinstance(item, dict) and is_quota_insufficient(item):
            scene_videos = sorted((result for result in results if isinstance(result, GeneratedSceneVideo)), key=lambda result: result.scene_index)
            failed_scenes = [result for result in results if isinstance(result, dict)]
            return GenerateSceneVideosResponse(
                ok=False,
                scene_videos=scene_videos,
                failed_scenes=failed_scenes,
                quota_insufficient=True,
                message=quota_resume_message(str(item.get("error") or "")),
            )
    scene_videos = sorted((item for item in results if isinstance(item, GeneratedSceneVideo)), key=lambda item: item.scene_index)
    failed_scenes = [item for item in results if isinstance(item, dict)]
    quota_insufficient = any(is_quota_insufficient(item) for item in failed_scenes)
    endpoints = {scene.endpoint for scene in scene_videos}
    return GenerateSceneVideosResponse(
        ok=not failed_scenes,
        endpoint=next(iter(endpoints)) if len(endpoints) == 1 else "/api/video/mixed",
        scene_videos=scene_videos,
        failed_scenes=failed_scenes,
        quota_insufficient=quota_insufficient,
        message="场景视频生成完成。" if not failed_scenes else (quota_resume_message() if quota_insufficient else "部分场景视频生成失败，请查看 failed_scenes。"),
    )


@router.post("/generate-scenes", response_model=GenerateSceneVideosResponse)
async def generate_scene_videos(body: GenerateSceneVideosRequest) -> GenerateSceneVideosResponse:
    return await _generate_scene_videos_response(body)


@router.post("/generate-scenes/start", response_model=GenerateSceneVideosJobStartResponse)
async def start_generate_scene_videos(body: GenerateSceneVideosRequest) -> GenerateSceneVideosJobStartResponse:
    if not body.scenes:
        raise HTTPException(status_code=400, detail="scenes不能为空")
    _trim_scene_video_jobs()
    job_id = uuid.uuid4().hex
    _SCENE_VIDEO_JOBS[job_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(_run_scene_video_job(job_id, body))
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


async def _run_scene_video_job(job_id: str, body: GenerateSceneVideosRequest) -> None:
    try:
        result = await _generate_scene_videos_response(body)
        _SCENE_VIDEO_JOBS[job_id] = {"status": "completed", "result": result, "error": None}
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _SCENE_VIDEO_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}


async def _run_direct_video_job(job_id: str, body: GenerateDirectVideoRequest) -> None:
    try:
        result = await _generate_direct_video_response(body)
        _DIRECT_VIDEO_JOBS[job_id] = {"status": "completed", "result": result, "error": None}
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure for polling clients
        _DIRECT_VIDEO_JOBS[job_id] = {"status": "failed", "result": None, "error": str(exc)}


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


@router.post("/merge", response_model=MergeSceneVideosResponse)
async def merge_scene_videos(body: MergeSceneVideosRequest) -> MergeSceneVideosResponse:
    ordered_scenes = sorted(body.scene_videos, key=lambda scene: scene.scene_index if scene.scene_index is not None else 0)
    video_urls = [scene.video_url for scene in ordered_scenes if scene.video_url]
    if len(video_urls) < 2:
        raise HTTPException(status_code=400, detail="至少需要2个场景视频才能合并")

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
    return MergeSceneVideosResponse(
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


@router.post("/analyze-flaws", response_model=VideoFlawAnalysisResponse)
async def analyze_video_flaws(body: VideoFlawAnalysisRequest) -> VideoFlawAnalysisResponse:
    skill = get_video_flaw_analysis_skill()
    result = await skill.analyze_video_flaws(
        merged_video_url=body.merged_video_url,
        scene_videos=[scene.model_dump() for scene in body.scene_videos],
        scene_packages=body.scene_packages,
        materials=body.materials,
        user_feedback=body.user_feedback,
    )
    endpoint = result.raw.get("endpoint")
    quota_insufficient = is_quota_insufficient(result.raw) or is_quota_insufficient(result.error)
    message = "视频穿帮分析完成。" if result.ok else (result.error or "视频穿帮分析失败。")
    if quota_insufficient:
        message = quota_resume_message(result.error)
    return VideoFlawAnalysisResponse(
        ok=result.ok,
        endpoint=endpoint if isinstance(endpoint, str) and endpoint else "/api/creative/analyze_video_flaws",
        task_id=result.task_id,
        flaw_analysis_markdown=result.flaw_analysis_markdown,
        issues=result.issues,
        affected_scene_ids=result.affected_scene_ids,
        revision_prompt=result.revision_prompt,
        error=result.error,
        message=message,
        quota_insufficient=quota_insufficient,
        raw=result.raw,
    )


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
