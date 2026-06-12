"""Borgrise video-generation skill (Shape B: in-process).

Thin async adapter over the vendored ``run_generation`` script, which is the
single source of the Borgrise API contract (auth, custom headers, endpoints,
polling) and is reused verbatim. The sync, blocking functions are offloaded to
a worker thread so they don't block the async event loop (the harness gates
blocking IO on the loop).

Config is environment-driven via ``run_generation`` (``BORGRISE_API_TOKEN``,
``BORGRISE_BASE_URL``); per-call generation params are passed through.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from pixelflow.skills.base import GenerationResult, StoryboardResult
from pixelflow.skills.borgrise import run_generation

logger = logging.getLogger(__name__)


def _to_result(raw: dict[str, Any]) -> GenerationResult:
    """Map a ``run_generation`` response dict onto ``GenerationResult``."""
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


def _extract_shots(raw: Any) -> list[dict[str, Any]]:
    """Pull the storyboard shot list out of a vendor response, tolerantly.

    博观's decompose nests the list at ``data.result.video_url.segments``; we also
    accept the simpler ``shots``/``scenes`` shapes other endpoints might use.
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
    """Seconds spanned by a ``"4-22s"``-style range; 0 when unparseable."""
    nums = re.findall(r"\d+(?:\.\d+)?", str(time_range or ""))
    if len(nums) >= 2:
        return max(0.0, round(float(nums[1]) - float(nums[0]), 2))
    return 0.0


def _normalize_segment(seg: Any) -> dict[str, Any]:
    """Map a 博观 segment (camelCase, Chinese fields) onto PixelFlow's shot shape.

    Keeps the vendor specifics behind the skill boundary so downstream pure logic
    (``summarize_storyboards``) reads stable, underscore-cased keys.
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


def _decompose_blocking(video_url: str) -> dict[str, Any]:
    """Call the decompose endpoint; poll when the response is an async task.

    Decompose runs on the vision model ``gemini-3-flash-preview`` (billType=1,
    size=all) and needs the same custom headers as the generation endpoints.
    """
    headers = run_generation.get_headers(model="gemini-3-flash-preview", bill_type=1, duration=1, size="all")
    result = run_generation.make_request("/creative/decompose_video_to_storyboard?projectId=1", {"video_url": video_url}, custom_headers=headers)
    task_id = run_generation.extract_task_id(result)
    if task_id and not _extract_shots(result):
        result = run_generation.poll_task(task_id)
    return result


async def _run(fn: Callable[..., dict[str, Any]], **kwargs: Any) -> GenerationResult:
    """Run a blocking ``run_generation`` call off-loop, normalizing failures.

    ``run_generation`` raises on config errors (e.g. missing token) and may
    raise on transport errors; normalize everything to a ``GenerationResult``
    so one failing shot never crashes the GENERATE phase.
    """
    try:
        raw = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary: normalize all vendor errors
        logger.exception("borgrise %s failed", getattr(fn, "__name__", "call"))
        return GenerationResult(ok=False, error=str(exc))
    return _to_result(raw)


class BorgriseSkill:
    """In-process Borgrise implementation of ``VideoGenerationSkill``."""

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.image_to_video, **kwargs)

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "video_url": video_url,
            "prompt": prompt,
            "duration": duration,
            "ratio": ratio,
        }
        if model:
            kwargs["model"] = model
        return await _run(run_generation.extend_video, **kwargs)

    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult:
        """Parse a reference video into a vendor storyboard (博观拆解)."""
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
