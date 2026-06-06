"""Skill capability interfaces.

The pipeline graph depends on these abstractions, never on a concrete vendor
(Borgrise) or its HTTP endpoints. This keeps the generation interface swappable:
MVP runs the skill in-process (Shape B); P1 can move the same implementation
into the sandbox (Shape A) without touching graph code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class GenerationResult:
    """Normalized result of a single generation call.

    Vendor-specific response shapes are mapped onto this so the graph reads a
    stable contract: ``ok`` + ``url`` on success, ``error`` on failure.
    """

    ok: bool
    url: str | None = None
    task_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VideoGenerationSkill(Protocol):
    """Capability the GENERATE phase needs: produce/extend video clips.

    Implementations own the vendor contract (auth, headers, endpoints, polling).
    Generation parameters are passed per call — nothing is hardcoded here.
    """

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult: ...

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult: ...


def get_video_skill() -> VideoGenerationSkill:
    """Return the configured video-generation skill.

    This is the single swap point for the implementation. MVP returns the
    in-process Borgrise skill; ``PIXELFLOW_VIDEO_SKILL`` is reserved for
    selecting alternative implementations (e.g. a sandbox-executed skill in P1).
    """
    impl = os.environ.get("PIXELFLOW_VIDEO_SKILL", "borgrise")
    if impl == "borgrise":
        from pixelflow.skills.borgrise import BorgriseSkill

        return BorgriseSkill()
    raise ValueError(f"Unknown video skill implementation: {impl!r}")
