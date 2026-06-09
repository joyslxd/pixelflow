"""PixelFlow skills: capability interfaces and their implementations."""

from pixelflow.skills.base import (
    EditResult,
    GenerationResult,
    VideoEditSkill,
    VideoGenerationSkill,
    get_video_edit_skill,
    get_video_skill,
)

__all__ = [
    "EditResult",
    "GenerationResult",
    "VideoEditSkill",
    "VideoGenerationSkill",
    "get_video_edit_skill",
    "get_video_skill",
]
