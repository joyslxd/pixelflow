"""PixelFlow skills: capability interfaces and their implementations."""

from pixelflow.skills.base import (
    GenerationResult,
    VideoGenerationSkill,
    get_video_skill,
)

__all__ = ["GenerationResult", "VideoGenerationSkill", "get_video_skill"]
