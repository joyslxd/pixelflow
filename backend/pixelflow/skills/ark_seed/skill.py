"""Seedance and Seedream skills on Volcengine Ark."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from pixelflow.skills.ark_seed.client import (
    DEFAULT_SEEDANCE_MODEL,
    DEFAULT_SEEDREAM_MODEL,
    ArkSeedClient,
    extract_task_id,
    extract_urls,
)
from pixelflow.skills.base import GenerationResult, ImageGenerationResult

logger = logging.getLogger(__name__)


def _image_blocks(image_urls: list[str], *, role: str = "reference_image") -> list[dict[str, Any]]:
    return [{"type": "image_url", "image_url": {"url": url}, "role": role} for url in image_urls if url]


def _video_blocks(video_urls: list[str], *, role: str = "reference_video") -> list[dict[str, Any]]:
    return [{"type": "video_url", "video_url": {"url": url}, "role": role} for url in video_urls if url]


def _audio_blocks(audio_urls: list[str], *, role: str = "reference_audio") -> list[dict[str, Any]]:
    return [{"type": "audio_url", "audio_url": {"url": url}, "role": role} for url in audio_urls if url]


def _to_generation_result(raw: dict[str, Any], task_id: str | None = None) -> GenerationResult:
    urls = extract_urls(raw)
    if urls:
        return GenerationResult(ok=True, url=urls[0], task_id=task_id or extract_task_id(raw), raw=raw)
    return GenerationResult(ok=False, task_id=task_id or extract_task_id(raw), error="Ark returned no video url", raw=raw)


def _to_image_result(raw: dict[str, Any], task_id: str | None = None) -> ImageGenerationResult:
    urls = extract_urls(raw)
    if urls:
        return ImageGenerationResult(ok=True, urls=urls, url=urls[0], task_id=task_id or extract_task_id(raw), raw=raw)
    return ImageGenerationResult(ok=False, task_id=task_id or extract_task_id(raw), error="Ark returned no image url", raw=raw)


class SeedanceSkill:
    """Seedance 2.0 video generation skill.

    Capabilities:
    - text-to-video
    - image-to-video
    - all-purpose reference video generation with image/video/audio references
    """

    def __init__(self, client: ArkSeedClient | None = None, *, model: str | None = None, resolution: str | None = None) -> None:
        self.client = client or ArkSeedClient()
        self.model = model or os.environ.get("ARK_SEEDANCE_MODEL") or DEFAULT_SEEDANCE_MODEL
        self.resolution = resolution

    def _video_payload(
        self,
        *,
        content: list[dict[str, Any]],
        duration: int,
        ratio: str,
        model: str | None = None,
        resolution: str | None = None,
        generate_audio: bool = False,
        watermark: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "content": content,
            "ratio": ratio,
            "duration": duration,
            "watermark": watermark,
        }
        if generate_audio:
            payload["generate_audio"] = True
        if resolution:
            payload["resolution"] = resolution
        return payload

    async def _create_and_wait(self, payload: dict[str, Any]) -> GenerationResult:
        task_id: str | None = None
        try:
            created = await asyncio.to_thread(self.client.create_video_task, payload)
            task_id = extract_task_id(created)
            if not task_id:
                return _to_generation_result(created)
            raw = await asyncio.to_thread(self.client.wait_video_task, task_id)
            return _to_generation_result(raw, task_id=task_id)
        except Exception as exc:  # noqa: BLE001 - skill boundary
            logger.exception("seedance generation failed")
            return GenerationResult(ok=False, task_id=task_id, error=str(exc))

    async def poll_video_task(self, task_id: str) -> GenerationResult:
        """Fetch one Ark video task and normalize its current/final result."""
        try:
            raw = await asyncio.to_thread(self.client.get_video_task, task_id)
        except Exception as exc:  # noqa: BLE001 - skill boundary
            logger.exception("seedance poll failed")
            return GenerationResult(ok=False, task_id=task_id, error=str(exc))
        status = str(raw.get("status") or raw.get("task_status") or "").lower()
        if status in {"failed", "error", "cancelled", "canceled"}:
            return GenerationResult(ok=False, task_id=task_id, error=str(raw.get("error") or raw.get("message") or status), raw=raw)
        result = _to_generation_result(raw, task_id=task_id)
        if result.ok:
            return result
        if status and status not in {"succeeded", "success", "completed", "done"}:
            return GenerationResult(ok=False, task_id=task_id, error=f"Ark video task is {status}", raw=raw)
        return result

    async def text_to_video(
        self,
        prompt: str,
        *,
        duration: int = 5,
        ratio: str = "9:16",
        model: str | None = None,
        resolution: str | None = None,
        generate_audio: bool = False,
        watermark: bool = False,
    ) -> GenerationResult:
        payload = self._video_payload(
            model=model,
            content=[{"type": "text", "text": prompt}],
            duration=duration,
            ratio=ratio,
            resolution=resolution or self.resolution,
            generate_audio=generate_audio,
            watermark=watermark,
        )
        return await self._create_and_wait(payload)

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        model: str | None = None,
        generate_audio: bool = False,
        watermark: bool = False,
    ) -> GenerationResult:
        return await self.reference_to_video(
            prompt=prompt or "",
            image_urls=[image_url],
            duration=duration,
            ratio=ratio,
            model=model,
            generate_audio=generate_audio,
            watermark=watermark,
        )

    async def reference_to_video(
        self,
        *,
        prompt: str,
        image_urls: list[str] | None = None,
        video_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        model: str | None = None,
        resolution: str | None = None,
        generate_audio: bool = False,
        watermark: bool = False,
    ) -> GenerationResult:
        content = [
            {"type": "text", "text": prompt},
            *_image_blocks(image_urls or []),
            *_video_blocks(video_urls or []),
            *_audio_blocks(audio_urls or []),
        ]
        payload = self._video_payload(
            model=model,
            content=content,
            duration=duration,
            ratio=ratio,
            resolution=resolution or self.resolution,
            generate_audio=generate_audio,
            watermark=watermark,
        )
        return await self._create_and_wait(payload)

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult:
        return await self.reference_to_video(prompt=prompt or "", video_urls=[video_url], duration=duration, ratio=ratio, model=model)


class SeedreamSkill:
    """Seedream image generation skill."""

    def __init__(self, client: ArkSeedClient | None = None, *, model: str | None = None) -> None:
        self.client = client or ArkSeedClient()
        self.model = model or os.environ.get("ARK_SEEDREAM_MODEL") or DEFAULT_SEEDREAM_MODEL

    async def _generate(self, payload: dict[str, Any]) -> ImageGenerationResult:
        try:
            raw = await asyncio.to_thread(self.client.generate_images, payload)
        except Exception as exc:  # noqa: BLE001 - skill boundary
            logger.exception("seedream generation failed")
            return ImageGenerationResult(ok=False, error=str(exc))
        return _to_image_result(raw)

    async def text_to_image(
        self,
        prompt: str,
        *,
        size: str = "2K",
        ratio: str = "1:1",
        num_images: int = 1,
        model: str | None = None,
        sequential_image_generation: str = "disabled",
        stream: bool = False,
        watermark: bool = True,
    ) -> ImageGenerationResult:
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "size": size,
            "sequential_image_generation": sequential_image_generation,
            "response_format": "url",
            "stream": stream,
            "watermark": watermark,
        }
        if ratio:
            payload["ratio"] = ratio
        if num_images != 1:
            payload["n"] = num_images
        return await self._generate(payload)

    async def image_to_image(
        self,
        image_urls: list[str],
        prompt: str,
        *,
        size: str = "2K",
        ratio: str = "1:1",
        num_images: int = 1,
        model: str | None = None,
        sequential_image_generation: str = "disabled",
        stream: bool = False,
        watermark: bool = True,
    ) -> ImageGenerationResult:
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "image": image_urls,
            "size": size,
            "sequential_image_generation": sequential_image_generation,
            "response_format": "url",
            "stream": stream,
            "watermark": watermark,
        }
        if ratio:
            payload["ratio"] = ratio
        if num_images != 1:
            payload["n"] = num_images
        return await self._generate(payload)

    async def reference_group_images(
        self,
        image_urls: list[str],
        prompt: str,
        *,
        size: str = "2K",
        ratio: str = "1:1",
        max_images: int = 4,
        model: str | None = None,
        stream: bool = False,
        watermark: bool = True,
    ) -> ImageGenerationResult:
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "image": image_urls,
            "size": size,
            "ratio": ratio,
            "n": max_images,
            "response_format": "url",
            "sequential_image_generation": "auto",
            "stream": stream,
            "watermark": watermark,
        }
        return await self._generate(payload)
