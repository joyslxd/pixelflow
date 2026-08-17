"""从脚本 Markdown 抽取镜头列表，供场景包按成稿镜数拆解。"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
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

# 成稿时间线：0—10秒｜标题 / 10-20秒|开场（用户粘贴微电影分镜常见）
_TIMELINE_SHOT_PATTERN = re.compile(
    r"(?m)^[\s>*-]*(?P<start>\d{1,4})\s*[-—–~～到至]\s*(?P<end>\d{1,4})\s*秒"
    r"(?:\s*[|｜]\s*(?P<title>[^\n]*))?"
)

_SHOT_HEADING_PATTERN = re.compile(
    r"(?m)^#{1,4}\s*(?:镜头|分镜)\s*(?P<index>\d+)\s*[:：.\s]*(?P<title>[^\n]*)"
)

# episode 常见：## 镜头1 0:00-0:10 / ### 分镜 2：00:10-00:20 开场
# 时码必须落在标题行末尾，避免把下一行「景别/画面…」吞进 match。
_SHOT_HEADING_TIME_PATTERN = re.compile(
    r"(?m)^#{1,4}\s*(?:镜头|分镜)\s*(?P<index>\d+)\s*[:：.\s]*"
    r"(?P<title>(?:(?!\d{1,2}:\d{2})[^\n])*?)\s*"
    r"(?:(?P<sh>\d{1,2}):)?(?P<sm>\d{1,2}):(?P<ss>\d{2})"
    r"\s*[-–—~～到至]\s*"
    r"(?:(?P<eh>\d{1,2}):)?(?P<em>\d{1,2}):(?P<es>\d{2})"
    r"[ \t]*(?P<title_after>[^\n]*)$"
)

# episode 成稿标准字段顺序（写入 shot_description，供分镜表展示）
_EPISODE_FIELD_ORDER = (
    "景别",
    "运镜",
    "画面",
    "旁白（对白）",
    "屏幕文案",
    "行动引导",
)

# 镜块内字段标签（兼容 **加粗**、普通冒号；含 episode 标准六字段）
# 「旁白/对白」「旁白/對白」必须写在「旁白」之前，避免被短标签抢先匹配；简繁同认。
_FIELD_LABEL_ALT = (
    r"旁白（对白）|旁白（對白）|旁白/对白|旁白/對白|旁白／对白|旁白／對白|"
    r"屏幕文案|行动引导|画面|镜头描述|提示词|画面提示词|"
    r"剧情/?动作|剧情|动作|新增对白|新增對白|原片对白|原片對白|原关键对白|原关键對白|对白|對白|"
    r"产品演示|追剧钩子|投流记忆点|旁白|画外音|时间|时长|景别|运镜|地点|场景"
)

_FIELD_LINE_PATTERN = re.compile(
    rf"(?m)^[\s>*\-]*\*?\s*\*?\*?\s*"
    rf"(?P<label>{_FIELD_LABEL_ALT})"
    rf"\*?\*?\s*[:：]\s*(?P<body>.+)$"
)

# 同行内联多字段：景别：近景。运镜：缓推。画面：…。旁白：…
_INLINE_FIELD_PATTERN = re.compile(
    rf"(?P<label>{_FIELD_LABEL_ALT})\s*[:：]\s*"
)

# 【剧情/动作】正文
_BRACKET_FIELD_PATTERN = re.compile(
    r"(?m)^[\s>*\-]*【\s*(?P<label>[^】]+?)\s*】\s*(?P<body>.+)$"
)

_TIME_META_LABELS = frozenset({"时间", "时长"})
_NARRATION_CANONICAL = "旁白（对白）"
_PICTURE_CANONICAL = "画面"

# 别名 → 写入 shot_description 的规范名（简繁「对/對」均归一为「旁白（对白）」）
_FIELD_ALIASES: dict[str, str] = {
    "旁白": _NARRATION_CANONICAL,
    "旁白/对白": _NARRATION_CANONICAL,
    "旁白/對白": _NARRATION_CANONICAL,
    "旁白／对白": _NARRATION_CANONICAL,
    "旁白／對白": _NARRATION_CANONICAL,
    "画外音": _NARRATION_CANONICAL,
    "对白": _NARRATION_CANONICAL,
    "對白": _NARRATION_CANONICAL,
    "新增对白": _NARRATION_CANONICAL,
    "新增對白": _NARRATION_CANONICAL,
    "原片对白": _NARRATION_CANONICAL,
    "原片對白": _NARRATION_CANONICAL,
    "原关键对白": _NARRATION_CANONICAL,
    "原关键對白": _NARRATION_CANONICAL,
    "旁白（对白）": _NARRATION_CANONICAL,
    "旁白（對白）": _NARRATION_CANONICAL,
    "画面": _PICTURE_CANONICAL,
    "镜头描述": _PICTURE_CANONICAL,
    "提示词": _PICTURE_CANONICAL,
    "画面提示词": _PICTURE_CANONICAL,
    "剧情/动作": _PICTURE_CANONICAL,
    "剧情": _PICTURE_CANONICAL,
    "动作": _PICTURE_CANONICAL,
    "产品演示": _PICTURE_CANONICAL,
    "追剧钩子": _PICTURE_CANONICAL,
    "投流记忆点": _PICTURE_CANONICAL,
    "景别": "景别",
    "运镜": "运镜",
    "屏幕文案": "屏幕文案",
    "行动引导": "行动引导",
    "地点": "地点",
    "场景": "地点",
}

# 纯元数据行：* **时间**: 00:00 - 00:10
_TIME_ONLY_LINE_PATTERN = re.compile(
    r"^[\s>*\-]*\*?\s*\*?\*?时间\*?\*?\s*[:：]\s*"
    r"(?:\d{1,2}:)?\d{1,2}:\d{2}\s*[-–—~～到至]\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s*$"
)


def _timecode_to_sec(hour: str | None, minute: str, second: str) -> int:
    hours = int(hour or 0)
    return hours * 3600 + int(minute) * 60 + int(second)


def _normalize_field_label(label: str) -> str:
    return re.sub(r"\s+", "", str(label or "").strip())


def _canonical_field_label(label: str) -> str | None:
    """把抽取到的标签归一到 episode 规范名；时间元数据返回 None。"""

    cleaned = _normalize_field_label(label)
    if cleaned in _TIME_META_LABELS:
        return None
    return _FIELD_ALIASES.get(cleaned, cleaned if cleaned in _EPISODE_FIELD_ORDER else cleaned)


def _is_time_meta_line(line: str) -> bool:
    cleaned = line.strip()
    if not cleaned:
        return True
    if _TIME_ONLY_LINE_PATTERN.match(cleaned):
        return True
    match = _FIELD_LINE_PATTERN.match(cleaned)
    if match and _normalize_field_label(match.group("label")) in _TIME_META_LABELS:
        return True
    if re.fullmatch(
        r"(?:\d{1,2}:)?\d{1,2}:\d{2}\s*[-–—~～到至]\s*(?:\d{1,2}:)?\d{1,2}:\d{2}",
        cleaned.strip("* "),
    ):
        return True
    return False


def _strip_bullet_markup(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^[\s>*\-]+", "", cleaned).strip()
    cleaned = re.sub(r"^\*+\s*", "", cleaned).strip()
    # * **画面**: xxx → 画面: xxx
    cleaned = re.sub(r"^\*?\*?([^*\n：:]+)\*?\*?\s*[:：]", r"\1：", cleaned, count=1)
    return cleaned.strip()


def _parse_labeled_chunks(text: str) -> list[tuple[str, str]]:
    """拆分「景别：…。运镜：…。画面：…」同行多字段。"""

    source = str(text or "").strip()
    if not source:
        return []
    matches = list(_INLINE_FIELD_PATTERN.finditer(source))
    if not matches:
        return []
    # 整行只有一处标签且标签不在开头时，仍按单字段取 body
    chunks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        canonical = _canonical_field_label(match.group("label"))
        if canonical is None:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[start:end].strip().strip("* ").strip("。．；;，,、 ")
        if body:
            chunks.append((canonical, body))
    return chunks


def _append_field_value(bucket: dict[str, list[str]], label: str, body: str) -> None:
    cleaned = body.strip()
    if not cleaned:
        return
    values = bucket.setdefault(label, [])
    if cleaned not in values:
        values.append(cleaned)


def _block_shot_fields(block: str, *, title: str = "") -> tuple[str, str, str]:
    """从镜块正文抽出 (storyline, shot_description, narration)。

    episode 标准字段（景别/运镜/画面/旁白（对白）/屏幕文案/行动引导）写入镜头描述；
    跳过时间元数据；同行多字段按标签切开。
    """

    fields: dict[str, list[str]] = {}
    prose_parts: list[str] = []

    for raw_line in str(block or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _is_time_meta_line(line):
            continue

        bracket = _BRACKET_FIELD_PATTERN.match(line)
        if bracket:
            canonical = _canonical_field_label(bracket.group("label"))
            body = bracket.group("body").strip().strip("* ").strip()
            if canonical and body:
                _append_field_value(fields, canonical, body)
            elif body:
                prose_parts.append(body)
            continue

        cleaned = _strip_bullet_markup(line)
        if not cleaned or _is_time_meta_line(cleaned):
            continue

        labeled = _parse_labeled_chunks(cleaned)
        if labeled:
            for label, body in labeled:
                _append_field_value(fields, label, body)
            continue

        line_match = _FIELD_LINE_PATTERN.match(cleaned) or _FIELD_LINE_PATTERN.match(line)
        if line_match:
            canonical = _canonical_field_label(line_match.group("label"))
            body = line_match.group("body").strip().strip("* ").strip()
            if canonical and body:
                # body 内可能仍嵌套「运镜：…」
                nested = _parse_labeled_chunks(f"{canonical}：{body}")
                if len(nested) > 1:
                    for label, nested_body in nested:
                        _append_field_value(fields, label, nested_body)
                else:
                    _append_field_value(fields, canonical, body)
            elif body:
                prose_parts.append(body)
            continue

        prose_parts.append(cleaned)

    description_lines: list[str] = []
    for key in _EPISODE_FIELD_ORDER:
        values = fields.get(key) or []
        if values:
            description_lines.append(f"{key}：{'；'.join(values)}")
    for key, values in fields.items():
        if key in _EPISODE_FIELD_ORDER or key in _TIME_META_LABELS:
            continue
        if values:
            description_lines.append(f"{key}：{'；'.join(values)}")
    if not description_lines and prose_parts:
        description_lines.extend(prose_parts)

    shot_description = "\n".join(description_lines).strip()
    if not shot_description:
        shot_description = (title or "").strip()

    picture_values = fields.get(_PICTURE_CANONICAL) or []
    narration_values = fields.get(_NARRATION_CANONICAL) or []
    storyline = (
        "；".join(picture_values).strip()
        or (prose_parts[0] if prose_parts else "")
        or (title or "").strip()
    )
    if storyline and _is_time_meta_line(storyline):
        storyline = (title or "").strip()
    narration = "；".join(narration_values).strip() or "本分镜无旁白"
    shot_description = _ensure_narration_in_shot_description(shot_description, narration)
    return storyline[:500], shot_description[:4_000], narration[:500]


_NARRATION_LINE_PATTERN = re.compile(
    r"(?:旁白（对白）|旁白（對白）|旁白/对白|旁白/對白|旁白／对白|旁白／對白|"
    r"(?:^|\n)旁白)\s*[:：]"
)


def ensure_narration_in_shot_description(shot_description: str, narration: str) -> str:
    """旁白已入 narration 字段时，仍保证 shot_description 含「旁白（对白）：」行。

    分镜面板已去掉底部独立旁白框，只读镜头描述六字段；漏写会导致 UI 看不到对白。
    """

    text = str(shot_description or "").strip()
    narr = str(narration or "").strip()
    if not narr or narr == "本分镜无旁白":
        return text
    if _NARRATION_LINE_PATTERN.search(text):
        return text
    line = f"{_NARRATION_CANONICAL}：{narr}"
    return f"{text}\n{line}".strip() if text else line


# 兼容旧私有名
_ensure_narration_in_shot_description = ensure_narration_in_shot_description


_TABLE_LINE_PATTERN = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_LINE_PATTERN = re.compile(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_SEC_RANGE_IN_CELL_PATTERN = re.compile(
    r"(?P<start>\d{1,4})\s*[-—–~～到至]\s*(?P<end>\d{1,4})\s*秒"
)
_TIMECODE_RANGE_IN_CELL_PATTERN = re.compile(
    r"(?:(?P<sh>\d{1,2}):)?(?P<sm>\d{1,2}):(?P<ss>\d{2})"
    r"\s*[-–—~～到至]\s*"
    r"(?:(?P<eh>\d{1,2}):)?(?P<em>\d{1,2}):(?P<es>\d{2})"
)


def _split_markdown_table_cells(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [cell.strip() for cell in raw.split("|")]


def _parse_time_range_cell(cell: str) -> tuple[int, int] | None:
    """解析表格「时间」单元格：0-10秒 或 00:00-00:10。"""

    text = str(cell or "").strip()
    if not text:
        return None
    match = _SEC_RANGE_IN_CELL_PATTERN.search(text)
    if match:
        start = int(match.group("start"))
        end = int(match.group("end"))
        return (start, end) if end > start else None
    match = _TIMECODE_RANGE_IN_CELL_PATTERN.search(text)
    if not match:
        return None
    start = _timecode_to_sec(match.group("sh"), match.group("sm"), match.group("ss"))
    end = _timecode_to_sec(match.group("eh"), match.group("em"), match.group("es"))
    return (start, end) if end > start else None


def _parse_markdown_shot_tables(text: str) -> list[dict[str, Any]]:
    """从 Markdown 镜头列表表格抽出镜头。

    episode 成稿常见：
    | 时间 | 景别 | 运镜 | 画面 | 旁白/对白 | 屏幕文案 | 行动引导 |
    | 0-10秒 | 中景 | 推 | … | … | … | … |
    旧解析只认「## 镜头N」或行首「0-10秒」，表格行带 ``|`` 前缀时抽不到镜。
    """

    lines = str(text or "").replace("\r\n", "\n").split("\n")
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if not _TABLE_LINE_PATTERN.match(lines[index]):
            index += 1
            continue
        rows: list[list[str]] = []
        while index < len(lines) and _TABLE_LINE_PATTERN.match(lines[index]):
            raw = lines[index]
            index += 1
            if _TABLE_SEP_LINE_PATTERN.match(raw.strip()):
                continue
            rows.append(_split_markdown_table_cells(raw))
        if len(rows) < 2:
            continue
        header = [_normalize_field_label(cell) for cell in rows[0]]
        if "时间" not in header and "时长" not in header:
            continue
        if not any(
            label in header
            for label in (
                "画面",
                "景别",
                "运镜",
                "旁白/对白",
                "旁白/對白",
                "旁白／对白",
                "旁白／對白",
                "旁白（对白）",
                "旁白（對白）",
                "旁白",
                "对白",
                "對白",
                "屏幕文案",
                "行动引导",
            )
        ):
            continue
        time_idx = header.index("时间") if "时间" in header else header.index("时长")
        for position, row in enumerate(rows[1:], start=1):
            padded = list(row) + [""] * max(0, len(header) - len(row))
            parsed = _parse_time_range_cell(padded[time_idx] if time_idx < len(padded) else "")
            if parsed is None:
                continue
            start, end = parsed
            block_lines: list[str] = []
            title = ""
            for col_idx, label in enumerate(header):
                if col_idx == time_idx:
                    continue
                value = padded[col_idx].strip() if col_idx < len(padded) else ""
                if not value or value in {"-", "—", "/", "／"}:
                    continue
                if label in {"标题", "镜头", "分镜", "镜头标题"}:
                    title = value[:80]
                    continue
                canonical = _canonical_field_label(label)
                if canonical is None:
                    continue
                block_lines.append(f"{canonical}：{value}")
            if not block_lines:
                continue
            _append_shot_entry(
                entries,
                index=position,
                start=start,
                end=end,
                title=title or f"镜头{position}",
                block="\n".join(block_lines),
            )
    return entries


def _append_shot_entry(
    entries: list[dict[str, Any]],
    *,
    index: int,
    start: int,
    end: int,
    title: str,
    block: str,
) -> None:
    if end <= start:
        return
    clean_title = (title or f"镜头{index}").strip()[:80] or f"镜头{index}"
    storyline, shot_description, narration = _block_shot_fields(block, title=clean_title)
    if not shot_description:
        shot_description = f"{start}-{end}秒: {clean_title}"
    entries.append(
        {
            "index": index,
            "start_sec": start,
            "end_sec": end,
            "duration_sec": end - start,
            "title": clean_title,
            "storyline": storyline or clean_title,
            "shot_description": shot_description,
            "narration": narration,
            "transition": "按动作完成点衔接下一镜头。",
            # 从画面等字段里的 @yann / 形象参考@安然 抽出名称，供后续绑定 global_assets。
            "asset_requirements": _asset_requirements_from_shot_text(
                f"{shot_description}\n{storyline}"
            ),
        }
    )


_AT_TOKEN_PATTERN = re.compile(
    r"@([^\s@，,。．；;：:！!？?\n\]）)】>\"'“”‘’]+)"
)


def _asset_requirements_from_shot_text(text: str) -> dict[str, list[str]]:
    """从镜头正文抽取 @引用名，先归入 characters，场景包阶段再按 global_assets 跨类匹配。"""

    names: list[str] = []
    for match in _AT_TOKEN_PATTERN.finditer(str(text or "")):
        name = str(match.group(1) or "").strip().strip("*").strip()
        if not name:
            continue
        # 去掉偶发尾巴：@安然。 / @yann的
        name = re.sub(r"[的地得]$", "", name).strip()
        if name and name not in names:
            names.append(name)
    return {"characters": names, "scenes": [], "props": []}


def _pipeline_stage_content(payload: Mapping[str, Any], stage_id: str) -> str:
    pipeline = payload.get("script_pipeline")
    if not isinstance(pipeline, Mapping):
        return ""
    item = pipeline.get(stage_id)
    if not isinstance(item, Mapping):
        return ""
    content = item.get("content")
    return str(content).strip() if isinstance(content, str) else ""


def resolve_shot_source_markdown(
    payload: Mapping[str, Any] | None = None,
    *fallbacks: str,
) -> str:
    """脚本确认后抽镜头的正文来源。

    优先顺序：``script_pipeline.episode`` → ``export`` → ``script.content`` →
    ``plan_markdown`` → 调用方 fallback。

    不用 characters/outline 拼接稿当主来源，避免设定段干扰时间线/镜头列表解析。
    """

    candidates: list[str] = []
    if isinstance(payload, Mapping):
        for stage_id in ("episode", "export"):
            text = _pipeline_stage_content(payload, stage_id)
            if text:
                candidates.append(text)
        script = payload.get("script")
        if isinstance(script, Mapping):
            content = str(script.get("content") or "").strip()
            if content:
                candidates.append(content)
        plan = str(payload.get("plan_markdown") or "").strip()
        if plan:
            candidates.append(plan)
    for item in fallbacks:
        text = str(item or "").strip()
        if text:
            candidates.append(text)

    unique: list[str] = []
    seen: set[str] = set()
    for text in candidates:
        key = text[:200]
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)

    for text in unique:
        if len(_parse_shot_entries_from_text(text)) >= 2:
            return text
    return unique[0] if unique else ""


def looks_like_shot_source_markdown(text: str) -> bool:
    """判断正文是否像可抽镜的剧本/镜头列表（用于确认时回写 episode）。"""

    body = str(text or "").strip()
    if not body:
        return False
    if _parse_shot_entries_from_text(body):
        return True
    if "镜头列表" in body or "分镜提示词" in body:
        return True
    if re.search(r"\|\s*时间\s*\|", body):
        return True
    if re.search(r"\d{1,4}\s*[—\-–~～到至]\s*\d{1,4}\s*秒", body):
        return True
    if re.search(r"镜头\s*\d+", body) and re.search(r"\d{1,2}:\d{2}", body):
        return True
    return False


def _shot_source_structure_score(text: str) -> int:
    """评估可抽镜正文的结构化程度；分数越高越接近拆解后的成稿。"""

    body = str(text or "").strip()
    if not body:
        return 0
    score = 0
    if re.search(r"\|\s*时间\s*\|", body) and "景别" in body and "画面" in body:
        score += 20
    if (
        "旁白/对白" in body
        or "旁白/對白" in body
        or "旁白（对白）" in body
        or "旁白（對白）" in body
    ):
        score += 4
    if "屏幕文案" in body and "行动引导" in body:
        score += 4
    if "景别：" in body and "运镜：" in body and "画面：" in body:
        score += 12
    # 用户粘贴的微电影时间线常见标记：结构化拆解后不应被它盖回。
    raw_markers = ("【剧情/动作】", "【原关键对白】", "【新增对白】", "【产品演示】")
    score -= 6 * sum(1 for marker in raw_markers if marker in body)
    score += min(len(_parse_shot_entries_from_text(body)), 24)
    score += min(len(body) // 400, 8)
    return score


def sync_shot_source_into_pipeline(
    payload: Mapping[str, Any] | None,
    markdown: str,
    *,
    source: str = "user_confirm",
) -> dict[str, Any] | None:
    """把用户确认/编辑的可抽镜正文写回 ``script_pipeline.episode``。

    预览保存默认只改 ``script.content``，而抽镜优先读 episode；不回写则重拆仍吃旧稿。
    若已有拆解后的结构化 episode，禁止被导入成稿原文（【剧情/动作】时间线）盖回。
    """

    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or not looks_like_shot_source_markdown(text):
        return None
    base = dict(payload) if isinstance(payload, Mapping) else {}
    pipeline_raw = base.get("script_pipeline")
    pipeline: dict[str, Any] = dict(pipeline_raw) if isinstance(pipeline_raw, Mapping) else {}
    previous = pipeline.get("episode")
    previous_map = dict(previous) if isinstance(previous, Mapping) else {}
    previous_content = str(previous_map.get("content") or "").strip()
    if previous_content == text:
        return None
    if previous_content and _shot_source_structure_score(previous_content) > _shot_source_structure_score(
        text
    ):
        return None
    pipeline["episode"] = {
        **previous_map,
        "stage": "episode",
        "title": previous_map.get("title") or "剧本正文",
        "content": text,
        "source": source,
    }
    return {"script_pipeline": pipeline}


def prefer_structured_shot_markdown(
    candidate: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """确认/保存时优先保留拆解后的结构化抽镜正文。

    预览「确认」常误传 ``script.content``（导入成稿原文），若直接落库会盖掉
    ``script_pipeline.episode`` 的规范化六列表。
    """

    text = str(candidate or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    existing = resolve_shot_source_markdown(payload).strip()
    if not existing:
        return text
    if not text:
        return existing
    if _shot_source_structure_score(existing) > _shot_source_structure_score(text):
        return existing
    return text


def compute_scene_packages_source_digest(
    payload: Mapping[str, Any] | None = None,
    *fallbacks: str,
) -> str:
    """场景包所依赖的脚本指纹：抽镜正文 + 角色/场景/道具设定。

    用于确认脚本时判断「正文是否相对上次 prepare 有变」；变了必须重拆。
    """

    shot = resolve_shot_source_markdown(payload, *fallbacks).strip()
    settings = ""
    if isinstance(payload, Mapping):
        settings = _pipeline_stage_content(payload, "characters").strip()
    material = f"{shot}\n---\n{settings}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def extract_script_shot_entries(
    plan_markdown: str = "",
    *,
    episode: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """解析脚本中的镜头条目（索引、起止秒、标题候选、画面摘要）。

    传入 ``payload`` / ``episode`` 时，优先从脚本确认后的 ``script_pipeline.episode`` 抽取。
    """

    if payload is not None or (isinstance(episode, str) and episode.strip()):
        text = resolve_shot_source_markdown(payload, *(str(episode or ""), plan_markdown))
        return _parse_shot_entries_from_text(text)
    return _parse_shot_entries_from_text(plan_markdown)


def _parse_shot_entries_from_text(plan_markdown: str) -> list[dict[str, Any]]:
    """仅解析传入 Markdown 正文中的镜头条目。"""

    text = str(plan_markdown or "")
    if not text.strip():
        return []
    entries: list[dict[str, Any]] = []
    for match in _SHOT_LINE_PATTERN.finditer(text):
        start = _timecode_to_sec(match.group("sh"), match.group("sm"), match.group("ss"))
        end = _timecode_to_sec(match.group("eh"), match.group("em"), match.group("es"))
        index = int(match.group("index"))
        block_end = match.end()
        next_shot = _SHOT_LINE_PATTERN.search(text, block_end)
        heading = _SHOT_HEADING_PATTERN.search(text, block_end)
        block = text[block_end : next_shot.start() if next_shot else len(text)]
        title = f"镜头{index}"
        if heading and int(heading.group("index")) == index:
            extra = heading.group("title").strip(" -—–:")
            if extra:
                title = extra[:80]
        _append_shot_entry(
            entries,
            index=index,
            start=start,
            end=end,
            title=title,
            block=block,
        )

    # 无「镜头N + 时码」时，回退成稿时间线「N—M秒｜标题」。
    if not entries:
        for position, match in enumerate(_TIMELINE_SHOT_PATTERN.finditer(text), start=1):
            start = int(match.group("start"))
            end = int(match.group("end"))
            block_end = match.end()
            next_shot = _TIMELINE_SHOT_PATTERN.search(text, block_end)
            block = text[block_end : next_shot.start() if next_shot else len(text)]
            title = (match.group("title") or "").strip()
            _append_shot_entry(
                entries,
                index=position,
                start=start,
                end=end,
                title=title or f"镜头{position}",
                block=block,
            )

    # 再回退：## 镜头1 0:00-0:10 + 同行/下文六字段
    if not entries:
        for match in _SHOT_HEADING_TIME_PATTERN.finditer(text):
            start = _timecode_to_sec(match.group("sh"), match.group("sm"), match.group("ss"))
            end = _timecode_to_sec(match.group("eh"), match.group("em"), match.group("es"))
            index = int(match.group("index"))
            block_end = match.end()
            next_shot = _SHOT_HEADING_TIME_PATTERN.search(text, block_end)
            block = text[block_end : next_shot.start() if next_shot else len(text)]
            title = (match.group("title") or match.group("title_after") or "").strip(" -—–:")
            # 标题位若被时码吃光，允许把同行时码后的残片当地名
            if not title:
                title = f"镜头{index}"
            _append_shot_entry(
                entries,
                index=index,
                start=start,
                end=end,
                title=title,
                block=block,
            )

    # 再回退：Markdown 镜头列表表格（| 时间 | 景别 | 运镜 | 画面 | … |）
    if not entries:
        entries.extend(_parse_markdown_shot_tables(text))

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
    plan_markdown: str = "",
    *,
    target_duration_ms: int | None = None,
    episode: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """把脚本镜头列表转成可规范化的 scene_blueprints，并返回权威总时长毫秒。

    无镜头时间码时返回空列表，调用方回退到机械切分。
    传入 ``payload`` / ``episode`` 时优先读确认后的 episode 正文。
    """

    entries = extract_script_shot_entries(
        plan_markdown,
        episode=episode,
        payload=payload,
    )
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
        # 保留成稿镜块正文，不压成「0-N秒: 摘要」以免丢掉剧情。
        shot_text = str(item.get("shot_description") or "").strip()
        if not shot_text or _is_time_meta_line(shot_text):
            shot_text = f"0-{duration}秒: {storyline}"
        elif not re.match(rf"^\d+\s*[-–—~]\s*{duration}\s*秒", shot_text):
            shot_text = f"0-{duration}秒: {shot_text}"
        raw.append(
            {
                "title": item["title"],
                "storyline": storyline,
                "shot_description": shot_text[:4_000],
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
        merged_shot = f"{left['shot_description']}\n{right['shot_description']}".strip()
        raw[merge_at] = {
            **left,
            "title": f"{left['title']} / {right['title']}",
            "storyline": storyline[:500],
            "shot_description": merged_shot[:4_000],
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
    "compute_scene_packages_source_digest",
    "extract_script_scene_blueprints",
    "extract_script_shot_entries",
    "looks_like_shot_source_markdown",
    "prefer_structured_shot_markdown",
    "resolve_shot_source_markdown",
    "sync_shot_source_into_pipeline",
]
