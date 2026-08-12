"""从脚本 Markdown 抽取镜头列表，供场景包按成稿镜数拆解。"""

from __future__ import annotations

import math
import re
from typing import Any

from pixelflow.creative.duration import (
    MAX_SCENE_DURATION_SEC,
    MAX_VIDEO_DURATION_SEC,
    MIN_SCENE_DURATION_SEC,
    MIN_VIDEO_DURATION_SEC,
)
from pixelflow.creative.scene_blueprint import (
    repair_scene_blueprints_schedule,
)

# 对齐 plan_video 模板：- 镜头1-「00:00-00:05」 / 镜头 2 「01:02-01:15」
_SHOT_LINE_PATTERN = re.compile(
    r"(?m)^[\s>*-]*\*?\*?(?:镜头|分镜)\s*(?P<index>\d+)\s*[*」』\]】）)]*\s*"
    r"(?:[-—–:：]\s*)?"
    r"(?:[「『\[【(（])?"
    r"(?:(?P<sh>\d{1,2}):)?(?P<sm>\d{1,2}):(?P<ss>\d{2})"
    r"\s*[-–—~～到至]\s*"
    r"(?:(?P<eh>\d{1,2}):)?(?P<em>\d{1,2}):(?P<es>\d{2})"
    r"(?:[」』\]】)）])?"
)

_SHOT_HEADING_PATTERN = re.compile(
    r"(?m)^#{1,4}\s*(?:镜头|分镜)\s*(?P<index>\d+)\s*[:：.\s]*(?P<title>[^\n]*)"
)

_PICTURE_LINE_PATTERN = re.compile(
    r"(?m)^[\s>*-]*(?:画面|镜头描述|提示词|画面提示词)\s*[:：]\s*(?P<body>.+)$"
)


def _timecode_to_sec(hour: str | None, minute: str, second: str) -> int:
    hours = int(hour or 0)
    return hours * 3600 + int(minute) * 60 + int(second)


def extract_script_shot_entries(plan_markdown: str) -> list[dict[str, Any]]:
    """解析脚本中的镜头条目（索引、起止秒、标题候选、画面摘要）。"""

    text = str(plan_markdown or "")
    if not text.strip():
        return []
    entries: list[dict[str, Any]] = []
    for match in _SHOT_LINE_PATTERN.finditer(text):
        start = _timecode_to_sec(match.group("sh"), match.group("sm"), match.group("ss"))
        end = _timecode_to_sec(match.group("eh"), match.group("em"), match.group("es"))
        if end <= start:
            continue
        index = int(match.group("index"))
        block_end = match.end()
        next_shot = _SHOT_LINE_PATTERN.search(text, block_end)
        heading = _SHOT_HEADING_PATTERN.search(text, block_end)
        block = text[block_end : next_shot.start() if next_shot else len(text)]
        picture = ""
        picture_match = _PICTURE_LINE_PATTERN.search(block)
        if picture_match:
            picture = picture_match.group("body").strip()
        else:
            prose = [
                line.strip(" -\t>")
                for line in block.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            picture = prose[0] if prose else ""
        title = f"镜头{index}"
        if heading and int(heading.group("index")) == index:
            extra = heading.group("title").strip(" -—–:")
            if extra:
                title = extra[:80]
        entries.append(
            {
                "index": index,
                "start_sec": start,
                "end_sec": end,
                "duration_sec": end - start,
                "title": title,
                "storyline": (picture or title)[:500],
                "shot_description": picture or f"{start}-{end}秒: {title}",
                "narration": "本分镜无旁白",
                "transition": "按动作完成点衔接下一镜头。",
                "asset_requirements": {"characters": [], "scenes": [], "props": []},
            }
        )
    entries.sort(key=lambda item: (item["start_sec"], item["index"]))
    # 去重：同 start 只留第一次（避免目录+正文重复）
    deduped: list[dict[str, Any]] = []
    seen_starts: set[int] = set()
    for item in entries:
        start = int(item["start_sec"])
        if start in seen_starts:
            continue
        seen_starts.add(start)
        deduped.append(item)
    return deduped


def extract_script_scene_blueprints(
    plan_markdown: str,
    *,
    target_duration_ms: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """把脚本镜头列表转成可规范化的 scene_blueprints，并返回权威总时长毫秒。

    无镜头时间码时返回空列表，调用方回退到机械切分。
    """

    entries = extract_script_shot_entries(plan_markdown)
    if len(entries) < 2:
        return int(target_duration_ms or 0), []

    script_total = max(int(item["end_sec"]) for item in entries)
    form_total = 0
    if isinstance(target_duration_ms, int) and target_duration_ms >= 1000:
        form_total = target_duration_ms // 1000
    total = max(script_total, form_total, len(entries) * MIN_SCENE_DURATION_SEC)
    total = min(max(total, MIN_VIDEO_DURATION_SEC), MAX_VIDEO_DURATION_SEC)

    max_count = total // MIN_SCENE_DURATION_SEC
    min_count = math.ceil(total / MAX_SCENE_DURATION_SEC)
    raw = []
    for position, item in enumerate(entries, start=1):
        duration = max(
            MIN_SCENE_DURATION_SEC,
            min(MAX_SCENE_DURATION_SEC, int(item["duration_sec"])),
        )
        storyline = str(item["storyline"] or item["title"])[:500]
        raw.append(
            {
                "title": item["title"],
                "storyline": storyline,
                "shot_description": f"0-{duration}秒: {storyline}",
                "narration": item["narration"],
                "transition": item["transition"],
                "structure_role": "opening"
                if position == 1
                else ("conclusion" if position == len(entries) else "development"),
                "duration_sec": duration,
                "start_sec": 0,
                "end_sec": 0,
                "asset_requirements": item["asset_requirements"],
            }
        )

    # 镜数超出总时长可承载上限时，按时间权重合并相邻镜。
    while len(raw) > max_count and len(raw) >= 2:
        merge_at = min(range(len(raw) - 1), key=lambda idx: raw[idx]["duration_sec"] + raw[idx + 1]["duration_sec"])
        left = raw[merge_at]
        right = raw[merge_at + 1]
        merged_duration = min(
            MAX_SCENE_DURATION_SEC,
            max(MIN_SCENE_DURATION_SEC, int(left["duration_sec"]) + int(right["duration_sec"])),
        )
        storyline = f"{left['storyline']}；{right['storyline']}"
        raw[merge_at] = {
            **left,
            "title": f"{left['title']} / {right['title']}",
            "storyline": storyline[:500],
            "shot_description": f"0-{merged_duration}秒: {storyline[:400]}",
            "duration_sec": merged_duration,
        }
        del raw[merge_at + 1]

    # 镜数不足时，不再凭空拆镜；交给时长加权铺满总时长。
    if len(raw) < min_count:
        return int(target_duration_ms or total * 1000), []

    try:
        blueprints = repair_scene_blueprints_schedule(raw, total_duration_sec=total)
    except ValueError:
        return int(target_duration_ms or total * 1000), []
    return total * 1000, blueprints


__all__ = [
    "extract_script_scene_blueprints",
    "extract_script_shot_entries",
]
