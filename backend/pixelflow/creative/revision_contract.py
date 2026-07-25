"""Plan 修订时的结构化合同补丁解析。"""

from __future__ import annotations

import copy
import difflib
import re
from typing import Any, Literal

from pixelflow.creative.contract import VideoCreationContract

CreationIntent = Literal["video", "image"]
MANUAL_PLAN_EDIT_MARKER = "【完整手工编辑稿】"
_MANUAL_PLAN_EDIT_DIFF_MARKER = "【本次编辑差异】"
_MANUAL_PLAN_EDIT_FULL_MARKER = "【完整编辑稿】"


def build_manual_plan_revision_feedback(current_plan_markdown: str, edited_plan_markdown: str) -> str:
    """同时携带确定性编辑差异和完整稿，避免 LLM 修改未编辑合同字段。"""

    current_lines = str(current_plan_markdown or "").splitlines()
    edited = str(edited_plan_markdown or "").strip()
    edited_lines = edited.splitlines()
    added_lines = [line[2:] for line in difflib.ndiff(current_lines, edited_lines) if line.startswith("+ ")]
    changed_text = "\n".join(line for line in added_lines if line.strip()).strip() or "仅删除原稿内容，未新增文本"
    return (
        f"{MANUAL_PLAN_EDIT_MARKER}\n"
        f"{_MANUAL_PLAN_EDIT_DIFF_MARKER}\n{changed_text}\n"
        f"{_MANUAL_PLAN_EDIT_FULL_MARKER}\n{edited}"
    )

_VIDEO_MUTABLE_FIELDS = {
    "video_duration_sec",
    "video_ratio",
    "video_model",
    "video_size",
    "video_sound",
    "image_model",
    "video_usage",
    "visual_style",
    "scene_image_ratio",
    "scene_image_size",
}
_IMAGE_MUTABLE_FIELDS = {
    "image_goal",
    "image_type",
    "image_usage",
    "image_style",
    "image_size",
    "image_count",
}
_TOTAL_DURATION_FIELD_PATTERN = re.compile(
    r"(?:视频(?:总)?时长|视频长度|总时长|总长度|时长|片子|成片|影片)",
    flags=re.IGNORECASE,
)
_TOTAL_DURATION_CHANGE_PATTERN = re.compile(
    r"(?:改|修改|调整|调成|延长|缩短|设置|设为|变成|做成|保持|维持)",
    flags=re.IGNORECASE,
)
_RELATIVE_DURATION_INCREASE_PATTERN = re.compile(r"(?:延长|增加|加长|多)", flags=re.IGNORECASE)
_RELATIVE_DURATION_DECREASE_PATTERN = re.compile(r"(?:缩短|减少|减短|少)", flags=re.IGNORECASE)
_RELATIVE_DURATION_TARGET_PATTERN = re.compile(r"(?:延长|增加|加长|缩短|减少|减短)\s*(?:到|至|为)", flags=re.IGNORECASE)
_SCENE_DURATION_FIELD_PATTERN = re.compile(r"(?:分镜|镜头|场景|片段)", flags=re.IGNORECASE)
_DURATION_VALUE_PATTERN = re.compile(
    r"(?:(?P<minutes>\d{1,3})\s*(?:分钟|分)\s*(?:(?P<seconds>\d{1,3})\s*秒)?|(?P<seconds_only>\d{1,3})\s*秒)",
    flags=re.IGNORECASE,
)
_CHINESE_DURATION_VALUE_PATTERN = re.compile(
    r"(?:(?P<minutes>[零〇一二两三四五六七八九十百]{1,5})\s*(?:分钟|分)"
    r"\s*(?:(?P<seconds>[零〇一二两三四五六七八九十百]{1,5})\s*秒)?|"
    r"(?P<seconds_only>[零〇一二两三四五六七八九十百]{1,5})\s*秒)",
    flags=re.IGNORECASE,
)
_RATIO_PATTERN = re.compile(r"(?<!\d)(1\s*:\s*1|9\s*:\s*16|16\s*:\s*9)(?!\d)")
_IMAGE_COUNT_PATTERNS = (
    re.compile(
        r"(?:生成|输出|产出|制作|创建|改成|调整为|修改为|数量(?:改为|调整为|设为|是|为)?|张数(?:改为|调整为|设为|是|为)?)"
        r"\s*[:：]?\s*(?:共|一共)?\s*(\d{1,2})\s*张(?:图片|图像|成图|结果|组图|海报|封面|主图)?"
    ),
    re.compile(r"(?:图片|图像|成图|结果|组图|海报|封面|主图)(?:数量|张数)?\s*(?:改为|调整为|设为|是|为)?\s*(\d{1,2})\s*张"),
)
_CHINESE_IMAGE_COUNT_PATTERN = re.compile(
    r"(?:生成|输出|产出|制作|创建|改成|调整为|修改为|做成|多出|再出|增加|添加)"
    r"\s*(?:共|一共)?\s*([零〇一二两三四五六七八九十]{1,3})\s*(?:张|版)",
    flags=re.IGNORECASE,
)
_RELATIVE_IMAGE_COUNT_PATTERN = re.compile(r"(?:多出|增加|添加|再出|再生成|再做)", flags=re.IGNORECASE)
_STYLE_FIELD_PATTERN = re.compile(
    r"(?:视觉)?(?:风格|调性|色调)|(?:[\u4e00-\u9fff]{1,8}风)(?=[，,。；;\s]|$)",
    flags=re.IGNORECASE,
)
_STYLE_PRESERVE_PATTERNS = (
    re.compile(r"(?:视觉)?(?:风格|调性|色调)\s*(?:不要|无需|不需要)\s*(?:改|修改|调整|变化)", flags=re.IGNORECASE),
    re.compile(r"(?:不要|无需|不需要)\s*(?:改|修改|调整|变化)[^，,。；;\n]{0,8}(?:视觉)?(?:风格|调性|色调)", flags=re.IGNORECASE),
    re.compile(r"(?:保持|维持)[^，,。；;\n]{0,8}(?:视觉)?(?:风格|调性|色调)[^，,。；;\n]{0,4}(?:不变|原样|原有|照旧)?", flags=re.IGNORECASE),
    re.compile(r"(?:视觉)?(?:风格|调性|色调)\s*(?:保持不变|不变|原样|照旧)", flags=re.IGNORECASE),
)


def merge_revision_contract(
    intent: CreationIntent,
    current_contract: dict[str, Any] | None,
    revision_feedback: str,
    llm_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合并 LLM 补丁和用户明确参数，用户明确值拥有最高优先级。"""

    merged = copy.deepcopy(current_contract or {})
    allowed_fields = mentioned_revision_fields(intent, revision_feedback)
    explicit_patch = extract_explicit_revision_patch(intent, revision_feedback)
    if intent == "video":
        _reject_model_change_without_capabilities(merged, allowed_fields, explicit_patch, llm_patch or {})
    for field_name, value in (llm_patch or {}).items():
        if field_name not in allowed_fields or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        merged[field_name] = _normalize_contract_value(field_name, value)
    relative_duration = _extract_relative_duration_sec(revision_feedback)
    current_duration = current_contract.get("video_duration_sec") if current_contract else None
    if relative_duration is not None and isinstance(current_duration, int) and not isinstance(current_duration, bool):
        explicit_patch["video_duration_sec"] = current_duration + relative_duration
    relative_image_count = _extract_relative_image_count(revision_feedback)
    current_image_count = current_contract.get("image_count") if current_contract else None
    if relative_image_count is not None and isinstance(current_image_count, int) and not isinstance(current_image_count, bool):
        explicit_patch["image_count"] = current_image_count + relative_image_count
    # 手工编辑器把执行合同展示为只读；即使模型误返回数量补丁，也不能改动未明确编辑的生成数量。
    if MANUAL_PLAN_EDIT_MARKER in str(revision_feedback or "") and "image_count" not in explicit_patch:
        if current_image_count is not None:
            merged["image_count"] = copy.deepcopy(current_image_count)
    merged.update(explicit_patch)
    return merged


def mentioned_revision_fields(intent: CreationIntent, revision_feedback: str) -> set[str]:
    """识别本轮用户真正提及的合同字段，防止 LLM 顺手修改无关参数。"""

    raw_text = str(revision_feedback or "").strip()
    is_manual_edit = MANUAL_PLAN_EDIT_MARKER in raw_text
    text = _manual_edit_diff_text(raw_text).lower()
    if intent == "video":
        fields: set[str] = set()
        if _total_duration_is_mutable_in_feedback(text) or (
            is_manual_edit and _TOTAL_DURATION_FIELD_PATTERN.search(text) and _extract_duration_sec(text) is not None
        ):
            fields.add("video_duration_sec")
        if re.search(r"(?:视频)?(?:画幅|比例|横屏|竖屏|1\s*:\s*1|9\s*:\s*16|16\s*:\s*9)", text):
            fields.add("video_ratio")
        if re.search(r"(?:视频)?(?:清晰度|分辨率|720p|1080p|2k|3k|4k)", text):
            fields.add("video_size")
        if re.search(r"(?:声音|音频|有声|无声|静音)", text):
            fields.add("video_sound")
        if re.search(r"(?:视频模型|seedance)", text):
            fields.add("video_model")
        if re.search(r"(?:图片|图像)模型|gpt-image|seeddream|nanobanana", text):
            fields.add("image_model")
        if re.search(r"(?:视频)?(?:用途|投放|发布渠道)", text):
            fields.add("video_usage")
        if _field_is_mutable_in_feedback(text, _STYLE_FIELD_PATTERN, _STYLE_PRESERVE_PATTERNS):
            fields.add("visual_style")
        if re.search(r"(?:场景|角色|道具)(?:图|图片).*(?:比例|画幅)", text):
            fields.add("scene_image_ratio")
        if re.search(r"(?:场景|角色|道具)(?:图|图片).*(?:清晰度|分辨率)", text):
            fields.add("scene_image_size")
        return fields

    fields = set()
    if re.search(r"(?:图片)?(?:目标|主题|主体|内容)", text):
        fields.add("image_goal")
    if re.search(r"(?:图片)?类型", text):
        fields.add("image_type")
    if re.search(r"(?:图片)?(?:用途|投放|发布渠道)", text):
        fields.add("image_usage")
    if _field_is_mutable_in_feedback(text, _STYLE_FIELD_PATTERN, _STYLE_PRESERVE_PATTERNS):
        fields.add("image_style")
    if re.search(r"(?:图片)?(?:尺寸|比例|画幅|横图|竖图|1\s*:\s*1|9\s*:\s*16|16\s*:\s*9)", text):
        fields.add("image_size")
    if _extract_image_count(text) is not None:
        fields.add("image_count")
    return fields


def validate_revision_contract(intent: CreationIntent, contract: dict[str, Any]) -> dict[str, Any]:
    """在创建新 Plan 版本前校验候选合同。"""

    if intent == "video":
        return VideoCreationContract.model_validate(contract).model_dump(exclude_none=True)

    intent_value = contract.get("intent", "image")
    if not isinstance(intent_value, str) or intent_value.strip() != "image":
        raise ValueError("图片创作合同 intent 必须是字符串 image")
    normalized = copy.deepcopy(contract)
    for field_name in ("image_goal", "image_type", "image_usage", "image_style", "image_size"):
        value = contract.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"图片创作合同 {field_name} 必须是非空字符串")
        normalized[field_name] = value.strip()
    count = contract.get("image_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError("图片生成数量必须是 1-10 的整数")
    size = normalized["image_size"]
    if size != "自动适配" and not _extract_ratio(size):
        raise ValueError("图片尺寸必须是 1:1、9:16、16:9 或自动适配")
    normalized["intent"] = "image"
    return normalized


def extract_explicit_revision_patch(intent: CreationIntent, revision_feedback: str) -> dict[str, Any]:
    """提取用户在修改意见中明确给出的硬参数。"""

    text = _manual_edit_diff_text(str(revision_feedback or "").strip())
    patch: dict[str, Any] = {}
    mentioned_fields = mentioned_revision_fields(intent, text)
    if intent == "video":
        if "video_duration_sec" in mentioned_fields:
            duration = _extract_duration_sec(text)
            if duration is not None:
                patch["video_duration_sec"] = duration
        if "video_ratio" in mentioned_fields:
            ratio = _extract_ratio(text)
            if ratio:
                patch["video_ratio"] = ratio
        if "video_size" in mentioned_fields:
            size = _extract_quality(text)
            if size:
                patch["video_size"] = size
        if "video_sound" in mentioned_fields:
            if re.search(r"(?:关闭声音|不要声音|无声|静音)", text, flags=re.IGNORECASE):
                patch["video_sound"] = "off"
            elif re.search(r"(?:开启声音|保留声音|有声)", text, flags=re.IGNORECASE):
                patch["video_sound"] = "on"
        if "video_model" in mentioned_fields:
            video_model = _extract_model(text, "seedance")
            if video_model:
                patch["video_model"] = video_model
        if "image_model" in mentioned_fields:
            image_model = _extract_model(text, "image")
            if image_model:
                patch["image_model"] = image_model
        return patch

    if "image_size" in mentioned_fields:
        ratio = _extract_ratio(text)
        if ratio:
            patch["image_size"] = ratio
    if "image_count" in mentioned_fields:
        image_count = _extract_image_count(text)
        if image_count is not None:
            patch["image_count"] = image_count
    return patch


def contract_form_values(contract: dict[str, Any]) -> dict[str, Any]:
    """把最终合同中可执行字段覆盖回 LLM 和下游使用的表单视图。"""

    fields = _VIDEO_MUTABLE_FIELDS | _IMAGE_MUTABLE_FIELDS
    return {field_name: copy.deepcopy(value) for field_name, value in contract.items() if field_name in fields and value is not None}


def _extract_duration_sec(text: str) -> int | None:
    matches: list[tuple[int, int]] = []
    for match in _DURATION_VALUE_PATTERN.finditer(text):
        clause = _clause_containing(text, match.start(), match.end())
        if _SCENE_DURATION_FIELD_PATTERN.search(clause) and not _TOTAL_DURATION_FIELD_PATTERN.search(clause):
            continue
        minutes = int(match.group("minutes") or 0)
        seconds = int(match.group("seconds") or match.group("seconds_only") or 0)
        matches.append((match.start(), minutes * 60 + seconds))
    for match in _CHINESE_DURATION_VALUE_PATTERN.finditer(text):
        clause = _clause_containing(text, match.start(), match.end())
        if _SCENE_DURATION_FIELD_PATTERN.search(clause) and not _TOTAL_DURATION_FIELD_PATTERN.search(clause):
            continue
        minutes = _chinese_number_to_int(match.group("minutes") or "")
        seconds = _chinese_number_to_int(match.group("seconds") or match.group("seconds_only") or "")
        matches.append((match.start(), minutes * 60 + seconds))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _extract_relative_duration_sec(text: str) -> int | None:
    """提取“总时长延长/缩短 N 秒”的增量，避免误当作绝对总时长。"""

    for clause in re.split(r"[，,。；;\n]+", text):
        if _RELATIVE_DURATION_TARGET_PATTERN.search(clause):
            continue
        if _SCENE_DURATION_FIELD_PATTERN.search(clause) and not re.search(r"(?:总时长|总长度)", clause):
            continue
        duration = _extract_duration_sec(clause)
        if duration is None:
            continue
        if _RELATIVE_DURATION_DECREASE_PATTERN.search(clause):
            return -duration
        if _RELATIVE_DURATION_INCREASE_PATTERN.search(clause):
            return duration
    return None


def _reject_model_change_without_capabilities(
    current_contract: dict[str, Any],
    allowed_fields: set[str],
    explicit_patch: dict[str, Any],
    llm_patch: dict[str, Any],
) -> None:
    """Plan 修订没有实时模型能力快照，禁止把旧能力误配给新模型。"""

    for field_name in ("video_model", "image_model"):
        if field_name not in allowed_fields:
            continue
        candidate = explicit_patch.get(field_name, llm_patch.get(field_name))
        current = current_contract.get(field_name)
        if candidate and str(candidate).strip().lower() != str(current or "").strip().lower():
            raise ValueError("修改模型需要重新确认模型能力，请返回需求表单重新选择模型")


def _extract_ratio(text: str) -> str:
    matches = list(_RATIO_PATTERN.finditer(text))
    return re.sub(r"\s+", "", matches[-1].group(1)) if matches else ""


def _extract_quality(text: str) -> str:
    matches = list(re.finditer(r"(?<![\w])(?:720p|1080p|2k|3k|4k)(?![\w])", text, flags=re.IGNORECASE))
    if not matches:
        return ""
    value = matches[-1].group(0)
    return value.lower() if value.lower().endswith("p") else value.upper()


def _extract_model(text: str, category: str) -> str:
    if category == "seedance":
        matches = list(re.finditer(r"seedance[A-Za-z0-9_.-]+", text, flags=re.IGNORECASE))
        return matches[-1].group(0).lower() if matches else ""
    matches = list(
        re.finditer(
            r"(?:gpt-image-[A-Za-z0-9_.-]+|seeddream-[A-Za-z0-9_.-]+|nanobanana-[A-Za-z0-9_.-]+)",
            text,
            flags=re.IGNORECASE,
        )
    )
    return matches[-1].group(0).lower() if matches else ""


def _extract_image_count(text: str) -> int | None:
    matches: list[tuple[int, int]] = []
    for pattern in _IMAGE_COUNT_PATTERNS:
        matches.extend((match.start(1), int(match.group(1))) for match in pattern.finditer(text))
    matches.extend(
        (match.start(1), _chinese_number_to_int(match.group(1)))
        for match in _CHINESE_IMAGE_COUNT_PATTERN.finditer(text)
    )
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _extract_relative_image_count(text: str) -> int | None:
    if not _RELATIVE_IMAGE_COUNT_PATTERN.search(text):
        return None
    return _extract_image_count(text)


def _clause_containing(text: str, start: int, end: int) -> str:
    delimiters = "，,。；;\n"
    clause_start = max((text.rfind(delimiter, 0, start) for delimiter in delimiters), default=-1) + 1
    clause_ends = [position for delimiter in delimiters if (position := text.find(delimiter, end)) >= 0]
    clause_end = min(clause_ends) if clause_ends else len(text)
    return text[clause_start:clause_end]


def _manual_edit_diff_text(text: str) -> str:
    """手工编辑只用新增/替换文本决定合同字段白名单，完整稿只供 LLM 重写。"""

    if MANUAL_PLAN_EDIT_MARKER not in text:
        return text
    if _MANUAL_PLAN_EDIT_DIFF_MARKER in text and _MANUAL_PLAN_EDIT_FULL_MARKER in text:
        return text.split(_MANUAL_PLAN_EDIT_DIFF_MARKER, 1)[1].split(_MANUAL_PLAN_EDIT_FULL_MARKER, 1)[0].strip()
    return text.split(MANUAL_PLAN_EDIT_MARKER, 1)[1].strip()


def _field_is_mutable_in_feedback(
    text: str,
    field_pattern: re.Pattern[str],
    preserve_patterns: tuple[re.Pattern[str], ...],
) -> bool:
    clauses = re.split(r"[，,。；;\n]+", text)
    mentioned_clauses = [clause for clause in clauses if field_pattern.search(clause)]
    if not mentioned_clauses:
        return False
    return any(not any(pattern.search(clause) for pattern in preserve_patterns) for clause in mentioned_clauses)


def _total_duration_is_mutable_in_feedback(text: str) -> bool:
    clauses = re.split(r"[，,。；;\n]+", text)
    for clause in clauses:
        if not _TOTAL_DURATION_CHANGE_PATTERN.search(clause):
            continue
        if _SCENE_DURATION_FIELD_PATTERN.search(clause) and not re.search(r"(?:总时长|总长度)", clause):
            continue
        if _TOTAL_DURATION_FIELD_PATTERN.search(clause) or _extract_duration_sec(clause) is not None:
            return True
    return False


def _chinese_number_to_int(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if all(character in digits for character in text):
        return int("".join(str(digits[character]) for character in text))
    total = 0
    current = 0
    for character in text:
        if character in digits:
            current = digits[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
    return total + current


def _normalize_contract_value(field_name: str, value: Any) -> Any:
    if field_name in {"video_duration_sec", "image_count"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if isinstance(value, str):
        return value.strip()
    return copy.deepcopy(value)
