"""build_draft_plan — flatten a Timeline IR into a DraftPlan (pure logic).

Resolves the pixel canvas from ``timeline.size`` ("WxH") and lays the clips
end-to-end on the main track, accumulating absolute start offsets. A concrete
render skill (剪映 / FFmpeg) then translates the plan one segment at a time
without doing any timing math itself.

Pure and deterministic: no I/O, fully testable offline.
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
    """Translate an assembled :class:`~pixelflow.edit.models.Timeline` (as a dict)
    into a :class:`DraftPlan` with absolute clip offsets."""
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
