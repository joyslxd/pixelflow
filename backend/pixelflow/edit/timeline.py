"""build_timeline — assemble generated clips into a Timeline IR (pure logic).

Binds each Brief shot to its generated clip (by ``shot_index``), keeps only the
shots that produced a usable video, and carries the shot's editing metadata
(duration, transitions, narration/onscreen text) onto the timeline. Shots whose
generation failed are skipped with a note so the gap stays visible to QC/UI.

Pure and deterministic: no I/O, fully testable offline. The actual render is a
separate skill that consumes the returned :class:`Timeline`.
"""

from __future__ import annotations

from .models import Clip, Timeline


def build_timeline(brief: dict, generated_assets: list[dict]) -> tuple[Timeline, list[str]]:
    """Build a :class:`Timeline` from the Brief and the GENERATE phase output.

    Returns ``(timeline, notes)``; ``notes`` records skipped shots.
    """
    shots = brief.get("shots", [])
    assets_by_index = {a.get("shot_index"): a for a in generated_assets}

    clips: list[Clip] = []
    notes: list[str] = []
    for i, shot in enumerate(shots):
        asset = assets_by_index.get(i)
        if not (asset and asset.get("ok") and asset.get("url")):
            notes.append(f"分镜 {i} 无可用片段，已跳过")
            continue
        clips.append(
            Clip(
                shot_index=i,
                shot_id=shot.get("shot_id", f"shot_{i:03d}"),
                source_url=asset["url"],
                duration=shot.get("duration", 0.0),
                transition_in=shot.get("transition_in", ""),
                transition_out=shot.get("transition_out", ""),
                narration_text=shot.get("narration_text", ""),
                onscreen_text=shot.get("onscreen_text", ""),
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
