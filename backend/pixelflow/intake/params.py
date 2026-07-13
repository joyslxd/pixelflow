"""视频参数归一化，纯逻辑实现（PRD §8.4）。

这里不调用 LLM，也不访问第三方服务，只把前端或用户传入的参数修正到当前平台
支持的范围：时长吸附到支持档位、分辨率固定为 MVP 支持的 1080p、未知平台只
记录提示而不直接拒绝。

返回值包含两个部分：归一化后的参数 dict，以及可展示给用户的调整说明。
"""

from __future__ import annotations

from .models import DURATION_BUCKETS, FIXED_RESOLUTION, SUPPORTED_PLATFORMS


def normalize_video_params(params: dict | None) -> tuple[dict, list[str]]:
    """安全归一化可能不完整的视频参数。

    函数会复制入参再修改，不会原地污染 ``TaskState`` 中已有的 dict。
    """
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
