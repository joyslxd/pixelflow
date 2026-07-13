"""参考视频 storyboard 摘要，纯逻辑实现。

参考视频拆解 skill 返回的字段名可能来自不同供应商，格式并不完全稳定。这里把
``reference_videos`` 中的 vendor storyboard 宽松归一到 ``brief_generate`` 能
消费的 ``reference_analysis`` 结构：每个 shot 尽量保留 index、duration、
description、camera、narration 等信息。

这是纯函数，不做 I/O，输出完全由入参决定，方便离线单测。每个视频最多保留
``MAX_SHOTS_PER_VIDEO`` 个镜头，避免把 Brief prompt 撑得过大。
"""

from __future__ import annotations

from typing import Any

MAX_SHOTS_PER_VIDEO = 12

_DURATION_KEYS = ("duration", "duration_sec", "seconds", "time")
_DESCRIPTION_KEYS = ("visual_description", "description", "desc", "content", "text", "prompt")
_CAMERA_KEYS = ("camera", "camera_movement", "shot_type")
_NARRATION_KEYS = ("narration_text", "voice_content", "narration", "voiceover")


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
    narration = _first(raw, _NARRATION_KEYS)
    if narration:
        out["narration"] = str(narration)
    return out


def summarize_storyboards(reference_videos: list | None) -> dict[str, Any] | None:
    """从已拆解的参考视频中构造 ``reference_analysis``。

    没有可用 storyboard 时返回 None，表示后续策划走原创模式。
    """
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
