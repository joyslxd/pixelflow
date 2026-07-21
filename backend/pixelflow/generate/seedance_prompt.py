"""Runtime adapter for the vendored Seedance prompt-authoring Skill."""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from pixelflow.creative.scene_blueprint import MAX_SCENE_ASSET_REFERENCES

SEEDANCE_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "public"
    / "borgrise-creative-assistant-v2"
    / "skills"
    / "seedance-prompt"
    / "SKILL.md"
)
MAX_IMAGE_REFERENCES = MAX_SCENE_ASSET_REFERENCES


@lru_cache(maxsize=1)
def load_seedance_guidance() -> str:
    """Load the concise Seedance rules needed by the scene-package LLM."""
    source = SEEDANCE_SKILL_PATH.read_text(encoding="utf-8")
    sections = [
        _markdown_section(source, "适用范围与模型边界", level=2),
        _markdown_section(source, "PixelFlow 分镜执行合同", level=2),
        _markdown_section(source, "参考素材与一致性", level=2),
        _markdown_section(source, "声音、对白与字幕", level=2),
        _markdown_section(source, "镜头语言与真实感", level=2),
        _markdown_section(source, "电商与 UGC 场景", level=2),
        _markdown_section(source, "质量检查", level=2),
    ]
    guidance = "\n\n".join(section.strip() for section in sections if section.strip())
    if not guidance:
        raise ValueError(f"Seedance guidance sections are missing from {SEEDANCE_SKILL_PATH}")
    return guidance


def build_seedance_shot_prompt(
    *,
    scene_index: int,
    start_second: int,
    end_second: int,
    plan_markdown: str,
    storyline: str,
    narration: str,
    visual_style: str,
    available_asset_ids: Sequence[str],
    video_ratio: str,
    video_model: str,
    include_guidance: bool = True,
    include_plan: bool = True,
) -> str:
    """Build one scene's strict Seedance shot-description instruction."""
    if (
        isinstance(start_second, bool)
        or not isinstance(start_second, int)
        or isinstance(end_second, bool)
        or not isinstance(end_second, int)
    ):
        raise ValueError("Seedance scene range must use integer seconds")
    duration = end_second - start_second
    if scene_index < 1:
        raise ValueError("scene_index must be positive")
    if start_second < 0 or duration < 4 or duration > 15:
        raise ValueError("Seedance scene range must be 4-15 integer seconds")
    normalized_video_model = str(video_model or "").strip()
    if not normalized_video_model:
        raise ValueError("video_model is required for Seedance shot prompts")

    asset_ids = _normalize_asset_ids(available_asset_ids)
    if len(asset_ids) > MAX_IMAGE_REFERENCES:
        raise ValueError(f"Seedance supports at most {MAX_IMAGE_REFERENCES} image references per scene")
    references = "、".join(f"@{asset_id}" for asset_id in asset_ids) or "无图片参考"
    guidance = f"Seedance 系列 Skill 规则：\n{load_seedance_guidance()}\n\n" if include_guidance else ""
    plan_context = f"\n- 必须严格执行的 plan.md：\n{str(plan_markdown or '').strip()[:6000]}" if include_plan else ""
    return (
        f"{guidance}"
        f"分镜 {scene_index} 的执行合同：\n"
        f"- 精确时间范围：{start_second}-{end_second}秒（时长 {duration} 秒）\n"
        f"- 视频画幅：{video_ratio}\n"
        f"- 当前视频模型：{normalized_video_model}\n"
        f"- 视觉风格：{visual_style}\n"
        f"- 故事线：{storyline}\n"
        f"- 旁白：{narration or '本分镜无旁白'}\n"
        f"- 可引用素材：{references}\n"
        f"- 素材规则：只允许使用上述 @asset_id，不要使用未声明素材；每个分镜最多 {MAX_IMAGE_REFERENCES} 张图片参考。\n"
        "- 镜头描述由一个或多个中文段落组成，每个段落必须以当前分镜内部的整数秒范围开头；"
        "段落数量由内容变化决定，动作阶段、景别、运镜、说话者、声音或叙事重点变化时必须换行拆段。\n"
        "- 多段必须从 0 秒开始连续覆盖本镜时长，每个时间段独占一段；每段显式使用地点、主体、动作、景别、"
        "运镜、光影、声音、收束八个标签，并明确每个 @素材的用途。\n"
        "- 不得使用 ms、毫秒或带小数的时间码。"
        f"{plan_context}"
    )


def _markdown_section(source: str, heading: str, *, level: int) -> str:
    marker = "#" * level
    match = re.search(
        rf"(?ms)^{re.escape(marker)}\s+{re.escape(heading)}\s*$.*?(?=^{'#' * level}\s+|\Z)",
        source,
    )
    return match.group(0).strip() if match else ""


def _normalize_asset_ids(values: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        asset_id = str(value or "").strip().lstrip("@").strip()
        if asset_id and asset_id not in normalized:
            normalized.append(asset_id)
    return normalized
