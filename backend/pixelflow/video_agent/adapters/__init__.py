"""VideoAgent 与既有视频领域能力之间的防腐适配层。"""

from .video_domain import (
    PixelFlowVideoDomainAdapter,
    ReferenceVideoAnalysis,
    VideoDomainAdapter,
    VideoDomainAdapterError,
)

__all__ = [
    "PixelFlowVideoDomainAdapter",
    "ReferenceVideoAnalysis",
    "VideoDomainAdapter",
    "VideoDomainAdapterError",
]
