"""视频 Plan 的结构化分镜蓝图与精确时长校验。"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from pixelflow.creative.duration import (
    MAX_SCENE_DURATION_SEC,
    MIN_SCENE_DURATION_SEC,
    PREFERRED_SCENE_DURATION_SEC,
)

MAX_SCENE_ASSET_REFERENCES = 9

_TIMELINE_RANGE_PATTERN = re.compile(r"(?P<prefix>^|[\n。；;！？!?】])(?P<spacing>\s*)(?P<start>\d+)\s*(?:[-~—至])\s*(?P<end>\d+)\s*秒")
_MILLISECOND_PATTERN = re.compile(r"(?:ms|毫秒|\d{1,2}:\d{2}\.\d+)", flags=re.IGNORECASE)
_ASSET_TIME_PATTERN = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:[-~—至]\s*\d+(?:\.\d+)?\s*)?(?:秒|s|ms|毫秒)(?:\s*[:：].*)?\s*$",
    flags=re.IGNORECASE,
)
_ASSET_REFERENCE_PATTERN = re.compile(r"@?\s*(?:图片|视频)\s*\d+", flags=re.IGNORECASE)
_ASSET_SEGMENT_PATTERN = re.compile(r"^(?:第?[A-Z0-9一二三四五六七八九十]+段|段[A-Z0-9一二三四五六七八九十]+)(?:\b|[:：_-])?", flags=re.IGNORECASE)
_ASSET_SPEC_PATTERN = re.compile(
    r"^(?:\d{1,2}\s*:\s*\d{1,2}(?:竖屏|横屏|画幅|比例)?|(?:(?:720|1080)p|[248]k)(?:真人|写实|画质|质感|清晰度|竖屏|横屏|画幅|比例)+)$",
    flags=re.IGNORECASE,
)
_ASSET_METADATA_PATTERNS = (
    ("钩子", re.compile(r"钩子")),
    ("叙事结构", re.compile(r"^(?:开场|高潮|收束|结尾|误判|反转)(?:$|段|镜头|画面|设计|节奏|情节)")),
    ("全局设定", re.compile(r"^(?:全局设定|关键差异)(?:$|指令|要求|说明)")),
    ("镜头调度", re.compile(r"(?:景别|运镜|推镜|拉镜|摇镜|跟拍|穿透|转场)(?:$|镜头|设计|方式|指令)")),
    ("声音设计", re.compile(r"^(?:背景音乐|音效|旁白|声音|环境声|ASMR)(?:$|设计|要求|效果|说明)", flags=re.IGNORECASE)),
    ("字幕要求", re.compile(r"字幕")),
    ("光影设计", re.compile(r"(?:光影|光线|色调)(?:$|设计|风格|效果|要求)")),
    ("创作风格", re.compile(r"(?:风格|质感|高级感|清晰度)(?:$|设计|要求|说明)")),
    ("画面规格", re.compile(r"(?:画幅|比例|竖屏|横屏)(?:$|要求|说明)")),
)
_ASSET_REQUIREMENT_LABELS = {
    "characters": "人物",
    "scenes": "场景",
    "props": "道具/商品",
}
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
_SHOT_DESCRIPTION_DIMENSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("地点", ("地点", "室内", "室外", "街道", "客厅", "卧室", "厨房", "办公室", "走廊", "商场", "门店", "户外", "车内", "台面", "桌面")),
    ("主体", ("主体", "角色", "人物", "用户", "模特")),
    ("动作", ("动作", "拿起", "握住", "放下", "走入", "进入", "离开", "转身", "抬手", "打开", "关闭", "按下", "擦去", "倒入", "泼向", "砸向", "操作", "使用", "穿上", "戴上", "展开", "收起", "旋转", "流动", "点亮", "启动")),
    ("景别", ("景别", "全景", "远景", "中景", "近景", "特写", "微距", "大特写", "半身", "俯拍", "仰拍", "航拍", "过肩", "主观镜头")),
    ("运镜", ("运镜", "推近", "推镜", "拉远", "拉镜", "摇镜", "横摇", "移镜", "跟拍", "环绕", "升降", "变焦", "手持", "固定镜头", "镜头跟随", "镜头切", "轨道", "稳定器")),
    ("光影", ("光影", "光线", "光源", "照明", "自然光", "逆光", "侧光", "柔光", "硬光", "顶光", "轮廓光", "冷光", "暖光", "高光", "阴影", "色温", "曝光", "明暗", "冷色调", "暖色调")),
    ("声音", ("声音", "音效", "环境声", "对白", "音乐", "节拍", "静音", "无声", "雨声", "脚步声", "水声", "机械声", "呼吸声", "提示音", "开合声", "运行声")),
    ("收束", ("收束", "结尾", "镜尾", "定格", "停在", "停留", "落版", "淡出", "衔接下一镜", "切入下一镜", "进入下一镜", "动作完成", "离开画面")),
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


def normalize_scene_blueprints(
    raw_blueprints: Any,
    *,
    total_duration_sec: int,
    allow_legacy_global_shot_ranges: bool = False,
) -> list[dict[str, Any]]:
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
        shot_description = _shot_description_text(
            raw.get("shot_description"),
            position,
            duration,
            scene_start_sec=start_sec,
            allow_legacy_global_ranges=allow_legacy_global_shot_ranges,
        )
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


def salvage_scene_blueprints(
    raw_blueprints: Any,
    *,
    total_duration_sec: int,
    visual_style: str,
) -> list[dict[str, Any]]:
    """保留可用叙事和资产，仅重建非法时间线与镜头描述。

    这里故意不生成“目标用户/真实使用场景”一类通用替代内容。只要标题、
    故事线、角色职能或资产合同无法从 LLM 结果中恢复，就让本次 Plan 失败，
    避免一个镜头格式错误把整份具体创意降级成看似成功的泛化 Plan。
    """

    _validate_total_duration(total_duration_sec)
    if not isinstance(raw_blueprints, list) or not raw_blueprints:
        raise ValueError("Plan LLM 未返回可恢复的 scene_blueprints")
    minimum_count = math.ceil(total_duration_sec / MAX_SCENE_DURATION_SEC)
    maximum_count = total_duration_sec // MIN_SCENE_DURATION_SEC
    if not minimum_count <= len(raw_blueprints) <= maximum_count:
        raise ValueError(f"分镜数量 {len(raw_blueprints)} 无法在 4-15 秒约束内恢复为 {total_duration_sec} 秒")
    if not all(isinstance(item, dict) for item in raw_blueprints):
        raise ValueError("Plan LLM 分镜必须是对象")

    source_durations = [_source_scene_duration(item) for item in raw_blueprints]
    durations = _weighted_durations(total_duration_sec, [float(value) for value in source_durations])
    salvaged: list[dict[str, Any]] = []
    cursor = 0
    for position, (raw, duration) in enumerate(zip(raw_blueprints, durations, strict=True), start=1):
        title = _public_required_text(raw.get("title"), f"分镜 {position} title")
        storyline = _public_required_text(raw.get("storyline"), f"分镜 {position} storyline")
        item = {
            "scene_id": f"scene-{position}",
            "scene_index": position,
            "title": title,
            "structure_role": _salvage_role(
                raw.get("structure_role"),
                position=position,
                scene_count=len(raw_blueprints),
                title=title,
                storyline=storyline,
            ),
            "start_sec": cursor,
            "end_sec": cursor + duration,
            "duration_sec": duration,
            "storyline": storyline,
            "shot_description": "",
            "narration": _public_optional_text(raw.get("narration")) or "本分镜无旁白",
            "transition": _transition_text(raw.get("transition"), is_last=position == len(raw_blueprints)),
            "asset_requirements": _normalize_asset_requirements(raw.get("asset_requirements")),
        }
        item["shot_description"] = _rich_fallback_shot_description(item, visual_style=visual_style)
        salvaged.append(item)
        cursor += duration

    normalized = normalize_scene_blueprints(salvaged, total_duration_sec=total_duration_sec)
    entity_issues = asset_requirement_entity_quality_issues(normalized)
    if entity_issues:
        raise ValueError("；".join(entity_issues))
    return normalized


def _salvage_role(value: Any, *, position: int, scene_count: int, title: str, storyline: str) -> str:
    """恢复 LLM 自创的结构标签；首尾由时间位置决定，中段按语义或展开职能归类。"""

    try:
        return _normalize_role(value, position)
    except ValueError:
        pass
    if scene_count == 1:
        return "conclusion"
    if position == 1:
        return "opening"
    if position == scene_count:
        return "conclusion"
    semantic_text = f"{_text(value)} {title} {storyline}".lower()
    if any(marker in semantic_text for marker in ("climax", "proof", "demo", "高潮", "证明", "验证", "证据", "卖点", "产品展示")):
        return "climax"
    return "development"


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
        blueprint = {
            "scene_id": f"scene-{index}",
            "scene_index": index,
            "title": title,
            "structure_role": role,
            "start_sec": cursor,
            "end_sec": cursor + duration,
            "duration_sec": duration,
            "storyline": storyline,
            "shot_description": "",
            "narration": narration,
            "transition": transition,
            "asset_requirements": {
                "characters": ["目标用户"] if role in {"opening", "development"} else [],
                "scenes": ["真实使用场景"],
                "props": [product],
            },
        }
        blueprint["shot_description"] = _rich_fallback_shot_description(
            blueprint,
            visual_style=style,
            action_hint=shot_action,
        )
        blueprints.append(blueprint)
        cursor += duration
    return normalize_scene_blueprints(blueprints, total_duration_sec=total_duration_sec)


def scene_blueprint_durations(blueprints: list[dict[str, Any]]) -> list[int]:
    """提取已经校验过的分镜时长数组。"""
    return [int(item["duration_sec"]) for item in blueprints]


def shot_description_quality_issues(blueprints: list[dict[str, Any]]) -> list[str]:
    """返回每个分镜镜头描述缺失的创作维度，供 Plan LLM 定向修正。"""
    issues: list[str] = []
    for position, blueprint in enumerate(blueprints, start=1):
        description = _text(blueprint.get("shot_description"))
        missing = [label for label, markers in _SHOT_DESCRIPTION_DIMENSIONS if not _shot_dimension_present(label, markers, description, blueprint)]
        if missing:
            scene_index = blueprint.get("scene_index")
            index = scene_index if isinstance(scene_index, int) and not isinstance(scene_index, bool) else position
            issues.append(f"分镜 {index} 镜头描述缺少：{'、'.join(missing)}")
    return issues


def validate_shot_description_quality(blueprints: list[dict[str, Any]]) -> None:
    """拒绝缺少关键摄影信息的候选 Plan 蓝图。"""
    issues = shot_description_quality_issues(blueprints)
    if issues:
        raise ValueError("；".join(issues))


def asset_requirement_quality_issues(blueprints: list[dict[str, Any]]) -> list[str]:
    """识别误入资产数组的时间、镜头、声音、风格和参考编号元信息。"""

    return [
        *asset_requirement_entity_quality_issues(blueprints),
        *scene_asset_reference_budget_issues(blueprints),
    ]


def asset_requirement_entity_quality_issues(blueprints: list[dict[str, Any]]) -> list[str]:
    """识别不是具体可生成实体的资产名，不处理每镜引用预算。"""

    issues: list[str] = []
    for position, blueprint in enumerate(blueprints, start=1):
        scene_index = blueprint.get("scene_index")
        index = scene_index if isinstance(scene_index, int) and not isinstance(scene_index, bool) else position
        requirements = blueprint.get("asset_requirements")
        if not isinstance(requirements, dict):
            issues.append(f"分镜 {index} asset_requirements 必须包含人物、场景和道具/商品数组")
            continue
        for key, label in _ASSET_REQUIREMENT_LABELS.items():
            values = requirements.get(key)
            if not isinstance(values, list):
                issues.append(f"分镜 {index} asset_requirements.{key} 必须是数组")
                continue
            for value in values:
                text = _text(value)
                reason = _asset_metadata_reason(text) or _generic_asset_reason(key, text)
                if reason:
                    issues.append(f"分镜 {index} {label}资产“{text}”不是可生成实体：{reason}")
    return issues


def scene_asset_reference_budget_issues(blueprints: list[dict[str, Any]]) -> list[str]:
    """校验每个分镜声明的去重全局图片资产引用不超过 Seedance 上限。"""

    issues: list[str] = []
    for position, blueprint in enumerate(blueprints, start=1):
        scene_index = blueprint.get("scene_index")
        index = scene_index if isinstance(scene_index, int) and not isinstance(scene_index, bool) else position
        requirements = blueprint.get("asset_requirements")
        if not isinstance(requirements, dict):
            continue
        reference_count = len(
            {
                text
                for key in _ASSET_REQUIREMENT_LABELS
                for text in _dedupe_texts(requirements.get(key))
            }
        )
        if reference_count > MAX_SCENE_ASSET_REFERENCES:
            scene_id = _text(blueprint.get("scene_id")) or f"scene-{index}"
            issues.append(
                f"分镜 {scene_id}（scene_index={index}）引用资产共 {reference_count} 个，"
                f"最多允许 {MAX_SCENE_ASSET_REFERENCES} 个"
            )
    return issues


def validate_asset_requirement_quality(blueprints: list[dict[str, Any]]) -> None:
    """拒绝把创作指令或参考编号当作角色、物理场景或有形道具。"""

    issues = asset_requirement_quality_issues(blueprints)
    if issues:
        raise ValueError("；".join(issues))


def apply_asset_requirement_repairs(
    blueprints: list[dict[str, Any]],
    repairs: Any,
    *,
    total_duration_sec: int,
) -> list[dict[str, Any]]:
    """只采纳 LLM 返回的资产数组，忽略其对其他权威字段的潜在改写。"""

    if not isinstance(repairs, list) or not repairs:
        raise ValueError("Plan LLM 未返回资产需求修正结果")
    requirements_by_scene: dict[int, dict[str, list[str]]] = {}
    for item in repairs:
        if not isinstance(item, dict):
            continue
        scene_index = item.get("scene_index")
        if isinstance(scene_index, int) and not isinstance(scene_index, bool):
            requirements_by_scene[scene_index] = _normalize_asset_requirements(item.get("asset_requirements"))
    repaired = copy.deepcopy(blueprints)
    for blueprint in repaired:
        scene_index = int(blueprint["scene_index"])
        if scene_index in requirements_by_scene and asset_requirement_quality_issues([blueprint]):
            blueprint["asset_requirements"] = requirements_by_scene[scene_index]
    return normalize_scene_blueprints(repaired, total_duration_sec=total_duration_sec)


def enrich_incomplete_shot_descriptions(
    blueprints: list[dict[str, Any]],
    *,
    visual_style: str,
) -> list[dict[str, Any]]:
    """只替换不完整镜头描述，不改写 Plan 的叙事、时长和资产合同。"""
    enriched = copy.deepcopy(blueprints)
    for blueprint in enriched:
        if shot_description_quality_issues([blueprint]):
            blueprint["shot_description"] = _rich_fallback_shot_description(
                blueprint,
                visual_style=visual_style,
            )
    return enriched


def rebuild_scene_shot_descriptions(
    blueprints: list[dict[str, Any]],
    *,
    visual_style: str,
    total_duration_sec: int,
) -> list[dict[str, Any]]:
    """保留权威叙事与资产，为全部分镜确定性重建内容驱动的连续秒段。"""

    rebuilt = copy.deepcopy(blueprints)
    for blueprint in rebuilt:
        action_hints = _existing_shot_action_hints(blueprint.get("shot_description"))
        blueprint["shot_description"] = _rich_fallback_shot_description(
            blueprint,
            visual_style=visual_style,
            action_hints=action_hints,
        )
    return normalize_scene_blueprints(rebuilt, total_duration_sec=total_duration_sec)


def _existing_shot_action_hints(value: Any) -> list[str]:
    """从旧描述提取纯动作阶段，避免把整段摄影标签嵌进新秒段。"""

    text = _text(value)
    if not text:
        return []
    without_ranges = _TIMELINE_RANGE_PATTERN.sub(lambda match: match.group("prefix"), text)
    matches = re.findall(
        r"(?:^|[；;。])\s*动作[:：]\s*(.+?)(?=[；;。]\s*(?:地点|主体|动作|景别|运镜|光影|声音|收束)[:：]|$)",
        without_ranges,
        flags=re.DOTALL,
    )
    hints: list[str] = []
    for match in matches:
        hint = _clean_extracted_action(re.sub(r"\s+", " ", match))
        if hint and hint not in hints:
            hints.append(hint)
    supplements = [
        re.sub(r"\s+", " ", match).strip(" ：:；;。")
        for match in re.findall(r"△[^\n；;。]+", without_ranges)
    ]
    supplements = [item for item in supplements if item]
    if supplements:
        supplement_text = "；".join(supplements)
        if hints:
            hints[-1] = f"{hints[-1]}；{supplement_text}"
        else:
            hints.append(supplement_text)
    return hints


def _clean_extracted_action(value: str) -> str:
    hint = value.strip(" ：:；;。")
    generic_prefixes = (
        "建立人物、环境与冲突起点，具体承接",
        "沿上一段动作方向继续执行核心过程，用可观察细节推进",
        "呈现动作结果并完成本分镜叙事收束，落实",
    )
    for prefix in generic_prefixes:
        if hint.startswith(prefix):
            hint = hint[len(prefix) :].strip(" ：:；;。")
            if (hint.startswith("“") and hint.endswith("”")) or (hint.startswith('"') and hint.endswith('"')):
                hint = hint[1:-1].strip(" ：:；;。")
            break
    return hint


def apply_shot_description_repairs(
    blueprints: list[dict[str, Any]],
    repairs: Any,
    *,
    total_duration_sec: int,
) -> list[dict[str, Any]]:
    """只采纳 LLM 返回的镜头描述，忽略其对其他权威字段的潜在改写。"""
    if not isinstance(repairs, list) or not repairs:
        raise ValueError("Plan LLM 未返回镜头描述修正结果")
    descriptions: dict[int, str] = {}
    for item in repairs:
        if not isinstance(item, dict):
            continue
        scene_index = item.get("scene_index")
        if isinstance(scene_index, int) and not isinstance(scene_index, bool):
            descriptions[scene_index] = _text(item.get("shot_description"))
    repaired = copy.deepcopy(blueprints)
    for blueprint in repaired:
        scene_index = int(blueprint["scene_index"])
        if scene_index in descriptions and shot_description_quality_issues([blueprint]):
            blueprint["shot_description"] = descriptions[scene_index]
    return normalize_scene_blueprints(repaired, total_duration_sec=total_duration_sec)


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
    preferred_count = max(1, round(total_duration_sec / PREFERRED_SCENE_DURATION_SEC))
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


def _shot_dimension_present(
    label: str,
    markers: tuple[str, ...],
    description: str,
    blueprint: dict[str, Any],
) -> bool:
    if _dimension_label_has_content(label, description):
        return True
    if any(marker != label and marker.lower() in description.lower() for marker in markers):
        return True
    assets = blueprint.get("asset_requirements")
    if not isinstance(assets, dict):
        return False
    if label == "地点":
        candidates = assets.get("scenes")
    elif label == "主体":
        candidates = [*(assets.get("characters") or []), *(assets.get("props") or [])]
    else:
        return False
    return any(_text(candidate) and _text(candidate) in description for candidate in candidates or [])


def _rich_fallback_shot_description(
    blueprint: dict[str, Any],
    *,
    visual_style: str,
    action_hint: str = "",
    action_hints: list[str] | None = None,
) -> str:
    duration = _strict_int(blueprint.get("duration_sec"), field_name="规则兜底 duration_sec")
    assets = blueprint.get("asset_requirements") if isinstance(blueprint.get("asset_requirements"), dict) else {}
    locations = _dedupe_texts(assets.get("scenes")) or ["与故事线一致的真实使用场景"]
    subjects = [*_dedupe_texts(assets.get("characters")), *_dedupe_texts(assets.get("props"))] or ["核心视觉主体"]
    role = _normalize_role(blueprint.get("structure_role"), int(blueprint.get("scene_index") or 1))
    storyline = _text(blueprint.get("storyline")) or "推进当前分镜的核心信息"
    narration = _text(blueprint.get("narration")) or "本分镜无旁白"
    style = _text(visual_style) or "真实广告风格"
    role_directions = {
        "opening": (
            "中近景切关键细节特写",
            "稳定跟拍主体建立空间后快速推近冲突细节",
            "用有方向的自然光与冷暖反差勾勒主体轮廓和关键高光",
            "停在冲突或产品关键细节，并沿动作方向衔接下一镜",
        ),
        "development": (
            "中景交代关系并切操作近景",
            "沿主体动作平稳侧移，随后推近当前信息点",
            "以柔和主光配合环境辅光，保持层次和材质可读性",
            "停在本步骤动作完成点，并以同方向运动衔接下一镜",
        ),
        "climax": (
            "中景切产品特写与结果大特写",
            "先环绕展示使用关系，再推近证据细节并稳定停住",
            "以高对比主光和轮廓光强化产品材质、动作结果与前后差异",
            "停在可验证的结果特写，并匹配剪辑进入最终使用状态",
        ),
        "conclusion": (
            "完整中景切产品定格特写",
            "跟随主体进入完成状态后缓慢拉稳，最终固定镜头",
            "使用干净柔光和清晰轮廓光统一品牌色与产品高光",
            "定格产品、结果和行动信息，完成落版并结束",
        ),
    }
    shot_size, camera, lighting, closure = role_directions[role]
    extracted_actions = _dedupe_texts(action_hints)
    base_action = _text(action_hint) or storyline
    sound = "保留符合地点的环境声与关键动作音效，本镜头无旁白" if narration in {"本分镜无旁白", "无旁白"} else f"保留符合地点的环境声和关键动作音效，并清晰承载旁白或对白“{narration}”"
    segment_count = 1 if duration <= 5 else 2 if duration <= 9 else 3
    boundaries = _segment_boundaries(duration, segment_count)
    grouped_actions = _group_action_hints(extracted_actions, segment_count)
    phases = (
        (
            f"建立人物、环境与冲突起点，具体承接“{base_action}”",
            "中景建立空间与人物关系",
            "稳定跟拍主体并轻推至第一个关键动作",
            "停在能引出下一步的动作、视线或物件上",
        ),
        (
            f"沿上一段动作方向继续执行核心过程，用可观察细节推进“{storyline}”",
            shot_size,
            camera,
            "在本段信息点或动作完成点短暂停留，保持方向连续",
        ),
        (
            f"呈现动作结果并完成本分镜叙事收束，落实“{storyline}”",
            "结果近景切主体或产品特写",
            "从证据细节缓慢拉稳并固定结果画面",
            closure,
        ),
    )
    lines: list[str] = []
    for segment_index, (start_sec, end_sec) in enumerate(boundaries):
        action, segment_shot_size, segment_camera, segment_closure = phases[min(segment_index, len(phases) - 1)]
        if grouped_actions[segment_index]:
            action = grouped_actions[segment_index]
        lines.append(
            f"{start_sec}-{end_sec}秒: 地点：{'、'.join(locations)}；主体：{'、'.join(subjects)}；"
            f"动作：{action}；景别：{segment_shot_size}；运镜：{segment_camera}；"
            f"光影：按{style}{lighting}；声音：{sound}；收束：{segment_closure}。"
        )
    return "\n".join(lines)


def _group_action_hints(action_hints: list[str], segment_count: int) -> list[str]:
    """按原先顺序把动作阶段完整分配到目标秒段，不丢动作也不复制整段标签。"""

    action_hints = _expand_action_hints(action_hints, segment_count)
    grouped: list[list[str]] = [[] for _ in range(segment_count)]
    hint_count = len(action_hints)
    if not hint_count:
        return ["" for _ in range(segment_count)]
    for index, action in enumerate(action_hints):
        if hint_count <= segment_count:
            target = index
        else:
            target = min(segment_count - 1, math.ceil((index + 1) * segment_count / hint_count) - 1)
        grouped[target].append(action)
    return ["；随后".join(items) for items in grouped]


def _expand_action_hints(action_hints: list[str], segment_count: int) -> list[str]:
    """动作阶段不足时按自然语义停顿拆开，让每个秒段承担不同且连续的动作。"""

    expanded = list(action_hints)
    while expanded and len(expanded) < segment_count:
        split_index = max(range(len(expanded)), key=lambda index: len(expanded[index]))
        parts = [part.strip(" ：:；;。，,") for part in re.split(r"[，；]", expanded[split_index])]
        parts = [part for part in parts if len(part) >= 3]
        if len(parts) < 2:
            break
        expanded[split_index : split_index + 1] = parts
    return expanded


def _segment_boundaries(duration_sec: int, segment_count: int) -> list[tuple[int, int]]:
    base, remainder = divmod(duration_sec, segment_count)
    durations = [base + (1 if index < remainder else 0) for index in range(segment_count)]
    result: list[tuple[int, int]] = []
    cursor = 0
    for duration in durations:
        result.append((cursor, cursor + duration))
        cursor += duration
    return result


def _dimension_label_has_content(label: str, description: str) -> bool:
    pattern = re.compile(rf"{re.escape(label)}\s*[:：]\s*(?P<content>[^；;。\n]*)")
    return any(re.search(r"[0-9A-Za-z\u4e00-\u9fff]", match.group("content")) for match in pattern.finditer(description))


def _shot_description_text(
    value: Any,
    position: int,
    duration_sec: int,
    *,
    scene_start_sec: int,
    allow_legacy_global_ranges: bool,
) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    text = _public_required_text(value, f"分镜 {position} shot_description")
    if _MILLISECOND_PATTERN.search(text):
        raise ValueError(f"分镜 {position} 镜头描述不能使用毫秒时间码")
    matches = list(_TIMELINE_RANGE_PATTERN.finditer(text))
    ranges = [(int(match.group("start")), int(match.group("end"))) for match in matches]
    if not ranges:
        raise ValueError(f"分镜 {position} 镜头描述必须包含秒级时间范围")
    if _ranges_cover(ranges, start_sec=0, end_sec=duration_sec):
        return text
    if allow_legacy_global_ranges and _ranges_cover(
        ranges,
        start_sec=scene_start_sec,
        end_sec=scene_start_sec + duration_sec,
    ):
        return _shift_timeline_ranges(text, offset_sec=scene_start_sec)
    raise ValueError(f"分镜 {position} 镜头描述时间范围必须从 0 秒连续覆盖到 {duration_sec} 秒")


def _ranges_cover(ranges: list[tuple[int, int]], *, start_sec: int, end_sec: int) -> bool:
    cursor = start_sec
    for range_start, range_end in ranges:
        if range_start != cursor or range_end <= range_start:
            return False
        cursor = range_end
    return cursor == end_sec


def _shift_timeline_ranges(text: str, *, offset_sec: int) -> str:
    def replace_range(match: re.Match[str]) -> str:
        start = int(match.group("start")) - offset_sec
        end = int(match.group("end")) - offset_sec
        return f"{match.group('prefix')}{match.group('spacing')}{start}-{end}秒"

    return _TIMELINE_RANGE_PATTERN.sub(replace_range, text)


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
    ends = [int(match.group("end")) for match in _TIMELINE_RANGE_PATTERN.finditer(_text(shot_description))]
    return max(ends, default=MIN_SCENE_DURATION_SEC)


def _rescale_shot_description(value: Any, *, source_duration: int, target_duration: int) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    text = _text(value)
    if not text:
        return text

    matches = list(_TIMELINE_RANGE_PATTERN.finditer(text))
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
        return f"{match.group('prefix')}{match.group('spacing')}{start}-{end}秒"

    return _TIMELINE_RANGE_PATTERN.sub(replace_range, text)


def _normalize_asset_requirements(value: Any) -> dict[str, list[str]]:
    source = value if isinstance(value, dict) else {}
    return {key: _dedupe_texts(source.get(key)) for key in ("characters", "scenes", "props")}


def _asset_metadata_reason(value: str) -> str | None:
    if not value:
        return "名称为空"
    if _ASSET_REFERENCE_PATTERN.search(value):
        return "未绑定的图片或视频参考编号"
    if _ASSET_TIME_PATTERN.search(value):
        return "时间范围或时长属于分镜调度信息"
    if _ASSET_SEGMENT_PATTERN.search(value):
        return "段落编号属于叙事结构信息"
    if _ASSET_SPEC_PATTERN.search(value):
        return "画幅或清晰度属于生成规格"
    for label, pattern in _ASSET_METADATA_PATTERNS:
        if pattern.search(value):
            return f"包含创作元信息“{label}”"
    return None


def _generic_asset_reason(collection: str, value: str) -> str | None:
    normalized = re.sub(r"\s+", "", value).lower()
    generic_values = {
        "characters": {"目标用户", "用户", "消费者", "人物", "角色", "模特"},
        "scenes": {"真实使用场景", "使用场景", "真实场景", "场景", "环境"},
        "props": {"产品", "商品", "核心产品", "主商品"},
    }
    if normalized in generic_values.get(collection, set()):
        return "名称过于泛化，必须给出可稳定复用的具体身份或实体名称"
    if collection == "props":
        sentence_markers = ("卖点", "强调", "主张", "要求", "需要", "目标", "配比", "包装", "logo")
        punctuation_count = sum(value.count(mark) for mark in ("，", ",", "；", ";", "。"))
        marker_count = sum(marker in normalized for marker in sentence_markers)
        if len(value) >= 28 and (punctuation_count >= 2 or marker_count >= 3):
            return "完整卖点或需求句不能作为单个道具名称"
    return None


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
