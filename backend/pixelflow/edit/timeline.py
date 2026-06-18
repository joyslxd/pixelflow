"""把生成片段装配成 Timeline IR，纯逻辑实现。

GENERATE 当前按 segment 生成视频：一个 segment 是一组连续 shots，并由一次
Seedance 调用生成。``build_timeline`` 会把每个成功生成的 segment clip 按顺序放入
Timeline，时长使用 segment 的精确规划时长；失败的 segment 会跳过并写入 notes，
让 QC 和前端都能看到缺口。

这里不做下载、不调剪辑工具，是可离线测试的纯函数。真正渲染由后续 edit skill
消费返回的 ``Timeline``。
"""

from __future__ import annotations

from .models import Clip, Timeline


def build_timeline(brief: dict, generated_assets: list[dict]) -> tuple[Timeline, list[str]]:
    """根据 Brief 和 GENERATE 输出构建 ``Timeline``。

    ``generated_assets`` 是 ``generate_node`` 的 per-segment 结果。返回
    ``(timeline, notes)``，其中 ``notes`` 记录被跳过的失败片段。
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
