"""Borgrise 视频生成 skill（Shape B：进程内执行）。

这是对 ``run_generation`` 脚本的异步薄封装。``run_generation`` 是 Borgrise API
合同的单一来源，包含鉴权、自定义请求头、端点、轮询等细节。这里不复制供应商
协议，只负责把同步阻塞函数丢到线程里执行，并把返回值映射成 PixelFlow 统一
Result DTO。

``run_generation`` 只读取供应商 base URL、轮询超时等非用户身份配置。
真正的用户身份来自入口请求的 content-app ``Authorization``，由网关写入
ContextVar 后在这里透传给生成接口。也就是说，这个 skill 不允许再使用配置文件里的
固定 token 或账号密码去替用户扣费。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from pixelflow.skills.base import (
    BatchStoryboardResult,
    GenerationResult,
    ImageGenerationResult,
    MediaLinkExtractionResult,
    PptGenerationResult,
    StoryboardResult,
    VideoQualityReviewResult,
)
from pixelflow.skills.borgrise import run_generation

logger = logging.getLogger(__name__)


def _to_result(raw: dict[str, Any]) -> GenerationResult:
    """把 ``run_generation`` 的原始 dict 映射成统一 ``GenerationResult``。"""
    if not raw or raw.get("error"):
        return GenerationResult(
            ok=False,
            task_id=raw.get("task_id") if raw else None,
            error=(raw.get("message") if raw else "empty response") or "generation failed",
            raw=raw or {},
        )
    url = raw.get("video_url") or raw.get("image_url") or raw.get("url")
    if not url:
        return GenerationResult(
            ok=False,
            task_id=raw.get("task_id"),
            error=raw.get("message") or "generation returned no video url",
            raw=raw,
        )
    return GenerationResult(ok=True, url=url, task_id=raw.get("task_id"), raw=raw)


def _to_image_result(raw: dict[str, Any]) -> ImageGenerationResult:
    """把 ``run_generation`` 的图片原始 dict 映射成统一图片结果。"""
    if not raw or raw.get("error"):
        return ImageGenerationResult(
            ok=False,
            task_id=raw.get("task_id") if raw else None,
            error=(raw.get("message") if raw else "empty response") or "image generation failed",
            raw=raw or {},
        )
    task_id = raw.get("task_id")
    urls: list[str] = []
    for value in (raw.get("image_urls"), raw.get("image_url"), raw.get("edited_image_url"), raw.get("url")):
        if isinstance(value, list):
            urls.extend(str(item) for item in value if item)
        elif value:
            urls.append(str(value))
    for url in run_generation.extract_result_urls(raw):
        urls.append(url)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    if not deduped:
        return ImageGenerationResult(
            ok=False,
            task_id=task_id,
            error=raw.get("message") or "image generation returned no image url",
            raw=raw,
        )
    asset_prefix = task_id or "image"
    images = [{"asset_id": f"{asset_prefix}-{index}", "url": url, "download_url": url} for index, url in enumerate(deduped)]
    return ImageGenerationResult(ok=True, images=images, task_id=task_id, raw=raw)


def _to_quality_review_result(raw: dict[str, Any]) -> VideoQualityReviewResult:
    """把 ``run_generation`` 的 QAAgent QC 原始 dict 映射成统一结果。"""
    if not raw or raw.get("error") or raw.get("success") is False:
        return VideoQualityReviewResult(
            ok=False,
            task_id=raw.get("task_id") if raw else None,
            error=(raw.get("message") if raw else "empty response") or "video quality review failed",
            raw=raw or {},
        )
    summary = str(raw.get("summary_markdown") or raw.get("quality_report_markdown") or "")
    return VideoQualityReviewResult(
        ok=True,
        task_id=raw.get("task_id"),
        summary_markdown=summary,
        quality_report_markdown=str(raw.get("quality_report_markdown") or summary),
        issues=[issue for issue in raw.get("issues", []) if isinstance(issue, dict)],
        affected_scene_ids=[str(scene_id) for scene_id in raw.get("affected_scene_ids", []) if scene_id],
        revision_prompt=str(raw.get("revision_prompt") or ""),
        raw=raw,
    )


def _to_media_link_result(raw: dict[str, Any]) -> MediaLinkExtractionResult:
    """把媒体链接识别原始 dict 映射成统一结果。"""
    if not raw or raw.get("error"):
        return MediaLinkExtractionResult(
            ok=False,
            error=(raw.get("message") if raw else "empty response") or "media link extraction failed",
            raw=raw or {},
        )
    links = [str(link) for link in raw.get("links", []) if link] if isinstance(raw.get("links"), list) else []
    return MediaLinkExtractionResult(ok=True, links=links, raw=raw)


def _to_ppt_result(raw: dict[str, Any]) -> PptGenerationResult:
    """把 SmartPPT 原始 dict 映射成统一 PPT 结果。"""
    if not raw or raw.get("error"):
        return PptGenerationResult(
            ok=False,
            task_id=raw.get("task_id") if raw else None,
            smart_ppt_project_id=raw.get("smart_ppt_project_id") if raw else None,
            error=(raw.get("message") if raw else "empty response") or "smart ppt generation failed",
            quota_insufficient=run_generation.is_quota_insufficient(raw),
            raw=raw or {},
        )
    slide_count = raw.get("slide_count")
    try:
        normalized_slide_count = int(slide_count) if slide_count is not None else None
    except (TypeError, ValueError):
        normalized_slide_count = None
    return PptGenerationResult(
        ok=True,
        task_id=raw.get("task_id"),
        smart_ppt_project_id=raw.get("smart_ppt_project_id"),
        summary=str(raw.get("summary") or ""),
        content_json=raw.get("content_json"),
        image_url=raw.get("image_url"),
        ppt_url=raw.get("ppt_url"),
        filename=raw.get("filename"),
        slide_count=normalized_slide_count,
        raw=raw,
    )


def _to_batch_storyboard_result(raw: dict[str, Any]) -> BatchStoryboardResult:
    """把批量视频拆解原始 dict 映射成统一结果。"""
    if not raw or raw.get("error"):
        return BatchStoryboardResult(
            ok=False,
            task_id=raw.get("task_id") if raw else None,
            error=(raw.get("message") if raw else "empty response") or "batch decompose failed",
            raw=raw or {},
        )
    storyboards = [item for item in raw.get("storyboards", []) if isinstance(item, dict)] if isinstance(raw.get("storyboards"), list) else []
    if not storyboards and (raw.get("batch_video_analysis_markdown") or raw.get("batch_video_generation_prompt")):
        storyboards = [
            {
                "video_urls": raw.get("video_urls", []),
                "analysis_markdown": raw.get("batch_video_analysis_markdown", ""),
                "generation_prompt": raw.get("batch_video_generation_prompt", ""),
            }
        ]
    return BatchStoryboardResult(
        ok=True,
        task_id=raw.get("task_id"),
        storyboards=storyboards,
        raw=raw,
    )


def _extract_shots(raw: Any) -> list[dict[str, Any]]:
    """从供应商响应中宽松提取 storyboard shots。

    博观拆解接口常把列表嵌在 ``data.result.video_url.segments``；这里也兼容
    ``shots``、``storyboard``、``scenes`` 等更简单形态，降低供应商响应变化的影响。
    """
    if not isinstance(raw, dict):
        return []
    for key in ("shots", "storyboard", "storyboards", "scenes", "segments"):
        value = raw.get(key)
        if isinstance(value, list) and value:
            return [s if isinstance(s, dict) else {"description": str(s)} for s in value]
    for key in ("data", "result", "video_url"):
        nested = raw.get(key)
        if isinstance(nested, list) and nested:
            return [s if isinstance(s, dict) else {"description": str(s)} for s in nested]
        if isinstance(nested, dict):
            shots = _extract_shots(nested)
            if shots:
                return shots
    return []


def _parse_time_range(time_range: Any) -> float:
    """解析 ``"4-22s"`` 这类时间范围对应的秒数，无法解析时返回 0。"""
    nums = re.findall(r"\d+(?:\.\d+)?", str(time_range or ""))
    if len(nums) >= 2:
        return max(0.0, round(float(nums[1]) - float(nums[0]), 2))
    return 0.0


def _normalize_segment(seg: Any) -> dict[str, Any]:
    """把博观 segment 映射成 PixelFlow 更稳定的 shot 形态。

    博观字段可能是 camelCase 或中文语义字段。这里把供应商差异挡在 skill 边界，
    让下游纯逻辑 ``summarize_storyboards`` 只读取稳定的下划线字段。
    """
    if not isinstance(seg, dict):
        return {"visual_description": str(seg)}
    return {
        "visual_description": seg.get("visualContent") or seg.get("visual_description") or "",
        "narration_text": seg.get("voiceContent") or "",
        "onscreen_text": seg.get("subtitle") or "",
        "shot_type": seg.get("shotType") or "",
        "camera_movement": seg.get("cameraMovement") or "",
        "duration": _parse_time_range(seg.get("timeRange")),
        "time_range": seg.get("timeRange") or "",
    }


def _collect_material_strings(value: Any) -> list[str]:
    """从素材 dict/list 中递归收集字符串，供 content-app 媒体链接识别接口处理。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_collect_material_strings(item))
        return collected
    if isinstance(value, dict):
        collected: list[str] = []
        for item in value.values():
            collected.extend(_collect_material_strings(item))
        return collected
    return []


def _decompose_blocking(video_url: str) -> dict[str, Any]:
    """调用参考视频拆解端点；如果返回异步任务，则继续轮询。

    拆解使用视觉模型 ``gemini-3-flash-preview``，并需要和生成接口相同的自定义
    请求头。这个函数是阻塞调用，外层必须用 ``asyncio.to_thread`` offload。
    """
    headers = run_generation.get_headers(model="gemini-3-flash-preview", bill_type=1, duration=1, size="all")
    result = run_generation.make_request("/creative/decompose_video_to_storyboard", {"video_url": video_url}, custom_headers=headers)
    task_id = run_generation.extract_task_id(result)
    if task_id and not _extract_shots(result):
        # 参考视频拆解属于“视频分析”任务，通常比图片生成久、但不应套用视频生成 1 小时上限。
        result = run_generation.poll_task(task_id, default_timeout=run_generation.VIDEO_ANALYSIS_POLL_TIMEOUT)
    return result


async def _run(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> GenerationResult:
    """在线程中运行阻塞的 ``run_generation`` 调用，并归一化失败。

    ``run_generation`` 可能因为缺 token、网络错误、供应商异常而抛出。这里统一转成
    ``GenerationResult(ok=False)``，避免某个片段失败时直接冲垮整个 GENERATE 阶段。
    """
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise %s failed", getattr(fn, "__name__", "call"))
        return GenerationResult(ok=False, error=str(exc))
    return _to_result(raw)


async def _run_image(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> ImageGenerationResult:
    """在线程中运行阻塞图片调用，并归一化失败。"""
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise image %s failed", getattr(fn, "__name__", "call"))
        return ImageGenerationResult(ok=False, error=str(exc))
    return _to_image_result(raw)


async def _run_quality_review(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> VideoQualityReviewResult:
    """在线程中运行阻塞 QAAgent QC 调用，并归一化失败。"""
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise video QC review %s failed", getattr(fn, "__name__", "call"))
        return VideoQualityReviewResult(ok=False, error=str(exc))
    return _to_quality_review_result(raw)


async def _run_media_links(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> MediaLinkExtractionResult:
    """在线程中运行阻塞媒体链接识别调用，并归一化失败。"""
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise media link extraction %s failed", getattr(fn, "__name__", "call"))
        return MediaLinkExtractionResult(ok=False, error=str(exc))
    return _to_media_link_result(raw)


async def _run_batch_storyboard(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> BatchStoryboardResult:
    """在线程中运行阻塞批量拆解调用，并归一化失败。"""
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise batch decompose %s failed", getattr(fn, "__name__", "call"))
        return BatchStoryboardResult(ok=False, error=str(exc))
    return _to_batch_storyboard_result(raw)


async def _run_ppt(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> PptGenerationResult:
    """在线程中运行阻塞 SmartPPT 调用，并归一化失败。"""
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise smart ppt %s failed", getattr(fn, "__name__", "call"))
        return PptGenerationResult(ok=False, error=str(exc))
    return _to_ppt_result(raw)


class BorgriseSkill:
    """进程内 Borgrise 实现，同时实现视频生成和参考视频拆解能力。"""

    async def text_to_video(
        self,
        prompt: str,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
            "size": size,
            "sound": sound,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.text_to_video, **kwargs)

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
            "size": size,
            "sound": sound,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.image_to_video, **kwargs)

    async def two_image_to_video(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "first_frame_image_url": first_frame_image_url,
            "last_frame_image_url": last_frame_image_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
            "size": size,
            "sound": sound,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.two_image_to_video, **kwargs)

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "video_url": video_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
            "size": size,
            "sound": sound,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.extend_video, **kwargs)

    async def reference_mode_video(
        self,
        prompt: str,
        image_urls: list[str] | None = None,
        video_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "image_urls": image_urls or [],
            "video_urls": video_urls or [],
            "audio_urls": audio_urls or [],
            "duration": duration,
            "ratio": ratio,
            "size": size,
            "sound": sound,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.reference_mode_video, **kwargs)

    async def edit_video(
        self,
        ref_video: str,
        prompt: str | None = None,
        ref_image: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "ref_video": ref_video,
            "prompt": prompt,
            "ref_image": ref_image,
            "duration": duration,
            "ratio": ratio,
            "size": size,
            "sound": sound,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.edit_video, **kwargs)

    async def merge_videos(
        self,
        video_urls: list[str],
        duration: int = 30,
        size: str = "1080p",
        model: str | None = None,
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "video_urls": video_urls,
            "duration": duration,
            "size": size,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.merge_videos, **kwargs)

    async def text_to_image(
        self,
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        num_images: int = 1,
    ) -> ImageGenerationResult:
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "ratio": ratio,
            "size": size,
            "num_images": num_images,
        }
        if model:
            kwargs["model"] = model
        return await _run_image(run_generation.text_to_image, **kwargs)

    async def reference_image(
        self,
        reference_images: list[str],
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        max_images: int = 1,
    ) -> ImageGenerationResult:
        kwargs: dict[str, Any] = {
            "reference_images": reference_images,
            "prompt": prompt,
            "ratio": ratio,
            "size": size,
            "max_images": max_images,
        }
        if model:
            kwargs["model"] = model
        return await _run_image(run_generation.reference_image, **kwargs)

    async def image_edit(
        self,
        image_url: str,
        prompt: str,
        model: str | None = None,
        ratio: str = "1:1",
        size: str = "1080p",
        max_images: int = 1,
    ) -> ImageGenerationResult:
        kwargs: dict[str, Any] = {
            "image_url": image_url,
            "prompt": prompt,
            "ratio": ratio,
            "size": size,
            "max_images": max_images,
        }
        if model:
            kwargs["model"] = model
        return await _run_image(run_generation.image_edit, **kwargs)

    async def multi_image_fusion(
        self,
        image_urls: list[str],
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        num_images: int = 1,
    ) -> ImageGenerationResult:
        kwargs: dict[str, Any] = {
            "image_urls": image_urls,
            "prompt": prompt,
            "ratio": ratio,
            "size": size,
            "num_images": num_images,
        }
        if model:
            kwargs["model"] = model
        return await _run_image(run_generation.multi_image_fusion, **kwargs)

    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult:
        """把参考视频拆解成供应商 storyboard（博观拆解）。"""
        try:
            raw = await asyncio.to_thread(_decompose_blocking, video_url)
        except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
            logger.exception("borgrise decompose failed url=%s", video_url)
            return StoryboardResult(ok=False, error=str(exc))
        if not raw or raw.get("error"):
            return StoryboardResult(ok=False, error=(raw.get("message") if raw else "empty response") or "decompose failed", raw=raw or {})
        shots = [_normalize_segment(s) for s in _extract_shots(raw)]
        if not shots:
            return StoryboardResult(ok=False, error="no shots in decompose response", raw=raw)
        return StoryboardResult(ok=True, shots=shots, raw=raw)

    async def batch_decompose_video_to_storyboard(self, video_urls: list[str]) -> BatchStoryboardResult:
        """把多个参考视频拆解成批量分析 storyboard。"""
        return await _run_batch_storyboard(run_generation.batch_decompose_video_to_storyboard, video_urls=video_urls)

    async def extract_media_links(self, text: str, materials: list[dict[str, Any]] | None = None) -> MediaLinkExtractionResult:
        """从提示词和素材文本中识别媒体链接。"""
        pieces = [text]
        for material in materials or []:
            pieces.extend(_collect_material_strings(material))
        return await _run_media_links(run_generation.extract_media_links, text="\n".join(piece for piece in pieces if piece))

    async def review_video_quality(
        self,
        merged_video_url: str,
        scene_videos: list[dict[str, Any]],
        scene_packages: list[dict[str, Any]] | None = None,
        brief: dict[str, Any] | None = None,
        materials: list[dict[str, Any]] | None = None,
        user_feedback: str | None = None,
        checks: list[str] | None = None,
        platform: str | None = None,
        ratio: str | None = None,
        size: str | None = None,
    ) -> VideoQualityReviewResult:
        """调用 content-app QAAgent QC 分析合并视频和场景视频。"""
        return await _run_quality_review(
            run_generation.review_video_quality,
            merged_video_url=merged_video_url,
            scene_videos=scene_videos,
            scene_packages=scene_packages or [],
            brief=brief or {},
            materials=materials or [],
            user_feedback=user_feedback,
            checks=checks or [],
            platform=platform,
            ratio=ratio,
            size=size,
        )

    async def generate_ppt_summary(
        self,
        topic: str,
        ppt_style: str,
        file_urls: list[str],
        smart_ppt_project_id: int | None = None,
    ) -> PptGenerationResult:
        kwargs: dict[str, Any] = {
            "topic": topic,
            "ppt_style": ppt_style,
            "file_urls": file_urls,
        }
        if smart_ppt_project_id is not None:
            kwargs["smart_ppt_project_id"] = smart_ppt_project_id
        return await _run_ppt(run_generation.generate_ppt_summary, **kwargs)

    async def update_ppt_summary(
        self,
        original_outline: str,
        modification_opinion: str,
        smart_ppt_project_id: int,
    ) -> PptGenerationResult:
        return await _run_ppt(
            run_generation.update_ppt_summary,
            original_outline=original_outline,
            modification_opinion=modification_opinion,
            smart_ppt_project_id=smart_ppt_project_id,
        )

    async def generate_ppt_content_json(
        self,
        original_outline: str,
        ppt_style: str,
        smart_ppt_project_id: int,
    ) -> PptGenerationResult:
        return await _run_ppt(
            run_generation.generate_ppt_content_json,
            original_outline=original_outline,
            ppt_style=ppt_style,
            smart_ppt_project_id=smart_ppt_project_id,
        )

    async def generate_ppt_image(
        self,
        json_content: str,
        smart_ppt_project_id: int,
    ) -> PptGenerationResult:
        return await _run_ppt(
            run_generation.generate_ppt_image,
            json_content=json_content,
            smart_ppt_project_id=smart_ppt_project_id,
        )

    async def generate_ppt_file(
        self,
        file_urls: list[str],
        smart_ppt_project_id: int,
    ) -> PptGenerationResult:
        return await _run_ppt(
            run_generation.generate_ppt_file,
            file_urls=file_urls,
            smart_ppt_project_id=smart_ppt_project_id,
        )
