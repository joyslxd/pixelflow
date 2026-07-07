"""PixelFlow skills: capability interfaces and their implementations."""

from pixelflow.skills.base import (
    EditResult,
    GenerationResult,
    ImageGenerationResult,
    ImageGenerationSkill,
    StoryboardResult,
    VideoDecomposeSkill,
    VideoEditSkill,
    VideoGenerationSkill,
    get_image_skill,
    get_video_decompose_skill,
    get_video_edit_skill,
    get_video_skill,
)

__all__ = [
    "EditResult",
    "GenerationResult",
    "ImageGenerationResult",
    "ImageGenerationSkill",
    "StoryboardResult",
    "VideoDecomposeSkill",
    "VideoEditSkill",
    "VideoGenerationSkill",
    "get_image_skill",
    "get_video_decompose_skill",
    "get_video_edit_skill",
    "get_video_skill",
]
