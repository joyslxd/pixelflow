"""Video-parameter normalization — pure logic (PRD §8.4).

No LLM. Snap duration to a supported bucket, force the MVP resolution, and flag
an unsupported platform. Returns the normalized params dict plus human-readable
adjustment notes the Agent can echo back to the user.
"""

from __future__ import annotations

from .models import DURATION_BUCKETS, FIXED_RESOLUTION, SUPPORTED_PLATFORMS


def normalize_video_params(params: dict | None) -> tuple[dict, list[str]]:
    """Normalize a (possibly partial) video-params dict in place-safe fashion."""
    out = dict(params or {})
    notes: list[str] = []

    duration = out.get("video_duration_sec")
    if duration is not None and duration not in DURATION_BUCKETS:
        nearest = min(DURATION_BUCKETS, key=lambda b: abs(b - duration))
        out["video_duration_sec"] = nearest
        notes.append(f"视频时长 {duration}s 不在支持档位，已归一到最近的 {nearest}s")

    resolution = out.get("video_resolution")
    if resolution and resolution != FIXED_RESOLUTION:
        out["video_resolution"] = FIXED_RESOLUTION
        notes.append(f"当前版本仅支持 {FIXED_RESOLUTION}，已将分辨率设为 {FIXED_RESOLUTION}")
    else:
        out.setdefault("video_resolution", FIXED_RESOLUTION)

    platform = out.get("platform")
    if platform and platform not in SUPPORTED_PLATFORMS:
        notes.append(f"暂不支持平台「{platform}」，请从 {', '.join(SUPPORTED_PLATFORMS)} 中选择")

    out.setdefault("segment_strategy", "auto")
    return out, notes
