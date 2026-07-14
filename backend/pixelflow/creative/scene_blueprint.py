"""视频 Plan 的结构化分镜蓝图与精确时长校验。"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from pixelflow.creative.duration import MAX_SCENE_DURATION_SEC, MIN_SCENE_DURATION_SEC

_SECOND_RANGE_PATTERN = re.compile(r"\d+\s*[-~—至]\s*\d+\s*秒")
_CAPTURED_SECOND_RANGE_PATTERN = re.compile(r"(?P<start>\d+)\s*(?:[-~—至])\s*(?P<end>\d+)\s*秒")
_MILLISECOND_PATTERN = re.compile(r"(?:ms|毫秒|\d{1,2}:\d{2}\.\d+)", flags=re.IGNORECASE)
_INTERNAL_CONTEXT_MARKERS = (
    "长期记忆约束",
    "PowerMem",
    "语义记忆上下文",
    "stage=",
    "用户创作上下文",
    "采集 Agent 完成意图识别",
    "Skill 经验",
    "Agent 阶段日志",
)
_ROLE_ALIASES = {
    "opening": "opening",
    "hook": "opening",
    "setup": "opening",
    "开场": "opening",
    "钩子": "opening",
    "development": "development",
    "develop": "development",
    "发展": "development",
    "展开": "development",
    "climax": "climax",
    "proof": "climax",
    "demo": "climax",
    "高潮": "climax",
    "证明": "climax",
    "conclusion": "conclusion",
    "cta": "conclusion",
    "ending": "conclusion",
    "结尾": "conclusion",
    "收束": "conclusion",
}


def normalize_scene_blueprints(raw_blueprints: Any, *, total_duration_sec: int) -> list[dict[str, Any]]:
    """规范化 LLM 分镜，并拒绝任何违反生产合同的时间线。"""
    _validate_total_duration(total_duration_sec)
    if not isinstance(raw_blueprints, list) or not raw_blueprints:
        raise ValueError("Plan LLM 未返回 scene_blueprints")

    minimum_count = math.ceil(total_duration_sec / MAX_SCENE_DURATION_SEC)
    maximum_count = total_duration_sec // MIN_SCENE_DURATION_SEC
    if not minimum_count <= len(raw_blueprints) <= maximum_count:
        raise ValueError(f"分镜数量 {len(raw_blueprints)} 不适用于 {total_duration_sec} 秒视频，合法范围为 {minimum_count}-{maximum_count}")

    normalized: list[dict[str, Any]] = []
    cursor = 0
    for position, raw in enumerate(raw_blueprints, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"分镜 {position} 必须是对象")
        duration = _strict_int(raw.get("duration_sec"), field_name=f"分镜 {position} duration_sec")
        if not MIN_SCENE_DURATION_SEC <= duration <= MAX_SCENE_DURATION_SEC:
            raise ValueError(f"分镜 {position} 时长必须是 4-15 秒整数")
        start_sec = _strict_int(raw.get("start_sec"), field_name=f"分镜 {position} start_sec")
        end_sec = _strict_int(raw.get("end_sec"), field_name=f"分镜 {position} end_sec")
        if start_sec != cursor or end_sec != cursor + duration:
            raise ValueError(f"分镜 {position} 时间线不连续，应为 {cursor}-{cursor + duration} 秒")

        role = _normalize_role(raw.get("structure_role"), position)
        shot_description = _shot_description_text(raw.get("shot_description"), position)
        normalized.append(
            {
                # 分镜 ID 是前端更新和后端重试的主键，不能信任 LLM 返回的重复值。
                "scene_id": f"scene-{position}",
                "scene_index": position,
                "title": _public_required_text(raw.get("title"), f"分镜 {position} title"),
                "structure_role": role,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "duration_sec": duration,
                "storyline": _public_required_text(raw.get("storyline"), f"分镜 {position} storyline"),
                "shot_description": shot_description,
                "narration": _public_optional_text(raw.get("narration")) or "本分镜无旁白",
                "transition": _transition_text(raw.get("transition"), is_last=position == len(raw_blueprints)),
                "asset_requirements": _normalize_asset_requirements(raw.get("asset_requirements")),
            }
        )
        cursor = end_sec

    if cursor != total_duration_sec:
        raise ValueError(f"分镜总时长 {cursor} 秒与目标 {total_duration_sec} 秒不一致")
    _validate_story_structure(normalized)
    return normalized


def repair_scene_blueprints_schedule(raw_blueprints: Any, *, total_duration_sec: int) -> list[dict[str, Any]]:
    """仅修复 LLM 的非法时间线，保留已经生成的分镜语义内容。"""
    _validate_total_duration(total_duration_sec)
    if not isinstance(raw_blueprints, list) or not raw_blueprints:
        raise ValueError("Plan LLM 未返回可修复的 scene_blueprints")

    minimum_count = math.ceil(total_duration_sec / MAX_SCENE_DURATION_SEC)
    maximum_count = total_duration_sec // MIN_SCENE_DURATION_SEC
    if not minimum_count <= len(raw_blueprints) <= maximum_count:
        raise ValueError(f"分镜数量 {len(raw_blueprints)} 无法在 4-15 秒约束内修复为 {total_duration_sec} 秒")
    if not all(isinstance(item, dict) for item in raw_blueprints):
        raise ValueError("Plan LLM 分镜必须是对象")

    source_durations = [_source_scene_duration(item) for item in raw_blueprints]
    repaired_durations = _weighted_durations(total_duration_sec, [float(value) for value in source_durations])
    repaired: list[dict[str, Any]] = []
    cursor = 0
    for position, (raw, source_duration, duration) in enumerate(
        zip(raw_blueprints, source_durations, repaired_durations, strict=True),
        start=1,
    ):
        item = copy.deepcopy(raw)
        item["scene_index"] = position
        item["start_sec"] = cursor
        item["end_sec"] = cursor + duration
        item["duration_sec"] = duration
        item["shot_description"] = _rescale_shot_description(
            item.get("shot_description"),
            source_duration=source_duration,
            target_duration=duration,
        )
        repaired.append(item)
        cursor += duration
    return normalize_scene_blueprints(repaired, total_duration_sec=total_duration_sec)


def fallback_scene_blueprints(
    *,
    total_duration_sec: int,
    product_name: str,
    direction_description: str,
    visual_style: str,
    conversion_goal: str,
) -> list[dict[str, Any]]:
    """在 LLM 蓝图不可用时，按叙事职能加权生成非机械等分蓝图。"""
    _validate_total_duration(total_duration_sec)
    scene_count = _fallback_scene_count(total_duration_sec)
    roles = _story_roles(scene_count)
    durations = _weighted_durations(total_duration_sec, [_role_weight(role, index, scene_count) for index, role in enumerate(roles)])
    product = _text(product_name) or "产品"
    direction = _text(direction_description) or f"围绕 {product} 完成卖点证明"
    style = _text(visual_style) or "真实广告风格"
    goal = _text(conversion_goal) or "完成转化"

    blueprints: list[dict[str, Any]] = []
    cursor = 0
    for index, (role, duration) in enumerate(zip(roles, durations, strict=True), start=1):
        title, storyline, shot_action, narration, transition = _fallback_content(
            role=role,
            product=product,
            direction=direction,
            visual_style=style,
            goal=goal,
        )
        blueprints.append(
            {
                "scene_id": f"scene-{index}",
                "scene_index": index,
                "title": title,
                "structure_role": role,
                "start_sec": cursor,
                "end_sec": cursor + duration,
                "duration_sec": duration,
                "storyline": storyline,
                "shot_description": f"0-{duration}秒: {shot_action}",
                "narration": narration,
                "transition": transition,
                "asset_requirements": {
                    "characters": ["目标用户"] if role in {"opening", "development"} else [],
                    "scenes": ["真实使用场景"],
                    "props": [product],
                },
            }
        )
        cursor += duration
    return normalize_scene_blueprints(blueprints, total_duration_sec=total_duration_sec)


def scene_blueprint_durations(blueprints: list[dict[str, Any]]) -> list[int]:
    """提取已经校验过的分镜时长数组。"""
    return [int(item["duration_sec"]) for item in blueprints]


def render_scene_blueprints_markdown(blueprints: list[dict[str, Any]]) -> str:
    """把权威蓝图渲染为可审核的 plan.md 章节。"""
    sections: list[str] = ["### 权威分镜创作蓝图"]
    for item in blueprints:
        assets = item.get("asset_requirements") if isinstance(item.get("asset_requirements"), dict) else {}
        asset_text = "；".join(f"{label}：{'、'.join(str(value) for value in assets.get(key, []) if str(value).strip()) or '无'}" for key, label in (("characters", "人物"), ("scenes", "场景"), ("props", "道具/商品")))
        sections.append(
            f"#### 分镜{item['scene_index']}：{item['title']}（{item['structure_role']}）\n\n"
            f"- 全局时间：{item['start_sec']}-{item['end_sec']}秒；时长：{item['duration_sec']}秒\n"
            f"- 故事线：{item['storyline']}\n"
            f"- 镜头描述：{item['shot_description']}\n"
            f"- 旁白：{item['narration']}\n"
            f"- 转场：{item['transition']}\n"
            f"- 资产需求：{asset_text}"
        )
    return "\n\n".join(sections)


def _fallback_scene_count(total_duration_sec: int) -> int:
    minimum_count = math.ceil(total_duration_sec / MAX_SCENE_DURATION_SEC)
    maximum_count = total_duration_sec // MIN_SCENE_DURATION_SEC
    preferred_count = max(1, round(total_duration_sec / 10))
    return min(max(preferred_count, minimum_count), maximum_count)


def _story_roles(scene_count: int) -> list[str]:
    if scene_count == 1:
        return ["opening"]
    if scene_count == 2:
        return ["opening", "conclusion"]
    if scene_count == 3:
        return ["opening", "climax", "conclusion"]
    return ["opening", *(["development"] * (scene_count - 3)), "climax", "conclusion"]


def _role_weight(role: str, index: int, count: int) -> float:
    if role == "opening":
        return 0.75
    if role == "climax":
        return 1.35
    if role == "conclusion":
        return 0.9
    return 0.95 if index % 2 else 1.05


def _weighted_durations(total: int, weights: list[float]) -> list[int]:
    raw = [total * weight / sum(weights) for weight in weights]
    durations = [min(MAX_SCENE_DURATION_SEC, max(MIN_SCENE_DURATION_SEC, math.floor(value))) for value in raw]
    delta = total - sum(durations)
    while delta != 0:
        if delta > 0:
            candidates = [index for index, value in enumerate(durations) if value < MAX_SCENE_DURATION_SEC]
            if not candidates:
                raise ValueError(f"无法为 {total} 秒视频分配合法分镜时长")
            index = max(candidates, key=lambda item: (raw[item] - durations[item], weights[item], -item))
            durations[index] += 1
            delta -= 1
        else:
            candidates = [index for index, value in enumerate(durations) if value > MIN_SCENE_DURATION_SEC]
            if not candidates:
                raise ValueError(f"无法为 {total} 秒视频分配合法分镜时长")
            index = max(candidates, key=lambda item: (durations[item] - raw[item], -weights[item], item))
            durations[index] -= 1
            delta += 1
    return durations


def _fallback_content(*, role: str, product: str, direction: str, visual_style: str, goal: str) -> tuple[str, str, str, str, str]:
    if role == "opening":
        return (
            "需求冲突钩子",
            f"用目标用户的高频问题建立观看理由，并让 {product} 的价值有明确介入空间。",
            f"近景从真实问题动作开始，快速推近关键细节；保持 {visual_style}，在结尾露出 {product} 形成悬念。",
            f"遇到这个问题，先别急着妥协。{product} 即将给出答案。",
            "沿问题动作或视线方向切入解决过程。",
        )
    if role == "climax":
        return (
            "核心卖点证明",
            f"围绕“{direction}”完成可观察的使用动作、证据和结果对比。",
            f"中景交代使用关系，镜头环绕 {product} 后切入卖点特写，以 {visual_style} 清楚展示前后变化。",
            f"真正的差别，要看得见。{product} 用结果证明价值。",
            "由结果细节匹配剪辑到最终使用状态。",
        )
    if role == "conclusion":
        return (
            "结果与转化收束",
            f"回到完整使用结果，强化 {product} 记忆并引导用户{goal}。",
            f"跟拍进入完成状态，随后稳定定格 {product} 完整外观和关键结果，画面干净并保持 {visual_style}。",
            f"让每次使用都更确定。现在就{goal}。",
            "产品定格结束。",
        )
    return (
        "使用过程展开",
        f"承接上一镜头，围绕“{direction}”推进一个独立的使用步骤。",
        f"中景呈现人物与 {product} 的动作关系，再用近景证明当前信息点；运镜有起止，保持 {visual_style}。",
        f"一步一步，{product} 把复杂问题变得简单。",
        "以动作完成点或同方向运动衔接下一步骤。",
    )


def _shot_description_text(value: Any, position: int) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    text = _public_required_text(value, f"分镜 {position} shot_description")
    if _MILLISECOND_PATTERN.search(text):
        raise ValueError(f"分镜 {position} 镜头描述不能使用毫秒时间码")
    if not _SECOND_RANGE_PATTERN.search(text):
        raise ValueError(f"分镜 {position} 镜头描述必须包含秒级时间范围")
    return text


def _source_scene_duration(raw: dict[str, Any]) -> int:
    duration = raw.get("duration_sec")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        return duration
    start_sec = raw.get("start_sec")
    end_sec = raw.get("end_sec")
    if all(isinstance(value, int) and not isinstance(value, bool) for value in (start_sec, end_sec)):
        timeline_duration = int(end_sec) - int(start_sec)
        if timeline_duration > 0:
            return timeline_duration
    shot_description = raw.get("shot_description")
    if isinstance(shot_description, dict):
        shot_description = shot_description.get("text")
    ends = [int(match.group("end")) for match in _CAPTURED_SECOND_RANGE_PATTERN.finditer(_text(shot_description))]
    return max(ends, default=MIN_SCENE_DURATION_SEC)


def _rescale_shot_description(value: Any, *, source_duration: int, target_duration: int) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    text = _text(value)
    if not text:
        return text

    matches = list(_CAPTURED_SECOND_RANGE_PATTERN.finditer(text))
    if not matches:
        return f"0-{target_duration}秒: {text}"
    scale_base = max(source_duration, max(int(match.group("end")) for match in matches), 1)

    def replace_range(match: re.Match[str]) -> str:
        start = round(int(match.group("start")) * target_duration / scale_base)
        end = round(int(match.group("end")) * target_duration / scale_base)
        start = min(target_duration, max(0, start))
        end = min(target_duration, max(0, end))
        if int(match.group("end")) > int(match.group("start")) and end <= start:
            if start >= target_duration:
                start = max(0, target_duration - 1)
            end = min(target_duration, start + 1)
        return f"{start}-{end}秒"

    return _CAPTURED_SECOND_RANGE_PATTERN.sub(replace_range, text)


def _normalize_asset_requirements(value: Any) -> dict[str, list[str]]:
    source = value if isinstance(value, dict) else {}
    return {key: _dedupe_texts(source.get(key)) for key in ("characters", "scenes", "props")}


def _transition_text(value: Any, *, is_last: bool) -> str:
    transition = _public_optional_text(value)
    if transition:
        return transition
    return "产品定格结束。" if is_last else "沿当前动作或视线自然切入下一分镜。"


def _dedupe_texts(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _public_optional_text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _validate_story_structure(blueprints: list[dict[str, Any]]) -> None:
    if len(blueprints) == 1:
        if blueprints[0]["structure_role"] not in {"opening", "conclusion"}:
            raise ValueError("单分镜视频必须同时承担开场和收束")
        return
    if blueprints[0]["structure_role"] != "opening":
        raise ValueError("第一个分镜必须是 opening")
    if blueprints[-1]["structure_role"] != "conclusion":
        raise ValueError("最后一个分镜必须是 conclusion")
    if len(blueprints) >= 3 and not any(item["structure_role"] in {"development", "climax"} for item in blueprints[1:-1]):
        raise ValueError("中间分镜必须承担展开或证明职能")


def _normalize_role(value: Any, position: int) -> str:
    normalized = _text(value).lower()
    role = _ROLE_ALIASES.get(normalized)
    if not role:
        composite_markers = (
            ("opening", ("opening", "hook", "setup", "开场", "钩子")),
            ("conclusion", ("conclusion", "cta", "ending", "结尾", "收束", "转化")),
            ("development", ("development", "develop", "展开", "发展", "推进", "承接", "巩固")),
            ("climax", ("climax", "proof", "demo", "高潮", "证明", "验证", "核心")),
        )
        role = next((target for target, markers in composite_markers if any(marker in normalized for marker in markers)), None)
    if not role:
        raise ValueError(f"分镜 {position} structure_role 不合法")
    return role


def _strict_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} 必须是整数")
    return value


def _validate_total_duration(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 4 <= value <= 300:
        raise ValueError("视频总时长必须是 4-300 秒整数")


def _required_text(value: Any, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _public_required_text(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    _reject_internal_context(text, field_name)
    return text


def _public_optional_text(value: Any) -> str:
    text = _text(value)
    if text:
        _reject_internal_context(text, "用户可见分镜字段")
    return text


def _reject_internal_context(text: str, field_name: str) -> None:
    marker = next((item for item in _INTERNAL_CONTEXT_MARKERS if item.lower() in text.lower()), None)
    if marker:
        raise ValueError(f"{field_name} 包含内部上下文标记，不能进入 plan.md：{marker}")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value or "").strip()
