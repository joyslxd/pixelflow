"""Best-effort structured preference extraction for P0.

This deliberately stays conservative and deterministic. Rich semantic memory
and LLM extraction are reserved for the P1 mem0/Qdrant layer.
"""

from __future__ import annotations

import re
from typing import Any

_NEGATIVE = re.compile(r"(不要|避免|禁止|别|不想|不能|不要再)([^。；;!！?？\n]{1,80})")
_STYLE = {
    "轻音乐": ("bgm_vibe", "轻音乐"),
    "快节奏": ("pace", "快节奏"),
    "慢节奏": ("pace", "慢节奏"),
    "高级感": ("overall_style", "高级感"),
    "真实感": ("overall_style", "真实感"),
    "口播": ("format", "口播"),
    "无口播": ("format", "无口播"),
}
_DEFAULT_PLATFORM = {"抖音": "douyin", "douyin": "douyin", "TikTok": "tiktok", "小红书": "xiaohongshu", "快手": "kuaishou", "淘宝": "taobao"}
_RATIO = re.compile(r"(9:16|16:9|1:1)")
_DURATION = re.compile(r"(\d{1,3})\s*(秒|s|S)")


def extract_structured_preferences(feedback: str, *, brief_patch: dict[str, Any] | None = None) -> dict[str, Any]:
    text = feedback.strip()
    patch: dict[str, Any] = {"style_preferences": {}, "negative_rules": [], "defaults": {}}
    if not text and not brief_patch:
        return patch

    for match in _NEGATIVE.finditer(text):
        rule = f"不要{match.group(2).strip()}"
        patch["negative_rules"].append(rule)

    if any(token in text for token in ("以后", "默认", "一直", "都这样", "偏好", "喜欢")):
        for token, (key, value) in _STYLE.items():
            if token in text:
                patch["style_preferences"][key] = value
        for token, platform in _DEFAULT_PLATFORM.items():
            if token in text:
                patch["defaults"]["platform"] = platform
        if ratio := _RATIO.search(text):
            patch["defaults"]["ratio"] = ratio.group(1)
        if duration := _DURATION.search(text):
            patch["defaults"]["duration_sec"] = int(duration.group(1))

    brief_patch = brief_patch or {}
    if brief_patch.get("platform"):
        patch["defaults"]["platform"] = brief_patch["platform"]
    if brief_patch.get("ratio"):
        patch["defaults"]["ratio"] = brief_patch["ratio"]
    if brief_patch.get("duration_sec"):
        patch["defaults"]["duration_sec"] = brief_patch["duration_sec"]

    return patch
