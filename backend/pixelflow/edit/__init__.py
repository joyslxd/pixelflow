"""剪辑阶段能力入口。

EDIT 阶段先把生成资产装配为工具无关的 Timeline 中间表示，再转换为剪映草稿或
FFmpeg 渲染计划。
"""

from pixelflow.edit.draft_plan import build_draft_plan
from pixelflow.edit.ffmpeg_plan import build_ffmpeg_args, passthrough_eligible
from pixelflow.edit.models import Clip, DraftPlan, DraftSegment, Timeline
from pixelflow.edit.timeline import build_timeline

__all__ = ["Clip", "DraftPlan", "DraftSegment", "Timeline", "build_draft_plan", "build_ffmpeg_args", "build_timeline", "passthrough_eligible"]
