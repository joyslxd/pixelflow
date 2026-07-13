"""Strict integer-second duration allocation shared by video Plan and scenes."""

from __future__ import annotations

import math
from collections.abc import Sequence

MIN_VIDEO_DURATION_SEC = 4
MAX_VIDEO_DURATION_SEC = 300
MIN_SCENE_DURATION_SEC = 4
MAX_SCENE_DURATION_SEC = 15
PREFERRED_SCENE_DURATION_SEC = 10


def split_video_duration(total_seconds: int, preferred_seconds: int = PREFERRED_SCENE_DURATION_SEC) -> list[int]:
    """Split a supported total into exact 4-15 second integer scenes."""
    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int) or not MIN_VIDEO_DURATION_SEC <= total_seconds <= MAX_VIDEO_DURATION_SEC:
        raise ValueError("video total duration must be an integer between 4 and 300 seconds")
    if isinstance(preferred_seconds, bool) or not isinstance(preferred_seconds, int) or preferred_seconds <= 0:
        raise ValueError("preferred scene duration must be a positive integer")

    minimum_count = math.ceil(total_seconds / MAX_SCENE_DURATION_SEC)
    maximum_count = total_seconds // MIN_SCENE_DURATION_SEC
    preferred_count = max(1, round(total_seconds / preferred_seconds))
    scene_count = min(max(preferred_count, minimum_count), maximum_count)
    base_duration, remainder = divmod(total_seconds, scene_count)
    durations = [base_duration + (1 if index < remainder else 0) for index in range(scene_count)]

    if sum(durations) != total_seconds or any(duration < MIN_SCENE_DURATION_SEC or duration > MAX_SCENE_DURATION_SEC for duration in durations):
        raise ValueError(f"unable to allocate exact scene durations for {total_seconds} seconds")
    return durations


def scene_time_ranges(durations: Sequence[int]) -> list[tuple[int, int]]:
    """Return contiguous integer-second ranges for validated scene durations."""
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for duration in durations:
        if isinstance(duration, bool) or not isinstance(duration, int) or not MIN_SCENE_DURATION_SEC <= duration <= MAX_SCENE_DURATION_SEC:
            raise ValueError("scene duration must be an integer between 4 and 15 seconds")
        ranges.append((cursor, cursor + duration))
        cursor += duration
    return ranges
