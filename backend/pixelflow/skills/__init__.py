"""PixelFlow 外部能力适配层。

Skill 可以理解成 Java 里的第三方 Client + interface：图节点只依赖接口和统一
Result DTO，不直接依赖某个供应商的 HTTP 细节。
"""

from pixelflow.skills.base import (
    BatchStoryboardResult,
    EditResult,
    GenerationResult,
    ImageGenerationResult,
    ImageGenerationSkill,
    MediaLinkExtractionResult,
    MediaLinkExtractionSkill,
    PptGenerationResult,
    PptGenerationSkill,
    StoryboardResult,
    VideoDecomposeSkill,
    VideoEditSkill,
    VideoGenerationSkill,
    VideoQualityReviewResult,
    VideoQualityReviewSkill,
    get_image_skill,
    get_media_link_extraction_skill,
    get_ppt_skill,
    get_video_decompose_skill,
    get_video_edit_skill,
    get_video_quality_review_skill,
    get_video_skill,
)

__all__ = [
    "BatchStoryboardResult",
    "EditResult",
    "GenerationResult",
    "ImageGenerationResult",
    "ImageGenerationSkill",
    "MediaLinkExtractionResult",
    "MediaLinkExtractionSkill",
    "PptGenerationResult",
    "PptGenerationSkill",
    "StoryboardResult",
    "VideoDecomposeSkill",
    "VideoEditSkill",
    "VideoGenerationSkill",
    "VideoQualityReviewResult",
    "VideoQualityReviewSkill",
    "get_image_skill",
    "get_media_link_extraction_skill",
    "get_ppt_skill",
    "get_video_decompose_skill",
    "get_video_edit_skill",
    "get_video_quality_review_skill",
    "get_video_skill",
]
