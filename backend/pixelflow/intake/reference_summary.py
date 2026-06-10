"""summarize_storyboards — compact reference-video analysis for the Brief prompt (pure logic).

Normalizes the vendor storyboards attached to ``reference_videos`` (by the
decompose skill) into the ``reference_analysis`` structure ``brief_generate``
consumes. Vendor field names vary, so each shot is mapped tolerantly onto
{index, duration, description, camera} and capped per video to keep the prompt
bounded.

Pure and deterministic: no I/O, fully testable offline.
"""

from __future__ import annotations

from typing import Any

MAX_SHOTS_PER_VIDEO = 12

_DURATION_KEYS = ("duration", "duration_sec", "seconds", "time")
_DESCRIPTION_KEYS = ("visual_description", "description", "desc", "content", "text", "prompt")
_CAMERA_KEYS = ("camera", "camera_movement", "shot_type")


def _first(raw: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _shot(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"index": index, "description": str(raw)}
    out: dict[str, Any] = {"index": index}
    duration = _first(raw, _DURATION_KEYS)
    try:
        if duration is not None:
            out["duration"] = round(float(duration), 2)
    except (TypeError, ValueError):
        pass
    description = _first(raw, _DESCRIPTION_KEYS)
    if description:
        out["description"] = str(description)
    camera = _first(raw, _CAMERA_KEYS)
    if camera:
        out["camera"] = str(camera)
    return out


def summarize_storyboards(reference_videos: list | None) -> dict[str, Any] | None:
    """Build ``reference_analysis`` from parsed reference videos; None when nothing usable."""
    videos: list[dict[str, Any]] = []
    for ref in reference_videos or []:
        ref = ref or {}
        board = ref.get("storyboard")
        if not isinstance(board, list) or not board:
            continue
        shots = [_shot(s, i) for i, s in enumerate(board[:MAX_SHOTS_PER_VIDEO])]
        video: dict[str, Any] = {"url": ref.get("url", ""), "shot_count": len(board), "shots": shots}
        durations = [s["duration"] for s in shots if "duration" in s]
        if durations:
            video["total_duration"] = round(sum(durations), 2)
            video["avg_shot_duration"] = round(sum(durations) / len(durations), 2)
        videos.append(video)
    if not videos:
        return None
    return {"video_count": len(videos), "videos": videos}
