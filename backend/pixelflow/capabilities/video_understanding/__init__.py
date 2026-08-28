"""视频理解 Capability Port 与 Adapter。"""

from .port import VideoAnalysisResult, VideoUnderstandingPort
from .providers.content_app import ContentAppVideoUnderstandingAdapter

__all__ = ["ContentAppVideoUnderstandingAdapter", "VideoAnalysisResult", "VideoUnderstandingPort"]
