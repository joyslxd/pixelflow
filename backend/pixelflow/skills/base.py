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


@dataclass
class EditResult:
    """Normalized result of an edit/assembly call.

    ``output_path`` points at the produced artifact; ``kind`` tells the graph
    what it is — ``"draft"`` for the 剪映 skill (an editable draft folder, final
    render needs the JianYing app) or ``"video"`` for the FFmpeg skill (a
    finished mp4).
    """

    ok: bool
    output_path: str | None = None
    error: str | None = None
    kind: str = "draft"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryboardResult:
    """Normalized result of a reference-video decompose call.

    ``shots`` is the vendor storyboard as a list of dicts; field names are
    vendor-specific — pure logic (``summarize_storyboards``) normalizes them
    before the Brief prompt sees anything.
    """

    ok: bool
    shots: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageGenerationResult:
    """Normalized result of one image-generation call.

    ``urls`` preserves multi-image outputs. ``url`` is the first image for
    callers that only need a single asset.
    """

    ok: bool
    urls: list[str] = field(default_factory=list)
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


class VideoEditSkill(Protocol):
    """Capability the EDIT phase needs: assemble clips into a final artifact.

    Implementations own the editor contract (剪映 draft format / FFmpeg cmds)
    and any media fetching/probing. The plan is passed per call — the graph
    encodes no editor specifics.
    """

    async def render(self, timeline: dict, *, draft_name: str, output_root: str | None = None) -> EditResult: ...


class VideoDecomposeSkill(Protocol):
    """Capability the INTAKE phase needs: parse a reference video into a storyboard.

    Implementations own the vendor contract (博观 decompose_video_to_storyboard —
    the only video-understanding endpoint; there is no separate OCR/ASR).
    """

    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult: ...


class ImageGenerationSkill(Protocol):
    """Capability the image-generation phases need."""

    async def text_to_image(
        self,
        prompt: str,
        *,
        size: str = "2K",
        ratio: str = "1:1",
        num_images: int = 1,
        model: str | None = None,
    ) -> ImageGenerationResult: ...

    async def image_to_image(
        self,
        image_urls: list[str],
        prompt: str,
        *,
        size: str = "2K",
        ratio: str = "1:1",
        num_images: int = 1,
        model: str | None = None,
    ) -> ImageGenerationResult: ...


def get_video_skill() -> VideoGenerationSkill:
    """Return the configured video-generation skill.

    This is the single swap point for the implementation. MVP returns the
    in-process Borgrise skill; ``PIXELFLOW_VIDEO_SKILL`` is reserved for
    selecting alternative implementations (e.g. a sandbox-executed skill in P1).
    """
    impl = os.environ.get("PIXELFLOW_VIDEO_SKILL", "borgrise").strip().lower()
    if impl == "borgrise":
        from pixelflow.skills.borgrise import BorgriseSkill

        return BorgriseSkill()
    if impl in {"seedance", "ark_seedance", "ark-seedance"}:
        from pixelflow.skills.ark_seed import SeedanceSkill

        return SeedanceSkill()
    raise ValueError(f"Unknown video skill implementation: {impl!r}")


def get_video_edit_skill() -> VideoEditSkill:
    """Return the configured video-edit skill (the EDIT-phase swap point).

    Default is the 剪映-draft skill (pyJianYingDraft); ``PIXELFLOW_EDIT_SKILL=ffmpeg``
    selects the headless FFmpeg renderer that produces a finished mp4.
    """
    impl = os.environ.get("PIXELFLOW_EDIT_SKILL", "jianying").strip().lower()
    if impl == "jianying":
        from pixelflow.skills.jianying import JianYingEditSkill

        return JianYingEditSkill()
    if impl == "ffmpeg":
        from pixelflow.skills.ffmpeg import FFmpegEditSkill

        return FFmpegEditSkill()
    raise ValueError(f"Unknown video edit skill implementation: {impl!r}")


def get_video_decompose_skill() -> VideoDecomposeSkill:
    """Return the configured reference-video decompose skill (the INTAKE-phase swap point)."""
    impl = os.environ.get("PIXELFLOW_DECOMPOSE_SKILL", "borgrise").strip().lower()
    if impl == "borgrise":
        from pixelflow.skills.borgrise import BorgriseSkill

        return BorgriseSkill()
    raise ValueError(f"Unknown video decompose skill implementation: {impl!r}")


def get_image_skill() -> ImageGenerationSkill:
    """Return the configured image-generation skill."""
    impl = os.environ.get("PIXELFLOW_IMAGE_SKILL", "seedream").strip().lower()
    if impl in {"seedream", "ark_seedream", "ark-seedream"}:
        from pixelflow.skills.ark_seed import SeedreamSkill

        return SeedreamSkill()
    raise ValueError(f"Unknown image skill implementation: {impl!r}")
