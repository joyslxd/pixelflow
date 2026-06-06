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
from collections.abc import Callable
from typing import Any

from deerflow.pixelflow.skills.base import GenerationResult
from deerflow.pixelflow.skills.borgrise import run_generation

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
    return GenerationResult(ok=True, url=url, task_id=raw.get("task_id"), raw=raw)


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
