"""PixelFlow v2 视频生成与分析 API。"""

from __future__ import annotations

import asyncio
import math
from typing import Any
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

router = APIRouter(prefix="/agent/flows/video", tags=["pixelflow-flows"])


class SceneVideo(BaseModel):
    scene_id: str
    scene_index: int | None = None
    video_url: str


class SceneGenerationItem(BaseModel):
    scene_id: str
    scene_index: int
    duration_ms: int = Field(gt=0, le=15_000)
    prompt: str
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
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)


class GenerateSceneAssetsRequest(BaseModel):
    scene_packages: list[dict[str, Any]]
    image_size: str = "1080p"
    model: str | None = None


class GenerateSceneAssetsResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/picture/text_to_image"
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    failed_assets: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class GeneratedSceneVideo(BaseModel):
    scene_id: str
    scene_index: int
    duration_ms: int
    video_url: str
    task_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class GenerateSceneVideosResponse(BaseModel):
    ok: bool
    endpoint: str = "/api/video/reference-mode-video"
    scene_videos: list[GeneratedSceneVideo] = Field(default_factory=list)
    failed_scenes: list[dict[str, Any]] = Field(default_factory=list)
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
    raw: dict[str, Any] = Field(default_factory=dict)


class VideoFlawAnalysisRequest(BaseModel):
    merged_video_url: str
    scene_videos: list[SceneVideo]
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
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
    raw: dict[str, Any] = Field(default_factory=dict)


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
            return AnalyzeStoryboardsResponse(
                ok=False,
                endpoint="/api/creative/extractMediaLinks",
                error=extraction.error,
                message=extraction.error or "媒体链接识别失败。",
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
        return AnalyzeStoryboardsResponse(
            ok=result.ok,
            mode="single",
            endpoint="/api/creative/decompose_video_to_storyboard",
            video_urls=video_urls,
            storyboards=[{"video_url": video_urls[0], "shots": result.shots}] if result.ok else [],
            error=result.error,
            message="视频分析完成。" if result.ok else (result.error or "视频分析失败。"),
            raw=result.raw,
        )

    result = await decompose_skill.batch_decompose_video_to_storyboard(video_urls)
    return AnalyzeStoryboardsResponse(
        ok=result.ok,
        mode="batch",
        endpoint="/api/creative/batch_decompose_video_to_storyboard",
        video_urls=video_urls,
        storyboards=result.storyboards if result.ok else [],
        task_id=result.task_id,
        error=result.error,
        message="批量视频分析完成。" if result.ok else (result.error or "批量视频分析失败。"),
        raw=result.raw,
    )


@router.post("/generate-scene-assets", response_model=GenerateSceneAssetsResponse)
async def generate_scene_assets(body: GenerateSceneAssetsRequest) -> GenerateSceneAssetsResponse:
    if not body.scene_packages:
        raise HTTPException(status_code=400, detail="scene_packages不能为空")

    image_skill = get_image_skill()
    enriched = [_clone_mapping(scene) for scene in body.scene_packages]
    failed_assets: list[dict[str, Any]] = []

    async def generate_asset(prompt: str, ratio: str, context: dict[str, Any]) -> list[str]:
        result = await image_skill.text_to_image(
            prompt=prompt,
            ratio=ratio,
            size=body.image_size,
            model=body.model,
            num_images=1,
        )
        if not result.ok:
            failed_assets.append({**context, "error": result.error or "图片生成失败", "raw": result.raw})
            return []
        urls = [str(image.get("url") or image.get("download_url")) for image in result.images if image.get("url") or image.get("download_url")]
        if not urls:
            failed_assets.append({**context, "error": "图片生成结果没有URL", "raw": result.raw})
        return urls

    tasks: list[tuple[dict[str, Any], str, asyncio.Task[list[str]]]] = []
    for scene in enriched:
        scene_id = str(scene.get("scene_id") or "")
        for character in _list_of_dicts(scene.get("characters")):
            prompt = str(character.get("three_view_prompt") or character.get("description") or "").strip()
            if prompt:
                task = asyncio.create_task(generate_asset(prompt, "1:1", {"scene_id": scene_id, "asset_type": "character"}))
                tasks.append((character, "three_view_images", task))
        for scene_image in _list_of_dicts(scene.get("scene_images")):
            prompt = str(scene_image.get("image_prompt") or scene_image.get("description") or "").strip()
            if prompt:
                task = asyncio.create_task(generate_asset(prompt, "9:16", {"scene_id": scene_id, "asset_type": "scene_image"}))
                tasks.append((scene_image, "images", task))
        for prop_image in _list_of_dicts(scene.get("prop_images")):
            prompt = str(prop_image.get("image_prompt") or prop_image.get("description") or prop_image.get("name") or "").strip()
            if prompt:
                task = asyncio.create_task(generate_asset(prompt, "1:1", {"scene_id": scene_id, "asset_type": "prop_image"}))
                tasks.append((prop_image, "images", task))

    for target, field_name, task in tasks:
        target[field_name] = await task

    return GenerateSceneAssetsResponse(
        ok=not failed_assets,
        scene_packages=enriched,
        failed_assets=failed_assets,
        message="场景资产图生成完成。" if not failed_assets else "部分场景资产图生成失败，请查看 failed_assets。",
    )


@router.post("/generate-scenes", response_model=GenerateSceneVideosResponse)
async def generate_scene_videos(body: GenerateSceneVideosRequest) -> GenerateSceneVideosResponse:
    if not body.scenes:
        raise HTTPException(status_code=400, detail="scenes不能为空")

    skill = get_video_skill()

    async def run_scene(scene: SceneGenerationItem) -> GeneratedSceneVideo | dict[str, Any]:
        result = await skill.reference_mode_video(
            prompt=scene.prompt,
            image_urls=scene.image_urls,
            video_urls=scene.video_urls,
            audio_urls=scene.audio_urls,
            duration=max(1, math.ceil(scene.duration_ms / 1000)),
            ratio=body.ratio,
            size=body.size,
            model=body.model,
            sound=body.sound,
        )
        if not result.ok or not result.url:
            return {
                "scene_id": scene.scene_id,
                "scene_index": scene.scene_index,
                "error": result.error or "场景视频生成失败",
                "raw": result.raw,
            }
        return GeneratedSceneVideo(
            scene_id=scene.scene_id,
            scene_index=scene.scene_index,
            duration_ms=scene.duration_ms,
            video_url=result.url,
            task_id=result.task_id,
            raw=result.raw,
        )

    results = await asyncio.gather(*(run_scene(scene) for scene in body.scenes))
    scene_videos = sorted((item for item in results if isinstance(item, GeneratedSceneVideo)), key=lambda item: item.scene_index)
    failed_scenes = [item for item in results if isinstance(item, dict)]
    return GenerateSceneVideosResponse(
        ok=not failed_scenes,
        scene_videos=scene_videos,
        failed_scenes=failed_scenes,
        message="场景视频生成完成。" if not failed_scenes else "部分场景视频生成失败，请查看 failed_scenes。",
    )


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
    return MergeSceneVideosResponse(
        ok=result.ok,
        endpoint=endpoint if isinstance(endpoint, str) and endpoint else "/api/video/merge",
        merged_video_url=result.url,
        task_id=result.task_id,
        scene_videos=ordered_scenes,
        error=result.error,
        message="视频合并完成。" if result.ok else (result.error or "视频合并失败。"),
        raw=result.raw,
    )


@router.post("/analyze-flaws", response_model=VideoFlawAnalysisResponse)
async def analyze_video_flaws(body: VideoFlawAnalysisRequest) -> VideoFlawAnalysisResponse:
    skill = get_video_flaw_analysis_skill()
    result = await skill.analyze_video_flaws(
        merged_video_url=body.merged_video_url,
        scene_videos=[scene.model_dump() for scene in body.scene_videos],
        scene_packages=body.scene_packages,
        user_feedback=body.user_feedback,
    )
    endpoint = result.raw.get("endpoint")
    return VideoFlawAnalysisResponse(
        ok=result.ok,
        endpoint=endpoint if isinstance(endpoint, str) and endpoint else "/api/creative/analyze_video_flaws",
        task_id=result.task_id,
        flaw_analysis_markdown=result.flaw_analysis_markdown,
        issues=result.issues,
        affected_scene_ids=result.affected_scene_ids,
        revision_prompt=result.revision_prompt,
        error=result.error,
        message="视频穿帮分析完成。" if result.ok else (result.error or "视频穿帮分析失败。"),
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
