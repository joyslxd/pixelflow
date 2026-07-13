"""把 Timeline IR 扁平化为 DraftPlan，纯逻辑实现。

函数会从 ``timeline.size``（如 "1080x1920"）解析像素画布，并把片段按顺序铺到
主轨上，累加出每个片段的绝对开始时间。具体 render skill（剪映 / FFmpeg）拿到
DraftPlan 后只需要逐段翻译，不需要再做时间轴数学。

这是纯逻辑，不做 I/O，输出完全由入参决定，方便离线单测。
"""

from __future__ import annotations

from .models import DraftPlan, DraftSegment

_DEFAULT_SIZE = (1080, 1920)


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return _DEFAULT_SIZE


def build_draft_plan(timeline: dict, fps: int = 30) -> DraftPlan:
    """把已装配的 ``Timeline`` dict 转成带绝对片段偏移的 ``DraftPlan``。"""
    width, height = _parse_size(timeline.get("size", "1080x1920"))

    segments: list[DraftSegment] = []
    cursor = 0.0
    for clip in timeline.get("clips", []):
        duration = float(clip.get("duration", 0.0))
        segments.append(
            DraftSegment(
                source_url=clip.get("source_url", ""),
                start=round(cursor, 3),
                duration=duration,
                transition_in=clip.get("transition_in", ""),
                caption=clip.get("onscreen_text", ""),
            )
        )
        cursor += duration

    return DraftPlan(width=width, height=height, fps=fps, segments=segments)
