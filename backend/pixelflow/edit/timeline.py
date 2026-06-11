"""build_timeline — assemble generated segment clips into a Timeline IR (pure logic).

GENERATE produces one clip per *segment* (a group of consecutive shots, ≤15s,
generated in a single seedance call). build_timeline places each successfully
generated segment clip on the timeline in order, trimmed to the segment's exact
duration, and concatenated; segments whose generation failed are skipped with a
note so the gap stays visible to QC/UI.

Pure and deterministic: no I/O, fully testable offline. The actual render is a
separate skill that consumes the returned :class:`Timeline`.
"""

from __future__ import annotations

from .models import Clip, Timeline


def build_timeline(brief: dict, generated_assets: list[dict]) -> tuple[Timeline, list[str]]:
    """Build a :class:`Timeline` from the Brief and the GENERATE phase output.

    ``generated_assets`` is the per-segment output from ``generate_node``.
    Returns ``(timeline, notes)``; ``notes`` records skipped segments.
    """
    clips: list[Clip] = []
    notes: list[str] = []
    for asset in generated_assets:
        index = asset.get("segment_index", 0)
        if not (asset.get("ok") and asset.get("url")):
            notes.append(f"片段 {index} 生成失败，已跳过")
            continue
        clips.append(
            Clip(
                shot_index=index,
                shot_id=f"seg_{index:03d}",
                source_url=asset["url"],
                duration=asset.get("duration", 0.0),
            )
        )

    timeline = Timeline(
        clips=clips,
        ratio=brief.get("ratio", "9:16"),
        size=brief.get("size", "1080x1920"),
        platform=brief.get("platform", ""),
        total_duration=round(sum(c.duration for c in clips), 2),
    )
    return timeline, notes
