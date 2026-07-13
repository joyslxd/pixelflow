"""Timeline 中间表示：EDIT 阶段的装配合同。

Timeline 是一个与具体剪辑工具无关的成片描述：有序 clips 列表加输出格式。每个
clip 绑定一个生成出来的视频源。真正的渲染由 FFmpeg 或剪映 draft skill 处理，
因此 LangGraph 主流程不需要写具体剪辑工具的细节。
"""

from __future__ import annotations

from pydantic import BaseModel


class Clip(BaseModel):
    """时间线上的一个片段，按播放顺序排列。"""

    shot_index: int
    shot_id: str
    source_url: str  # 绑定到该片段的生成视频 URL。
    duration: float  # 片段时长，单位秒，来自 Brief 或 segment 规划。
    transition_in: str = ""
    transition_out: str = ""
    narration_text: str = ""  # 旁白，在渲染阶段做 TTS 或叠加。
    onscreen_text: str = ""  # 花字，在渲染阶段烧录为字幕/标题。


class Timeline(BaseModel):
    """完整剪辑时间线：有序片段加输出格式。"""

    clips: list[Clip]
    ratio: str = "9:16"
    size: str = "1080x1920"
    platform: str = ""
    total_duration: float = 0.0


class DraftSegment(BaseModel):
    """已计算绝对时间偏移的片段，可直接交给草稿/渲染构建器。"""

    source_url: str
    start: float  # 主轨上的绝对开始时间，单位秒。
    duration: float  # 片段时长，单位秒。
    transition_in: str = ""  # 从上一个片段进入当前片段的转场。
    caption: str = ""  # 渲染时烧录的花字，来自 shot.onscreen_text。


class DraftPlan(BaseModel):
    """扁平化渲染计划：像素画布加绝对定位片段。

    它连接 Timeline IR 和具体编辑器（剪映 / FFmpeg）：提前解析画布尺寸并计算每个
    片段的开始时间，让 render skill 只做一对一翻译，不再承担时间轴计算。
    """

    width: int
    height: int
    fps: int = 30
    segments: list[DraftSegment]
