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


def get_video_edit_skill() -> VideoEditSkill:
    """Return the configured video-edit skill (the EDIT-phase swap point).

    Default is the 剪映-draft skill (pyJianYingDraft); ``PIXELFLOW_EDIT_SKILL=ffmpeg``
    selects the headless FFmpeg renderer that produces a finished mp4.
    """
    impl = os.environ.get("PIXELFLOW_EDIT_SKILL", "jianying")
    if impl == "jianying":
        from pixelflow.skills.jianying import JianYingEditSkill

        return JianYingEditSkill()
    if impl == "ffmpeg":
        from pixelflow.skills.ffmpeg import FFmpegEditSkill

        return FFmpegEditSkill()
    raise ValueError(f"Unknown video edit skill implementation: {impl!r}")
